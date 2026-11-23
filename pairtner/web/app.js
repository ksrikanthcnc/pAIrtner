/* pAIrtner PWA shell: hash router, auth, api(), store, i18n.
 * Views live in /components/*.js and each export init(container, ctx),
 * show(), hide(), destroy(). All dynamic text goes through textContent or
 * PairtnerRender (render.js). No HTML sinks, no inline scripts. */

  var UI = {
    en: {
      name: "Name", password: "Password", invite: "Invite code", language: "Language",
      signup: "Log in", bad_login: "Wrong name or password. Set one with /password in Telegram.",
      rotate: "Rotate token", signout: "Sign out",
      send: "Send", message: "Message", online: "online", offline: "offline, retrying",
      closed: "Registration is closed on this server.",
      bad_token: "That token was not accepted.",
      new_token: "New token (copy it now, it is shown once):",
      failed: "Could not send. Check your connection.",
      attachment: "Download",
      nav_chat: "Chat", nav_data: "My Data", nav_memory: "Knowledge", nav_settings: "Settings", nav_admin: "Admin",
      loading: "Loading...", load_failed: "Could not load this view."
    },
    te: {
      name: "పేరు", password: "పాస్‌వర్డ్", invite: "ఆహ్వాన కోడ్", language: "భాష",
      signup: "లాగిన్", bad_login: "పేరు లేదా పాస్‌వర్డ్ తప్పు. టెలిగ్రామ్‌లో /password తో సెట్ చేయండి.",
      rotate: "టోకెన్ మార్చు", signout: "సైన్ అవుట్",
      send: "పంపు", message: "సందేశం", online: "ఆన్‌లైన్", offline: "ఆఫ్‌లైన్, మళ్లీ ప్రయత్నిస్తోంది",
      closed: "ఈ సర్వర్‌లో నమోదు మూసివేయబడింది.",
      bad_token: "ఆ టోకెన్ అంగీకరించబడలేదు.",
      new_token: "కొత్త టోకెన్ (ఇప్పుడే కాపీ చేయండి, ఒక్కసారి మాత్రమే కనిపిస్తుంది):",
      failed: "పంపలేకపోయాము. మీ కనెక్షన్‌ని తనిఖీ చేయండి.",
      attachment: "డౌన్‌లోడ్",
      nav_chat: "చాట్", nav_data: "నా డేటా", nav_memory: "జ్ఞానం", nav_settings: "సెట్టింగ్‌లు", nav_admin: "అడ్మిన్",
      loading: "లోడ్ అవుతోంది...", load_failed: "ఈ వీక్షణను లోడ్ చేయలేకపోయాము."
    }
  };

var params = new URLSearchParams(location.search);
var profile = (params.get("profile") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 32);
/* Sandbox mode (duo dev page): API calls carry ?sandbox=<id> and the bearer
 * token comes from the URL; storage is namespaced per sandbox. */
var sandbox = (params.get("sandbox") || "").replace(/[^0-9a-zA-Z_-]/g, "").slice(0, 40);
var prefix = "pairtner:" + (profile || "default") + ":" + (sandbox ? "sbx" + sandbox.slice(0, 8) + ":" : "");
var store = {
  get: function (k) { return localStorage.getItem(prefix + k); },
  set: function (k, v) { localStorage.setItem(prefix + k, v); },
  del: function (k) { localStorage.removeItem(prefix + k); }
};
/* The admin token is a shared secret: keep it for this browser session only. */
var adminStore = {
  get: function () { return sessionStorage.getItem(prefix + "admintoken"); },
  set: function (v) { sessionStorage.setItem(prefix + "admintoken", v); },
  del: function () { sessionStorage.removeItem(prefix + "admintoken"); }
};

var $ = function (id) { return document.getElementById(id); };
var lang = store.get("lang") || ((navigator.language || "").indexOf("te") === 0 ? "te" : "en");
var meta = null;
var token = store.get("token");
if (sandbox && params.get("token")) {
  token = params.get("token");
  store.set("token", token);
  if (window.history && history.replaceState) {
    history.replaceState(null, "", location.pathname + "?profile=" + profile + "&sandbox=" + sandbox);
  }
}
var me = null;
var adminToken = null;

/* ---- i18n ---- */

function tr(key) { return (UI[lang] && UI[lang][key]) || UI.en[key] || key; }

/* Components register their own strings: {en: {...}, te: {...}}. */
function addStrings(dict) {
  Object.keys(dict).forEach(function (code) {
    UI[code] = Object.assign(UI[code] || {}, dict[code]);
  });
}

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach(function (el) {
    el.textContent = tr(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
    el.setAttribute("placeholder", tr(el.getAttribute("data-i18n-placeholder")));
  });
}

/* ---- theme (CSS variables; "auto" follows the OS) ---- */

