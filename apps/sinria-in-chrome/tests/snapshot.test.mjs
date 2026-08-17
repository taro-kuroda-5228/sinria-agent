import assert from "node:assert/strict";
import test from "node:test";
import { sanitizeField, truncateText, sanitizeUrl, redactText, MAX_PAGE_TEXT } from "../src/snapshot.js";

test("snapshot redacts sensitive values", () => {
  assert.equal(sanitizeField({ type: "password", value: "secret" }).value, "[REDACTED]");
  assert.equal(sanitizeField({ name: "access_token", value: "secret" }).value, "[REDACTED]");
  assert.equal(sanitizeField({ type: "text", value: "hello" }).value, "hello");
});

test("snapshot text is bounded", () => {
  const text = "a".repeat(MAX_PAGE_TEXT + 10);
  assert.equal(truncateText(text).length, MAX_PAGE_TEXT);
});

test("snapshot removes URL secrets and obvious sensitive text", () => {
  assert.equal(sanitizeUrl("https://user:pass@example.test/path?q=secret#token"), "https://example.test/path");
  assert.equal(redactText("Email person@example.test bearer abcdefghijklmnop"), "Email [REDACTED_EMAIL] [REDACTED_TOKEN]");
});
