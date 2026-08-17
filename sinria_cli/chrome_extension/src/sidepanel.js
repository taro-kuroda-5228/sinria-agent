import { submitRun, resolveRunApproval, healthCheck, parseAssistantEnvelope, normalizeBaseUrl } from "./api-client.js";
import { validateAction, actionSummary } from "./action-policy.js";

const $ = (id) => document.getElementById(id);
const transcript = $("transcript");
const proposals = $("proposals");
let busy = false;
let currentSnapshot = null;

function appendMessage(text, role = "assistant") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article.textContent = String(text || "");
  transcript.append(article);
  transcript.scrollTop = transcript.scrollHeight;
  return article;
}

function setStatus(text) { $("status").textContent = text; }
function localGet(keys) { return chrome.storage.local.get(keys); }
function localSet(value) { return chrome.storage.local.set(value); }
function sessionGet(keys) { return chrome.storage.session.get(keys); }
function sessionSet(value) { return chrome.storage.session.set(value); }

async function settings() {
  const [saved, secrets] = await Promise.all([
    localGet(["baseUrl", "sessionId"]),
    sessionGet(["token"])
  ]);
  if (!saved.sessionId) {
    saved.sessionId = `sinria-chrome-${crypto.randomUUID()}`;
    await localSet({ sessionId: saved.sessionId });
  }
  return { baseUrl: saved.baseUrl || "http://127.0.0.1:8642", token: secrets.token || "", sessionId: saved.sessionId };
}

async function capture() {
  const response = await chrome.runtime.sendMessage({ type: "snapshot" });
  if (!response?.ok) throw new Error(response?.error || "Unable to inspect the active page.");
  currentSnapshot = response.snapshot;
  return currentSnapshot;
}

function clearProposals() { proposals.replaceChildren(); }

function proposalCard(title, detail) {
  const card = document.createElement("article");
  card.className = "proposal";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const body = document.createElement("div");
  body.textContent = detail;
  const buttons = document.createElement("div");
  buttons.className = "buttons";
  card.append(heading, body, buttons);
  proposals.append(card);
  return { card, buttons };
}

function renderBrowserAction(action) {
  const target = action.type === "navigate"
    ? null
    : currentSnapshot?.elements?.find((element) => element.ref === action.ref);
  if (action.type !== "navigate" && !target) {
    appendMessage("Blocked stale action proposal: the target is not in the approved page snapshot.", "system");
    return;
  }
  const guardedAction = target
    ? { ...action, field: { ...target.field, tag: target.tag, contenteditable: target.contenteditable } }
    : action;
  const verdict = validateAction(guardedAction);
  if (!verdict.ok) {
    appendMessage(`Blocked unsafe action proposal: ${verdict.reason}`, "system");
    return;
  }
  const { card, buttons } = proposalCard("Approval required", actionSummary(action, target));
  const approve = document.createElement("button");
  approve.type = "button";
  approve.textContent = "Approve once";
  const deny = document.createElement("button");
  deny.type = "button";
  deny.className = "deny";
  deny.textContent = "Deny";
  buttons.append(approve, deny);
  deny.addEventListener("click", () => { card.remove(); appendMessage("Browser action denied.", "system"); });
  approve.addEventListener("click", async () => {
    approve.disabled = true; deny.disabled = true; setStatus("Executing approved action…");
    try {
      const result = await chrome.runtime.sendMessage({ type: "execute-action", action, expected: target });
      if (!result?.ok) throw new Error(result?.error || result?.reason || "Action failed.");
      card.remove();
      appendMessage(`Verified browser action: ${result.detail}`, "system");
      await new Promise((resolve) => setTimeout(resolve, action.type === "navigate" ? 1200 : 300));
      try {
        const readback = await capture();
        appendMessage(`Readback: ${readback.title} — ${readback.url}`, "system");
      } catch (readbackError) {
        const guidance = action.type === "navigate"
          ? " Click the extension icon on the destination page to grant temporary access, then ask Sinria to verify it."
          : "";
        appendMessage(`Action executed, but readback was unavailable: ${readbackError.message}.${guidance}`, "system");
      }
    } catch (error) {
      appendMessage(`Action stopped: ${error.message}`, "system");
      approve.disabled = false; deny.disabled = false;
    } finally { setStatus("Ready"); }
  });
}

function renderRunApproval(event, runId, currentSettings) {
  const detail = event.description || event.preview || event.message || "Sinria requested permission for a protected tool action.";
  const { card, buttons } = proposalCard("Sinria tool approval", detail);
  for (const [label, choice, style] of [["Approve once", "once", ""], ["Deny", "deny", "deny"]]) {
    const button = document.createElement("button");
    button.type = "button"; button.textContent = label; button.className = style;
    button.addEventListener("click", async () => {
      for (const child of buttons.children) child.disabled = true;
      try { await resolveRunApproval({ settings: currentSettings, runId, choice }); card.remove(); }
      catch (error) { appendMessage(`Approval failed: ${error.message}`, "system"); for (const child of buttons.children) child.disabled = false; }
    });
    buttons.append(button);
  }
}

async function sendPrompt(prompt) {
  if (busy) return;
  busy = true; $("send").disabled = true; clearProposals(); appendMessage(prompt, "user");
  try {
    setStatus("Reading current page…");
    const snapshot = await capture();
    const currentSettings = await settings();
    setStatus("Sinria is working…");
    let output = "";
    await submitRun({
      settings: currentSettings, prompt, snapshot,
      onEvent: async (event, runId) => {
        if (event.event === "approval.request") renderRunApproval(event, runId, currentSettings);
        if (event.event === "run.failed") throw new Error(event.error || "Sinria run failed.");
        if (event.event === "run.completed") output = event.output || "";
        if (event.event === "tool.start") setStatus(`Using ${event.tool || "a tool"}…`);
      }
    });
    const envelope = parseAssistantEnvelope(output);
    appendMessage(envelope.message || "Sinria completed without a text response.");
    for (const action of envelope.actions) renderBrowserAction(action);
    setStatus(envelope.actions.length ? "Waiting for your approval" : "Ready");
  } catch (error) {
    const corsHint = String(error.message).includes("403")
      ? ` Add ${location.origin} to API_SERVER_CORS_ORIGINS and restart the Sinria API.`
      : "";
    appendMessage(`Sinria could not complete the request: ${error.message}.${corsHint}`, "system");
    setStatus("Stopped safely");
  } finally { busy = false; $("send").disabled = false; }
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault(); const prompt = $("prompt").value.trim(); if (!prompt) return;
  $("prompt").value = ""; await sendPrompt(prompt);
});

$("settings-toggle").addEventListener("click", () => {
  const panel = $("settings"); panel.hidden = !panel.hidden;
  $("settings-toggle").setAttribute("aria-expanded", String(!panel.hidden));
});

$("save-settings").addEventListener("click", async () => {
  const baseUrl = $("base-url").value.trim(); const token = $("api-token").value;
  try {
    normalizeBaseUrl(baseUrl);
    const current = await settings();
    await Promise.all([localSet({ baseUrl }), sessionSet({ token })]);
    $("connection-status").textContent = "Testing…";
    await healthCheck({ ...current, baseUrl, token });
    $("connection-status").textContent = "Connected";
  } catch (error) { $("connection-status").textContent = `Not connected: ${error.message}`; }
});

const initial = await settings();
$("base-url").value = initial.baseUrl;
$("api-token").value = initial.token;
$("extension-origin").value = location.origin;
