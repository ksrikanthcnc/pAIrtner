/* "My Data" view: the signed-in user's own stats, topics, facts and summaries.
 * Every request goes through the authenticated api() and the server only
 * returns that user's rows. Couple sections are delegated to couple.js.
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var tr = null;
var relFilter = "";       /* "" = all, "none" = solo only, else relationship id */
var relationships = [];
var coupleMod = null;
var couplePromise = null;
var loadSeq = 0;
var els = {};

var SUMMARY_PERIODS = ["weekly", "monthly", "quarterly", "yearly"];

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}

function empty(msg) { return el("p", "empty", msg); }

function section(title) {
  var p = el("section", "panel");
  p.appendChild(el("h2", "", title));
  return p;
}

function when(iso) { return iso ? ctx.formatTime(iso) : ""; }

export function init(container, context) {
  ctx = context;
  root = container;
  tr = ctx.tr;
  ctx.addStrings({
    en: {
      d_filter: "Relationship", d_all: "All", d_solo: "Solo",
      d_stats: "Quick stats", d_facts: "Facts", d_topics: "Topics", d_msgs: "Messages", d_sums: "Summaries",
      d_topics_h: "Topics", d_recent: "Recent facts", d_summaries: "Summaries",
      d_none_topics: "No topics yet.", d_none_facts: "No facts yet.", d_none_sums: "No summaries yet.",
      d_mentions: "facts", d_last: "last", d_source_msg: "Extracted from message", d_core: "core",
      d_private: "private", d_shared: "shared", d_source_facts: "Source facts",
      d_period_weekly: "Weekly", d_period_monthly: "Monthly",
      d_period_quarterly: "Quarterly", d_period_yearly: "Yearly",
      d_couple: "Couple view", d_load_err: "Could not load this section."
    },
    te: {
      d_filter: "సంబంధం", d_all: "అన్నీ", d_solo: "ఒంటరిగా",
      d_stats: "త్వరిత గణాంకాలు", d_facts: "వాస్తవాలు", d_topics: "అంశాలు", d_msgs: "సందేశాలు", d_sums: "సారాంశాలు",
      d_topics_h: "అంశాలు", d_recent: "ఇటీవలి వాస్తవాలు", d_summaries: "సారాంశాలు",
      d_none_topics: "ఇంకా అంశాలు లేవు.", d_none_facts: "ఇంకా వాస్తవాలు లేవు.", d_none_sums: "ఇంకా సారాంశాలు లేవు.",
      d_mentions: "వాస్తవాలు", d_last: "చివరిగా", d_source_msg: "సందేశం నుండి సేకరించబడింది", d_core: "ప్రధాన",
      d_private: "ప్రైవేట్", d_shared: "పంచుకున్నవి", d_source_facts: "మూల వాస్తవాలు",
      d_period_weekly: "వారపు", d_period_monthly: "నెలవారీ",
      d_period_quarterly: "త్రైమాసిక", d_period_yearly: "వార్షిక",
      d_couple: "జంట వీక్షణ", d_load_err: "ఈ విభాగాన్ని లోడ్ చేయలేకపోయాము."
    }
  });
  root.classList.add("dashboard");
  els.filter = el("section", "panel");
  els.filter.hidden = true;
  els.stats = section(tr("d_stats"));
  els.topics = section(tr("d_topics_h"));
  els.facts = section(tr("d_recent"));
  els.sums = section(tr("d_summaries"));
  els.couple = el("div", "couple-slot");
  [els.filter, els.stats, els.topics, els.facts, els.sums, els.couple].forEach(function (n) {
    root.appendChild(n);
  });
  return Promise.resolve();
}

export function show() { refresh(); }

export function hide() { loadSeq++; }

export function destroy() {
  loadSeq++;
  if (coupleMod && coupleMod.destroy) { coupleMod.destroy(); }
  coupleMod = null;
  couplePromise = null;
  relFilter = "";
  relationships = [];
  els = {};
  if (root) { root.textContent = ""; }
}

/* ---- data loading ---- */

function failed(panel, err) {
  if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
  var old = panel.querySelector(".err-note");
  if (old) { old.remove(); }
  var p = el("p", "error err-note", tr("d_load_err"));
  panel.appendChild(p);
}

function resetPanel(panel, title) {
  panel.textContent = "";
  panel.appendChild(el("h2", "", title));
}

