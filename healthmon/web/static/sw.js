// Service worker mínimo: guarda apenas a "casca" estática do app para abrir rápido.
// Respostas da API (dados de saúde) NUNCA são armazenadas em cache.
const CACHE = "hm-shell-v1";
const SHELL = ["/static/style.css", "/static/app.js", "/static/vendor/chart.umd.js", "/static/icon.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || !u.pathname.startsWith("/static/")) return;
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
