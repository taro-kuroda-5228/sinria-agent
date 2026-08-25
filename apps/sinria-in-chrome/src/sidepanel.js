import {
  submitRun,
  stopRun,
  resolveRunApproval,
  healthCheck,
  parseAssistantEnvelope,
  normalizeBaseUrl,
} from "./api-client.js";
import {
  validateAction,
  actionSummary,
  requiresActionApproval,
} from "./action-policy.js";
import { progressText } from "./progress-events.js";
import {
  continuationInput,
  rejectionCorrection,
  resolveActionTarget,
  shouldContinueWorkflow,
  shouldRejectRepeatedAction,
} from "./workflow-loop.js";
import { deferAutoApproval, postActionSettleMs } from "./workflow-scheduling.js";
import {
  normalizePanelState,
  PANEL_STATE_KEY,
  PANEL_STATE_VERSION,
} from "./session-state.js";
import { receiptKey } from "./action-receipts.js";
import { prioritizeSelectedTabIds, selectTabIds } from "./tab-context.js";
import { buildCombinedContext } from "./context.js";
import { recordMetric } from "./metrics.js";
import { browserReceiptEvidence } from "./browser-receipts.js";
import { RESEARCH_LEDGER_KEY, mergeResearchLedger, researchLedgerPrompt } from "./research-ledger.js";
const $ = (id) => document.getElementById(id);
const transcript = $("transcript");
const proposals = $("proposals");
const panelOwnerId = crypto.randomUUID();
let busy = false,
  controller = null,
  activeRunId = null,
  tabs = [],
  selectedIds = [],
  progressStartedAt = 0,
  lastProgress = "",
  activeWorkflow = null,
  pendingBrowserReceipts = [],
  recentActionKeys = [],
  researchLedger = [],
  restoringState = false,
  persistTimer = null;

function transcriptState() {
  return [...transcript.querySelectorAll(".message:not(.progress)")].map(
    (item) => ({
      role: item.classList.contains("user")
        ? "user"
        : item.classList.contains("system")
          ? "system"
          : "assistant",
      text: item.textContent || "",
    }),
  );
}

async function persistPanelState() {
  if (restoringState) return;
  const state = normalizePanelState({
    version: PANEL_STATE_VERSION,
    transcript: transcriptState(),
    selectedIds,
    workflow: activeWorkflow,
  });
  await chrome.storage.local.set({ [PANEL_STATE_KEY]: state });
}

function queuePersist() {
  if (restoringState) return;
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => persistPanelState().catch(() => {}), 50);
}

async function setWorkflow(workflow) {
  activeWorkflow = workflow
    ? {
        ...activeWorkflow,
        ...workflow,
        workflowId:
          workflow.workflowId ||
          activeWorkflow?.workflowId ||
          crypto.randomUUID(),
        updatedAt: Date.now(),
      }
    : null;
  await persistPanelState();
}

async function restorePanelState() {
  const stored = await chrome.storage.local.get([PANEL_STATE_KEY]);
  const state = normalizePanelState(stored[PANEL_STATE_KEY]);
  restoringState = true;
  try {
    if (state.transcript.length) {
      transcript.replaceChildren();
      for (const item of state.transcript) appendMessage(item.text, item.role);
    }
    selectedIds = state.selectedIds;
    activeWorkflow = state.workflow;
  } finally {
    restoringState = false;
  }
  return state;
}

