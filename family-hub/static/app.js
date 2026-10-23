"use strict";
/* Family Hub web app: a dependency-free single-page app over the /api endpoints. */

// ------------------------------------------------------------------ state & helpers

const S = {
  token: localStore("fh_token"),
  joinCode: localStore("fh_join"),
  me: null, family: null, members: [], unread: 0,
  choreDay: ymd(new Date()),
  month: ymd(new Date()).slice(0, 7),
  authMode: "parent", pickedKid: null, kidList: null,
  map: null, watchId: null, lastSent: null,
};

function localStore(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { return null; }
}

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (c) => (c < 0 ? "−" : "") + "$" + (Math.abs(c) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const isParent = () => S.me?.role === "parent";
const member = (id) => S.members.find((m) => m.id === id);
const nameOf = (id) => member(id)?.name ?? "Everyone";

function ymd(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function parseYmd(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function addDays(s, n) { const d = parseYmd(s); d.setDate(d.getDate() + n); return ymd(d); }
function dayLabel(s) {
  const today = ymd(new Date());
  if (s === today) return "Today";
  if (s === addDays(today, 1)) return "Tomorrow";
  if (s === addDays(today, -1)) return "Yesterday";
  return parseYmd(s).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function eventDay(e) { return e.all_day ? e.start.slice(0, 10) : ymd(new Date(e.start)); }
function eventTime(e) {
  if (e.all_day) return "All day";
  return new Date(e.start).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function avatar(m, size) {
  if (!m) return `<span class="avatar" style="background:#888">👪</span>`;
  const st = size ? `width:${size}px;height:${size}px;` : "";
  return `<span class="avatar" style="${st}background:${esc(m.color)}" title="${esc(m.name)}">${esc(m.name[0] ?? "?")}</span>`;
}
function memberOptions(selected, { all = false, kidsOnly = false, none = "" } = {}) {
  const list = S.members.filter((m) => !kidsOnly || m.role === "child");
  return (none ? `<option value="">${esc(none)}</option>` : "") +
    (all ? `<option value="">Everyone</option>` : "") +
    list.map((m) => `<option value="${m.id}" ${m.id === selected ? "selected" : ""}>${esc(m.name)}</option>`).join("");
}

function toast(msg) {
  document.querySelector(".toast")?.remove();
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.append(t);
  setTimeout(() => t.remove(), 3200);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (S.token) opts.headers.Authorization = `Bearer ${S.token}`;
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const resp = await fetch(path, opts);
  if (resp.status === 401 && S.token) { signOut(false); throw new Error("Please sign in again"); }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join("; ") : d || resp.statusText);
  }
  return data;
}

function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === "checkbox") {
      if (el.dataset.group) { (out[el.dataset.group] ??= []); if (el.checked) out[el.dataset.group].push(isNaN(el.value) ? el.value : Number(el.value)); }
      else out[el.name] = el.checked;
    } else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
    else if (el.type === "file") out[el.name] = el.files[0];
    else out[el.name] = el.value.trim();
  }
  return out;
}
const toCents = (v) => Math.round(parseFloat(v || "0") * 100);
const intOrNull = (v) => (v === "" || v == null ? null : Number(v));

// ------------------------------------------------------------------ navigation

const NAV = [
  { id: "today", label: "Today", ico: "🏠", tab: true },
  { id: "chores", label: "Chores", ico: "🧹", tab: true },
  { id: "rewards", label: "Rewards", ico: "🎁" },
  { id: "calendar", label: "Calendar", ico: "📅", tab: true },
  { id: "homework", label: "Homework", ico: "📚" },
  { id: "milestones", label: "Milestones", ico: "⭐" },
  { id: "money", label: "Money", ico: "💵" },
  { id: "health", label: "Health", ico: "🩺" },
  { id: "map", label: "Map", ico: "📍", tab: true },
  { id: "home", label: "Doors", ico: "🔐", parent: true },
  { id: "alerts", label: "Alerts", ico: "🔔" },
  { id: "settings", label: "Settings", ico: "⚙️" },
  { id: "more", label: "More", ico: "☰", tab: true, mobileOnly: true },
];

