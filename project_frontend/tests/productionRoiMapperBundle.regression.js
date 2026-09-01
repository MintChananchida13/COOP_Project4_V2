const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const bundlePath = path.resolve(__dirname, "../.next/server/app/page.js");
const bundle = fs.readFileSync(bundlePath, "utf8");
const marker = "`template-field:${";
const markerIndex = bundle.indexOf(marker);

assert.notEqual(markerIndex, -1, "Production page bundle does not contain the template ROI mapper marker.");

const mapperStart = bundle.lastIndexOf("async function", markerIndex);
const mapperEnd = bundle.indexOf("async function", markerIndex + marker.length);
const mapperChunk = bundle.slice(mapperStart, mapperEnd === -1 ? markerIndex + 2500 : mapperEnd);

assert.match(mapperChunk, /for\(let|for\(const|for\(/, "Template ROI mapper should compile to a loop, not a chained map callback.");
assert.doesNotMatch(
  mapperChunk,
  /([a-zA-Z_$][\w$]*)=.+?;\s*let\s+\1\b/,
  "Template ROI mapper bundle assigns to a block-scoped variable before its let declaration."
);
assert.doesNotMatch(
  mapperChunk,
  /\.map\([^)]*=>\{[^}]*let\s+[a-zA-Z_$][\w$]*,/,
  "Template ROI mapper bundle should not use the old chained map callback shape."
);
