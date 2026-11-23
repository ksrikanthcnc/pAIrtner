/* Chat view: messages, send, long-poll, offline queue, push, source badges.
 * Extracted unchanged from the single-file client; only the shared helpers
 * (api, store, tr, signOut, setStatus) now come from the ctx passed to init().
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var api, store, tr, ApiError, formatTime;
var cursor = 0;
var pollAbort = null;
var polling = false;
var flushing = false;
var stopped = true;
var started = false;
var _lastDateStr = "";
var listeners = [];

var $ = function (id) { return document.getElementById(id); };

function listen(target, type, fn) {
  target.addEventListener(type, fn);
  listeners.push([target, type, fn]);
}

/* ---- lifecycle ---- */

export function init(container, context) {
  ctx = context;
  root = container;
  api = ctx.api; store = ctx.store; tr = ctx.tr; ApiError = ctx.ApiError; formatTime = ctx.formatTime;
  cursor = parseInt(store.get("cursor") || "0", 10) || 0;
  root.classList.add("chat");
  build();
  bind();
  return Promise.resolve();
}

export function show() {
  var log = $("log");
  if (log) { log.scrollTop = log.scrollHeight; }
}

export function hide() { /* polling continues in the background so relays arrive */ }

export function destroy() {
  stopped = true;
  started = false;
  if (pollAbort) { pollAbort.abort(); }
  listeners.forEach(function (l) { l[0].removeEventListener(l[1], l[2]); });
  listeners = [];
  polling = false;
  cursor = 0;
}

/* Called by the shell once the user is authenticated. */
export function start(fresh) {
  if (started) { return; }
  started = true;
  stopped = false;
  var meta = ctx.getMeta();
  if (meta && meta.llm) {
    $("status").textContent = meta.llm;
    $("status").title = "LLM provider";
  }
  $("log").textContent = "";
  loadHistory();
  pollLoop();
  // Subscribe to push notifications after auth (earlier for a fresh login).
  setTimeout(function () { if (ctx.hasToken()) { subscribePush(); } }, fresh ? 1000 : 3000);
}

/* A bot-styled line in the log (used for the rotated token). */
export function addNotice(text) { addBubble("bot", text); }

function build() {
  var log = document.createElement("section");
  log.id = "log";
  log.setAttribute("aria-live", "polite");
  var form = document.createElement("form");
  form.id = "composer";
  var ta = document.createElement("textarea");
  ta.id = "text";
  ta.rows = 1;
  ta.setAttribute("autocomplete", "off");
  ta.setAttribute("placeholder", tr("message"));
  var btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "primary";
  btn.textContent = tr("send");
  form.appendChild(ta);
  form.appendChild(btn);
  root.appendChild(log);
  root.appendChild(form);
}

/* ---- chat rendering ---- */

function scrollDown() {
  var log = $("log");
  log.scrollTop = log.scrollHeight;
}

function addBubble(cls, msg) {
  var div = document.createElement("div");
  // Classify bot messages for visual styling
  var text = typeof msg === "string" ? msg : (msg.text || "");
  if (cls === "bot" || cls === "relay") {
    var subtype = classifyMessage(text);
    if (subtype) { cls += " " + subtype; }
  }
  div.className = "msg " + cls;
  // Add timestamp to all live messages
  if (cls.indexOf("typing") === -1) {
    maybeAddDateDivider(new Date().toISOString());
    var ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = formatTime(new Date().toISOString());
    div.appendChild(ts);
  }
  if (typeof msg === "string") {
    div.textContent = msg;
  } else {
    PairtnerRender.renderMarkup(msg.markup != null ? msg.markup : msg.text, div, document);
    if (msg.buttons && msg.buttons.length) { div.appendChild(buttonRows(msg.buttons)); }
    if (msg.document) { div.appendChild(docLink(msg.document)); }
  }
  $("log").appendChild(div);
  scrollDown();
  return div;
}

