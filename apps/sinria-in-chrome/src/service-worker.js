import { capturePageSnapshot } from "./snapshot.js";
import { executePageAction } from "./action-executor.js";
import { shouldOpenNewTab } from "./navigation-policy.js";
import {
  validateAction,
  isWritableField,
  isSensitiveField,
} from "./action-policy.js";
import {
  RECEIPTS_KEY,
  normalizeReceipts,
  recordReceipt,
  receiptMatchesAction,
} from "./action-receipts.js";
import {
  sanitizeTabs,
  sanitizeTabMetadata,
  isAllowedTab,
} from "./tab-context.js";
import { PANEL_LEASE_KEY, claimPanelLease, releasePanelLease } from "./panel-lease.js";

const NATIVE_HOST = "ai.sinria.chrome_bridge";
const inFlightReceipts = new Map();

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => {});
});

async function getTab(tabId) {
  const tab = await chrome.tabs.get(Number(tabId));
  if (!tab?.id || !isAllowedTab(tab))
    throw new Error("Sinria can only inspect HTTP(S) pages.");
  return tab;
}

async function waitForTabComplete(tabId, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("New browser tab did not finish loading.");
}

async function snapshotTab(tabId) {
  const tab = await getTab(tabId);
  const metadata = sanitizeTabMetadata(tab);
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: capturePageSnapshot,
    });
    const topEntry = results.find((entry) => entry.frameId === 0) || results[0];
    if (!topEntry?.result) throw new Error("Snapshot returned no result.");
    const topOrigin = (() => { try { return new URL(topEntry.result.url).origin; } catch { return ""; } })();
    const snapshot = { ...topEntry.result, elements: [], canvases: [], charts: [], crossOriginFrames: [] };
    const textParts = [topEntry.result.text || ""];
    for (const entry of results) {
      if (!entry?.result) continue;
      const frame = entry.result;
      let frameOrigin = "";
      try {
        const parsedFrameUrl = new URL(frame.url);
        frameOrigin = parsedFrameUrl.protocol === "about:" ? topOrigin : parsedFrameUrl.origin;
      } catch { frameOrigin = topOrigin; }
      if (entry.frameId !== 0 && frameOrigin && frameOrigin !== topOrigin) {
        snapshot.crossOriginFrames.push({ frameId: entry.frameId, src: frame.url, title: frame.title || "" });
        continue;
      }
      if (entry.frameId !== 0 && frame.text) textParts.push(frame.text);
      for (const element of frame.elements || []) {
        const domRef = element.ref;
        snapshot.elements.push({
          ...element,
          ref: entry.frameId === 0 ? domRef : `f${entry.frameId}-${domRef}`,
          domRef,
          frameId: entry.frameId,
          framePath: entry.frameId === 0 ? (element.framePath || []) : [entry.frameId, ...(element.framePath || [])],
        });
      }
      snapshot.canvases.push(...(frame.canvases || []).map((item) => ({ ...item, frameId: entry.frameId })));
      snapshot.charts.push(...(frame.charts || []).map((item) => ({ ...item, frameId: entry.frameId })));
      if (entry.frameId === 0) snapshot.crossOriginFrames.push(...(frame.crossOriginFrames || []));
    }
    snapshot.text = textParts.join("\n").slice(0, 24000);
    const document = metadata.url && new URL(metadata.url).pathname.toLowerCase().endsWith(".pdf")
      ? { kind: "pdf", supported: true, localOnly: true }
      : snapshot.document;
    return { ...snapshot, document, tabId: tab.id, tab: metadata, title: snapshot.title || metadata.title, url: metadata.url || snapshot.url };
  } catch (error) {
    if (/\.pdf(?:$|[?#])/i.test(tab.url || "")) {
      return {
        tabId: tab.id,
        title: metadata.title,
        url: metadata.url,
        text: "PDF document; use the local visual capture for page understanding.",
        elements: [],
        crossOriginFrames: [],
        canvases: [],
        charts: [],
        document: { kind: "pdf", scriptable: false },
        capturedAt: new Date().toISOString(),
      };
    }
    throw error;
  }
}
async function completedReceipt(executionId) {
  if (!executionId) return null;
  const stored = await chrome.storage.local.get(RECEIPTS_KEY);
  return normalizeReceipts(stored[RECEIPTS_KEY])[executionId] || null;
}

async function saveReceipt(executionId, result) {
  if (!executionId || !result?.ok) return;
  const stored = await chrome.storage.local.get(RECEIPTS_KEY);
  await chrome.storage.local.set({
    [RECEIPTS_KEY]: recordReceipt(stored[RECEIPTS_KEY], executionId, result),
  });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab is available.");
  return getTab(tab.id);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handle = async () => {
    if (message?.type === "CLAIM_PANEL_LEASE") {
      const owner = String(message.owner || "").slice(0, 120);
      if (!owner) return { ok: false, error: "Panel owner is required." };
      const stored = await chrome.storage.local.get(PANEL_LEASE_KEY);
      const result = claimPanelLease(stored[PANEL_LEASE_KEY], owner);
      if (result.granted) await chrome.storage.local.set({ [PANEL_LEASE_KEY]: result.lease });
      return { ok: true, granted: result.granted, expiresAt: result.lease?.expiresAt || 0 };
    }
    if (message?.type === "RELEASE_PANEL_LEASE") {
      const owner = String(message.owner || "").slice(0, 120);
      const stored = await chrome.storage.local.get(PANEL_LEASE_KEY);
      const next = releasePanelLease(stored[PANEL_LEASE_KEY], owner);
      if (next) await chrome.storage.local.set({ [PANEL_LEASE_KEY]: next });
      else await chrome.storage.local.remove(PANEL_LEASE_KEY);
      return { ok: true };
    }
    if (message?.type === "LIST_TABS") {
      const tabs = await chrome.tabs.query({});
      return { ok: true, tabs: sanitizeTabs(tabs) };
    }
    if (message?.type === "SNAPSHOT" || message?.type === "snapshot") {
      const tab =
        message.tabId == null ? await activeTab() : await getTab(message.tabId);
      const snapshot = await snapshotTab(tab.id);
      let screenshot = null;
      let localPath = "";
      let screenshotStoreError = "";
      try {
        screenshot = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 35 });
      } catch (error) {
        screenshotStoreError = `capture:${error.message}`;
      }
      if (screenshot) {
        const comma = screenshot.indexOf(",");
        if (comma > 0) {
          try {
            const stored = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
              type: "store_screenshot",
              mime: "image/jpeg",
              base64: screenshot.slice(comma + 1),
            });
            if (stored?.ok && typeof stored.localPath === "string") localPath = stored.localPath;
            else screenshotStoreError = stored?.error || "native_store_failed";
          } catch (error) {
            screenshotStoreError = `native:${error.message}`;
          }
        }
      }
      const visualKind = /\.pdf(?:$|[?#])/i.test(tab.url || "")
        ? "pdf"
        : (snapshot.canvases?.length ? "canvas" : "page");
      return { ok: true, snapshot: { ...snapshot, screenshot: screenshot ? { dataUrl: screenshot, width: 0, height: 0, localPath, storeError: screenshotStoreError } : null, visual: { kind: visualKind, supported: Boolean(localPath), localPath, storeError: screenshotStoreError } } };
    }
    if (message?.type === "ACTIVATE_TAB") {
      const tab = await getTab(message.tabId);
      await chrome.tabs.update(tab.id, { active: true });
      return { ok: true, tab: sanitizeTabMetadata(tab) };
    }
    if (message?.type === "ACTION" || message?.type === "execute-action") {
      const executionId =
        typeof message.executionId === "string"
          ? message.executionId.slice(0, 200)
          : "";
      const priorReceipt = await completedReceipt(executionId);
      if (priorReceipt) return { ...priorReceipt, duplicate: true };
      const action = message.action || {};
      const tab = await getTab(message.tabId ?? action.tabId);
      if (action.tabId != null && Number(action.tabId) !== tab.id)
        return { ok: false, reason: "Action tab binding mismatch." };
      const lifecycleVerdict = validateAction(action);
      if (!lifecycleVerdict.ok) return lifecycleVerdict;
      const finishBrowserAction = async (result) => {
        await saveReceipt(executionId, result);
        return result;
      };
      if (["back", "forward"].includes(action.type)) {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: (direction) => direction === "back" ? history.back() : history.forward(),
          args: [action.type],
        });
        await new Promise((resolve) => setTimeout(resolve, 100));
        await waitForTabComplete(tab.id);
        return finishBrowserAction({ ok: true, detail: action.type === "back" ? "Went back." : "Went forward.", tabId: tab.id });
      }
      if (action.type === "reload") { await chrome.tabs.reload(tab.id); await waitForTabComplete(tab.id); return finishBrowserAction({ ok: true, detail: "Reloaded tab.", tabId: tab.id }); }
      if (action.type === "close_tab") { await chrome.tabs.remove(tab.id); return finishBrowserAction({ ok: true, detail: "Closed tab.", tabId: tab.id }); }
      if (action.type === "activate_tab") { const updated = await chrome.tabs.update(tab.id, { active: true }); return finishBrowserAction({ ok: true, detail: "Activated tab.", tabId: updated.id }); }
      if (action.type === "open_tab") {
        const created = await chrome.tabs.create({ url: action.url, active: action.active !== false });
        await waitForTabComplete(created.id);
        return finishBrowserAction({ ok: true, detail: "Opened tab.", tabId: created.id, openedNewTab: true });
      }
      const expected = message.expected || null;
      if (
        action.type !== "navigate" &&
        (!expected ||
          expected.ref !== action.ref ||
          expected.tabId !== tab.id ||
          typeof expected.guard !== "string" ||
          !expected.guard)
      ) {
        return {
          ok: false,
          reason: "Action is not bound to an approved page snapshot.",
        };
      }
      const guardedAction = expected
        ? {
            ...action,
            field: {
              ...expected.field,
              tag: expected.tag,
              contenteditable: expected.contenteditable,
            },
          }
        : action;
      const verdict = validateAction(guardedAction);
      if (!verdict.ok) return verdict;
      if (action.type === "navigate" && shouldOpenNewTab(tab.url, action.url)) {
        const created = await chrome.tabs.create({
          url: action.url,
          active: true,
        });
        await waitForTabComplete(created.id);
        const result = {
          ok: true,
          detail: "Opened research destination in a new tab.",
          tabId: created.id,
          openedNewTab: true,
        };
        await saveReceipt(executionId, result);
        return result;
      }
      if (action.type === "navigate") {
        await chrome.tabs.update(tab.id, { url: action.url });
        await waitForTabComplete(tab.id);
        const result = { ok: true, detail: `Navigated tab ${tab.id}.`, tabId: tab.id };
        await saveReceipt(executionId, result);
        return result;
      }
      if (
        action.type === "type" &&
        !isWritableField(guardedAction.field || {}) &&
        !isSensitiveField(guardedAction.field || {})
      )
        return {
          ok: false,
          reason: "Typing is limited to text-entry controls.",
        };
      const frameId = Number.isInteger(expected?.frameId) ? expected.frameId : 0;
      const pageAction = expected?.domRef ? { ...action, ref: expected.domRef } : action;
      const pageExpected = expected?.domRef ? { ...expected, ref: expected.domRef } : expected;
      const results = await chrome.scripting.executeScript({
        target: frameId === 0 ? { tabId: tab.id } : { tabId: tab.id, frameIds: [frameId] },
        func: executePageAction,
        args: [pageAction, pageExpected],
      });
      const result = results?.[0]?.result || {
        ok: false,
        error: "The page returned no action result.",
      };
      await saveReceipt(executionId, { ...result, tabId: tab.id });
      return result;
    }
    return { ok: false, error: "Unknown extension request." };
  };
  let responsePromise;
  const executionId = String(message?.executionId || "");
  if (message?.type === "ACTION" && executionId) {
    if (!receiptMatchesAction(executionId, message.action)) {
      responsePromise = Promise.resolve({
        ok: false,
        error: "Action receipt does not match the requested action.",
      });
    } else if (inFlightReceipts.has(executionId)) {
      responsePromise = inFlightReceipts.get(executionId);
    } else {
      responsePromise = handle().finally(() => inFlightReceipts.delete(executionId));
      inFlightReceipts.set(executionId, responsePromise);
    }
  } else {
    responsePromise = handle();
  }
  responsePromise
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