function currentView() {
  const v = location.hash.replace(/^#/, "").split("?")[0] || "today";
  const item = NAV.find((n) => n.id === v);
  if (!item || (item.parent && !isParent())) return "today";
  return v;
}

function shell(content) {
  const v = currentView();
  const visible = NAV.filter((n) => !n.parent || isParent());
  const badge = (id) => (id === "alerts" && S.unread ? `<span class="badge">${S.unread}</span>` : "");
  return `<div class="app">
    <aside class="side">
      <div class="brand">Family <span>Hub</span></div>
      <nav class="nav">${visible.filter((n) => !n.mobileOnly).map((n) =>
        `<a href="#${n.id}" class="${n.id === v ? "on" : ""}"><span>${n.ico}</span>${n.label}${badge(n.id)}</a>`).join("")}</nav>
      <div style="padding:16px 12px" class="small muted">${esc(S.me.name)} · ${esc(S.family.name)}<br>
        <a href="#" data-act="signOut">Sign out</a></div>
    </aside>
    <main id="view">${content}</main>
    <nav class="tabbar">${visible.filter((n) => n.tab).map((n) => {
      const on = n.id === v || (n.id === "more" && !NAV.find((x) => x.id === v)?.tab);
      const more = n.id === "more" && S.unread ? `<span class="badge">${S.unread}</span>` : "";
      return `<a href="#${n.id}" class="${on ? "on" : ""}"><span class="ico">${n.ico}</span>${n.label}${more}</a>`;
    }).join("")}</nav>
  </div>`;
}

async function render() {
  if (!S.token) { $("#root").innerHTML = authView(); return; }
  try {
    if (!S.me) await loadSession();
    const v = currentView();
    const html = await VIEWS[v]();
    $("#root").innerHTML = shell(html);
    MOUNT[v]?.();
    window.scrollTo({ top: 0 });
  } catch (err) {
    if (!S.token) { $("#root").innerHTML = authView(); return; }
    $("#root").innerHTML = shell(`<div class="banner critical">${esc(err.message)}</div>`);
  }
}

async function refresh() {
  // Re-render the current view without jumping to the top.
  const y = window.scrollY;
  await render();
  window.scrollTo({ top: y });
}

async function loadSession() {
  const me = await api("GET", "/api/auth/me");
  S.me = me.member; S.family = me.family;
  S.members = await api("GET", "/api/members");
  S.unread = (await api("GET", "/api/alerts?unread_only=true")).length;
  if (localStore("fh_share") === "1") startSharing(false);
}

function signOut(callServer = true) {
  if (callServer && S.token) api("POST", "/api/auth/logout").catch(() => {});
  stopSharing();
  S.token = null; S.me = null;
  localStore("fh_token", null);
  location.hash = "";
  render();
}

// ------------------------------------------------------------------ auth screens

function authView() {
  const m = S.authMode;
  const seg = (id, label) => `<button class="${m === id ? "on" : ""}" data-act="authMode" data-mode="${id}">${label}</button>`;
  let body = "";
  if (m === "parent") {
    body = `<form class="stack" data-form="login">
      <label>Email<input name="email" type="email" autocomplete="email" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <button class="primary">Sign in</button></form>`;
  } else if (m === "kid") {
    if (!S.kidList) {
      body = `<form class="stack" data-form="joinCode">
        <label>Family code<input name="code" value="${esc(S.joinCode || "")}" placeholder="From a parent's Settings page" required autocapitalize="characters"></label>
        <button class="primary">Continue</button></form>`;
    } else {
      body = `<p class="muted">${esc(S.kidList.family_name)} — who's signing in?</p>
        <div class="kidpick">${S.kidList.members.map((k) => `<button data-act="pickKid" data-id="${k.id}" class="${S.pickedKid === k.id ? "on" : ""}">
          ${avatar(k)}<span>${esc(k.name)}</span></button>`).join("") || `<p class="muted">No one has a PIN yet. Ask a parent to add you.</p>`}</div>
        ${S.pickedKid ? `<form class="stack" data-form="pinLogin" style="margin-top:14px">
          <label>PIN<input name="pin" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="8" required autofocus></label>
          <button class="primary">Sign in</button></form>` : ""}
        <p><a href="#" data-act="forgetFamily" class="small">Use a different family code</a></p>`;
    }
  } else {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    body = `<form class="stack" data-form="signup">
      <label>Family name<input name="family_name" placeholder="The Rivera Family" required></label>
      <label>Your name<input name="parent_name" required></label>
      <label>Email<input name="email" type="email" autocomplete="email" required></label>
      <label>Password<input name="password" type="password" minlength="8" autocomplete="new-password" required></label>
      <input type="hidden" name="timezone" value="${esc(tz)}">
      <button class="primary">Create family</button>
      <p class="small muted">Time zone: ${esc(tz)}. You can add kids and another parent next.</p></form>`;
  }
  return `<div class="auth"><div class="brand">Family <span style="color:var(--brand)">Hub</span></div>
    <p class="muted" style="text-align:center">Chores, rewards, calendar, homework, money, health and home — together.</p>
    <div class="seg">${seg("parent", "Parent")}${seg("kid", "Kid")}${seg("signup", "New family")}</div>
    <div class="card">${body}</div></div>`;
}

async function afterAuth(res) {
  S.token = res.token;
  localStore("fh_token", res.token);
  S.me = null;
  location.hash = "#today";
  await render();
}

// ------------------------------------------------------------------ views

const VIEWS = {};
const MOUNT = {};

VIEWS.today = async () => {
  const d = await api("GET", "/api/dashboard");
  S.unread = d.unread_alerts;
  const hour = new Date().getHours();
  const hello = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const today = ymd(new Date());
  const crit = d.critical_alerts.map((a) => `<div class="banner critical">🚨 ${esc(a.title)}</div>`).join("");
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const waiting = isParent() ? [
    d.pending_approvals && `<a href="#chores">✅ ${plural(d.pending_approvals, "chore")} to approve</a>`,
    d.pending_rewards && `<a href="#rewards">🎁 ${plural(d.pending_rewards, "reward request")}</a>`,
  ].filter(Boolean) : [];
  const pending = waiting.length ? `<div class="banner info">Waiting for you: ${waiting.join(" · ")}</div>` : "";
  const locks = isParent() && d.locks.length ? `<h2>Doors</h2><div class="grid">${d.locks.map(lockCard).join("")}</div>` : "";
  const fam = isParent() ? `<h2>Family</h2><div class="grid">${d.members.map((m) => `<div class="card row">${avatar(m)}
      <div class="grow"><div class="title">${esc(m.name)}</div><div class="small muted">${m.role === "child" ? `⭐ ${m.points} pts · ${money(m.wallet_cents)}` : "Parent"}</div></div></div>`).join("")}</div>` : "";
  const evs = d.events.map((e) => `<div class="item"><div style="width:72px" class="small muted">${eventDay(e) === today ? "" : "Tmrw "}${eventTime(e)}</div>
      <div class="grow"><div class="title">${esc(e.title)}</div>${e.location ? `<div class="small muted">${esc(e.location)}</div>` : ""}</div>
      ${e.member_ids.map((id) => avatar(member(id), 26)).join("")}</div>`).join("");
  return `<div class="top"><div class="grow"><h1>${hello}, ${esc(S.me.name)}</h1>
      <div class="muted">${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</div></div>
      ${!isParent() ? `<span class="chip brand">⭐ ${d.me.points} points</span><span class="chip">${money(d.me.wallet_cents)}</span>` : ""}</div>
    ${crit}${pending}
    <h2>Today's chores</h2>${choreList(d.chores, "No chores today 🎉")}
    <h2>Coming up</h2><div class="list">${evs || `<div class="empty">Nothing on the calendar today or tomorrow.</div>`}</div>
    <h2>Homework due soon</h2>${homeworkList(d.homework)}
    ${locks}${fam}`;
};

function choreList(items, emptyMsg) {
  if (!items.length) return `<div class="list"><div class="empty">${emptyMsg}</div></div>`;
  return `<div class="list">${items.map((i) => {
    const icon = { approved: "✓", submitted: "…", rejected: "↺" }[i.status] || "";
    const cls = { approved: "on", submitted: "wait" }[i.status] || "";
    const mine = isParent() || i.assignee_id == null || i.assignee_id === S.me.id;
    const canTap = mine && (i.status === "todo" || i.status === "rejected");
    const review = isParent() && i.status === "submitted"
      ? `<button class="small primary" data-act="review" data-id="${i.id}" data-ok="1">Approve</button><button class="small" data-act="review" data-id="${i.id}" data-ok="0">Redo</button>` : "";
    const statusChip = i.status === "submitted" ? `<span class="chip warn">Waiting for OK</span>` : i.status === "rejected" ? `<span class="chip bad">Try again</span>` : "";
    return `<div class="item ${i.status === "approved" ? "done" : ""}">
      <button class="check ${cls}" ${canTap ? `data-act="complete" data-id="${i.id}"` : "disabled"} aria-label="Mark done">${icon}</button>
      <div class="grow"><div class="title">${esc(i.title)}</div><div class="small muted">${esc(nameOf(i.assignee_id))} · ⭐ ${i.points}</div></div>
      ${statusChip}${review}</div>`;
  }).join("")}</div>`;
}

function homeworkList(items) {
  if (!items.length) return `<div class="list"><div class="empty">All caught up 📚</div></div>`;
  const today = ymd(new Date());
  return `<div class="list">${items.map((h) => {
    const late = h.status !== "done" && h.due_date < today;
    return `<div class="item ${h.status === "done" ? "done" : ""}">
      <button class="check ${h.status === "done" ? "on" : h.status === "in_progress" ? "wait" : ""}" data-act="hwCycle" data-id="${h.id}" data-status="${h.status}" aria-label="Change status">${h.status === "done" ? "✓" : h.status === "in_progress" ? "…" : ""}</button>
      <div class="grow"><div class="title">${esc(h.subject)}: ${esc(h.title)}</div>
        <div class="small muted">${isParent() ? esc(nameOf(h.member_id)) + " · " : ""}Due ${dayLabel(h.due_date)}${h.points ? ` · ⭐ ${h.points}` : ""}</div>
        ${attachLinks(h.attachments)}</div>
      ${late ? `<span class="chip bad">Late</span>` : ""}
      <label class="btn small" title="Attach to Google Drive">📎<input type="file" hidden data-upload="homework" data-id="${h.id}"></label>
    </div>`;
  }).join("")}</div>`;
}

function attachLinks(list) {
  if (!list?.length) return "";
  return `<div class="small">${list.map((a) => `<a href="${esc(a.url)}" target="_blank" rel="noopener">📎 ${esc(a.name)}</a>`).join(" · ")}</div>`;
}

// ---------- chores

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

VIEWS.chores = async () => {
  const day = S.choreDay;
  const [items, defs] = await Promise.all([
    api("GET", `/api/chores/instances?start=${day}&end=${day}`),
    isParent() ? api("GET", "/api/chores") : Promise.resolve([]),
  ]);
  const mine = isParent() ? items : items.filter((i) => i.assignee_id == null || i.assignee_id === S.me.id);
  const manage = isParent() ? `<h2>All chores</h2><div class="list">${defs.map((c) => `<div class="item"><div class="grow">
      <div class="title">${esc(c.title)}</div><div class="small muted">${esc(nameOf(c.assignee_id))} · ⭐ ${c.points} · ${recurrenceLabel(c)}${c.requires_approval ? "" : " · auto-approve"}</div></div>
      <button class="small danger" data-act="delChore" data-id="${c.id}">Remove</button></div>`).join("") || `<div class="empty">No chores yet.</div>`}</div>
    <details class="add card"><summary>+ Add a chore</summary><form class="stack" data-form="addChore">
      <div class="fields"><label>Chore<input name="title" required placeholder="Unload dishwasher"></label>
      <label>Who<select name="assignee_id">${memberOptions(null, { all: true })}</select></label>
      <label>Points<input name="points" type="number" min="0" value="10"></label>
      <label>Repeats<select name="recurrence"><option value="once">Once</option><option value="daily">Daily</option><option value="weekly" selected>Weekly</option><option value="monthly">Monthly</option></select></label>
      <label>Starting<input name="start_date" type="date" value="${ymd(new Date())}"></label></div>
      <div class="weekdays">${WEEKDAYS.map((d, i) => `<label><input type="checkbox" name="wd${i}" data-group="weekdays" value="${i}">${d}</label>`).join("")}</div>
      <label class="inline"><input type="checkbox" name="requires_approval" checked> Parent approves before points are awarded</label>
      <button class="primary">Add chore</button></form></details>` : "";
  return `<div class="top"><h1 class="grow">Chores</h1>
      <button data-act="choreDay" data-delta="-1" aria-label="Previous day">‹</button>
      <strong>${dayLabel(day)}</strong>
      <button data-act="choreDay" data-delta="1" aria-label="Next day">›</button></div>
    ${choreList(mine, "Nothing for this day.")}${manage}`;
};

function recurrenceLabel(c) {
  if (c.recurrence === "weekly") return "Weekly " + (c.weekdays.length ? c.weekdays.map((d) => WEEKDAYS[d]).join(", ") : WEEKDAYS[(parseYmd(c.start_date).getDay() + 6) % 7]);
  return { once: `Once (${dayLabel(c.start_date)})`, daily: "Daily", monthly: `Monthly on day ${parseYmd(c.start_date).getDate()}` }[c.recurrence];
}

// ---------- rewards

VIEWS.rewards = async () => {
  const [rewards, reds] = await Promise.all([api("GET", "/api/rewards"), api("GET", "/api/redemptions")]);
  const pts = isParent() ? null : (await api("GET", `/api/points/${S.me.id}`));
  const pending = reds.filter((r) => r.status === "pending");
  const cards = rewards.map((r) => {
    const short = pts && pts.balance < r.cost;
    return `<div class="card"><div style="font-size:34px">${esc(r.icon)}</div><h3>${esc(r.title)}</h3>
      <div class="muted">⭐ ${r.cost} points</div><div class="row" style="margin-top:10px">
      ${isParent() ? `<button class="small danger" data-act="delReward" data-id="${r.id}">Remove</button>`
        : `<button class="primary small" data-act="redeem" data-id="${r.id}" ${short ? "disabled" : ""}>${short ? `${r.cost - pts.balance} more to go` : "Get it!"}</button>`}</div></div>`;
  }).join("");
  const requests = pending.length ? `<h2>Requests</h2><div class="list">${pending.map((r) => `<div class="item">${avatar(member(r.member_id))}
      <div class="grow"><div class="title">${esc(r.icon)} ${esc(r.title)}</div><div class="small muted">${esc(nameOf(r.member_id))} · ⭐ ${r.cost} · ${ago(r.created_at)}</div></div>
      ${isParent() ? `<button class="small primary" data-act="decide" data-id="${r.id}" data-ok="1">Give it</button><button class="small" data-act="decide" data-id="${r.id}" data-ok="0">Not now</button>` : `<span class="chip warn">Waiting</span>`}</div>`).join("")}</div>` : "";
  const kidsPts = isParent() ? `<h2>Points</h2><div class="grid">${S.members.filter((m) => m.role === "child").map((m) => `<div class="card row">${avatar(m)}<div class="grow"><div class="title">${esc(m.name)}</div><div class="stat">⭐ ${m.points}</div></div></div>`).join("")}</div>
    <details class="add card"><summary>± Give or take points</summary><form class="stack" data-form="adjust"><div class="fields">
      <label>Kid<select name="member_id">${memberOptions(null, { kidsOnly: true })}</select></label>
      <label>Points (negative to take)<input name="amount" type="number" required value="5"></label>
      <label>Why<input name="reason" required placeholder="Helped a sibling"></label></div><button class="primary">Save</button></form></details>
    <details class="add card"><summary>+ Add a reward</summary><form class="stack" data-form="addReward"><div class="fields">
      <label>Reward<input name="title" required placeholder="Pick Friday's movie"></label>
      <label>Cost (points)<input name="cost" type="number" min="1" value="50" required></label>
      <label>Emoji<input name="icon" value="🎁" maxlength="4"></label></div><button class="primary">Add reward</button></form></details>` : "";
  const history = pts ? `<h2>My points history</h2><div class="list">${pts.entries.slice(0, 20).map((e) => `<div class="item"><div class="grow">${esc(e.reason)}<div class="small muted">${ago(e.created_at)}</div></div><strong class="${e.amount >= 0 ? "" : "muted"}">${e.amount > 0 ? "+" : ""}${e.amount}</strong></div>`).join("") || `<div class="empty">Finish chores to earn points!</div>`}</div>` : "";
  return `<div class="top"><h1 class="grow">Rewards</h1>${pts ? `<span class="chip brand">⭐ ${pts.balance} points</span>` : ""}</div>
    ${requests}<h2>Reward shop</h2><div class="grid">${cards || `<div class="card muted">No rewards yet${isParent() ? " — add some below" : ""}.</div>`}</div>
    ${kidsPts}${history}`;
};

// ---------- calendar

VIEWS.calendar = async () => {
  const start = new Date(); start.setHours(0, 0, 0, 0);
  const end = new Date(start); end.setDate(end.getDate() + 31);
  const events = await api("GET", `/api/events?start=${start.toISOString()}&end=${end.toISOString()}`);
  const integ = isParent() ? await api("GET", "/api/integrations").catch(() => null) : null;
  const byDay = {};
  for (const e of events) (byDay[eventDay(e)] ??= []).push(e);
  const src = { google: `<span class="chip">Google</span>`, icloud: `<span class="chip">iCloud</span>`, local: "" };
  const days = Object.keys(byDay).sort().map((d) => `<h2>${dayLabel(d)}</h2><div class="list">${byDay[d].map((e) => `<div class="item">
      <div style="width:70px" class="small muted">${eventTime(e)}</div>
      <div class="grow"><div class="title">${esc(e.title)}</div>${e.location ? `<div class="small muted">📍 ${esc(e.location)}</div>` : ""}</div>
      ${e.member_ids.map((id) => avatar(member(id), 26)).join("")}${src[e.source] || ""}
      ${e.pushed.map((p) => `<span class="chip good" title="Copied to ${p}">→ ${p}</span>`).join("")}
      ${isParent() && e.source === "local" ? `<button class="small ghost" data-act="delEvent" data-id="${e.id}" aria-label="Delete">✕</button>` : ""}</div>`).join("")}</div>`).join("");
  const targets = [integ?.google && "google", integ?.icloud && "icloud"].filter(Boolean);
  return `<div class="top"><h1 class="grow">Calendar</h1>${isParent() && targets.length ? `<button data-act="syncCal">⟳ Sync now</button>` : ""}</div>
    ${isParent() && integ && !targets.length ? `<div class="banner info">Connect Google Calendar or iCloud in <a href="#settings">Settings</a> to see everyone's calendars here.</div>` : ""}
    <details class="add card"><summary>+ Add an event</summary><form class="stack" data-form="addEvent"><div class="fields">
      <label>What<input name="title" required placeholder="Soccer practice"></label>
      <label>Starts<input name="start" type="datetime-local" required></label>
      <label>Ends<input name="end" type="datetime-local"></label>
      <label>Where<input name="location"></label></div>
      <label class="inline"><input type="checkbox" name="all_day"> All day</label>
      <div class="weekdays">${S.members.map((m) => `<label><input type="checkbox" name="m${m.id}" data-group="member_ids" value="${m.id}">${esc(m.name)}</label>`).join("")}</div>
      ${targets.length ? `<div class="weekdays">${targets.map((t) => `<label><input type="checkbox" name="p${t}" data-group="push_to" value="${t}" checked>Add to ${t === "google" ? "Google" : "iCloud"}</label>`).join("")}</div>` : ""}
      <button class="primary">Add event</button></form></details>
    ${days || `<div class="card muted" style="margin-top:12px">No events in the next month.</div>`}`;
};

// ---------- homework

VIEWS.homework = async () => {
  const showDone = localStore("fh_hw_done") === "1";
  const items = await api("GET", `/api/homework?include_done=${showDone}`);
  return `<div class="top"><h1 class="grow">Homework</h1>
      <label class="inline small"><input type="checkbox" data-act="toggleHwDone" ${showDone ? "checked" : ""}> Show finished</label></div>
    ${homeworkList(items)}
    <details class="add card"><summary>+ Add homework</summary><form class="stack" data-form="addHomework"><div class="fields">
      ${isParent() ? `<label>For<select name="member_id">${memberOptions(null, { kidsOnly: true })}</select></label>` : ""}
      <label>Subject<input name="subject" required placeholder="Math" list="subjects"></label>
      <label>Assignment<input name="title" required placeholder="Worksheet p. 42"></label>
      <label>Due<input name="due_date" type="date" value="${addDays(ymd(new Date()), 1)}" required></label>
      ${isParent() ? `<label>Bonus points<input name="points" type="number" min="0" value="0"></label>` : ""}</div>
      <datalist id="subjects">${["Math", "Reading", "Science", "Spanish", "History", "Writing", "Art", "Music"].map((s) => `<option value="${s}">`).join("")}</datalist>
      <button class="primary">Add</button></form></details>
    <p class="small muted">Tap the circle to move between to-do → working on it → done. 📎 attaches a photo or file to Google Drive.</p>`;
};

// ---------- milestones

VIEWS.milestones = async () => {
  const items = await api("GET", "/api/milestones");
  return `<div class="top"><h1 class="grow">Milestones</h1></div>
    <details class="add card"><summary>+ Record a milestone</summary><form class="stack" data-form="addMilestone"><div class="fields">
      <label>What happened<input name="title" required placeholder="First bike ride without training wheels"></label>
      <label>Who<select name="member_id">${memberOptions(null, { all: true })}</select></label>
      <label>When<input name="date" type="date" value="${ymd(new Date())}" required></label>
      <label>Type<select name="category">${["first", "school", "sports", "health", "growth", "achievement", "family", "general"].map((c) => `<option>${c}</option>`).join("")}</select></label></div>
      <label>Notes<textarea name="notes"></textarea></label><button class="primary">Save</button></form></details>
    <div class="timeline" style="margin-top:18px">${items.map((m) => `<div class="card"><div class="row">
      ${avatar(member(m.member_id), 28)}<div class="grow"><h3>${esc(m.title)}</h3>
      <div class="small muted">${parseYmd(m.date).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })} · ${esc(m.category)}</div></div>
      <label class="btn small" title="Add a photo (Google Drive)">📷<input type="file" accept="image/*,video/*,application/pdf" hidden data-upload="milestones" data-id="${m.id}"></label>
      ${isParent() ? `<button class="small ghost" data-act="delMilestone" data-id="${m.id}" aria-label="Delete">✕</button>` : ""}</div>
      ${m.notes ? `<p>${esc(m.notes)}</p>` : ""}${attachLinks(m.attachments)}</div>`).join("") || `<div class="card muted">Firsts, wins and big days go here.</div>`}</div>`;
};

// ---------- money

VIEWS.money = async () => {
  if (!isParent()) {
    const w = await api("GET", `/api/finance/wallet/${S.me.id}`);
    return `<div class="top"><h1 class="grow">My money</h1></div>
      <div class="card"><div class="muted">In my wallet</div><div class="stat">${money(w.balance_cents)}</div>
      ${S.me.weekly_allowance_cents ? `<div class="small muted">Allowance: ${money(S.me.weekly_allowance_cents)} / week</div>` : ""}</div>
      <details class="add card"><summary>− I spent some money</summary><form class="stack" data-form="kidSpend"><div class="fields">
        <label>Amount ($)<input name="amount" type="number" step="0.01" min="0.01" required></label>
        <label>On what<input name="description" required placeholder="Comic book"></label></div><button class="primary">Save</button></form></details>
      <h2>History</h2>${txList(w.transactions)}`;
  }
  const [s, txs, budgets] = await Promise.all([
    api("GET", `/api/finance/summary?month=${S.month}`),
    api("GET", `/api/finance/transactions?month=${S.month}`),
    api("GET", "/api/finance/budgets"),
  ]);
  const monthName = parseYmd(S.month + "-01").toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const cats = s.categories.map((c) => {
    const pct = c.budget_cents ? Math.min(100, (100 * c.spent_cents) / c.budget_cents) : 0;
    return `<div class="item"><div class="grow"><div class="row"><span class="title grow">${esc(c.category)}</span>
      <span class="small">${money(c.spent_cents)}${c.budget_cents ? ` of ${money(c.budget_cents)}` : ""}</span></div>
      ${c.budget_cents ? `<div class="bar ${c.spent_cents > c.budget_cents ? "over" : ""}"><div style="width:${pct}%"></div></div>` : ""}</div></div>`;
  }).join("");
  return `<div class="top"><h1 class="grow">Money</h1>
      <button data-act="month" data-delta="-1" aria-label="Previous month">‹</button><strong>${monthName}</strong><button data-act="month" data-delta="1" aria-label="Next month">›</button></div>
    <div class="grid"><div class="card"><div class="muted">Income</div><div class="stat good">${money(s.income_cents)}</div></div>
      <div class="card"><div class="muted">Spent</div><div class="stat">${money(s.expense_cents)}</div></div>
      <div class="card"><div class="muted">Left over</div><div class="stat ${s.net_cents < 0 ? "bad" : ""}">${money(s.net_cents)}</div></div></div>
    <details class="add card"><summary>+ Add income or expense</summary><form class="stack" data-form="addTx"><div class="fields">
      <label>Type<select name="kind"><option value="out">Expense</option><option value="in">Income</option></select></label>
      <label>Amount ($)<input name="amount" type="number" step="0.01" min="0.01" required></label>
      <label>Category<input name="category" list="cats" required placeholder="groceries"></label>
      <label>Note<input name="description"></label>
      <label>Date<input name="date" type="date" value="${ymd(new Date())}"></label>
      <label>Kid's wallet<select name="member_id">${memberOptions(null, { kidsOnly: true, none: "— Household —" })}</select></label></div>
      <datalist id="cats">${[...new Set([...budgets.map((b) => b.category), "groceries", "dining", "utilities", "rent", "kids", "gas", "health", "fun", "salary"])].map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
      <button class="primary">Save</button></form></details>
    <h2>Spending by category</h2><div class="list">${cats || `<div class="empty">No spending recorded this month.</div>`}</div>
    <details class="add card"><summary>⚖ Set a monthly budget</summary><form class="stack" data-form="setBudget"><div class="fields">
      <label>Category<input name="category" list="cats" required></label>
      <label>Monthly limit ($)<input name="limit" type="number" step="1" min="1" required></label></div>
      <button class="primary">Save budget</button><p class="small muted">You'll get an alert at 80% and when a category goes over.</p></form></details>
    <h2>Kids' wallets</h2><div class="grid">${s.wallets.map((w) => `<div class="card row">${avatar(member(w.member_id))}<div class="grow"><div class="title">${esc(w.name)}</div>
      <div class="stat">${money(w.balance_cents)}</div><div class="small muted">${w.weekly_allowance_cents ? money(w.weekly_allowance_cents) + "/week allowance" : "No allowance set"}</div></div></div>`).join("") || `<div class="card muted">Add kids in Settings.</div>`}</div>
    <h2>Transactions</h2>${txList(txs, true)}`;
};

function txList(txs, canDelete = false) {
  if (!txs.length) return `<div class="list"><div class="empty">Nothing yet.</div></div>`;
  return `<div class="list">${txs.map((t) => `<div class="item"><div class="grow"><div class="title">${esc(t.description || t.category)}</div>
    <div class="small muted">${dayLabel(t.date)} · ${esc(t.category)}${t.member_id ? " · " + esc(nameOf(t.member_id)) + "'s wallet" : ""}</div></div>
    <strong style="color:${t.amount_cents > 0 ? "var(--good)" : "var(--text)"}">${t.amount_cents > 0 ? "+" : ""}${money(t.amount_cents)}</strong>
    ${canDelete ? `<button class="small ghost" data-act="delTx" data-id="${t.id}" aria-label="Delete">✕</button>` : ""}</div>`).join("")}</div>`;
}

// ---------- health

const HEALTH_KINDS = { appointment: "🗓 Appointments", medication: "💊 Medications", allergy: "⚠️ Allergies", immunization: "💉 Immunizations", vital: "📏 Growth & vitals", note: "📝 Notes" };

VIEWS.health = async () => {
  const who = isParent() ? intOrNull(localStore("fh_health_member")) : S.me.id;
  const recs = await api("GET", `/api/health${who ? `?member_id=${who}` : ""}`);
  const groups = Object.entries(HEALTH_KINDS).map(([k, label]) => {
    const rows = recs.filter((r) => r.kind === k);
    if (!rows.length) return "";
    return `<h2>${label}</h2><div class="list">${rows.map((r) => `<div class="item">${avatar(member(r.member_id), 28)}<div class="grow">
      <div class="title">${esc(r.title)}${r.value ? ` — ${esc(r.value)} ${esc(r.unit)}` : ""}</div>
      <div class="small muted">${r.date ? dayLabel(r.date) : ""}${r.next_due ? ` · next due ${dayLabel(r.next_due)}` : ""}${r.notes ? " · " + esc(r.notes) : ""}</div>${attachLinks(r.attachments)}</div>
      ${isParent() ? `<label class="btn small" title="Attach a document (Google Drive)">📎<input type="file" hidden data-upload="health" data-id="${r.id}"></label>
      <button class="small ghost" data-act="delHealth" data-id="${r.id}" aria-label="Delete">✕</button>` : ""}</div>`).join("")}</div>`;
  }).join("");
  return `<div class="top"><h1 class="grow">Health</h1>
      ${isParent() ? `<select data-act="healthMember" style="width:auto">${memberOptions(who, { all: true })}</select>` : ""}</div>
    ${isParent() ? `<details class="add card"><summary>+ Add a record</summary><form class="stack" data-form="addHealth"><div class="fields">
      <label>Who<select name="member_id">${memberOptions(who ?? S.members[0]?.id)}</select></label>
      <label>Type<select name="kind">${Object.entries(HEALTH_KINDS).map(([k, l]) => `<option value="${k}">${l.slice(2).trim()}</option>`).join("")}</select></label>
      <label>Title<input name="title" required placeholder="Annual checkup / Amoxicillin / Height"></label>
      <label>Date<input name="date" type="date" value="${ymd(new Date())}"></label>
      <label>Value<input name="value" placeholder="e.g. 250 or 4'2&quot;"></label>
      <label>Unit<input name="unit" placeholder="mg, in, lb"></label>
      <label>Next due<input name="next_due" type="date"></label></div>
      <label>Notes<textarea name="notes" placeholder="Doctor, dosage schedule, reactions…"></textarea></label>
      <button class="primary">Save</button><p class="small muted">Records with a "next due" date show up in the daily digest a week ahead.</p></form></details>` : ""}
    ${groups || `<div class="card muted" style="margin-top:12px">No health records yet.</div>`}`;
};

// ---------- map

VIEWS.map = async () => {
  const [people, fences] = await Promise.all([api("GET", "/api/location/latest"), api("GET", "/api/geofences")]);
  S._mapData = { people, fences };
  const sharing = S.watchId != null;
  return `<div class="top"><h1 class="grow">Where is everyone</h1>
      <button class="${sharing ? "" : "primary"}" data-act="toggleShare">${sharing ? "Stop sharing" : "📍 Share my location"}</button></div>
    <div id="map"></div>
    <div class="list" style="margin-top:12px">${people.map((p) => `<div class="item">${avatar(member(p.member_id))}<div class="grow">
      <div class="title">${esc(p.name)} ${p.places.map((x) => `<span class="chip good">${esc(x)}</span>`).join("")}</div>
      <div class="small muted">${!p.share_location ? "Location sharing off" : p.last ? `Updated ${ago(p.last.at)}${p.last.battery != null ? ` · 🔋 ${Math.round(p.last.battery)}%` : ""}` : "No location yet"}</div></div>
      ${p.last ? `<button class="small" data-act="focus" data-lat="${p.last.lat}" data-lng="${p.last.lng}">Show</button>` : ""}</div>`).join("")}</div>
    ${isParent() ? `<h2>Places</h2><div class="list">${fences.map((f) => `<div class="item"><div class="grow"><div class="title">${esc(f.name)}</div>
      <div class="small muted">${Math.round(f.radius_m)} m radius · alerts on ${[f.notify_enter && "arrive", f.notify_leave && "leave"].filter(Boolean).join(" & ") || "nothing"}</div></div>
      <button class="small danger" data-act="delFence" data-id="${f.id}">Remove</button></div>`).join("") || `<div class="empty">Add Home, School, Grandma's… Name one “Home” to get “nobody's home and the door is unlocked” alerts.</div>`}</div>
    <details class="add card" id="fenceForm"><summary>+ Add a place (or tap the map)</summary><form class="stack" data-form="addFence"><div class="fields">
      <label>Name<input name="name" required placeholder="Home"></label>
      <label>Latitude<input name="lat" type="number" step="any" required></label>
      <label>Longitude<input name="lng" type="number" step="any" required></label>
      <label>Radius (m)<input name="radius_m" type="number" min="25" value="150"></label></div>
      <label class="inline"><input type="checkbox" name="notify_enter" checked> Alert on arrive</label>
      <label class="inline"><input type="checkbox" name="notify_leave" checked> Alert on leave</label>
      <button class="primary">Save place</button></form></details>` : ""}
    <p class="small muted">The web app shares location while it's open. For background tracking, use the OwnTracks app pointed at your location webhook (Settings → Webhooks).</p>`;
};

MOUNT.map = () => {
  if (!window.L) { $("#map").innerHTML = `<div class="empty">Map library didn't load. Check your connection.</div>`; return; }
  const { people, fences } = S._mapData;
  const map = L.map("map");
  S.map = map;
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(map);
  const pts = [];
  for (const f of fences) {
    L.circle([f.lat, f.lng], { radius: f.radius_m, color: "#3F6FE0", weight: 1, fillOpacity: 0.08 }).addTo(map).bindTooltip(f.name);
    pts.push([f.lat, f.lng]);
  }
  for (const p of people) {
    if (!p.last) continue;
    const icon = L.divIcon({ className: "", html: avatar(member(p.member_id)), iconSize: [34, 34], iconAnchor: [17, 17] });
    L.marker([p.last.lat, p.last.lng], { icon }).addTo(map).bindPopup(`<b>${esc(p.name)}</b><br>${ago(p.last.at)}`);
    pts.push([p.last.lat, p.last.lng]);
  }
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 }); else map.setView([39.5, -98.35], 4);
  if (isParent()) {
    map.on("click", (e) => {
      const form = $("#fenceForm");
      form.open = true;
      form.querySelector("[name=lat]").value = e.latlng.lat.toFixed(6);
      form.querySelector("[name=lng]").value = e.latlng.lng.toFixed(6);
      form.querySelector("[name=name]").focus();
    });
  }
};

