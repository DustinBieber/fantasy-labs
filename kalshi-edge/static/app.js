/* Kalshi Edge dashboard. Plain JS, no build step. Every number comes from /api; nothing is invented here. */
'use strict';

const COLORS = { kalshi: '#3987e5', fair: '#d95926', neutral: '#8b919a', grid: '#24272c', text: '#b4b9c0' };
const TABS = [
  ['edges', 'Top Edges'], ['sports', 'Sports'], ['parlays', 'Parlays'], ['follow', 'Follow'],
  ['econ', 'Commodities & Econ'], ['news', 'News'],
  ['flow', 'Flow'], ['markets', 'Markets & History'], ['trades', 'Trades'], ['settings', 'Settings'], ['audit', 'Audit'],
];
const S = { tab: 'edges', status: null, edges: null, nfl: null, gas: null, settings: null, experts: null,
  showPass: false, open: new Set(), charts: {}, mkt: { cat: '', q: '', sort: 'volume_24h', sel: null },
  audit: { source: '', failed: false } };

/* ---------- helpers ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function api(path, opts = {}) {
  const r = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return (r.headers.get('content-type') || '').includes('json') ? r.json() : r.text();
}
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body || {}) });
const put = (p, body) => api(p, { method: 'PUT', body: JSON.stringify(body || {}) });
const pct = (x, d = 1) => (x == null ? '—' : (100 * x).toFixed(d) + '%');
const cents = (x) => {
  if (x == null) return '—';
  const c = Math.round(100 * x * 100) / 100;
  return (Number.isInteger(c) ? c.toFixed(0) : String(c)) + '¢';
};
const money = (x) => (x == null ? '—' : (x < 0 ? '−$' : '$') + Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const signed = (x, d = 1, suf = '') => (x == null ? '—' : (x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(x).toFixed(d) + suf);
const TZ = 'Pacific/Honolulu';
const fmtFull = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const fmtTime = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit' });
const fmtDay = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
const hst = (ts) => (ts ? fmtFull.format(new Date(ts * 1000)) + ' HST' : '—');
const hstTime = (ts) => (ts ? fmtTime.format(new Date(ts * 1000)) : '—');
const pnlClass = (x) => (x > 0 ? 'text-emerald-400' : x < 0 ? 'text-red-400' : 'text-ink-200');

function toast(msg, bad = false) {
  const t = $('#toast');
  t.className = `fixed bottom-4 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-lg text-sm shadow-lg border ${bad ? 'bg-red-950 border-red-700 text-red-100' : 'bg-ink-800 border-ink-600 text-ink-100'}`;
  t.textContent = msg;
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), 4000);
}

function tierBadge(t) {
  const cls = {
    S: 'bg-red-600 text-white', G: 'bg-amber-400 text-ink-950', W: 'border border-sky-500 text-sky-300',
    P: 'border border-ink-600 text-ink-400',
  }[t] || 'border border-ink-600';
  const label = { S: 'S', G: 'G', W: 'Watch', P: 'Pass' }[t] || t;
  return `<span class="inline-flex items-center justify-center min-w-[3.25rem] px-1.5 h-6 rounded text-xs font-bold ${cls}">${label}</span>`;
}
const card = (inner, extra = '') => `<section class="min-w-0 bg-ink-900 border border-ink-800 rounded-xl p-4 ${extra}">${inner}</section>`;
const h2 = (t, sub = '') => `<div class="flex flex-wrap items-baseline gap-x-3 mb-3"><h2 class="text-base font-semibold">${t}</h2>${sub ? `<span class="text-xs text-ink-400">${sub}</span>` : ''}</div>`;
const stat = (label, value, cls = '') => `<div><div class="text-[11px] uppercase tracking-wider text-ink-400">${label}</div><div class="font-semibold tabular-nums ${cls}">${value}</div></div>`;
const phaseNote = (t) => card(`<div class="text-sm text-ink-300">${t}</div>`, 'border-dashed');
const inputCls = 'bg-ink-950 border border-ink-700 rounded-md px-2 py-1.5 text-sm text-ink-100 focus:outline-none focus:border-red-500 w-full';
const btn = 'px-3 py-1.5 rounded-md text-sm font-medium border border-ink-600 bg-ink-800 hover:border-red-500 disabled:opacity-50';
const btnPrimary = 'px-3 py-1.5 rounded-md text-sm font-semibold bg-red-600 hover:bg-red-500 text-white';

/* ---------- charts ---------- */
function destroyCharts(prefix = '') {
  for (const k of Object.keys(S.charts)) {
    if (k.startsWith(prefix)) { S.charts[k].destroy(); delete S.charts[k]; }
  }
}
function baseOpts(extra = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false, labels: { color: COLORS.text, boxWidth: 10, boxHeight: 10, usePointStyle: true } },
      tooltip: { backgroundColor: '#0d0e10', borderColor: '#3d4148', borderWidth: 1, titleColor: '#ecedef', bodyColor: '#d5d8dc', padding: 8 },
    },
    scales: {
      x: { ticks: { color: COLORS.text, maxTicksLimit: 6, maxRotation: 0 }, grid: { display: false }, border: { color: COLORS.grid } },
      y: { ticks: { color: COLORS.text, maxTicksLimit: 5 }, grid: { color: COLORS.grid }, border: { display: false } },
    },
    ...extra,
  };
}
function lineChart(id, labels, datasets, opts = {}) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  if (S.charts[id]) S.charts[id].destroy();
  const o = baseOpts();
  if (datasets.length > 1) o.plugins.legend.display = true;
  if (opts.yFmt) o.scales.y.ticks.callback = opts.yFmt;
  if (opts.tip) o.plugins.tooltip.callbacks = { label: opts.tip };
  if (opts.xTitle) o.scales.x.title = { display: true, text: opts.xTitle, color: COLORS.text };
  S.charts[id] = new Chart(el, {
    type: opts.type || 'line',
    data: { labels, datasets: datasets.map((d) => ({ borderWidth: 2, pointRadius: d.points ? 4 : 0, pointHoverRadius: 5, tension: 0, spanGaps: true, ...d })) },
    options: o,
  });
}
function sparkline(id, values) {
  const el = document.getElementById(id);
  if (!el || !window.Chart || !values || values.length < 2) return;
  if (S.charts[id]) S.charts[id].destroy();
  S.charts[id] = new Chart(el, {
    type: 'line',
    data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: COLORS.kalshi, borderWidth: 1.5, pointRadius: 0, tension: 0 }] },
    options: { responsive: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } },
  });
}

/* ---------- top bar ---------- */
const STATE_STYLE = {
  ok: 'border-ink-700 text-ink-300', stale: 'border-red-600 bg-red-950 text-red-200 font-semibold',
  error: 'border-red-600 bg-red-950 text-red-200 font-semibold', never: 'border-ink-700 text-ink-500', off: 'border-ink-800 text-ink-500',
};
const DOT = { ok: 'bg-emerald-500', stale: 'bg-red-500', error: 'bg-red-500', never: 'bg-ink-600', off: 'bg-ink-700' };
const SHORT = { kalshi_scan: 'Kalshi scan', kalshi_active: 'Kalshi 1-min', espn_nfl: 'ESPN', odds_api: 'Odds API', aaa: 'AAA', follow_picks: 'Followed picks' };
function freshText(f) {
  if (f.state === 'ok') return `${f.age_min < 1 ? 'now' : Math.round(f.age_min) + 'm ago'}`;
  if (f.state === 'stale') return `STALE · last good ${f.last_ok_ts ? hstTime(f.last_ok_ts) : '—'}`;
  if (f.state === 'error') return `ERROR · last good ${f.last_ok_ts ? hstTime(f.last_ok_ts) : 'never'}`;
  if (f.state === 'off') return 'off (no key)';
  return 'never pulled';
}
function renderTop() {
  const s = S.status;
  if (!s) return;
  $('#topstats').innerHTML = [
    stat('Bankroll', money(s.bankroll)), stat('Exposure', money(s.open_exposure)),
    stat('Today P&L', money(s.today_realized), pnlClass(s.today_realized)), stat('Unrealized', money(s.unrealized), pnlClass(s.unrealized)),
  ].join('');
  $('#fresh').innerHTML = s.freshness.filter((f) => f.headline).map((f) => `
    <a href="#audit" title="${esc(f.last_error ? 'Last error: ' + f.last_error + ' at ' + f.last_error_at : 'Last good: ' + (f.last_ok || 'never'))}"
       class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border ${STATE_STYLE[f.state]}">
      <span class="w-1.5 h-1.5 rounded-full ${DOT[f.state]}"></span>${esc(SHORT[f.name] || f.label)}: ${freshText(f)}</a>`).join('');
  $('#clock').textContent = s.now_hst;
}
function renderNav() {
  $('#nav').innerHTML = TABS.map(([k, label]) => `<a href="#${k}" class="px-3 py-2 whitespace-nowrap border-b-2 ${S.tab === k ? 'border-red-500 text-white font-semibold' : 'border-transparent text-ink-400 hover:text-ink-100'}">${label}${['news', 'flow'].includes(k) ? '<span class="ml-1 text-[10px] text-ink-500">P2</span>' : ''}</a>`).join('');
}

