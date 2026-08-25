const MAX_TABS = 8;

export function isAllowedTab(tab = {}) {
  try { return ["http:", "https:"].includes(new URL(String(tab.url || "")).protocol); }
  catch { return false; }
}

export function sanitizeTabMetadata(tab = {}) {
  if (!isAllowedTab(tab)) return null;
  const url = new URL(tab.url);
  url.username = ""; url.password = ""; url.search = ""; url.hash = "";
  return {
    id: Number.isInteger(tab.id) ? tab.id : null,
    title: String(tab.title || "").replace(/\s+/g, " ").trim().slice(0, 240),
    url: url.href,
    active: Boolean(tab.active),
    windowId: Number.isInteger(tab.windowId) ? tab.windowId : null
  };
}

export function sanitizeTabs(tabs = []) {
  return tabs
    .map(sanitizeTabMetadata)
    .filter(Boolean)
    .map((tab, index) => ({ tab, index }))
    .sort((left, right) => Number(right.tab.active) - Number(left.tab.active) || left.index - right.index)
    .slice(0, MAX_TABS)
    .map(({ tab }) => tab);
}

export function selectTabIds(tabs, selected = []) {
  const allowed = new Set(sanitizeTabs(tabs).map((tab) => tab.id));
  return [...new Set(selected.map(Number).filter((id) => allowed.has(id)))].slice(0, MAX_TABS);
}

export function prioritizeSelectedTabIds(tabs, selected = [], focusId = null) {
  const focused = Number(focusId);
  const ordered = Number.isInteger(focused) ? [focused, ...selected] : selected;
  return selectTabIds(tabs, ordered);
}

export const MAX_CONTEXT_TABS = MAX_TABS;
