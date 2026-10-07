/* Meru Auto Recharge — frontend vanilla JS */
"use strict";

// ---------- eta global ----------
const state = {
  csrf: null,
  mock: true,
  killSwitch: false,
  view: "dashboard",
  currentOrderId: null,
  chart: null,
  pollTimer: null,
  es: null,
};

const STATUS_LABELS = {
  created: "Kreye", awaiting_payment: "Apè peman", paid: "Peye",
  sending: "Voye…", sent: "Voye", confirmed: "Konfime",
  failed_payment: "Peman echwe", expired: "Ekspire",
  needs_approval: "Apwobasyon", send_failed: "Voye echwe",
  cancelled: "Anile",
};

const STEPS = [
  { key: "created", label: "Lòd kreye" },
  { key: "awaiting_payment", label: "Apè peman MonCash" },
  { key: "paid", label: "Peman konfime" },
  { key: "sending", label: "Voye USDC/USDT" },
  { key: "sent", label: "Tranzaksyon sou chèn nan" },
  { key: "confirmed", label: "Konfime sou Meru" },
];
const STEP_ORDER = ["created", "awaiting_payment", "paid", "sending", "sent", "confirmed"];
const FAIL_STATES = ["failed_payment", "expired", "send_failed", "cancelled", "rejected"];

// ---------- èd ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("fr-FR", { maximumFractionDigits: d }) : "—";
};
const fmtDate = (iso) => iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("fr-FR") : "—";

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3500);
}

function icons() { if (window.lucide) lucide.createIcons(); }

// ---------- api ----------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (opts.method && opts.method !== "GET" && state.csrf)
    headers["X-CSRF-Token"] = state.csrf;
  let res;
  try {
    res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
  } catch (e) {
    $("offline").classList.remove("hidden");
    throw new Error("Backend la deconekte");
  }
  $("offline").classList.add("hidden");
  if (res.status === 401 && !path.startsWith("/api/auth")) {
    showLogin();
    throw new Error("Sesyon an fini");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === "object" && d ? (d.message || JSON.stringify(d)) : (d || `Erè ${res.status}`);
    const err = new Error(msg);
    err.payload = typeof d === "object" ? d : null;
    throw err;
  }
  return data;
}
const get = (p) => api(p);
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

// ---------- sesyon ----------
async function boot() {
  try {
    const me = await get("/api/auth/me");
    state.mock = me.mock_mode;
    state.killSwitch = me.kill_switch;
    updateModeBanner();
    if (me.setup_required) return showSetup();
    if (!me.authenticated) return showLogin();
    state.csrf = me.csrf;
    showApp();
  } catch {
    showLogin();
  }
}

function showSetup() {
  $("navbar").classList.add("hidden");
  show("login");
  $("auth-title").textContent = "Kreye modpas la";
  $("auth-sub").textContent = "Premye lansman — chwazi yon modpas fò (8+ karaktè)";
  $("auth-btn").querySelector("span").textContent = "Kreye kont";
  state.setupMode = true;
}

function showLogin() {
  $("navbar").classList.add("hidden");
  if (state.pollTimer) clearInterval(state.pollTimer);
  if (state.es) { state.es.close(); state.es = null; }
  show("login");
  if (!state.setupMode) {
    $("auth-title").textContent = "Konekte";
    $("auth-sub").textContent = "Antre modpas ou a";
    $("auth-btn").querySelector("span").textContent = "Konekte";
  }
}

function showApp() {
  $("navbar").classList.remove("hidden");
  startEvents();
  go(location.hash.replace("#", "") || "dashboard");
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(refreshView, 4000);
}

// ---------- navigasyon ----------
const VIEWS = ["dashboard", "new", "orders", "float", "settings", "audit"];
function show(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  const el = $("view-" + name);
  if (el) el.classList.remove("hidden");
}
function go(name) {
  if (!VIEWS.includes(name)) name = "dashboard";
  state.view = name;
  // Chan vi a = abandone modifikasyon ki pa sove — retounen eta sèvè a
  document.querySelectorAll("[data-dirty]").forEach((el) => delete el.dataset.dirty);
  show(name);
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === name));
  $("nav-links").classList.remove("open");
  refreshView();
}
window.addEventListener("hashchange", () => go(location.hash.replace("#", "")));

