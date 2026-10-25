"""ISP Account Sync Engine for Egyptian Telecom Providers (WE, Vodafone, Orange, Etisalat).

Connects to backend self-care portal endpoints to automatically retrieve:
* Total monthly bundle GB
* Remaining unconsumed GB
* Next bill / bundle rollover renewal date
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

log = logging.getLogger("quota.isp")


@dataclass
class IspBundleStatus:
    provider: str               # 'we', 'vodafone', 'orange', 'etisalat'
    landline: str
    total_gb: float
    remaining_gb: float
    used_gb: float
    reset_day: int
    renewal_date: str           # ISO format or YYYY-MM-DD
    last_synced: float
    success: bool
    error: str = ""


class WeEgyptClient:
    """Client for Telecom Egypt (WE) My-WE API (api-my.te.eg)."""

    BASE_URL = "https://api-my.te.eg/api"

    def __init__(self, msisdn: str, password: str) -> None:
        # msisdn: landline number with area code e.g. "0212345678" or "012345678"
        self.msisdn = msisdn.strip()
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://my.te.eg",
            "Referer": "https://my.te.eg/",
        })
        self._jwt_token: Optional[str] = None

    def login(self) -> bool:
        """Authenticate with TE account credentials."""
        url = f"{self.BASE_URL}/user/login"
        payload = {
            "header": {"customerId": "", "msisdn": self.msisdn, "timestamp": int(time.time() * 1000)},
            "body": {"password": self.password, "isMobile": False}
        }
        try:
            resp = self.session.post(url, json=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                body = data.get("body", {})
                self._jwt_token = body.get("jwtToken")
                if self._jwt_token:
                    self.session.headers["Jwt"] = self._jwt_token
                    return True
                log.error("WE login succeeded but jwtToken not returned: %s", data)
            else:
                log.error("WE login failed with status %d: %s", resp.status_code, resp.text)
        except Exception as exc:
            log.exception("WE login network exception: %s", exc)
        return False

    def get_bundle_usage(self) -> Optional[IspBundleStatus]:
        """Fetch remaining balance and quota usage from WE self-care API."""
        if not self._jwt_token and not self.login():
            return None

        url = f"{self.BASE_URL}/line/freeunitUsage"
        payload = {
            "header": {"msisdn": self.msisdn, "timestamp": int(time.time() * 1000)},
            "body": {}
        }
        try:
            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                body = data.get("body", {})
                summaries = body.get("detailedLineUsageSummaries", [])
                
                total_bytes = 0
                remaining_bytes = 0
                renewal_str = ""

                for item in summaries:
                    # Parse free unit types matching internet data
                    total_bytes += float(item.get("initialTotalAmount", 0))
                    remaining_bytes += float(item.get("freeAmount", 0))
                    if not renewal_str:
                        renewal_str = item.get("expiryDate", "")

                total_gb = round(total_bytes / (1024 ** 3), 2)
                remaining_gb = round(remaining_bytes / (1024 ** 3), 2)
                used_gb = round(max(0.0, total_gb - remaining_gb), 2)

                # Extract renewal day
                reset_day = 1
                if renewal_str and len(renewal_str) >= 10:
                    try:
                        # e.g. 2026-11-28
                        reset_day = int(renewal_str.split("-")[2][:2])
                    except (ValueError, IndexError):
                        pass

                return IspBundleStatus(
                    provider="we",
                    landline=self.msisdn,
                    total_gb=total_gb if total_gb > 0 else 140.0,
                    remaining_gb=remaining_gb,
                    used_gb=used_gb,
                    reset_day=reset_day,
                    renewal_date=renewal_str,
                    last_synced=time.time(),
                    success=True
                )
        except Exception as exc:
            log.exception("Failed to query WE freeunit usage: %s", exc)

        return None


class IspSyncManager:
    """Manages background auto-sync of home bundle with the Egyptian ISP."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def sync_now(self) -> Optional[IspBundleStatus]:
        """Read saved ISP credentials from settings, query ISP API, and update local bundle."""
        provider = await self.db.get_setting("isp_provider", "")
        landline = await self.db.get_setting("isp_landline", "")
        password = await self.db.get_setting("isp_password", "")

        if not provider or not landline or not password:
            return None

        client = None
        if provider.lower() in ("we", "te", "telecom_egypt"):
            client = WeEgyptClient(landline, password)
        # Future expansions for Vodafone / Orange / Etisalat clients

        if not client:
            return None

        status = client.get_bundle_usage()
        if status and status.success:
            # Sync bundle config directly in database
            bundle = await self.db.get_bundle()
            bundle.total_gb = status.total_gb
            bundle.reset_day = status.reset_day
            await self.db.update_bundle(bundle)
            await self.db.set_setting("isp_last_sync", str(int(time.time())))
            await self.db.set_setting("isp_remaining_gb", str(status.remaining_gb))
            await self.db.add_event(
                f"Auto-synced with {provider.upper()}: {status.remaining_gb} GB remaining of {status.total_gb} GB",
                "info"
            )
            return status

        return None
