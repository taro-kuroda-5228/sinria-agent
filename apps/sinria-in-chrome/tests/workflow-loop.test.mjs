import assert from "node:assert/strict";
import test from "node:test";
import { continuationInput, rejectionCorrection, resolveActionTarget, shouldContinueWorkflow, shouldRejectRepeatedAction } from "../src/workflow-loop.js";

test("verified browser steps continue the original request with a bounded loop", () => {
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 0, maxCycles: 8 }), true);
  assert.equal(shouldContinueWorkflow({ verified: false, cycle: 0, maxCycles: 8 }), false);
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 7, maxCycles: 8 }), false);
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 29, maxCycles: 30 }), false);
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 28, maxCycles: 30 }), true);
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 59 }), false);
  assert.equal(shouldContinueWorkflow({ verified: true, cycle: 58 }), true);
  const text = continuationInput("Research the selected people", 2, "Choose a different candidate.");
  assert.match(text, /Continue completing/);
  assert.match(text, /Research the selected people/);
  assert.match(text, /step 3/i);
  assert.match(text, /do not repeat the same search or action/i);
  assert.match(text, /different candidate/i);
  assert.equal((text.match(/Research the selected people/g) || []).length, 1);
  assert.match(text, /Choose a different candidate/);
});

test("immediately repeated browser actions are rejected before wasting another cycle", () => {
  assert.equal(shouldRejectRepeatedAction({ recentKeys: ["a", "b"], key: "b" }), true);
  assert.equal(shouldRejectRepeatedAction({ recentKeys: ["a", "b"], key: "a" }), false);
  assert.equal(shouldRejectRepeatedAction({ recentKeys: [], key: "a" }), false);
});

test("mistargeted safe typing is rebound to the approved keyword search field", () => {
  const snapshot = { elements: [
    { ref: "e21", tag: "button", name: "Geography" },
    { ref: "e36", tag: "input", role: "textbox", name: "Search keywords", field: { type: "text" } },
  ] };
  assert.equal(resolveActionTarget({ type: "type", ref: "e21", text: "Boston hospital" }, snapshot).ref, "e36");
  assert.equal(resolveActionTarget({ type: "click", ref: "e21" }, snapshot).ref, "e21");
  assert.match(rejectionCorrection("stale ref e99"), /choose a valid current ref/i);
  assert.ok(rejectionCorrection("x".repeat(1000)).length < 500);
});
