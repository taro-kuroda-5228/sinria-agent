import assert from "node:assert/strict";
import test from "node:test";
import { executePageAction } from "../src/action-executor.js";

function withFakePage(element, run) {
  const originalDocument = globalThis.document;
  const originalLocation = globalThis.location;
  globalThis.document = { querySelector: () => element };
  globalThis.location = { href: "https://example.test/", assign() {} };
  try { return run(); }
  finally {
    if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
    if (originalLocation === undefined) delete globalThis.location; else globalThis.location = originalLocation;
  }
}

function button(guard = "approved-guard") {
  let clicked = false;
  return {
    tagName: "BUTTON",
    getAttribute(name) { return name === "data-sinria-guard" ? guard : null; },
    scrollIntoView() {},
    click() { clicked = true; },
    wasClicked() { return clicked; }
  };
}

test("page actions require an approved snapshot binding", () => {
  const element = button();
  const result = withFakePage(element, () => executePageAction({ type: "click", ref: "e1" }));
  assert.equal(result.ok, false);
  assert.equal(element.wasClicked(), false);
});

test("page actions fail closed when the approved element guard changed", () => {
  const element = button("new-guard");
  const expected = { ref: "e1", guard: "approved-guard", tag: "button" };
  const result = withFakePage(element, () => executePageAction({ type: "click", ref: "e1" }, expected));
  assert.equal(result.ok, false);
  assert.match(result.error, /changed after approval/i);
  assert.equal(element.wasClicked(), false);
});

test("page actions execute when the approved element identity is unchanged", () => {
  const element = button();
  const expected = { ref: "e1", guard: "approved-guard", tag: "button" };
  const result = withFakePage(element, () => executePageAction({ type: "click", ref: "e1" }, expected));
  assert.equal(result.ok, true);
  assert.equal(element.wasClicked(), true);
});
