import test from "node:test";
import assert from "node:assert/strict";
import { deferAutoApproval, postActionSettleMs } from "../src/workflow-scheduling.js";

test("automatic browser actions wait until the current prompt turn unwinds", async () => {
  const order = [];
  Promise.resolve().then(() => order.push("prompt-finally"));
  deferAutoApproval(() => order.push("approve"));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(order, ["prompt-finally", "approve"]);
});

test("dynamic page actions wait for fresh DOM before continuation readback", () => {
  assert.ok(postActionSettleMs("navigate") >= 1500);
  assert.ok(postActionSettleMs("reload") >= 1500);
  assert.ok(postActionSettleMs("open_tab") >= 2000);
  assert.ok(postActionSettleMs("keypress") >= 1000);
  assert.ok(postActionSettleMs("click") >= 1000);
  assert.ok(postActionSettleMs("focus") < 500);
});