function applyTheme(mode) {
  var root = document.documentElement;
  if (mode === "light" || mode === "dark") { root.setAttribute("data-theme", mode); }
  else { root.removeAttribute("data-theme"); }
}
applyTheme(store.get("theme") || "auto");

/* ---- api ---- */

function ApiError(status, code) {
  this.status = status;
  this.code = code;
}

function api(method, path, body, signal, extraHeaders) {
  var headers = Object.assign({}, extraHeaders || {});
  if (token) { headers.Authorization = "Bearer " + token; }
  var init = { method: method, headers: headers, signal: signal };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (sandbox && path.indexOf("/api/") === 0) {
    path += (path.indexOf("?") < 0 ? "?" : "&") + "sandbox=" + sandbox;
  }
  return fetch(path, init).then(function (res) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      if (!res.ok) { throw new ApiError(res.status, data.error || "error"); }
      return data;
    });
  });
}

function qs(obj) {
  var p = new URLSearchParams();
  Object.keys(obj).forEach(function (k) {
    if (obj[k] !== undefined && obj[k] !== null && obj[k] !== "") { p.set(k, String(obj[k])); }
  });
  var s = p.toString();
  return s ? "?" + s : "";
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

/* ---- shared context handed to every component ---- */

var ctx = {
  api: api, ApiError: ApiError, qs: qs, store: store, tr: tr, addStrings: addStrings,
  formatTime: formatTime, applyTheme: applyTheme,
  getLang: function () { return lang; },
  getMeta: function () { return meta; },
  getMe: function () { return me; },
  hasToken: function () { return !!token; },
  isAdmin: function () { return !!adminToken; },
  adminHeaders: function () { return adminToken ? { "X-Admin-Token": adminToken } : {}; },
  setAdminToken: setAdminToken,
  clearAdmin: clearAdmin,
  setStatus: setStatus,
  signOut: signOut,
  loadModule: function (name) { return import(COMPONENTS[name]); }
};

/* ---- screens ---- */

function show(screen) {
  $("auth").hidden = screen !== "auth";
  $("app").hidden = screen !== "app";
}

function authError(text) {
  var el = $("auth-error");
  el.textContent = text || "";
  el.hidden = !text;
}

function setStatus(key) {
  var el = $("status");
  if (key === "offline") {
    el.textContent = "⚡ " + tr(key);
    el.className = "status offline-badge";
  } else {
    el.textContent = (meta && meta.llm) ? meta.llm : tr(key);
    el.className = "status";
  }
}

/* ---- admin access (X-Admin-Token) ---- */

function updateAdminUi() {
  $("admin-badge").hidden = !adminToken;
  $("nav-admin").hidden = !adminToken;
}

function setAdminToken(value) {
  return api("GET", "/api/v1/admin/stats", undefined, undefined, { "X-Admin-Token": value })
    .then(function () {
      adminToken = value;
      adminStore.set(value);
      updateAdminUi();
    });
}

function clearAdmin() {
  adminToken = null;
  adminStore.del();
  updateAdminUi();
  if (currentRoute().admin) { location.hash = "#dashboard"; }
}

/* ---- router ---- */

var COMPONENTS = {
  chat: "./components/chat.js",
  dashboard: "./components/dashboard.js",
  memory: "./components/memory.js",
  settings: "./components/settings.js",
  couple: "./components/couple.js",
  admin: "./components/admin.js"
};

var ROUTES = {
  "chat": { comp: "chat", tab: "chat" },
  "dashboard": { comp: "dashboard", tab: "dashboard" },
  "memory": { comp: "memory", tab: "memory" },
  "dashboard/admin": { comp: "admin", tab: "admin", admin: true },
  "settings": { comp: "settings", tab: "settings" }
};

var mounted = {};   /* component name -> {el, mod, ready} */
var navSeq = 0;

function currentRoute() {
  var key = location.hash.replace(/^#\/?/, "").replace(/\/+$/, "") || "chat";
  return ROUTES[key] || ROUTES.chat;
}

/* Load a component lazily and give it its own container. */
function mount(name) {
  if (mounted[name]) { return mounted[name].ready; }
  var el = document.createElement("section");
  el.className = "view view-" + name;
  el.hidden = true;
  $("views").appendChild(el);
  var entry = { el: el, mod: null };
  entry.ready = import(COMPONENTS[name]).then(function (mod) {
    entry.mod = mod;
    return mod.init(el, ctx);
  }).then(function () { return entry; });
  entry.ready.catch(function () {
    delete mounted[name];
    el.remove();
  });
  mounted[name] = entry;
  return entry.ready;
}

function updateNav(tab) {
  document.querySelectorAll("#bottom-nav a").forEach(function (a) {
    var on = a.getAttribute("data-tab") === tab;
    a.classList.toggle("active", on);
    if (on) { a.setAttribute("aria-current", "page"); } else { a.removeAttribute("aria-current"); }
  });
}

function render() {
  if (!token || $("app").hidden) { return; }
  var route = currentRoute();
  if (route.admin && !adminToken) { location.hash = "#dashboard"; return; }
  var seq = ++navSeq;
  mount(route.comp).then(function (entry) {
    if (seq !== navSeq) { return; }
    Object.keys(mounted).forEach(function (n) {
      var e = mounted[n];
      if (n !== route.comp && e.mod && !e.el.hidden) {
        e.el.hidden = true;
        e.mod.hide();
      }
    });
    entry.el.hidden = false;
    entry.mod.show();
    updateNav(route.tab);
  }).catch(function () {
    if (seq !== navSeq) { return; }
    var note = document.createElement("p");
    note.className = "error pad";
    note.textContent = tr("load_failed");
    $("views").appendChild(note);
  });
}

window.addEventListener("hashchange", render);

/* ---- auth flow ---- */

function enterApp(name, fresh) {
  $("who").textContent = name || "";
  show("app");
  applyLang();
  updateAdminUi();
  return mount("chat").then(function (entry) {
    entry.mod.start(fresh);
    render();
  });
}

function destroyAll() {
  Object.keys(mounted).forEach(function (n) {
    var e = mounted[n];
    if (e.mod && e.mod.destroy) { e.mod.destroy(); }
    e.el.remove();
  });
  mounted = {};
  navSeq++;
}

function signOut() {
  destroyAll();
  token = null;
  me = null;
  adminToken = null;
  adminStore.del();
  store.del("token");
  store.del("cursor");
  store.del("history");
  localStorage.removeItem("msgQueue");
  $("menu").hidden = true;
  updateAdminUi();
  showAuth();
}

function finishLogin(newToken) {
  token = newToken;
  store.set("token", token);
  store.set("lang", lang);
  return api("GET", "/api/v1/me").then(function (data) {
    me = data;
    return enterApp(data.name, true);
  });
}

function login(name, password) {
  return api("POST", "/api/v1/login", { name: name, password: password })
    .then(function (res) { return finishLogin(res.token); });
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
  var sel = $("lang");
  sel.textContent = "";
  ((meta && meta.languages) || ["en", "te"]).forEach(function (code) {
    var opt = document.createElement("option");
    opt.value = code;
    opt.textContent = code;
    if (code === lang) { opt.selected = true; }
    sel.appendChild(opt);
  });
}

function autoRegister() {
  var name = params.get("name");
  if (profile && name && meta && meta.registration === "open") {
    register(name, "", lang).catch(function () { showAuth(); });
    return true;
  }
  if (profile && name && meta && meta.registration !== "open") {
    showAuth();
    authError("Dev duo mode: set REST_REGISTRATION=open in .env to auto-register, or log in with password.");
    return true;
  }
  return false;
}

function bind() {
  $("signup").addEventListener("submit", function (ev) {
    ev.preventDefault();
    lang = $("lang").value || lang;
    applyLang();
    login($("name").value.trim(), $("password").value)
      .catch(function (err) {
        authError(err instanceof ApiError && err.status === 401
          ? tr("bad_login") : (err.code || "error"));
      });
  });
  $("lang").addEventListener("change", function () {
    lang = $("lang").value;
    applyLang();
  });
  $("menu-btn").addEventListener("click", function () { $("menu").hidden = !$("menu").hidden; });
  $("signout").addEventListener("click", signOut);
  $("rotate").addEventListener("click", function () {
    api("POST", "/api/v1/token/rotate").then(function (res) {
      token = res.token;
      store.set("token", token);
      $("menu").hidden = true;
      return mount("chat").then(function (entry) {
        entry.mod.addNotice(tr("new_token") + " " + res.token);
      });
    }).catch(function (err) {
      if (err instanceof ApiError && err.status === 401) { signOut(); }
    });
  });
}

function initMode() {
  var m = /^\/(couple|solo)\//.exec(location.pathname);
  if (!m) { return; }
  localStorage.setItem("mode", m[1]);
  var link = document.querySelector('link[rel="manifest"]');
  if (link) { link.href = "/" + m[1] + "/manifest.json"; }
}

function restoreAdmin() {
  var saved = adminStore.get();
  if (!saved) { return Promise.resolve(); }
  return setAdminToken(saved).catch(function () { adminStore.del(); });
}

function start() {
  initMode();
  applyLang();
  bind();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* offline shell is optional */ });
  }

  api("GET", "/api/v1/meta").then(function (m) { meta = m; }, function () { meta = null; })
    .then(function () {
      if (token) {
        return api("GET", "/api/v1/me").then(
          function (data) {
            me = data;
            return enterApp(data.name, false).then(restoreAdmin);
          },
          function (err) {
            if (err instanceof ApiError && err.status === 401) { token = null; store.del("token"); showAuth(); }
            else { return enterApp("", false).then(function () { setStatus("offline"); }); }
            return null;
          });
      }
      if (!autoRegister()) { showAuth(); }
      return null;
    });
}

start();
