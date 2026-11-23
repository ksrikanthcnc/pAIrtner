/* Admin view (route #dashboard/admin): DB stats, users, pipeline trace.
 * Only reachable after the admin token was accepted; every request also
 * carries X-Admin-Token and the server re-checks it.
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var tr = null;
var panels = {};
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

function table(head, rows) {
  var t = el("table");
  var tr0 = el("tr");
  head.forEach(function (h) { tr0.appendChild(el("th", "", h)); });
  t.appendChild(tr0);
  rows.forEach(function (r) {
    var tr1 = el("tr");
    r.forEach(function (c) { tr1.appendChild(el("td", "", c === null || c === undefined ? "" : String(c))); });
    t.appendChild(tr1);
  });
  return t;
}

function adminGet(path) {
  return ctx.api("GET", path, undefined, undefined, ctx.adminHeaders());
}

function fail(err, box) {
  if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
  if (err instanceof ctx.ApiError && err.status === 403) { ctx.clearAdmin(); return; }
  box.appendChild(el("p", "error", tr("a_err")));
}

export function init(container, context) {
  ctx = context;
  root = container;
  tr = ctx.tr;
  ctx.addStrings({
    en: {
      a_stats: "Database", a_users: "All users", a_pipeline: "Pipeline debug",
      a_msg_id: "Message id", a_trace: "Trace", a_err: "Request failed.",
      a_tbl: "Table", a_rows: "Rows", a_name: "Name", a_facts: "Facts", a_msgs: "Messages",
      a_msg: "Message", a_extracted: "Facts extracted", a_used: "Summaries citing them",
      a_none: "None.", a_notfound: "No such message.",
      a_dryrun: "Dry-run playground", a_dry_msg: "Test message", a_dry_nouser: "(no user context)",
      a_dry_go: "Test Extraction", a_dry_running: "Running...", a_dry_nothing_saved: "Nothing was saved.",
      a_dry_system: "System Prompt", a_dry_content: "User Content", a_dry_llm: "LLM Response",
      a_dry_parsed: "Parsed Ops", a_dry_would_save: "Would save", a_dry_ops: "Parsed result"
    },
    te: {
      a_stats: "డేటాబేస్", a_users: "అందరు వినియోగదారులు", a_pipeline: "పైప్‌లైన్ డీబగ్",
      a_msg_id: "సందేశం ఐడి", a_trace: "ట్రేస్", a_err: "అభ్యర్థన విఫలమైంది.",
      a_tbl: "పట్టిక", a_rows: "వరుసలు", a_name: "పేరు", a_facts: "వాస్తవాలు", a_msgs: "సందేశాలు",
      a_msg: "సందేశం", a_extracted: "సేకరించిన వాస్తవాలు", a_used: "వాటిని ఉదహరించే సారాంశాలు",
      a_none: "ఏమీ లేదు.", a_notfound: "ఆ సందేశం లేదు.",
      a_dryrun: "డ్రై-రన్ ప్లేగ్రౌండ్", a_dry_msg: "పరీక్ష సందేశం", a_dry_nouser: "(వినియోగదారు సందర్భం లేదు)",
      a_dry_go: "సేకరణను పరీక్షించు", a_dry_running: "నడుస్తోంది...", a_dry_nothing_saved: "ఏమీ సేవ్ చేయబడలేదు.",
      a_dry_system: "సిస్టమ్ ప్రాంప్ట్", a_dry_content: "వినియోగదారు కంటెంట్", a_dry_llm: "LLM స్పందన",
      a_dry_parsed: "విశ్లేషించిన ఆప్స్", a_dry_would_save: "సేవ్ అయ్యేవి", a_dry_ops: "విశ్లేషించిన ఫలితం"
    }
  });
  root.classList.add("admin");
  panels.stats = panel(tr("a_stats"));
  panels.users = panel(tr("a_users"));
  panels.pipe = panel(tr("a_pipeline"));
  buildPipeline(panels.pipe);
  panels.dry = panel(tr("a_dryrun"));
  panels.dry.classList.add("dryrun");
  buildDryrun(panels.dry);
  [panels.stats, panels.users, panels.pipe, panels.dry].forEach(function (p) { root.appendChild(p); });
  return Promise.resolve();
}

function section(title, content) {
  var d = el("details");
  d.appendChild(el("summary", "", title));
  d.appendChild(content);
  return d;
}

function pretty(v) { return el("pre", "", JSON.stringify(v, null, 2)); }

/* Dry run: real LLM call, nothing is saved. */
function buildDryrun(p) {
  var form = el("form");
  var text = el("textarea");
  text.placeholder = tr("a_dry_msg");
  var sel = el("select");
  var none = el("option", "", tr("a_dry_nouser"));
  none.value = "";
  sel.appendChild(none);
  adminGet("/api/v1/admin/users").then(function (d) {
    (d.users || []).forEach(function (u) {
      var o = el("option", "", u.name + " (#" + u.user_id + ")");
      o.value = String(u.user_id);
      sel.appendChild(o);
    });
  }).catch(function () {});
  var go = el("button", "primary", tr("a_dry_go"));
  go.type = "submit";
  var out = el("div");
  form.appendChild(text);
  form.appendChild(sel);
  form.appendChild(go);
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var msg = text.value.trim();
    if (!msg) { return; }
    var body = { text: msg };
    if (sel.value) { body.user_id = parseInt(sel.value, 10); }
    out.textContent = "";
    out.appendChild(el("p", "empty", tr("a_dry_running")));
    go.disabled = true;
    ctx.api("POST", "/api/v1/admin/dryrun", body, undefined, ctx.adminHeaders()).then(function (r) {
      out.textContent = "";
      out.appendChild(el("p", "", tr("a_dry_nothing_saved")));
      out.appendChild(section(tr("a_dry_system"), el("pre", "", r.system_prompt)));
      out.appendChild(section(tr("a_dry_content"), el("pre", "", r.user_content)));
      out.appendChild(section(tr("a_dry_llm"), pretty(r.llm_response)));
      var parsed = el("div");
      parsed.appendChild(el("p", "", (r.parsed && r.parsed.reply) || ""));
      parsed.appendChild(el("h3", "", tr("a_dry_would_save")));
      if (!r.would_save.length) { parsed.appendChild(el("p", "empty", tr("a_none"))); }
      r.would_save.forEach(function (o) {
        var row = el("div", "row");
        row.appendChild(el("span", "grow", o.text));
        row.appendChild(el("span", "tag", o.kind || o.op));
        (o.tag || []).forEach(function (t) { row.appendChild(el("span", "tag", t)); });
        row.appendChild(el("span", "tag " + o.visibility, o.visibility));
        parsed.appendChild(row);
      });
      parsed.appendChild(el("h3", "", tr("a_dry_ops")));
      parsed.appendChild(pretty(r.parsed));
      var det = section(tr("a_dry_parsed"), parsed);
      det.open = true;
      out.appendChild(det);
    }).catch(function (err) {
      out.textContent = "";
      fail(err, out);
    }).then(function () { go.disabled = false; });
  });
  p.appendChild(form);
  p.appendChild(out);
}