function refresh() {
  var seq = ++loadSeq;
  ctx.api("GET", "/api/v1/relationships").then(function (data) {
    if (seq !== loadSeq) { return; }
    relationships = data.relationships || [];
    if (relFilter && relFilter !== "none" &&
        !relationships.some(function (r) { return r.id === relFilter; })) {
      relFilter = "";
    }
    renderFilter();
    loadAll(seq);
    mountCouple();
  }).catch(function (err) { failed(els.stats, err); });
}

function loadAll(seq) {
  var rel = relFilter;
  ctx.api("GET", "/api/v1/dashboard/stats" + ctx.qs({ relationship_id: rel }))
    .then(function (s) { if (seq === loadSeq) { renderStats(s); } })
    .catch(function (err) { failed(els.stats, err); });
  ctx.api("GET", "/api/v1/topics" + ctx.qs({ relationship_id: rel, limit: 30 }))
    .then(function (d) { if (seq === loadSeq) { renderTopics(d.topics || []); } })
    .catch(function (err) { failed(els.topics, err); });
  ctx.api("GET", "/api/v1/facts" + ctx.qs({
    relationship_id: rel, order: "desc", limit: 30, include_summaries: 0 }))
    .then(function (d) { if (seq === loadSeq) { renderFacts(d.facts || []); } })
    .catch(function (err) { failed(els.facts, err); });
  ctx.api("GET", "/api/v1/summaries" + ctx.qs({ relationship_id: rel, limit: 100 }))
    .then(function (d) { if (seq === loadSeq) { renderSummaries(d.summaries || []); } })
    .catch(function (err) { failed(els.sums, err); });
}

/* ---- relationship filter (only when there is more than solo) ---- */

function relLabel(r) {
  var kind = (r.rel_type || "").replace("_", " ");
  return r.partner_name ? kind + ": " + r.partner_name : kind;
}

function renderFilter() {
  els.filter.textContent = "";
  els.filter.hidden = relationships.length === 0;
  if (relationships.length === 0) { return; }
  var chips = el("div", "chips");
  chips.setAttribute("role", "group");
  chips.setAttribute("aria-label", tr("d_filter"));
  var options = [["", tr("d_all")], ["none", tr("d_solo")]].concat(
    relationships.map(function (r) { return [r.id, relLabel(r)]; }));
  options.forEach(function (o) {
    var b = el("button", "chip" + (o[0] === relFilter ? " on" : ""), o[1]);
    b.type = "button";
    b.setAttribute("aria-pressed", o[0] === relFilter ? "true" : "false");
    b.addEventListener("click", function () {
      relFilter = o[0];
      renderFilter();
      var seq = ++loadSeq;
      loadAll(seq);
      mountCouple();
    });
    chips.appendChild(b);
  });
  els.filter.appendChild(chips);
}

/* ---- sections ---- */

function renderStats(s) {
  resetPanel(els.stats, tr("d_stats"));
  var grid = el("div", "stats");
  [["d_facts", s.facts], ["d_topics", s.topics], ["d_msgs", s.messages], ["d_sums", s.summaries]]
    .forEach(function (pair) {
      var cell = el("div", "stat");
      cell.appendChild(el("b", "", String(pair[1] || 0)));
      cell.appendChild(el("span", "", tr(pair[0])));
      grid.appendChild(cell);
    });
  els.stats.appendChild(grid);
}

function renderTopics(topics) {
  resetPanel(els.topics, tr("d_topics_h"));
  if (!topics.length) { els.topics.appendChild(empty(tr("d_none_topics"))); return; }
  topics.forEach(function (t) {
    var d = el("details");
    var sum = el("summary", "", t.topic + " (" + t.mention_count + " " + tr("d_mentions") +
      ", " + tr("d_last") + ": " + when(t.last_discussed) + ")");
    d.appendChild(sum);
    var body = el("div");
    d.appendChild(body);
    var loaded = false;
    d.addEventListener("toggle", function () {
      if (!d.open || loaded) { return; }
      loaded = true;
      ctx.api("GET", "/api/v1/facts" + ctx.qs({
        tag: t.topic, relationship_id: relFilter, order: "desc", limit: 50, include_summaries: 0 }))
        .then(function (data) {
          if (!(data.facts || []).length) { body.appendChild(empty(tr("d_none_facts"))); }
          (data.facts || []).forEach(function (f) { body.appendChild(factRow(f, false)); });
        })
        .catch(function () { loaded = false; body.appendChild(el("p", "error", tr("d_load_err"))); });
    });
    els.topics.appendChild(d);
  });
}

