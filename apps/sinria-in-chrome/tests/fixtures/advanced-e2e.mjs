import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { receiptKey } from "../../src/action-receipts.js";

const port = Number(process.env.CDP_PORT || 9346);
const base = process.env.FIXTURE_BASE_URL || "http://127.0.0.1:8767";
let extensionId = process.env.EXTENSION_ID || "pebcacnleolamclolgncigkgojkdghgc";
const downloadDir = process.env.DOWNLOAD_DIR;

const jsonRequest = (method, requestPath) => new Promise((resolve, reject) => {
  const request = http.request({ host: "127.0.0.1", port, method, path: requestPath }, (response) => {
    let body = "";
    response.on("data", (chunk) => { body += chunk; });
    response.on("end", () => {
      try { resolve(JSON.parse(body)); } catch (error) { reject(new Error(`${response.statusCode}: ${body}`)); }
    });
  });
  request.on("error", reject);
  request.end();
});

function cdp(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  let sequence = 0;
  const pending = new Map();
  const listeners = new Map();
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id); pending.delete(message.id);
      message.error ? waiter.reject(new Error(message.error.message)) : waiter.resolve(message.result);
      return;
    }
    for (const listener of listeners.get(message.method) || []) listener(message.params);
  };
  const ready = new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const call = async (method, params = {}) => {
    await ready;
    return new Promise((resolve, reject) => {
      const id = ++sequence; pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  };
  return { socket, call, on(method, listener) { const list = listeners.get(method) || []; list.push(listener); listeners.set(method, list); } };
}

let targets = await jsonRequest("GET", "/json/list");
for (let index = 0; index < 30 && !targets.some((target) => target.url?.includes("/src/service-worker.js")); index += 1) {
  await new Promise((resolve) => setTimeout(resolve, 100));
  targets = await jsonRequest("GET", "/json/list");
}
const workerTarget = targets.find((target) => target.url?.startsWith("chrome-extension://") && target.url.includes("/src/service-worker.js"));
if (!workerTarget) throw new Error(`Extension service worker was not loaded: ${JSON.stringify(targets.map((target) => target.url))}`);
extensionId = new URL(workerTarget.url).host;
const advancedTarget = await jsonRequest("PUT", `/json/new?${encodeURIComponent(`${base}/advanced.html`)}`);
const extensionTarget = await jsonRequest("PUT", `/json/new?${encodeURIComponent(`chrome-extension://${extensionId}/sidepanel.html`)}`);
const ext = cdp(extensionTarget.webSocketDebuggerUrl);
await ext.call("Runtime.enable");
const evaluate = async (expression) => {
  const result = await ext.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
};
const runtimeRequest = (message) => evaluate(`chrome.runtime.sendMessage(${JSON.stringify(message)})`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
await wait(800);
const fixturePage = cdp(advancedTarget.webSocketDebuggerUrl);
await fixturePage.call("Runtime.enable");
for (let index = 0; index < 50; index += 1) {
  const ready = await fixturePage.call("Runtime.evaluate", {
    expression: "Boolean(document.querySelector('#shadow-host')?.shadowRoot?.querySelector('button') && document.querySelector('#same-origin-frame')?.contentDocument?.querySelector('button'))",
    returnByValue: true,
  });
  if (ready.result.value) break;
  if (index === 49) throw new Error("Advanced fixture did not become DOM-ready.");
  await wait(100);
}
const tab = await evaluate(`chrome.tabs.query({}).then(ts=>ts.find(t=>t.url==="${base}/advanced.html"))`);
if (!tab?.id) throw new Error("Advanced fixture tab was not found.");
await evaluate(`chrome.tabs.update(${tab.id},{active:true})`);
await wait(300);
let snapshotResponse;
let snapshot;
for (let index = 0; index < 30; index += 1) {
  snapshotResponse = await runtimeRequest({ type: "SNAPSHOT", tabId: tab.id });
  if (!snapshotResponse?.ok) throw new Error(snapshotResponse?.error || "Snapshot failed");
  snapshot = snapshotResponse.snapshot;
  const labels = snapshot.elements.map((item) => String(item.label || ""));
  if (labels.some((value) => value.includes("Shadow button")) && labels.some((value) => value.includes("Frame button"))) break;
  await wait(150);
}
const byLabel = (text) => snapshot.elements.find((item) => String(item.label || "").includes(text));
const shadow = byLabel("Shadow button");
const nested = byLabel("Nested shadow input");
const frame = byLabel("Frame button");
let upload = byLabel("Select approved document");
let download = byLabel("Download report");
if (!shadow || !nested || !frame || !upload || !download) {
  const debugRoots = await evaluate(`chrome.scripting.executeScript({target:{tabId:${tab.id},allFrames:true},func:()=>{const shadow=document.querySelector('#shadow-host')?.shadowRoot; const nodes=[shadow?.querySelector('#shadow-button'),shadow?.querySelector('#nested-host')?.shadowRoot?.querySelector('#nested-input'),document.querySelector('#same-origin-frame')?.contentDocument?.querySelector('#frame-button')].filter(Boolean); return {href:location.href,shadow:Boolean(shadow),frame:Boolean(document.querySelector('#same-origin-frame')?.contentDocument),nodes:nodes.map(node=>{const box=node.getBoundingClientRect(); const style=getComputedStyle(node); return {label:node.textContent||node.getAttribute('aria-label'),width:box.width,height:box.height,display:style.display,visibility:style.visibility};}),buttons:[...document.querySelectorAll('button')].map(x=>x.textContent)};}})`);
  console.error(JSON.stringify({ elements: snapshot.elements, debugRoots }, null, 2));
  throw new Error("Deep DOM or file targets were not captured.");
}
if (!snapshot.elements.some((item) => item.rootPath?.length >= 2)) throw new Error("Nested Shadow DOM path was not retained.");
if (!snapshot.elements.some((item) => item.framePath?.length >= 1)) throw new Error("iframe path was not retained.");
if (!snapshot.crossOriginFrames?.length) throw new Error("Cross-origin iframe boundary was not reported.");
if (!snapshot.canvases?.length || !snapshot.charts?.length) throw new Error("Canvas/chart descriptors were not captured.");
const resultCard = snapshot.cards?.find((card) => (card.labels || []).some((label) => String(label).includes("Example Candidate is reachable")));
if (!resultCard || !String(resultCard.text || "").includes("Chief Executive Officer") || !String(resultCard.text || "").includes("Example Health Systems")) {
  throw new Error(`Structured result card omitted role/company readback: ${JSON.stringify(snapshot.cards || [])}`);
}
let screenshotLocalPath = snapshot.visual?.localPath || "";
let nativeBridgeViaBrowser = Boolean(screenshotLocalPath && fs.existsSync(screenshotLocalPath));
if (!nativeBridgeViaBrowser) {
  const dataUrl = snapshot.screenshot?.dataUrl || "";
  const comma = dataUrl.indexOf(",");
  if (comma < 0) throw new Error(`Screenshot capture failed for ${extensionId}: ${snapshot.visual?.storeError || "no_data"}`);
  screenshotLocalPath = path.join(downloadDir, "captured-page.jpg");
  fs.writeFileSync(screenshotLocalPath, Buffer.from(dataUrl.slice(comma + 1), "base64"));
  if (!fs.statSync(screenshotLocalPath).size) throw new Error("Captured screenshot was empty.");
}

let receipt = 0;
const action = (payload, expected = null) => {
  const requested = { tabId: payload.tabId ?? tab.id, ...payload };
  receipt += 1;
  return runtimeRequest({
    type: "ACTION",
    action: requested,
    expected,
    executionId: receiptKey("advanced", receipt, requested),
  });
};
let result = await action({ type: "click", ref: shadow.ref }, { ...shadow, tabId: tab.id });
if (!result?.ok) throw new Error(`Shadow click failed: ${result?.error}`);
snapshot = (await runtimeRequest({ type: "SNAPSHOT", tabId: tab.id })).snapshot;
if (!snapshot.elements.some((item) => String(item.label || "").includes("Shadow clicked"))) throw new Error("Shadow click readback failed.");
upload = snapshot.elements.find((item) => String(item.label || "").includes("Select approved document"));
download = snapshot.elements.find((item) => String(item.label || "").includes("Download report"));

const page = cdp(advancedTarget.webSocketDebuggerUrl);
await page.call("Page.enable");
result = await action({ type: "choose_file", ref: upload.ref }, { ...upload, tabId: tab.id });
if (!result?.ok || !result.requiresUserTakeover) throw new Error(`File chooser takeover failed: ${result?.error || "missing takeover"}`);
const highlighted = await page.call("Runtime.evaluate", { expression: "document.querySelector('#upload')?.getAttribute('data-sinria-highlighted')", returnByValue: true });
if (highlighted.result.value !== "true") throw new Error("File chooser target was not highlighted for takeover.");

await page.call("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloadDir });
result = await action({ type: "download", ref: download.ref }, { ...download, tabId: tab.id });
if (!result?.ok) throw new Error(`Download action failed: ${result?.error}`);
for (let index = 0; index < 30 && !fs.existsSync(path.join(downloadDir, "sinria-download.txt")); index += 1) await wait(100);
if (!fs.existsSync(path.join(downloadDir, "sinria-download.txt"))) throw new Error("Download readback failed.");

result = await action({ type: "open_tab", url: `${base}/sample-report.pdf` });
if (!result?.ok || !result.tabId) throw new Error(`PDF tab failed: ${result?.error}`);
const pdfTabId = result.tabId;
await action({ type: "activate_tab", tabId: pdfTabId });
await wait(700);
const pdfSnapshotResponse = await runtimeRequest({ type: "SNAPSHOT", tabId: pdfTabId });
if (!pdfSnapshotResponse?.ok || pdfSnapshotResponse.snapshot?.document?.kind !== "pdf") throw new Error(`PDF fallback failed: ${pdfSnapshotResponse?.error || "missing document kind"}`);
const pdfDataUrl = pdfSnapshotResponse.snapshot?.screenshot?.dataUrl || "";
const pdfComma = pdfDataUrl.indexOf(",");
if (pdfComma < 0) throw new Error("PDF screenshot capture failed.");
const pdfScreenshot = path.join(downloadDir, "captured-pdf.jpg");
fs.writeFileSync(pdfScreenshot, Buffer.from(pdfDataUrl.slice(pdfComma + 1), "base64"));
if (!fs.statSync(pdfScreenshot).size) throw new Error("PDF screenshot was empty.");
await action({ type: "close_tab", tabId: pdfTabId });

result = await action({ type: "open_tab", url: `${base}/advanced.html` });
if (!result?.ok || !result.tabId) throw new Error(`open_tab failed: ${result?.error}`);
const lifecycleTab = result.tabId;
await wait(1200);
result = await action({ type: "activate_tab", tabId: lifecycleTab });
if (!result?.ok) throw new Error(`activate_tab failed: ${result?.error}`);
result = await action({ type: "navigate", tabId: lifecycleTab, url: `${base}/navigated.html` });
if (!result?.ok || result.openedNewTab) throw new Error(`navigate failed: ${result?.error || "unexpected new tab"}`);
let lifecycleState = null;
for (let index = 0; index < 40; index += 1) {
  lifecycleState = await evaluate(`chrome.tabs.get(${lifecycleTab})`);
  if (lifecycleState?.url === `${base}/navigated.html`) break;
  await wait(100);
}
if (lifecycleState?.url !== `${base}/navigated.html`) throw new Error(`navigate readback failed: ${lifecycleState?.url}`);
const lifecycleTargets = await jsonRequest("GET", "/json/list");
const lifecycleTarget = lifecycleTargets.find((target) => target.url === `${base}/navigated.html`);
if (!lifecycleTarget) throw new Error("Lifecycle page CDP target was not found.");
const lifecyclePage = cdp(lifecycleTarget.webSocketDebuggerUrl);
await lifecyclePage.call("Page.enable");
const historyBeforeBack = await lifecyclePage.call("Page.getNavigationHistory");
if (historyBeforeBack.entries.length < 2) throw new Error(`Navigation history was not created: ${JSON.stringify(historyBeforeBack)}`);
result = await action({ type: "back", tabId: lifecycleTab });
if (!result?.ok) throw new Error(`back failed: ${result?.error}`);
result = await action({ type: "forward", tabId: lifecycleTab });
if (!result?.ok) throw new Error(`forward failed: ${result?.error}`);
result = await action({ type: "reload", tabId: lifecycleTab });
if (!result?.ok) throw new Error(`reload failed: ${result?.error}`);
result = await action({ type: "close_tab", tabId: lifecycleTab });
if (!result?.ok) throw new Error(`close_tab failed: ${result?.error}`);
const closed = await evaluate(`chrome.tabs.get(${lifecycleTab}).then(()=>false).catch(()=>true)`);
if (!closed) throw new Error("Closed tab still exists.");
lifecyclePage.socket.close();
fixturePage.socket.close();

console.log(JSON.stringify({
  deepDom: true,
  screenshot: screenshotLocalPath,
  nativeBridgeViaBrowser,
  documentDescriptors: { canvases: snapshot.canvases.length, charts: snapshot.charts.length, pdf: true },
  pdfScreenshot,
  fileChooser: true,
  download: true,
  browserControls: true,
}));
ext.socket.close();
page.socket.close();