export function show() {
  var mySeq = ++seq;
  adminGet("/api/v1/admin/stats").then(function (s) {
    if (mySeq !== seq) { return; }
    reset(panels.stats, tr("a_stats"));
    var names = Object.keys(s.tables || {});
    panels.stats.appendChild(table([tr("a_tbl"), tr("a_rows")],
      names.map(function (n) { return [n, s.tables[n] < 0 ? "-" : s.tables[n]]; })));
  }).catch(function (err) { fail(err, panels.stats); });
  adminGet("/api/v1/admin/users").then(function (d) {
    if (mySeq !== seq) { return; }
    reset(panels.users, tr("a_users"));
    panels.users.appendChild(table(
      ["id", tr("a_name"), "lang", "mode", tr("a_facts"), tr("a_msgs")],
      (d.users || []).map(function (u) {
        return [u.user_id, u.name, u.lang, u.mode, u.facts, u.messages];
      })));
  }).catch(function (err) { fail(err, panels.users); });
}

export function hide() { seq++; }

export function destroy() {
  seq++;
  panels = {};
  if (root) { root.textContent = ""; }
}

function reset(p, title) {
  p.textContent = "";
  p.appendChild(el("h2", "", title));
}

/* message -> facts extracted from it -> summaries that cite those facts */
function buildPipeline(p) {
  var form = el("form", "inline-form");
  var input = el("input");
  input.type = "number";
  input.min = "1";
  input.placeholder = tr("a_msg_id");
  var go = el("button", "primary", tr("a_trace"));
  go.type = "submit";
  form.appendChild(input);
  form.appendChild(go);
  var out = el("div");
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var id = parseInt(input.value, 10);
    if (!id || id < 1) { return; }
    out.textContent = "";
    adminGet("/api/v1/admin/pipeline/" + id).then(function (t) {
      out.appendChild(el("h3", "", tr("a_msg") + " #" + t.message.id + " (" + t.message.role + ", user " + t.message.user_id + ")"));
      out.appendChild(el("pre", "", t.message.content));
      out.appendChild(el("h3", "", tr("a_extracted")));
      if (!t.facts.length) { out.appendChild(el("p", "empty", tr("a_none"))); }
      t.facts.forEach(function (f) {
        var r = el("div", "row");
        r.appendChild(el("span", "grow", f.text));
        r.appendChild(el("span", "tag", f.kind));
        r.appendChild(el("span", "tag " + f.visibility, f.visibility));
        out.appendChild(r);
      });
      out.appendChild(el("h3", "", tr("a_used")));
      if (!t.summaries.length) { out.appendChild(el("p", "empty", tr("a_none"))); }
      t.summaries.forEach(function (s) {
        var r = el("div", "row");
        r.appendChild(el("span", "grow", s.text));
        r.appendChild(el("span", "tag", s.kind));
        out.appendChild(r);
      });
    }).catch(function (err) {
      if (err instanceof ctx.ApiError && err.status === 404) {
        out.appendChild(el("p", "empty", tr("a_notfound")));
      } else { fail(err, out); }
    });
  });
  p.appendChild(form);
  p.appendChild(out);
}
