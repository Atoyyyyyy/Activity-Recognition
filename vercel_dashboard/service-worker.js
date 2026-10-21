const CACHE_NAME = "activity-dashboard-static-v1";
const APP_ROOT = new URL("./", self.location.href).pathname;
const INDEX_PATH = new URL("index.html", self.location.href).pathname;
const APP_FILES = [
    APP_ROOT,
    INDEX_PATH,
    `${APP_ROOT}manifest.json`,
    `${APP_ROOT}icons/icon-192.png`,
    `${APP_ROOT}icons/icon-512.png`
];

self.addEventListener("install", event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(APP_FILES))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== "GET" || url.origin !== self.location.origin) return;

    // Never cache API responses: cached readings must not appear to be live.
    if (url.pathname.startsWith(`${APP_ROOT}api/`)) return;

    if (request.mode === "navigate") {
        event.respondWith(fetch(request).catch(() => caches.match(INDEX_PATH)));
        return;
    }

    if (url.pathname.startsWith(APP_ROOT)) {
        event.respondWith(
            caches.match(request).then(cached => cached || fetch(request))
        );
    }
});
