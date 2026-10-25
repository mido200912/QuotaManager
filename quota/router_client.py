"""Router Client Manager for Home ISP Routers (Huawei HG630 V2 and ZTE).

Supports:
- Huawei HG630 V2 (WE Egypt default router) — full API login + device list
- ZTE ZXHN H168N / H188A fallback

Login algorithm for Huawei HG630:
  step1 = SHA256(password)                      [hex string]
  step2 = base64(step1.encode())                [as text bytes]
  plaint = username + step2 + csrf_param + csrf_token
  final_password = SHA256(plaint)               [hex string, sent in JSON]
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Optional, Any

import urllib3
from urllib3 import PoolManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("quota.router")


def _make_ssl_context() -> ssl.SSLContext:
    """Build a permissive SSL context for old router firmware (TLS 1.0/1.1)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except AttributeError:
        pass
    ctx.set_ciphers("ALL:@SECLEVEL=0")
    return ctx


@dataclass
class RouterDevice:
    mac: str
    ip: str
    hostname: str
    interface: str  # 'wifi_2.4g', 'wifi_5g', 'lan'
    online: bool
    rate: str = ""
    rssi: str = ""
ROUTER_PERMANENT_MACS = {
    "ba:d5:a8:f0:af:b6",
    "46:ba:b7:ec:b1:a7",
    "54:92:09:92:97:d1",
    "30:a2:c2:cf:9a:5a",
}