function refreshView() {
  if (!state.csrf) return;
  ({
    dashboard: loadDashboard,
    new: refreshOrderProgress,
    orders: loadOrders,
    float: loadFloat,
    settings: loadSettings,
    audit: loadAudit,
  })[state.view]?.().catch(() => {});
}

// ---------- SSE + fallback ----------
function startEvents() {
  if (state.es) state.es.close();
  try {
    state.es = new EventSource("/api/events");
    state.es.addEventListener("order", (e) => {
      const ev = JSON.parse(e.data);
      if (state.view === "new" && ev.id === state.currentOrderId) refreshOrderProgress();
      if (state.view === "dashboard" || state.view === "orders") refreshView();
      if (ev.status === "confirmed") toast(`Rechaj konfime: ${ev.reference_id}`);
    });
    state.es.onerror = () => { /* fallback: polling la deja aktif */ };
  } catch { /* SSE pa sipòte — polling ase */ }
}

// ---------- banyè mòd ----------
function updateModeBanner() {
  const b = $("mode-banner");
  b.className = "mode-banner " + (state.mock ? "mock" : "real");
  b.querySelector("i")?.remove();
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", state.mock ? "flask-conical" : "alert-octagon");
  b.prepend(icon);
  $("mode-banner-text").textContent = state.mock ? "MOD SIMILASYON" : "MOD REYÈL — LAJAN REYÈL";
  icons();
}

// ---------- dashboard ----------
async function loadDashboard() {
  const [st, fl, orders, monthly] = await Promise.all([
    get("/api/system/status"), get("/api/float"),
    get("/api/orders?limit=8"), get("/api/system/monthly-loss"),
  ]);
  state.killSwitch = st.kill_switch;
  $("sys-status-text").textContent = st.kill_switch ? "Otomatik OFF (kill switch)" : "Otomatik ON";
  document.querySelector("#sys-status .dot").className = "dot " + (st.kill_switch ? "off" : "ok");
  $("dash-float").textContent = fmt(fl.units, 4) + " " + fl.asset;
  $("dash-float-sub").textContent = fl.low ? "⚠ Anba sèy la" : `~${fl.remaining_recharges} rechaj ki rete`;
  $("dash-plop").textContent = fl.plop_balance != null ? fmt(fl.plop_balance) + " HTG" : "—";
  $("dash-loss").textContent = fmt(st.month.loss_htg) + " HTG";
  $("dash-rate").textContent = st.month.avg_effective_rate
    ? fmt(st.month.avg_effective_rate, 2) + " HTG/USD" : "—";
  renderOrdersTable($("dash-orders"), orders, true);
  renderChart(monthly);
}

function renderChart(monthly) {
  const ctx = $("loss-chart");
  if (!ctx || !window.Chart) return;
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: monthly.map((m) => m.month),
      datasets: [{
        label: "Pèt (HTG)",
        data: monthly.map((m) => m.loss_htg),
        backgroundColor: "#F97316",
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

// ---------- tablo lòd ----------
function renderOrdersTable(container, orders, compact = false) {
  if (!orders.length) {
    container.innerHTML = `<p class="muted">Pa gen lòd ankò.</p>`;
    return;
  }
  container.innerHTML = `<table><thead><tr>
    <th>Ref</th><th>HTG</th><th>USDT</th><th>Estati</th><th>Pèt</th><th>Dat</th>
    ${compact ? "" : "<th>Aksyon</th>"}
  </tr></thead><tbody>` + orders.map((o) => `
    <tr class="clickable" data-oid="${o.id}">
      <td>${esc(o.reference_id)}</td>
      <td>${fmt(o.amount_htg)}</td>
      <td>${o.usdt_send ? fmt(o.usdt_send, 4) : "—"}</td>
      <td><span class="tag ${o.status}">${STATUS_LABELS[o.status] || o.status}</span></td>
      <td>${o.loss_pct != null ? fmt(o.loss_pct, 2) + "%" : "—"}</td>
      <td>${fmtDate(o.created_at)}</td>
      ${compact ? "" : `<td>${actionButtons(o)}</td>`}
    </tr>`).join("") + "</tbody></table>";
  container.querySelectorAll("tr.clickable").forEach((tr) =>
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      loadOrderDetail(tr.dataset.oid);
    }));
  container.querySelectorAll("[data-act]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      orderAction(b.dataset.oid, b.dataset.act);
    }));
}