function startSharing(announce = true) {
  if (!navigator.geolocation || S.watchId != null) return;
  S.watchId = navigator.geolocation.watchPosition(async (pos) => {
    const { latitude: lat, longitude: lng, accuracy } = pos.coords;
    const now = Date.now();
    const last = S.lastSent;
    // Send at most every 2 minutes unless we've moved noticeably.
    if (last && now - last.t < 120000 && Math.hypot(lat - last.lat, lng - last.lng) < 0.0008) return;
    S.lastSent = { t: now, lat, lng };
    let battery = null;
    try { battery = navigator.getBattery ? Math.round((await navigator.getBattery()).level * 100) : null; } catch { /* unsupported */ }
    api("POST", "/api/location", { lat, lng, accuracy_m: accuracy, battery })
      .then((r) => {
        r.transitions.forEach((t) => toast(`You ${t}`));
        if (!last && currentView() === "map") refresh();  // show your own dot as soon as the first fix lands
      }).catch(() => {});
  }, (err) => { toast(`Location: ${err.message}`); stopSharing(); }, { enableHighAccuracy: true, maximumAge: 60000 });
  localStore("fh_share", "1");
  if (announce) toast("Sharing your location while the app is open");
}

function stopSharing() {
  if (S.watchId != null) navigator.geolocation.clearWatch(S.watchId);
  S.watchId = null;
  localStore("fh_share", null);
}

