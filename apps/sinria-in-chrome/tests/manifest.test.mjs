import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const manifest = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));

test("manifest uses MV3 side panel and least-privilege permissions", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.side_panel.default_path, "sidepanel.html");
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "scripting", "sidePanel", "storage"].sort());
  assert.deepEqual(manifest.host_permissions.sort(), ["http://127.0.0.1/*", "http://localhost/*"].sort());
  assert.ok(!manifest.permissions.includes("tabs"));
  assert.ok(!manifest.host_permissions.includes("<all_urls>"));
});

test("bundled public key gives the documented stable extension identity", () => {
  const digest = createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest().subarray(0, 16);
  const id = [...digest].map((byte) => String.fromCharCode(97 + (byte >> 4), 97 + (byte & 15))).join("");
  assert.equal(id, "pebcacnleolamclolgncigkgojkdghgc");
});
