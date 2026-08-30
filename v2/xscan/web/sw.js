const CACHE = 'xscan-v2-shell-8';
const SHELL = ['/', '/styles.css', '/app.js', '/api.js', '/player.js', '/vendor/hls.min.js', '/manifest.webmanifest', '/favicon.svg', '/icons/xscan-192.png', '/icons/xscan-512.png'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()).then(() => self.clients.matchAll().then(clients => clients.forEach(client => client.postMessage({type:'XSCAN_SW_ACTIVE'}))))));
self.addEventListener('message', event => { if (event.data?.type === 'SKIP_WAITING') self.skipWaiting(); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.includes('/audio') || event.request.method !== 'GET') return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/')));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(response => {
    if (response.ok && response.type === 'basic') caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
    return response;
  })));
});