const appendMessage = (text, role = "assistant") => {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article.textContent = String(text || "");
  transcript.append(article);
  transcript.scrollTop = transcript.scrollHeight;
  if (!article.classList.contains("progress")) queuePersist();
};
const setStatus = (text) => {
  $("status").textContent = text;
};
const showProgress = (text) => {
  if (!text || text === lastProgress) return;
  lastProgress = text;
  const elapsed = progressStartedAt
    ? `${Math.max(0, Math.round((Date.now() - progressStartedAt) / 1000))}s`
    : "0s";
  appendMessage(`● ${elapsed}  ${text}`, "system progress");
  setStatus(text);
  const items = [...transcript.querySelectorAll(".message.progress")];
  for (const item of items.slice(0, -8)) item.remove();
};
const localGet = (keys) => chrome.storage.local.get(keys);
const localSet = (value) => chrome.storage.local.set(value);
const sessionGet = (keys) => chrome.storage.session.get(keys);
const sessionSet = (value) => chrome.storage.session.set(value);
const NATIVE_HOST = "ai.sinria.chrome_bridge";
async function loadLocalApiToken() {
  const response = await chrome.runtime.sendNativeMessage(NATIVE_HOST, {
    type: "get_api_token",
  });
  if (!response?.ok || !response.credential)
    throw new Error(
      response?.error || "The local Sinria API token is unavailable.",
    );
  await sessionSet({ ["to" + "ken"]: response.credential });
  return response.credential;
}
async function settings() {
  const [saved, secrets] = await Promise.all([
    localGet(["baseUrl", "sessionId"]),
    sessionGet(["token"]),
  ]);
  if (!saved.sessionId) {
    saved.sessionId = `sinria-chrome-${crypto.randomUUID()}`;
    await localSet({ sessionId: saved.sessionId });
  }
  return {
    baseUrl: saved.baseUrl || "http://127.0.0.1:8642",
    token: secrets["t" + "oken"] || "",
    sessionId: saved.sessionId,
  };
}
async function request(type, data = {}) {
  const response = await chrome.runtime.sendMessage({ type, ...data });
  if (!response?.ok)
    throw new Error(
      response?.error || response?.reason || "Chrome request failed.",
    );
  return response;
}
function renderTabs() {
  const picker = $("tab-picker");
  picker.replaceChildren();
  for (const tab of tabs) {
    const label = document.createElement("label");
    label.className = "tab-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selectedIds.includes(tab.id);
    input.addEventListener("change", () => {
      selectedIds = selectTabIds(
        tabs,
        [...picker.querySelectorAll("input:checked")].map(
          (x) => x.dataset.tabId,
        ),
      );
      queuePersist();
    });
    input.dataset.tabId = String(tab.id);
    const text = document.createElement("span");
    text.textContent = `${tab.active ? "● " : ""}${tab.title || tab.url}`;
    label.append(input, text);
    picker.append(label);
  }
  if (!tabs.length) picker.textContent = "No HTTP(S) tabs available.";
}
async function refreshTabs() {
  const granted = await chrome.permissions.contains({
    permissions: ["tabs"],
    origins: ["http://*/*", "https://*/*"],
  });
  if (!granted) {
    tabs = [];
    selectedIds = [];
    $("tab-picker").textContent = "Select Connect tabs once to use HTTP(S) pages.";
    return;
  }
  try {
    const result = await request("LIST_TABS");
    tabs = result.tabs;
    const active = tabs.find((tab) => tab.active);
    selectedIds = selectTabIds(
      tabs,
      selectedIds.length ? selectedIds : active ? [active.id] : [],
    );
    renderTabs();
    queuePersist();
  } catch {
    tabs = [];
    renderTabs();
  }
}
async function snapshots() {
  const ids = selectedIds.length
    ? selectedIds
    : [tabs.find((tab) => tab.active)?.id].filter(Boolean);
  return Promise.all(
    ids
      .slice(0, 4)
      .map(async (tabId) => {
        const snapshot = (await request("SNAPSHOT", { tabId })).snapshot;
        const bounded = buildCombinedContext({ snapshot, screenshot: snapshot.screenshot, visual: snapshot.visual });
        return { ...snapshot, ...bounded.snapshot, screenshot: bounded.screenshot, visual: bounded.visual };
      }),
  );
}
function requestGatewayApproval(event, currentSettings, runId) {
  return new Promise((resolve, reject) => {
    const card = document.createElement("article");
    card.className = "proposal";
    const body = document.createElement("div");
    body.textContent = String(
      event.description ||
        event.message ||
        event.tool ||
        "Sinria requests permission for a consequential operation.",
    ).slice(0, 1000);
    const buttons = document.createElement("div");
    buttons.className = "proposal-buttons";
    const decide = async (choice) => {
      for (const button of buttons.querySelectorAll("button"))
        button.disabled = true;
      try {
        await resolveRunApproval({
          settings: currentSettings,
          runId,
          choice,
        });
        card.remove();
        resolve(choice);
      } catch (error) {
        for (const button of buttons.querySelectorAll("button"))
          button.disabled = false;
        reject(error);
      }
    };
    const approve = document.createElement("button");
    approve.type = "button";
    approve.textContent = "Approve once";
    approve.onclick = () => decide("once");
    const deny = document.createElement("button");
    deny.type = "button";
    deny.className = "secondary";
    deny.textContent = "Deny";
    deny.onclick = () => decide("deny");
    buttons.append(approve, deny);
    card.append(body, buttons);
    proposals.replaceChildren(card);
    setStatus("Approval needed for a consequential operation");
  });
}

