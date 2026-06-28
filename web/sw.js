// Minimal service worker — enables "Add to Home Screen" / standalone mode.
// Network-first: always fetch fresh (prototype), fall back to cache offline.
const CACHE = 'fit-proto-v1';
const SHELL = ['./', 'index.html', 'styles.css', 'app.js', 'icon.svg', 'manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache API
  e.respondWith(
    fetch(e.request).then(r => {
      const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r;
    }).catch(() => caches.match(e.request).then(m => m || caches.match('index.html')))
  );
});
