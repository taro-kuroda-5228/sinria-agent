import test from "node:test";
import assert from "node:assert/strict";
import { mergeResearchLedger, researchDeficits, researchLedgerPrompt } from "../src/research-ledger.js";
import { parseAssistantEnvelope } from "../src/api-client.js";

const verified = (name, category) => ({ name, role: "Partner", company: "Example Health", region: "New York", category, evidence: "fresh-card-readback", verified: true });

test("research ledger keeps only complete verified candidates and deduplicates", () => {
  const ledger = mergeResearchLedger([], [verified("A", "notable_vc"), verified("A", "notable_vc"), { ...verified("B", "hospital"), region: "" }]);
  assert.equal(ledger.length, 1);
  assert.equal(researchDeficits(ledger).missing.notable_vc, 2);
  assert.match(researchLedgerPrompt(ledger), /Remaining verified targets/);
});

test("assistant envelope carries structured research candidates", () => {
  const candidate = verified("A", "notable_vc");
  const envelope = parseAssistantEnvelope(JSON.stringify({ message: "continue", actions: [], research_candidates: [candidate] }));
  assert.deepEqual(envelope.researchCandidates, [candidate]);
});
