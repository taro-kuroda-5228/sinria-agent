export function shouldContinueWorkflow({ verified, cycle, maxCycles = 60 }) {
  return Boolean(verified) && Number.isInteger(cycle) && cycle + 1 < maxCycles;
}

export function shouldRejectRepeatedAction({ recentKeys, key }) {
  return Boolean(key) && Array.isArray(recentKeys) && recentKeys.at(-1) === key;
}

export function resolveActionTarget(action, snapshot) {
  if (!action || action.type !== "type" || !snapshot) return action;
  const elements = Array.isArray(snapshot.elements) ? snapshot.elements : [];
  const current = elements.find((item) => item.ref === action.ref);
  const writable = (item) =>
    ["input", "textarea"].includes(String(item?.tag || "").toLowerCase()) ||
    item?.contenteditable === true ||
    ["textbox", "searchbox", "combobox"].includes(String(item?.role || "").toLowerCase());
  if (writable(current)) return action;
  const candidates = elements.filter(writable);
  const keywordSearch = candidates.find((item) =>
    /search\s*keywords|keywords\s*search/i.test(`${item.name || ""} ${item.label || ""}`),
  );
  if (keywordSearch) return { ...action, ref: keywordSearch.ref };
  if (candidates.length === 1) return { ...action, ref: candidates[0].ref };
  return action;
}

export function rejectionCorrection(reason) {
  return `The previous browser action was rejected before execution (${String(reason || "unknown").slice(0, 220)}). Re-read the fresh snapshot and choose a valid current ref or a different strategy; do not retry the stale target.`;
}

export function continuationInput(originalPrompt, cycle, correction = "") {
  const boundedCorrection = String(correction || "").slice(0, 500);
  return `Continue completing the user's original request after verified browser step ${cycle + 1}. Re-read the current selected page state and perform the next necessary step. Do not repeat completed steps. Do not repeat the same search or action when its result has already been read back; if the page still lacks enough evidence, exclude that item and choose a different candidate or strategy.${boundedCorrection ? ` Workflow correction: ${boundedCorrection}` : ""} Original request: ${String(originalPrompt || "")}`;
}
