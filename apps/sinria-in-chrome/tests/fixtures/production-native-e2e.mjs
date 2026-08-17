import http from "node:http";

const port = Number(process.env.CDP_PORT || 9361);
const extensionId = process.env.EXTENSION_ID || "pebcacnleolamclolgncigkgojkdghgc";
const fixtureUrl = process.env.FIXTURE_URL || "http://127.0.0.1:8770/smoke.html";
const token = process.env["API_" + "SERVER_KEY"];
if (!token) throw new Error("API_SERVER_KEY is required.");

function httpJson(path, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path, method }, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => resolve(JSON.parse(body)));
    });
    req.on("error", reject);
    req.end();
  });
}

function connect(url) {
  const ws = new WebSocket(url);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (!message.id) return;
    const waiter = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) waiter.reject(message.error);
    else waiter.resolve(message.result);
  };
  const ready = new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  return {
    ws,
    ready,
    call(method, params = {}) {
      return new Promise((resolve, reject) => {
        const callId = ++id;
        pending.set(callId, { resolve, reject });
        ws.send(JSON.stringify({ id: callId, method, params }));
      });
    }
  };
}

async function evaluate(client, expression) {
  const result = await client.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Runtime evaluation failed.");
  return result.result?.value;
}

async function waitFor(client, expression, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await evaluate(client, expression);
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Timed out waiting for the Sinria Chrome workflow.");
}

const version = await httpJson("/json/version");
const browser = connect(version.webSocketDebuggerUrl);
await browser.ready;

let targets = await httpJson("/json/list");
const fixtureSeed = targets.find((target) => target.type === "page" && target.url.startsWith(fixtureUrl));
if (!fixtureSeed) throw new Error("Fixture seed target is not open.");
if (process.env.INJECT_FIXTURE === "1") {
  const seedClient = connect(fixtureSeed.webSocketDebuggerUrl);
  await seedClient.ready;
  await seedClient.call("Page.stopLoading");
  await evaluate(seedClient, `(()=>{document.title='Sinria smoke';document.body.innerHTML='<main><h1>Smoke page</h1><button id="go" onclick="this.textContent=\\'Clicked\\'">Continue</button><input name="password" type="password" value="do-not-leak"><input name="query" value="visible"></main>';return true})()`);
  seedClient.ws.close();
}
let extension = targets.find((target) => target.type === "page" && target.url.startsWith(`chrome-extension://${extensionId}/`));
if (!extension) {
  await browser.call("Target.createTarget", {
    url: `chrome-extension://${extensionId}/sidepanel.html`,
    background: true
  });
  targets = await httpJson("/json/list");
  extension = targets.find((target) => target.type === "page" && target.url.startsWith(`chrome-extension://${extensionId}/`));
}
if (!extension) throw new Error("Sinria extension page is not open.");
const panel = connect(extension.webSocketDebuggerUrl);
await panel.ready;
await panel.call("Runtime.enable");

await evaluate(panel, `(async()=>{
  await chrome.storage.local.set({baseUrl:'http://127.0.0.1:8642',sessionId:'extension-production-readback'});
  await chrome.storage.session.set({token:${JSON.stringify(token)}});
  return true;
})()`);
await browser.call("Target.activateTarget", { targetId: fixtureSeed.id });
await evaluate(panel, `(async()=>{
  document.querySelector('#prompt').value='On this page, propose clicking the Continue button. Return exactly JSON with a short message and one click action using the matching element ref.';
  document.querySelector('#send').click();
  return true;
})()`);

const approval = await waitFor(panel, `(()=>{
  const buttons=[...document.querySelectorAll('button')];
  const approve=buttons.find(button=>button.textContent==='Approve once'&&!button.disabled);
  const stopped=document.querySelector('#status')?.textContent==='Stopped safely';
  if(stopped) throw new Error([...document.querySelectorAll('.message')].map(x=>x.textContent).join(' | '));
  return approve?{status:document.querySelector('#status')?.textContent,messages:[...document.querySelectorAll('.message')].map(x=>x.textContent).slice(-3)}:null;
})()`);

await evaluate(panel, `(()=>{const button=[...document.querySelectorAll('button')].find(x=>x.textContent==='Approve once'&&!x.disabled);button.click();return true})()`);

const fixtureTargets = await httpJson("/json/list");
const fixture = fixtureTargets.find(target => target.type === "page" && target.url.startsWith(fixtureUrl));
if (!fixture) throw new Error("Fixture target disappeared before readback.");
const fixtureClient = connect(fixture.webSocketDebuggerUrl);
await fixtureClient.ready;
const clicked = await waitFor(fixtureClient, `document.querySelector('#go')?.textContent==='Clicked'` , 30000);
const verified = await waitFor(panel, `(()=>{const text=[...document.querySelectorAll('.message')].map(x=>x.textContent).join(' | ');return text.includes('Verified browser action:')&&text.includes('Readback:')})()`, 30000);
const localHasToken = await evaluate(panel, `chrome.storage.local.get(null).then(value=>Object.hasOwn(value,'token'))`);
const sessionHasToken = await evaluate(panel, `chrome.storage.session.get('token').then(value=>Boolean(value.token))`);

console.log(JSON.stringify({
  connected: true,
  proposalReady: Boolean(approval),
  approvedOnce: true,
  clicked: Boolean(clicked),
  verifiedReadback: Boolean(verified),
  sessionHasToken,
  localHasToken,
  exceptions: []
}));

fixtureClient.ws.close();
panel.ws.close();
browser.ws.close();
