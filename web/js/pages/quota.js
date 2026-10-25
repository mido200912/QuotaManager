import { h, icon } from "../core/dom.js";
import { t, num, iso } from "../core/i18n.js";
import { api } from "../core/api.js";
import { bar, chip } from "../components/ui.js";
import { openDialog } from "../components/dialog.js";
import { toast } from "../components/toast.js";

let draft = null, topup = "", armedReset = false, armedDel = null, busy = false;
let ispLoaded = false, ispProvider = "we", ispLandline = "", ispPassword = "", ispStatus = null, ispSyncing = false;
let routerIp = "192.168.1.1", routerUser = "admin", routerPassword = "", routerScanning = false, routerResults = null, importingRouter = false;
const DAY = 86400;

export default function quota(ctx) {
  const d = ctx.dashboard;
  if (!d) return h("div", { class: "page" }, h("div", { class: "skeleton", style: { height: "260px" } }));
  const b = d.bundle, users = d.users || [];
  if (!draft) draft = { total_gb: b.base_total_gb ?? b.total_gb, reset_day: b.reset_day ?? 0, period_type: b.period_type === "end_of_month" ? "end_of_month" : "renew_day" };

  // Load ISP config once
  if (!ispLoaded) {
    ispLoaded = true;
    api.get("/api/isp/config").then((res) => {
      if (res) {
        if (res.provider) ispProvider = res.provider;
        if (res.landline) ispLandline = res.landline;
        if (res.remaining_gb) ispStatus = { remaining_gb: res.remaining_gb, last_sync: res.last_sync };
        ctx.touch();
      }
    }).catch(() => {});
  }

  const run = (fn, ok) => async () => {
    if (busy) return; busy = true;
    try { await fn(); toast(ok || t("q.saved")); await ctx.refresh(); } catch (e) { toast(`${t("dr.failed")}: ${e.message}`, "bad"); } finally { busy = false; ctx.touch(); }
  };
  const field = (label, input, hint) => h("div", { class: "field" }, h("label", {}, label), input, hint ? h("span", { class: "sub" }, hint) : null);
  const total = h("input", { class: "input", type: "number", min: "1", step: "any", value: String(draft.total_gb), oninput: (e) => { draft.total_gb = +e.target.value; } });
  const reset = h("input", { class: "input", type: "number", min: "0", max: "31", step: "1", value: String(draft.reset_day), oninput: (e) => { draft.reset_day = parseInt(e.target.value || "0", 10); } });
  const ptype = h("select", { class: "select", onchange: (e) => { draft.period_type = e.target.value; } },
    ["renew_day", "end_of_month"].map((v) => h("option", { value: v, selected: draft.period_type === v }, t("q.period." + v))));
  const save = run(async () => {
    if (!(draft.total_gb > 0)) throw new Error(t("q.total"));
    await api.post("/api/bundle", { total_gb: draft.total_gb, reset_day: draft.reset_day, period_type: draft.period_type }); draft = null;
  });
  const addGb = h("input", { class: "input", type: "number", min: "0", step: "any", placeholder: "10", value: topup, oninput: (e) => { topup = e.target.value; } });
  const doTop = run(async () => { const gb = +topup; if (!(gb > 0)) throw new Error(t("q.topup.gb")); await api.post("/api/bundle", { add_gb: gb }); topup = ""; });
  const doReset = async (ev) => {
    if (!armedReset) { armedReset = true; ev.currentTarget.classList.add("armed"); ev.currentTarget.textContent = t("dr.confirm"); setTimeout(() => { armedReset = false; }, 4000); return; }
    armedReset = false; await run(() => api.post("/api/reset-month"))();
  };

  const packs = b.recharges || [];
  const who = (p) => p.target_type === "user" ? (users.find((u) => u.id === p.target_id)?.name || "#" + p.target_id)
    : p.target_type === "device" ? (users.flatMap((u) => u.devices || []).find((x) => x.id === p.target_id)?.name || "#" + p.target_id) : t("q.pack.all");
  const expiry = (p) => { const dl = Math.ceil((p.expires_at * 1000 - Date.now()) / 86400000); return dl > 0 ? `${num(dl, 0)} ${t("common.days")}` : "–"; };
  const delPack = (p) => h("button", { type: "button", class: "btn danger sm" + (armedDel === p.id ? " armed" : ""), "aria-label": t("q.pack.del"),
    onclick: async (ev) => {
      if (armedDel !== p.id) { armedDel = p.id; ev.currentTarget.classList.add("armed"); ev.currentTarget.textContent = t("dr.confirm"); setTimeout(() => { if (armedDel === p.id) armedDel = null; }, 4000); return; }
      armedDel = null; await run(() => api.del(`/api/bundle/recharges/${p.id}`), t("dr.done"))();
    } }, armedDel === p.id ? t("dr.confirm") : icon("x", 15));

  function packDialog() {
    const gb = h("input", { class: "input", type: "number", min: "0", step: "any", required: true, placeholder: "50" });
    const days = h("input", { class: "input", type: "number", min: "1", step: "1", value: "30" });
    const rec = h("input", { type: "checkbox" });
    const tt = h("select", { class: "select" }, ["all", "user", "device"].map((v) => h("option", { value: v }, t("q.pack." + v))));
    const tgt = h("select", { class: "select" });
    const tgtF = field(t("q.pack.who"), tgt); tgtF.hidden = true;
    const fill = () => {
      const v = tt.value; tgtF.hidden = v === "all";
      const opts = v === "user" ? users.map((u) => [u.id, u.name]) : users.flatMap((u) => (u.devices || []).map((x) => [x.id, `${x.name || x.mac} · ${u.name}`]));
      tgt.replaceChildren(...opts.map(([id, n]) => h("option", { value: id }, n)));
    };
    tt.addEventListener("change", fill);
    const note = h("input", { class: "input" });
    openDialog({ title: t("q.pack.title"), submitLabel: t("q.pack.create"),
      content: [h("div", { class: "form-row" }, field(t("q.pack.gb"), gb), field(t("q.pack.days"), days)), h("div", { class: "form-row" }, field(t("q.pack.target"), tt), tgtF),
        h("label", { class: "row-sw", style: { cursor: "pointer" } }, h("span", {}, t("q.pack.recurring")), rec), field(t("q.pack.note"), note)],
      onSubmit: async () => {
        const body = { gb: +gb.value, expires_at: Math.floor(Date.now() / 1000) + Math.max(1, +days.value || 30) * DAY, recurring: rec.checked, target_type: tt.value, comment: note.value.trim() };
        if (!(body.gb > 0)) throw new Error(t("q.pack.gb"));
        if (tt.value !== "all") { body.target_id = parseInt(tgt.value, 10); if (!body.target_id) throw new Error(t("q.pack.who")); }
        await api.post("/api/bundle/recharges", body); await ctx.refresh(); toast(t("q.saved")); return true;
      } });
  }

  // ISP Action Handlers
  const saveAndSyncIsp = async () => {
    if (ispSyncing) return;
    if (!ispLandline.trim()) { toast("من فضلك أدخل رقم التليفون الأرضي", "bad"); return; }
    ispSyncing = true; ctx.touch();
    try {
      await api.post("/api/isp/config", { provider: ispProvider, landline: ispLandline, password: ispPassword });
      const res = await api.post("/api/isp/sync");
      ispStatus = res;
      toast(`تمت المزامنة بنجاح! المتبقي: ${res.remaining_gb} جيجا`);
      await ctx.refresh();
    } catch (e) {
      toast("فشلت المزامنة: " + (e.message || "تأكد من الرقم وكلمة المرور"), "bad");
    } finally {
      ispSyncing = false; ctx.touch();
    }
  };

  // Router Scan Handlers
  const scanRouter = async () => {
    if (routerScanning) return;
    routerScanning = true; ctx.touch();
    try {
      const res = await api.post("/api/router/scan", { ip: routerIp, username: routerUser, password: routerPassword });

      // ===== Debug output in browser console =====
      if (res && res.debug && res.debug.length) {
        console.group("🔌 Router Scan Debug Log");
        res.debug.forEach((line) => console.log(line));
        console.groupEnd();
      }
      // ===========================================

      if (res && res.logged_in) {
        routerResults = res.devices || [];
        toast(`تم سحب ${routerResults.length} جهاز بنجاح من الراوتر!`);
      } else {
        console.warn("🔌 Router scan failed. logged_in=false. Check debug group above ☝️");
        toast("فشل تسجيل الدخول للراوتر — تأكد من كلمة السر في ظهر الراوتر", "bad");
      }
    } catch (e) {
      console.error("🔌 Router scan JS exception:", e);
      toast("خطأ في الاتصال بالراوتر: " + e.message, "bad");
    } finally {
      routerScanning = false; ctx.touch();
    }
  };

  const importRouterDevices = async (onlyActive = false) => {
    if (!routerResults || !routerResults.length || importingRouter) return;
    importingRouter = true; ctx.touch();
    try {
      const res = await api.post("/api/router/import-devices", { devices: routerResults, only_active: onlyActive });
      toast(`تم استيراد ${res.imported} جهاز بنجاح ودمج المتطابقين في صفحة الأجهزة!`);
      await ctx.refresh();
    } catch (e) {
      toast("خطأ في استيراد الأجهزة: " + e.message, "bad");
    } finally {
      importingRouter = false; ctx.touch();
    }
  };


  const pct = b.total_gb > 0 ? (b.used_gb / b.total_gb) * 100 : 0;
  return h("div", { class: "page" }, h("div", { class: "grid" },
    h("section", { class: "card c7" },
      h("header", { class: "card-h" }, h("h3", {}, t("q.bundle")), chip("", b.days_left >= 0 ? `${t("ov.resets")} ${num(b.days_left, 0)} ${t("common.days")}` : t("ov.period.manual"))),
      h("div", { class: "c-num", style: { "margin-bottom": "8px" } }, h("b", {}, `${num(b.used_gb)} ${t("common.gb")} ${t("ov.of")} ${num(b.total_gb)}`), h("span", {}, `${num(pct, 0)}%`)), bar(pct),
      h("div", { class: "form-row", style: { "margin-top": "20px" } }, field(t("q.total"), total), field(t("q.reset"), reset)),
      h("div", { style: { "margin-top": "12px" } }, field(t("q.period"), ptype)),
      h("div", { class: "fa", style: { "margin-top": "18px" } }, h("button", { type: "button", class: "btn primary", onclick: save }, t("q.save")))),
    h("section", { class: "card c5" },
      h("header", { class: "card-h" }, h("h3", {}, t("q.topup"))), h("p", { class: "sub", style: { "margin-bottom": "12px" } }, t("q.topup.d")),
      h("div", { style: { display: "flex", gap: "10px" } }, h("div", { class: "grow" }, addGb), h("button", { type: "button", class: "btn primary", onclick: doTop }, icon("plus", 16), t("q.topup.btn"))),
      h("div", { class: "setting" }, h("div", {}, h("div", { class: "t" }, t("q.newperiod")), h("div", { class: "d" }, t("q.newperiod.d"))),
        h("button", { type: "button", class: "btn danger sm", onclick: doReset }, t("q.newperiod.btn")))),

    // NEW CARD 1: Egyptian ISP Auto-Sync Card (WE / Vodafone)
    h("section", { class: "card c6", style: { "border": "1px solid rgba(56, 189, 248, 0.3)" } },
      h("header", { class: "card-h" },
        h("div", {}, h("h3", {}, "📡 ربط باقة شركة الإنترنت (WE / Vodafone)"), h("span", { class: "sub" }, "سحب الجيجابايت المتبقية وتاريخ التجديد من الشركة مباشرة")),
        ispStatus ? chip("ok", `${ispStatus.remaining_gb} GB متبقية`) : chip("", "غير مربوط")),
      h("div", { class: "form-row", style: { "margin-top": "10px" } },
        field("شركة الإنترنت", h("select", { class: "select", onchange: (e) => { ispProvider = e.target.value; } },
          [h("option", { value: "we", selected: ispProvider === "we" }, "المصرية للاتصالات (WE)"),
           h("option", { value: "vodafone", selected: ispProvider === "vodafone" }, "فودافون مصر (Vodafone)"),
           h("option", { value: "orange", selected: ispProvider === "orange" }, "أورانج مصر (Orange)"),
           h("option", { value: "etisalat", selected: ispProvider === "etisalat" }, "اتصالات مصر (e& Etisalat)")])),
        field("رقم الخط الأرضي", h("input", { class: "input", placeholder: "02xxxxxxxx", value: ispLandline, oninput: (e) => { ispLandline = e.target.value; } }))),
      h("div", { style: { "margin-top": "8px" } },
        field("كلمة مرور تطبيق الشركة (My WE Password)", h("input", { class: "input", type: "password", placeholder: "••••••••", value: ispPassword, oninput: (e) => { ispPassword = e.target.value; } }), "كلمة سر حسابك على تطبيق My WE لاستعلام الرصيد")),
      h("div", { class: "fa", style: { "margin-top": "14px" } },
        h("button", { type: "button", class: "btn primary", disabled: ispSyncing, onclick: saveAndSyncIsp }, ispSyncing ? "جارٍ الاتصال بالشركة…" : "⚡ مزامنة وسحب الباقة الآن"))),

    // NEW CARD 2: Router Devices Scanner (بيانات ظهر الراوتر)
    h("section", { class: "card c6", style: { "border": "1px solid rgba(16, 185, 129, 0.3)" } },
      h("header", { class: "card-h" },
        h("div", {}, h("h3", {}, "🔌 سحب الأجهزة ببيانات ظهر الراوتر"), h("span", { class: "sub" }, "تسجيل الدخول للراوتر وسحب أسماء وماكات المتصلين تلقائياً")),
        routerResults ? chip("ok", `${routerResults.length} جهاز`) : null),
      h("div", { class: "form-row", style: { "margin-top": "10px" } },
        field("عنوان الراوتر", h("input", { class: "input", value: routerIp, oninput: (e) => { routerIp = e.target.value; } })),
        field("اسم المستخدم", h("input", { class: "input", value: routerUser, oninput: (e) => { routerUser = e.target.value; } }))),
      h("div", { style: { "margin-top": "8px" } },
        field("كلمة مرور ظهر الراوتر", h("input", { class: "input", type: "password", placeholder: "مكتوبة على ظهر الراوتر", value: routerPassword, oninput: (e) => { routerPassword = e.target.value; } }), "الباسورد المطبوع على الاستيكر خلف راوتر WE/ZTE")),
      h("div", { class: "fa", style: { "margin-top": "14px" } },
        h("button", { type: "button", class: "btn primary", disabled: routerScanning, onclick: scanRouter }, routerScanning ? "جارٍ فحص الراوتر…" : "🔍 سحب أجهزة الراوتر الآن")),
      routerResults && routerResults.length ? h("div", { style: { "margin-top": "12px" } },
        h("div", { style: { "display": "flex", "gap": "8px", "margin-bottom": "8px" } },
          h("button", { type: "button", class: "btn primary sm", style: { "flex": "1" }, disabled: importingRouter, onclick: () => importRouterDevices(true) },
            importingRouter ? "جارٍ الاستيراد…" : `🟢 المتصلة حالياً (${routerResults.filter((d) => d.online).length})`),
          h("button", { type: "button", class: "btn secondary sm", style: { "flex": "1" }, disabled: importingRouter, onclick: () => importRouterDevices(false) },
            importingRouter ? "جارٍ الاستيراد…" : `👥 الكل مع الدمج (${routerResults.length})`)),
        h("div", { style: { "max-height": "160px", "overflow-y": "auto", "font-size": "12px", "padding-right": "4px" } },
          routerResults.map((dev) => h("div", { style: { "display": "flex", "justify-content": "space-between", "align-items": "center", "padding": "5px 0", "border-bottom": "1px solid rgba(255,255,255,0.05)" } },
            h("div", {},
              h("b", {}, dev.hostname || "جهاز مجهول"),
              dev.online ? h("span", { style: { "color": "#10b981", "margin-right": "6px", "font-size": "11px" } }, "● متصل") : h("span", { style: { "color": "var(--muted)", "margin-right": "6px", "font-size": "11px" } }, "○ غير متصل"),
              dev.rate ? h("span", { style: { "color": "#38bdf8", "margin-right": "6px", "font-size": "11px" } }, `⚡ ${dev.rate}`) : null),
            h("span", { class: "sub mono" }, `${dev.ip || "بدون IP"} · ${dev.mac}`))))) : null),

    h("section", { class: "card c12" },
      h("header", { class: "card-h" }, h("h3", {}, t("q.packs")), h("button", { type: "button", class: "btn primary sm", onclick: packDialog }, icon("plus", 15), t("q.packs.new"))),
      packs.length ? h("div", {}, packs.map((p) => h("div", { class: "crow", style: { "grid-template-columns": "36px minmax(120px,1fr) minmax(130px,1.2fr) 84px auto" } },
        h("span", { class: "d-ic" }, icon("bolt", 16)),
        h("div", { class: "c-name" }, `${num(p.gb)} ${t("common.gb")}`, h("span", {}, [who(p), p.comment].filter(Boolean).join(" · "))),
        h("div", { class: "c-use" }, h("div", { class: "c-num" }, h("b", {}, `${num(p.remaining_gb)} ${t("common.gb")} ${t("q.pack.left")}`), h("span", {}, `${t("q.pack.expires")}: ${expiry(p)}`)), bar(p.gb > 0 ? (p.used_gb / p.gb) * 100 : 0, true)),
        p.recurring ? chip("accent", t("q.pack.recur")) : h("span"), delPack(p))))
        : h("div", { class: "empty" }, t("q.packs.none")))));
}