// ---------- doors

function lockCard(l) {
  const label = { locked: "🔒 Locked", unlocked: "🔓 Unlocked", jammed: "⚠️ Jammed", unknown: "❔ Unknown" }[l.state];
  return `<div class="card lock ${l.state}"><div class="row"><h3 class="grow">${esc(l.name)}</h3>
      ${l.battery != null ? `<span class="chip ${l.battery <= 20 ? "bad" : ""}">🔋 ${l.battery}%</span>` : ""}</div>
    <div class="state">${label}</div>
    <div class="small muted">${l.door !== "unknown" ? `Door ${l.door} · ` : ""}since ${ago(l.state_changed_at)}</div>
    ${l.can_control ? `<div class="row">${l.state !== "locked" ? `<button class="primary" data-act="lockCmd" data-id="${l.id}" data-action="lock">Lock</button>` : ""}
      ${l.state !== "unlocked" ? `<button data-act="lockCmd" data-id="${l.id}" data-action="unlock">Unlock</button>` : ""}</div>`
      : `<div class="small muted">Status only — lock/unlock in the Philips app.</div>`}</div>`;
}

VIEWS.home = async () => {
  const [locks, fam] = await Promise.all([api("GET", "/api/locks"), api("GET", "/api/family")]);
  const logs = await Promise.all(locks.map((l) => api("GET", `/api/locks/${l.id}/events`)));
  const st = fam.settings;
  const recent = locks.flatMap((l, i) => logs[i].map((e) => ({ ...e, lock: l.name }))).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 25);
  const evLabel = { locked: "🔒 Locked", unlocked: "🔓 Unlocked", jammed: "⚠️ Jammed", door_open: "🚪 Opened", door_closed: "🚪 Closed", battery_low: "🪫 Battery low" };
  return `<div class="top"><h1 class="grow">Doors & locks</h1></div>
    <div class="grid">${locks.map(lockCard).join("") || `<div class="card muted">No locks yet. Add your Philips deadbolt below.</div>`}</div>
    <h2>Activity</h2><div class="list">${recent.map((e) => `<div class="item"><div class="grow"><div class="title">${evLabel[e.event] || esc(e.event)} · ${esc(e.lock)}</div>
      <div class="small muted">${ago(e.at)}${e.actor ? " · " + esc(e.actor) : ""}${e.source ? " · via " + esc(e.source) : ""}</div></div></div>`).join("") || `<div class="empty">No activity yet.</div>`}</div>
    <h2>Alert rules</h2><form class="card stack" data-form="doorRules"><div class="fields">
      <label>Alert if unlocked longer than (min)<input name="unlocked_minutes" type="number" min="1" value="${st.unlocked_minutes ?? 10}"></label>
      <label>Alert if door open longer than (min)<input name="door_open_minutes" type="number" min="1" value="${st.door_open_minutes ?? 5}"></label>
      <label>Quiet hours start<input name="quiet_start" type="time" value="${esc(st.quiet_start ?? "22:00")}"></label>
      <label>Quiet hours end<input name="quiet_end" type="time" value="${esc(st.quiet_end ?? "06:00")}"></label>
      <label>Low battery at (%)<input name="battery_low_percent" type="number" min="1" max="99" value="${st.battery_low_percent ?? 20}"></label></div>
      <label class="inline"><input type="checkbox" name="notify_all_unlocks" ${st.notify_all_unlocks ? "checked" : ""}> Tell me about every unlock (not just quiet hours)</label>
      <button class="primary">Save rules</button>
      <p class="small muted">Always on: jammed bolt (critical), unlock/open during quiet hours, low battery, and “nobody's home but the door is unlocked” (needs a place named Home on the Map).</p></form>
    <details class="add card"><summary>+ Add a lock</summary><form class="stack" data-form="addLock"><div class="fields">
      <label>Name<input name="name" required value="Front Door"></label>
      <label>Connected through<select name="provider">
        <option value="webhook">Webhook (Philips → Alexa/Google Home/IFTTT/HA automation)</option>
        <option value="homeassistant">Home Assistant (status + lock/unlock)</option>
        <option value="simulated">Demo lock (try the app)</option></select></label>
      <label>Lock ID<input name="external_id" required placeholder="lock.front_door or any short name"></label></div>
      <button class="primary">Add lock</button></form>
      <div class="small muted" style="margin-top:10px">Webhook URL for your automations:<div class="copy">${esc(fam.webhooks.lock)}</div>
      POST JSON like <code>{"lock": "lock.front_door", "event": "unlocked", "actor": "Keypad", "battery": 80}</code>.
      Events: locked, unlocked, jammed, door_open, door_closed, battery_low.</div></details>
    ${locks.length ? `<details class="add"><summary>Remove a lock</summary>${locks.map((l) => `<button class="small danger" data-act="delLock" data-id="${l.id}">Remove ${esc(l.name)}</button> `).join("")}</details>` : ""}`;
};

