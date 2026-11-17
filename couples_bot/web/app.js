/* pAIrtner PWA client. All dynamic text goes through textContent or
 * PairtnerRender (render.js). No HTML sinks, no inline scripts. */
(function () {
  "use strict";

  var UI = {
    en: {
      name: "Display name", invite: "Invite code", language: "Language",
      signup: "Sign up", have_token: "I already have a token", token: "Token",
      signin: "Sign in", rotate: "Rotate token", signout: "Sign out",
      send: "Send", message: "Message", online: "online", offline: "offline, retrying",
      closed: "Registration is closed on this server.",
      bad_token: "That token was not accepted.",
      new_token: "New token (copy it now, it is shown once):",
      failed: "Could not send. Check your connection.",
      attachment: "Download"
    },
    te: {
      name: "ప్రదర్శన పేరు", invite: "ఆహ్వాన కోడ్", language: "భాష",
      signup: "సైన్ అప్", have_token: "నా దగ్గర ఇప్పటికే టోకెన్ ఉంది", token: "టోకెన్",
      signin: "సైన్ ఇన్", rotate: "టోకెన్ మార్చు", signout: "సైన్ అవుట్",
      send: "పంపు", message: "సందేశం", online: "ఆన్‌లైన్", offline: "ఆఫ్‌లైన్, మళ్లీ ప్రయత్నిస్తోంది",
      closed: "ఈ సర్వర్‌లో నమోదు మూసివేయబడింది.",
      bad_token: "ఆ టోకెన్ అంగీకరించబడలేదు.",
      new_token: "కొత్త టోకెన్ (ఇప్పుడే కాపీ చేయండి, ఒక్కసారి మాత్రమే కనిపిస్తుంది):",
      failed: "పంపలేకపోయాము. మీ కనెక్షన్‌ని తనిఖీ చేయండి.",
      attachment: "డౌన్‌లోడ్"
    }
  };

  var params = new URLSearchParams(location.search);
  var profile = (params.get("profile") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32);
  var prefix = "pairtner:" + (profile || "default") + ":";
  var store = {
    get: function (k) { return localStorage.getItem(prefix + k); },
    set: function (k, v) { localStorage.setItem(prefix + k, v); },
    del: function (k) { localStorage.removeItem(prefix + k); }
  };

  var $ = function (id) { return document.getElementById(id); };
  var lang = store.get("lang") || ((navigator.language || "").indexOf("te") === 0 ? "te" : "en");
  var meta = null;
  var token = store.get("token");
  var cursor = parseInt(store.get("cursor") || "0", 10) || 0;
  var pollAbort = null;
  var polling = false;

  function tr(key) { return (UI[lang] && UI[lang][key]) || UI.en[key] || key; }

  function applyLang() {
    document.documentElement.lang = lang;
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = tr(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.setAttribute("placeholder", tr(el.getAttribute("data-i18n-placeholder")));
    });
  }

  function ApiError(status, code) {
    this.status = status;
    this.code = code;
  }

  function api(method, path, body, signal) {
    var headers = {};
    if (token) { headers.Authorization = "Bearer " + token; }
    var init = { method: method, headers: headers, signal: signal };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new ApiError(res.status, data.error || "error"); }
        return data;
      });
    });
  }

  function show(screen) {
    $("auth").hidden = screen !== "auth";
    $("chat").hidden = screen !== "chat";
  }

  function authError(text) {
    var el = $("auth-error");
    el.textContent = text || "";
    el.hidden = !text;
  }

  /* ---- chat rendering ---- */

  function scrollDown() {
    var log = $("log");
    log.scrollTop = log.scrollHeight;
  }

  function addBubble(cls, msg) {
    var div = document.createElement("div");
    div.className = "msg " + cls;
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
    if (err instanceof ApiError && err.status === 401) { signOut(); return; }
    addBubble("bot err", tr("failed"));
  }

  function setStatus(key) { $("status").textContent = tr(key); }

  /* ---- long poll ---- */

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function pollLoop() {
    if (polling || !token) { return; }
    polling = true;
    var backoff = 1000;
    function step() {
      if (!token || document.hidden) { polling = false; return Promise.resolve(); }
      pollAbort = new AbortController();
      return api("GET", "/api/v1/events?wait=20&after=" + cursor, undefined, pollAbort.signal)
        .then(function (data) {
          backoff = 1000;
          setStatus("online");
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
          if (err instanceof ApiError && err.status === 401) { signOut(); return null; }
          setStatus("offline");
          var wait = backoff;
          backoff = Math.min(backoff * 2, 30000);
          return sleep(wait);
        })
        .then(step);
    }
    step();
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (pollAbort) { pollAbort.abort(); }
    } else {
      pollLoop();
    }
  });

  /* ---- auth flow ---- */

  function enterChat(name) {
    $("who").textContent = name || "";
    $("log").textContent = "";
    show("chat");
    applyLang();
    loadHistory();
    pollLoop();
  }

  function loadHistory() {
    // Load cached messages first (instant render)
    var cached = JSON.parse(store.get("history") || "[]");
    var lastId = 0;
    if (cached.length) {
      renderMessages(cached);
      lastId = cached[cached.length - 1].id || 0;
    }

    // Fetch only new messages since last cached ID
    var url = "/api/v1/history?limit=500";
    if (lastId > 0) { url += "&offset_id=" + lastId; }
    api("GET", url).then(function (data) {
      var newMsgs = data.messages || [];
      if (newMsgs.length) {
        // If we had no cache, this is the full history
        if (!lastId) {
          cached = newMsgs;
          renderMessages(newMsgs);
        } else {
          // Append delta to cache and render just the new ones
          cached = cached.concat(newMsgs);
          newMsgs.forEach(function (m) { renderOneMessage(m); });
        }
        // Cap cache at 1000 messages
        if (cached.length > 1000) { cached = cached.slice(-1000); }
        store.set("history", JSON.stringify(cached));
      }
      setStatus("online");
    }).catch(function (err) {
      if (err instanceof ApiError && err.status === 401) { signOut(); return; }
      setStatus("offline");
      // Still show cached messages if offline
      if (cached.length && !$("log").children.length) { renderMessages(cached); }
    });
  }

  function renderMessages(messages) {
    var log = $("log");
    log.textContent = "";
    messages.forEach(function (m) { renderOneMessage(m); });
    scrollDown();
  }

  function renderOneMessage(m) {
    var cls = m.role === "user" ? "me" : "bot";
    var div = document.createElement("div");
    div.className = "msg " + cls;
    if (m.created_at) {
      var ts = document.createElement("span");
      ts.className = "ts";
      ts.textContent = formatTime(m.created_at);
      div.appendChild(ts);
    }
    var content = document.createElement("span");
    PairtnerRender.renderMarkup(m.content, content, document);
    div.appendChild(content);
    $("log").appendChild(div);
  }

  function formatTime(iso) {
    try {
      var d = new Date(iso);
      var now = new Date();
      if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      }
      return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
        d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) { return ""; }
  }

  function signOut() {
    if (pollAbort) { pollAbort.abort(); }
    token = null;
    cursor = 0;
    store.del("token");
    store.del("cursor");
    store.del("history");
    store.del("outbox");
    $("menu").hidden = true;
    showAuth();
  }

  function finishLogin(newToken) {
    token = newToken;
    store.set("token", token);
    store.set("lang", lang);
    return api("GET", "/api/v1/me").then(function (me) { enterChat(me.name); });
  }

  function register(name, invite, language) {
    var body = { name: name, lang: language };
    if (invite) { body.invite = invite; }
    return api("POST", "/api/v1/register", body).then(function (res) {
      return finishLogin(res.token);
    });
  }

  function showAuth() {
    show("auth");
    applyLang();
    authError("");
    var pv = $("privacy"); pv.textContent = ""; if (meta) { PairtnerRender.renderMarkup(meta.privacy, pv, document); }
    $("invite-row").hidden = !(meta && meta.registration === "invite");
    var sel = $("lang");
    sel.textContent = "";
    ((meta && meta.languages) || ["en", "te"]).forEach(function (code) {
      var opt = document.createElement("option");
      opt.value = code;
      opt.textContent = code;
      if (code === lang) { opt.selected = true; }
      sel.appendChild(opt);
    });
    if (meta && meta.registration === "closed") { authError(tr("closed")); }
  }

  function autoRegister() {
    var name = params.get("name");
    if (profile && name && meta && meta.registration === "open") {
      register(name, "", lang).catch(function () { showAuth(); });
      return true;
    }
    return false;
  }

  function bind() {
    $("signup").addEventListener("submit", function (ev) {
      ev.preventDefault();
      lang = $("lang").value || lang;
      applyLang();
      register($("name").value.trim(), $("invite").value.trim(), lang)
        .catch(function (err) { authError(err.code || "error"); });
    });
    $("signin").addEventListener("submit", function (ev) {
      ev.preventDefault();
      finishLogin($("token").value.trim()).catch(function () {
        token = null;
        store.del("token");
        authError(tr("bad_token"));
      });
    });
    $("lang").addEventListener("change", function () {
      lang = $("lang").value;
      applyLang();
    });
    $("composer").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var input = $("text");
      var text = input.value.trim();
      if (!text) { return; }
      input.value = "";
      addBubble("me", text);
      var typing = addBubble("bot typing", "●●●");
      api("POST", "/api/v1/message", { text: text }).then(function (data) {
        typing.remove();
        showReplies(data);
      }).catch(function (err) {
        typing.remove();
        if (!navigator.onLine) {
          // Queue for later
          var queue = JSON.parse(store.get("outbox") || "[]");
          queue.push({ text: text, ts: new Date().toISOString() });
          store.set("outbox", JSON.stringify(queue));
          addBubble("bot err", "📴 Saved offline. Will send when reconnected.");
        } else {
          sendFailed(err);
        }
      });
    });
    $("menu-btn").addEventListener("click", function () { $("menu").hidden = !$("menu").hidden; });
    $("signout").addEventListener("click", signOut);
    $("rotate").addEventListener("click", function () {
      api("POST", "/api/v1/token/rotate").then(function (res) {
        token = res.token;
        store.set("token", token);
        $("menu").hidden = true;
        addBubble("bot", tr("new_token") + " " + res.token);
      }).catch(sendFailed);
    });
  }

  function start() {
    applyLang();
    bind();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* offline shell is optional */ });
    }

    // Offline/online detection
    window.addEventListener("online", function () {
      setStatus("online");
      drainOutbox();
      pollLoop();
    });
    window.addEventListener("offline", function () {
      setStatus("offline");
    });

    api("GET", "/api/v1/meta").then(function (m) { meta = m; }, function () { meta = null; })
      .then(function () {
        if (token) {
          return api("GET", "/api/v1/me").then(
            function (me) { enterChat(me.name); },
            function (err) {
              if (err instanceof ApiError && err.status === 401) { token = null; store.del("token"); showAuth(); }
              else { enterChat(""); setStatus("offline"); }
            });
        }
        if (!autoRegister()) { showAuth(); }
        return null;
      });
  }

  function drainOutbox() {
    var queue = JSON.parse(store.get("outbox") || "[]");
    if (!queue.length) { return; }
    store.del("outbox");
    var next = function (i) {
      if (i >= queue.length) { return; }
      api("POST", "/api/v1/message", { text: queue[i].text })
        .then(function (data) {
          showReplies(data);
          next(i + 1);
        })
        .catch(function () {
          // Re-queue remaining
          var remaining = queue.slice(i);
          store.set("outbox", JSON.stringify(remaining));
          addBubble("bot err", "📴 " + remaining.length + " message(s) still queued.");
        });
    };
    addBubble("bot", "📶 Sending " + queue.length + " queued message(s)...");
    next(0);
  }

  start();
})();
