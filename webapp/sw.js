/**
 * AirBridge Service Worker — offline caching for PWA.
 */

const CACHE_NAME = "airbridge-v1";
const STATIC_ASSETS = [
    "/",
    "/static/css/style.css",
    "/static/js/app.js",
    "/manifest.json",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((names) =>
            Promise.all(
                names
                    .filter((name) => name !== CACHE_NAME)
                    .map((name) => caches.delete(name))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    // Only cache GET requests for static assets
    if (event.request.method !== "GET") return;

    const url = new URL(event.request.url);

    // Don't cache API or WebSocket requests
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;

    event.respondWith(
        caches.match(event.request).then((cached) => {
            // Network-first for HTML, cache-first for assets
            if (url.pathname === "/" || url.pathname.endsWith(".html")) {
                return fetch(event.request)
                    .then((response) => {
                        const clone = response.clone();
                        caches.open(CACHE_NAME)
                            .then((cache) => cache.put(event.request, clone))
                            .catch(() => {/* cache write failed — non-critical */});
                        return response;
                    })
                    .catch(() => cached || new Response("AirBridge is offline", { status: 503 }));
            }

            return cached || fetch(event.request).then((response) => {
                const clone = response.clone();
                caches.open(CACHE_NAME)
                    .then((cache) => cache.put(event.request, clone))
                    .catch(() => {/* cache write failed — non-critical */});
                return response;
            });
        })
    );
});