function factRow(f, expandable) {
  var row = el("div", "row");
  row.appendChild(el("span", "grow", f.text));
  if (f.kind === "core") { row.appendChild(el("span", "tag", tr("d_core"))); }
  row.appendChild(el("span", "tag " + f.visibility, tr(f.visibility === "private" ? "d_private" : "d_shared")));
  row.appendChild(el("span", "meta", when(f.created_at)));
  if (!expandable) { return row; }
  var d = el("details");
  d.appendChild(el("summary", "", f.text));
  var body = el("div");
  var tags = [f.tag].filter(Boolean);
  body.appendChild(el("p", "meta", [f.kind, f.visibility].concat(tags).join(" · ") + " · " + when(f.created_at)));
  if (f.source_message_id) {
    body.appendChild(el("p", "meta", tr("d_source_msg") + " #" + f.source_message_id));
  }
  d.appendChild(body);
  return d;
}

function renderFacts(facts) {
  resetPanel(els.facts, tr("d_recent"));
  if (!facts.length) { els.facts.appendChild(empty(tr("d_none_facts"))); return; }
  facts.forEach(function (f) {
    var d = el("details");
    var sum = el("summary", "", f.text);
    d.appendChild(sum);
    var body = el("div");
    var chips = el("div", "chips");
    if (f.kind === "core") { chips.appendChild(el("span", "tag", tr("d_core"))); }
    chips.appendChild(el("span", "tag " + f.visibility, tr(f.visibility === "private" ? "d_private" : "d_shared")));
    if (f.tag) { chips.appendChild(el("span", "tag", String(f.tag).replace(/[\[\]"]/g, ""))); }
    body.appendChild(chips);
    body.appendChild(el("p", "meta", when(f.created_at)));
    if (f.source_message_id) {
      body.appendChild(el("p", "meta", tr("d_source_msg") + " #" + f.source_message_id));
    }
    d.appendChild(body);
    els.facts.appendChild(d);
  });
}

function renderSummaries(rows) {
  resetPanel(els.sums, tr("d_summaries"));
  if (!rows.length) { els.sums.appendChild(empty(tr("d_none_sums"))); return; }
  var periods = SUMMARY_PERIODS.slice();
  rows.forEach(function (r) { if (periods.indexOf(r.kind) === -1) { periods.push(r.kind); } });
  periods.forEach(function (p) {
    var group = rows.filter(function (r) { return r.kind === p; });
    if (!group.length) { return; }
    els.sums.appendChild(el("h3", "", tr("d_period_" + p) === "d_period_" + p ? p : tr("d_period_" + p)));
    group.slice(0, 8).forEach(function (r) { els.sums.appendChild(summaryItem(r)); });
  });
}

function summaryItem(r) {
  var d = el("details");
  d.appendChild(el("summary", "", (r.period_id || when(r.created_at)) + (r.tags.length ? " · " + r.tags.join(", ") : "")));
  var body = el("div");
  body.appendChild(el("p", "", r.text));
  d.appendChild(body);
  if (r.source_ids && r.source_ids.length) {
    var loaded = false;
    d.addEventListener("toggle", function () {
      if (!d.open || loaded) { return; }
      loaded = true;
      ctx.api("GET", "/api/v1/facts" + ctx.qs({ ids: r.source_ids.slice(0, 50).join(",") }))
        .then(function (data) {
          if (!(data.facts || []).length) { return; }
          body.appendChild(el("h3", "", tr("d_source_facts")));
          data.facts.forEach(function (f) { body.appendChild(factRow(f, false)); });
        })
        .catch(function () { loaded = false; });
    });
  }
  return d;
}

/* ---- couple section (separate, swappable component) ---- */

function mountCouple() {
  var rels = relationships;
  if (!rels.length) { els.couple.textContent = ""; return; }
  var selected = relFilter && relFilter !== "none" ? relFilter : "";
  if (!couplePromise) {
    couplePromise = ctx.loadModule("couple").then(function (mod) {
      return Promise.resolve(mod.init(els.couple, ctx)).then(function () { coupleMod = mod; return mod; });
    });
  }
  couplePromise.then(function (mod) { mod.setRelationships(rels, selected); mod.show(); })
    .catch(function () { couplePromise = null; els.couple.textContent = ""; });
}