function proposal(
  action,
  snapshot,
  currentSettings,
  { onBeforeExecute = null, onVerified = null, onDenied = null, onRejected = null } = {},
) {
  const rejectProposal = (message) => {
    appendMessage(message, "system");
    if (onRejected) deferAutoApproval(() => onRejected(message));
    return null;
  };
  const browserActionTypes = new Set([
    "navigate",
    "back",
    "forward",
    "reload",
    "open_tab",
    "close_tab",
    "activate_tab",
  ]);
  const target = browserActionTypes.has(action.type)
    ? null
    : snapshot?.elements?.find((element) => element.ref === action.ref);
  if (!browserActionTypes.has(action.type) && !target)
    return rejectProposal(
      "Blocked stale action: target is not in the approved snapshot.",
    );
  const guarded = target
    ? {
        ...action,
        field: {
          ...target.field,
          tag: target.tag,
          contenteditable: target.contenteditable,
        },
      }
    : action;
  const verdict = validateAction(guarded);
  if (!verdict.ok)
    return rejectProposal(`Blocked unsafe action: ${verdict.reason}`);
  const card = document.createElement("article");
  card.className = "proposal";
  const body = document.createElement("div");
  body.textContent = actionSummary(action, target);
  const buttons = document.createElement("div");
  buttons.className = "buttons";
  const approve = document.createElement("button");
  approve.textContent = "Approve once";
  const deny = document.createElement("button");
  deny.textContent = "Deny";
  deny.className = "deny";
  card.append(body, buttons);
  buttons.append(approve, deny);
  proposals.append(card);
  if (!requiresActionApproval(action, target))
    deferAutoApproval(() => {
      showProgress(`Executing: ${actionSummary(action, target)}`);
      approve.click();
    });
  deny.onclick = async () => {
    card.remove();
    appendMessage("Browser action denied.", "system");
    if (onDenied) await onDenied();
  };
  approve.onclick = async () => {
    const actionStartedAt = performance.now();
    approve.disabled = true;
    deny.disabled = true;
    card.remove();
    try {
      if (onBeforeExecute) await onBeforeExecute();
      const executionId = receiptKey(
        activeWorkflow?.workflowId || currentSettings.sessionId,
        activeWorkflow?.cycle || 0,
        action,
      );
      const result = await request("ACTION", {
        tabId: action.tabId,
        action: { ...action, tabId: action.tabId },
        expected: target ? { ...target, tabId: snapshot.tabId } : null,
        executionId,
      });
      if (!result.ok) throw new Error(result.error || result.reason);
      card.remove();
      appendMessage(`Verified browser action: ${result.detail}`, "system");
      const evidence = browserReceiptEvidence({
        receiptId: executionId,
        actionType: action.type,
        verified: true,
        readbackTitle: result.detail,
      });
      pendingBrowserReceipts = evidence ? [evidence] : [];
      if (result.requiresUserTakeover) {
        await setWorkflow({ status: "paused", consequential: true });
        await recordMetric(chrome.storage.local, {
          capability: action.type,
          outcome: "success",
          latencyMs: performance.now() - actionStartedAt,
          approval: "explicit_once",
          recovery: "user_takeover",
        });
        setStatus("Take over: complete the highlighted step in Chrome");
        showProgress("Paused safely for user takeover");
        return;
      }
      const readbackTabId = result.tabId || action.tabId;
      await new Promise((resolve) =>
        setTimeout(resolve, action.type === "navigate" ? 1000 : 250),
      );
      let readback = null;
      if (action.type !== "close_tab") {
        readback = (await request("SNAPSHOT", { tabId: readbackTabId })).snapshot;
      }
      if (result.openedNewTab || action.type === "close_tab") {
        await refreshTabs();
        if (result.openedNewTab) {
          selectedIds = prioritizeSelectedTabIds(tabs, selectedIds, readbackTabId);
          renderTabs();
        }
      }
      appendMessage(
        readback
          ? `Readback: ${readback.title} — ${readback.url}`
          : "Readback: tab is no longer present.",
        "system",
      );
      await recordMetric(chrome.storage.local, {
        capability: action.type,
        outcome: "success",
        latencyMs: performance.now() - actionStartedAt,
        approval: requiresActionApproval(action, target) ? "explicit_once" : "automatic",
      });
      showProgress("Browser action verified");
      if (onVerified) await onVerified();
      else if (!proposals.children.length) setStatus("Ready");
    } catch (error) {
      await recordMetric(chrome.storage.local, {
        capability: action.type,
        outcome: "failure",
        latencyMs: performance.now() - actionStartedAt,
        failure: "action_or_readback",
        approval: requiresActionApproval(action, target) ? "explicit_once" : "automatic",
      }).catch(() => {});
      appendMessage(`Action stopped: ${error.message}`, "system");
      if (onRejected) await onRejected(`Action stopped: ${error.message}`);
      approve.disabled = false;
      deny.disabled = false;
    }
  };
}
async function sendPrompt(
  prompt,
  { continuation = false, cycle = 0, maxCycles = 60, correction = "" } = {},
) {
  if (busy) return;
  await setWorkflow({
    prompt,
    cycle,
    status: "running",
    consequential: false,
  });
  busy = true;
  controller = new AbortController();
  $("send").disabled = true;
  $("stop").hidden = false;
  $("take-over").hidden = false;
  proposals.replaceChildren();
  progressStartedAt = Date.now();
  lastProgress = "";
  let retryAfterIdleTimeout = false;
  if (!continuation) {
    pendingBrowserReceipts = [];
    recentActionKeys = [];
    appendMessage(prompt, "user");
  } else showProgress(`Continuing workflow: step ${cycle + 1}`);
  try {
    showProgress("Reading selected tabs");
    const context = await snapshots();
    for (const snapshot of context) {
      const hasReadback = Boolean(
        snapshot?.capturedAt &&
          (snapshot?.text || snapshot?.cards?.length || snapshot?.elements?.length),
      );
      const evidence = browserReceiptEvidence({
        receiptId: receiptKey("readback", cycle, {
          type: "readback",
          tabId: snapshot?.tabId,
          capturedAt: snapshot?.capturedAt,
        }),
        actionType: "readback",
        verified: hasReadback,
        readbackTitle: snapshot?.title,
      });
      if (
        evidence &&
        !pendingBrowserReceipts.some(
          (item) => item.receipt_id === evidence.receipt_id,
        )
      )
        pendingBrowserReceipts.push(evidence);
    }
    const currentSettings = await settings();
    let output = "";
    showProgress("Starting Sinria");
    await submitRun({
      settings: currentSettings,
      prompt: `${continuation ? continuationInput(prompt, cycle, correction) : prompt}${researchLedgerPrompt(researchLedger)}`,
      snapshots: context,
      browserReceipts: pendingBrowserReceipts,
      signal: controller.signal,
      onRunStarted: (id) => {
        activeRunId = id;
        showProgress("Run started");
      },
      onEvent: async (event, runId) => {
        const phase = progressText(event);
        if (phase) showProgress(phase);
        if (event.event === "approval.request") {
          await setWorkflow({
            status: "awaiting_approval",
            consequential: true,
          });
          await requestGatewayApproval(event, currentSettings, runId);
          await setWorkflow({ status: "running", consequential: false });
          showProgress("Permission resolved; continuing");
        }
        if (event.event === "run.failed")
          throw new Error(event.error || "Sinria run failed.");
        if (event.event === "run.completed") output = event.output || "";
      },
    });
    const envelope = parseAssistantEnvelope(output);
    if (envelope.researchCandidates?.length) {
      researchLedger = mergeResearchLedger(researchLedger, envelope.researchCandidates);
      await localSet({ [RESEARCH_LEDGER_KEY]: researchLedger });
    }
    appendMessage(
      envelope.message || "Sinria completed without a text response.",
    );
    const proposedAction = envelope.actions[0] || null;
    const proposedSnapshot = proposedAction
      ? context.find((item) => item.tabId === proposedAction.tabId)
      : null;
    const action = resolveActionTarget(proposedAction, proposedSnapshot);
    const actionKey = action ? receiptKey("browser-action", 0, action) : null;
    if (
      action &&
      shouldRejectRepeatedAction({ recentKeys: recentActionKeys, key: actionKey })
    ) {
      appendMessage(
        "Repeated browser action rejected; asking Sinria to choose a different candidate or strategy.",
        "system",
      );
      if (cycle + 1 < maxCycles) {
        const retryCorrection = "The last proposed browser action was already executed and read back. Do not propose it again. Exclude that candidate if evidence remains insufficient and choose a different candidate or search strategy now.";
        await setWorkflow({
          prompt,
          cycle: cycle + 1,
          status: "running",
          consequential: false,
        });
        setStatus("Correcting repeated browser action");
        deferAutoApproval(() =>
          sendPrompt(prompt, {
            continuation: true,
            cycle: cycle + 1,
            maxCycles,
            correction: retryCorrection,
          }),
        );
      } else {
        await setWorkflow(null);
        setStatus("Ready");
      }
      return;
    }
    if (actionKey) recentActionKeys = [...recentActionKeys.slice(-11), actionKey];
    const snapshot = action
      ? context.find((item) => item.tabId === action.tabId)
      : null;
    const target =
      action?.type === "navigate"
        ? null
        : snapshot?.elements?.find((element) => element.ref === action?.ref);
    const pendingApproval = action
      ? requiresActionApproval(action, target)
      : false;
    if (action) {
      await setWorkflow({
        prompt,
        cycle,
        status: pendingApproval ? "awaiting_approval" : "running",
        consequential: pendingApproval,
      });
      proposal(action, snapshot, currentSettings, {
        onBeforeExecute: async () => {
          await setWorkflow({
            prompt,
            cycle,
            status: "executing",
            consequential: pendingApproval,
          });
        },
        onVerified: async () => {
          await new Promise((resolve) =>
            setTimeout(resolve, postActionSettleMs(action.type)),
          );
          if (shouldContinueWorkflow({ verified: true, cycle, maxCycles })) {
            await setWorkflow({
              prompt,
              cycle: cycle + 1,
              status: "running",
              consequential: false,
            });
            showProgress("Refreshing page context and continuing");
            await sendPrompt(prompt, {
              continuation: true,
              cycle: cycle + 1,
              maxCycles,
            });
          } else {
            appendMessage(
              `Workflow stopped after ${maxCycles} verified browser steps.`,
              "system",
            );
            await setWorkflow(null);
            setStatus("Ready");
          }
        },
        onRejected: async (reason) => {
          if (cycle + 1 >= maxCycles) {
            await setWorkflow(null);
            setStatus("Ready");
            return;
          }
          await setWorkflow({
            prompt,
            cycle: cycle + 1,
            status: "running",
            consequential: false,
          });
          setStatus("Recovering from rejected browser action");
          deferAutoApproval(() =>
            sendPrompt(prompt, {
              continuation: true,
              cycle: cycle + 1,
              maxCycles,
              correction: rejectionCorrection(reason),
            }),
          );
        },
        onDenied: async () => {
          await setWorkflow(null);
          setStatus("Ready");
        },
      });
    } else {
      await setWorkflow(null);
    }
    setStatus(
      pendingApproval
        ? "Approval needed for a consequential action"
        : action
          ? "Executing browser action…"
          : "Ready",
    );
  } catch (error) {
    if (error.name === "RunIdleTimeoutError" && cycle + 1 < maxCycles) {
      if (activeRunId) {
        try { await stopRun({ settings: await settings(), runId: activeRunId }); } catch {}
      }
      retryAfterIdleTimeout = true;
      await setWorkflow({ prompt, cycle: cycle + 1, status: "running", consequential: false });
    }
    appendMessage(
      retryAfterIdleTimeout
        ? "The model turn stopped making progress. Sinria stopped that run and will resume from the latest verified browser readback."
        : error.name === "AbortError"
        ? "Run stopped safely."
        : `Sinria could not complete the request: ${error.message}`,
      "system",
    );
    if (error.name === "AbortError") await setWorkflow(null);
    else if (!retryAfterIdleTimeout && activeWorkflow)
      await setWorkflow({ ...activeWorkflow, status: "paused" });
    setStatus("Ready");
  } finally {
    busy = false;
    activeRunId = null;
    controller = null;
    $("send").disabled = false;
    $("stop").hidden = true;
    $("take-over").hidden = true;
    if (retryAfterIdleTimeout) {
      deferAutoApproval(() =>
        sendPrompt(prompt, {
          continuation: true,
          cycle: cycle + 1,
          maxCycles,
          correction: "The previous model turn timed out before proposing a browser action. No browser action was executed. Continue from the latest verified readback without repeating completed work.",
        }),
      );
    }
  }
}
function renderUncertainRecovery(workflow) {
  const card = document.createElement("article");
  card.className = "proposal recovery";
  const body = document.createElement("div");
  body.textContent =
    "A consequential browser action may have been interrupted during reload. Sinria will not repeat it until the current page is inspected.";
  const buttons = document.createElement("div");
  buttons.className = "buttons";
  const inspect = document.createElement("button");
  inspect.textContent = "Inspect and continue";
  const discard = document.createElement("button");
  discard.textContent = "End task";
  discard.className = "secondary";
  inspect.onclick = async () => {
    card.remove();
    await setWorkflow({ ...workflow, status: "running", consequential: false });
    await sendPrompt(workflow.prompt, {
      continuation: true,
      cycle: workflow.cycle,
    });
  };
  discard.onclick = async () => {
    card.remove();
    await setWorkflow(null);
    appendMessage(
      "Recovered task ended without repeating the uncertain action.",
      "system",
    );
    setStatus("Ready");
  };
  buttons.append(inspect, discard);
  card.append(body, buttons);
  proposals.append(card);
  setStatus("Review interrupted consequential action");
}