function buttonRows(rows) {
  var wrap = document.createElement("div");
  wrap.className = "btns";
  rows.forEach(function (row) {
    row.forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = b.text;
      btn.addEventListener("click", function () { pressButton(wrap, b.data); });
      wrap.appendChild(btn);
    });
  });
  return wrap;
}

function disableRow(wrap) {
  wrap.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
}

function pressButton(wrap, data) {
  disableRow(wrap);
  api("POST", "/api/v1/callback", { data: data }).then(showReplies).catch(sendFailed);
}

function docLink(doc) {
  var a = document.createElement("a");
  a.className = "doc";
  var blob = new Blob([doc.content], { type: "text/plain;charset=utf-8" });
  a.href = URL.createObjectURL(blob);
  a.download = doc.filename;
  a.textContent = tr("attachment") + ": " + doc.filename;
  return a;
}

function showReplies(data) {
  (data.replies || []).forEach(function (m) { addBubble("bot", m); });
}

function sendFailed(err) {
  if (err instanceof ApiError && err.status === 401) { ctx.signOut(); return; }
  addBubble("bot err", tr("failed"));
}

/* ---- long poll ---- */

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function pollLoop() {
  if (polling || stopped || !ctx.hasToken()) { return; }
  polling = true;
  var backoff = 1000;
  function step() {
    if (stopped || !ctx.hasToken() || document.hidden) { polling = false; return Promise.resolve(); }
    pollAbort = new AbortController();
    return api("GET", "/api/v1/events?wait=20&after=" + cursor, undefined, pollAbort.signal)
      .then(function (data) {
        backoff = 1000;
        ctx.setStatus("online");
        flushQueue();
        data.events.forEach(function (ev) {
          if (ev.id > cursor) { addBubble("relay", ev.msg); }
        });
        if (data.cursor > cursor) {
          cursor = data.cursor;
          store.set("cursor", String(cursor));
        }
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") { return null; }
        if (err instanceof ApiError && err.status === 401) { ctx.signOut(); return null; }
        ctx.setStatus("offline");
        var wait = backoff;
        backoff = Math.min(backoff * 2, 30000);
        return sleep(wait);
      })
      .then(step);
  }
  step();
}

/* ---- history ---- */

function loadHistory() {
  // In sandbox mode, skip cache entirely -- always fetch fresh from server.
  // Sandbox DBs can be re-created, making cached IDs stale.
  var isSandbox = !!(new URLSearchParams(location.search).get("sandbox"));
  var cached = isSandbox ? [] : JSON.parse(store.get("history") || "[]");
  var lastId = 0;
  if (cached.length) {
    renderMessages(cached);
    lastId = cached[cached.length - 1].id || 0;
  }

  // Fetch messages from server
  var url = "/api/v1/history?limit=500";
  if (lastId > 0) { url += "&offset_id=" + lastId; }
  api("GET", url).then(function (data) {
    var newMsgs = data.messages || [];
    if (newMsgs.length) {
      if (!lastId) {
        cached = newMsgs;
        renderMessages(newMsgs);
      } else {
        cached = cached.concat(newMsgs);
        newMsgs.forEach(function (m) { renderOneMessage(m); });
      }
      if (cached.length > 1000) { cached = cached.slice(-1000); }
      if (!isSandbox) { store.set("history", JSON.stringify(cached)); }
    } else if (!lastId && !cached.length) {
      // Server returned nothing and we have no cache -- empty chat
    }
    ctx.setStatus("online");
  }).catch(function (err) {
    if (err instanceof ApiError && err.status === 401) { ctx.signOut(); return; }
    ctx.setStatus("offline");
    // Still show cached messages if offline
    if (cached.length && !$("log").children.length) { renderMessages(cached); }
  });
}

function renderMessages(messages) {
  var log = $("log");
  log.textContent = "";
  _lastDateStr = "";  // Reset date divider tracking
  messages.forEach(function (m) { renderOneMessage(m); });
  scrollDown();
}

