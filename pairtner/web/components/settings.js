/* Settings view: preferences (core facts), appearance, language, mode,
 * push test, admin access, about.
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var tr = null;
var prefsBox = null;
var seq = 0;

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}

function panel(title) {
  var p = el("section", "panel");
  p.appendChild(el("h2", "", title));
  return p;
}

function row(label, control) {
  var r = el("div", "settings-row");
  r.appendChild(el("span", "", label));
  r.appendChild(control);
  return r;
}

function select(options, value, onChange) {
  var s = el("select");
  options.forEach(function (o) {
    var opt = el("option", "", o[1]);
    opt.value = o[0];
    opt.selected = o[0] === value;
    s.appendChild(opt);
  });
  s.addEventListener("change", function () { onChange(s.value); });
  return s;
}

export function init(container, context) {
  ctx = context;
  root = container;
  tr = ctx.tr;
  ctx.addStrings({
    en: {
      s_prefs: "Your preferences", s_prefs_none: "No saved preferences yet. Tell the bot what you like.",
      s_look: "Appearance", s_theme: "Theme", s_auto: "Auto", s_light: "Light", s_dark: "Dark",
      s_lang: "Language", s_mode: "Mode", s_couple: "Couple", s_solo: "Solo",
      s_mode_note: "Reinstall the app from the home screen to apply the new mode icon.",
      s_push: "Notifications", s_push_btn: "Test notifications",
      s_admin: "Admin access", s_admin_ph: "Admin token", s_admin_on: "Enable", s_admin_off: "Disable",
      s_admin_bad: "That token was not accepted.", s_admin_ok: "Admin access enabled for this session.",
      s_about: "About", s_account: "Account", s_load_err: "Could not load preferences."
    },
    te: {
      s_prefs: "మీ ప్రాధాన్యతలు", s_prefs_none: "ఇంకా ప్రాధాన్యతలు లేవు. మీకు ఏమి ఇష్టమో బాట్‌కు చెప్పండి.",
      s_look: "రూపం", s_theme: "థీమ్", s_auto: "ఆటో", s_light: "లైట్", s_dark: "డార్క్",
      s_lang: "భాష", s_mode: "మోడ్", s_couple: "జంట", s_solo: "ఒంటరి",
      s_mode_note: "కొత్త మోడ్ ఐకాన్ కోసం యాప్‌ను హోమ్ స్క్రీన్ నుండి మళ్లీ ఇన్‌స్టాల్ చేయండి.",
      s_push: "నోటిఫికేషన్లు", s_push_btn: "నోటిఫికేషన్లను పరీక్షించు",
      s_admin: "అడ్మిన్ యాక్సెస్", s_admin_ph: "అడ్మిన్ టోకెన్", s_admin_on: "ప్రారంభించు", s_admin_off: "నిలిపివేయి",
      s_admin_bad: "ఆ టోకెన్ అంగీకరించబడలేదు.", s_admin_ok: "ఈ సెషన్‌కు అడ్మిన్ యాక్సెస్ ప్రారంభించబడింది.",
      s_about: "గురించి", s_account: "ఖాతా", s_load_err: "ప్రాధాన్యతలను లోడ్ చేయలేకపోయాము."
    }
  });
  root.classList.add("settings");
  build();
  return Promise.resolve();
}

export function show() { loadPrefs(); }

export function hide() { seq++; }

export function destroy() {
  seq++;
  prefsBox = null;
  if (root) { root.textContent = ""; }
}

function build() {
  /* preferences = the user's core facts */
  var prefs = panel(tr("s_prefs"));
  prefsBox = el("div");
  prefs.appendChild(prefsBox);
  root.appendChild(prefs);

  /* appearance + language + mode */
  var look = panel(tr("s_look"));
  look.appendChild(row(tr("s_theme"), select(
    [["auto", tr("s_auto")], ["light", tr("s_light")], ["dark", tr("s_dark")]],
    ctx.store.get("theme") || "auto",
    function (v) { ctx.store.set("theme", v); ctx.applyTheme(v); })));
  var langs = (ctx.getMeta() && ctx.getMeta().languages) || ["en", "te"];
  look.appendChild(row(tr("s_lang"), select(
    langs.map(function (c) { return [c, c]; }), ctx.getLang(),
    function (v) { ctx.store.set("lang", v); location.reload(); })));
  look.appendChild(row(tr("s_mode"), select(
    [["couple", tr("s_couple")], ["solo", tr("s_solo")]],
    localStorage.getItem("mode") || "couple",
    function (v) {
      localStorage.setItem("mode", v);
      var link = document.querySelector('link[rel="manifest"]');
      if (link) { link.href = "/" + v + "/manifest.json"; }
      note.textContent = tr("s_mode_note");
    })));
  var note = el("p", "meta");
  look.appendChild(note);
  root.appendChild(look);

  /* push */
  var push = panel(tr("s_push"));
  var pushBtn = el("button", "", "🔔 " + tr("s_push_btn"));
  pushBtn.type = "button";
  var pushOut = el("p", "out");
  pushBtn.addEventListener("click", function () { testPush(pushOut); });
  push.appendChild(pushBtn);
  push.appendChild(pushOut);
  root.appendChild(push);

  /* admin access: the token stays in sessionStorage, never localStorage */
  var admin = panel(tr("s_admin"));
  var form = el("form", "inline-form");
  var input = el("input");
  input.type = "password";
  input.autocomplete = "off";
  input.placeholder = tr("s_admin_ph");
  var go = el("button", "primary", tr("s_admin_on"));
  go.type = "submit";
  var off = el("button", "", tr("s_admin_off"));
  off.type = "button";
  var adminOut = el("p", "out");
  form.appendChild(input);
  form.appendChild(go);
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var v = input.value.trim();
    if (!v) { return; }
    ctx.setAdminToken(v).then(function () {
      input.value = "";
      adminOut.textContent = tr("s_admin_ok");
    }).catch(function () { adminOut.textContent = tr("s_admin_bad"); });
  });
  off.addEventListener("click", function () { ctx.clearAdmin(); adminOut.textContent = ""; });
  admin.appendChild(form);
  admin.appendChild(off);
  admin.appendChild(adminOut);
  root.appendChild(admin);

  /* about + account */
  var about = panel(tr("s_about"));
  var meta = ctx.getMeta() || {};
  var me = ctx.getMe() || {};
  about.appendChild(el("p", "", (meta.name || "pAIrtner") + (meta.llm ? " · " + meta.llm : "")));
  if (me.name) { about.appendChild(el("p", "meta", me.name + (me.partner_name ? " ♥ " + me.partner_name : ""))); }
  if (meta.privacy) {
    var pv = el("p", "notice");
    PairtnerRender.renderMarkup(meta.privacy, pv, document);
    about.appendChild(pv);
  }
  var out = el("button", "", tr("signout"));
  out.type = "button";
  out.addEventListener("click", function () { ctx.signOut(); });
  about.appendChild(out);
  root.appendChild(about);
}

