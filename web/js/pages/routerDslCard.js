// Router & DSL Line Management Card (Huawei HG630 V2 & ISP Home Gateway)
import { h, icon } from "../core/dom.js";
import { t, num, iso } from "../core/i18n.js";
import { chip } from "../components/ui.js";
import { toast } from "../components/toast.js";
import { openDialog } from "../components/dialog.js";
import { actions } from "../core/actions.js";

const state = {
  loaded: false,
  loading: false,
  failAt: 0,
  dsl: null,
  info: null,
  wifi: null,
  armedReboot: false,
  rebooting: false,
  showPass: false,
};

let pollTimer = null;

async function loadData(ctx, force = false) {
  if (state.loading || (!force && Date.now() - state.failAt < 12000 && state.loaded)) return;
  state.loading = true;
  try {
    const [dsl, info, wifi] = await Promise.all([
      actions.getRouterDsl().catch(() => null),
      actions.getRouterInfo().catch(() => null),
      actions.getRouterWifi().catch(() => null),
    ]);
    state.dsl = dsl;
    state.info = info;
    state.wifi = wifi;
    state.loaded = true;
  } catch (e) {
    state.failAt = Date.now();
    state.loaded = true;
  } finally {
    state.loading = false;
    ctx.touch();
  }
}

export default function routerDslCard(ctx) {
  if (!state.loaded && !state.loading) loadData(ctx);

  const dsl = state.dsl;
  const info = state.info;
  const wifi = state.wifi;

  const refreshAll = async () => {
    toast("جارٍ تحديث بيانات الراوتر وخط الـ DSL...");
    await loadData(ctx, true);
    toast("تم تحديث بيانات الراوتر بنجاح");
  };

  const doReboot = async (ev) => {
    if (!state.armedReboot) {
      state.armedReboot = true;
      ev.currentTarget.classList.add("armed");
      ev.currentTarget.textContent = "تأكيد إعادة التشغيل؟";
      setTimeout(() => {
        state.armedReboot = false;
        ctx.touch();
      }, 5000);
      return;
    }
    state.armedReboot = false;
    state.rebooting = true;
    ctx.touch();
    try {
      await actions.rebootRouter();
      toast("تم إرسال أمر إعادة التشغيل للراوتر بنجاح! سيعود الاتصال خلال دقيقة أو دقيقتين");
    } catch (e) {
      toast("فشل إعادة التشغيل: " + e.message, "bad");
    } finally {
      state.rebooting = false;
      ctx.touch();
    }
  };

  const editWifiDialog = () => {
    if (!wifi) return;
    const ssidIn = h("input", { class: "input", value: wifi.ssid || "", placeholder: "MyWifi" });
    const passIn = h("input", { class: "input", type: "text", value: wifi.password || "", placeholder: "كلمة مرور الواي فاي" });
    const maxIn = h("input", { class: "input", type: "number", min: "1", max: "32", value: String(wifi.max_clients || 32) });
    const hideChk = h("input", { type: "checkbox", checked: !!wifi.hidden });

    openDialog({
      title: "تعديل بيانات شبكة الواي فاي",
      sub: "تغيير اسم الشبكة وكلمة المرور من الراوتر مباشرة",
      submitLabel: "حفظ وتطبيق",
      content: [
        h("div", { class: "field" }, h("label", {}, "اسم شبكة الواي فاي (SSID)"), ssidIn),
        h("div", { class: "field" }, h("label", {}, "كلمة مرور الواي فاي"), passIn, h("span", { class: "sub" }, "على الأقل 8 أحرف")),
        h("div", { class: "field" }, h("label", {}, "أقصى عدد أجهزة مسموح بها"), maxIn, h("span", { class: "sub" }, "الحد الأقصى للمتصلين بوقت واحد (1 - 32)")),
        h("label", { class: "row-sw", style: { cursor: "pointer", "margin-top": "12px" } },
          h("div", {}, h("b", {}, "إخفاء شبكة الواي فاي (Hide Broadcast)"), h("span", { class: "sub" }, "تتطلب كتابة اسم الشبكة يدوياً للاتصال")),
          hideChk)
      ],
      onSubmit: async () => {
        const ssid = ssidIn.value.trim();
        const pass = passIn.value.trim();
        const max = parseInt(maxIn.value || "32", 10);
        if (!ssid) throw new Error("اسم الشبكة مطلوب");
        if (pass.length < 8) throw new Error("كلمة المرور يجب أن تكون 8 أحرف على الأقل");
        await actions.updateRouterWifi({
          ssid,
          password: pass,
          max_clients: max,
          hidden: hideChk.checked
        });
        toast("تم تحديث إعدادات الواي فاي في الراوتر بنجاح!");
        await loadData(ctx, true);
        return true;
      }
    });
  };

  // Status badges
  const healthLabel = dsl?.line_health === "excellent" ? "ممتاز (خط فائق النقاء)"
    : dsl?.line_health === "good" ? "جيد جداً (مستقر)"
    : dsl?.line_health === "fair" ? "مقبول"
    : "ضعيف (معرض للتقطيع)";
  const healthChipCls = dsl?.line_health === "excellent" || dsl?.line_health === "good" ? "ok"
    : dsl?.line_health === "fair" ? "warn" : "danger";

  const uptimeStr = info?.uptime_seconds
    ? `${Math.floor(info.uptime_seconds / 3600)} ساعة و ${Math.floor((info.uptime_seconds % 3600) / 60)} دقيقة`
    : "–";

  return [
    // CARD 1: DSL Line Stats & Physical Health
    h("section", { class: "card c7", style: { "border": "1px solid rgba(56, 189, 248, 0.25)" } },
      h("header", { class: "card-h" },
        h("div", {},
          h("h3", {}, "⚡ خط الـ DSL واستقرار الراوتر"),
          h("span", { class: "sub" }, `${info?.device_name || "Huawei HG630 V2"} · فيرموير ${info?.software_version ? info.software_version.split(" ")[0] : "WE V100"}`)),
        dsl?.status ? chip("ok", `${dsl.modulation || "VDSL"} · متصل`) : chip("", "جارٍ القراءة...")),

      dsl ? [
        h("div", { class: "grid", style: { "display": "grid", "grid-template-columns": "repeat(auto-fit, minmax(130px, 1fr))", "gap": "10px", "margin-top": "12px", "margin-bottom": "14px" } },
          h("div", { style: { "background": "rgba(255,255,255,0.03)", "border": "1px solid rgba(255,255,255,0.08)", "border-radius": "10px", "padding": "12px" } },
            h("span", { class: "sub", style: { "font-size": "11px", "display": "block", "margin-bottom": "4px" } }, "سرعة الخط الحالية"),
            h("b", { style: { "font-size": "16px", "color": "#38bdf8" } }, `↓ ${dsl.down_rate_mbps} `),
            h("small", { style: { "font-size": "10px", "color": "var(--muted)" } }, "Mbps"),
            h("div", { style: { "font-size": "11px", "color": "#a855f7", "margin-top": "2px" } }, `↑ ${dsl.up_rate_mbps} Mbps`)),

          h("div", { style: { "background": "rgba(255,255,255,0.03)", "border": "1px solid rgba(255,255,255,0.08)", "border-radius": "10px", "padding": "12px" } },
            h("span", { class: "sub", style: { "font-size": "11px", "display": "block", "margin-bottom": "4px" } }, "أقصى سرعة يتحملها سلكك"),
            h("b", { style: { "font-size": "16px", "color": "#10b981" } }, `🚀 ${dsl.down_max_mbps} `),
            h("small", { style: { "font-size": "10px", "color": "var(--muted)" } }, "Mbps"),
            h("div", { style: { "font-size": "11px", "color": "var(--muted)", "margin-top": "2px" } }, `أقصى رفع: ${dsl.up_max_mbps} Mbps`)),

          h("div", { style: { "background": "rgba(255,255,255,0.03)", "border": "1px solid rgba(255,255,255,0.08)", "border-radius": "10px", "padding": "12px" } },
            h("span", { class: "sub", style: { "font-size": "11px", "display": "block", "margin-bottom": "4px" } }, "نقاء الإشارة (SNR Margin)"),
            h("b", { style: { "font-size": "16px" } }, `${dsl.down_snr_db} `),
            h("small", { style: { "font-size": "10px", "color": "var(--muted)" } }, "dB"),
            h("div", { style: { "margin-top": "4px" } }, chip(healthChipCls, healthLabel))),

          h("div", { style: { "background": "rgba(255,255,255,0.03)", "border": "1px solid rgba(255,255,255,0.08)", "border-radius": "10px", "padding": "12px" } },
            h("span", { class: "sub", style: { "font-size": "11px", "display": "block", "margin-bottom": "4px" } }, "مستوى التوهين (Attenuation)"),
            h("b", { style: { "font-size": "16px" } }, `${dsl.down_attenuation_db} `),
            h("small", { style: { "font-size": "10px", "color": "var(--muted)" } }, "dB"),
            h("div", { style: { "font-size": "11px", "color": "var(--muted)", "margin-top": "2px" } }, "مؤشر بعد السلك عن الكابينة"))),

        h("div", { class: "kv", style: { "margin-top": "10px", "font-size": "12px" } },
          h("div", {}, h("span", { class: "k" }, "مدة عمل الراوتر (Uptime)"), h("span", { class: "v" }, uptimeStr)),
          info?.serial_number ? h("div", {}, h("span", { class: "k" }, "الرقم التسلسلي"), h("span", { class: "v mono" }, info.serial_number)) : null),

        h("div", { class: "fa", style: { "margin-top": "14px", "display": "flex", "gap": "10px" } },
          h("button", { type: "button", class: "btn danger sm" + (state.armedReboot ? " armed" : ""), disabled: state.rebooting, onclick: doReboot },
            state.rebooting ? "جارٍ إرسال الأمر…" : state.armedReboot ? "تأكيد إعادة التشغيل؟" : "🔄 إعادة تشغيل الراوتر"),
          h("button", { type: "button", class: "btn ghost sm", onclick: refreshAll }, icon("refresh", 14), "تحديث القياسات"))
      ] : h("div", { class: "skeleton", style: { height: "140px" } })),

    // CARD 2: WiFi Settings & Quick Controls
    h("section", { class: "card c5", style: { "border": "1px solid rgba(168, 85, 247, 0.25)" } },
      h("header", { class: "card-h" },
        h("div", {},
          h("h3", {}, "📶 شبكة الواي فاي للراوتر"),
          h("span", { class: "sub" }, "اسم الشبكة، إخفائها، وكلمة المرور")),
        wifi?.enabled ? chip("ok", wifi.hidden ? "مخفية" : "مرئية") : chip("danger", "معطلة")),

      wifi ? [
        h("div", { class: "kv", style: { "margin-top": "12px", "margin-bottom": "14px" } },
          h("div", {}, h("span", { class: "k" }, "اسم شبكة الواي فاي"), h("span", { class: "v mono", style: { "font-size": "14px", "font-weight": "bold", "color": "#c084fc" } }, wifi.ssid || "–")),
          h("div", {}, h("span", { class: "k" }, "كلمة مرور الواي فاي"),
            h("span", { class: "v mono", style: { "display": "inline-flex", "align-items": "center", "gap": "8px" } },
              state.showPass ? (wifi.password || "–") : "••••••••",
              h("button", { type: "button", class: "btn ghost sm", style: { "padding": "2px 6px", "font-size": "11px" }, onclick: () => { state.showPass = !state.showPass; ctx.touch(); } }, state.showPass ? "إخفاء" : "إظهار"))),
          h("div", {}, h("span", { class: "k" }, "أقصى عدد أجهزة متصلة"), h("span", { class: "v" }, `${wifi.max_clients} أجهزة`)),
          h("div", {}, h("span", { class: "k" }, "التردد والتشفير"), h("span", { class: "v" }, `${wifi.frequency} · ${wifi.security}`))),

        h("div", { class: "fa", style: { "margin-top": "16px" } },
          h("button", { type: "button", class: "btn primary sm", onclick: editWifiDialog }, icon("edit", 14), "تعديل إعدادات الواي فاي"))
      ] : h("div", { class: "skeleton", style: { height: "140px" } }))
  ];
}
