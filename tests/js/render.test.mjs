import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { renderMarkup } = require("../../pairtner/web/render.js");

function makeDoc() {
  const created = [];
  return {
    created,
    createElement(tag) {
      const n = { tag, children: [] };
      n.appendChild = (c) => { n.children.push(c); return c; };
      created.push(n);
      return n;
    },
    createTextNode(text) { const n = { text }; created.push(n); return n; },
  };
}

function render(markup) {
  const doc = makeDoc();
  const root = doc.createElement("root");
  renderMarkup(markup, root, doc);
  return { doc, root };
}

const tags = (doc) => doc.created.filter((n) => n.tag && n.tag !== "root").map((n) => n.tag);
const text = (node) => node.text !== undefined ? node.text : node.children.map(text).join("");

test("whitelisted tags become elements", () => {
  const { doc, root } = render("a <b>bold</b> and <i>it</i> <u>u</u> <code>c</code> <pre>p</pre>");
  assert.deepEqual(tags(doc), ["b", "i", "u", "code", "pre"]);
  assert.equal(text(root), "a bold and it u c p");
});

test("script tag stays literal text", () => {
  const { doc, root } = render("<script>alert(1)</script>");
  assert.deepEqual(tags(doc), []);
  assert.equal(text(root), "<script>alert(1)</script>");
});

test("img with onerror stays literal text", () => {
  const { doc, root } = render("<img src=x onerror=alert(1)>");
  assert.deepEqual(tags(doc), []);
  assert.equal(text(root), "<img src=x onerror=alert(1)>");
});

test("attributes on allowed tags are not honoured", () => {
  const { doc, root } = render("<b onclick=x>hi</b>");
  assert.deepEqual(tags(doc), []);
  assert.equal(text(root), "<b onclick=x>hi</b>");
});

test("entity round trip decodes once, in text nodes only", () => {
  const { doc, root } = render("&lt;script&gt; &amp;lt; &quot;q&quot; &#39;s&#39;");
  assert.deepEqual(tags(doc), []);
  assert.equal(text(root), "<script> &lt; \"q\" 's'");
});

test("entities inside allowed tags", () => {
  const { doc, root } = render("<b>&lt;i&gt;</b>");
  assert.deepEqual(tags(doc), ["b"]);
  assert.equal(text(root), "<i>");
});

test("nested tags nest", () => {
  const { doc, root } = render("<b>x<i>y</i>z</b>");
  assert.deepEqual(tags(doc), ["b", "i"]);
  const b = root.children[0];
  assert.equal(b.tag, "b");
  assert.equal(b.children[1].tag, "i");
  assert.equal(text(root), "xyz");
});

test("unbalanced open tags are closed implicitly", () => {
  const { root } = render("<b>one <i>two");
  assert.equal(root.children[0].tag, "b");
  assert.equal(root.children[0].children[1].tag, "i");
  assert.equal(text(root), "one two");
});

test("stray closing tag stays literal text", () => {
  const { doc, root } = render("a</b>b");
  assert.deepEqual(tags(doc), []);
  assert.equal(text(root), "a</b>b");
});

test("mismatched closing tag closes inner elements", () => {
  const { root } = render("<b>x<i>y</b>z");
  assert.equal(root.children.length, 2);
  assert.equal(root.children[1].text, "z");
});

test("very long input is handled linearly", () => {
  const big = "<b>".repeat(5000) + "x".repeat(200000);
  const t0 = Date.now();
  const { doc } = render(big);
  assert.equal(tags(doc).length, 5000);
  assert.ok(Date.now() - t0 < 2000);
});

test("javascript: text stays text", () => {
  const { doc, root } = render("javascript:alert(1) <b>x</b>");
  assert.deepEqual(tags(doc), ["b"]);
  assert.equal(text(root), "javascript:alert(1) x");
});

test("null and empty input", () => {
  assert.equal(render("").root.children.length, 0);
  assert.equal(render(null).root.children.length, 0);
});
