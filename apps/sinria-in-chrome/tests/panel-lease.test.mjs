import test from "node:test";
import assert from "node:assert/strict";
import { claimPanelLease, releasePanelLease } from "../src/panel-lease.js";

test("one live sidepanel owns workflow execution", () => {
  const first = claimPanelLease(null, "panel-a", 1000, 100);
  assert.equal(first.granted, true);
  const duplicate = claimPanelLease(first.lease, "panel-b", 1050, 100);
  assert.equal(duplicate.granted, false);
  const renewed = claimPanelLease(first.lease, "panel-a", 1050, 100);
  assert.equal(renewed.granted, true);
  assert.equal(renewed.lease.expiresAt, 1150);
});

test("expired or released sidepanel lease can be recovered", () => {
  const expired = claimPanelLease({ owner: "panel-a", expiresAt: 1050 }, "panel-b", 1051, 100);
  assert.equal(expired.granted, true);
  assert.equal(releasePanelLease(expired.lease, "panel-b"), null);
});
