"use strict";

// ---- helpers ----------------------------------------------------------------

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(`${res.status}: ${msg}`);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res.text();
}
const post = (path, body) => api(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

const state = { tz: undefined, demo: false, orderView: "watch", orderQuery: "", invTab: "stock",
  shipView: "open", invoiceId: null, alertSev: "all", trackDays: 30 };

function fmtDate(iso, opts = {}) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: state.tz, ...opts });
}
function fmtDateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric",
    minute: "2-digit", timeZone: state.tz });
}
function rel(iso) {
  if (!iso) return "";
  const h = (new Date(iso) - Date.now()) / 36e5;
  const a = Math.abs(h);
  const txt = a < 1 ? `${Math.round(a * 60)}m` : a < 48 ? `${Math.round(a)}h` : `${(a / 24).toFixed(1)}d`;
  return h < 0 ? `${txt} ago` : `in ${txt}`;
}
const money = (v) => v == null ? "" : "$" + Number(v).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const chips = (list, cls = "") => (list || []).map((x) => `<span class="chip ${cls}">${esc(x)}</span>`).join("");
const sevChip = (sev) => `<span class="chip ${sev}">${esc(sev)}</span>`;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 3500);
}

function table(headers, rows, empty = "Nothing here.") {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  const th = headers.map((h) => `<th class="${h.cls || ""}">${esc(h.label ?? h)}</th>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function pills(options, current, attr) {
  return `<div class="pills">${options.map(([key, label]) =>
    `<button class="pill ${key === current ? "on" : ""}" data-${attr}="${esc(key)}">${esc(label)}</button>`).join("")}</div>`;
}

// ---- drawer -------------------------------------------------------------------

function openDrawer(title, html) {
  $("#drawer-title").textContent = title;
  $("#drawer-body").innerHTML = html;
  $("#drawer").hidden = false;
  $("#scrim").hidden = false;
  $("#drawer-close").focus();
}
function closeDrawer() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
}
$("#drawer-close").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ---- overview ----------------------------------------------------------------------

function kpi(label, value, sub = "") {
  return `<div class="card kpi"><div class="label">${esc(label)}</div><div class="value">${value}</div>
    <div class="sub">${sub}</div></div>`;
}

async function renderOverview(view) {
  const [ov, alerts] = await Promise.all([api("/overview"), api("/alerts")]);
  const o = ov.orders, a = ov.alerts, s = ov.shipments, inv = ov.inventory;
  const shown = state.alertSev === "all" ? alerts : alerts.filter((x) => x.severity === state.alertSev);
  view.innerHTML = `
    <div class="kpis">
      ${kpi("Open alerts", a.urgent + a.warn + a.info,
        `<b class="t-urgent">${a.urgent}</b> urgent · <b class="t-warn">${a.warn}</b> warn · ${a.info} info`)}
      ${kpi("Active orders", o.active, `<b class="${o.overdue ? "t-urgent" : ""}">${o.overdue} overdue</b> · ${o.due_24h} due in 24h`)}
      ${kpi("Orders with notes", o.flagged, `${o.priority} priority · ${o.hazmat} hazmat`)}
      ${kpi("Freight in transit", s.open, `<b class="${s.late ? "t-warn" : ""}">${s.late} late</b> · ${s.stale} no update · ${s.hazmat} hazmat`)}
      ${kpi("Lots near best-by", inv.expiring_lots, `${inv.expiring_units} units · <b class="${inv.at_risk_units ? "t-warn" : ""}">${inv.at_risk_units} at risk</b>`)}
      ${kpi("3PL possible overbilling", money(ov.billing.open_overbilled), "invoices not yet approved, incl. disputed")}
    </div>
    <div class="section-head"><h2>Alerts</h2>
      ${pills([["all", "All"], ["urgent", "Urgent"], ["warn", "Warn"], ["info", "Info"]], state.alertSev, "sev")}</div>
    <div class="card">${shown.length ? `<ul class="alerts">${shown.map(alertItem).join("")}</ul>`
      : `<div class="empty">No open alerts.</div>`}</div>`;
  view.querySelectorAll("[data-sev]").forEach((b) => b.addEventListener("click", () => {
    state.alertSev = b.dataset.sev; render();
  }));
  view.querySelectorAll("[data-alert]").forEach((b) => b.addEventListener("click", async () => {
    await post(`/alerts/${b.dataset.alert}/${b.dataset.action}`);
    toast(b.dataset.action === "ack" ? "Acknowledged" : "Dismissed until it gets worse");
    render();
  }));
  view.querySelectorAll("[data-subject]").forEach((b) => b.addEventListener("click", () =>
    openSubject(b.dataset.subject, Number(b.dataset.id))));
}

function alertItem(a) {
  const openable = ["order", "shipment", "invoice", "lot", "product", "standing_order", "inbox"].includes(a.subject_type);
  const isTask = a.kind.endsWith("_task");
  return `<li class="alert"><span class="dot ${a.severity}" aria-label="${esc(a.severity)}"></span>
    <div class="body"><div class="title">${esc(a.title)}</div><div class="detail">${esc(a.detail)}</div></div>
    <div class="actions">
      ${openable ? `<button class="btn small" data-subject="${esc(a.subject_type)}" data-id="${a.subject_id}">Open</button>` : ""}
      <button class="btn small" data-alert="${a.id}" data-action="ack">Ack</button>
      <button class="btn small ghost" data-alert="${a.id}" data-action="dismiss">${isTask ? "Done" : "Dismiss"}</button>
    </div></li>`;
}

function openSubject(type, id) {
  if (type === "order") return openOrder(id);
  if (type === "shipment") return openShipment(id);
  if (type === "invoice") { state.invoiceId = id; location.hash = "#billing"; return; }
  if (type === "inbox") { location.hash = "#connectors"; return; }
  state.invTab = type === "lot" ? "lots" : type === "standing_order" ? "planner" : "sell";
  location.hash = "#inventory";
}

// ---- orders ---------------------------------------------------------------------------

function deadlineCell(o) {
  if (!o.required_by) return `<span class="muted">-</span>`;
  const cls = o.hours_left < 24 ? "t-urgent" : o.hours_left < 72 ? "t-warn" : "";
  return `<span class="${cls}">${o.hours_left < 0 ? "Overdue " + rel(o.required_by).replace(" ago", "") : rel(o.required_by)}</span>
    <div class="small muted">${fmtDate(o.required_by, { weekday: "short" })}</div>`;
}

function statusText(o) {
  const wh = o.warehouse.map((w) => `${w.source}: ${w.status}`).join(", ");
  return `${esc(o.status.replace(/_/g, " "))}${wh ? `<div class="small muted">${esc(wh.replace(/_/g, " "))}</div>` : ""}`;
}

async function renderOrders(view) {
  const q = state.orderQuery ? `&q=${encodeURIComponent(state.orderQuery)}` : "";
  const rows = await api(`/orders?view=${state.orderView}${q}`);
  view.innerHTML = `
    <div class="section-head"><h2>Orders</h2>
      ${pills([["watch", "Watchlist"], ["active", "All active"], ["priority", "Priority"], ["subscription", "Subscription"],
        ["seasonal", "Seasonal"], ["wholesale", "Wholesale"], ["hazmat", "Hazmat"], ["all", "Everything"]], state.orderView, "view")}
      <div class="grow"></div>
      <input id="order-q" type="search" placeholder="Search number, customer, notes" value="${esc(state.orderQuery)}">
      <a class="btn small" href="/api/export/watchlist.csv">Export CSV</a></div>
    <div class="card">${table(["Deadline", "Order", "Customer", "Type", "Status", "Flags", { label: "Notes", cls: "hide-sm" }],
      rows.map((o) => `<tr class="click" data-order="${o.id}">
        <td>${deadlineCell(o)}</td>
        <td><b>${esc(o.number)}</b> ${o.has_alert ? `<span class="dot warn" title="Has an open alert"></span>` : ""}
          <div class="small muted">${esc(o.source)} · ${fmtDate(o.placed_at)}</div></td>
        <td>${esc(o.customer)}</td>
        <td>${o.order_type !== "standard" ? `<span class="chip">${esc(o.order_type)}</span>` : ""}${o.hazmat ? `<span class="chip warn">hazmat</span>` : ""}</td>
        <td>${statusText(o)}</td>
        <td>${chips(o.note_flags, "info")}</td>
        <td class="notes hide-sm">${esc((o.notes || "").slice(0, 140))}</td></tr>`),
      state.orderView === "watch" ? "No orders need watching right now." : "No orders.")}</div>`;
  view.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    state.orderView = b.dataset.view; render();
  }));
  const input = $("#order-q", view);
  input.addEventListener("change", () => { state.orderQuery = input.value.trim(); render(); });
  view.querySelectorAll("[data-order]").forEach((tr) => tr.addEventListener("click", () => openOrder(Number(tr.dataset.order))));
}

async function openOrder(id) {
  const o = await api(`/orders/${id}`);
  const ai = o.ai ? `<h3 style="margin-top:18px">AI read (${esc(o.ai.provider || "")})</h3>
    <dl class="kv"><dt>Deadline</dt><dd>${esc(o.ai.deadline || "-")} ${o.ai.deadline_quote ? `<span class="muted">"${esc(o.ai.deadline_quote)}"</span>` : ""}</dd>
    <dt>Important</dt><dd>${chips(o.ai.important)}</dd></dl>` : "";
  openDrawer(`Order ${o.number}`, `
    <dl class="kv">
      <dt>Customer</dt><dd>${esc(o.customer)}</dd>
      <dt>Status</dt><dd>${statusText(o)}</dd>
      <dt>Type</dt><dd>${esc(o.order_type)} ${o.hazmat ? `<span class="chip warn">hazmat</span>` : ""}</dd>
      <dt>Placed</dt><dd>${fmtDateTime(o.placed_at)} <span class="muted">(${esc(o.source)})</span></dd>
      <dt>Needed by</dt><dd>${o.required_by ? `${fmtDateTime(o.required_by)} · ${rel(o.required_by)}
        <div class="small muted">from ${esc(o.required_by_source)}: "${esc(o.required_by_text)}"</div>` : "-"}</dd>
      <dt>Ships from</dt><dd>${esc(o.ship_from || "-")}</dd>
      <dt>Flags</dt><dd>${chips(o.note_flags, "info") || "-"}</dd>
      <dt>Tags</dt><dd>${chips(o.tags) || "-"}</dd>
      ${Object.keys(o.attributes).length ? `<dt>Attributes</dt><dd>${Object.entries(o.attributes).map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>`).join("<br>")}</dd>` : ""}
      ${o.shipments.length ? `<dt>Shipments</dt><dd>${chips(o.shipments)}</dd>` : ""}
    </dl>
    ${o.notes ? `<h3 style="margin-top:18px">Notes</h3><pre class="note">${esc(o.notes)}</pre>` : ""}
    ${o.warehouse.map((w) => `<h3 style="margin-top:18px">Warehouse record (${esc(w.source)})</h3>
      <dl class="kv"><dt>Number</dt><dd>${esc(w.number)}</dd><dt>Status</dt><dd>${esc(w.status)} <span class="muted">(${esc(w.raw_status)})</span></dd>
      <dt>Location</dt><dd>${esc(w.ship_from || "-")}</dd>${w.notes ? `<dt>Notes</dt><dd>${esc(w.notes)}</dd>` : ""}</dl>`).join("")}
    ${(o.external_notes || []).length ? `<h3 style="margin-top:18px">Notes from other tools</h3>
      ${o.external_notes.map((n) => `<p style="margin:6px 0"><span class="chip">${esc(n.source)}</span> ${esc(n.note)}
        <span class="small muted">${fmtDateTime(n.at)}</span></p>`).join("")}` : ""}
    ${ai}
    <h3 style="margin-top:18px">Items</h3>
    ${table(["SKU", "Item", { label: "Qty", cls: "num" }, { label: "Fulfilled", cls: "num" }], o.lines.map((l) =>
      `<tr><td class="mono">${esc(l.sku)}${l.hazmat ? ` <span class="chip warn">hazmat</span>` : ""}</td><td>${esc(l.title)}</td>
       <td class="num">${l.qty}</td><td class="num">${l.fulfilled}</td></tr>`))}`);
}

