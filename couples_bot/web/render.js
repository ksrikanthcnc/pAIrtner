/* Safe renderer for bot markup (plan D5). Only b, i, u, code, pre become
 * elements; everything else is a text node. No HTML parsing is used. */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) { module.exports = api; }
  if (root) { root.PairtnerRender = api; }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var TAG_RE = /<(\/?)(b|i|u|code|pre)>/gi;
  var ENTITY_RE = /&(lt|gt|amp|quot|#39);/g;
  var ENTITIES = { lt: "<", gt: ">", amp: "&", quot: "\"", "#39": "'" };

  function decode(text) {
    return text.replace(ENTITY_RE, function (_, name) { return ENTITIES[name]; });
  }

  function renderMarkup(markup, container, doc) {
    var stack = [container];
    var names = [];
    var pos = 0;
    var m;

    function text(str) {
      if (str) { stack[stack.length - 1].appendChild(doc.createTextNode(decode(str))); }
    }

    TAG_RE.lastIndex = 0;
    var src = String(markup == null ? "" : markup);
    while ((m = TAG_RE.exec(src)) !== null) {
      var name = m[2].toLowerCase();
      if (m[1]) {
        var at = names.lastIndexOf(name);
        if (at === -1) { continue; } /* stray closing tag stays literal text */
        text(src.slice(pos, m.index));
        pos = m.index + m[0].length;
        names.length = at;
        stack.length = at + 1;
      } else {
        text(src.slice(pos, m.index));
        pos = m.index + m[0].length;
        var el = doc.createElement(name);
        stack[stack.length - 1].appendChild(el);
        stack.push(el);
        names.push(name);
      }
    }
    text(src.slice(pos)); /* unbalanced tags are closed implicitly */
    return container;
  }

  return { renderMarkup: renderMarkup, decode: decode };
});