function loadPrefs() {
  var mySeq = ++seq;
  ctx.api("GET", "/api/v1/facts" + ctx.qs({ kind: "core", order: "desc", limit: 50 }))
    .then(function (data) {
      if (mySeq !== seq || !prefsBox) { return; }
      prefsBox.textContent = "";
      var facts = data.facts || [];
      if (!facts.length) { prefsBox.appendChild(el("p", "empty", tr("s_prefs_none"))); return; }
      facts.forEach(function (f) {
        var r = el("div", "row");
        r.appendChild(el("span", "grow", f.text));
        r.appendChild(el("span", "tag " + f.visibility, f.visibility));
        prefsBox.appendChild(r);
      });
    })
    .catch(function (err) {
      if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
      if (prefsBox) { prefsBox.textContent = tr("s_load_err"); }
    });
}

/* Same diagnostics the old menu button showed, now inline instead of alert(). */
function testPush(out) {
  var info = [];
  info.push("Notification API: " + ("Notification" in window ? "yes" : "NO"));
  info.push("PushManager: " + ("PushManager" in window ? "yes" : "NO (needs HTTPS)"));
  info.push("ServiceWorker: " + ("serviceWorker" in navigator ? "yes" : "NO"));
  info.push("Protocol: " + location.protocol);
  function done() { out.textContent = info.join("\n"); }
  if (!("Notification" in window)) { done(); return; }
  info.push("Permission: " + Notification.permission);
  if (Notification.permission !== "default") { done(); return; }
  Notification.requestPermission().then(function (perm) {
    info.push("Result: " + perm);
    if (perm !== "granted") { return null; }
    return ctx.loadModule("chat").then(function (chat) {
      chat.subscribePush();
      info.push("Subscribed!");
    });
  }).catch(function (e) { info.push("Error: " + e.message); }).then(done);
}