// ---- inventory -----------------------------------------------------------------------------

async function renderInventory(view) {
  view.innerHTML = `<div class="section-head"><h2>Inventory</h2>
    ${pills([["stock", "Stock by location"], ["sell", "Sell-through"], ["lots", "Lots & best-by"], ["planner", "Pick planner"]], state.invTab, "inv")}
    <div class="grow"></div><span id="inv-export"></span></div><div id="inv-body" class="stack"></div>`;
  view.querySelectorAll("[data-inv]").forEach((b) => b.addEventListener("click", () => { state.invTab = b.dataset.inv; render(); }));
  const body = $("#inv-body", view);
  const exp = (name) => { $("#inv-export", view).innerHTML = `<a class="btn small" href="/api/export/${name}.csv">Export CSV</a>`; };
  if (state.invTab === "stock") {
    exp("stock");
    $("#inv-export", view).insertAdjacentHTML("afterbegin", `<label class="btn small" style="margin-right:6px">Import RQ report
      <input id="rq-file" type="file" accept=".csv,.tsv,.xlsx,.xlsm" hidden></label>`);
    $("#rq-file", view).addEventListener("change", (e) => importFile("iqmetrix_rq", e.target.files[0]));
    const m = await api("/inventory/stock");
    body.innerHTML = `<div class="card">${table(["SKU", "Item", ...m.locations.map((l) => ({ label: l.code, cls: "num" })),
      { label: "Total", cls: "num" }, { label: "Available", cls: "num" }], m.rows.map((r) => `<tr>
        <td class="mono">${esc(r.sku)}</td><td>${esc(r.name)} ${r.hazmat ? `<span class="chip warn">hazmat</span>` : ""}${r.seasonal ? `<span class="chip">seasonal</span>` : ""}</td>
        ${m.locations.map((l) => { const c = r.by_location[l.code]; return c ? `<td class="num ${c.on_hand ? "" : "zero"}" title="${c.available} available, ${c.allocated} allocated">${c.on_hand}</td>` : `<td class="num zero">-</td>`; }).join("")}
        <td class="num"><b>${r.on_hand}</b></td><td class="num">${r.available}</td></tr>`), "No stock synced yet.")}</div>
      <p class="muted small">${m.locations.map((l) => `<b>${esc(l.code)}</b> ${esc(l.name)}`).join(" · ")}</p>`;
  } else if (state.invTab === "sell") {
    exp("sell-through");
    const st = await api("/inventory/sell-through");
    body.innerHTML = `<p class="muted small">Last ${st.days} days. Sell-through = sold / (sold + on hand). Click a row for locations.</p>
      <div class="card">${table(["SKU", "Item", { label: "Sold", cls: "num" }, { label: "On hand", cls: "num" }, "Sell-through",
        { label: "Per day", cls: "num" }, { label: "Forecast/day", cls: "num" }, { label: "Days of supply", cls: "num" }], st.rows.flatMap((r) => [
        `<tr class="click" data-sku="${esc(r.sku)}"><td class="mono">${esc(r.sku)}</td><td>${esc(r.name)} ${r.seasonal ? `<span class="chip">${esc(r.season || "seasonal")}</span>` : ""}</td>
          <td class="num">${r.sold}</td><td class="num">${r.on_hand}</td>${stCell(r)}<td class="num">${r.per_day}</td>
          <td class="num ${r.forecast_per_day == null ? "zero" : ""}">${r.forecast_per_day ?? "-"}</td>${dosCell(r)}</tr>`,
        ...r.by_location.map((l) => `<tr data-parent="${esc(r.sku)}" hidden><td></td><td class="muted">${esc(l.location)}</td>
          <td class="num">${l.sold}</td><td class="num">${l.on_hand}</td>${stCell(l)}<td class="num">${l.per_day}</td>
          <td class="num ${l.forecast_per_day == null ? "zero" : ""}">${l.forecast_per_day ?? "-"}</td>${dosCell(l)}</tr>`)]))}</div>
      <p class="muted small">Forecasts come from Muse. Low-stock alerts plan on the faster of recent sales and the forecast.</p>`;
    body.querySelectorAll("[data-sku]").forEach((tr) => tr.addEventListener("click", () =>
      body.querySelectorAll(`[data-parent="${CSS.escape(tr.dataset.sku)}"]`).forEach((x) => { x.hidden = !x.hidden; })));
  } else if (state.invTab === "lots") {
    exp("lots");
    const lots = await api("/inventory/lots");
    body.innerHTML = `<p class="muted small">Pick order: ${lots.policy === "fefo" ? "earliest best-by first (FEFO), then receipt date" : "receipt date (FIFO)"}.
      "At risk" projects this location's share of sales against each lot's best-by.</p>
      <div class="card">${table(["SKU", "Location", "Lot", "Received", "Best-by", { label: "Days left", cls: "num" },
        { label: "Qty", cls: "num" }, { label: "At risk", cls: "num" }], lots.rows.map((l) => {
        const cls = l.days_left == null ? "" : l.days_left < 0 || l.days_left <= 14 ? "t-urgent" : l.days_left <= lots.warn_days ? "t-warn" : "";
        return `<tr><td class="mono">${esc(l.sku)}</td><td>${esc(l.location)}</td><td class="mono">${esc(l.lot_number)}</td>
          <td>${fmtDate(l.received_on)}</td><td>${l.best_by ? fmtDate(l.best_by, { year: "numeric" }) : `<span class="muted">not dated</span>`}</td>
          <td class="num ${cls}">${l.days_left ?? "-"}</td><td class="num">${l.qty}</td>
          <td class="num ${l.at_risk_units ? "t-warn" : "zero"}">${l.at_risk_units || 0}</td></tr>`;
      }), "No lot-tracked stock.")}</div>`;
  } else {
    const m = await api("/inventory/stock");
    body.innerHTML = `<div class="card pad"><p class="muted small" style="margin-top:0">Plan a pick oldest-first while skipping lots too close
      to best-by, e.g. for a standing order that needs 90+ days left at ship date.</p>
      <form id="plan" class="form-grid">
        <label class="field">SKU<select name="sku">${m.rows.map((r) => `<option>${esc(r.sku)}</option>`).join("")}</select></label>
        <label class="field">Quantity<input name="qty" type="number" min="1" value="24" required></label>
        <label class="field">Location<select name="location"><option value="">Any</option>${m.locations.map((l) => `<option>${esc(l.code)}</option>`).join("")}</select></label>
        <label class="field">Min days to best-by<input name="min_days" type="number" min="0" value="0"></label>
        <label class="field">Ship date<input name="ship_date" type="date"></label>
        <button class="btn primary">Plan pick</button>
      </form></div><div id="plan-out"></div>`;
    $("#plan", body).addEventListener("submit", async (e) => {
      e.preventDefault();
      const p = new URLSearchParams([...new FormData(e.target)].filter(([, v]) => v !== ""));
      const r = await api(`/inventory/allocate?${p}`);
      $("#plan-out", body).innerHTML = `<div class="card pad stack">
        <div>${r.shortfall ? `<b class="t-urgent">Short ${r.shortfall} of ${r.requested}</b>` : `<b class="t-ok">Covered: ${r.requested} units</b>`}
          ${r.error ? `<span class="t-urgent">${esc(r.error)}</span>` : ""}</div>
        ${table(["Pick from lot", "Location", "Best-by", { label: "Days left", cls: "num" }, { label: "Qty", cls: "num" }],
          r.picks.map((x) => `<tr><td class="mono">${esc(x.lot_number)}</td><td>${esc(x.location)}</td><td>${fmtDate(x.best_by, { year: "numeric" })}</td>
          <td class="num">${x.days_left ?? "-"}</td><td class="num">${x.qty}</td></tr>`), "No eligible lots.")}
        ${r.skipped.length ? `<div class="muted small">Skipped (too close to best-by): ${r.skipped.map((x) => `${esc(x.lot_number)} @ ${esc(x.location)} (${x.qty} units, ${x.days_left} days)`).join("; ")}</div>` : ""}
      </div>`;
    });
  }
}