class HuaweiHG630Client:
    """
    Connector for Huawei HG630 V2 (WE Egypt default router).

    Uses HTTPS on port 443 with TLS 1.0 (self-signed cert accepted).
    Login uses a SHA256-based password hashing scheme discovered from
    the router's own user_login.js.
    """

    BASE = "https://192.168.1.1"

    def __init__(self, ip: str = "192.168.1.1", username: str = "admin", password: str = "") -> None:
        self.ip = ip
        self.username = username
        self.password = password
        self.BASE = f"https://{ip}"
        self._http = PoolManager(ssl_context=_make_ssl_context())
        self._session_cookie: str = ""
        self._csrf_param: str = ""
        self._csrf_token: str = ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, _retry: bool = True, **kw) -> urllib3.HTTPResponse:
        if not self._session_cookie and _retry and path != "/":
            self.login()
        headers = {"Cookie": self._session_cookie, "Referer": f"{self.BASE}/"}
        res = self._http.request("GET", f"{self.BASE}{path}", headers=headers, timeout=8, redirect=False, **kw)
        text = res.data.decode("utf-8", errors="replace")
        needs_relogin = (
            res.status in (302, 401, 403)
            or "login.html" in text
            or (path.startswith("/api/") and "/*" not in text)
        )
        if _retry and path != "/" and needs_relogin:
            log.info("[HG630] Session expired on GET %s (status=%s), re-logging in...", path, res.status)
            if self.login():
                headers = {"Cookie": self._session_cookie, "Referer": f"{self.BASE}/"}
                return self._http.request("GET", f"{self.BASE}{path}", headers=headers, timeout=8, redirect=False, **kw)
        return res

    def _post_json(self, path: str, body: dict, _retry: bool = True) -> urllib3.HTTPResponse:
        if not self._session_cookie and _retry and path != "/api/system/user_login":
            self.login()
        if "csrf" in body and isinstance(body["csrf"], dict):
            body["csrf"]["csrf_param"] = self._csrf_param
            body["csrf"]["csrf_token"] = self._csrf_token
        headers = {
            "Content-Type": "application/json",
            "Cookie": self._session_cookie,
            "Referer": f"{self.BASE}/",
        }
        res = self._http.request(
            "POST", f"{self.BASE}{path}",
            body=json.dumps(body).encode(),
            headers=headers, timeout=8, redirect=False
        )
        resp_text = res.data.decode("utf-8", errors="replace")
        new_param = re.search(r'"csrf_param":"([^"]+)"', resp_text)
        new_token = re.search(r'"csrf_token":"([^"]+)"', resp_text)
        if new_param:
            self._csrf_param = new_param.group(1)
        if new_token:
            self._csrf_token = new_token.group(1)
        needs_relogin = (
            res.status in (302, 401, 403)
            or "login.html" in resp_text
            or '"errcode":9002' in resp_text
            or (path.startswith("/api/") and "/*" not in resp_text)
        )
        if _retry and path != "/api/system/user_login" and needs_relogin:
            log.info("[HG630] Session expired on POST %s, re-logging in...", path)
            if self.login():
                return self._post_json(path, body, _retry=False)
        return res

    @staticmethod
    def _sha256(s: str) -> str:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _b64_text(s: str) -> str:
        return base64.b64encode(s.encode("utf-8")).decode()

    def _compute_password(self) -> str:
        """Huawei HG630 password hash: SHA256(user + base64(SHA256(pw)) + csrf_param + csrf_token)"""
        step1 = self._sha256(self.password)
        step2 = self._b64_text(step1)
        plaint = self.username + step2 + self._csrf_param + self._csrf_token
        return self._sha256(plaint)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Authenticate with the Huawei HG630 router."""
        try:
            log.info("[HG630] Fetching login page from %s", self.BASE)
            r = self._get("/", _retry=False)
            html = r.data.decode("utf-8", errors="replace")
            log.info("[HG630] Login page status: %s, size: %d", r.status, len(html))

            # Extract CSRF tokens
            token_m = re.search(r'csrf_token" content="([^"]+)"', html)
            param_m = re.search(r'csrf_param" content="([^"]+)"', html)
            if not token_m or not param_m:
                log.warning("[HG630] Could not find CSRF tokens in login page")
                return False

            self._csrf_token = token_m.group(1)
            self._csrf_param = param_m.group(1)
            log.info("[HG630] CSRF param=%s token=%s", self._csrf_param[:10], self._csrf_token[:10])

            # Extract session cookie
            raw_cookie = r.headers.get("Set-Cookie", "")
            self._session_cookie = raw_cookie.split(";")[0] if raw_cookie else ""
            log.info("[HG630] Session cookie: %s...", self._session_cookie[:30])

            # Build and send login request
            final_pw = self._compute_password()
            log.info("[HG630] Computed password hash: %s...", final_pw[:16])

            post_body = {
                "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
                "data": {"UserName": self.username, "Password": final_pw},
            }
            r2 = self._post_json("/api/system/user_login", post_body, _retry=False)
            resp = r2.data.decode("utf-8", errors="replace")
            log.info("[HG630] Login response: %s", resp[:200])

            if '"errorCategory":"ok"' in resp:
                log.info("[HG630] Login SUCCESS!")
                # Update session cookie from login response if provided
                new_set_cookie = r2.headers.get("Set-Cookie", "")
                if new_set_cookie:
                    self._session_cookie = new_set_cookie.split(";")[0]
                    log.info("[HG630] Updated session cookie after login: %s...", self._session_cookie[:30])
                new_param = re.search(r'"csrf_param":"([^"]+)"', resp)
                new_token = re.search(r'"csrf_token":"([^"]+)"', resp)
                if new_param:
                    self._csrf_param = new_param.group(1)
                if new_token:
                    self._csrf_token = new_token.group(1)
                return True

            log.warning("[HG630] Login FAILED: %s", resp[:200])
            return False

        except Exception as exc:
            log.error("[HG630] Login exception: %s", exc)
            return False

    def list_connected_devices(self) -> list[RouterDevice]:
        """Fetch connected devices from /api/system/HostInfo."""
        devices: list[RouterDevice] = []
        try:
            log.info("[HG630] Fetching device list from /api/system/HostInfo")
            r = self._get("/api/system/HostInfo")
            body = r.data.decode("utf-8", errors="replace")

            json_match = re.search(r"/\*(\[.*\])\*/", body, re.DOTALL)
            if not json_match:
                log.warning("[HG630] Could not find JSON in response: %s", body[:200])
                return devices

            data = json.loads(json_match.group(1))
            log.info("[HG630] Found %d host entries", len(data))

            for entry in data:
                mac = entry.get("MACAddress", entry.get("mac", ""))
                if not mac:
                    continue
                ip = entry.get("IPAddress", entry.get("ip", ""))
                hostname = entry.get("HostName", entry.get("hostname", "Unknown")) or "Unknown"
                active = entry.get("Active", True)
                interface_type = entry.get("InterfaceType", entry.get("Layer2Interface", "wifi"))
                iface = "wifi" if any(w in str(interface_type).lower() for w in ["wlan", "wifi", "ssid"]) else "lan"

                devices.append(RouterDevice(
                    mac=mac.lower().replace("-", ":"),
                    ip=ip,
                    hostname=hostname,
                    interface=iface,
                    online=bool(active),
                    rate=str(entry.get("AssociatedDeviceRate", "") or ""),
                    rssi=str(entry.get("Rssi", "") or ""),
                ))

        except Exception as exc:
            log.error("[HG630] list_connected_devices exception: %s", exc)

        return devices

    # ------------------------------------------------------------------
    # WLAN Filter (Hardware WiFi Blacklist) & Parental Control
    # ------------------------------------------------------------------

    def get_wlan_filters(self) -> list[dict]:
        """Fetch active WLAN MAC filter rules from /api/ntwk/wlanfilterone."""
        try:
            r = self._get("/api/ntwk/wlanfilterone")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                return json.loads(m.group(1))
        except Exception as exc:
            log.error("[HG630] get_wlan_filters exception: %s", exc)
        return []

    def list_wlan_blocked_macs(self) -> set[str]:
        """Return set of lowercased normalized MACs in WLAN blacklist."""
        blocked: set[str] = set()
        for item in self.get_wlan_filters():
            for m_obj in item.get("MACAddresses", []):
                mac = m_obj.get("MACAddress", "")
                if mac:
                    blocked.add(mac.lower().replace("-", ":"))
        return blocked

    def block_wlan_device(self, mac: str) -> bool:
        """Block wireless device via /api/ntwk/wlanfilterone hardware blacklist (drops client immediately)."""
        mac_norm = mac.lower().replace("-", ":")
        mac_up = mac_norm.upper()
        filters = self.get_wlan_filters()
        if not filters:
            log.warning("[HG630] No WLAN filter configuration found")
            return False

        target = filters[0]
        current_macs = [x.get("MACAddress", "").upper() for x in target.get("MACAddresses", []) if x.get("MACAddress")]
        if mac_up in current_macs:
            log.info("[HG630] MAC %s already in WLAN blacklist", mac_norm)
            return True

        new_macs = list(current_macs) + [mac_up]
        payload = {
            "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
            "data": {
                "ID": target.get("ID", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1."),
                "FrequencyBand": target.get("FrequencyBand", "2.4GHz"),
                "MACAddressControlEnabled": True,
                "MacFilterPolicy": 0,
                "MacFilters": [{"MACAddress": m} for m in new_macs]
            }
        }
        try:
            res = self._post_json("/api/ntwk/wlanfilterone", payload)
            resp_text = res.data.decode("utf-8", errors="replace")
            ok = '"errcode":0' in resp_text
            log.info("[HG630] block_wlan_device %s -> %s (resp=%s)", mac_norm, ok, resp_text[:120])
            return ok
        except Exception as exc:
            log.error("[HG630] block_wlan_device exception for %s: %s", mac_norm, exc)
            return False

    def unblock_wlan_device(self, mac: str) -> bool:
        """Unblock wireless device by removing from /api/ntwk/wlanfilterone hardware blacklist."""
        mac_norm = mac.lower().replace("-", ":")
        if mac_norm in ROUTER_PERMANENT_MACS:
            log.info("[HG630] Protecting permanent router MAC %s from unblock", mac_norm)
            return True

        mac_up = mac_norm.upper()
        filters = self.get_wlan_filters()
        if not filters:
            return True

        target = filters[0]
        current_macs = [x.get("MACAddress", "").upper() for x in target.get("MACAddresses", []) if x.get("MACAddress")]
        if mac_up not in current_macs:
            log.info("[HG630] MAC %s not in WLAN blacklist", mac_norm)
            return True

        new_macs = [m for m in current_macs if m != mac_up]
        payload = {
            "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
            "data": {
                "ID": target.get("ID", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1."),
                "FrequencyBand": target.get("FrequencyBand", "2.4GHz"),
                "MACAddressControlEnabled": True,
                "MacFilterPolicy": 0,
                "MacFilters": [{"MACAddress": m} for m in new_macs]
            }
        }
        try:
            res = self._post_json("/api/ntwk/wlanfilterone", payload)
            resp_text = res.data.decode("utf-8", errors="replace")
            ok = '"errcode":0' in resp_text
            log.info("[HG630] unblock_wlan_device %s -> %s (resp=%s)", mac_norm, ok, resp_text[:120])
            return ok
        except Exception as exc:
            log.error("[HG630] unblock_wlan_device exception for %s: %s", mac_norm, exc)
            return False

    def get_blocked_rules(self) -> list[dict]:
        """Fetch active MAC filter (Parental Control) rules from the router."""
        try:
            r = self._get("/api/ntwk/macfilter")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                return json.loads(m.group(1))
        except Exception as exc:
            log.error("[HG630] get_blocked_rules exception: %s", exc)
        return []

    def _list_parental_control_macs(self) -> set[str]:
        """Return set of lowercased normalized MACs in Parental Control rules."""
        blocked: set[str] = set()
        for rule in self.get_blocked_rules():
            for dev in rule.get("Devices", []):
                mac = dev.get("MACAddress", "")
                if mac:
                    blocked.add(mac.lower().replace("-", ":"))
        return blocked

    def _block_parental_control(self, mac: str) -> bool:
        """Block internet access via router Parental Control rule."""
        mac_norm = mac.lower().replace("-", ":")
        current_blocked = self._list_parental_control_macs()
        if mac_norm in current_blocked:
            return True

        mac_clean = mac_norm.replace(":", "").upper()
        rule_data = {
            "ID": "",
            "RuleName": f"QM_{mac_clean[-6:]}",
            "Enable": 1,
            "TimeMode": "0",
            "DailyFrom": "00:00",
            "DailyTo": "23:59",
            "Devices": [{"MACAddress": mac_norm.upper()}],
            "Mondayenable": False,
            "Tuesdayenable": False,
            "Wednesdayenable": False,
            "Thursdayenable": False,
            "Fridayenable": False,
            "Saturdayenable": False,
            "Sundayenable": False
        }
        payload = {
            "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
            "action": "create",
            "data": rule_data
        }
        try:
            res = self._post_json("/api/ntwk/macfilter", payload)
            resp_text = res.data.decode("utf-8", errors="replace")
            return '"errcode":0' in resp_text
        except Exception as exc:
            log.error("[HG630] _block_parental_control exception for %s: %s", mac_norm, exc)
            return False

    def _unblock_parental_control(self, mac: str) -> bool:
        """Unblock internet access by deleting matching parental control rules."""
        mac_norm = mac.lower().replace("-", ":")
        mac_clean = mac_norm.replace(":", "").upper()
        rule_name_match = f"QM_{mac_clean[-6:]}"
        rules = self.get_blocked_rules()
        any_found = False
        all_ok = True
        for rule in rules:
            devs = rule.get("Devices", [])
            matches_dev = any(d.get("MACAddress", "").lower().replace("-", ":") == mac_norm for d in devs)
            matches_name = (rule.get("RuleName") or "").upper() == rule_name_match
            if matches_dev or matches_name:
                any_found = True
                rule_id = rule.get("ID")
                if not rule_id:
                    continue
                del_payload = {
                    "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
                    "action": "delete",
                    "data": {"ID": rule_id}
                }
                try:
                    res = self._post_json("/api/ntwk/macfilter", del_payload)
                    resp_text = res.data.decode("utf-8", errors="replace")
                    if '"errcode":0' not in resp_text:
                        all_ok = False
                except Exception as exc:
                    log.error("[HG630] _unblock_parental_control exception: %s", exc)
                    all_ok = False
        return all_ok if any_found else True

    def block_device(self, mac: str) -> bool:
        """Block internet access via WLAN hardware blacklist (instant cutoff) + Parental Control."""
        wlan_ok = self.block_wlan_device(mac)
        pc_ok = self._block_parental_control(mac)
        return wlan_ok or pc_ok

    def unblock_device(self, mac: str) -> bool:
        """Unblock internet access via WLAN hardware blacklist + Parental Control."""
        wlan_ok = self.unblock_wlan_device(mac)
        pc_ok = self._unblock_parental_control(mac)
        return wlan_ok and pc_ok

    def list_blocked_macs(self) -> set[str]:
        """Return all blocked MACs from WLAN blacklist and Parental Control."""
        wlan_blocked = self.list_wlan_blocked_macs()
        pc_blocked = self._list_parental_control_macs()
        return wlan_blocked | pc_blocked

    def get_wlan_diagnostics(self) -> dict[str, dict[str, Any]]:
        """Fetch real-time packet counters and WiFi stats per MAC address."""
        res: dict[str, dict[str, Any]] = {}
        try:
            r = self._get("/api/system/diagnose_wlan_advance")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                entries = json.loads(m.group(1))
                for item in entries:
                    mac = item.get("MACAddress", "").lower().replace("-", ":")
                    if not mac:
                        continue
                    res[mac] = {
                        "rx_pkt": int(item.get("RxPkt") or 0),
                        "tx_pkt": int(item.get("TxPkt") or 0),
                        "rssi": str(item.get("Rssi") or ""),
                        "rate": str(item.get("Rate") or ""),
                        "bandwidth": str(item.get("BandWidth") or ""),
                    }
        except Exception as exc:
            log.error("[HG630] get_wlan_diagnostics exception: %s", exc)
        return res

    def get_dsl_info(self) -> dict[str, Any]:
        """Fetch DSL line statistics and SNR margins from /api/ntwk/dslinfo."""
        try:
            r = self._get("/api/ntwk/dslinfo")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                down_curr = float(data.get("DownCurrRate", 0))
                up_curr = float(data.get("UpCurrRate", 0))
                down_max = float(data.get("DownstreamMaxBitRate", 0))
                up_max = float(data.get("UpstreamMaxBitRate", 0))
                down_snr = float(data.get("DownMargin", 0))
                up_snr = float(data.get("UpMargin", 0))
                down_attn = float(data.get("DownAttenuation", 0))
                up_attn = float(data.get("UpAttenuation", 0))

                if down_snr >= 15:
                    health = "excellent"
                elif down_snr >= 10:
                    health = "good"
                elif down_snr >= 6:
                    health = "fair"
                else:
                    health = "poor"

                return {
                    "status": data.get("Status", "Unknown"),
                    "modulation": data.get("Modulation", "Unknown"),
                    "down_rate_kbps": down_curr,
                    "up_rate_kbps": up_curr,
                    "down_rate_mbps": round(down_curr / 1024, 2),
                    "up_rate_mbps": round(up_curr / 1024, 2),
                    "down_max_kbps": down_max,
                    "up_max_kbps": up_max,
                    "down_max_mbps": round(down_max / 1024, 2),
                    "up_max_mbps": round(up_max / 1024, 2),
                    "down_snr_db": down_snr,
                    "up_snr_db": up_snr,
                    "down_attenuation_db": down_attn,
                    "up_attenuation_db": up_attn,
                    "line_health": health,
                }
        except Exception as exc:
            log.error("[HG630] get_dsl_info exception: %s", exc)
        return {}

    def get_device_info(self) -> dict[str, Any]:
        """Fetch router hardware, software, and uptime from /api/system/deviceinfo."""
        try:
            r = self._get("/api/system/deviceinfo")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                return {
                    "device_name": data.get("DeviceName", "HG630 V2"),
                    "serial_number": data.get("SerialNumber", ""),
                    "uptime_seconds": int(data.get("UpTime", 0)),
                    "software_version": data.get("SoftwareVersion", ""),
                    "hardware_version": data.get("HardwareVersion", ""),
                }
        except Exception as exc:
            log.error("[HG630] get_device_info exception: %s", exc)
        return {}

    def get_wifi_settings(self) -> dict[str, Any]:
        """Fetch basic WiFi settings from /api/ntwk/WlanBasic."""
        try:
            r = self._get("/api/ntwk/WlanBasic")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if m:
                arr = json.loads(m.group(1))
                if arr:
                    item = arr[0]
                    return {
                        "ssid": item.get("WifiSsid", ""),
                        "password": item.get("WpaPreSharedKey", ""),
                        "hidden": bool(item.get("WifiHideBroadcast", False)),
                        "enabled": bool(item.get("WifiEnable", True)),
                        "max_clients": int(item.get("X_AssociateDeviceNum", 32)),
                        "channel": int(item.get("Channel", 0)),
                        "frequency": item.get("FrequencyBand", "2.4GHz"),
                        "security": item.get("MixedEncryptionModes", "WPA/WPA2"),
                    }
        except Exception as exc:
            log.error("[HG630] get_wifi_settings exception: %s", exc)
        return {}

    def update_wifi_settings(self, ssid: Optional[str] = None, password: Optional[str] = None,
                             hidden: Optional[bool] = None, max_clients: Optional[int] = None,
                             enabled: Optional[bool] = None) -> bool:
        """Update WiFi settings via /api/ntwk/WlanBasic."""
        try:
            r = self._get("/api/ntwk/WlanBasic")
            body = r.data.decode("utf-8", errors="replace")
            m = re.search(r"/\*(.*)\*/", body, re.DOTALL)
            if not m:
                return False
            arr = json.loads(m.group(1))
            if not arr:
                return False
            data = arr[0]
            if ssid is not None:
                data["WifiSsid"] = ssid
            if password is not None:
                data["WpaPreSharedKey"] = password
            if hidden is not None:
                data["WifiHideBroadcast"] = hidden
            if max_clients is not None:
                data["X_AssociateDeviceNum"] = max_clients
            if enabled is not None:
                data["WifiEnable"] = enabled

            payload = {
                "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token},
                "data": data
            }
            res = self._post_json("/api/ntwk/WlanBasic", payload)
            resp_text = res.data.decode("utf-8", errors="replace")
            return '"errcode":0' in resp_text
        except Exception as exc:
            log.error("[HG630] update_wifi_settings exception: %s", exc)
            return False

    def reboot(self) -> bool:
        """Trigger router hardware reboot via /api/service/reboot.cgi."""
        try:
            payload = {
                "csrf": {"csrf_param": self._csrf_param, "csrf_token": self._csrf_token}
            }
            res = self._post_json("/api/service/reboot.cgi", payload)
            resp_text = res.data.decode("utf-8", errors="replace")
            ok = '"errcode":0' in resp_text
            log.info("[HG630] reboot triggered -> %s", ok)
            return ok
        except Exception as exc:
            log.error("[HG630] reboot exception: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Legacy ZTE client (kept as fallback for other router models)
# ---------------------------------------------------------------------------

class ZteRouterClient:
    """
    Auto-detecting router client. Tries Huawei HG630 first, then ZTE ZXHN.
    This is the class imported by the API endpoint for backwards compatibility.
    """

    def __init__(self, ip: str = "192.168.1.1", username: str = "admin", password: str = "") -> None:
        self.ip = ip
        self.username = username
        self.password = password
        self._client: Optional[HuaweiHG630Client] = None

    def login(self) -> bool:
        """Try Huawei HG630 first, fall back to ZTE."""
        log.info("[Router] Trying Huawei HG630 login for %s", self.ip)
        huawei = HuaweiHG630Client(ip=self.ip, username=self.username, password=self.password)
        if huawei.login():
            self._client = huawei
            return True

        log.info("[Router] Huawei failed, trying ZTE ZXHN for %s", self.ip)
        return self._zte_login()

    def _zte_login(self) -> bool:
        """Fallback ZTE ZXHN H168N login."""
        try:
            import requests
            self._zte_session = requests.Session()
            self._zte_session.headers["User-Agent"] = "Mozilla/5.0"
            self._zte_session.verify = False

            resp = self._zte_session.get(f"http://{self.ip}/", timeout=6, verify=False)
            log.info("[ZTE] Probe: status=%s url=%s", resp.status_code, resp.url)

            auth = self._zte_session.post(
                f"http://{self.ip}/getpage.gch?pid=1002",
                data={"Username": self.username, "Password": self.password, "action": "login"},
                timeout=6, verify=False
            )
            log.info("[ZTE] Auth: status=%s url=%s", auth.status_code, auth.url)
            ok = auth.status_code == 200 and "login" not in auth.url
            if ok:
                self._client = None  # mark ZTE mode
                self._use_zte = True
            return ok
        except Exception as exc:
            log.error("[ZTE] Login exception: %s", exc)
            return False

    def list_connected_devices(self) -> list[RouterDevice]:
        """List devices using the client that successfully logged in."""
        if self._client:
            return self._client.list_connected_devices()
        return self._zte_list_devices()

    def _zte_list_devices(self) -> list[RouterDevice]:
        """ZTE ZXHN device list fallback."""
        devices: list[RouterDevice] = []
        try:
            url = f"http://{self.ip}/getpage.gch?pid=1003&nextpage=net_dhcp_dynamic_list_t.gch"
            resp = self._zte_session.get(url, timeout=6, verify=False)
            matches = re.findall(
                r"([0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-]"
                r"[0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2})"
                r".*?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
                resp.text, re.DOTALL
            )
            seen: set[str] = set()
            for mac, ip in matches:
                mac_n = mac.lower().replace("-", ":")
                if mac_n in seen:
                    continue
                seen.add(mac_n)
                devices.append(RouterDevice(mac=mac_n, ip=ip, hostname="Unknown", interface="wifi", online=True))
        except Exception as exc:
            log.error("[ZTE] list_devices exception: %s", exc)
        return devices

    def block_device(self, mac: str) -> bool:
        if self._client:
            return self._client.block_device(mac)
        return False

    def unblock_device(self, mac: str) -> bool:
        if self._client:
            return self._client.unblock_device(mac)
        return False

    def list_blocked_macs(self) -> set[str]:
        if self._client:
            return self._client.list_blocked_macs()
        return set()

    def block_wlan_device(self, mac: str) -> bool:
        if self._client:
            return self._client.block_wlan_device(mac)
        return False

    def unblock_wlan_device(self, mac: str) -> bool:
        if self._client:
            return self._client.unblock_wlan_device(mac)
        return False

    def list_wlan_blocked_macs(self) -> set[str]:
        if self._client:
            return self._client.list_wlan_blocked_macs()
        return set()

    def get_wlan_diagnostics(self) -> dict[str, dict[str, Any]]:
        if self._client:
            return self._client.get_wlan_diagnostics()
        return {}

    def get_dsl_info(self) -> dict[str, Any]:
        if self._client:
            return self._client.get_dsl_info()
        return {}

    def get_device_info(self) -> dict[str, Any]:
        if self._client:
            return self._client.get_device_info()
        return {}

    def get_wifi_settings(self) -> dict[str, Any]:
        if self._client:
            return self._client.get_wifi_settings()
        return {}

    def update_wifi_settings(self, **kwargs) -> bool:
        if self._client:
            return self._client.update_wifi_settings(**kwargs)
        return False

    def reboot(self) -> bool:
        if self._client:
            return self._client.reboot()
        return False
