import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "node:http";
import { buildRunRequest, parseSSE, parseAssistantEnvelope, submitRun, resolveRunApproval } from "../src/api-client.js";

test("run request binds session and strict browser instructions", () => {
  const request = buildRunRequest({ prompt: "summarize", snapshot: { title: "A", url: "https://e.test", text: "body", elements: [] }, sessionId: "chrome-1" });
  assert.equal(request.session_id, "chrome-1");
  assert.equal(request.input, "summarize");
  assert.match(request.instructions, /JSON/);
  assert.match(request.instructions, /actions/);
  assert.equal(request.require_approval, true);
});

test("SSE parser handles split chunks", () => {
  const parsed = parseSSE('data: {"event":"message.delta","delta":"hi"}\n\ndata: {"event":"run.completed","output":"done"}\n\n');
  assert.deepEqual(parsed.map((x) => x.event), ["message.delta", "run.completed"]);
});

test("assistant envelope accepts fenced JSON and defaults safely", () => {
  assert.deepEqual(parseAssistantEnvelope('```json\n{"message":"ok","actions":[]}\n```'), { message: "ok", actions: [] });
  assert.deepEqual(parseAssistantEnvelope("plain answer"), { message: "plain answer", actions: [] });
});

test("run client streams gateway events and resolves approval choices", async () => {
  const requests = [];
  const server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      requests.push({ method: request.method, url: request.url, headers: request.headers, body });
      if (request.method === "POST" && request.url === "/v1/runs") {
        response.writeHead(202, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ run_id: "run_1" }));
      } else if (request.method === "GET" && request.url === "/v1/runs/run_1/events") {
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        response.write('data: {"event":"message.delta","delta":"hel"}\n\n');
        setTimeout(() => response.end('data: {"event":"run.completed","output":"{\\"message\\":\\"done\\",\\"actions\\":[]}"}\n\n'), 5);
      } else if (request.method === "POST" && request.url === "/v1/runs/run_1/approval") {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ ok: true }));
      } else {
        response.writeHead(404).end();
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const settings = { baseUrl: `http://127.0.0.1:${server.address().port}`, token: "session-secret", sessionId: "chrome-test" };
  const events = [];
  try {
    const completed = await submitRun({ settings, prompt: "summarize", snapshot: { title: "Test", elements: [] }, onEvent: (event) => events.push(event.event) });
    assert.equal(completed.finalEvent.event, "run.completed");
    await resolveRunApproval({ settings, runId: "run_1", choice: "once" });
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  assert.deepEqual(events, ["message.delta", "run.completed"]);
  const create = requests.find((request) => request.url === "/v1/runs");
  assert.equal(JSON.parse(create.body).input, "summarize");
  assert.equal(create.headers["x-sinria-session-id"], "chrome-test");
  assert.equal(create.headers.authorization, "Bearer session-secret");
  const approval = requests.find((request) => request.url.endsWith("/approval"));
  assert.deepEqual(JSON.parse(approval.body), { choice: "once" });
});
