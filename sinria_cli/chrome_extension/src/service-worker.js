import { capturePageSnapshot } from "./snapshot.js";
import { executePageAction } from "./action-executor.js";
import { validateAction, isWritableField } from "./action-policy.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab is available.");
  if (!/^https?:/.test(tab.url || "")) throw new Error("Sinria can only inspect HTTP(S) pages.");
  return tab;
}

async function runInTab(func, args = []) {
  const tab = await activeTab();
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func, args });
  return results?.[0]?.result;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handle = async () => {
    if (message?.type === "snapshot") return { ok: true, snapshot: await runInTab(capturePageSnapshot) };
    if (message?.type === "execute-action") {
      const expected = message.expected || null;
      if (message.action?.type !== "navigate" && (
        !expected || expected.ref !== message.action?.ref || typeof expected.guard !== "string" || !expected.guard
      )) {
        return { ok: false, reason: "Action is not bound to an approved page snapshot." };
      }
      const guardedAction = expected
        ? { ...message.action, field: { ...expected.field, tag: expected.tag, contenteditable: expected.contenteditable } }
        : message.action;
      const verdict = validateAction(guardedAction);
      if (!verdict.ok) return verdict;
      if (message.action.type === "type" && !isWritableField(expected || {})) {
        return { ok: false, reason: "Typing is limited to text-entry controls." };
      }
      const result = await runInTab(executePageAction, [message.action, expected]);
      return result || { ok: false, error: "The page returned no action result." };
    }
    return { ok: false, error: "Unknown extension request." };
  };
  handle().then(sendResponse).catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