// ---------- alerts

VIEWS.alerts = async () => {
  const alerts = await api("GET", "/api/alerts");
  const sev = { critical: "bad", warning: "warn", info: "brand" };
  const ico = { door: "🔐", location: "📍", chore: "🧹", reward: "🎁", homework: "📚", health: "🩺", finance: "💵", digest: "🗒", system: "🏠" };
  return `<div class="top"><h1 class="grow">Alerts</h1>${alerts.some((a) => !a.read) ? `<button data-act="readAll">Mark all read</button>` : ""}</div>
    <div class="list">${alerts.map((a) => `<div class="item" style="${a.read ? "opacity:.65" : ""}"><div style="font-size:22px">${ico[a.kind] || "🔔"}</div>
      <div class="grow"><div class="title">${esc(a.title)}</div>${a.body ? `<div class="small" style="white-space:pre-line">${esc(a.body)}</div>` : ""}
      <div class="small muted">${ago(a.created_at)}${Object.values(a.delivery).some((d) => d !== "sent") ? " · ⚠ some channels failed" : ""}</div></div>
      ${a.severity !== "info" ? `<span class="chip ${sev[a.severity]}">${a.severity}</span>` : ""}
      ${!a.read ? `<button class="small ghost" data-act="read" data-id="${a.id}" aria-label="Mark read">✓</button>` : ""}</div>`).join("") || `<div class="empty">No alerts. All quiet 🌙</div>`}</div>`;
};

