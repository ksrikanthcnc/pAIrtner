/* Shell cache only. API traffic is never intercepted or cached. */
"use strict";
var CACHE = "pairtner-shell-v16";  // BUMP on every change to web/ files, or installed PWAs keep the old code
var SHELL = [
  "/", "/index.html", "/app.js", "/render.js", "/style.css",
  "/components/chat.js", "/components/dashboard.js", "/components/memory.js", "/components/settings.js",
  "/components/couple.js", "/components/admin.js",
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


/* ── Push notifications ──────────────────────────────────────────── */

self.addEventListener("push", function (event) {
  var data = {title: "pAIrtner", body: "You have a new message"};
  try { data = event.data.json(); } catch (e) { /* use defaults */ }
  event.waitUntil(
    self.registration.showNotification(data.title || "pAIrtner", {
      body: data.body || "",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: {url: data.url || "/"},
      vibrate: [200, 100, 200],
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({type: "window"}).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        if (clients[i].url.indexOf(url) !== -1 && "focus" in clients[i]) {
          return clients[i].focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
