import { capturePageSnapshot } from "../../src/snapshot.js";
import { executePageAction } from "../../src/action-executor.js";
import http from "node:http";

const port = Number(process.env.CDP_PORT || 9336);
const fixtureBase = process.env.FIXTURE_BASE_URL || "http://127.0.0.1:8766";
const list = await new Promise((resolve, reject) => http.get(`http://127.0.0.1:${port}/json/list`, (response) => {
  let body = "";
  response.on("data", (chunk) => { body += chunk; });
  response.on("end", () => resolve(JSON.parse(body)));
  response.on("error", reject);
}));
const target = list.find((item) => item.type === "page" && !item.url.startsWith("chrome-extension:"));
if (!target) throw new Error("No inspectable page target found.");
const socket = new WebSocket(target.webSocketDebuggerUrl);
let sequence = 0;
const pending = new Map();
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.error) request.reject(message.error);
  else request.resolve(message.result);
};
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
await call("Network.enable");
await call("Network.setCacheDisabled", { cacheDisabled: true });
await call("Page.navigate", { url: `${fixtureBase}/smoke.html?run=${Date.now()}` });
await new Promise((resolve) => setTimeout(resolve, 500));
const evaluate = async (expression) => (await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result.value;
const snapshot = await evaluate(`(${capturePageSnapshot.toString()})()`);
const buttonTarget = snapshot.elements.find((element) => element.tag === "button");
const queryTarget = snapshot.elements.find((element) => element.field?.name === "query")
  || snapshot.elements.find((element) => element.tag === "input" && !element.sensitive);
const protectedField = snapshot.elements.find((element) => element.field?.name === "password")
  || snapshot.elements.find((element) => element.sensitive);
if (!buttonTarget || !queryTarget || !protectedField) {
  const diagnostics = await evaluate(`({html: document.body?.innerHTML, visibility: document.visibilityState, rect: document.querySelector('#go')?.getBoundingClientRect().toJSON()})`);
  throw new Error(`Incomplete fixture snapshot: ${JSON.stringify({ elements: snapshot.elements, diagnostics })}`);
}
const click = await evaluate(`(${executePageAction.toString()})(${JSON.stringify({ type: "click", ref: buttonTarget.ref })}, ${JSON.stringify(buttonTarget)})`);
const type = await evaluate(`(${executePageAction.toString()})(${JSON.stringify({ type: "type", ref: queryTarget.ref, text: "Sinria typed" })}, ${JSON.stringify(queryTarget)})`);
await evaluate(`document.querySelector('[data-sinria-ref="${buttonTarget.ref}"]').setAttribute('data-sinria-guard', 'changed')`);
const stale = await evaluate(`(${executePageAction.toString()})(${JSON.stringify({ type: "click", ref: buttonTarget.ref })}, ${JSON.stringify(buttonTarget)})`);
const button = await evaluate("document.querySelector('#go').textContent");
const query = await evaluate("document.querySelector('[name=query]').value");
const navigate = await evaluate(`(${executePageAction.toString()})(${JSON.stringify({ type: "navigate", url: `${fixtureBase}/navigated.html` })})`);
await new Promise((resolve) => setTimeout(resolve, 500));
const navigatedTitle = await evaluate("document.title");
const result = {
  title: snapshot.title,
  sensitiveRedacted: protectedField?.value === "[REDACTED]",
  clickSucceeded: click.ok,
  typeSucceeded: type.ok,
  staleActionBlocked: stale.ok === false,
  navigateSucceeded: navigate.ok && navigatedTitle === "Sinria navigated",
  button,
  query,
  navigatedTitle
};
console.log(JSON.stringify(result));
socket.close();
if (!result.sensitiveRedacted || !result.clickSucceeded || !result.typeSucceeded || !result.staleActionBlocked || !result.navigateSucceeded || button !== "Clicked" || query !== "Sinria typed") process.exitCode = 1;