function classifyMessage(text) {
  // Classify bot messages into subtypes for visual styling
  if (!text) { return ""; }
  if (/^✅\s*Noted!|^✅\s*Saved\s+\d+|^✅\s*గమనించాను/i.test(text)) { return "note"; }
  if (/^💡\s*Tip:|^💡\s*చిట్కా:/i.test(text)) { return "tip"; }
  if (/^💌\s|^💌\s*A message from|^💌\s*మీ భాగస్వామి/i.test(text)) { return "relay"; }
  return "";
}

function maybeAddDateDivider(isoDate) {
  if (!isoDate) { return; }
  try {
    var d = new Date(isoDate);
    var dateStr = d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
    if (dateStr !== _lastDateStr) {
      _lastDateStr = dateStr;
      var divider = document.createElement("div");
      divider.className = "date-divider";
      var today = new Date();
      var yesterday = new Date(today);
      yesterday.setDate(yesterday.getDate() - 1);
      if (d.toDateString() === today.toDateString()) {
        divider.textContent = "Today";
      } else if (d.toDateString() === yesterday.toDateString()) {
        divider.textContent = "Yesterday";
      } else {
        divider.textContent = dateStr;
      }
      $("log").appendChild(divider);
    }
  } catch (e) { /* ignore */ }
}

function renderOneMessage(m) {
  maybeAddDateDivider(m.created_at);
  var cls = m.role === "user" ? "me" : "bot";
  // Sub-classify bot messages
  if (m.role !== "user") {
    var subtype = classifyMessage(m.content);
    if (subtype) { cls += " " + subtype; }
  }
  var div = document.createElement("div");
  div.className = "msg " + cls;
  if (m.id) { div.dataset.msgId = m.id; }
  if (m.created_at) {
    var ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = formatTime(m.created_at);
    div.appendChild(ts);
  }
  if (m.source === "telegram" || m.source === "pwa") {
    var badge = document.createElement("span");
    badge.className = "src";
    badge.textContent = m.source === "telegram" ? "\uD83D\uDCF1" : "\uD83C\uDF10";
    badge.title = m.source === "telegram" ? "Telegram" : "PWA";
    div.appendChild(badge);
  }
  var content = document.createElement("span");
  PairtnerRender.renderMarkup(m.content, content, document);
  div.appendChild(content);
  // Long-press to delete (mobile-friendly)
  if (m.id && m.role === "user") {
    var timer = null;
    div.addEventListener("pointerdown", function () {
      timer = setTimeout(function () {
        if (confirm("Delete this message?")) {
          api("DELETE", "/api/v1/message", { id: m.id }).then(function () {
            div.classList.add("deleted");
            // Remove from local cache
            var cached = JSON.parse(store.get("history") || "[]");
            store.set("history", JSON.stringify(cached.filter(function (c) { return c.id !== m.id; })));
          }).catch(function () { /* ignore delete failures */ });
        }
      }, 600);
    });
    div.addEventListener("pointerup", function () { clearTimeout(timer); });
    div.addEventListener("pointerleave", function () { clearTimeout(timer); });
  }
  $("log").appendChild(div);
}

/* ---- composer + connectivity ---- */

function bind() {
  $("composer").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var input = $("text");
    var text = input.value.trim();
    if (!text) { return; }
    input.value = "";
    input.style.height = "auto";
    addBubble("me", text);
    var typing = addBubble("bot typing", "●●●");
    api("POST", "/api/v1/message", { text: text }).then(function (data) {
      typing.remove();
      showReplies(data);
    }).catch(function (err) {
      typing.remove();
      if (err instanceof ApiError) { sendFailed(err); return; }
      // network failure: queue and mark the bubble pending
      queueMessage(text);
      addBubble("me pending", "⏳ " + text);
      updatePendingBadges();
    });
  });
  // Shift+Enter = newline, Enter = send. Auto-resize textarea.
  $("text").addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      $("composer").dispatchEvent(new Event("submit"));
    }
  });
  $("text").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  listen(document, "visibilitychange", function () {
    if (document.hidden) {
      if (pollAbort) { pollAbort.abort(); }
    } else {
      pollLoop();
    }
  });
  // Offline/online detection
  listen(window, "online", function () {
    ctx.setStatus("online");
    flushQueue();
    pollLoop();
  });
  listen(window, "offline", function () { ctx.setStatus("offline"); });
}