async function importFile(source, f) {
  if (!f) return;
  const res = await fetch(`/api/import/${source}?filename=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
  const r = await res.json();
  const parts = (x) => [x.orders && `${x.orders} orders`, x.stock_rows && `${x.stock_rows} stock rows`, x.lots && `${x.lots} lots`,
    x.sales_lines && `${x.sales_lines} sales lines`].filter(Boolean).join(", ");
  const info = (r.files || []).map((x) => `${x.file}: ${x.kind}${parts(x) ? " (" + parts(x) + ")" : ""}`).join("; ");
  const zeroed = r.stats && r.stats.stock_zeroed ? ` ${r.stats.stock_zeroed} SKUs missing from the report were set to 0 at those locations.` : "";
  toast(r.imported ? `Imported ${info}.${zeroed}` : `Nothing imported: ${info || r.detail || "unrecognised file"}. Check the column headers.`);
  render();
}

function stCell(r) {
  const pct = r.sell_through_pct;
  return `<td>${pct == null ? "-" : `<div class="row" style="flex-wrap:nowrap"><div class="bar grow"><i style="width:${Math.min(pct, 100)}%"></i></div>
    <span class="num small">${pct}%</span></div>`}</td>`;
}
function dosCell(r) {
  const d = r.days_of_supply;
  const cls = d == null ? "zero" : d < 7 ? "t-urgent" : d < 14 ? "t-warn" : "";
  return `<td class="num ${cls}">${d ?? "-"}</td>`;
}

// ---- freight -------------------------------------------------------------------------------------

async function renderFreight(view) {
  const rows = await api(`/shipments?status=${state.shipView}`);
  view.innerHTML = `<div class="section-head"><h2>Freight & shipments</h2>
      ${pills([["open", "In transit"], ["all", "All"]], state.shipView, "shipview")}
      <div class="grow"></div><button class="btn small" id="book-ship">Book shipment</button>
      <a class="btn small" href="/api/export/shipments.csv">Export CSV</a></div>
    <div class="card">${table(["Reference", "Type", "Current carrier / agent", "Tracking", "Status", "ETA", "Last update"],
      rows.map((s) => `<tr class="click" data-shipment="${s.id}">
        <td><b>${esc(s.reference)}</b>${s.hazmat ? ` <span class="chip warn">hazmat${s.un_numbers.length ? " " + esc(s.un_numbers.join(", ")) : ""}</span>` : ""}
          ${s.priority ? `<span class="chip urgent">priority</span>` : ""}
          <div class="small muted">${esc(s.origin || "")} &rarr; ${esc(s.destination || "")}</div></td>
        <td>${esc(s.direction)}<div class="small muted">${esc(s.mode)}</div></td>
        <td>${esc(s.carrier)}${s.agent ? `<div class="small muted">via ${esc(s.agent)}</div>` : ""}
          ${s.legs > 1 ? `<div class="small muted">leg ${s.legs}</div>` : ""}</td>
        <td class="mono">${s.tracking_url ? `<a href="${esc(s.tracking_url)}" target="_blank" rel="noopener">${esc(s.tracking_ref)}</a>` : esc(s.tracking_ref || "")}</td>
        <td>${esc(s.status.replace(/_/g, " "))}</td>
        <td class="${s.late ? "t-warn" : ""}">${s.eta ? fmtDate(s.eta) : "-"}${s.late ? "<div class='small'>late</div>" : ""}</td>
        <td class="${s.stale ? "t-warn" : ""}">${rel(s.last_event_at)}</td></tr>`), "No shipments.")}</div>`;
  view.querySelectorAll("[data-shipment]").forEach((tr) => tr.addEventListener("click", () => openShipment(Number(tr.dataset.shipment))));
  view.querySelectorAll("[data-shipview]").forEach((b) => b.addEventListener("click", () => { state.shipView = b.dataset.shipview; render(); }));
  $("#book-ship", view).addEventListener("click", openBooking);
}

function openBooking() {
  openDrawer("Book a shipment", `<p class="muted small" style="margin-top:0">For freight booked by phone or email. Updates that quote
    the tracking/PRO/BOL number (email, WhatsApp, Telegram) will attach to it, and handoffs to new carriers start a new leg.</p>
    <form id="book" class="form-grid">
      <label class="field">Reference<input name="reference" required placeholder="PO, BOL or your own ref"></label>
      <label class="field">Direction<select name="direction"><option>inbound</option><option>outbound</option><option>transfer</option></select></label>
      <label class="field">Mode<select name="mode">${["ltl", "ftl", "parcel", "ocean", "air", "courier"].map((m) => `<option>${m}</option>`).join("")}</select></label>
      <label class="field">Carrier<input name="carrier" required></label>
      <label class="field">Agent / forwarder<input name="agent"></label>
      <label class="field">Tracking / PRO / BOL<input name="tracking_ref"></label>
      <label class="field">From<input name="origin"></label>
      <label class="field">To<input name="destination"></label>
      <label class="field">ETA<input name="eta" type="date"></label>
      <label class="field">Order #<input name="order_number"></label>
      <label class="field">UN numbers<input name="un_numbers" placeholder="UN3480, UN1950"></label>
      <label class="field" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" name="hazmat"> Hazmat</label>
      <label class="field" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" name="priority"> Priority</label>
      <label class="field" style="grid-column:1/-1">Notes<input name="notes"></label>
      <button class="btn primary">Save shipment</button>
    </form>`);
  $("#book").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = Object.fromEntries([...fd].filter(([k, v]) => v !== "" && k !== "hazmat" && k !== "priority" && k !== "un_numbers"));
    body.hazmat = fd.has("hazmat");
    body.priority = fd.has("priority");
    body.un_numbers = (fd.get("un_numbers") || "").split(",").map((x) => x.trim()).filter(Boolean);
    try {
      const shp = await post("/shipments", body);
      toast(`Booked ${shp.reference}`);
      await openShipment(shp.id);
      render();
    } catch (err) { toast(err.message); }
  });
}

const SHIP_STATUSES = ["picked_up", "in_transit", "at_terminal", "out_for_delivery", "delivered", "exception"];

async function openShipment(id) {
  const s = await api(`/shipments/${id}`);
  openDrawer(`Shipment ${s.reference}`, `
    <dl class="kv">
      <dt>Status</dt><dd>${esc(s.status.replace(/_/g, " "))} ${s.late ? `<span class="chip warn">late</span>` : ""} ${s.stale ? `<span class="chip warn">no recent update</span>` : ""}</dd>
      <dt>Route</dt><dd>${esc(s.origin || "?")} &rarr; ${esc(s.destination || "?")}</dd>
      <dt>Type</dt><dd>${esc(s.direction)} · ${esc(s.mode)}</dd>
      <dt>ETA</dt><dd>${s.eta ? fmtDateTime(s.eta) : "-"}</dd>
      <dt>Hazmat</dt><dd>${s.hazmat ? `yes ${s.un_numbers.length ? esc(s.un_numbers.join(", ")) : `<span class="t-warn">(no UN numbers)</span>`}
        ${s.carrier_hazmat_approved === false ? `<div class="t-urgent small">Current carrier is not approved for hazmat</div>` : ""}` : "no"}</dd>
      ${s.order_number ? `<dt>Order</dt><dd>${esc(s.order_number)}</dd>` : ""}
      ${s.carrier_contact ? `<dt>Carrier contact</dt><dd>${esc(s.carrier_contact)}</dd>` : ""}
      ${s.notes ? `<dt>Notes</dt><dd>${esc(s.notes)}</dd>` : ""}
    </dl>
    <h3 style="margin-top:18px">Legs</h3>
    <div class="legs">${s.legs_detail.map((l, i) => `<div class="leg ${i === s.legs_detail.length - 1 ? "current" : ""}">
      <div class="small muted">Leg ${l.seq} · ${esc(l.mode || "")}</div><b>${esc(l.carrier)}</b>
      ${l.agent ? `<div class="small">via ${esc(l.agent)}</div>` : ""}<div class="mono">${esc(l.tracking_ref || "")}</div>
      <div class="small muted">${esc(l.status.replace(/_/g, " "))}${l.eta ? " · ETA " + fmtDate(l.eta) : ""}</div></div>`).join("")}</div>
    <h3 style="margin-top:18px">Log an update</h3>
    <form id="ship-update" class="form-grid" style="margin-top:8px">
      <label class="field">Status<select name="status">${SHIP_STATUSES.map((x) => `<option value="${x}" ${x === "in_transit" ? "selected" : ""}>${x.replace(/_/g, " ")}</option>`).join("")}</select></label>
      <label class="field">Where<input name="location" placeholder="e.g. Dallas terminal"></label>
      <label class="field">New ETA<input name="eta" type="date"></label>
      <label class="field" style="grid-column:1/-1">Note<input name="description" placeholder="What the carrier / driver said"></label>
      <label class="field">Handed to carrier<input name="new_carrier" placeholder="optional"></label>
      <label class="field">Agent<input name="new_agent" placeholder="optional"></label>
      <label class="field">New tracking / PRO<input name="new_tracking_ref" placeholder="optional"></label>
      <button class="btn primary">Save update</button>
    </form>
    <h3 style="margin-top:18px">History</h3>
    <ul class="timeline" style="margin-top:8px">${s.events.map((e) => `<li><b>${esc(e.status.replace(/_/g, " "))}</b>
      <span class="small muted">${fmtDateTime(e.occurred_at)} · ${esc(e.source)}</span>
      ${e.location ? `<div class="small">${esc(e.location)}</div>` : ""}<div class="small muted">${esc(e.description || "")}</div></li>`).join("")}</ul>`);
  $("#ship-update").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(e.target)].filter(([, v]) => v !== ""));
    await post(`/shipments/${id}/updates`, body);
    toast("Update saved");
    await openShipment(id);
    if (location.hash === "#freight") render();
  });
}

// ---- billing ---------------------------------------------------------------------------------------

async function renderBilling(view) {
  const [invoices, rates] = await Promise.all([api("/billing/invoices"), api("/billing/rate-card")]);
  if (!state.invoiceId && invoices.length) state.invoiceId = (invoices.find((i) => Number(i.overbilled) > 0) || invoices[0]).id;
  view.innerHTML = `<div class="section-head"><h2>3PL invoices</h2><span class="muted small">Checked against the rate card and
      warehouse activity for each billing period</span></div>
    <div class="card">${table(["Invoice", "Period", { label: "Billed", cls: "num" }, { label: "Expected", cls: "num" },
      { label: "Possible overbilling", cls: "num" }, { label: "Issues", cls: "num" }, "Status"], invoices.map((i) => `
      <tr class="click" data-inv-id="${i.id}" style="${i.id === state.invoiceId ? "background:var(--panel-2)" : ""}">
        <td><b>${esc(i.number)}</b><div class="small muted">${esc(i.vendor)}</div></td>
        <td>${fmtDate(i.period_start)} - ${fmtDate(i.period_end, { year: "numeric" })}</td>
        <td class="num">${money(i.billed_total)}</td><td class="num">${money(i.expected_total)}</td>
        <td class="num ${Number(i.overbilled) > 0 ? "t-urgent" : "t-ok"}">${money(i.overbilled)}</td>
        <td class="num">${i.issues}</td><td><span class="chip ${i.status === "approved" ? "ok" : i.status === "disputed" ? "urgent" : ""}">${esc(i.status)}</span></td></tr>`),
      "No invoices yet. They arrive from Gmail or Drive.")}</div>
    <div id="audit"></div>
    <div class="section-head"><h2>Rate card</h2></div>
    <div class="card">${table(["Vendor", "Code", "Charge", "Unit", { label: "Rate", cls: "num" }, "Effective"], rates.map((r) => `
      <tr><td>${esc(r.vendor)}</td><td class="mono">${esc(r.charge_code)}</td><td>${esc(r.description)}
        ${r.requires_approval ? `<span class="chip">needs work order</span>` : ""}</td><td>${esc(r.unit)}</td>
        <td class="num">${money(r.rate)}</td><td>${fmtDate(r.effective_from, { year: "numeric" })}${r.effective_to ? " - " + fmtDate(r.effective_to, { year: "numeric" }) : ""}</td></tr>`),
      "No rate card. Add one to the RateCard tab of the master sheet.")}</div>`;
  view.querySelectorAll("[data-inv-id]").forEach((tr) => tr.addEventListener("click", () => {
    state.invoiceId = Number(tr.dataset.invId); render();
  }));
  if (state.invoiceId) await renderAudit($("#audit", view), state.invoiceId);
}

async function renderAudit(el, id) {
  const a = await api(`/billing/invoices/${id}/audit`);
  const inv = a.invoice;
  el.innerHTML = `<div class="section-head"><h2>Audit: ${esc(inv.number)}</h2>
      <span class="muted small">${esc(inv.vendor)} · ${fmtDate(inv.period_start)} - ${fmtDate(inv.period_end, { year: "numeric" })}</span>
      <div class="grow"></div>
      <button class="btn small" id="copy-dispute" ${Number(a.overbilled) > 0 ? "" : "disabled"}>Copy dispute note</button>
      <button class="btn small" data-status="disputed">Mark disputed</button>
      <button class="btn small primary" data-status="approved">Approve</button></div>
    <div class="kpis">${kpi("Billed", money(a.billed_total))}${kpi("Expected", money(a.expected_total))}
      ${kpi("Possible overbilling", `<span class="${Number(a.overbilled) > 0 ? "t-urgent" : "t-ok"}">${money(a.overbilled)}</span>`)}
      ${kpi("Issues", a.issues)}</div>
    ${a.findings.map((f) => `<p class="t-urgent">${esc(f.message)}</p>`).join("")}
    <div class="card" style="margin-top:12px">${table(["Code", "Description", { label: "Qty", cls: "num" }, { label: "Rate", cls: "num" },
      { label: "Contract", cls: "num" }, { label: "Supported", cls: "num" }, { label: "Billed", cls: "num" },
      { label: "Expected", cls: "num" }, { label: "Variance", cls: "num" }, "Findings"], a.lines.map((l) => `
      <tr><td class="mono">${esc(l.charge_code || "-")}</td><td>${esc(l.description)}</td><td class="num">${esc(l.qty)}</td>
        <td class="num">${money(l.rate)}</td><td class="num">${l.contract_rate ? money(l.contract_rate) : "-"}</td>
        <td class="num">${l.supported_qty ?? "-"}</td><td class="num">${money(l.amount)}</td><td class="num">${money(l.expected)}</td>
        <td class="num ${Number(l.variance) > 0 ? "t-urgent" : Number(l.variance) < 0 ? "t-ok" : "zero"}">${money(l.variance)}</td>
        <td>${l.findings.map((f) => `<div><span class="chip ${f.code === "rate_below_contract" ? "ok" : "warn"}">${esc(f.code.replace(/_/g, " "))}</span>
          <span class="small muted">${esc(f.message)}</span></div>`).join("")}</td></tr>`))}</div>
    ${a.not_billed.length ? `<p class="muted small">Activity not billed yet: ${a.not_billed.map((n) => `${esc(n.description)} (${esc(n.qty)})`).join(", ")}</p>` : ""}`;
  el.querySelectorAll("[data-status]").forEach((b) => b.addEventListener("click", async () => {
    await post(`/billing/invoices/${id}/status`, { status: b.dataset.status });
    toast(`Invoice marked ${b.dataset.status}`);
    render();
  }));
  $("#copy-dispute", el).addEventListener("click", async () => {
    const lines = a.lines.filter((l) => Number(l.variance) > 0).map((l) =>
      `- ${l.description} (${l.charge_code || "no code"}): billed ${money(l.amount)}, expected ${money(l.expected)}. ` +
      l.findings.map((f) => f.message).join("; "));
    const text = `Hi ${inv.vendor} billing team,\n\nWe reviewed invoice ${inv.number} for ${inv.period_start} to ${inv.period_end} ` +
      `against our rate card and the activity in your WMS, and need these lines corrected or supported before we pay:\n\n` +
      `${lines.join("\n")}\n\nTotal in question: ${money(a.overbilled)} (billed ${money(a.billed_total)}, expected ${money(a.expected_total)}).\n\nThanks`;
    try { await navigator.clipboard.writeText(text); toast("Dispute note copied"); }
    catch (_) { openDrawer("Dispute note", `<pre class="note">${esc(text)}</pre>`); }
  });
}

// ---- connectors ----------------------------------------------------------------------------------------

const CATEGORY = { commerce: "Commerce", pos: "Point of sale", warehouse: "Warehouse", workspace: "Google Workspace",
  messaging: "Messaging", ai: "AI", other: "Other" };

function modeLabel(c) {
  if (c.mode === "live") return ["live", "Live"];
  if (c.mode === "files") return ["live", "Live: export files"];
  if (c.mode === "planned") return ["planned", "Planned"];
  if (c.mode === "disabled") return ["disabled", "Not set up"];
  if (c.key === "iqmetrix_rq") return ["demo", "Demo data · upload works"];
  if (c.pulls) return ["demo", "Demo data"];
  if (c.category === "ai") return ["demo", "Not set up: rules only"];
  if (c.capabilities.includes("notify")) return ["demo", "Not set up: outbox only"];
  return ["demo", "Not set up"];
}

async function renderConnectors(view) {
  const [conns, notes, events, digest, jobsInfo, handoffs, inbox, google] = await Promise.all([api("/connectors"),
    api("/notifications?limit=40"), api("/calendar/events"), api("/digest"), api("/jobs"), api("/handoffs?partner=muse"),
    api("/inbox?status=open"), api("/google/status")]);
  const groups = Object.keys(CATEGORY).map((cat) => [cat, conns.filter((c) => c.category === cat)]).filter(([, l]) => l.length);
  view.innerHTML = `
    <p class="muted" style="margin-top:0">Connectors without credentials serve sample data so you can try everything. Put credentials
      in <code>.env</code> (see <code>.env.example</code>) and restart to go live.</p>
    <div class="card pad" style="margin-bottom:8px">${google.method
      ? `<b class="t-ok">Google connected</b> <span class="muted">(${esc(google.method === "oauth" ? "Google account login" : "service account")})</span>`
      : `<b>Google not connected.</b> <span class="muted">Sheets, Drive, Gmail, Calendar, RQ files and Muse need it. Set
         <code>GOOGLE_OAUTH_CLIENT_ID</code>/<code>GOOGLE_OAUTH_CLIENT_SECRET</code>, then run <code>python -m opshub google-login</code>
         (or set <code>GOOGLE_SERVICE_ACCOUNT_JSON</code>). See docs/SETUP.md.</span>`}</div>
    ${groups.map(([cat, list]) => `<div class="section-head"><h3>${esc(CATEGORY[cat])}</h3></div>
      <div class="conn-grid">${list.map(connCard).join("")}</div>`).join("")}
    <div class="section-head"><h2>Muse handoff</h2><span class="muted small">Daily brief out at ${esc(jobsInfo.muse_export_time)}
      (${esc(jobsInfo.timezone)}); Muse's files are read on every sync</span><div class="grow"></div>
      <button class="btn small" data-job="muse_export">Send to Muse now</button></div>
    <div class="card">${table(["From Muse (open)", "Kind", "Due", ""], inbox.filter((i) => i.source === "muse").map((i) => `<tr>
      <td><b>${esc(i.title)}</b><div class="small muted">${esc((i.detail || "").slice(0, 200))}</div></td>
      <td><span class="chip ${i.kind === "alert" ? i.severity : ""}">${esc(i.kind)}</span></td><td>${i.due ? fmtDate(i.due) : "-"}</td>
      <td><button class="btn small" data-done="${i.id}">Done</button></td></tr>`), "Nothing open from Muse.")}</div>
    <div class="card" style="margin-top:12px">${table(["When", "Direction", "File", "Summary"], handoffs.map((h) => `<tr>
      <td class="small">${fmtDateTime(h.created_at)}</td><td>${h.direction === "out" ? "to Muse" : "from Muse"}</td>
      <td class="mono">${esc(h.name)}</td><td class="small muted">${esc(h.summary || "")}</td></tr>`), "No files exchanged yet.")}</div>
    <div class="section-head"><h2>Schedule</h2><span class="muted small">${jobsInfo.scheduler ? `Syncing every ${jobsInfo.sync_interval_minutes} min;
      digest at ${esc(jobsInfo.digest_time)}` : "Scheduler is off (OPSHUB_SCHEDULER=off)"}</span></div>
    <div class="card">${table(["Job", "Last run", "Result", ""], jobsInfo.jobs.map((j) => `<tr><td class="mono">${esc(j.name)}</td>
      <td class="small">${j.last ? fmtDateTime(j.last.at) : "never"}</td>
      <td class="small muted">${j.last ? esc(JSON.stringify(j.last.result).slice(0, 160)) : ""}</td>
      <td><button class="btn small" data-job="${esc(j.name)}">Run now</button></td></tr>`))}</div>
    <div class="section-head"><h2>Calendar preview</h2><span class="muted small">What Google Calendar will get: next 30 days</span></div>
    <div class="card">${table(["Date", "Event", { label: "Details", cls: "hide-sm" }], events.map((e) => `<tr>
      <td>${fmtDate(e.date, { weekday: "short" })}</td><td>${esc(e.summary)}</td><td class="notes hide-sm">${esc(e.description)}</td></tr>`),
      "Nothing scheduled.")}</div>
    <div class="section-head"><h2>Daily digest</h2><div class="grow"></div><button class="btn small" id="send-digest">Send digest now</button></div>
    <div class="card pad"><pre class="note">${esc(digest)}</pre></div>
    <div class="section-head"><h2>Message outbox</h2><span class="muted small">"demo" = would have been sent if the channel were set up</span></div>
    <div class="card">${table(["When", "Channel", "Status", "Message"], notes.map((n) => `<tr><td class="small">${fmtDateTime(n.created_at)}</td>
      <td>${esc(n.channel)}</td><td><span class="chip ${n.status === "sent" ? "ok" : n.status === "failed" ? "urgent" : ""}">${esc(n.status)}</span></td>
      <td class="notes"><pre class="note" style="background:none;padding:0">${esc(n.body)}</pre>${n.error ? `<div class="t-urgent small">${esc(n.error)}</div>` : ""}</td></tr>`),
      "No messages yet.")}</div>`;
  view.querySelectorAll("[data-sync]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await post(`/connectors/${b.dataset.sync}/sync`);
      toast(`${b.dataset.sync}: ${r.status}${r.error ? " - " + r.error.split("\n")[0] : ""}`);
    } finally { render(); }
  }));
  view.querySelectorAll("[data-import]").forEach((inp) => inp.addEventListener("change", (e) => importFile(inp.dataset.import, e.target.files[0])));
  view.querySelectorAll("[data-job]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const r = await post(`/jobs/${b.dataset.job}`);
      toast(`${b.dataset.job}: ${JSON.stringify(r.result).slice(0, 140)}`);
    } catch (err) { toast(err.message); }
    render();
  }));
  view.querySelectorAll("[data-done]").forEach((b) => b.addEventListener("click", async () => {
    await post(`/inbox/${b.dataset.done}/done`);
    toast("Marked done");
    render();
  }));
  $("#send-digest", view).addEventListener("click", async () => {
    const r = await post("/digest");
    toast(r.sent.length ? `Digest: ${r.sent.join(", ")}` : "No digest channels set up");
    render();
  });
}

function connCard(c) {
  const [cls, label] = modeLabel(c);
  const run = c.last_run;
  const stats = run && run.stats ? Object.entries(run.stats).filter(([k, v]) => v && !k.startsWith("signals_") && k !== "warnings")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ") : "";
  const warnings = (run && run.stats && run.stats.warnings) || [];
  return `<div class="card conn">
    <div class="row"><span class="name">${esc(c.name)}</span><div class="grow"></div><span class="badge mode-${cls}">${esc(label)}</span></div>
    <div>${chips(c.capabilities)}</div>
    <div class="small muted">${esc(c.status_note)}</div>
    ${c.missing_settings.length && c.mode !== "live" ? `<div class="small">Needs: ${c.missing_settings.map((k) => `<code>${esc(k)}</code>`).join(" ")}</div>` : ""}
    ${run ? `<div class="small"><span class="${run.status === "ok" ? "t-ok" : run.status === "error" ? "t-urgent" : "muted"}">Last sync ${esc(run.status)}</span>
      <span class="muted">${fmtDateTime(run.finished_at)}</span>${c.interval_minutes ? `<span class="muted"> · every ${c.interval_minutes} min</span>` : ""}
      ${stats ? `<div class="muted">${esc(stats)}</div>` : ""}
      ${warnings.map((w) => `<div class="t-warn">Skipped: ${esc(w.split("\n")[0])}</div>`).join("")}
      ${run.error && run.status !== "ok" ? `<div class="muted">${esc(run.error.split("\n")[0])}</div>` : ""}</div>` : ""}
    <div class="row">${c.pulls && c.mode !== "planned" ? `<button class="btn small" data-sync="${esc(c.key)}">Sync now</button>` : ""}
      ${c.imports ? `<label class="btn small">Import file<input type="file" data-import="${esc(c.key)}" accept=".csv,.tsv,.xlsx,.xlsm" hidden></label>` : ""}
      ${c.docs_url ? `<a class="small" href="${esc(c.docs_url)}" target="_blank" rel="noopener">API docs</a>` : ""}</div>
  </div>`;
}

// ---- shell -------------------------------------------------------------------------------------------------

// ---- setup ---------------------------------------------------------------------------------------------------

const MODE_CHIP = { live: ["ok", "Live"], files: ["ok", "Live: files"], demo: ["warn", "Sample data"],
  disabled: ["", "Off"], planned: ["", "Planned"] };

async function renderSetup(view) {
  const st = await api("/setup");
  const all = st.sections.flatMap((s) => s.status);
  const connected = all.filter((c) => c.mode === "live" || c.mode === "files").length;
  view.innerHTML = `
    <div class="card pad"><div class="row"><h2 style="margin:0">Connect your systems</h2><div class="grow"></div>
      <b>${connected} of ${all.length}</b><span class="muted">connected</span></div>
      <p class="muted small" style="margin-bottom:0">Values saved here are stored in the Ops Hub database; anything set in
        <code>.env</code> wins and shows as read-only. Systems not connected keep running on sample data. Systems without
        API access yet can run on <b>export files</b>: drop them in that system's Drive folder or use Import file.
        This page's address for OAuth redirects: <code>${esc(st.base_url)}</code>.</p></div>
    ${st.sections.map(setupCard).join("")}`;
  view.querySelectorAll("form[data-section]").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = Object.fromEntries([...new FormData(f)].filter(([, v]) => v !== ""));
    f.querySelectorAll("input[data-clear]:checked").forEach((c) => { values[c.dataset.clear] = ""; });
    const r = await post("/setup", { values });
    toast(`Saved ${r.saved.length} setting(s)` + (r.kept_from_env.length ? `; .env still sets ${r.kept_from_env.join(", ")}` : ""));
    render();
  }));
  view.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const out = $(`#test-${b.dataset.test}`, view);
    b.disabled = true;
    out.innerHTML = `<span class="muted">Testing...</span>`;
    try {
      const r = await post(`/setup/test/${b.dataset.test}`);
      out.innerHTML = `<span class="${r.ok ? "t-ok" : "t-urgent"}">${r.ok ? "✓" : "✗"} ${esc(r.message)}</span>`;
    } catch (err) { out.innerHTML = `<span class="t-urgent">${esc(err.message)}</span>`; }
    b.disabled = false;
  }));
  view.querySelectorAll("[data-action-post]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    const out = $(`#action-${b.dataset.section}`, view);
    out.innerHTML = `<span class="muted">Working... (this can take a minute)</span>`;
    try {
      const r = await post(b.dataset.actionPost.replace(/^\/api/, ""));
      if (r.token) {
        out.innerHTML = `<div class="t-ok">✓ New read-only key (copy it now; it is shown once):</div><pre class="note">${esc(r.token)}</pre>
          <div class="small muted">Example: <code>curl -H "Authorization: Bearer ${esc(r.token)}" ${esc(location.origin)}/api/trackers/outbound</code></div>`;
        b.disabled = false;
        return;
      }
      out.innerHTML = `<div class="t-ok">✓ Done in ${esc(r.account || "")}.</div>
        <div class="small muted">${r.created && r.created.length ? "Created: " + esc(r.created.join(", ")) : "Everything already existed."}
        ${r.saved && r.saved.length ? "<br>Saved: " + esc(r.saved.join(", ")) : ""} ${r.calendar ? "<br>Calendar: " + esc(r.calendar) : ""}</div>
        ${r.folder_url ? `<a href="${esc(r.folder_url)}" target="_blank" rel="noopener">Open the Ops Hub folder</a>` : ""}`;
      setTimeout(render, 2500);
    } catch (err) { out.innerHTML = `<span class="t-urgent">${esc(err.message)}</span>`; }
    b.disabled = false;
  }));
  view.querySelectorAll("[data-action-get]").forEach((b) => b.addEventListener("click", async () => {
    const out = $(`#action-${b.dataset.section}`, view);
    const chats = await api(b.dataset.actionGet.replace(/^\/api/, ""));
    out.innerHTML = chats.length ? chats.map((c) => `<div class="row small"><code>${esc(c.id)}</code> ${esc(c.name)}
      <button class="btn small" data-use-chat="${esc(c.id)}">Use for alerts</button></div>`).join("")
      : `<span class="muted small">No chats yet. Add the bot to your group and send /start, then try again.</span>`;
    out.querySelectorAll("[data-use-chat]").forEach((u) => u.addEventListener("click", async () => {
      await post("/setup", { values: { TELEGRAM_CHAT_ID: u.dataset.useChat } });
      toast("Alerts chat saved");
      render();
    }));
  }));
}

