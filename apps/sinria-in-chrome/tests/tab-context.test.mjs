import assert from "node:assert/strict";
import test from "node:test";
import { isAllowedTab, prioritizeSelectedTabIds, sanitizeTabMetadata, sanitizeTabs, selectTabIds } from "../src/tab-context.js";

test("tab metadata is restricted to HTTP(S) and redacts URL secrets", () => {
  assert.equal(isAllowedTab({ url: "chrome://settings" }), false);
  assert.equal(sanitizeTabMetadata({ id: 2, title: " Secret\n page ", url: "https://user:pass@example.test/a?q=secret#x", active: true }).url, "https://example.test/a");
  assert.equal(sanitizeTabMetadata({ id: 3, url: "file:///tmp/a" }), null);
});

test("selection only returns visible, bounded tab ids", () => {
  const tabs = [{ id: 1, url: "https://a.test" }, { id: 2, url: "http://b.test" }, { id: 3, url: "chrome://newtab" }];
  assert.deepEqual(selectTabIds(tabs, ["2", 3, 1, 2]), [2, 1]);
  assert.equal(sanitizeTabs(tabs).length, 2);
});

test("new active tab remains visible when the bounded tab list is full", () => {
  const tabs = Array.from({ length: 9 }, (_, index) => ({
    id: index + 1,
    url: `https://example.test/${index + 1}`,
    active: index === 8,
  }));
  const visible = sanitizeTabs(tabs);
  assert.equal(visible.length, 8);
  assert.equal(visible[0].id, 9);
  assert.deepEqual(selectTabIds(visible, [9]), [9]);
});

test("fresh action tab is prioritized inside the four-tab snapshot budget", () => {
  const tabs = Array.from({ length: 8 }, (_, index) => ({
    id: index + 1,
    url: `https://example.test/${index + 1}`,
  }));
  assert.deepEqual(prioritizeSelectedTabIds(tabs, [1, 2, 3, 4], 8).slice(0, 4), [8, 1, 2, 3]);
});
