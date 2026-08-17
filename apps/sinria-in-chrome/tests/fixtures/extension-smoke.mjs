import http from "node:http";

const port = Number(process.env.CDP_PORT || 9340);
const extensionId = process.env.EXTENSION_ID || "pebcacnleolamclolgncigkgojkdghgc";
const requestJson = (method, path) => new Promise((resolve, reject) => {
  const request = http.request({ host: "127.0.0.1", port, method, path }, (response) => {
    let body = "";
    response.on("data", (chunk) => { body += chunk; });
    response.on("end", () => resolve(JSON.parse(body)));
  });
  request.on("error", reject);
  request.end();
});
const target = await requestJson("PUT", `/json/new?${encodeURIComponent(`chrome-extension://${extensionId}/sidepanel.html`)}`);
const socket = new WebSocket(target.webSocketDebuggerUrl);
let sequence = 0;
const pending = new Map();
const exceptions = [];
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  socket.send(JSON.stringify({ id, method, params }));
});
socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails.text);
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.error) request.reject(message.error); else request.resolve(message.result);
};
await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
await call("Runtime.enable");
const evaluate = async (expression) => {
  const response = await call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
  return response.result.value;
};
for (let i = 0; i < 40; i += 1) {
  if (await evaluate("Boolean(document.querySelector('#save-settings'))")) break;
  await new Promise((resolve) => setTimeout(resolve, 50));
}
await evaluate(`chrome.storage.session.set({token: "ephemeral-smoke-token"})`);
const storage = await evaluate(`Promise.all([chrome.storage.session.get(["token"]), chrome.storage.local.get(["token"])]).then(([session, local]) => ({sessionHasToken: session.token === "ephemeral-smoke-token", localHasToken: Object.hasOwn(local, "token")}))`);
await evaluate(`document.querySelector('#api-token').value = ""; document.querySelector('#save-settings').click()`);
for (let i = 0; i < 50; i += 1) {
  const status = await evaluate("document.querySelector('#connection-status').textContent");
  if (status === "Connected" || status.startsWith("Not connected:")) break;
  await new Promise((resolve) => setTimeout(resolve, 50));
}
const result = await evaluate(`({title: document.title, origin: document.querySelector('#extension-origin').value, connection: document.querySelector('#connection-status').textContent})`);
await evaluate(`chrome.storage.session.remove(["token"])`);
socket.close();
console.log(JSON.stringify({ ...result, ...storage, exceptions }));
if (result.title !== "Sinria" || result.connection !== "Connected" || !storage.sessionHasToken || storage.localHasToken || exceptions.length) process.exitCode = 1;