/* ---------- edge rows (shared by every board) ---------- */
function edgeKey(o) { return `${o.ticker}|${o.side}`; }
function edgeRow(o) {
  const k = edgeKey(o);
  const open = S.open.has(k);
  const sz = o.size || {};
  const metric = (label, val, cls = '') => `<div class="text-right min-w-[4.2rem]"><div class="text-[10px] uppercase tracking-wider text-ink-500">${label}</div><div class="tabular-nums text-sm ${cls}">${val}</div></div>`;
  return `<div class="border border-ink-800 rounded-lg bg-ink-900 mb-2 ${o.tier === 'S' ? 'border-l-4 border-l-red-600' : ''}">
    <button class="w-full text-left px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2" data-toggle="${esc(k)}">
      ${tierBadge(o.tier)}
      <div class="min-w-0 grow basis-56">
        <div class="font-medium truncate">${esc(o.subject)} <span class="text-ink-400 font-normal">· buy ${o.side.toUpperCase()}</span></div>
        <div class="text-xs text-ink-400 truncate">${esc(o.game_title || o.event_title || o.title || '')} · <span class="font-mono">${esc(o.ticker)}</span></div>
      </div>
      <div class="flex flex-wrap gap-x-3 gap-y-1">
        ${metric('Fair', pct(o.fair))}
        ${metric('Maker', cents(o.maker_price))}
        ${metric('Ask', cents(o.taker_price), 'text-ink-400')}
        ${metric('Edge', o.raw_edge_pts == null ? '—' : signed(o.raw_edge_pts, 1, ' pts'), o.raw_edge_pts > 0 ? 'text-emerald-400' : 'text-ink-300')}
        ${metric('Net EV', o.net_ev_pct == null ? '—' : signed(o.net_ev_pct, 1, '%'), o.net_ev_pct > 0 ? 'text-emerald-400 font-semibold' : 'text-ink-300')}
        ${metric('Size', sz.contracts ? `${sz.contracts} · ${money(sz.dollars)}` : '0', sz.contracts ? 'text-ink-100' : 'text-ink-500')}
        ${metric('Max loss', sz.contracts ? money(sz.max_loss) : '—')}
      </div>
    </button>
    ${open ? edgeDetail(o) : ''}
  </div>`;
}
function edgeDetail(o) {
  const k = edgeKey(o);
  const fi = o.fair_info || {};
  const warn = (o.warnings || []).map((w) => `<div class="rounded-md border ${/TRAP/.test(w) ? 'border-red-600 bg-red-950/60 text-red-100 font-bold' : 'border-amber-700 bg-amber-950/40 text-amber-100'} px-3 py-2 text-sm">${esc(w)}</div>`).join('');
  const pulls = (fi.pull_ids || []).map((p) => `<a class="underline decoration-dotted" href="#audit" data-pull="${p}">#${p}</a>`).join(', ');
  return `<div class="border-t border-ink-800 px-3 py-3 grid gap-4 lg:grid-cols-2">
    <div class="space-y-3">
      <p class="text-sm leading-relaxed text-ink-100">${esc(o.reason)}</p>
      ${warn}
      <div>
        <div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">The full math</div>
        <ol class="list-decimal ml-5 space-y-0.5 font-mono text-[12px] text-ink-200">${(o.math || []).map((l) => `<li>${esc(l)}</li>`).join('')}</ol>
      </div>
      <div class="text-xs text-ink-400 space-y-0.5">
        <div><span class="text-ink-300">Fair source:</span> ${esc(fi.plain || fi.source || '—')} · as of ${esc(fi.as_of || '—')} ${pulls ? '· pulls ' + pulls : ''}</div>
        <div><span class="text-ink-300">Kalshi price:</span> bid ${cents(o.best_bid)} / ask ${cents(o.best_ask)} · as of ${hst(o.price_ts)} · pull <a class="underline decoration-dotted" href="#audit" data-pull="${o.price_pull_id}">#${o.price_pull_id ?? '—'}</a></div>
        <div><span class="text-ink-300">Fees:</span> maker ${o.fee_rates?.maker} / taker ${o.fee_rates?.taker} (${esc(o.fee_rates?.basis)})</div>
        <div><span class="text-ink-300">Taker check:</span> buying at the ${cents(o.taker_price)} ask instead would be ${o.taker_net_ev_pct_100 == null ? '—' : signed(o.taker_net_ev_pct_100, 1, '%')} net EV (100 contracts).</div>
        <div><span class="text-ink-300">Expert:</span> ${esc(o.expert?.note || 'none')}</div>
      </div>
    </div>
    <div class="space-y-3">
      <div class="text-[11px] uppercase tracking-wider text-ink-400">YES price, last 24h (Kalshi mid)</div>
      <div class="h-40"><canvas id="ec-${esc(k)}"></canvas></div>
      ${tradeForm(o)}
    </div>
  </div>`;
}
function tradeForm(o) {
  const sz = o.size || {};
  const id = `tf-${o.ticker}-${o.side}`;
  return `<form class="trade-form grid grid-cols-2 sm:grid-cols-4 gap-2 bg-ink-950/60 border border-ink-800 rounded-lg p-3" data-ticker="${esc(o.ticker)}" data-maker-rate="${o.fee_rates?.maker ?? 0.0175}" data-taker-rate="${o.fee_rates?.taker ?? 0.07}" data-precision="${o.fee_rates?.precision ?? 0.0001}" id="${esc(id)}">
    <div class="col-span-2 sm:col-span-4 text-[11px] uppercase tracking-wider text-ink-400">Log a trade (paper by default; this app never places orders)</div>
    <label class="text-xs text-ink-400">Mode<select name="mode" class="${inputCls}"><option value="paper">Paper</option><option value="real">Real (already placed by me)</option></select></label>
    <label class="text-xs text-ink-400">Order<select name="order_type" class="${inputCls}"><option value="maker">Maker (rest bid)</option><option value="taker">Taker (hit ask)</option></select></label>
    <label class="text-xs text-ink-400">Side<select name="side" class="${inputCls}"><option value="yes" ${o.side === 'yes' ? 'selected' : ''}>YES</option><option value="no" ${o.side === 'no' ? 'selected' : ''}>NO</option></select></label>
    <label class="text-xs text-ink-400">Price ($)<input name="limit_price" type="number" step="0.0001" min="0.0001" max="0.9999" value="${o.maker_price ?? ''}" class="${inputCls}"></label>
    <label class="text-xs text-ink-400">Contracts<input name="contracts" type="number" min="1" step="1" value="${sz.contracts || 1}" class="${inputCls}"></label>
    <label class="text-xs text-ink-400">Tier<select name="tier" class="${inputCls}">${['S', 'G', 'W', 'P'].map((t) => `<option ${o.tier === t ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label class="text-xs text-ink-400 col-span-2">Expert source<input name="expert_source" class="${inputCls}" placeholder="optional"></label>
    <label class="text-xs text-ink-400 col-span-2 sm:col-span-4">Reason<input name="reason" class="${inputCls}" value="${esc((o.tier_reason || '').slice(0, 180))}"></label>
    <label class="text-xs text-ink-400 col-span-2 sm:col-span-3">News link<input name="news_link" class="${inputCls}" placeholder="optional"></label>
    <label class="text-xs text-ink-400 flex items-end gap-2 pb-2"><input type="checkbox" name="assume_filled"> assume filled</label>
    <input type="hidden" name="fair_at_entry" value="${o.fair ?? ''}"><input type="hidden" name="edge_at_entry" value="${o.raw_edge_pts ?? ''}">
    <div class="col-span-2 sm:col-span-4 flex flex-wrap items-center gap-3">
      <button class="${btnPrimary}" type="submit">Log trade</button>
      <span class="text-xs text-ink-300 maxloss"></span>
    </div>
  </form>`;
}
function feeCalc(rate, c, p, precision = 0.0001) {
  const units = Math.round(rate * c * p * (1 - p) * 1e8) / 1e8 / precision;
  return Math.ceil(units - 1e-9) * precision;
}
function updateMaxLoss(form) {
  const f = new FormData(form);
  const p = parseFloat(f.get('limit_price')); const c = parseInt(f.get('contracts'), 10);
  const rate = parseFloat(f.get('order_type') === 'maker' ? form.dataset.makerRate : form.dataset.takerRate);
  const out = form.querySelector('.maxloss');
  if (!(p > 0 && p < 1 && c > 0)) { out.textContent = ''; return; }
  const fee = feeCalc(rate, c, p, parseFloat(form.dataset.precision || '0.0001'));
  out.textContent = `Cost ${money(c * p)} + fee $${fee.toFixed(4)} = max loss ${money(c * p + fee)} · pays ${money(c)} if right`;
}
async function submitTrade(form) {
  const f = new FormData(form);
  const body = Object.fromEntries(f.entries());
  body.ticker = form.dataset.ticker;
  body.assume_filled = f.get('assume_filled') === 'on';
  body.limit_price = parseFloat(body.limit_price);
  body.contracts = parseInt(body.contracts, 10);
  for (const k of ['fair_at_entry', 'edge_at_entry']) body[k] = body[k] === '' ? null : parseFloat(body[k]);
  if (body.mode === 'real' && !confirm(`Log a REAL trade you already placed on Kalshi?\n${body.side.toUpperCase()} ${body.contracts} @ ${body.limit_price}\nThis only records it; nothing is sent to Kalshi.`)) return;
  try {
    const t = await post('/trades', body);
    toast(`Logged ${t.mode} #${t.id}: ${t.side.toUpperCase()} ${t.contracts} @ ${cents(t.limit_price)} (${t.status})`);
    refreshStatus();
  } catch (e) { toast(e.message, true); }
}
async function loadEdgeChart(o) {
  const id = `ec-${edgeKey(o)}`;
  try {
    const d = await api(`/markets/${encodeURIComponent(o.ticker)}?hours=24`);
    const pts = d.history.filter((h) => h.yes_bid != null && h.yes_ask != null);
    if (!pts.length) { const el = document.getElementById(id); if (el) el.parentElement.innerHTML = '<div class="text-xs text-ink-500 py-6">No snapshots yet for this market.</div>'; return; }
    lineChart(id, pts.map((h) => hstTime(h.ts)), [{ label: 'YES mid', data: pts.map((h) => +(((h.yes_bid + h.yes_ask) / 2) * 100).toFixed(1)), borderColor: COLORS.kalshi }],
      { yFmt: (v) => v + '¢', tip: (c) => ` ${c.dataset.label}: ${c.parsed.y}¢` });
  } catch (e) { /* chart is optional */ }
}
function allOpps() {
  const out = [];
  if (S.edges) S.edges.groups.forEach((g) => out.push(...g.items));
  if (S.nfl) out.push(...S.nfl.opportunities);
  if (S.gas) out.push(...S.gas.opportunities);
  return out;
}
function bindEdgeRows(root) {
  $$('[data-toggle]', root).forEach((b) => b.addEventListener('click', () => {
    const k = b.dataset.toggle;
    if (S.open.has(k)) S.open.delete(k); else S.open.add(k);
    render();
  }));
  $$('.trade-form', root).forEach((form) => {
    updateMaxLoss(form);
    form.addEventListener('input', () => updateMaxLoss(form));
    form.addEventListener('submit', (e) => { e.preventDefault(); submitTrade(form); });
  });
  const seen = new Set();
  for (const o of allOpps()) {
    const k = edgeKey(o);
    if (S.open.has(k) && !seen.has(k) && document.getElementById(`ec-${k}`)) { seen.add(k); loadEdgeChart(o); }
  }
  $$('[data-pull]', root).forEach((a) => a.addEventListener('click', () => { S.audit.pull = a.dataset.pull; }));
}

/* ---------- Board 1: Top Edges ---------- */
function viewEdges() {
  const d = S.edges;
  if (!d) return loading();
  const counts = d.counts || {};
  const filt = (items) => items.filter((o) => S.showPass || o.tier !== 'P');
  const groups = d.groups.map((g) => {
    const items = filt(g.items);
    const hidden = g.items.length - items.length;
    return `<div class="mb-6">${h2(g.title, `${g.items.length} rated${hidden ? ` · ${hidden} Pass hidden` : ''}`)}
      ${g.note ? `<div class="text-sm text-ink-400 mb-2">${esc(g.note)}</div>` : ''}
      ${items.length ? items.map(edgeRow).join('') : `<div class="text-sm text-ink-500 border border-dashed border-ink-800 rounded-lg px-3 py-4">${g.items.length ? 'Everything here is Pass right now.' : emptyReason(g.key)}</div>`}
    </div>`;
  }).join('');
  return `<div class="flex flex-wrap items-center gap-3 mb-4">
      <div class="flex gap-2 text-sm">${['S', 'G', 'W', 'P'].map((t) => `<span class="inline-flex items-center gap-1.5">${tierBadge(t)}<span class="tabular-nums">${counts[t] || 0}</span></span>`).join('')}</div>
      <div class="grow"></div>
      <label class="text-sm text-ink-300 flex items-center gap-2"><input type="checkbox" id="showPass" ${S.showPass ? 'checked' : ''}> show Pass</label>
      <span class="text-xs text-ink-500">computed ${esc(d.computed_hst || '')}</span>
    </div>
    ${(d.errors || []).map((e) => `<div class="mb-3 rounded-md border border-red-700 bg-red-950/50 px-3 py-2 text-sm text-red-100">${esc(e)}</div>`).join('')}
    <div class="text-xs text-ink-400 mb-4">Headline numbers are maker prices and net EV after Kalshi fees. Tier S needs edge &gt; ${S.settings?.tiers?.s_min_edge_pts ?? 10} pts and net EV &gt; ${S.settings?.tiers?.s_min_net_ev_pct ?? 20}%. Tap any row for the full math, sources, chart, and a trade form.</div>
    ${groups}`;
}
function emptyReason(key) {
  const fr = Object.fromEntries((S.status?.freshness || []).map((f) => [f.name, f]));
  if (fr.kalshi_scan && fr.kalshi_scan.state !== 'ok') return `No Kalshi data yet (scanner: ${freshText(fr.kalshi_scan)}).`;
  if (key === 'games') return 'No NFL game market has a fair price yet. Add a manual line on the Sports tab, add ODDS_API_KEY, or wait for the ESPN pull.';
  if (key === 'commodities') return 'No gas ladder is rated yet: needs open gas markets plus enough AAA history (see Commodities & Econ).';
  return 'Nothing to show yet.';
}

