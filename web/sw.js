// Minimal service worker — enables "Add to Home Screen" / standalone mode.
// Network-first: always fetch fresh (prototype), fall back to cache offline.
// Bump CACHE on shell/header changes so the byte-changed sw.js replaces the old
// worker and re-caches the app shell. v3: pick up the CSP fix (script-src
// 'unsafe-eval') that lets the Telegram Login Widget render for cached clients.
const CACHE = 'fit-proto-v3';
const SHELL = ['./', 'index.html', 'styles.css', 'app.js', 'icon.svg', 'manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim())
));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache API
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match('index.html')))
  );
});