function actionButtons(o) {
  if (o.status === "needs_approval")
    return `<button class="btn btn-ghost" data-act="approve" data-oid="${o.id}">Apwouve</button>
            <button class="btn btn-ghost danger" data-act="reject" data-oid="${o.id}">Rejte</button>`;
  if (o.status === "send_failed")
    return `<button class="btn btn-ghost" data-act="retry-send" data-oid="${o.id}">Reeseye</button>`;
  return "";
}

async function orderAction(oid, act) {
  try {
    await post(`/api/orders/${oid}/${act}`);
    toast("Aksyon an fet");
    refreshView();
  } catch (e) { toast(e.message); }
}

async function loadOrders() {
  const status = $("orders-filter").value;
  const orders = await get("/api/orders" + (status ? `?status=${status}` : ""));
  renderOrdersTable($("orders-table"), orders);
}

async function loadOrderDetail(oid) {
  try {
    const o = await get(`/api/orders/${oid}`);
    const box = $("order-detail");
    box.classList.remove("hidden");
    box.innerHTML = `<h2><i data-lucide="file-text"></i> ${esc(o.reference_id)}</h2>` +
      renderTimeline(o) +
      `<div class="quote-box" style="margin-top:1rem">
        <div class="qrow"><span>Montan</span><b>${fmt(o.amount_htg)} HTG</b></div>
        <div class="qrow"><span>Metòd</span><b>${esc(o.method)}</b></div>
        <div class="qrow"><span>USDT voye</span><b>${o.usdt_send ? fmt(o.usdt_send, 6) : "—"}</b></div>
        <div class="qrow"><span>Taux efektif</span><b>${o.effective_rate ? fmt(o.effective_rate, 2) + " HTG/USD" : "—"}</b></div>
        <div class="qrow"><span>Pèt</span><b>${o.loss_pct ? fmt(o.loss_pct, 2) + "% (" + fmt(o.loss_htg) + " HTG)" : "—"}</b></div>
        ${o.tx_hash ? `<div class="qrow"><span>Tx hash</span><b class="small">${esc(o.tx_hash)}</b></div>` : ""}
        ${o.payment_url ? `<div class="qrow"><span>Peye</span><b><a href="${esc(o.payment_url)}" target="_blank" rel="noopener">Louvri MonCash</a></b></div>` : ""}
        ${o.failure_reason ? `<div class="qrow"><span>Rezon</span><b class="error-text">${esc(o.failure_reason)}</b></div>` : ""}
      </div>`;
    icons();
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch { /* ignore */ }
}

// ---------- timeline ----------
function renderTimeline(o) {
  const failed = FAIL_STATES.includes(o.status);
  const idx = STEP_ORDER.indexOf(o.status);
  return `<div class="timeline">` + STEPS.map((s, i) => {
    let cls = "", icon = "circle";
    if (failed) {
      if (o.status === "failed_payment" && s.key === "paid" ||
          o.status === "send_failed" && s.key === "sending" ||
          o.status === "expired" && s.key === "awaiting_payment" ||
          o.status === "cancelled" && i === idx + 1) {
        cls = "failed"; icon = "x";
      } else if (i < STEP_ORDER.indexOf("paid") && o.status === "failed_payment") {
        cls = "done"; icon = "check";
      } else if (o.status === "send_failed" && s.key === "paid") {
        cls = "done"; icon = "check";
      } else if (i < idx) { cls = "done"; icon = "check"; }
    } else if (o.status === "needs_approval") {
      cls = i === 0 ? "done" : (i === 1 ? "active" : "");
      icon = i === 0 ? "check" : (i === 1 ? "shield-question" : "circle");
    } else {
      if (i < idx) { cls = "done"; icon = "check"; }
      else if (i === idx) { cls = o.status === "confirmed" ? "done" : "active"; icon = o.status === "confirmed" ? "check" : "loader"; }
    }
    return `<div class="tl-step ${cls}">
      <div class="tl-dot"><i data-lucide="${icon}"></i></div>
      <div><div class="tl-label">${s.label}</div>
      ${cls === "failed" && o.failure_reason ? `<div class="tl-sub error-text">${esc(o.failure_reason)}</div>` : ""}
      ${s.key === "sent" && o.tx_hash ? `<div class="tl-sub">tx: ${esc(o.tx_hash.slice(0, 24))}…</div>` : ""}
      </div></div>`;
  }).join("") + `</div>`;
}

// ---------- nouvo rechaj ----------
let quoteTimer = null;
async function updateQuote() {
  const amount = parseFloat($("o-amount").value);
  const box = $("quote-box");
  if (!amount || amount <= 0) { box.classList.add("hidden"); return; }
  try {
    const q = await post("/api/orders/quote", { amount_htg: amount });
    const badgeCls = q.loss_pct <= 1 ? "green" : q.loss_pct <= 4 ? "yellow" : "red";
    box.innerHTML = `
      <div class="qrow"><span>HTG net (apre frè ${esc(q.fee_pct)}%)</span><b>${fmt(q.htg_net)} HTG</b></div>
      <div class="qrow"><span>Taux float</span><b>${fmt(q.taux_float, 2)} HTG/USD</b></div>
      <div class="qrow"><span>Frè rezo + Meru</span><b>${fmt(Number(q.fee_network) + Number(q.fee_meru), 4)} USDT</b></div>
      <div class="qrow strong"><span>USDT ki rive Meru</span><b>${fmt(q.usdt_send, 6)}</b></div>
      <div class="qrow"><span>Taux efektif</span><b>${fmt(q.effective_rate, 2)} HTG/USD</b></div>
      <div class="qrow"><span>Pèt vs referans ${fmt(q.reference)}</span>
        <b><span class="badge ${badgeCls}">${fmt(q.loss_pct, 2)}%</span> (${fmt(q.loss_htg)} HTG)</b></div>
      ${!q.float_sufficient ? `<div class="qrow"><span class="error-text">Float ensifizan (${fmt(q.float_units)} USDT)</span></div>` : ""}`;
    box.classList.remove("hidden");
    $("loss-warn").classList.toggle("hidden", q.within_loss_limit);
    $("loss-warn").textContent = q.within_loss_limit ? "" :
      `Atansyon: pèt la (${fmt(q.loss_pct, 2)}%) depase limit la — ou dwe konfime.`;
    state.lastQuote = q;
  } catch (e) {
    box.innerHTML = `<p class="error-text">${esc(e.message)}</p>`;
    box.classList.remove("hidden");
  }
}

async function submitOrder(e) {
  e.preventDefault();
  $("o-error").classList.add("hidden");
  const body = {
    amount_htg: parseFloat($("o-amount").value),
    method: $("o-method").value,
    phone: $("o-phone").value || null,
    confirm_loss: state.lastQuote && !state.lastQuote.within_loss_limit && state.lossConfirmed,
  };
  try {
    const order = await post("/api/orders", body);
    state.currentOrderId = order.id;
    state.lossConfirmed = false;
    renderCurrentOrder(order);
  } catch (err) {
    if (err.payload && err.payload.code === "LOSS_LIMIT") {
      if (confirm(err.message + "\n\nOu vle kontinye kanmenm?")) {
        state.lossConfirmed = true;
        submitOrder(e);
        return;
      }
    }
    $("o-error").textContent = err.message;
    $("o-error").classList.remove("hidden");
  }
}

function renderCurrentOrder(o) {
  $("order-timeline").innerHTML = renderTimeline(o);
  $("mock-controls").classList.toggle("hidden", !state.mock ||
    !["awaiting_payment", "created"].includes(o.status));
  const res = $("order-result");
  if (o.status === "confirmed") {
    res.classList.remove("hidden");
    res.innerHTML = `<div class="alert-warn" style="background:#dcfce7;border-color:#16a34a;color:#166534">
      <i data-lucide="check-circle"></i> Konfime! tx: ${esc(o.tx_hash || "")}</div>`;
  } else if (o.payment_url) {
    res.classList.remove("hidden");
    res.innerHTML = `<a class="btn btn-warn btn-block" href="${esc(o.payment_url)}" target="_blank" rel="noopener">
      <i data-lucide="external-link"></i> Peye ak MonCash</a>`;
  }
  icons();
}

async function refreshOrderProgress() {
  if (!state.currentOrderId) return;
  try {
    const o = await get(`/api/orders/${state.currentOrderId}`);
    renderCurrentOrder(o);
    if (o.status === "needs_approval") {
      $("order-result").classList.remove("hidden");
      $("order-result").innerHTML = `<div class="alert-warn">
        <i data-lucide="shield-question"></i> Lòd la bezwen apwobasyon (ale nan paj Lòd)</div>`;
      icons();
    }
  } catch { /* ignore */ }
}

// ---------- float ----------
async function loadFloat() {
  const [f, refills] = await Promise.all([get("/api/float"), get("/api/float/refills")]);
  $("float-balance").textContent = fmt(f.units, 4) + " " + f.asset;
  $("float-remaining").textContent = "~" + f.remaining_recharges;
  $("float-rate").textContent = f.avg_rate ? fmt(f.avg_rate, 2) + " HTG/USD" : "—";
  $("float-cost").textContent = fmt(f.cost_htg) + " HTG";
  $("float-alert").classList.toggle("hidden", !f.low);
  const t = $("refill-table");
  t.innerHTML = refills.length ? `<table><thead><tr>
    <th>Dat</th><th>HTG</th><th>USDT</th><th>Taux</th><th>Sous</th></tr></thead><tbody>` +
    refills.map((r) => `<tr><td>${fmtDate(r.created_at)}</td><td>${fmt(r.htg_spent)}</td>
      <td>${fmt(r.usdt_received, 4)}</td><td>${fmt(r.rate, 2)}</td><td>${esc(r.source)}</td></tr>`).join("") +
    "</tbody></table>" : `<p class="muted">Pa gen ranpli ankò.</p>`;
  icons();
}

// ---------- paramèt ----------
// Pa recrase yon chan itilizatè a ap modifye (polling chak 4 s)
document.addEventListener("input", (e) => { e.target.dataset.dirty = "1"; });
document.addEventListener("change", (e) => { e.target.dataset.dirty = "1"; });
const setVal = (id, v) => { const el = $(id); if (!el.dataset.dirty) el.value = v ?? ""; };
const setChk = (id, v) => { const el = $(id); if (!el.dataset.dirty) el.checked = !!v; };
const clearDirty = (form) => form.querySelectorAll("[data-dirty]").forEach((el) => delete el.dataset.dirty);

async function loadSettings() {
  const s = await get("/api/settings");
  setVal("s-ref", s.reference_rate);
  setVal("s-fee", s.fee_pct);
  setVal("s-feemodel", s.fee_model);
  setVal("s-feenet", s.fee_network);
  setVal("s-feemeru", s.fee_meru);
  setVal("s-loss", s.loss_limit_pct);
  setVal("s-maxorder", s.max_order_htg);
  setVal("s-maxday", s.max_daily_htg);
  setVal("s-reserve", s.float_reserve);
  setVal("s-alert", s.float_alert_below);
  setVal("n-network", s.wallet_network);
  setVal("m-address", s.meru_address || "");
  setVal("m-memo", s.meru_memo || "");
  setChk("m-verified", s.meru_verified);
  setChk("meru-check", s.meru_verified);
  $("meru-current").innerHTML = s.meru_address
    ? `<p>Aktif: <b>${esc(s.meru_address)}</b>${s.meru_memo ? " (memo: " + esc(s.meru_memo) + ")" : ""}</p>` +
      (s.pending_meru ? `<p class="alert-warn">Nouvo adrès ap aktive ${fmtDate(s.pending_meru.activate_at)}</p>` : "")
    : `<p class="error-text">Pa gen adrès — anrejistre l anvan ou fè rechaj.</p>`;
  $("mode-desc").textContent = s.mock_mode
    ? "Tout bagay simile — okenn lajan reyèl pa deplase."
    : "MOD REYÈL AKTIF — lajan reyèl ap deplase!";
  $("btn-mode-text").textContent = s.mock_mode ? "Pase an MOD REYÈL" : "Retounen an MOD SIMILASYON";
  $("btn-kill-text").textContent = s.kill_switch ? "Rekòmanse voye otomatik" : "Sispann voye otomatik";
  state.killSwitch = s.kill_switch;
}

async function switchMode() {
  const toReal = state.mock;
  if (toReal && !confirm("Ou sèten ou vle pase an MOD REYÈL? Lajan reyèl ap deplase.\n\nYon tès pre-vòl pral fèt anvan."))
    return;
  const box = $("preflight-box");
  if (toReal) {
    box.classList.remove("hidden");
    box.innerHTML = `<p class="muted">Tès pre-vòl…</p>`;
  }
  try {
    const res = await post("/api/settings/mode", {
      mode: toReal ? "real" : "mock",
      confirm: toReal,
      meru_verified: $("meru-check").checked,
    });
    state.mock = res.mock_mode;
    updateModeBanner();
    box.classList.add("hidden");
    toast(toReal ? "MOD REYÈL aktive" : "MOD SIMILASYON aktive");
    loadSettings();
  } catch (e) {
    if (e.payload && e.payload.checks) {
      box.innerHTML = `<p class="error-text">${esc(e.message)}</p>` +
        e.payload.checks.map((c) =>
          `<div class="preflight-item ${c.ok ? "ok" : "fail"}">
            <i data-lucide="${c.ok ? "check-circle" : "x-circle"}"></i>
            <span>${esc(c.label)}${c.error ? " — " + esc(c.error) : ""}</span></div>`).join("");
      icons();
    } else {
      box.innerHTML = `<p class="error-text">${esc(e.message)}</p>`;
    }
  }
}

// ---------- odit ----------
async function loadAudit() {
  const rows = await get("/api/audit");
  const t = $("audit-table");
  t.innerHTML = rows.length ? `<table><thead><tr>
    <th>Dat</th><th>Aksyon</th><th>Detay</th><th>IP</th></tr></thead><tbody>` +
    rows.map((r) => `<tr><td>${fmtDate(r.created_at)}</td>
      <td><code>${esc(r.action)}</code></td><td>${esc(r.detail || "")}</td>
      <td>${esc(r.ip || "")}</td></tr>`).join("") + "</tbody></table>"
    : `<p class="muted">Jounal la vid.</p>`;
}

// ---------- evènman ----------
$("form-auth").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("auth-error").classList.add("hidden");
  const pw = $("auth-password").value;
  try {
    const res = await post(state.setupMode ? "/api/auth/setup" : "/api/auth/login", { password: pw });
    state.csrf = res.csrf;
    state.setupMode = false;
    if (res.mock_mode !== undefined) { state.mock = res.mock_mode; updateModeBanner(); }
    $("auth-password").value = "";
    showApp();
  } catch (err) {
    $("auth-error").textContent = err.message;
    $("auth-error").classList.remove("hidden");
  }
});