// ---------- "More" (phone tab bar overflow)

VIEWS.more = async () => {
  const items = NAV.filter((n) => !n.tab && (!n.parent || isParent()));
  return `<div class="top"><h1 class="grow">More</h1></div>
    <div class="grid">${items.map((n) => `<a class="card row" href="#${n.id}" style="text-decoration:none;color:inherit">
      <span style="font-size:24px">${n.ico}</span><strong class="grow">${n.label}</strong>${n.id === "alerts" && S.unread ? `<span class="badge">${S.unread}</span>` : ""}</a>`).join("")}</div>`;
};

// ---------- settings

const CHANNEL_FORMS = {
  ntfy: { label: "Push (ntfy app — iPhone & Android)", fields: [["topic", "Topic (make it hard to guess)"], ["server", "Server (blank = ntfy.sh)"], ["token", "Access token (optional)"]] },
  twilio_sms: { label: "Text message (SMS via Twilio — Google Messages & iMessage)", fields: [["account_sid", "Account SID"], ["auth_token", "Auth token"], ["from_number", "From number (+1…)"], ["to_numbers", "To numbers, comma separated"]] },
  gmail: { label: "Email (via connected Gmail)", fields: [["to", "Send to (comma separated)"]] },
  google_chat: { label: "Google Chat space (webhook)", fields: [["webhook_url", "Space webhook URL"]] },
  webhook: { label: "Webhook (Home Assistant, IFTTT, Pushcut…)", fields: [["url", "URL"]] },
};

