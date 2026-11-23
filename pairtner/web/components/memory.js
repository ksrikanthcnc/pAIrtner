/* Memory view: browse, search, edit, promote and delete the user's facts.
 * Component contract: init(container, ctx), show(), hide(), destroy(). */

var ctx = null;
var root = null;
var tr = null;
var els = {};
var facts = [];
var topics = [];
var topicFilter = "";
var grouped = false;
var searchTerm = "";
var expandedId = null;
var editingId = null;
var loadSeq = 0;
var searchTimer = null;

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) { n.className = cls; }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}

function button(label, cls, onClick) {
  var b = el("button", cls || "", label);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function tagsOf(fact) {
  var raw = fact.tag;
  if (!raw) { return []; }
  try {
    var parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) { return parsed.map(String); }
  } catch (e) { /* plain string tag */ }
  return [String(raw)];
}

function stat(label, value) {
  var s = el("div", "mem-stat");
  s.appendChild(el("b", "", String(value)));
  s.appendChild(el("span", "meta", label));
  return s;
}

export function init(container, context) {
  ctx = context;
  root = container;
  tr = ctx.tr;
  ctx.addStrings({
    en: {
      nav_memory: "Memory",
      m_total: "Total", m_core: "Core", m_working: "Working", m_episode: "Episodes",
      m_search_ph: "Search memory", m_group: "Group by topic", m_all: "All topics",
      m_none: "No facts found.", m_source: "Source", m_created: "Created", m_updated: "Updated",
      m_cited: "Cited", m_edit: "Edit", m_save: "Save", m_cancel: "Cancel", m_delete: "Delete",
      m_promote: "Promote to core", m_confirm: "Delete this fact permanently?",
      m_untagged: "Untagged", m_err: "Could not load memory.", m_act_err: "That change was not accepted.",
      m_kind_core: "core", m_kind_working: "working", m_kind_episode: "episode",
      m_st_active: "active", m_st_outdated: "outdated", m_st_archived: "archived",
      m_src_msg: "From a chat message", m_src_none: "Not recorded"
    },
    te: {
      nav_memory: "జ్ఞాపకం",
      m_total: "మొత్తం", m_core: "ప్రధాన", m_working: "పని", m_episode: "ఘట్టాలు",
      m_search_ph: "జ్ఞాపకంలో వెతకండి", m_group: "అంశం వారీగా", m_all: "అన్ని అంశాలు",
      m_none: "వాస్తవాలు కనిపించలేదు.", m_source: "మూలం", m_created: "సృష్టించబడింది", m_updated: "నవీకరించబడింది",
      m_cited: "ఉదహరించబడింది", m_edit: "సవరించు", m_save: "సేవ్", m_cancel: "రద్దు", m_delete: "తొలగించు",
      m_promote: "ప్రధానంగా మార్చు", m_confirm: "ఈ వాస్తవాన్ని శాశ్వతంగా తొలగించాలా?",
      m_untagged: "ట్యాగ్ లేదు", m_err: "జ్ఞాపకాన్ని లోడ్ చేయలేకపోయాము.", m_act_err: "ఆ మార్పు అంగీకరించబడలేదు.",
      m_kind_core: "ప్రధాన", m_kind_working: "పని", m_kind_episode: "ఘట్టం",
      m_st_active: "క్రియాశీలం", m_st_outdated: "పాతది", m_st_archived: "ఆర్కైవ్",
      m_src_msg: "చాట్ సందేశం నుండి", m_src_none: "నమోదు కాలేదు"
    }
  });
  root.classList.add("memory");

  els.stats = el("section", "panel mem-stats");
  var bar = el("section", "panel");
  els.search = el("input");
  els.search.type = "search";
  els.search.setAttribute("placeholder", tr("m_search_ph"));
  els.search.setAttribute("aria-label", tr("m_search_ph"));
  els.search.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      searchTerm = els.search.value.trim();
      load();
    }, 300);
  });
  bar.appendChild(els.search);
  var groupLabel = el("label", "mem-group");
  els.group = el("input");
  els.group.type = "checkbox";
  els.group.addEventListener("change", function () { grouped = els.group.checked; renderList(); });
  groupLabel.appendChild(els.group);
  groupLabel.appendChild(el("span", "", tr("m_group")));
  bar.appendChild(groupLabel);
  els.chips = el("div", "mem-chips");
  bar.appendChild(els.chips);
  els.error = el("p", "error pad");
  els.error.hidden = true;
  els.list = el("section", "mem-list");
  [els.stats, bar, els.error, els.list].forEach(function (n) { root.appendChild(n); });
  return Promise.resolve();
}

export function show() { load(); }

export function hide() { loadSeq++; }

export function destroy() {
  loadSeq++;
  clearTimeout(searchTimer);
  els = {};
  facts = [];
  if (root) { root.textContent = ""; }
}

function load() {
  var seq = ++loadSeq;
  var factsReq = searchTerm
    ? ctx.api("GET", "/api/v1/memory/search" + ctx.qs({ q: searchTerm, limit: 50 }))
    : ctx.api("GET", "/api/v1/memory" + ctx.qs({ limit: 200 }));
  Promise.all([
    factsReq,
    ctx.api("GET", "/api/v1/memory/stats"),
    ctx.api("GET", "/api/v1/topics")
  ]).then(function (res) {
    if (seq !== loadSeq) { return; }
    facts = res[0].facts || [];
    topics = res[2].topics || [];
    els.error.hidden = true;
    renderStats(res[1]);
    renderChips();
    renderList();
  }).catch(function (err) {
    if (seq !== loadSeq) { return; }
    if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
    els.error.textContent = tr("m_err");
    els.error.hidden = false;
  });
}