function setupCard(sec) {
  const chips = sec.status.map((c) => { const [cls, label] = c.mode === "demo" && !c.pulls ? ["", "Not connected"]
      : MODE_CHIP[c.mode] || ["", c.mode];
    return `<span class="chip ${cls}" title="${esc(c.missing.join(", "))}">${esc(c.name)}: ${esc(label)}</span>`; }).join("");
  const fields = sec.fields.map((f) => `<label class="field">${esc(f.label)}
      <input name="${esc(f.key)}" type="${f.secret ? "password" : "text"}" autocomplete="off"
        ${f.source === "env" ? "disabled" : ""} value="${f.secret ? "" : esc(f.value)}"
        placeholder="${esc(f.secret && f.set ? f.value + " (saved)" : f.help || "")}">
      <span class="small muted">${f.source === "env" ? "set in .env" : f.secret && f.set ? "leave blank to keep" : f.value ? esc(f.help) : ""}</span>
      ${f.source === "saved" ? `<span class="small"><input type="checkbox" data-clear="${esc(f.key)}"> clear</span>` : ""}
    </label>`).join("");
  const actions = (sec.actions || []).map((a) => a.href
    ? `<a class="btn small primary" href="${esc(a.href)}">${esc(a.label)}</a>`
    : a.post ? `<button type="button" class="btn small primary" data-section="${esc(sec.key)}" data-action-post="${esc(a.post)}">${esc(a.label)}</button>`
    : `<button type="button" class="btn small" data-section="${esc(sec.key)}" data-action-get="${esc(a.get)}">${esc(a.label)}</button>`).join("");
  const tests = sec.status.map((c) => `<button type="button" class="btn small" data-test="${esc(c.key)}">Test ${esc(c.name)}</button>
    <div id="test-${esc(c.key)}" class="small" style="flex-basis:100%"></div>`).join("");
  return `<div class="card pad" style="margin-top:12px">
    <div class="row"><h3 style="margin:0;text-transform:none;letter-spacing:0;color:var(--text);font-size:15px">${esc(sec.title)}</h3>
      <div class="grow"></div>${chips}</div>
    <p class="muted small">${esc(sec.intro)}</p>
    <form data-section="${esc(sec.key)}" class="form-grid">${fields}
      <div class="row" style="grid-column:1/-1"><button class="btn primary small">Save</button>${actions}</div>
    </form>
    <div id="action-${esc(sec.key)}" class="small" style="margin-top:8px"></div>
    <div class="row" style="margin-top:8px">${tests}</div>
  </div>`;
}