VIEWS.settings = async () => {
  const kidOnly = `<div class="top"><h1 class="grow">Settings</h1></div>
    <div class="card stack"><label>My color<input type="color" data-act="myColor" value="${esc(S.me.color)}"></label>
    <label class="inline"><input type="checkbox" data-act="myShare" ${S.me.share_location ? "checked" : ""}> Share my location with my parents</label>
    <button data-act="signOut">Sign out</button></div>`;
  if (!isParent()) return kidOnly;
  const [fam, channels, integ] = await Promise.all([api("GET", "/api/family"), api("GET", "/api/notify/channels"), api("GET", "/api/integrations")]);
  const flash = new URLSearchParams(location.hash.split("?")[1] || "").get("google");
  const kind = localStore("fh_ch_kind") || "ntfy";
  const g = integ.google, ic = integ.icloud, ha = integ.homeassistant;
  const status = (i) => i ? `<span class="chip good">Connected${i.account ? " · " + esc(i.account) : ""}</span>${i.last_error ? `<div class="small" style="color:var(--bad)">${esc(i.last_error)}</div>` : i.last_sync_at ? `<div class="small muted">Synced ${ago(i.last_sync_at)}</div>` : ""}` : `<span class="chip">Not connected</span>`;
  return `<div class="top"><h1 class="grow">Settings</h1></div>
    ${flash ? `<div class="banner ${flash === "connected" ? "info" : "critical"}">Google: ${esc(flash)}</div>` : ""}
    <h2>Family members</h2><div class="list">${S.members.map((m) => `<div class="item">${avatar(m)}<div class="grow"><div class="title">${esc(m.name)}</div>
      <div class="small muted">${m.role === "parent" ? esc(m.email || "Parent") : `Kid · ${m.has_pin ? "PIN set" : "no PIN"} · ${money(m.weekly_allowance_cents)}/wk`}</div></div>
      ${m.role === "child" ? `<button class="small" data-act="editKid" data-id="${m.id}">Edit</button>` : ""}
      ${m.id !== S.me.id ? `<button class="small danger" data-act="delMember" data-id="${m.id}">Remove</button>` : ""}</div>`).join("")}</div>
    <details class="add card" id="memberForm"><summary>+ Add a family member</summary><form class="stack" data-form="addMember"><div class="fields">
      <label>Name<input name="name" required></label>
      <label>Role<select name="role"><option value="child">Kid</option><option value="parent">Parent</option></select></label>
      <label>PIN (kids, 4–8 digits)<input name="pin" inputmode="numeric" pattern="[0-9]{4,8}"></label>
      <label>Email (parents)<input name="email" type="email"></label>
      <label>Password (parents)<input name="password" type="password" minlength="8"></label>
      <label>Weekly allowance ($)<input name="allowance" type="number" step="0.25" min="0" value="0"></label>
      <label>Birthday<input name="birthday" type="date"></label>
      <label>Color<input name="color" type="color" value="#E07A3F"></label></div>
      <button class="primary">Add</button></form></details>
    <div class="card" style="margin-top:10px">Kids sign in on their own phone or tablet with the family code
      <strong class="copy" style="font-size:15px">${esc(fam.join_code)}</strong> and their PIN.</div>

    <h2>Connected accounts</h2><div class="grid two">
      <div class="card stack"><h3>Google</h3><div class="small muted">Calendar sync, Drive for homework/milestone/health files, Gmail for alert emails.</div>${status(g)}
        ${g ? `<label>Calendar ID<input data-act="gcalId" value="${esc(g.options.calendar_id || "primary")}"></label>
          <button class="small danger" data-act="disconnect" data-provider="google">Disconnect</button>`
          : integ.google_configured ? `<button class="primary" data-act="connectGoogle">Connect Google</button>`
          : `<div class="small" style="color:var(--warn)">The server needs GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first (see README).</div>`}</div>
      <div class="card stack"><h3>iCloud Calendar (iPhone)</h3><div class="small muted">Uses an <a href="https://support.apple.com/en-us/102654" target="_blank" rel="noopener">app-specific password</a>, not your Apple ID password.</div>${status(ic)}
        ${ic ? `<button class="small danger" data-act="disconnect" data-provider="icloud">Disconnect</button>` : `<form class="stack" data-form="icloud">
          <label>Apple ID<input name="username" type="email" required></label>
          <label>App-specific password<input name="password" required placeholder="xxxx-xxxx-xxxx-xxxx"></label>
          <label>Calendar name (optional)<input name="calendar_name" placeholder="Family"></label><button class="primary">Connect iCloud</button></form>`}</div>
      <div class="card stack"><h3>Home Assistant</h3><div class="small muted">Lets the hub see and control a smart lock that Home Assistant can reach.</div>${status(ha)}
        ${ha ? `<button class="small danger" data-act="disconnect" data-provider="homeassistant">Disconnect</button>` : `<form class="stack" data-form="homeassistant">
          <label>URL<input name="url" required placeholder="https://xxxx.ui.nabu.casa"></label>
          <label>Long-lived token<input name="token" required></label><button class="primary">Connect</button></form>`}</div></div>

    <h2>Where alerts go</h2><div class="list">${channels.map((c) => `<div class="item"><div class="grow"><div class="title">${esc(c.label)}</div>
      <div class="small muted">${esc(CHANNEL_FORMS[c.kind]?.label || c.kind)} · ${c.min_severity}+${c.kinds.length ? " · " + c.kinds.join(", ") : ""}</div></div>
      <button class="small" data-act="testCh" data-id="${c.id}">Test</button>
      <button class="small ${c.enabled ? "" : "primary"}" data-act="toggleCh" data-id="${c.id}" data-on="${c.enabled ? 0 : 1}">${c.enabled ? "Pause" : "Resume"}</button>
      <button class="small danger" data-act="delCh" data-id="${c.id}">✕</button></div>`).join("") || `<div class="empty">Alerts only show in the app until you add a channel.</div>`}</div>
    <details class="add card"><summary>+ Send alerts somewhere</summary><form class="stack" data-form="addChannel">
      <label>Type<select name="kind" data-act="chKind">${Object.entries(CHANNEL_FORMS).map(([k, v]) => `<option value="${k}" ${k === kind ? "selected" : ""}>${v.label}</option>`).join("")}</select></label>
      <div class="fields"><label>Label<input name="label" required value="${esc(CHANNEL_FORMS[kind].label.split(" (")[0])}"></label>
      ${CHANNEL_FORMS[kind].fields.map(([f, l]) => `<label>${l}<input name="cfg_${f}" ${["server", "token"].includes(f) ? "" : "required"}></label>`).join("")}
      <label>Send when at least<select name="min_severity"><option value="info">Info (everything)</option><option value="warning" selected>Warning</option><option value="critical">Critical only</option></select></label></div>
      <button class="primary">Add channel</button></form></details>

    <h2>Family</h2><form class="card stack" data-form="family"><div class="fields">
      <label>Family name<input name="name" value="${esc(fam.name)}"></label>
      <label>Time zone<input name="timezone" value="${esc(fam.timezone)}"></label>
      <label>Allowance day<select name="allowance_weekday">${WEEKDAYS.map((d, i) => `<option value="${i}" ${(fam.settings.allowance_weekday ?? 5) === i ? "selected" : ""}>${d}</option>`).join("")}</select></label>
      <label>Daily digest at (hour, 0–23)<input name="digest_hour" type="number" min="0" max="23" value="${fam.settings.digest_hour ?? 17}"></label></div>
      <button class="primary">Save</button></form>

    <h2>Webhooks</h2><div class="card stack small">
      <div>Lock events<div class="copy">${esc(fam.webhooks.lock)}</div></div>
      <div>Location (OwnTracks HTTP mode, or any app; replace &lt;member id&gt;)<div class="copy">${esc(fam.webhooks.location)}</div>
        <div class="muted">Member IDs: ${S.members.map((m) => `${esc(m.name)} = ${m.id}`).join(", ")}</div></div>
      <div>Custom alert (garage, water leak, doorbell…)<div class="copy">${esc(fam.webhooks.alert)}</div></div>
      <div class="muted">Anyone with these URLs can post events. If one leaks, rotate them.</div>
      <div><button class="small danger" data-act="rotateHooks">Rotate webhook URLs</button></div></div>
    <p><button data-act="signOut">Sign out</button></p>`;
};

// ------------------------------------------------------------------ actions

const ACT = {
  authMode: (d) => { S.authMode = d.mode; render(); },
  pickKid: (d) => { S.pickedKid = Number(d.id); render(); },
  forgetFamily: () => { S.kidList = null; S.pickedKid = null; S.joinCode = null; localStore("fh_join", null); render(); },
  signOut: () => signOut(),
  complete: async (d) => {
    const r = await api("POST", `/api/chores/instances/${d.id}/complete`);
    toast(r.status === "approved" ? `Nice! +${r.points} points` : "Sent to a parent for approval 👍");
  },
  review: async (d) => { await api("POST", `/api/chores/instances/${d.id}/review`, { approve: d.ok === "1" }); },
  choreDay: (d) => { S.choreDay = addDays(S.choreDay, Number(d.delta)); },
  delChore: async (d) => { if (confirm("Remove this chore?")) await api("DELETE", `/api/chores/${d.id}`); },
  redeem: async (d) => { await api("POST", `/api/rewards/${d.id}/redeem`); toast("Request sent! 🎉"); },
  decide: async (d) => { await api("POST", `/api/redemptions/${d.id}/decide`, { fulfill: d.ok === "1" }); },
  delReward: async (d) => { await api("DELETE", `/api/rewards/${d.id}`); },
  delEvent: async (d) => { if (confirm("Delete this event?")) await api("DELETE", `/api/events/${d.id}`); },
  syncCal: async () => {
    const r = await api("POST", "/api/events/sync");
    toast(Object.entries(r).map(([k, v]) => v.error ? `${k}: ${v.error}` : `${k}: ${v.pulled} in, ${v.pushed} out`).join(" · "));
  },
  hwCycle: async (d) => {
    const next = { todo: "in_progress", in_progress: "done", done: "todo" }[d.status];
    await api("PATCH", `/api/homework/${d.id}`, { status: next });
    if (next === "done") toast("Homework done! 🎓");
  },
  delMilestone: async (d) => { if (confirm("Delete this milestone?")) await api("DELETE", `/api/milestones/${d.id}`); },
  month: (d) => { const [y, m] = S.month.split("-").map(Number); const dt = new Date(y, m - 1 + Number(d.delta), 1); S.month = ymd(dt).slice(0, 7); },
  delTx: async (d) => { if (confirm("Delete this transaction?")) await api("DELETE", `/api/finance/transactions/${d.id}`); },
  delHealth: async (d) => { if (confirm("Delete this record?")) await api("DELETE", `/api/health/${d.id}`); },
  toggleShare: () => { if (S.watchId != null) stopSharing(); else startSharing(); },
  focus: (d) => { S.map?.setView([Number(d.lat), Number(d.lng)], 16); return "noRender"; },
  delFence: async (d) => { if (confirm("Remove this place?")) await api("DELETE", `/api/geofences/${d.id}`); },
  lockCmd: async (d) => {
    if (d.action === "unlock" && !confirm("Unlock the door?")) return "noRender";
    await api("POST", `/api/locks/${d.id}/command`, { action: d.action });
    toast(d.action === "lock" ? "Locked 🔒" : "Unlocked 🔓");
  },
  delLock: async (d) => { if (confirm("Remove this lock?")) await api("DELETE", `/api/locks/${d.id}`); },
  read: async (d) => { await api("POST", `/api/alerts/${d.id}/read`); S.unread = Math.max(0, S.unread - 1); },
  readAll: async () => { await api("POST", "/api/alerts/read-all"); S.unread = 0; },
  connectGoogle: async () => { location.href = (await api("GET", "/api/integrations/google/start")).url; return "noRender"; },
  disconnect: async (d) => { if (confirm(`Disconnect ${d.provider}?`)) await api("DELETE", `/api/integrations/${d.provider}`); },
  testCh: async (d) => { await api("POST", `/api/notify/channels/${d.id}/test`); toast("Test sent ✔"); return "noRender"; },
  toggleCh: async (d) => { await api("PATCH", `/api/notify/channels/${d.id}`, { enabled: d.on === "1" }); },
  delCh: async (d) => { if (confirm("Remove this channel?")) await api("DELETE", `/api/notify/channels/${d.id}`); },
  delMember: async (d) => {
    if (!confirm(`Remove ${nameOf(Number(d.id))} and all of their records?`)) return "noRender";
    await api("DELETE", `/api/members/${d.id}`);
    S.members = await api("GET", "/api/members");
  },
  editKid: async (d) => {
    const m = member(Number(d.id));
    const pin = prompt(`New PIN for ${m.name} (leave blank to keep):`, "");
    if (pin === null) return "noRender";
    const allowance = prompt("Weekly allowance in $:", (m.weekly_allowance_cents / 100).toFixed(2));
    if (allowance === null) return "noRender";
    const body = { weekly_allowance_cents: toCents(allowance) };
    if (pin) body.pin = pin;
    await api("PATCH", `/api/members/${m.id}`, body);
    S.members = await api("GET", "/api/members");
    toast("Saved");
  },
  rotateHooks: async () => {
    if (!confirm("Old webhook URLs will stop working. Update your automations afterwards. Continue?")) return "noRender";
    await api("POST", "/api/family/rotate-hook-token");
  },
};

