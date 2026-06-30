// Minimal service worker — enables "Add to Home Screen" / standalone mode.
// Network-first: always fetch fresh (prototype), fall back to cache offline.
// Bump CACHE on shell/header changes so the byte-changed sw.js replaces the old
// worker and re-caches the app shell. v4: only the CSP fix (script-src
// 'unsafe-eval') + stop proxying cross-origin requests (see fetch handler) so the
// Telegram Login Widget loads for installed-PWA clients. v5: flagship AI coach
// («Собери следующую неделю») — new coach UI in app.js. v6: UX brief — 4-tab nav
// (no «Ещё»), Photos→Замеры, Reports→История. v7: plan delete + multi-plan
// day + Replace/Add-second dialog. v8: weekday everywhere + configurable
// date format (one fmtDate helper). v9: charts with Y gridlines/ticks + X date
// ticks (one axed lineChart), dead spark() removed. v10: «на этой неделе» label
// (dashboard week counter is calendar-week, backend fix). v11: single-entry
// Home (Планы/Расписание only via Тренировка hub) + confirm PlanEdit delete.
// v12: clearer planning UI (create vs view, named AI buttons, Шаблоны as link).
// v13: visible «Отчёты (PDF)» at top of История. v14: duplicate-day warning for
// mass plan creation (AI week / paste / template) — Отменить/Заменить/Добавить.
// v15: nice round Y-axis ticks on all charts (no ugly fractions). v16: iOS
// design pass — blur bars, segmented control, switches, inset lists, sticky title.
// v17: swipe-left-to-delete on plan/history/measurement lists. v18: Plans is
// create-only. v19: manual plan create gets a calendar picker (occupied days
// v21: feed remembers the selected day on return + «сегодня» marker in month grid.
// marked). v20: «💤 Отдых» days + Apple-style month → centered scrolling day feed.
const CACHE = 'fit-proto-v37';
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