// ---- charts (inline SVG) ----------------------------------------------------------------
// Marks use the --series-N tokens (validated for the light and dark panels); all text uses text tokens.
// Every chart has a hover/focus tooltip and a data table, so no value depends on color or hover alone.

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const OTHER = "var(--series-other)";
const SVGNS = "http://www.w3.org/2000/svg";

const compact = (n) => {
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n * 10) / 10);
};

function niceStep(raw, integer) {
  if (raw <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  return integer ? Math.max(1, Math.ceil(step)) : step;
}

function svgEl(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

// Rounded on the data end (top or right), square at the baseline.
function topRounded(x, yTop, w, yBottom, r) {
  r = Math.max(0, Math.min(r, w / 2, yBottom - yTop));
  return `M${x},${yBottom}V${yTop + r}Q${x},${yTop} ${x + r},${yTop}H${x + w - r}Q${x + w},${yTop} ${x + w},${yTop + r}V${yBottom}Z`;
}
function rightRounded(xLeft, y, xRight, h, r) {
  r = Math.max(0, Math.min(r, h / 2, xRight - xLeft));
  return `M${xLeft},${y}H${xRight - r}Q${xRight},${y} ${xRight},${y + r}V${y + h - r}Q${xRight},${y + h} ${xRight - r},${y + h}H${xLeft}Z`;
}

const tip = (() => {
  const el = document.createElement("div");
  el.className = "viz-tip";
  el.hidden = true;
  document.body.appendChild(el);
  return el;
})();

// rows: [{value, label, color?}] - value leads, label follows; untrusted text goes in via textContent.
function showTip(title, rows, x, y) {
  tip.replaceChildren();
  const h = document.createElement("div");
  h.className = "viz-tip-title";
  h.textContent = title;
  tip.appendChild(h);
  for (const r of rows) {
    const line = document.createElement("div");
    line.className = "viz-tip-row";
    if (r.color) {
      const key = document.createElement("i");
      key.className = "viz-key";
      key.style.background = r.color;
      line.appendChild(key);
    }
    const v = document.createElement("b");
    v.textContent = r.value;
    const k = document.createElement("span");
    k.textContent = r.label;
    line.append(v, k);
    tip.appendChild(line);
  }
  tip.hidden = false;
  const w = tip.offsetWidth, ht = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(x + 14, window.innerWidth - w - 8)) + "px";
  tip.style.top = Math.max(8, Math.min(y - ht - 10, window.innerHeight - ht - 8)) + "px";
}
const hideTip = () => { tip.hidden = true; };

function hoverable(target, onShow, onHide) {
  const at = (e) => {
    if (e.clientX !== undefined) return [e.clientX, e.clientY];
    const b = target.getBoundingClientRect();
    return [b.left + b.width / 2, b.top];
  };
  target.addEventListener("pointermove", (e) => onShow(...at(e)));
  target.addEventListener("focus", (e) => onShow(...at(e)));
  target.addEventListener("pointerleave", () => { hideTip(); onHide && onHide(); });
  target.addEventListener("blur", () => { hideTip(); onHide && onHide(); });
}

function legend(series) {
  return series.length < 2 ? "" : `<div class="viz-legend">${series.map((s) =>
    `<span><i class="viz-swatch" style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>`;
}

function dataTable(headers, rows) {
  return `<details class="viz-table"><summary>Data table</summary>${table(headers, rows.map((r) =>
    `<tr>${r.map((c, i) => `<td class="${i ? "num" : ""}">${esc(c)}</td>`).join("")}</tr>`))}</details>`;
}

function vizCard(title, sub, body) {
  return `<div class="card viz"><div class="viz-title">${esc(title)}</div>${sub ? `<div class="viz-sub">${sub}</div>` : ""}${body}</div>`;
}

// Draw once the element is in the page (it needs its width); redraw on resize.
function mountChart(el, draw) {
  el.dataset.viz = "1";
  el._draw = () => { el.replaceChildren(); draw(el, Math.max(260, el.clientWidth)); };
  el._draw();
}
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => document.querySelectorAll("[data-viz]").forEach((el) => el._draw && el._draw()), 150);
});

// Columns over days, stacked when there is more than one series.
// rows: [{x: "YYYY-MM-DD", values: {key: n}}]; series: [{key, label, color}]
function drawColumns(el, width, { rows, series, integer = true, fmt = compact, height = 180 }) {
  const m = { l: 38, r: 6, t: 10, b: 24 };
  const pw = width - m.l - m.r, ph = height - m.t - m.b;
  const totals = rows.map((r) => series.reduce((a, s) => a + (r.values[s.key] || 0), 0));
  const step = niceStep(Math.max(...totals, 0) / 4, integer);
  const top = Math.max(step, step * Math.ceil(Math.max(...totals, 0) / step));
  const y = (v) => m.t + ph - (v / top) * ph;
  const svg = svgEl("svg", { width, height, role: "group", "aria-label": series.map((s) => s.label).join(", ") }, el);
  for (let v = 0; v <= top + 1e-9; v += step) {
    if (v > 0) svgEl("line", { x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), class: "viz-gridline" }, svg);
    svgEl("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, svg).textContent = fmt(v);
  }
  const band = pw / Math.max(rows.length, 1);
  const bw = Math.max(2, Math.min(24, band - 2));
  const hl = svgEl("rect", { x: 0, y: m.t, width: band, height: ph, class: "viz-band", visibility: "hidden" }, svg);
  const marks = svgEl("g", {}, svg);
  const labelEvery = Math.ceil(rows.length / Math.max(1, Math.floor(pw / 58)));
  rows.forEach((r, i) => {
    const x = m.l + i * band + (band - bw) / 2;
    let cum = 0;
    const visible = series.filter((s) => (r.values[s.key] || 0) > 0);
    visible.forEach((s, j) => {
      const v = r.values[s.key];
      const yTop = y(cum + v), yBottom = y(cum) - (cum > 0 ? 2 : 0);  // 2px surface gap between segments
      cum += v;
      if (yBottom - yTop <= 0) return;
      svgEl("path", { d: j === visible.length - 1 ? topRounded(x, yTop, bw, yBottom, 4)
        : `M${x},${yBottom}V${yTop}H${x + bw}V${yBottom}Z`, fill: s.color }, marks);
    });
    if (i % labelEvery === 0 || i === rows.length - 1 && rows.length < 10) {
      svgEl("text", { x: m.l + i * band + band / 2, y: height - 6, "text-anchor": "middle" }, svg).textContent = fmtDate(r.x);
    }
    const hit = svgEl("rect", { x: m.l + i * band, y: m.t, width: band, height: ph, class: "viz-hit", tabindex: 0,
      "aria-label": `${fmtDate(r.x)}: ${series.map((s) => `${s.label} ${fmt(r.values[s.key] || 0)}`).join(", ")}` }, svg);
    hoverable(hit, (cx, cy) => {
      hl.setAttribute("x", m.l + i * band);
      hl.setAttribute("visibility", "visible");
      const lines = series.map((s) => ({ value: fmt(r.values[s.key] || 0), label: s.label, color: series.length > 1 ? s.color : null }));
      if (series.length > 1) lines.push({ value: fmt(totals[i]), label: "total" });
      showTip(fmtDate(r.x, { weekday: "short" }), lines, cx, cy);
    }, () => hl.setAttribute("visibility", "hidden"));
  });
  svgEl("line", { x1: m.l, x2: width - m.r, y1: y(0), y2: y(0), class: "viz-axis" }, svg);
}

// Horizontal bars, one series, value at the tip. items: [{label, value, text}]
function drawBars(el, width, { items, color = SERIES[0], fmt = compact }) {
  const rowH = 30, bar = 14;
  const labelW = Math.min(150, Math.max(70, Math.round(width * 0.32)));
  const valueW = 64;
  const height = Math.max(rowH, items.length * rowH) + 4;
  const svg = svgEl("svg", { width, height, role: "group" }, el);
  const max = Math.max(...items.map((d) => d.value), 0) || 1;
  const x0 = labelW + 8, span = width - x0 - valueW;
  svgEl("line", { x1: x0, x2: x0, y1: 0, y2: height - 4, class: "viz-axis" }, svg);
  items.forEach((d, i) => {
    const yMid = i * rowH + rowH / 2;
    const label = svgEl("text", { x: labelW, y: yMid + 4, "text-anchor": "end", class: "viz-label" }, svg);
    label.textContent = d.label.length > 22 ? d.label.slice(0, 21) + "…" : d.label;
    const w = Math.max(0, (d.value / max) * span);
    if (w > 0) svgEl("path", { d: rightRounded(x0, yMid - bar / 2, x0 + w, bar, 4), fill: color }, svg);
    svgEl("text", { x: x0 + w + 6, y: yMid + 4, class: "viz-value" }, svg).textContent = d.text ?? fmt(d.value);
    const hit = svgEl("rect", { x: 0, y: i * rowH, width, height: rowH, class: "viz-hit", tabindex: 0,
      "aria-label": `${d.label}: ${d.text ?? fmt(d.value)}` }, svg);
    hoverable(hit, (cx, cy) => showTip(d.label, [{ value: d.text ?? fmt(d.value), label: d.unit || "" }], cx, cy));
  });
}

// One bar split into consecutive parts (e.g. stage times). parts: [{label, value, color}]
function drawSegments(el, width, { parts, fmt }) {
  const height = 28, bar = 18, y = 4;
  const svg = svgEl("svg", { width, height, role: "group" }, el);
  const total = parts.reduce((a, p) => a + (p.value || 0), 0) || 1;
  let x = 0;
  parts.forEach((p, i) => {
    const w = ((p.value || 0) / total) * width - (i < parts.length - 1 ? 2 : 0);
    if (w > 0) {
      svgEl("path", { d: i === parts.length - 1 ? rightRounded(x, y, x + w, bar, 4) : `M${x},${y}H${x + w}V${y + bar}H${x}Z`,
        fill: p.color }, svg);
      const hit = svgEl("rect", { x, y: 0, width: w + 2, height, class: "viz-hit", tabindex: 0,
        "aria-label": `${p.label}: ${fmt(p.value)}` }, svg);
      hoverable(hit, (cx, cy) => showTip(p.label, [{ value: fmt(p.value), label: "median" }], cx, cy));
    }
    x += w + 2;
  });
}

// ---- trackers: inbound / outbound / productivity -----------------------------------------

const TRACKER_TABS = new Set(["inbound", "outbound", "productivity"]);
const num = (v, unit = "") => v == null ? "-" : `${Number(v).toLocaleString()}${unit}`;
const hrs = (v) => v == null ? "-" : v >= 48 ? `${(v / 24).toFixed(1)} d` : `${v} h`;

function trackerHead(title, csv) {
  return `<div class="section-head"><h2>${esc(title)}</h2>
    ${pills([["7", "7 days"], ["30", "30 days"], ["90", "90 days"]], String(state.trackDays), "days")}
    <div class="grow"></div><span class="muted small">Refreshes every minute</span>
    ${csv.map(([name, label]) => `<a class="btn small" href="/api/export/${name}.csv?days=${state.trackDays}">${esc(label)}</a>`).join("")}</div>`;
}
function bindTracker(view) {
  view.querySelectorAll("[data-days]").forEach((b) => b.addEventListener("click", () => {
    state.trackDays = Number(b.dataset.days); render();
  }));
  view.querySelectorAll("[data-order]").forEach((tr) => tr.addEventListener("click", () => openOrder(Number(tr.dataset.order))));
  view.querySelectorAll("[data-shipment]").forEach((tr) => tr.addEventListener("click", () => openShipment(Number(tr.dataset.shipment))));
}
const statusChip = (s, late) => `<span class="chip ${late ? "urgent" : s === "receiving" ? "info" : s === "received" ? "ok" : ""}">${esc(late ? "late" : s)}</span>`;

async function renderInbound(view) {
  const d = await api(`/trackers/inbound?days=${state.trackDays}`);
  const k = d.kpis;
  const series = [{ key: "units", label: "Units received", color: SERIES[0] }];
  view.innerHTML = `${trackerHead("Inbound", [["inbound", "Export CSV"]])}
    <div class="kpis">
      ${kpi("Open receipts", k.open_receipts, `<b class="${k.late_receipts ? "t-urgent" : ""}">${k.late_receipts} late</b> · ${k.on_dock} on the dock`)}
      ${kpi("Units due in 7 days", num(k.units_due_7d), `${k.freight_open} inbound shipments · <b class="${k.freight_late ? "t-warn" : ""}">${k.freight_late} late</b>`)}
      ${kpi(`Received (${d.days}d)`, num(k.received_units), `${k.received_receipts} receipts · ${num(k.received_pallets)} pallets`)}
      ${kpi("Arrived on time", num(k.on_time_pct, "%"), `fill rate ${num(k.fill_rate_pct, "%")} · <b class="${k.short_receipts ? "t-warn" : ""}">${k.short_receipts} short</b>`)}
      ${kpi("Dock to stock", hrs(k.dock_to_stock_hours), "median, arrival to put-away")}
    </div>
    <div class="section-head"><h2>Receiving</h2></div>
    ${vizCard("Units received per day", "", `<div id="ch-received" class="viz-plot"></div>` +
      dataTable(["Date", "Units", "Receipts"], d.series.map((r) => [r.date, r.units, r.receipts])))}
    <div class="viz-grid">
      ${vizCard("Units due, next 8 days", "Late receipts count as due today.", `<div id="ch-upcoming" class="viz-plot"></div>` +
        dataTable(["Date", "Units", "Receipts"], d.upcoming.map((r) => [r.date, r.units, r.receipts])))}
      ${vizCard(`Units received by location (${d.days}d)`, "", `<div id="ch-byloc" class="viz-plot"></div>` +
        dataTable(["Location", "Units", "Receipts", "Open"], d.by_location.map((r) => [r.location, r.received_units, r.receipts, r.open])))}
    </div>
    <div class="section-head"><h2>Open receipts</h2><span class="muted small">3PL receivers, store purchase orders, transfers</span></div>
    <div class="card">${table(["Status", "Reference", "From", "To", "Expected", { label: "Units", cls: "num" }, { label: "Pallets", cls: "num" }, "Notes"],
      d.open.map((r) => `<tr><td>${statusChip(r.status, r.late)}${r.late ? ` <span class="small muted">${r.days_late}d</span>` : ""}</td>
        <td><b>${esc(r.reference || "-")}</b><div class="small muted">${esc(r.source)} ${esc(r.kind.replace("_", " "))}</div></td>
        <td>${esc(r.vendor || "")}</td><td>${esc(r.location)}</td>
        <td>${fmtDate(r.expected_at)}<div class="small muted">${rel(r.expected_at)}</div></td>
        <td class="num">${num(r.units_expected)}</td><td class="num">${num(r.pallets)}</td>
        <td class="notes">${esc(r.notes || r.lines.map((l) => `${l.sku} ×${l.expected}`).join(", "))}</td></tr>`), "Nothing expected.")}</div>
    <div class="section-head"><h2>Inbound freight</h2></div>
    <div class="card">${table(["Reference", "Status", "Carrier", "To", "ETA", ""], d.freight.map((f) => `<tr class="click" data-shipment="${f.id}">
        <td><b>${esc(f.reference)}</b><div class="small muted">${esc(f.direction)} ${esc(f.mode)}</div></td>
        <td>${esc(f.status.replace(/_/g, " "))}</td><td>${esc(f.carrier || "")}${f.agent ? `<div class="small muted">via ${esc(f.agent)}</div>` : ""}</td>
        <td>${esc(f.destination || "")}</td><td>${fmtDate(f.eta)}<div class="small muted">${rel(f.eta)}</div></td>
        <td>${f.late ? `<span class="chip urgent">late</span>` : ""}${f.stale ? `<span class="chip warn">no update</span>` : ""}${f.hazmat ? `<span class="chip warn">hazmat</span>` : ""}</td></tr>`),
      "No inbound freight in transit.")}</div>
    <div class="section-head"><h2>Received in the last ${d.days} days</h2></div>
    <div class="card">${table(["Reference", "To", "From", "Expected", "Arrived", "Put away", { label: "Units", cls: "num" }, { label: "Dock to stock", cls: "num" }],
      d.received.map((r) => `<tr><td><b>${esc(r.reference || "-")}</b><div class="small muted">${esc(r.source)}</div></td>
        <td>${esc(r.location)}</td><td>${esc(r.vendor || "")}</td><td>${fmtDate(r.expected_at)}</td><td>${fmtDateTime(r.arrived_at)}</td>
        <td>${fmtDateTime(r.received_at)}</td>
        <td class="num">${num(r.units_received)}${r.short_units ? ` <span class="chip warn">${r.short_units} short</span>` : ""}</td>
        <td class="num">${hrs(r.dock_to_stock_hours)}</td></tr>`), "Nothing received in this window.")}</div>`;
  mountChart($("#ch-received", view), (el, w) => drawColumns(el, w, { series,
    rows: d.series.map((r) => ({ x: r.date, values: { units: r.units } })) }));
  mountChart($("#ch-upcoming", view), (el, w) => drawColumns(el, w, { height: 150,
    series: [{ key: "units", label: "Units due", color: SERIES[0] }],
    rows: d.upcoming.map((r) => ({ x: r.date, values: { units: r.units } })) }));
  mountChart($("#ch-byloc", view), (el, w) => drawBars(el, w, {
    items: d.by_location.map((r) => ({ label: r.location, value: r.received_units, unit: `units on ${r.receipts} receipts` })) }));
  bindTracker(view);
}

function originSeries(byOrigin) {
  // Color follows the location (sorted by code), not its rank; past three, the rest fold into Other.
  const codes = byOrigin.map((o) => o.origin).sort();
  const main = codes.slice(0, 3);
  const series = main.map((c, i) => ({ key: c, label: c, color: SERIES[i] }));
  if (codes.length > 3) series.push({ key: "__other", label: "Other", color: OTHER });
  return { series, fold: (code) => main.includes(code) ? code : "__other" };
}

async function renderOutbound(view) {
  const d = await api(`/trackers/outbound?days=${state.trackDays}`);
  const k = d.kpis, st = d.stages;
  const { series, fold } = originSeries(d.by_origin);
  const rows = d.series.map((r) => {
    const values = {};
    for (const [code, n] of Object.entries(r.by_origin || {})) values[fold(code)] = (values[fold(code)] || 0) + n;
    return { x: r.date, values };
  });
  const stageParts = [["Order to pick", st.order_to_pick_hours], ["Pick to pack", st.pick_to_pack_hours],
    ["Pack to ship", st.pack_to_ship_hours]].map(([label, value], i) => ({ label, value, color: SERIES[i] }));
  view.innerHTML = `${trackerHead("Outbound", [["outbound", "Shipped CSV"], ["backlog", "Backlog CSV"]])}
    <div class="kpis">
      ${kpi("Shipped today", num(k.shipped_today), `${num(k.shipped)} in ${d.days} days · ${num(k.shipped_units)} units`)}
      ${kpi("Backlog", num(k.backlog), `<b class="${k.backlog_over_48h ? "t-warn" : ""}">${k.backlog_over_48h} over 48h</b> · ${k.backlog_on_hold} on hold`)}
      ${kpi("Order to ship", hrs(k.order_to_ship_hours), `median · 90% within ${hrs(k.order_to_ship_p90_hours)}`)}
      ${kpi("Shipped within 48h", num(k.within_48h_pct, "%"), `within 24h ${num(k.within_24h_pct, "%")}`)}
      ${kpi("Ship to delivery", k.ship_to_deliver_days == null ? "-" : `${k.ship_to_deliver_days} d`,
        k.deadline_orders ? `deadline met ${num(k.deadline_met_pct, "%")} of ${k.deadline_orders}` : "median, carrier delivery scans")}
    </div>
    <div class="section-head"><h2>Volume</h2></div>
    ${vizCard("Orders shipped per day, by ship-from location", "", legend(series) + `<div id="ch-shipped" class="viz-plot"></div>` +
      dataTable(["Date", "Placed", "Shipped", "Delivered", ...series.map((s) => s.label)],
        d.series.map((r, i) => [r.date, r.placed, r.shipped, r.delivered, ...series.map((s) => rows[i].values[s.key] || 0)])))}
    <div class="viz-grid">
      ${vizCard("Backlog by age", "Open orders, by time since placed.", `<div id="ch-aging" class="viz-plot"></div>` +
        dataTable(["Age", "Orders"], d.aging.map((a) => [a.bucket, a.orders])))}
      ${vizCard("Warehouse stage times", `Median hours, ${st.orders} orders with warehouse scans.`,
        `<div id="ch-stages" class="viz-plot"></div><div class="viz-legend">${stageParts.map((p) =>
          `<span><i class="viz-swatch" style="background:${p.color}"></i>${esc(p.label)} <b>${hrs(p.value)}</b></span>`).join("")}</div>`)}
    </div>
    <div class="section-head"><h2>By ship-from location</h2></div>
    <div class="card">${table(["Ships from", { label: "Shipped", cls: "num" }, { label: "Units", cls: "num" }, { label: "Order to ship", cls: "num" }, { label: "Within 48h", cls: "num" }],
      d.by_origin.map((o) => `<tr><td><b>${esc(o.origin)}</b></td><td class="num">${num(o.shipped)}</td><td class="num">${num(o.units)}</td>
        <td class="num">${hrs(o.order_to_ship_hours)}</td><td class="num">${num(o.within_48h_pct, "%")}</td></tr>`), "Nothing shipped in this window.")}</div>
    <div class="section-head"><h2>Backlog</h2><span class="muted small">oldest first</span></div>
    <div class="card">${table(["Order", "Ships from", "Status", { label: "Age", cls: "num" }, "Warehouse", "Needed by"],
      d.backlog.map((b) => `<tr class="click" data-order="${b.id}"><td><b>${esc(b.number)}</b><div class="small muted">${esc(b.customer || "")}</div></td>
        <td>${esc(b.origin)}</td><td>${esc(b.status.replace("_", " "))}${b.order_type !== "standard" ? ` <span class="chip">${esc(b.order_type)}</span>` : ""}</td>
        <td class="num ${b.age_hours > 48 ? "t-warn" : ""}">${hrs(b.age_hours)}</td>
        <td class="small">${b.packed_at ? `packed ${fmtDateTime(b.packed_at)}` : b.picked_at ? `picked ${fmtDateTime(b.picked_at)}` : "-"}</td>
        <td>${b.required_by ? `${fmtDate(b.required_by)} <span class="small ${b.hours_left < 0 ? "t-urgent" : "muted"}">${rel(b.required_by)}</span>` : "-"}</td></tr>`),
      "No open orders.")}</div>
    <div class="section-head"><h2>Recently shipped</h2></div>
    <div class="card">${table(["Order", "Ships from", "Placed", "Picked", "Packed", "Shipped", "Delivered", { label: "To ship", cls: "num" }, "Handled by"],
      d.shipped_orders.slice(0, 60).map((o) => `<tr class="click" data-order="${o.id}"><td><b>${esc(o.number)}</b></td><td>${esc(o.origin)}</td>
        <td>${fmtDateTime(o.placed_at)}</td><td>${fmtDateTime(o.picked_at)}</td><td>${fmtDateTime(o.packed_at)}</td>
        <td>${fmtDateTime(o.shipped_at)}</td><td>${fmtDateTime(o.delivered_at)}</td><td class="num">${hrs(o.order_to_ship_hours)}</td>
        <td>${esc(o.handled_by || "")}</td></tr>`), "Nothing shipped in this window.")}</div>`;
  mountChart($("#ch-shipped", view), (el, w) => drawColumns(el, w, { series, rows }));
  mountChart($("#ch-aging", view), (el, w) => drawBars(el, w, {
    items: d.aging.map((a) => ({ label: a.bucket, value: a.orders, unit: "open orders" })) }));
  mountChart($("#ch-stages", view), (el, w) => drawSegments(el, w, { parts: stageParts, fmt: hrs }));
  bindTracker(view);
}

async function renderProductivity(view) {
  const d = await api(`/trackers/productivity?days=${state.trackDays}`);
  const k = d.kpis;
  const clocked = d.stores.filter((s) => s.labor_hours > 0);
  const people = d.people.filter((p) => Number(p.revenue) > 0).slice(0, 10);
  view.innerHTML = `${trackerHead("Productivity", [["productivity", "Staff CSV"], ["warehouse-handlers", "Warehouse CSV"]])}
    <div class="kpis">
      ${kpi(`Warehouse orders (${d.days}d)`, num(k.warehouse_orders_shipped), `${num(k.warehouse_orders_per_day)} per shipping day · ${num(k.warehouse_units_shipped)} units`)}
      ${kpi("Pick to ship", hrs(k.warehouse_pick_to_ship_hours), "median, partner 3PL")}
      ${kpi(`Store sales (${d.days}d)`, money(k.store_revenue), `${num(k.store_labor_hours)} labor hours clocked`)}
      ${kpi("Sales per labor hour", k.store_revenue_per_labor_hour == null ? "-" : money(k.store_revenue_per_labor_hour), "stores with a time clock")}
      ${kpi("On the clock now", num(k.staff_on_clock), d.on_clock.map((p) => esc(p.name)).join(", ") || "nobody clocked in")}
    </div>
    <div class="section-head"><h2>Warehouse</h2></div>
    ${vizCard("Partner 3PL orders shipped per day", "", `<div id="ch-wh" class="viz-plot"></div>` +
      dataTable(["Date", "Picked", "Packed", "Shipped", "Units shipped"], d.warehouse_series.map((r) => [r.date, r.picked, r.packed, r.shipped, r.units_shipped])))}
    <div class="card">${table(["Handler", { label: "Orders", cls: "num" }, { label: "Units", cls: "num" }, { label: "Days active", cls: "num" }, { label: "Orders / day", cls: "num" }, { label: "Pick to ship", cls: "num" }],
      d.handlers.map((h) => `<tr><td><b>${esc(h.name)}</b></td><td class="num">${num(h.orders)}</td><td class="num">${num(h.units)}</td>
        <td class="num">${num(h.days_active)}</td><td class="num">${num(h.orders_per_day)}</td><td class="num">${hrs(h.pick_to_ship_hours)}</td></tr>`),
      "No warehouse orders shipped in this window.")}</div>
    <p class="muted small">${esc(d.notes)}</p>
    <div class="section-head"><h2>Stores</h2></div>
    <div class="viz-grid">
      ${vizCard("Sales per labor hour, by store", "Stores with time-clock hours.", `<div id="ch-splh" class="viz-plot"></div>` +
        dataTable(["Store", "Sales", "Labor hours", "Sales / hour"], clocked.map((s) => [s.location, s.revenue, s.labor_hours, s.revenue_per_labor_hour])))}
      ${vizCard("Sales by employee", `Top ${people.length}, last ${d.days} days.`, `<div id="ch-people" class="viz-plot"></div>` +
        dataTable(["Employee", "Location", "Sales", "Units"], people.map((p) => [p.name, p.location, p.revenue, p.units])))}
    </div>
    <div class="card">${table(["Employee", "Location", { label: "Sales", cls: "num" }, { label: "Units", cls: "num" }, { label: "Hours", cls: "num" }, { label: "Sales / hour", cls: "num" }, { label: "Units / hour", cls: "num" }],
      d.people.map((p) => `<tr><td><b>${esc(p.name)}</b><div class="small muted">${esc(p.source)}</div></td><td>${esc(p.location)}</td>
        <td class="num">${money(p.revenue)}</td><td class="num">${num(p.units)}</td><td class="num">${p.hours ? num(p.hours) : "-"}</td>
        <td class="num">${p.revenue_per_hour == null ? "-" : money(p.revenue_per_hour)}</td><td class="num">${num(p.units_per_hour)}</td></tr>`),
      "No store sales in this window.")}</div>
    <div class="card" style="margin-top:12px">${table(["Store", { label: "Sales", cls: "num" }, { label: "Units", cls: "num" }, { label: "Labor hours", cls: "num" }, { label: "Sales / labor hour", cls: "num" }, { label: "Staff", cls: "num" }],
      d.stores.map((s) => `<tr><td><b>${esc(s.location)}</b></td><td class="num">${money(s.revenue)}</td><td class="num">${num(s.units)}</td>
        <td class="num">${s.labor_hours ? num(s.labor_hours) : "-"}</td><td class="num">${s.revenue_per_labor_hour == null ? "-" : money(s.revenue_per_labor_hour)}</td>
        <td class="num">${num(s.staff)}</td></tr>`), "No store data in this window.")}</div>`;
  mountChart($("#ch-wh", view), (el, w) => drawColumns(el, w, {
    series: [{ key: "shipped", label: "Orders shipped", color: SERIES[0] }],
    rows: d.warehouse_series.map((r) => ({ x: r.date, values: { shipped: r.shipped } })) }));
  mountChart($("#ch-splh", view), (el, w) => drawBars(el, w, {
    items: clocked.map((s) => ({ label: s.location, value: s.revenue_per_labor_hour || 0, text: money(s.revenue_per_labor_hour), unit: "per labor hour" })) }));
  mountChart($("#ch-people", view), (el, w) => drawBars(el, w, {
    items: people.map((p) => ({ label: p.name, value: Number(p.revenue), text: money(p.revenue), unit: `${p.location}, ${p.units} units` })) }));
  bindTracker(view);
}

// While a tracker tab is open (and the page is visible), pull fresh numbers every minute.
setInterval(() => {
  const tab = (location.hash || "#overview").slice(1);
  const busy = !$("#drawer").hidden || !tip.hidden || document.querySelector("details.viz-table[open]");
  if (TRACKER_TABS.has(tab) && document.visibilityState === "visible" && !busy) render();
}, 60000);

const VIEWS = { overview: renderOverview, orders: renderOrders, inbound: renderInbound, outbound: renderOutbound,
  productivity: renderProductivity, inventory: renderInventory, freight: renderFreight, billing: renderBilling,
  connectors: renderConnectors, setup: renderSetup };

async function refreshHeader() {
  const ov = await api("/overview");
  state.tz = ov.timezone;
  state.demo = ov.demo_mode;
  $("#demo-badge").hidden = !ov.demo_mode;
  $("#last-sync").textContent = ov.last_sync ? `Synced ${rel(ov.last_sync)}` : "Never synced";
}

async function render() {
  const tab = (location.hash || "#overview").slice(1);
  const fn = VIEWS[tab] || renderOverview;
  document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  const view = $("#view");
  try {
    await fn(view);
  } catch (err) {
    view.innerHTML = `<div class="card pad"><b class="t-urgent">Could not load this page.</b><div class="muted">${esc(err.message)}</div></div>`;
  }
}

async function runAction(btn, path, label) {
  btn.disabled = true;
  try {
    const r = await post(path);
    const rules = r.rules || {};
    const failed = (r.runs || []).filter((x) => x.status === "error").map((x) => x.connector);
    toast(`${label}: ${rules.open ?? 0} open alerts, ${rules.new ?? 0} new, ${rules.auto_resolved ?? 0} cleared` +
      (failed.length ? ` · failed: ${failed.join(", ")}` : ""));
    await refreshHeader();
    await render();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

$("#btn-sync").addEventListener("click", (e) => runAction(e.currentTarget, "/sync", "Synced"));
$("#btn-rules").addEventListener("click", (e) => runAction(e.currentTarget, "/rules/run", "Rules"));
window.addEventListener("hashchange", () => { closeDrawer(); render(); });

(async () => {
  const msg = new URLSearchParams(location.search).get("setup");
  if (msg) { toast(msg); history.replaceState(null, "", location.pathname + location.hash); }
  try { await refreshHeader(); } catch (_) { /* first load before tables exist */ }
  await render();
})();