$("btn-logout").addEventListener("click", async () => {
  try { await post("/api/auth/logout"); } catch {}
  state.csrf = null;
  showLogin();
});

$("nav-toggle").addEventListener("click", () => $("nav-links").classList.toggle("open"));

$("o-method").addEventListener("change", () => {
  $("o-phone-wrap").style.display = $("o-method").value === "moncash_ussd" ? "" : "none";
});
$("o-amount").addEventListener("input", () => {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(updateQuote, 350);
});
$("form-order").addEventListener("submit", submitOrder);
$("btn-accelerate").addEventListener("click", async () => {
  if (!state.currentOrderId) return;
  try { renderCurrentOrder(await post(`/api/orders/${state.currentOrderId}/mock-accelerate`)); }
  catch (e) { toast(e.message); }
});
$("btn-sim-fail").addEventListener("click", async () => {
  if (!state.currentOrderId) return;
  try { renderCurrentOrder(await post(`/api/orders/${state.currentOrderId}/mock-fail`)); }
  catch (e) { toast(e.message); }
});

$("orders-filter").addEventListener("change", loadOrders);

$("form-refill").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("r-error").classList.add("hidden");
  try {
    await post("/api/float/refills", {
      htg_spent: parseFloat($("r-htg").value),
      usdt_received: parseFloat($("r-usdt").value),
      source: $("r-source").value,
      tx_hash: $("r-hash").value || null,
    });
    toast("Ranpli anrejistre");
    e.target.reset();
    loadFloat();
  } catch (err) {
    $("r-error").textContent = err.message;
    $("r-error").classList.remove("hidden");
  }
});