function renderStats(s) {
  var byKind = s.by_kind || {};
  els.stats.textContent = "";
  els.stats.appendChild(stat(tr("m_total"), s.total || 0));
  els.stats.appendChild(stat(tr("m_core"), byKind.core || 0));
  els.stats.appendChild(stat(tr("m_working"), byKind.working || 0));
  els.stats.appendChild(stat(tr("m_episode"), byKind.episode || 0));
}

function renderChips() {
  els.chips.textContent = "";
  if (!topics.length) { return; }
  els.chips.appendChild(button(tr("m_all"), "chip" + (topicFilter ? "" : " on"), function () {
    topicFilter = "";
    renderChips();
    renderList();
  }));
  topics.forEach(function (t) {
    var on = topicFilter === t.topic;
    els.chips.appendChild(button(t.topic + " (" + t.mention_count + ")", "chip" + (on ? " on" : ""), function () {
      topicFilter = on ? "" : t.topic;
      renderChips();
      renderList();
    }));
  });
}

function visibleFacts() {
  if (!topicFilter) { return facts; }
  return facts.filter(function (f) { return tagsOf(f).indexOf(topicFilter) >= 0; });
}

function renderList() {
  els.list.textContent = "";
  var items = visibleFacts();
  if (!items.length) {
    els.list.appendChild(el("p", "meta pad", tr("m_none")));
    return;
  }
  if (!grouped) {
    items.forEach(function (f) { els.list.appendChild(card(f)); });
    return;
  }
  var groups = {};
  var order = [];
  items.forEach(function (f) {
    var tags = tagsOf(f);
    var key = tags.length ? tags[0] : tr("m_untagged");
    if (!groups[key]) { groups[key] = []; order.push(key); }
    groups[key].push(f);
  });
  order.forEach(function (key) {
    els.list.appendChild(el("h3", "mem-group-h", key + " (" + groups[key].length + ")"));
    groups[key].forEach(function (f) { els.list.appendChild(card(f)); });
  });
}

function badge(kind) {
  var k = ["core", "working", "episode"].indexOf(kind) >= 0 ? kind : "working";
  return el("span", "mem-kind mem-kind-" + k, tr("m_kind_" + k));
}

function statusBadge(status) {
  var s = ["active", "outdated", "archived"].indexOf(status) >= 0 ? status : "active";
  return el("span", "mem-status mem-status-" + s, tr("m_st_" + s));
}

function card(f) {
  var open = expandedId === f.id;
  var c = el("article", "mem-card" + (open ? " open" : ""));
  var head = el("div", "mem-head");
  head.setAttribute("role", "button");
  head.setAttribute("tabindex", "0");
  head.setAttribute("aria-expanded", open ? "true" : "false");
  head.appendChild(badge(f.kind));
  head.appendChild(statusBadge(f.status));
  head.appendChild(el("p", "mem-text" + (open ? "" : " clamp"), f.text));
  var toggle = function () {
    expandedId = open ? null : f.id;
    editingId = null;
    renderList();
  };
  head.addEventListener("click", toggle);
  head.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
  });
  c.appendChild(head);
  if (open) { c.appendChild(detail(f)); }
  return c;
}

function metaLine(label, value) {
  return el("p", "meta", label + ": " + value);
}

function detail(f) {
  var d = el("div", "mem-detail");
  var err = el("p", "error");
  err.hidden = true;
  if (editingId === f.id) {
    var ta = el("textarea", "mem-edit");
    ta.value = f.text;
    ta.rows = 4;
    d.appendChild(ta);
    var eb = el("div", "btns");
    eb.appendChild(button(tr("m_save"), "primary", function () {
      var text = ta.value.trim();
      if (!text) { return; }
      act("PUT", f, { text: text }, err);
    }));
    eb.appendChild(button(tr("m_cancel"), "", function () { editingId = null; renderList(); }));
    d.appendChild(eb);
  }
  var tags = tagsOf(f);
  if (tags.length) { d.appendChild(metaLine("#", tags.join(", "))); }
  d.appendChild(metaLine(tr("m_source"), f.source_message_id ? tr("m_src_msg") : tr("m_src_none")));
  d.appendChild(metaLine(tr("m_created"), ctx.formatTime(f.created_at)));
  d.appendChild(metaLine(tr("m_updated"), ctx.formatTime(f.updated_at)));
  d.appendChild(metaLine(tr("m_cited"), String(f.citation_count || 0) +
    (f.last_cited_at ? " (" + ctx.formatTime(f.last_cited_at) + ")" : "")));
  var b = el("div", "btns");
  if (editingId !== f.id) {
    b.appendChild(button(tr("m_edit"), "", function () { editingId = f.id; renderList(); }));
  }
  if (f.kind !== "core") {
    b.appendChild(button("\u2B50 " + tr("m_promote"), "", function () { act("PUT", f, { kind: "core" }, err); }));
  }
  b.appendChild(button(tr("m_delete"), "danger", function () {
    if (window.confirm(tr("m_confirm"))) { act("DELETE", f, undefined, err); }
  }));
  d.appendChild(b);
  d.appendChild(err);
  return d;
}

function act(method, f, body, errEl) {
  ctx.api(method, "/api/v1/facts/" + encodeURIComponent(f.id), body).then(function () {
    editingId = null;
    if (method === "DELETE") { expandedId = null; }
    load();
  }).catch(function (err) {
    if (err instanceof ctx.ApiError && err.status === 401) { ctx.signOut(); return; }
    errEl.textContent = tr("m_act_err");
    errEl.hidden = false;
  });
}