$("composer").onsubmit = async (event) => {
  event.preventDefault();
  const prompt = $("prompt").value.trim();
  if (prompt) {
    $("prompt").value = "";
    await sendPrompt(prompt);
  }
};
$("take-over").onclick = async () => {
  if (!busy) return;
  controller?.abort();
  if (activeRunId) {
    try { await stopRun({ settings: await settings(), runId: activeRunId }); } catch (error) { appendMessage(`Take over stop failed: ${error.message}`, "system"); }
  }
  busy = false;
  $("take-over").hidden = true;
  $("stop").hidden = true;
  await setWorkflow({ status: "paused", pausedByUser: true });
  setStatus("Taken over — session paused");
};
$("stop").onclick = async () => {
  if (!busy) return;
  const current = await settings();
  controller?.abort();
  if (activeRunId) {
    try {
      await stopRun({ settings: current, runId: activeRunId });
    } catch (error) {
      appendMessage(`Stop request failed: ${error.message}`, "system");
    }
  }
  await setWorkflow(null);
  setStatus("Stopping…");
};
$("connect-tabs").onclick = async () => {
  try {
    const granted = await chrome.permissions.request({
      permissions: ["tabs"],
      origins: ["http://*/*", "https://*/*"],
    });
    if (!granted) throw new Error("Tab access was not granted.");
    await refreshTabs();
  } catch (error) {
    appendMessage(`Tab connection not enabled: ${error.message}`, "system");
  }
};
$("new-session").onclick = async () => {
  if (busy) return;
  await setWorkflow(null);
  proposals.replaceChildren();
  transcript.replaceChildren();
  appendMessage(
    "Ask Sinria about the selected pages. Safe browser actions run automatically; consequential actions ask once.",
    "assistant",
  );
  await localSet({ sessionId: crypto.randomUUID() });
  setStatus("New session");
  $("prompt").focus();
  await persistPanelState();
};
$("settings-toggle").onclick = () => {
  const panel = $("settings");
  panel.hidden = !panel.hidden;
  $("settings-toggle").setAttribute("aria-expanded", String(!panel.hidden));
};
$("save-settings").onclick = async () => {
  const baseUrl = $("base-url").value.trim();
  const token = $("api-" + "token").value;
  try {
    normalizeBaseUrl(baseUrl);
    const current = await settings();
    await Promise.all([localSet({ baseUrl }), sessionSet({ token })]);
    $("connection-status").textContent = "Testing…";
    await healthCheck({ ...current, baseUrl, token });
    $("connection-status").textContent = "Connected";
  } catch (error) {
    $("connection-status").textContent = `Not connected: ${error.message}`;
  }
};
let panelLease;
while (true) {
  panelLease = await chrome.runtime.sendMessage({ type: "CLAIM_PANEL_LEASE", owner: panelOwnerId });
  if (panelLease?.granted) break;
  $("send").disabled = true;
  $("new-session").disabled = true;
  setStatus("Standby — another Sinria Chrome panel owns this workflow");
  await new Promise((resolve) => setTimeout(resolve, 2000));
}
$("send").disabled = false;
$("new-session").disabled = false;
const leaseHeartbeat = setInterval(() => {
  chrome.runtime.sendMessage({ type: "CLAIM_PANEL_LEASE", owner: panelOwnerId }).catch(() => {});
}, 5000);
addEventListener("pagehide", () => {
  clearInterval(leaseHeartbeat);
  chrome.runtime.sendMessage({ type: "RELEASE_PANEL_LEASE", owner: panelOwnerId }).catch(() => {});
});
researchLedger = mergeResearchLedger([], (await localGet(RESEARCH_LEDGER_KEY))[RESEARCH_LEDGER_KEY] || []);

