// Lets `node --test tests/js/` work on Node versions that treat a directory
// argument as a module path: load every *.test.mjs here.
const fs = require("node:fs");
const path = require("node:path");
for (const f of fs.readdirSync(__dirname).filter((n) => n.endsWith(".test.mjs")).sort()) {
  import(path.join(__dirname, f));
}
