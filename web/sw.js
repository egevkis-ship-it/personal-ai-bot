// Minimal service worker — enables "Add to Home Screen" / standalone mode.
// Network-first: always fetch fresh (prototype), fall back to cache offline.
// Bump CACHE on shell/header changes so the byte-changed sw.js replaces the old
// worker and re-caches the app shell. v4: only the CSP fix (script-src
// 'unsafe-eval') + stop proxying cross-origin requests (see fetch handler) so the
// Telegram Login Widget loads for installed-PWA clients.
const CACHE = 'fit-proto-v4';
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
  // Only handle same-origin GETs. Cross-origin requests (telegram.org widget
  // script, oauth.telegram.org login iframe) must load natively: proxying them
  // through the worker's fetch() is blocked by `connect-src 'self'`, and the
  // catch-fallback would serve index.html in their place — which silently broke
  // the Telegram Login Widget for installed-PWA clients.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // never cache API
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match('index.html')))
  );
});