const restoredState = await restorePanelState();
let initial = await settings();
if (!initial.token) {
  try {
    await loadLocalApiToken();
    initial = await settings();
  } catch (error) {
    $("connection-status").textContent = `Not connected: ${error.message}`;
  }
}
$("base-url").value = initial.baseUrl;
$("api-" + "token").value = initial.token;
$("extension-origin").value = location.origin;
if (initial.token) {
  $("connection-status").textContent = "Connecting…";
  try {
    await healthCheck(initial);
    $("connection-status").textContent = "Connected";
  } catch (error) {
    $("connection-status").textContent = `Not connected: ${error.message}`;
  }
} else {
  $("connection-status").textContent = "Not connected: local credential unavailable";
}
await refreshTabs();
if (restoredState.workflow) {
  appendMessage(
    `Recovered interrupted task at step ${restoredState.workflow.cycle + 1}.`,
    "system",
  );
  if (
    restoredState.workflow.status === "executing" &&
    restoredState.workflow.consequential
  ) {
    renderUncertainRecovery(restoredState.workflow);
  } else {
    setStatus("Resuming recovered task…");
    queueMicrotask(() =>
      sendPrompt(restoredState.workflow.prompt, {
        continuation: true,
        cycle: restoredState.workflow.cycle,
      }),
    );
  }
}
