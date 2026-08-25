import test from "node:test";
import assert from "node:assert/strict";
import { browserReceiptEvidence } from "../src/browser-receipts.js";
import { buildRunRequest } from "../src/api-client.js";

test("browser receipt evidence keeps only bounded verification metadata", () => {
  const receipt = browserReceiptEvidence({
    receiptId: "wf-1:3:abc12345",
    actionType: "keypress",
    verified: true,
    readbackTitle: "Search | Sales Navigator",
    readbackUrl: "https://www.linkedin.com/sales/search/people?secret=do-not-store",
  });
  assert.deepEqual(receipt, {
    receipt_id: "wf-1:3:abc12345",
    action_type: "keypress",
    verified: true,
    readback_label: "Search | Sales Navigator",
  });
  assert.equal(JSON.stringify(receipt).includes("linkedin.com"), false);
  assert.equal(browserReceiptEvidence({ receiptId: "x", actionType: "click", verified: false }), null);
});

test("browser research requests carry receipts and require autonomous coverage", () => {
  const receipt = browserReceiptEvidence({
    receiptId: "wf-1:3:abc12345",
    actionType: "keypress",
    verified: true,
    readbackTitle: "Search | Sales Navigator",
  });
  const request = buildRunRequest({
    prompt: "Find appointment candidates",
    sessionId: "session-1",
    snapshots: [{ tabId: 7, title: "Search", elements: [] }],
    browserReceipts: [receipt],
  });
  assert.deepEqual(request.browser_receipts, [receipt]);
  assert.match(request.instructions, /exactly one next browser action/i);
  assert.match(request.instructions, /research ledger/i);
  assert.match(request.instructions, /requested category and count/i);
  assert.match(request.instructions, /extract every distinct visible result card/i);
});

test("verified page readback is accepted as bounded browser evidence", () => {
  const receipt = browserReceiptEvidence({
    receiptId: "readback:4:snapshot123",
    actionType: "readback",
    verified: true,
    readbackTitle: "Search | Sales Navigator",
  });
  assert.deepEqual(receipt, {
    receipt_id: "readback:4:snapshot123",
    action_type: "readback",
    verified: true,
    readback_label: "Search | Sales Navigator",
  });
});

test("run request keeps the newest bounded browser receipts", () => {
  const receipts = Array.from({ length: 6 }, (_, index) => ({
    receipt_id: `readback:1:snapshot${index}`,
    action_type: "readback",
    verified: true,
    readback_label: `Snapshot ${index}`,
  }));
  const request = buildRunRequest({ prompt: "continue", browserReceipts: receipts });
  assert.deepEqual(request.browser_receipts, receipts.slice(-4));
});

test("run request preserves bounded structured cards and capture time", () => {
  const request = buildRunRequest({
    prompt: "continue",
    snapshots: [{
      tabId: 42,
      title: "Search",
      url: "https://example.test/search",
      capturedAt: "2026-08-25T00:00:00.000Z",
      text: "results",
      cards: [{ text: "Candidate One\nChief Executive Officer\nExample Health\nBoston", labels: ["Candidate One is reachable"] }],
    }],
  });
  assert.match(request.instructions, /Chief Executive Officer/);
  assert.match(request.instructions, /Example Health/);
  assert.match(request.instructions, /2026-08-25T00:00:00.000Z/);
});
