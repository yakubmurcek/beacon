// Beacon's service worker: only for pings (hub/webpush.py). A push shows one notification; a tap opens the card
// it is about, in the Beacon window already open when there is one. No caching: pages load fresh as before.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { title: e.data ? e.data.text() : 'Beacon' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Beacon', {
    body: d.body || '',
    tag: d.tag || 'beacon',
    data: { url: d.url || '/inbox' },
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    requireInteraction: Boolean(d.urgent),
    silent: !d.urgent,  // a digest arrives quietly; only urgent makes a sound
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = new URL(e.notification.data?.url || '/inbox', self.location.origin).href;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const same = wins.find((w) => new URL(w.url).origin === self.location.origin);
    if (same) { await same.focus(); return same.navigate(url); }
    return self.clients.openWindow(url);
  })());
});