/* ---- offline queue ---- */

function queueMessage(text) {
  var q = JSON.parse(localStorage.getItem("msgQueue") || "[]");
  q.push({ text: text, ts: Date.now() });
  localStorage.setItem("msgQueue", JSON.stringify(q));
}

function updatePendingBadges() {
  var q = JSON.parse(localStorage.getItem("msgQueue") || "[]");
  var el = $("text");
  if (el) { el.placeholder = q.length ? "⏳ " + q.length + " pending" : ""; }
  if (!q.length) {
    var pend = document.querySelectorAll(".pending");
    for (var i = 0; i < pend.length; i++) {
      pend[i].classList.remove("pending");
      pend[i].textContent = pend[i].textContent.replace(/^⏳ /, "");
    }
  }
}

function flushQueue() {
  var q = JSON.parse(localStorage.getItem("msgQueue") || "[]");
  if (!q.length || flushing) { return; }
  flushing = true;
  var combined = q.map(function (m) {
    var dt = new Date(m.ts);
    var time = dt.getHours() + ":" + String(dt.getMinutes()).padStart(2, "0");
    return "[" + time + "] " + m.text;
  }).join("\n");
  api("POST", "/api/v1/message", { text: combined, batched: true, queued_since: q[0].ts })
    .then(function (data) {
      // keep messages queued after the snapshot
      var cur = JSON.parse(localStorage.getItem("msgQueue") || "[]").slice(q.length);
      if (cur.length) { localStorage.setItem("msgQueue", JSON.stringify(cur)); }
      else { localStorage.removeItem("msgQueue"); }
      updatePendingBadges();
      showReplies(data);
    })
    .catch(function () { /* retry on next reconnect */ })
    .then(function () { flushing = false; });
}

/* ── Push notifications ──────────────────────────────────────────── */

function urlBase64ToUint8Array(base64String) {
  var padding = "=".repeat((4 - base64String.length % 4) % 4);
  var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  var rawData = atob(base64);
  var outputArray = new Uint8Array(rawData.length);
  for (var i = 0; i < rawData.length; ++i) { outputArray[i] = rawData.charCodeAt(i); }
  return outputArray;
}

export function subscribePush() {
  if (!("PushManager" in window) || !("serviceWorker" in navigator)) { return; }
  if (Notification.permission === "denied") { return; }

  fetch("/api/v1/push/vapid")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.vapid_public_key) { return; }
      navigator.serviceWorker.ready.then(function (reg) {
        reg.pushManager.getSubscription().then(function (existing) {
          if (existing) {
            // Already subscribed, send to server (in case it's a new device/reinstall)
            sendSubscription(existing);
            return;
          }
          if (Notification.permission === "default") {
            Notification.requestPermission().then(function (perm) {
              if (perm === "granted") { doSubscribe(reg, data.vapid_public_key); }
            });
          } else if (Notification.permission === "granted") {
            doSubscribe(reg, data.vapid_public_key);
          }
        });
      });
    })
    .catch(function (e) { console.warn("Push setup error:", e); /* push not configured, ignore */ });
}

function doSubscribe(reg, vapidKey) {
  reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidKey),
  }).then(function (sub) {
    sendSubscription(sub);
  }).catch(function (e) {
    console.warn("Push subscribe failed:", e);
  });
}

function sendSubscription(sub) {
  if (!ctx.hasToken()) { return; }
  var subJson = sub.toJSON();
  api("POST", "/api/v1/push/subscribe", {
    subscription: {
      endpoint: subJson.endpoint,
      keys: subJson.keys,
    }
  }).catch(function () { /* best effort */ });
}
