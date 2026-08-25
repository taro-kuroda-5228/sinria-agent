export const RESEARCH_LEDGER_KEY = "sinriaResearchLedgerV1";
export const RESEARCH_CATEGORIES = ["hospital", "healthcare_startup", "notable_vc"];
const clean = (value, limit) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);

export function sanitizeResearchCandidate(candidate = {}) {
  const category = RESEARCH_CATEGORIES.includes(candidate.category) ? candidate.category : "";
  const normalized = {
    name: clean(candidate.name, 160), role: clean(candidate.role, 240), company: clean(candidate.company, 240),
    region: clean(candidate.region, 180), category, evidence: clean(candidate.evidence, 300),
    verified: candidate.verified === true,
  };
  if (!normalized.verified || !normalized.name || !normalized.role || !normalized.company || !normalized.region || !category) return null;
  normalized.key = [normalized.name, normalized.company].map((x) => x.toLowerCase()).join("|");
  return normalized;
}

export function mergeResearchLedger(current = [], incoming = []) {
  const byKey = new Map();
  for (const item of [...current, ...incoming]) {
    const cleanItem = sanitizeResearchCandidate(item);
    if (cleanItem) byKey.set(cleanItem.key, cleanItem);
  }
  return [...byKey.values()].slice(-100);
}

export function researchDeficits(ledger = [], targets = { hospital: 3, healthcare_startup: 3, notable_vc: 3, total: 12 }) {
  const counts = Object.fromEntries(RESEARCH_CATEGORIES.map((c) => [c, ledger.filter((x) => x.category === c && x.verified).length]));
  return { counts, missing: Object.fromEntries(RESEARCH_CATEGORIES.map((c) => [c, Math.max(0, (targets[c] || 0) - counts[c])])), total: ledger.length, totalMissing: Math.max(0, (targets.total || 0) - ledger.length) };
}

export function researchLedgerPrompt(ledger = []) {
  const state = researchDeficits(ledger);
  const bounded = ledger.slice(-40).map(({ name, role, company, region, category, evidence }) => ({ name, role, company, region, category, evidence }));
  return `\nLocal verified research ledger (do not repeat entries): ${JSON.stringify(bounded)}\nRemaining verified targets: ${JSON.stringify(state)}\nReturn JSON with message, actions, and research_candidates. Add a research candidate only after fresh page readback confirms name, role, company, region, category, and set verified=true with a short evidence label. Never infer missing fields.`;
}
