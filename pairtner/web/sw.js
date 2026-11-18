/* Shell cache only. API traffic is never intercepted or cached. */
"use strict";
var CACHE = "pairtner-shell-v8";  // BUMP on every change to web/ files, or installed PWAs keep the old code
var SHELL = [
  "/", "/index.html", "/app.js", "/render.js", "/style.css",
  "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon.svg"
];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  var url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) { return; }
  if (url.pathname.indexOf("/api/") === 0) { return; } /* network only */
  if (SHELL.indexOf(url.pathname) === -1) { return; }
  event.respondWith(caches.match(url.pathname).then(function (hit) {
    return hit || fetch(req);
  }));
});