$("form-withdraw").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("w-error").classList.add("hidden");
  $("w-ok").classList.add("hidden");
  try {
    const r = await post("/api/float/withdraw", {
      amount: parseFloat($("w-amount").value),
      method: $("w-method").value,
      recipient: $("w-recipient").value,
    });
    $("w-ok").textContent = `Retrè ${r.reference}: ${r.status}`;
    $("w-ok").classList.remove("hidden");
  } catch (err) {
    $("w-error").textContent = err.message;
    $("w-error").classList.remove("hidden");
  }
});

$("form-settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post("/api/settings", {
      reference_rate: $("s-ref").value, fee_pct: $("s-fee").value,
      fee_model: $("s-feemodel").value, fee_network: $("s-feenet").value,
      fee_meru: $("s-feemeru").value, loss_limit_pct: $("s-loss").value,
      max_order_htg: $("s-maxorder").value, max_daily_htg: $("s-maxday").value,
      float_reserve: $("s-reserve").value, float_alert_below: $("s-alert").value,
    });
    clearDirty(e.target);
    toast("Paramèt yo sove");
  } catch (err) { toast(err.message); }
});

$("form-network").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await post("/api/settings", { wallet_network: $("n-network").value });
    clearDirty(e.target);
    toast("Rezo a sove");
  } catch (err) { toast(err.message); }
});

