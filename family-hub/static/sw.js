// Caches the app shell so the hub opens instantly (and offline); API calls always go to the network.
const CACHE = "family-hub-v3";
const SHELL = ["/", "/index.html", "/styles.css", "/app.js", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // Network first so updates show up right away; fall back to cache when offline.
  e.respondWith(
    fetch(e.request).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return resp;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html")))
  );
});

// Web Push: alerts arrive here even when the app is closed (iPhone: only for the home-screen app).
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { title: e.data?.text() }; }
  const title = data.title || "Family Hub";
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: data.id ? `alert-${data.id}` : undefined,
    requireInteraction: data.severity === "critical" || !!data.taken_url,
    // Android shows these buttons on the notification; iPhone opens the app instead.
    actions: data.taken_url ? [{ action: "taken", title: "Taken ✓" }] : [],
    data: { url: data.url || (data.kind === "door" ? "/#home" : "/#alerts"), taken_url: data.taken_url },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  if (e.action === "taken" && e.notification.data?.taken_url) {
    e.waitUntil(fetch(e.notification.data.taken_url, { method: "POST" }).then((r) =>
      self.registration.showNotification(r.ok ? "Marked as taken ✓" : "Couldn't mark it — open the app", { icon: "/icon-192.png" })));
    return;
  }
  const url = e.notification.data?.url || "/#alerts";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) {
      if ("focus" in w) { w.navigate(url).catch(() => {}); return w.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
