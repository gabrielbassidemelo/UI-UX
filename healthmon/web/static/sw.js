// Service worker: guarda só a "casca" estática do app e recebe notificações push.
// Respostas da API (dados de saúde) NUNCA são armazenadas em cache.
const CACHE = "hm-shell-v2";
const SHELL = ["/static/style.css", "/static/app.js", "/static/vendor/chart.umd.js", "/static/icon.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !u.pathname.startsWith("/static/")) return;
  // rede primeiro (para receber atualizações); cache só como reserva offline
  e.respondWith(fetch(e.request).then((r) => { const c = r.clone(); caches.open(CACHE).then((k) => k.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request)));
});
self.addEventListener("push", (e) => {
  let d = { title: "healthmon", body: "Há novidades no seu painel.", url: "/" };
  try { d = { ...d, ...e.data.json() }; } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title, { body: d.body, icon: "/static/icon-180.png", data: { url: d.url } }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => cs.length ? cs[0].focus() : self.clients.openWindow(e.notification.data.url || "/")));
});
