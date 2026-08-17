import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const read = (name) => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");

test("extension sources avoid remote code and unsafe HTML sinks", () => {
  const source = ["src/sidepanel.js", "src/service-worker.js", "src/snapshot.js", "src/action-executor.js"].map(read).join("\n");
  assert.doesNotMatch(source, /innerHTML\s*=/);
  assert.doesNotMatch(source, /\beval\s*\(/);
  assert.doesNotMatch(source, /new\s+Function\s*\(/);
  assert.doesNotMatch(source, /https?:\/\/[^\s"']+\.js/);
});

test("token is kept in session storage rather than persistent local storage", () => {
  const source = read("src/sidepanel.js");
  assert.match(source, /chrome\.storage\.session/);
  assert.doesNotMatch(source, /storageSet\(\{\s*baseUrl,\s*token/);
});
