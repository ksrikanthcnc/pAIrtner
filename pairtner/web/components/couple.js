/* Couple view: shared facts, nudges for you, open conflicts, relay queue.
 * Mounted by dashboard.js; usable standalone. Each list is fetched lazily
 * when its section is opened. The server returns 404 unless the signed-in
 * user belongs to the relationship, so the id here is never trusted.
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var tr = null;
var rels = [];
var current = "";
var seq = 0;
var body = null;

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}

export function init(container, context) {
  ctx = context;
  root = container;
  tr = ctx.tr;
  ctx.addStrings({
    en: {
      c_title: "Couple view", c_pick: "Relationship", c_facts: "Shared facts",
      c_nudges: "Pending nudges", c_conflicts: "Open conflicts", c_relays: "Relay queue",
      c_none: "Nothing here.", c_err: "Could not load this section.",
      c_sent: "sent", c_received: "received", c_ack: "acknowledged"
    },
    te: {
      c_title: "జంట వీక్షణ", c_pick: "సంబంధం", c_facts: "పంచుకున్న వాస్తవాలు",
      c_nudges: "పెండింగ్ సూచనలు", c_conflicts: "తెరిచి ఉన్న విభేదాలు", c_relays: "రిలే క్యూ",
      c_none: "ఇక్కడ ఏమీ లేదు.", c_err: "ఈ విభాగాన్ని లోడ్ చేయలేకపోయాము.",
      c_sent: "పంపినవి", c_received: "అందినవి", c_ack: "అంగీకరించబడింది"
    }
  });
  root.classList.add("couple");
  return Promise.resolve();
}

/* rels: [{id, rel_type, partner_name}], selected: id or "" (let the user pick). */
export function setRelationships(list, selected) {
  rels = list || [];
  var ids = rels.map(function (r) { return r.id; });
  if (selected && ids.indexOf(selected) !== -1) { current = selected; }
  else if (ids.indexOf(current) === -1) { current = ids[0] || ""; }
  render(!!selected);
}

export function show() { if (root) { root.hidden = false; } }
export function hide() { if (root) { root.hidden = true; } }

export function destroy() {
  seq++;
  rels = [];
  current = "";
  body = null;
  if (root) { root.textContent = ""; }
}

function label(r) {
  var kind = (r.rel_type || "").replace("_", " ");
  return r.partner_name ? kind + ": " + r.partner_name : kind;
}

function render(locked) {
  seq++;
  root.textContent = "";
  if (!rels.length) { return; }
  var panel = el("section", "panel");
  panel.appendChild(el("h2", "", tr("c_title")));
  if (rels.length > 1 && !locked) {
    var row = el("label");
    row.appendChild(el("span", "", tr("c_pick")));
    var sel = el("select");
    rels.forEach(function (r) {
      var o = el("option", "", label(r));
      o.value = r.id;
      o.selected = r.id === current;
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () { current = sel.value; render(false); });
    row.appendChild(sel);
    panel.appendChild(row);
  } else if (rels.length > 1) {
    var cur = rels.filter(function (r) { return r.id === current; })[0];
    if (cur) { panel.appendChild(el("p", "meta", label(cur))); }
  }
  body = el("div");
  panel.appendChild(body);
  root.appendChild(panel);
  [
    ["facts", "c_facts", factRow, "facts"],
    ["nudges", "c_nudges", nudgeRow, "nudges"],
    ["conflicts", "c_conflicts", conflictRow, "conflicts"],
    ["relays", "c_relays", relayRow, "relays"]
  ].forEach(function (s) { body.appendChild(lazySection(s[0], s[1], s[2], s[3])); });
}

function lazySection(path, titleKey, rowFn, key) {
  var d = el("details");
  d.appendChild(el("summary", "", tr(titleKey)));
  var inner = el("div");
  d.appendChild(inner);
  var loaded = false;
  var mySeq = seq;
  var relId = current;
  d.addEventListener("toggle", function () {
    if (!d.open || loaded) { return; }
    loaded = true;
    ctx.api("GET", "/api/v1/couple/" + encodeURIComponent(relId) + "/" + path)
      .then(function (data) {
        if (mySeq !== seq) { return; }
        var items = data[key] || [];
        if (!items.length) { inner.appendChild(el("p", "empty", tr("c_none"))); }
        items.forEach(function (it) { inner.appendChild(rowFn(it)); });
      })
      .catch(function (err) {
        if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
        loaded = false;
        inner.appendChild(el("p", "error", tr("c_err")));
      });
  });
  return d;
}

function factRow(f) {
  var row = el("div", "row");
  row.appendChild(el("span", "grow", f.text));
  row.appendChild(el("span", "meta", ctx.formatTime(f.shared_at || f.created_at)));
  return row;
}

function nudgeRow(n) {
  var row = el("div", "row");
  row.appendChild(el("span", "grow", n.nudge_suggestion));
  if (n.nudge_type) { row.appendChild(el("span", "tag", n.nudge_type)); }
  return row;
}

function conflictRow(c) {
  var row = el("div", "row");
  row.appendChild(el("span", "grow", c.theme));
  row.appendChild(el("span", "tag", c.status));
  row.appendChild(el("span", "meta", ctx.formatTime(c.opened_at)));
  return row;
}

function relayRow(r) {
  var row = el("div", "row");
  row.appendChild(el("span", "grow", r.text));
  row.appendChild(el("span", "tag", tr(r.direction === "sent" ? "c_sent" : "c_received") + " · " + r.status));
  if (r.acknowledged_at) { row.appendChild(el("span", "tag", tr("c_ack"))); }
  row.appendChild(el("span", "meta", ctx.formatTime(r.sent_at || r.created_at)));
  return row;
}
