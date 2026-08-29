import assert from "node:assert/strict";
import { test } from "node:test";
import { codePathFor, isSupportedSource, mapPathFor, prosePathFor } from "../src/files";

test("supported source detection skips prose, unsupported suffixes and vendored dirs", () => {
  assert.equal(isSupportedSource("/w/src/a.py"), true);
  assert.equal(isSupportedSource("/w/src/a.py.prose"), false);
  assert.equal(isSupportedSource("/w/src/DIR.prose"), false);
  assert.equal(isSupportedSource("/w/src/notes.txt"), false);
  assert.equal(isSupportedSource("/w/node_modules/x/index.js"), false);
  assert.equal(isSupportedSource("/w/.venv/lib/x.py"), false);
  assert.equal(isSupportedSource("/w/.prose/a.py.map.json"), false);
});

test("sidecar path helpers round-trip", () => {
  assert.equal(prosePathFor("/w/src/a.py", ""), "/w/src/a.py.prose");
  assert.equal(prosePathFor("/w/src/a.py", ".prose"), "/w/src/.prose/a.py.prose");
  assert.equal(codePathFor("/w/src/a.py.prose", ""), "/w/src/a.py");
  assert.equal(codePathFor("/w/src/.prose/a.py.prose", ".prose"), "/w/src/a.py");
  assert.equal(mapPathFor("/w/src/a.py"), "/w/src/.prose/a.py.map.json");
});
