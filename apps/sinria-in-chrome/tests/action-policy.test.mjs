import assert from "node:assert/strict";
import test from "node:test";
import { validateAction, isSensitiveField, isWritableField } from "../src/action-policy.js";

test("action policy rejects unsafe navigation and malformed refs", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "file:///tmp/x", "chrome://settings"]) {
    assert.equal(validateAction({ type: "navigate", url }).ok, false);
  }
  assert.equal(validateAction({ type: "navigate", url: "https://example.com" }).ok, true);
  assert.equal(validateAction({ type: "click", ref: "x-1" }).ok, false);
  assert.equal(validateAction({ type: "click", ref: "e12" }).ok, true);
});

test("typing is blocked for sensitive fields", () => {
  assert.equal(isSensitiveField({ type: "password" }), true);
  assert.equal(isSensitiveField({ autocomplete: "cc-number" }), true);
  assert.equal(isSensitiveField({ name: "api_token" }), true);
  assert.equal(validateAction({ type: "type", ref: "e2", text: "ok", field: { type: "password" } }).ok, false);
});

test("typing only targets text-entry controls", () => {
  assert.equal(isWritableField({ tag: "input", type: "text" }), true);
  assert.equal(isWritableField({ tag: "textarea", type: "textarea" }), true);
  assert.equal(isWritableField({ tag: "div", contenteditable: "true" }), true);
  assert.equal(isWritableField({ tag: "select", type: "select" }), false);
  assert.equal(isWritableField({ tag: "button", type: "button" }), false);
});