$("form-meru").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("m-error").classList.add("hidden");
  $("m-ok").classList.add("hidden");
  try {
    const r = await post("/api/settings/meru-address", {
      address: $("m-address").value, memo: $("m-memo").value,
      verified: $("m-verified").checked, password: $("p-meru")?.value || $("m-password").value,
    });
    $("m-ok").textContent = r.pending ? "Chanjman an ap aktive nan 10 minit" : "Adrès la sove";
    $("m-ok").classList.remove("hidden");
    clearDirty(e.target);
    loadSettings();
  } catch (err) {
    $("m-error").textContent = err.message;
    $("m-error").classList.remove("hidden");
  }
});

$("form-password").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("p-error").classList.add("hidden");
  $("p-ok").classList.add("hidden");
  try {
    await post("/api/settings/password", {
      current_password: $("p-current").value, new_password: $("p-new").value });
    $("p-ok").textContent = "Modpas la chanje";
    $("p-ok").classList.remove("hidden");
    e.target.reset();
  } catch (err) {
    $("p-error").textContent = err.message;
    $("p-error").classList.remove("hidden");
  }
});

$("btn-mode").addEventListener("click", switchMode);
$("btn-kill").addEventListener("click", async () => {
  const on = !state.killSwitch;
  if (on && !confirm("Sispann TOUT voye otomatik?")) return;
  try {
    const r = await post("/api/settings/kill-switch", { on });
    state.killSwitch = r.kill_switch;
    toast(on ? "Voye otomatik sispann" : "Voye otomatik rekòmanse");
    loadSettings();
  } catch (e) { toast(e.message); }
});

// ---------- start ----------
boot().then(icons);
