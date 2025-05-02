const CACHE_NAME = 'slovozviaz-cache-v1';
const urlsToCache = [
    '/',
    '/static/css/style.css',
    '/static/js/main.js',
    '/static/js/ui.js',
    '/static/js/api.js',
    '/static/manifest.json',
    '/static/images/icons/icon-192x192.png',
    '/static/images/icons/icon-512x512.png',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
            .catch(err => console.error('[SW] Cache install error:', err))
    );
    self.skipWaiting();
});

self.addEventListener('fetch', event => {
    const requestUrl = new URL(event.request.url);
    const isCachable = self.origin === requestUrl.origin &&
        urlsToCache.includes(requestUrl.pathname);

    if (isCachable) {
        event.respondWith(
            caches.match(event.request)
                .then(cachedResponse => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    return fetch(event.request);
                })
        );
    } else {
        event.respondWith(fetch(event.request));
    }
});

self.addEventListener('activate', event => {
    const cacheWhitelist = [CACHE_NAME];
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (!cacheWhitelist.includes(cacheName)) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
    return self.clients.claim();
});
