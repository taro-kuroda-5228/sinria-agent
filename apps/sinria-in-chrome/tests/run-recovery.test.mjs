import test from "node:test";
import assert from "node:assert/strict";
import { readRunEventChunk } from "../src/api-client.js";
import { actionSummary } from "../src/action-policy.js";

test("run event reader fails with a typed recovery timeout", async () => {
  const reader = { read: () => new Promise(() => {}) };
  await assert.rejects(() => readRunEventChunk(reader, 5), (error) => {
    assert.equal(error.name, "RunIdleTimeoutError");
    return true;
  });
});

test("browser-control summaries never render undefined interactions", () => {
  assert.equal(actionSummary({ type: "activate_tab", tabId: 42 }), "Activate tab 42");
  assert.match(actionSummary({ type: "open_tab", tabId: 1, url: "https://example.test" }), /^Open https:/);
  assert.equal(actionSummary({ tabId: 42 }), "Reject malformed browser action");
});