/* ---------- Board 2: Sports ---------- */
function viewSports() {
  const d = S.nfl;
  if (!d) return loading();
  const q = d.odds_api_quota;
  const head = d.odds_api_enabled
    ? `The Odds API is on${q && q['x-requests-remaining'] ? ` · ${esc(q['x-requests-remaining'])} credits left` : ''}. Fair = median of devigged books.`
    : 'No ODDS_API_KEY yet: fair prices come from your manual lines first, then ESPN\'s single book (labeled low confidence).';
  const games = d.games.map((g) => gameCard(g, d.opportunities.filter((o) => o.event_ticker === g.event_ticker))).join('');
  return `<div class="text-sm text-ink-300 mb-4">${head}</div>
    <div class="grid grid-cols-1 gap-4">${games || card('<div class="text-sm text-ink-400">No open NFL game markets in the database yet. The scanner fills these in on its next run.</div>')}</div>
    <div class="mt-6 grid gap-3 sm:grid-cols-2">${phaseNote('College football and NBA boards, injuries, rest, travel, and closing line value arrive in Phase 2.')}${phaseNote('Props board with best odds arrives in Phase 2.')}</div>`;
}
function gameCard(g, opps) {
  const c = g.context || {};
  const f = g.fair;
  const teams = g.teams.map((t) => `<tr class="border-t border-ink-800">
      <td class="py-1.5 pr-3 font-medium">${esc(t.name)}</td>
      <td class="pr-3 tabular-nums">${cents(t.yes_bid)} / ${cents(t.yes_ask)}</td>
      <td class="pr-3 tabular-nums">${pct(t.mid, 0)}</td>
      <td class="pr-3 tabular-nums ${t.fair != null ? 'text-orange-300' : 'text-ink-500'}">${t.fair != null ? pct(t.fair) : 'need line'}</td>
      <td class="pr-3 tabular-nums text-xs">${signed(t.movement?.chg_1h, 1, '¢')}</td>
      <td class="pr-3 tabular-nums text-xs">${signed(t.movement?.chg_24h, 1, '¢')}</td>
      <td><canvas id="sp-${esc(t.ticker)}" width="110" height="26"></canvas></td></tr>`).join('');
  const [a, b] = g.teams;
  const homeGuess = c.home_team || (b && b.team);
  const ctx = [c.commence_ts ? `Kickoff ${hst(c.commence_ts)}` : (g.date ? `Game date ${g.date}` : ''), c.venue, c.indoor ? 'indoor' : c.weather, c.details ? `ESPN line ${c.details}` : '', c.status]
    .filter(Boolean).map(esc).join(' · ');
  return card(`
    <div class="flex flex-wrap items-baseline gap-x-3 mb-1"><h3 class="font-semibold">${esc(g.title)}</h3>${g.started ? '<span class="text-xs text-amber-300">in progress / started</span>' : ''}</div>
    <div class="text-xs text-ink-400 mb-2">${ctx || 'No ESPN context pulled yet.'}</div>
    <div class="text-xs mb-2 ${f ? 'text-ink-300' : 'text-ink-500'}">${f ? `Fair: ${esc(f.plain)} · as of ${esc(f.as_of)}` : 'No fair price yet for this game.'}</div>
    <div class="overflow-x-auto"><table class="text-sm w-full min-w-[560px]">
      <thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Team</th><th class="pr-3">Kalshi bid/ask</th><th class="pr-3">Mid</th><th class="pr-3">Fair</th><th class="pr-3">1h</th><th class="pr-3">24h</th><th>24h</th></tr></thead>
      <tbody>${teams}</tbody></table></div>
    ${opps.length ? `<div class="mt-3">${opps.map(edgeRow).join('')}</div>` : ''}
    <details class="mt-3"><summary class="text-sm text-ink-300 cursor-pointer">Enter a line by hand</summary>
      <form class="line-form mt-2 grid grid-cols-2 sm:grid-cols-5 gap-2" data-a="${a?.team}" data-b="${b?.team}">
        <label class="text-xs text-ink-400">Home team<select name="home" class="${inputCls}">${g.teams.map((t) => `<option value="${t.team}" ${t.team === homeGuess ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></label>
        <label class="text-xs text-ink-400">Home ML<input name="home_price" type="number" placeholder="-150" class="${inputCls}"></label>
        <label class="text-xs text-ink-400">Away ML<input name="away_price" type="number" placeholder="+130" class="${inputCls}"></label>
        <label class="text-xs text-ink-400">Book / source<input name="book" class="${inputCls}" placeholder="e.g. Circa screen"></label>
        <div class="flex items-end"><button class="${btn} w-full" type="submit">Save line</button></div>
      </form></details>`);
}
function bindSports(root) {
  (S.nfl?.games || []).forEach((g) => g.teams.forEach((t) => sparkline(`sp-${t.ticker}`, t.movement?.spark)));
  $$('.line-form', root).forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const home = f.get('home');
    const away = home === form.dataset.a ? form.dataset.b : form.dataset.a;
    try {
      await post('/nfl/lines', { home_team: home, away_team: away, home_price: parseInt(f.get('home_price'), 10), away_price: parseInt(f.get('away_price'), 10), book: f.get('book') || 'manual' });
      toast('Line saved. Fair price and edges recomputed.');
      await loadTab(true);
    } catch (err) { toast(err.message, true); }
  }));
}

/* ---------- Board 3: Commodities & Economics ---------- */
function viewEcon() {
  const d = S.gas;
  if (!d) return loading();
  const a = d.aaa || {};
  const tr = a.trends || {};
  const ch = (k) => (tr[k] == null ? '—' : signed(tr[k] * 100, 1, '¢'));
  const aaaCard = card(`${h2('AAA National Average (resolution source)', a.latest_date ? `latest ${esc(a.latest_date)}` : 'no readings yet')}
    <div class="flex flex-wrap gap-x-6 gap-y-2 mb-3">
      ${stat('Latest', a.latest != null ? '$' + a.latest.toFixed(4) : '—', 'text-xl')}
      ${stat('Yesterday move', ch('chg_1d'))}${stat('3 days', ch('chg_3d'))}${stat('7 days', ch('chg_7d'))}${stat('14 days', ch('chg_14d'))}
      ${tr.decelerating != null ? stat('Trend', tr.decelerating ? 'rising, slowing' : (tr.chg_3d > 0 ? 'rising' : tr.chg_3d < 0 ? 'falling' : 'flat')) : ''}
    </div>
    <div class="h-40"><canvas id="aaa-chart"></canvas></div>
    <details class="mt-3"><summary class="text-sm text-ink-300 cursor-pointer">Enter an AAA reading by hand (if the AAA pull is blocked)</summary>
      <form id="aaa-form" class="mt-2 grid grid-cols-3 gap-2">
        <label class="text-xs text-ink-400">Date<input name="date" type="date" class="${inputCls}"></label>
        <label class="text-xs text-ink-400">Regular avg ($)<input name="value" type="number" step="0.0001" class="${inputCls}" placeholder="4.2926"></label>
        <div class="flex items-end"><button class="${btn} w-full">Save</button></div>
      </form></details>`);
  const ladders = d.ladders.map((l, i) => ladderCard(l, i, d.opportunities)).join('') ||
    card('<div class="text-sm text-ink-400">No open AAA gas markets found yet. Series watched: ' + esc((d.series || []).join(', ')) + '. The scanner also auto-detects gas events by title.</div>');
  const c = d.calibration || {};
  const calRow = (label, s) => `<tr class="border-t border-ink-800"><td class="py-1.5 pr-3">${label}</td><td class="pr-3 tabular-nums">${s.n || 0}</td>
    <td class="pr-3 tabular-nums">${s.n ? signed(s.bias * 100, 2, '¢') : '—'}</td><td class="pr-3 tabular-nums">${s.n ? (s.mae * 100).toFixed(2) + '¢' : '—'}</td>
    <td class="pr-3 tabular-nums">${s.n ? s.pct_above + '%' : '—'}</td><td class="tabular-nums">${s.n ? s.within_1c + '%' : '—'}</td></tr>`;
  const calCard = card(`${h2('Median calibration vs actual AAA', 'ladder as it stood before close vs the AAA value Kalshi settled on')}
    <div class="overflow-x-auto"><table class="text-sm w-full min-w-[520px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left">
      <tr><th class="py-1 pr-3">Forecast</th><th class="pr-3">Days</th><th class="pr-3">Bias</th><th class="pr-3">Avg miss</th><th class="pr-3">Actual above median</th><th>Within 1¢</th></tr></thead>
      <tbody>${calRow('Kalshi implied median', c.market || {})}${calRow('Our model median', c.model || {})}</tbody></table></div>
    <div class="text-xs text-ink-400 mt-2">A calibrated median has about 50% of outcomes above it and bias near 0.</div>
    <div class="h-44 mt-3"><canvas id="cal-chart"></canvas></div>`);
  const sc = d.model_score || {};
  const scRows = sc.brier ? Object.entries(sc.brier).sort((x, y) => +x[0] - +y[0]).map(([w, b]) => `<tr class="border-t border-ink-800">
      <td class="py-1 pr-3">${+w === 0 ? 'Kalshi alone' : +w === 1 ? 'Model alone' : `${Math.round(w * 100)}% model`}${+w === sc.current_weight ? ' <span class="text-xs text-red-300">(current)</span>' : ''}</td>
      <td class="tabular-nums ${+w === sc.best_weight ? 'text-emerald-300 font-semibold' : ''}">${b.toFixed(4)}</td></tr>`).join('') : '';
  const scoreCard = card(`${h2('Model check: does the model beat Kalshi?', 'Brier score on settled gas strikes; lower is better')}
    <div class="rounded-md border px-3 py-2 text-sm mb-3 ${sc.adds_value ? 'border-emerald-700 bg-emerald-950/40 text-emerald-100' : 'border-amber-700 bg-amber-950/40 text-amber-100'}">${esc(sc.verdict || 'Not scored yet.')}</div>
    <div class="text-xs text-ink-400 mb-2">${sc.strikes || 0} strikes over ${sc.days || 0} settled days (needs ${sc.min_days ?? 10}). Each day uses only AAA values known when the ladder was priced.</div>
    ${scRows ? `<table class="text-sm"><tbody>${scRows}</tbody></table>` : ''}`);
  return `<div class="mb-4 rounded-lg border-2 border-red-600 bg-red-950/50 px-4 py-3 text-red-50 font-bold">${esc(d.warning)}</div>
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2 mb-4">${aaaCard}${calCard}</div>
    <div class="mb-4">${scoreCard}</div>
    <div class="grid grid-cols-1 gap-4">${ladders}</div>
    <div class="mt-6 grid gap-3 sm:grid-cols-2">
      ${phaseNote('Release calendar (CPI, PCE, payrolls, claims, GDP, FOMC) with consensus and strike-by-strike surprise maps: Phase 2.')}
      ${phaseNote('Oil and natural gas (EIA inventories, curve shape, rigs, NOAA demand), gold and silver: Phase 3.')}
    </div>`;
}
function ladderCard(l, i, opps) {
  const rows = l.rows.map((r) => {
    const best = opps.find((o) => o.ticker === r.ticker);
    return `<tr class="border-t border-ink-800">
      <td class="py-1.5 pr-3">${esc(r.label || r.ticker)}</td>
      <td class="pr-3 tabular-nums">${cents(r.yes_bid)} / ${cents(r.yes_ask)}</td>
      <td class="pr-3 tabular-nums">${r.mid != null ? pct(r.mid, 0) : '—'}<span class="text-[10px] text-ink-500"> ${r.mid_basis !== 'mid' ? esc(r.mid_basis) : ''}</span></td>
      <td class="pr-3 tabular-nums text-ink-300">${r.model_yes == null ? '—' : r.model_yes >= 0.995 ? '&gt;99%' : r.model_yes <= 0.005 ? '&lt;1%' : pct(r.model_yes)}</td>
      <td class="pr-3 tabular-nums text-orange-300">${r.fair_yes != null ? pct(r.fair_yes) : '—'}</td>
      <td class="pr-3">${best ? tierBadge(best.tier) + ` <span class="text-xs text-ink-400">${best.side.toUpperCase()} ${signed(best.raw_edge_pts, 1, ' pts')} · ${signed(best.net_ev_pct, 1, '%')}</span>` : ''}</td></tr>`;
  }).join('');
  const m = l.model || {};
  const ladderOpps = opps.filter((o) => o.event_ticker === l.event_ticker && (S.showPass || o.tier !== 'P'));
  return `<details ${i === 0 ? 'open' : ''} class="min-w-0 bg-ink-900 border border-ink-800 rounded-xl p-4" data-ladder="${esc(l.event_ticker)}">
    <summary class="cursor-pointer"><span class="font-semibold">${esc(l.title || l.event_ticker)}</span>
      <span class="text-xs text-ink-400 ml-2">resolves ${esc(l.resolution_date || '?')} · closes ${esc(l.close_hst || '?')} · <span class="font-mono">${esc(l.series_ticker)}</span></span></summary>
    <div class="flex flex-wrap gap-x-6 gap-y-2 my-3">
      ${stat('Kalshi implied median', l.implied_median != null ? '$' + l.implied_median.toFixed(4) : esc(l.median_note))}
      ${stat('Model mean', m.ok && m.mu != null ? '$' + m.mu.toFixed(4) : '—')}
      ${stat('Fair blend', `${Math.round(l.model_weight * 100)}% model / ${Math.round((1 - l.model_weight) * 100)}% Kalshi`)}
      ${stat('Model sigma', m.ok && m.sigma != null ? (m.sigma * 100).toFixed(2) + '¢' : '—')}
      ${stat('Horizon', m.horizon != null ? m.horizon + ' day(s)' : '—')}
    </div>
    ${!m.ok ? `<div class="text-sm text-amber-200 mb-2">No fair price yet: ${esc(m.why || 'model unavailable')}.</div>` : ''}
    <div class="grid gap-4 lg:grid-cols-5">
      <div class="min-w-0 lg:col-span-3 overflow-x-auto"><table class="text-sm w-full min-w-[480px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left">
        <tr><th class="py-1 pr-3">Strike</th><th class="pr-3">Bid / ask</th><th class="pr-3">Kalshi</th><th class="pr-3">Model</th><th class="pr-3">Fair</th><th>Best side</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="min-w-0 lg:col-span-2"><div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">P(AAA above strike)</div><div class="h-48"><canvas id="lad-${i}"></canvas></div></div>
    </div>
    <div class="text-xs text-ink-400 mt-2">Price as of ${esc(l.price_as_of)}</div>
    ${ladderOpps.length ? `<div class="mt-3">${ladderOpps.map(edgeRow).join('')}</div>` : ''}
  </details>`;
}
function bindEcon(root) {
  const d = S.gas;
  if (!d) return;
  const hist = d.aaa?.history || [];
  if (hist.length) {
    lineChart('aaa-chart', hist.map((h) => h.date.slice(5)), [{ label: 'AAA regular', data: hist.map((h) => h.value), borderColor: COLORS.fair, points: hist.length < 20 }],
      { yFmt: (v) => '$' + Number(v).toFixed(2), tip: (c) => ` AAA: $${c.parsed.y.toFixed(4)}` });
  } else {
    const el = $('#aaa-chart'); if (el) el.parentElement.innerHTML = '<div class="text-xs text-ink-500 py-6">No AAA history yet. It fills from the AAA pull and from settled Kalshi gas markets.</div>';
  }
  const recs = d.calibration?.records || [];
  if (recs.length) {
    lineChart('cal-chart', recs.map((r) => r.resolution_date.slice(5)), [
      { label: 'Kalshi median miss (actual − median)', data: recs.map((r) => (r.implied_median != null ? +((r.actual - r.implied_median) * 100).toFixed(2) : null)), backgroundColor: COLORS.kalshi, borderRadius: 4, borderSkipped: 'start' },
      { label: 'Model median miss', data: recs.map((r) => (r.model_median != null ? +((r.actual - r.model_median) * 100).toFixed(2) : null)), backgroundColor: COLORS.fair, borderRadius: 4, borderSkipped: 'start' },
    ], { type: 'bar', yFmt: (v) => v + '¢', tip: (c) => ` ${c.dataset.label}: ${c.parsed.y}¢` });
  } else {
    const el = $('#cal-chart'); if (el) el.parentElement.innerHTML = '<div class="text-xs text-ink-500 py-6">No settled ladders graded yet. The backfill job grades past days from Kalshi candles; our own snapshots add a record each day going forward.</div>';
  }
  d.ladders.forEach((l, i) => {
    if (!l.strikes || !l.strikes.length) return;
    const fairByStrike = {};
    l.rows.forEach((r) => { if (r.fair_yes != null && r.strike_type?.startsWith('greater')) fairByStrike[r.floor] = r.fair_yes; });
    const ds = [{ label: 'Kalshi (mid, monotone)', data: l.survival.map((s) => +(s * 100).toFixed(1)), borderColor: COLORS.kalshi, points: true }];
    if (Object.keys(fairByStrike).length) ds.push({ label: 'Our fair', data: l.strikes.map((k) => (fairByStrike[k] != null ? +(fairByStrike[k] * 100).toFixed(1) : null)), borderColor: COLORS.fair, points: true });
    lineChart(`lad-${i}`, l.strikes.map((k) => '$' + Number(k).toFixed(2)), ds, { yFmt: (v) => v + '%', tip: (c) => ` ${c.dataset.label}: ${c.parsed.y}%`, xTitle: 'strike' });
  });
  const form = $('#aaa-form', root);
  if (form) form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    try { await post('/gas/aaa', { date: f.get('date'), value: parseFloat(f.get('value')) }); toast('AAA reading saved.'); await loadTab(true); } catch (err) { toast(err.message, true); }
  });
}


/* ---------- Parlays (Kalshi Combos) ---------- */
const netEv = (fair, p, rate) => { const f = rate * p * (1 - p); return 100 * (fair - p - f) / (p + f); };
function viewParlays() {
  const d = S.parlays;
  if (!d) return loading();
  const pool = (d.pool || []).map((g) => `<tr class="border-t border-ink-800"><td class="py-1.5 pr-3">${esc(g.label)}</td>
      <td class="pr-3 text-xs text-ink-400">${esc(g.game || '')}</td><td class="pr-3 tabular-nums text-orange-300">${pct(g.fair)}</td>
      <td class="pr-3 tabular-nums">${cents(g.ask)}</td><td class="pr-3 tabular-nums text-emerald-400">${signed(g.leg_edge_pts, 1, ' pts')}</td>
      <td class="text-xs ${g.expert === 'agree' ? 'text-emerald-300' : 'text-ink-500'}">${g.expert === 'agree' ? 'proven bettor agrees' : '—'}</td></tr>`).join('');
  const tickets = d.tickets.map((t) => parlayRow(t)).join('');
  const combos = (d.combos || []).map((c) => `<tr class="border-t border-ink-800"><td class="py-1.5 pr-3 text-xs">${esc(c.created_hst)}</td>
      <td class="pr-3 text-xs">${c.mode} · ${c.status}${c.settlement_result ? ' (' + esc(c.settlement_result) + ')' : ''}</td>
      <td class="pr-3">${esc(c.title)}</td><td class="pr-3 tabular-nums">${c.contracts} @ ${cents(c.limit_price)}</td>
      <td class="tabular-nums ${pnlClass(c.realized_pnl)}">${c.realized_pnl != null ? money(c.realized_pnl) : '—'}</td></tr>`).join('');
  return `<div class="grid grid-cols-1 gap-4">
    ${card(`${h2('NFL parlays (Kalshi Combos)', `${d.legs_considered} game-winner legs priced · ${(d.pool || []).length} are +EV on their own`)}
      <div class="text-sm text-ink-300 space-y-1">
        <p>Every ticket uses only legs where our fair price beats the Kalshi ask. A parlay of fair-priced legs isn't +EV, just higher variance. ${esc(d.note)}</p>
        <p>Kalshi prices each combo by market-maker quote after you build it in the app. Each ticket gives the legs, our fair price, our estimate of the quote, and the <b>highest quote worth accepting</b> (net EV ≥ ${d.min_net_ev_pct}% after fees). This app never places bets.</p>
      </div>`)}
    <div>${tickets || card(`<div class="text-sm text-ink-400">${(d.pool || []).length < 2 ? 'Fewer than two +EV legs right now, so no parlay is worth building. Legs need a fair price (Odds API key, a manual line, or ESPN) and a Kalshi ask below it.' : 'No ticket clears the rules right now.'}</div>`)}</div>
    ${card(`${h2('Leg pool', 'best +EV leg per game, ranked by fair ÷ ask')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[620px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Leg</th><th class="pr-3">Game</th><th class="pr-3">Fair</th><th class="pr-3">Kalshi ask</th><th class="pr-3">Leg edge</th><th>Followed bettors</th></tr></thead><tbody>${pool || '<tr><td colspan="6" class="py-3 text-ink-500">No +EV legs right now.</td></tr>'}</tbody></table></div>`)}
    ${combos ? card(`${h2('Your parlays')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[640px]"><tbody>${combos}</tbody></table></div>`) : ''}
  </div>`;
}
function parlayRow(t) {
  const k = 'parlay|' + t.id;
  const open = S.open.has(k);
  const sz = t.size || {};
  const metric = (label, val, cls = '') => `<div class="text-right min-w-[4.2rem]"><div class="text-[10px] uppercase tracking-wider text-ink-500">${label}</div><div class="tabular-nums text-sm ${cls}">${val}</div></div>`;
  const title = t.legs.map((g) => esc(g.label.replace(' to win', ''))).join(' + ');
  return `<div class="border border-ink-800 rounded-lg bg-ink-900 mb-2 ${t.tier === 'S' ? 'border-l-4 border-l-red-600' : ''}">
    <button class="w-full text-left px-3 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2" data-toggle="${esc(k)}">
      ${tierBadge(t.tier)}
      <div class="min-w-0 grow basis-56"><div class="font-medium">${t.n_legs}-leg: ${title}</div>
        <div class="text-xs text-ink-400">pays ${esc(t.pays || '—')} · ${t.expert === 'agree' ? '<span class="text-emerald-300">proven bettors on every leg</span>' : 'not every leg has proven agreement'}</div></div>
      <div class="flex flex-wrap gap-x-3 gap-y-1">
        ${metric('Fair', pct(t.fair))}${metric('Est. quote', cents(t.est_quote))}
        ${metric('Max price', t.max_price != null ? cents(t.max_price) : 'none', 'text-amber-300 font-semibold')}
        ${metric('Edge', signed(t.edge_pts, 1, ' pts'), t.edge_pts > 0 ? 'text-emerald-400' : '')}
        ${metric('Net EV', signed(t.net_ev_pct, 1, '%'), t.net_ev_pct > 0 ? 'text-emerald-400 font-semibold' : '')}
        ${metric('Size', sz.contracts ? `${sz.contracts} · ${money(sz.dollars)}` : '0', sz.contracts ? '' : 'text-ink-500')}
        ${metric('Max loss', sz.contracts ? money(sz.max_loss) : '—')}
      </div></button>
    ${open ? `<div class="border-t border-ink-800 px-3 py-3 grid gap-4 lg:grid-cols-2">
      <div class="space-y-3">
        <div><div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">Set it up on Kalshi</div>
          <ol class="list-decimal ml-5 space-y-1 text-sm">${t.setup.map((x) => `<li>${esc(x)}</li>`).join('')}</ol></div>
        <div><div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">The full math</div>
          <ol class="list-decimal ml-5 space-y-0.5 font-mono text-[12px] text-ink-200">${t.math.map((x) => `<li>${esc(x)}</li>`).join('')}</ol></div>
      </div>
      <div class="space-y-3">
        <div class="bg-ink-950/60 border border-ink-800 rounded-lg p-3">
          <div class="text-[11px] uppercase tracking-wider text-ink-400 mb-2">Check the quote Kalshi gives you</div>
          <div class="flex flex-wrap items-center gap-2"><input type="number" step="0.1" min="0.1" max="99" class="${inputCls} max-w-[8rem] quote-in" data-fair="${t.fair}" data-rate="${t.fee_rate}" data-max="${t.max_price ?? ''}" placeholder="quote ¢">
            <span class="text-sm quote-out text-ink-300">Enter the quote in cents.</span></div></div>
        <form class="combo-form grid grid-cols-2 sm:grid-cols-4 gap-2 bg-ink-950/60 border border-ink-800 rounded-lg p-3" data-legs='${esc(JSON.stringify(t.legs.map((g) => ({ ticker: g.ticker, side: 'yes', label: g.label }))))}'>
          <div class="col-span-2 sm:col-span-4 text-[11px] uppercase tracking-wider text-ink-400">Log this parlay (paper by default)</div>
          <label class="text-xs text-ink-400">Mode<select name="mode" class="${inputCls}"><option value="paper">Paper</option><option value="real">Real (I placed it)</option></select></label>
          <label class="text-xs text-ink-400">Quote you got ($)<input name="limit_price" type="number" step="0.0001" min="0.0001" max="0.9999" value="${t.est_quote}" class="${inputCls}"></label>
          <label class="text-xs text-ink-400">Contracts<input name="contracts" type="number" min="1" value="${sz.contracts || 1}" class="${inputCls}"></label>
          <label class="text-xs text-ink-400">Tier<select name="tier" class="${inputCls}">${['S', 'G', 'W', 'P'].map((x) => `<option ${x === t.tier ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
          <input type="hidden" name="fair_at_entry" value="${t.fair}"><input type="hidden" name="edge_at_entry" value="${t.edge_pts}">
          <div class="col-span-2 sm:col-span-4"><button class="${btnPrimary}" type="submit">Log parlay</button></div>
        </form>
      </div></div>` : ''}
  </div>`;
}
function bindParlays(root) {
  bindEdgeRows(root);
  $$('.quote-in', root).forEach((inp) => inp.addEventListener('input', () => {
    const q = parseFloat(inp.value) / 100; const out = inp.parentElement.querySelector('.quote-out');
    if (!(q > 0 && q < 1)) { out.textContent = 'Enter the quote in cents.'; return; }
    const ev = netEv(parseFloat(inp.dataset.fair), q, parseFloat(inp.dataset.rate));
    const max = parseFloat(inp.dataset.max);
    const ok = !isNaN(max) && q <= max + 1e-9;
    out.innerHTML = `Net EV at ${cents(q)}: <b class="${ev > 0 ? 'text-emerald-400' : 'text-red-400'}">${signed(ev, 1, '%')}</b> · ${ok ? '<span class="text-emerald-300 font-semibold">within your max: OK to accept</span>' : '<span class="text-red-300 font-semibold">above your max: decline</span>'}`;
  }));
  $$('.combo-form', root).forEach((form) => form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = { mode: f.get('mode'), combo_legs: JSON.parse(form.dataset.legs), limit_price: parseFloat(f.get('limit_price')),
      contracts: parseInt(f.get('contracts'), 10), tier: f.get('tier'), fair_at_entry: parseFloat(f.get('fair_at_entry')), edge_at_entry: parseFloat(f.get('edge_at_entry')) };
    if (body.mode === 'real' && !confirm(`Log a REAL parlay you already placed on Kalshi at ${cents(body.limit_price)} x ${body.contracts}? Nothing is sent to Kalshi.`)) return;
    try { const t = await post('/trades', body); toast(`Logged ${t.mode} parlay #${t.id} at ${cents(t.limit_price)}.`); await loadTab(true); refreshStatus(); } catch (err) { toast(err.message, true); }
  }));
}

/* ---------- Follow proven bettors ---------- */
function viewFollow() {
  const d = S.follow;
  if (!d) return loading();
  const picks = d.picks.map((p) => `<tr class="border-t border-ink-800 align-top">
      <td class="py-1.5 pr-3"><div class="font-medium">${esc(p.name)}</div><div class="text-xs ${p.proven ? 'text-emerald-300' : 'text-ink-500'}">${p.proven ? 'proven' : 'context only'} · ${esc(p.short)}</div></td>
      <td class="pr-3">${esc(p.label)}<div class="text-[11px] text-ink-500 font-mono">${esc(p.ticker)}</div></td>
      <td class="pr-3 tabular-nums">${cents(p.their_price)}</td><td class="pr-3 tabular-nums">${money(p.size_usd)}</td>
      <td class="pr-3 tabular-nums">${cents(p.kalshi_bid)} / ${cents(p.kalshi_ask)}</td>
      <td class="pr-3 tabular-nums text-orange-300">${p.fair != null ? pct(p.fair) : '—'}</td>
      <td class="pr-3">${p.tier ? tierBadge(p.tier) : ''}</td>
      <td class="text-xs">${esc(p.verdict)}<div class="text-[11px] text-ink-500">seen ${esc(p.first_seen_hst || '')}</div></td></tr>`).join('');
  const wallets = d.wallets.map((w) => {
    const st = w.stats || {};
    return `<tr class="border-t border-ink-800 align-top">
      <td class="py-1.5 pr-3"><div class="font-medium">${esc(w.name)}</div><a class="text-xs underline decoration-dotted text-ink-400" target="_blank" rel="noopener" href="${esc(w.profile)}">${esc(w.short)}</a></td>
      <td class="pr-3"><span class="font-bold ${w.proven ? 'text-emerald-300' : 'text-ink-400'}">${w.proven ? 'Proven' : 'Context'}</span></td>
      <td class="pr-3 tabular-nums">${st.nfl_picks ?? 0}</td><td class="pr-3 tabular-nums">${st.wins ?? 0}-${st.losses ?? 0}${st.win_pct != null ? ` (${st.win_pct}%)` : ''}</td>
      <td class="pr-3 tabular-nums ${pnlClass(st.pnl)}">${money(st.pnl)}</td><td class="pr-3 tabular-nums">${signed(st.units, 1, 'u')}</td>
      <td class="pr-3 tabular-nums">${st.roi_pct != null ? signed(st.roi_pct, 1, '%') : '—'}</td>
      <td class="pr-3 text-xs">${w.rolling.active ? `${w.rolling.wins}-${w.rolling.losses}, ${w.rolling.units}u` : `${w.rolling.graded}/50 graded`}</td>
      <td class="text-xs text-ink-400 max-w-[20rem]">${esc(st.why_not || w.why)}</td></tr>`;
  }).join('');
  const cons = (d.consensus || []).map((c) => `<span class="inline-flex items-center gap-1 px-2 py-1 rounded-full border border-emerald-700 text-emerald-200 text-xs">${esc(c.label)} · ${c.wallets} proven · ${money(c.usd)}</span>`).join(' ');
  return `<div class="grid grid-cols-1 gap-4">
    ${card(`${h2('Follow proven bettors', `${d.proven_count} proven of ${d.wallets.length} graded`)}
      <div class="text-sm text-ink-300 space-y-1">
        <p>Bettors are graded by this app from their public Polymarket record (on-chain, resolved NFL positions only). Pick weight requires your proof rule: <b>200+ NFL picks or a full season, with positive units</b>. Hot streaks, small samples, and market makers (both sides of the same game) stay context only.</p>
        <p>Their open NFL game-winner positions show up here, mapped to the Kalshi market. A proven bettor on your side is what Tier G needs; one on the other side forces Pass. Following is a signal, not a reason to chase: if Kalshi already trades at or above our fair price, the row says so.</p>
      </div>
      <div class="mt-3 flex flex-wrap gap-2"><button class="${btn}" data-run-job="follow_discover">Grade bettors now</button><button class="${btn}" data-run-job="follow_picks">Refresh their picks</button></div>
      <form id="wallet-form" class="mt-3 flex flex-wrap gap-2"><input name="wallet" class="${inputCls} max-w-md font-mono" placeholder="0x… Polymarket wallet to grade"><input name="name" class="${inputCls} max-w-[12rem]" placeholder="name (optional)"><button class="${btn}">Grade this wallet</button></form>`)}
    ${cons ? card(`${h2('Where proven bettors agree')}<div class="flex flex-wrap gap-2">${cons}</div>`) : ''}
    ${card(`${h2('Their open NFL picks')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[900px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Bettor</th><th class="pr-3">Pick</th><th class="pr-3">Their price</th><th class="pr-3">Size</th><th class="pr-3">Kalshi bid/ask</th><th class="pr-3">Our fair</th><th class="pr-3">Tier</th><th>Verdict</th></tr></thead><tbody>${picks || '<tr><td colspan="8" class="py-3 text-ink-500">No open NFL picks from graded bettors right now.</td></tr>'}</tbody></table></div>`)}
    ${card(`${h2('Graded bettors', 'NFL only; proof never transfers across sports')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[960px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Bettor</th><th class="pr-3">Status</th><th class="pr-3">NFL picks</th><th class="pr-3">W-L</th><th class="pr-3">Profit</th><th class="pr-3">Units</th><th class="pr-3">ROI</th><th class="pr-3">Our grade</th><th>Notes</th></tr></thead><tbody>${wallets || '<tr><td colspan="9" class="py-3 text-ink-500">Nobody graded yet. Press "Grade bettors now".</td></tr>'}</tbody></table></div>`)}
  </div>`;
}
function bindFollow(root) {
  $$('[data-run-job]', root).forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; const label = b.textContent; b.textContent = 'Working…';
    try { const r = await post(`/jobs/${b.dataset.runJob}/run`); toast(r.error ? `Failed: ${r.error}` : `Done: ${JSON.stringify(r).slice(0, 140)}`, !!r.error); } catch (e) { toast(e.message, true); }
    b.textContent = label; b.disabled = false; await loadTab(true);
  }));
  $('#wallet-form', root)?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { const r = await post('/follow/wallet', { wallet: f.get('wallet'), name: f.get('name') || null }); toast(r.eligible ? 'Proven: this wallet now counts.' : `Graded, context only: ${r.why_not}`); await loadTab(true); } catch (err) { toast(err.message, true); }
  });
}

/* ---------- Boards 4 & 5 (Phase 2) ---------- */
const viewNews = () => phaseNote('<b>News Movers</b> arrives in Phase 2: approved RSS feeds, GDELT, and official sources, deduplicated, each story linked to the tickers it could move with price before/after, Hawaii time, and a link confidence (weak links labeled weak). Add the feeds you approve under Settings now.');
const viewFlow = () => phaseNote('<b>Flow and Big Trades tape</b> arrives in Phase 2: Kalshi trades endpoint and order book, large trades by dollar size and percentile, volume and open interest spikes, book imbalance, and unexplained moves. Polymarket whale activity shown as context only.');

/* ---------- Board 6: Markets & History ---------- */
async function loadMarkets() {
  const p = new URLSearchParams({ limit: 150, sort: S.mkt.sort });
  if (S.mkt.cat) p.set('category', S.mkt.cat);
  if (S.mkt.q) p.set('q', S.mkt.q);
  const [cats, rows] = await Promise.all([api('/markets/categories'), api('/markets?' + p)]);
  S.mkt.cats = cats; S.mkt.rows = rows;
  if (S.mkt.sel) S.mkt.detail = await api(`/markets/${encodeURIComponent(S.mkt.sel)}?hours=${S.mkt.hours || 24}`).catch(() => null);
}
function viewMarkets() {
  const m = S.mkt;
  if (!m.rows) return loading();
  const watched = new Set(S.settings?.watched_tickers || []);
  const chips = [{ category: '', count: m.cats.reduce((a, c) => a + c.count, 0) }, ...m.cats].map((c) =>
    `<button data-cat="${esc(c.category)}" class="px-2.5 py-1 rounded-full border text-xs ${m.cat === c.category ? 'border-red-500 text-white bg-red-950/40' : 'border-ink-700 text-ink-300'}">${esc(c.category || 'All')} <span class="tabular-nums text-ink-400">${c.count}</span></button>`).join('');
  const rows = m.rows.map((r) => `<tr class="border-t border-ink-800 hover:bg-ink-850 cursor-pointer" data-sel="${esc(r.ticker)}">
      <td class="py-1.5 pr-3"><div class="truncate max-w-[22rem]">${watched.has(r.ticker) ? '★ ' : ''}${esc(r.event_title || r.title)}</div><div class="text-xs text-ink-400 truncate max-w-[22rem]">${esc(r.yes_sub_title || r.title || '')} · <span class="font-mono">${esc(r.ticker)}</span></div></td>
      <td class="pr-3 text-xs text-ink-400">${esc(r.category)}</td>
      <td class="pr-3 tabular-nums">${cents(r.yes_bid)} / ${cents(r.yes_ask)}</td><td class="pr-3 tabular-nums">${cents(r.last_price)}</td>
      <td class="pr-3 tabular-nums">${r.volume_24h != null ? Math.round(r.volume_24h).toLocaleString() : '—'}</td>
      <td class="pr-3 tabular-nums">${r.open_interest != null ? Math.round(r.open_interest).toLocaleString() : '—'}</td>
      <td class="text-xs text-ink-400 whitespace-nowrap">${esc(r.close_hst || '')}</td></tr>`).join('');
  return `<div class="flex flex-wrap gap-1.5 mb-3">${chips}</div>
    <div class="flex flex-wrap gap-2 mb-3">
      <input id="mq" class="${inputCls} max-w-xs" placeholder="Search title or ticker" value="${esc(m.q)}">
      <select id="msort" class="${inputCls} max-w-[12rem]">${[['volume_24h', '24h volume'], ['volume', 'Total volume'], ['oi', 'Open interest'], ['close', 'Closing soonest']].map(([v, l]) => `<option value="${v}" ${m.sort === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
    </div>
    ${m.detail ? marketDetail(m.detail, watched) : ''}
    ${card(`<div class="overflow-x-auto"><table class="text-sm w-full min-w-[760px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left">
      <tr><th class="py-1 pr-3">Market</th><th class="pr-3">Board</th><th class="pr-3">Bid / ask</th><th class="pr-3">Last</th><th class="pr-3">24h vol</th><th class="pr-3">Open int.</th><th>Closes</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="py-4 text-ink-500">No markets match. If the scanner has not run yet, check the freshness chips above.</td></tr>'}</tbody></table></div>`)}
    <div class="mt-4">${phaseNote('Calibration by category (when Kalshi said 70%, how often it happened), closing line value, and the no-look-ahead backtest replay arrive in Phase 3. Every snapshot needed for them is being stored now.')}</div>`;
}
function marketDetail(d, watched) {
  const m = d.market;
  const isW = watched.has(m.ticker);
  const fakeOpp = { ticker: m.ticker, side: 'yes', maker_price: m.yes_bid != null ? Math.min(+(m.yes_bid + 0.01).toFixed(2), (m.yes_ask ?? 1) - 0.01) : null, size: {}, tier: 'P', fee_rates: { maker: 0.0175, taker: 0.07 } };
  return card(`<div class="flex flex-wrap items-start gap-3 mb-2">
      <div class="grow min-w-0"><div class="font-semibold">${esc(m.event_title || m.title)}</div><div class="text-xs text-ink-400">${esc(m.yes_sub_title || '')} · <span class="font-mono">${esc(m.ticker)}</span> · ${esc(m.category)} · closes ${esc(m.close_hst)}</div></div>
      <select id="mhours" class="${inputCls} max-w-[8rem]">${[[6, '6h'], [24, '24h'], [168, '7d'], [0, 'All']].map(([v, l]) => `<option value="${v}" ${(S.mkt.hours ?? 24) === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <button id="mwatch" class="${btn}">${isW ? '★ Watching' : '☆ Watch'}</button><button id="mclose" class="${btn}">Close</button></div>
    <div class="flex flex-wrap gap-x-6 gap-y-1 text-sm mb-3">${stat('Bid / ask', `${cents(m.yes_bid)} / ${cents(m.yes_ask)}`)}${stat('Last', cents(m.last_price))}${stat('Volume', m.volume != null ? Math.round(m.volume).toLocaleString() : '—')}${stat('Open interest', m.open_interest != null ? Math.round(m.open_interest).toLocaleString() : '—')}${stat('Snapshots shown', d.history.length)}${stat('Fee type', esc(d.series?.fee_type || 'not loaded'))}</div>
    <div class="grid gap-4 lg:grid-cols-2"><div><div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">YES price</div><div class="h-48"><canvas id="md-price"></canvas></div></div>
      <div><div class="text-[11px] uppercase tracking-wider text-ink-400 mb-1">Volume (contracts)</div><div class="h-48"><canvas id="md-vol"></canvas></div></div></div>
    ${m.rules_primary ? `<details class="mt-3"><summary class="text-sm text-ink-300 cursor-pointer">Rules</summary><p class="text-sm text-ink-300 mt-2">${esc(m.rules_primary)}</p></details>` : ''}
    <div class="mt-3">${tradeForm(fakeOpp)}</div>`, 'mb-4');
}
function bindMarkets(root) {
  $$('[data-cat]', root).forEach((b) => b.addEventListener('click', async () => { S.mkt.cat = b.dataset.cat; await loadTab(true); }));
  $$('[data-sel]', root).forEach((tr) => tr.addEventListener('click', async () => { S.mkt.sel = tr.dataset.sel; await loadTab(true); window.scrollTo({ top: 0, behavior: 'smooth' }); }));
  const q = $('#mq', root);
  if (q) q.addEventListener('change', async () => { S.mkt.q = q.value.trim(); await loadTab(true); });
  const so = $('#msort', root);
  if (so) so.addEventListener('change', async () => { S.mkt.sort = so.value; await loadTab(true); });
  const mh = $('#mhours', root);
  if (mh) mh.addEventListener('change', async () => { S.mkt.hours = parseInt(mh.value, 10); await loadTab(true); });
  const mc = $('#mclose', root);
  if (mc) mc.addEventListener('click', () => { S.mkt.sel = null; S.mkt.detail = null; render(); });
  const mw = $('#mwatch', root);
  if (mw) mw.addEventListener('click', async () => {
    const t = S.mkt.detail.market.ticker;
    const list = new Set(S.settings.watched_tickers || []);
    if (list.has(t)) list.delete(t); else list.add(t);
    S.settings = await put('/settings', { watched_tickers: [...list] });
    toast(list.has(t) ? 'Watching: snapshots every minute.' : 'Removed from watchlist.');
    render();
  });
  $$('.trade-form', root).forEach((form) => { updateMaxLoss(form); form.addEventListener('input', () => updateMaxLoss(form)); form.addEventListener('submit', (e) => { e.preventDefault(); submitTrade(form); }); });
  const d = S.mkt.detail;
  if (d && d.history.length) {
    const h = d.history;
    const labs = h.map((x) => fmtDay.format(new Date(x.ts * 1000)));
    const c = (v) => (v == null ? null : +(v * 100).toFixed(1));
    lineChart('md-price', labs, [
      { label: 'Mid', data: h.map((x) => (x.yes_bid != null && x.yes_ask != null ? +(((x.yes_bid + x.yes_ask) / 2) * 100).toFixed(1) : null)), borderColor: COLORS.kalshi },
      { label: 'Bid', data: h.map((x) => c(x.yes_bid)), borderColor: COLORS.neutral, borderWidth: 1, borderDash: [4, 3] },
      { label: 'Ask', data: h.map((x) => c(x.yes_ask)), borderColor: COLORS.neutral, borderWidth: 1 },
    ], { yFmt: (v) => v + '¢', tip: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y}¢` });
    lineChart('md-vol', labs, [{ label: 'Volume', data: h.map((x) => x.volume), borderColor: COLORS.kalshi }], { tip: (ctx) => ` Volume: ${Math.round(ctx.parsed.y).toLocaleString()}` });
  }
}

/* ---------- Trades ---------- */
async function loadTrades() {
  const [p, t] = await Promise.all([api('/portfolio'), api('/trades')]);
  S.port = p; S.trades = t;
}
function viewTrades() {
  const p = S.port;
  if (!p) return loading();
  const pos = p.positions.map((t) => `<tr class="border-t border-ink-800 align-top">
      <td class="py-1.5 pr-3"><span class="text-[10px] uppercase px-1.5 py-0.5 rounded border ${t.mode === 'real' ? 'border-red-600 text-red-300' : 'border-ink-600 text-ink-300'}">${t.mode}</span> <span class="text-xs ${t.status === 'resting' ? 'text-amber-300' : 'text-emerald-300'}">${t.status}</span></td>
      <td class="pr-3"><div class="truncate max-w-[16rem]">${esc(t.title || t.ticker)}</div><div class="text-xs text-ink-400 font-mono truncate max-w-[16rem]">${esc(t.ticker)}</div><div class="text-[11px] text-ink-500">${esc(t.fill_note || '')}</div></td>
      <td class="pr-3">${t.side.toUpperCase()} ${t.tier ? tierBadge(t.tier) : ''}</td>
      <td class="pr-3 tabular-nums">${t.contracts} @ ${cents(t.limit_price)}</td><td class="pr-3 tabular-nums">${money(t.fees)}</td>
      <td class="pr-3 tabular-nums">${money(t.max_loss)}</td><td class="pr-3 tabular-nums">${cents(t.mark)}</td>
      <td class="pr-3 tabular-nums ${pnlClass(t.unrealized_pnl)}">${money(t.unrealized_pnl)}</td>
      <td class="whitespace-nowrap">${t.status === 'resting' ? `<button class="${btn} text-xs" data-cancel="${t.id}">Cancel</button>` : `<button class="${btn} text-xs" data-close="${t.id}" data-mark="${t.mark ?? ''}">Close…</button>`}</td></tr>`).join('');
  const done = (S.trades || []).filter((t) => ['settled', 'closed', 'cancelled'].includes(t.status)).map((t) => `<tr class="border-t border-ink-800">
      <td class="py-1.5 pr-3 text-xs">${esc(t.closed_hst || '')}</td><td class="pr-3 text-xs">${t.mode} · ${t.status}${t.settlement_result ? ' (' + t.settlement_result.toUpperCase() + ')' : ''}</td>
      <td class="pr-3"><div class="truncate max-w-[16rem]">${esc(t.title || t.ticker)}</div></td><td class="pr-3">${t.side.toUpperCase()} ${t.tier || ''}</td>
      <td class="pr-3 tabular-nums">${t.contracts} @ ${cents(t.limit_price)}</td><td class="pr-3 tabular-nums">${t.clv_pts != null ? signed(t.clv_pts, 1, ' pts') : '—'}</td>
      <td class="tabular-nums ${pnlClass(t.realized_pnl)}">${money(t.realized_pnl)}</td></tr>`).join('');
  const tiers = Object.entries(p.by_tier || {}).map(([k, v]) => `${tierBadge(k)} <span class="text-sm tabular-nums mr-4">${v.n} trades · ${v.wins} wins · ${money(v.pnl)}</span>`).join('');
  return `<div class="grid grid-cols-1 gap-4">
    ${card(`<div class="flex flex-wrap gap-x-8 gap-y-3">${stat('Bankroll (start + paper realized)', money(p.bankroll))}${stat('Paper realized', money(p.realized_paper), pnlClass(p.realized_paper))}${stat('Real realized (logged)', money(p.realized_real), pnlClass(p.realized_real))}${stat('Unrealized (at bid)', money(p.unrealized), pnlClass(p.unrealized))}${stat('Open exposure (max loss)', money(p.open_exposure))}${stat('Correlated cap', money(p.correlated_cap))}</div>
      ${p.warnings.map((w) => `<div class="mt-3 rounded-md border border-red-700 bg-red-950/50 px-3 py-2 text-sm text-red-100">${esc(w)}</div>`).join('')}
      <div class="mt-3 text-xs text-ink-400">Exposure by board: ${Object.entries(p.exposure_by_category).map(([k, v]) => `${esc(k)} ${money(v)}`).join(' · ') || 'none'}</div>
      <div class="mt-3 flex flex-wrap gap-2"><a class="${btn}" href="/api/trades.csv">Export CSV</a></div>`)}
    ${card(`${h2('Open positions and resting orders')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[820px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Status</th><th class="pr-3">Market</th><th class="pr-3">Side</th><th class="pr-3">Qty @ price</th><th class="pr-3">Fees</th><th class="pr-3">Max loss</th><th class="pr-3">Mark</th><th class="pr-3">Unrealized</th><th></th></tr></thead><tbody>${pos || '<tr><td colspan="9" class="py-4 text-ink-500">No open positions. Log a paper trade from any edge row.</td></tr>'}</tbody></table></div>`)}
    ${card(`${h2('By tier')}${tiers || '<span class="text-sm text-ink-500">No closed trades yet.</span>'}`)}
    ${card(`${h2('History')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[760px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Closed</th><th class="pr-3">Result</th><th class="pr-3">Market</th><th class="pr-3">Side</th><th class="pr-3">Qty @ price</th><th class="pr-3">CLV</th><th>P&amp;L</th></tr></thead><tbody>${done || '<tr><td colspan="7" class="py-4 text-ink-500">Nothing closed yet.</td></tr>'}</tbody></table></div>`)}
    ${card(`${h2('Log a trade for any ticker')}<div id="free-trade"></div>`)}
  </div>`;
}
function bindTrades(root) {
  $$('[data-cancel]', root).forEach((b) => b.addEventListener('click', async () => { try { await post(`/trades/${b.dataset.cancel}/cancel`); toast('Order cancelled.'); await loadTab(true); } catch (e) { toast(e.message, true); } }));
  $$('[data-close]', root).forEach((b) => b.addEventListener('click', async () => {
    const px = prompt('Exit price in dollars (e.g. 0.62). Taker fee is added automatically.', b.dataset.mark || '');
    if (px == null) return;
    try { const t = await post(`/trades/${b.dataset.close}/close`, { exit_price: parseFloat(px) }); toast(`Closed: ${money(t.realized_pnl)}`); await loadTab(true); refreshStatus(); } catch (e) { toast(e.message, true); }
  }));
  const holder = $('#free-trade', root);
  if (holder) {
    holder.innerHTML = `<label class="text-xs text-ink-400 block mb-2">Ticker<input id="ft-ticker" class="${inputCls} max-w-md" placeholder="KXNFLGAME-…"></label>` + tradeForm({ ticker: '', side: 'yes', size: {}, tier: 'P', fee_rates: { maker: 0.0175, taker: 0.07 } });
    const form = $('.trade-form', holder);
    updateMaxLoss(form);
    form.addEventListener('input', () => updateMaxLoss(form));
    form.addEventListener('submit', (e) => { e.preventDefault(); form.dataset.ticker = $('#ft-ticker').value.trim(); submitTrade(form).then(() => loadTab(true)); });
  }
}

/* ---------- Settings ---------- */
const SETTING_FIELDS = [
  ['Bankroll and sizing', [['bankroll', 'Starting bankroll ($)'], ['kelly_fraction', 'Kelly fraction (0.25 = quarter Kelly)'], ['per_strike_cap_pct', 'Per-strike cap (% of bankroll)'], ['correlated_cap_pct', 'Correlated cap (% of bankroll)']]],
  ['Tier gates', [['tiers.s_min_edge_pts', 'S: edge above (pts)'], ['tiers.s_min_net_ev_pct', 'S: net EV above (%)'], ['tiers.g_min_edge_pts', 'G: edge at least (pts)'], ['tiers.g_min_net_ev_pct', 'G: net EV at least (%)'], ['tiers.g_size_multiplier', 'G size multiplier'], ['tiers.w_min_edge_pts', 'Watch: edge at least (pts)']]],
  ['Kalshi fees', [['fees.taker_rate', 'Taker rate'], ['fees.maker_rate', 'Maker rate when a series fee type is unknown'], ['fees.rounding_precision', 'Fee rounding ($0.0001 direct member, 0.01 non-direct)'], ['fees.index_taker_rate', 'Index market taker rate'], ['fees.assume_maker_fee_when_unknown', 'Assume maker fee when series fee type unknown (true/false)']]],
  ['Gas model', [['gas.model_weight', 'Model weight vs Kalshi ladder probability (0-1)'], ['gas.drift_weight', 'Drift weight on last 3 daily changes'], ['gas.min_history_days', 'Minimum AAA daily changes before rating'], ['gas.sigma_floor', 'Daily sigma floor ($)'], ['gas.calibration_horizon_hours', 'Calibration: hours before close'], ['gas.min_graded_days', 'Graded days the model needs before rating above Watch']]],
  ['Following and parlays', [['follow.min_proof_picks', 'Proof rule: minimum graded NFL picks'], ['follow.full_season_weeks', 'Proof rule: weeks that count as a full season'], ['follow.min_position_usd', 'Ignore followed positions smaller than ($)'], ['follow.max_hedge_share', 'Market-maker filter: max share of games with both sides'], ['follow.candidates_per_window', 'Leaderboard wallets to grade per window'], ['parlays.min_net_ev_pct', 'Parlay max price: net EV at least (%)'], ['parlays.max_legs', 'Parlay max legs'], ['parlays.candidate_legs', 'Parlay leg pool size']]],
  ['Staleness (minutes)', [['staleness_minutes.kalshi_scan', 'Kalshi price'], ['staleness_minutes.espn_nfl', 'ESPN line'], ['staleness_minutes.odds_api', 'Odds API lines'], ['staleness_minutes.manual_lines', 'Manual lines'], ['staleness_minutes.aaa', 'AAA']]],
];
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
function setPath(o, p, v) { const ks = p.split('.'); let c = o; ks.slice(0, -1).forEach((k) => { c[k] = c[k] || {}; c = c[k]; }); c[ks.at(-1)] = v; }
function viewSettings() {
  const s = S.settings;
  if (!s || !S.experts) return loading();
  const groups = SETTING_FIELDS.map(([title, fields]) => card(`${h2(title)}<div class="grid sm:grid-cols-2 gap-3">${fields.map(([path, label]) => `<label class="text-xs text-ink-400">${label}<input data-path="${path}" class="${inputCls}" value="${esc(getPath(s, path))}"></label>`).join('')}</div>`)).join('');
  const lists = card(`${h2('Lists', 'one per line')}<div class="grid sm:grid-cols-2 gap-3">
    <label class="text-xs text-ink-400">Watched tickers (snapshot every minute)<textarea data-list="watched_tickers" rows="5" class="${inputCls} font-mono">${esc((s.watched_tickers || []).join('\n'))}</textarea></label>
    <label class="text-xs text-ink-400">Approved news feeds (used in Phase 2)<textarea data-list="news_feeds" rows="5" class="${inputCls} font-mono">${esc((s.news_feeds || []).join('\n'))}</textarea></label>
    <label class="text-xs text-ink-400">NFL fair source priority<textarea data-list="nfl.fair_source_priority" rows="3" class="${inputCls} font-mono">${esc((s.nfl.fair_source_priority || []).join('\n'))}</textarea></label>
    <label class="text-xs text-ink-400">Gas series tickers<textarea data-list="gas.series" rows="3" class="${inputCls} font-mono">${esc((s.gas.series || []).join('\n'))}</textarea></label>
    <label class="text-xs text-ink-400">Extra series snapshotted every minute<textarea data-list="active_series" rows="3" class="${inputCls} font-mono">${esc((s.active_series || []).join('\n'))}</textarea></label></div>`);
  const ex = S.experts.map((e) => `<tr class="border-t border-ink-800 align-top">
      <td class="py-1.5 pr-3 font-medium">${esc(e.name)}<div class="text-xs text-ink-400">${esc(e.notes || '')}</div></td><td class="pr-3">${esc(e.sport)}</td>
      <td class="pr-3"><span class="font-bold ${e.pick_weight ? 'text-emerald-300' : 'text-ink-400'}">${e.proof_grade}</span> <span class="text-xs text-ink-400">${esc(e.proof_note)}</span>${e.proof_url ? ` <a class="text-xs underline" target="_blank" rel="noopener" href="${esc(e.proof_url)}">proof</a>` : ''}</td>
      <td class="pr-3 text-xs">${e.rolling.active ? `${e.rolling.wins}-${e.rolling.losses} · ${e.rolling.units}u · ${e.rolling.win_pct}%` : `${e.rolling.graded}/50 graded`}</td>
      <td class="pr-3 text-xs">${e.open_picks}</td>
      <td class="whitespace-nowrap"><button class="${btn} text-xs" data-pick="${e.id}">Log pick</button> <button class="${btn} text-xs" data-del="${e.id}">Delete</button></td></tr>`).join('');
  const expertCard = card(`${h2('Expert roster', 'pick weight only with a published or independently graded record of 200+ picks or a full season in that exact sport, with positive units')}
    <div class="overflow-x-auto"><table class="text-sm w-full min-w-[760px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Name</th><th class="pr-3">Sport</th><th class="pr-3">Proof grade</th><th class="pr-3">Our grade</th><th class="pr-3">Open picks</th><th></th></tr></thead><tbody>${ex || '<tr><td colspan="6" class="py-3 text-ink-500">No experts yet. Add one with a verifiable record.</td></tr>'}</tbody></table></div>
    <form id="expert-form" class="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
      <label class="text-xs text-ink-400">Name<input name="name" required class="${inputCls}"></label>
      <label class="text-xs text-ink-400">Sport<select name="sport" class="${inputCls}">${['NFL', 'NCAAF', 'NBA', 'MLB', 'NHL'].map((x) => `<option>${x}</option>`).join('')}</select></label>
      <label class="text-xs text-ink-400">Proof type<select name="proof_type" class="${inputCls}"><option value="none">none (context only)</option><option value="published">published record</option><option value="independent">independently graded</option></select></label>
      <label class="text-xs text-ink-400">Graded picks<input name="proof_picks" type="number" min="0" class="${inputCls}"></label>
      <label class="text-xs text-ink-400">Units (+/-)<input name="proof_units" type="number" step="0.1" class="${inputCls}"></label>
      <label class="text-xs text-ink-400 flex items-end gap-2 pb-2"><input type="checkbox" name="proof_full_season"> full season</label>
      <label class="text-xs text-ink-400 col-span-2">Proof link<input name="proof_url" class="${inputCls}"></label>
      <label class="text-xs text-ink-400 col-span-2 sm:col-span-3">Notes<input name="notes" class="${inputCls}"></label>
      <div class="flex items-end"><button class="${btn} w-full">Add expert</button></div></form>`);
  return `<div class="grid grid-cols-1 gap-4">${groups}${lists}
    <div class="flex gap-2"><button id="save-settings" class="${btnPrimary}">Save settings</button><button id="reset-settings" class="${btn}">Reset to defaults</button></div>
    ${expertCard}
    ${card(`<div class="text-sm text-ink-300">Secrets (ODDS_API_KEY, Telegram tokens) live only in the <span class="font-mono">.env</span> file and are never shown or saved here.</div>`)}</div>`;
}
function bindSettings(root) {
  $('#save-settings', root)?.addEventListener('click', async () => {
    const patch = {};
    $$('[data-path]', root).forEach((i) => {
      const v = i.value.trim();
      setPath(patch, i.dataset.path, v === 'true' ? true : v === 'false' ? false : (v !== '' && !isNaN(Number(v)) ? Number(v) : v));
    });
    $$('[data-list]', root).forEach((t) => setPath(patch, t.dataset.list, t.value.split('\n').map((x) => x.trim()).filter(Boolean)));
    try { S.settings = await put('/settings', patch); toast('Settings saved. Edges recomputed.'); refreshStatus(); } catch (e) { toast(e.message, true); }
  });
  $('#reset-settings', root)?.addEventListener('click', async () => { if (!confirm('Reset every setting to the defaults?')) return; S.settings = await post('/settings/reset'); render(); });
  $('#expert-form', root)?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = Object.fromEntries(f.entries());
    body.proof_full_season = f.get('proof_full_season') === 'on';
    try { await post('/experts', body); toast('Expert added.'); await loadTab(true); } catch (err) { toast(err.message, true); }
  });
  $$('[data-del]', root).forEach((b) => b.addEventListener('click', async () => { if (!confirm('Delete this expert and their logged picks?')) return; await api(`/experts/${b.dataset.del}`, { method: 'DELETE' }); await loadTab(true); }));
  $$('[data-pick]', root).forEach((b) => b.addEventListener('click', async () => {
    const t = prompt('Ticker the expert picked (exact Kalshi ticker):');
    if (!t) return;
    const side = (prompt('Side: yes or no', 'yes') || '').toLowerCase();
    const url = prompt('Link to the pick (optional):') || null;
    try { await post(`/experts/${b.dataset.pick}/picks`, { ticker: t, side, source_url: url }); toast('Pick logged.'); await loadTab(true); } catch (err) { toast(err.message, true); }
  }));
}

/* ---------- Audit ---------- */
async function loadAudit() {
  const p = new URLSearchParams({ limit: 200 });
  if (S.audit.source) p.set('source', S.audit.source);
  if (S.audit.failed) p.set('failed', 'true');
  S.pulls = await api('/pulls?' + p);
}
function viewAudit() {
  const fr = S.status?.freshness || [];
  const jobsRows = fr.map((f) => `<tr class="border-t border-ink-800 align-top"><td class="py-1.5 pr-3">${esc(f.label)}</td>
    <td class="pr-3"><span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs ${STATE_STYLE[f.state]}"><span class="w-1.5 h-1.5 rounded-full ${DOT[f.state]}"></span>${freshText(f)}</span></td>
    <td class="pr-3 text-xs">${esc(f.last_ok || '—')}</td><td class="pr-3 text-xs text-red-300">${esc(f.last_error || '')}</td>
    <td class="pr-3 text-xs font-mono text-ink-400 break-all max-w-[18rem]">${esc(f.detail || '')}</td>
    <td>${f.enabled ? `<button class="${btn} text-xs" data-run="${f.name}">Run now</button>` : ''}</td></tr>`).join('');
  const pulls = (S.pulls || []).map((p) => `<tr class="border-t border-ink-800 ${S.audit.pull == p.id ? 'bg-red-950/40' : ''}">
    <td class="py-1 pr-3 tabular-nums">#${p.id}</td><td class="pr-3">${esc(p.source)}</td><td class="pr-3 text-xs whitespace-nowrap">${esc(p.started_hst)}</td>
    <td class="pr-3">${p.ok ? '<span class="text-emerald-400">ok</span>' : (p.duration_ms == null && !p.error ? '<span class="text-amber-300">running</span>' : '<span class="text-red-400 font-semibold">failed</span>')} ${p.http_status ?? ''}</td>
    <td class="pr-3 tabular-nums">${p.rows ?? ''}</td><td class="pr-3 tabular-nums whitespace-nowrap">${p.duration_ms != null ? p.duration_ms + ' ms' : ''}</td>
    <td class="text-xs font-mono text-ink-400 break-all">${esc(p.endpoint)} ${esc(p.params || '')}${p.error ? `<div class="text-red-300">${esc(p.error)}</div>` : ''}</td></tr>`).join('');
  return `<div class="grid grid-cols-1 gap-4">${card(`${h2('Data jobs', 'every scheduled pull, its last good time (HST), and its last error')}<div class="overflow-x-auto"><table class="text-sm w-full min-w-[820px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Job</th><th class="pr-3">State</th><th class="pr-3">Last good</th><th class="pr-3">Last error</th><th class="pr-3">Last result</th><th></th></tr></thead><tbody>${jobsRows}</tbody></table></div>`)}
    ${card(`${h2('Pull log', 'every HTTP request; prices and lines carry the pull id they came from')}
      <div class="flex flex-wrap gap-2 mb-3"><select id="asrc" class="${inputCls} max-w-[10rem]">${['', 'kalshi', 'espn', 'odds_api', 'aaa'].map((x) => `<option value="${x}" ${S.audit.source === x ? 'selected' : ''}>${x || 'all sources'}</option>`).join('')}</select>
      <label class="text-sm text-ink-300 flex items-center gap-2"><input type="checkbox" id="afail" ${S.audit.failed ? 'checked' : ''}> failed only</label></div>
      <div class="overflow-x-auto"><table class="text-sm w-full min-w-[820px]"><thead class="text-[11px] uppercase tracking-wider text-ink-500 text-left"><tr><th class="py-1 pr-3">Pull</th><th class="pr-3">Source</th><th class="pr-3">Time</th><th class="pr-3">Result</th><th class="pr-3">Rows</th><th class="pr-3">Took</th><th>Request</th></tr></thead><tbody>${pulls || '<tr><td colspan="7" class="py-3 text-ink-500">No pulls logged yet.</td></tr>'}</tbody></table></div>`)}</div>`;
}
function bindAudit(root) {
  $$('[data-run]', root).forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'Running…';
    try { const r = await post(`/jobs/${b.dataset.run}/run`); toast(r.error ? `Failed: ${r.error}` : `Done: ${JSON.stringify(r).slice(0, 120)}`, !!r.error); } catch (e) { toast(e.message, true); }
    await refreshStatus(); await loadTab(true);
  }));
  $('#asrc', root)?.addEventListener('change', async (e) => { S.audit.source = e.target.value; await loadTab(true); });
  $('#afail', root)?.addEventListener('change', async (e) => { S.audit.failed = e.target.checked; await loadTab(true); });
}

/* ---------- loading / routing ---------- */
const loading = () => '<div class="text-ink-400 text-sm py-10 text-center">Loading…</div>';
const VIEWS = {
  edges: [viewEdges, bindEdgeRows], sports: [viewSports, (r) => { bindEdgeRows(r); bindSports(r); }],
  parlays: [viewParlays, bindParlays], follow: [viewFollow, bindFollow],
  econ: [viewEcon, (r) => { bindEdgeRows(r); bindEcon(r); }], news: [viewNews, () => {}], flow: [viewFlow, () => {}],
  markets: [viewMarkets, bindMarkets], trades: [viewTrades, bindTrades], settings: [viewSettings, bindSettings], audit: [viewAudit, bindAudit],
};
function render() {
  renderNav();
  destroyCharts();
  const [view, bind] = VIEWS[S.tab] || VIEWS.edges;
  const root = $('#view');
  try {
    root.innerHTML = view();
    bind(root);
  } catch (e) {
    root.innerHTML = `<div class="text-red-300 text-sm">Display error: ${esc(e.message)}</div>`;
    console.error(e);
  }
  if (S.tab === 'audit' && S.audit.pull) {
    const row = $('.bg-red-950\\/40', root);
    if (row) row.scrollIntoView({ block: 'center' });
  }
}
async function loadTab(force = false) {
  try {
    if (!S.settings) S.settings = await api('/settings');
    const t = S.tab;
    if (t === 'edges') S.edges = await api('/edges' + (force ? '?force=true' : ''));
    if (t === 'sports') S.nfl = await api('/nfl');
    if (t === 'econ') S.gas = await api('/gas');
    if (t === 'parlays') S.parlays = await api('/parlays');
    if (t === 'follow') S.follow = await api('/follow');
    if (t === 'markets') await loadMarkets();
    if (t === 'trades') await loadTrades();
    if (t === 'settings') { S.settings = await api('/settings'); S.experts = await api('/experts'); }
    if (t === 'audit') await loadAudit();
  } catch (e) {
    toast('Could not load: ' + e.message, true);
  }
  render();
}
async function refreshStatus() {
  try { S.status = await api('/status'); renderTop(); } catch (e) {
    $('#fresh').innerHTML = `<span class="px-2 py-0.5 rounded-full border border-red-600 bg-red-950 text-red-200 font-semibold">APP SERVER UNREACHABLE · ${esc(e.message)}</span>`;
  }
}
function userIsTyping() {
  const a = document.activeElement;
  return a && $('#view').contains(a) && ['INPUT', 'SELECT', 'TEXTAREA'].includes(a.tagName);
}
window.addEventListener('hashchange', () => { S.tab = (location.hash || '#edges').slice(1); loadTab(); window.scrollTo(0, 0); });
(async function init() {
  S.tab = (location.hash || '#edges').slice(1);
  if (!VIEWS[S.tab]) S.tab = 'edges';
  renderNav();
  await refreshStatus();
  await loadTab();
  setInterval(refreshStatus, 30000);
  setInterval(() => { if (!userIsTyping() && !['settings'].includes(S.tab) && !document.querySelector('details[open] form:focus-within')) loadTab(); }, 60000);
})();
