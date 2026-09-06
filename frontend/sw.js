const CACHE_NAME = 'knowledge-hub-disabled-v20';

self.addEventListener('install', (event) => {
    self.skipWaiting();
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
            .then(() => self.registration.unregister())
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});