const CHANGE = {
  toggleHwDone: (el) => localStore("fh_hw_done", el.checked ? "1" : null),
  healthMember: (el) => localStore("fh_health_member", el.value || null),
  chKind: (el) => localStore("fh_ch_kind", el.value),
  gcalId: async (el) => { await api("PATCH", "/api/integrations/google", { calendar_id: el.value || "primary" }); toast("Calendar updated; it syncs within 15 min"); return "noRender"; },
  myColor: async (el) => { await api("PATCH", `/api/members/${S.me.id}`, { color: el.value }); S.me = null; },
  myShare: async (el) => { await api("PATCH", `/api/members/${S.me.id}`, { share_location: el.checked }); if (!el.checked) stopSharing(); S.me = null; },
};

const FORMS = {
  login: async (f) => afterAuth(await api("POST", "/api/auth/login", f)),
  signup: async (f) => afterAuth(await api("POST", "/api/auth/signup", f)),
  joinCode: async (f) => {
    S.kidList = await api("GET", `/api/auth/join/${encodeURIComponent(f.code)}`);
    S.joinCode = f.code.toUpperCase(); localStore("fh_join", S.joinCode);
    render();
    return "noRender";
  },
  pinLogin: async (f) => afterAuth(await api("POST", "/api/auth/pin-login", { join_code: S.joinCode, member_id: S.pickedKid, pin: f.pin })),
  addChore: async (f) => {
    await api("POST", "/api/chores", { title: f.title, points: f.points ?? 0, assignee_id: intOrNull(f.assignee_id), recurrence: f.recurrence,
      weekdays: f.weekdays || [], start_date: f.start_date || null, requires_approval: f.requires_approval });
    toast("Chore added");
  },
  addReward: async (f) => { await api("POST", "/api/rewards", f); toast("Reward added"); },
  adjust: async (f) => { await api("POST", "/api/points/adjust", { ...f, member_id: Number(f.member_id) }); S.members = await api("GET", "/api/members"); },
  addEvent: async (f) => {
    const allDay = f.all_day;
    const start = allDay ? `${f.start.slice(0, 10)}T00:00:00` : new Date(f.start).toISOString();
    const end = f.end ? (allDay ? `${f.end.slice(0, 10)}T00:00:00` : new Date(f.end).toISOString()) : null;
    await api("POST", "/api/events", { title: f.title, start, end, all_day: allDay, location: f.location, member_ids: f.member_ids || [], push_to: f.push_to || [] });
    toast("Event added");
  },
  addHomework: async (f) => {
    await api("POST", "/api/homework", { member_id: isParent() ? Number(f.member_id) : S.me.id, subject: f.subject, title: f.title, due_date: f.due_date, points: f.points ?? 0 });
    toast("Homework added");
  },
  addMilestone: async (f) => { await api("POST", "/api/milestones", { ...f, member_id: intOrNull(f.member_id) }); toast("Milestone saved ⭐"); },
  kidSpend: async (f) => { await api("POST", "/api/finance/transactions", { amount_cents: -toCents(f.amount), member_id: S.me.id, category: "spending", description: f.description }); },
  addTx: async (f) => {
    const cents = toCents(f.amount) * (f.kind === "out" ? -1 : 1);
    await api("POST", "/api/finance/transactions", { amount_cents: cents, category: f.category, description: f.description, date: f.date || null, member_id: intOrNull(f.member_id) });
  },
  setBudget: async (f) => { await api("PUT", "/api/finance/budgets", { category: f.category, monthly_limit_cents: toCents(f.limit) }); },
  addHealth: async (f) => {
    await api("POST", "/api/health", { ...f, member_id: Number(f.member_id), date: f.date || null, next_due: f.next_due || null });
  },
  addFence: async (f) => { await api("POST", "/api/geofences", f); toast("Place saved"); },
  doorRules: async (f) => { await api("PATCH", "/api/family", { settings: f }); toast("Rules saved"); },
  addLock: async (f) => { await api("POST", "/api/locks", f); toast("Lock added"); },
  addMember: async (f) => {
    const body = { name: f.name, role: f.role, color: f.color, weekly_allowance_cents: toCents(f.allowance), birthday: f.birthday || null };
    if (f.role === "child") body.pin = f.pin; else { body.email = f.email; body.password = f.password; }
    await api("POST", "/api/members", body);
    S.members = await api("GET", "/api/members");
    toast(`${f.name} added`);
  },
  icloud: async (f) => { await api("POST", "/api/integrations/icloud", f); toast("iCloud connected"); },
  homeassistant: async (f) => { await api("POST", "/api/integrations/homeassistant", f); toast("Home Assistant connected"); },
  addChannel: async (f) => {
    const config = {};
    for (const [k, v] of Object.entries(f)) if (k.startsWith("cfg_") && v) config[k.slice(4)] = v;
    await api("POST", "/api/notify/channels", { kind: f.kind, label: f.label, min_severity: f.min_severity, config });
    toast("Channel added — try Test");
  },
  family: async (f) => {
    await api("PATCH", "/api/family", { name: f.name, timezone: f.timezone,
      settings: { allowance_weekday: Number(f.allowance_weekday), digest_hour: f.digest_hour ?? 17 } });
    S.me = null;
    toast("Saved");
  },
};

async function run(fn, ...args) {
  try {
    const r = await fn(...args);
    if (r !== "noRender") await refresh();
  } catch (err) {
    toast(err.message);
  }
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el || el.tagName === "SELECT" || el.type === "checkbox" || el.type === "color" || (el.tagName === "INPUT" && el.dataset.act in CHANGE)) return;
  const fn = ACT[el.dataset.act];
  if (!fn) return;
  e.preventDefault();
  if (el.tagName === "BUTTON") el.disabled = true;
  run(fn, el.dataset, el).finally(() => { if (el.isConnected) el.disabled = false; });
});

document.addEventListener("change", (e) => {
  const el = e.target;
  if (el.dataset.upload) { run(upload, el); return; }
  const fn = CHANGE[el.dataset.act];
  if (fn) run(fn, el);
});

document.addEventListener("submit", (e) => {
  const form = e.target.closest("form[data-form]");
  if (!form) return;
  e.preventDefault();
  const btn = form.querySelector("button.primary");
  if (btn) btn.disabled = true;
  run(FORMS[form.dataset.form], formData(form), form).finally(() => { if (btn?.isConnected) btn.disabled = false; });
});

async function upload(input) {
  const file = input.files[0];
  if (!file) return "noRender";
  const fd = new FormData();
  fd.append("file", file);
  toast("Uploading to Google Drive…");
  await api("POST", `/api/attachments/${input.dataset.upload}/${input.dataset.id}`, fd);
  toast("Saved to Drive 📎");
}

window.addEventListener("hashchange", render);
// Keep the dashboard and alert badge fresh while the app is open.
setInterval(() => {
  if (!S.me || document.hidden || document.querySelector("form :focus, details[open]")) return;
  if (["today", "alerts", "map", "home"].includes(currentView())) refresh();
}, 60000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

if (S.joinCode && !S.token) {
  S.authMode = "kid";
  api("GET", `/api/auth/join/${encodeURIComponent(S.joinCode)}`).then((r) => { S.kidList = r; render(); }).catch(() => render());
}
render();
