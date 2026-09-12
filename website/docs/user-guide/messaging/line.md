---
sidebar_position: 17
title: "LINE"
description: "Set up Sinria as a LINE Messaging API bot"
---

# LINE Setup

Run Sinria as a [LINE](https://line.me/) bot via the official LINE Messaging API. The adapter lives as a bundled platform plugin under `plugins/platforms/line/` — no core edits, just enable it like any other platform.

LINE is the dominant messaging app in Japan, Taiwan, and Thailand. If your users live there, this is how they reach you.

## How the bot responds

| Context | Behavior |
|---------|----------|
| **1:1 chat** (`U` IDs) | Responds to every message |
| **Group chat** (`C` IDs) | Responds when the group is on the allowlist |
| **Multi-user room** (`R` IDs) | Responds when the room is on the allowlist |

Inbound text, images, audio, video, files, stickers, and locations are all handled. Outbound text uses the **free reply token first** (single-use, ~60s window) and falls back to the metered Push API when the token has expired.

---

## Step 1: Create a LINE Messaging API channel

1. Go to the [LINE Developers Console](https://developers.line.biz/console/).
2. Create a Provider, then under it a **Messaging API** channel.
3. From the channel's **Basic settings** tab, copy the **Channel secret**.
4. From the **Messaging API** tab, scroll to **Channel access token (long-lived)** and click **Issue**. Copy the token.
5. In the **Messaging API** tab, also disable **Auto-reply messages** and **Greeting messages** so they don't fight your bot's replies.

---

## Step 2: Expose the webhook port

LINE delivers webhooks over public HTTPS. The default port is `8646` — override with `LINE_PORT` if needed.

```bash
# Cloudflare Tunnel (recommended for production — fixed hostname)
cloudflared tunnel --url http://localhost:8646

# ngrok (good for dev)
ngrok http 8646

# devtunnel
devtunnel create sinria-line --allow-anonymous
devtunnel port create sinria-line -p 8646 --protocol https
devtunnel host sinria-line
```

Copy the `https://...` URL — you'll set it as the webhook URL below. **Leave the tunnel running** while testing. For production, set up a fixed Cloudflare named tunnel so the webhook URL doesn't change on restart.

---

## Step 3: Configure Sinria

Add to `~/.sinria/.env`:

```env
LINE_CHANNEL_ACCESS_TOKEN=YOUR_LONG_LIVED_TOKEN
LINE_CHANNEL_SECRET=YOUR_CHANNEL_SECRET

# Allowlist — at least one of these (or LINE_ALLOW_ALL_USERS=true for dev)
LINE_ALLOWED_USERS=U1234567890abcdef...           # comma-separated U-prefixed IDs
LINE_ALLOWED_GROUPS=C1234567890abcdef...          # optional group IDs
LINE_ALLOWED_ROOMS=R1234567890abcdef...           # optional room IDs

# Required for image / audio / video sends — the public HTTPS base URL
# the tunnel resolves to.  Without it, send_image/voice/video will refuse.
LINE_PUBLIC_URL=https://my-tunnel.example.com
```

Then in `~/.sinria/config.yaml`:

```yaml
gateway:
  platforms:
    line:
      enabled: true
```

That's enough — the bundled-plugin scan in `gateway/config.py` automatically picks up `plugins/platforms/line/`. No `Platform.LINE` enum edit, no `_create_adapter` registration.

---

## Step 4: Set the webhook URL

Back in the LINE console:

1. Open your channel → **Messaging API** tab.
2. Under **Webhook settings** → **Webhook URL**, paste `https://<your-tunnel>/line/webhook` (note the `/line/webhook` path — the adapter listens there).
3. Click **Verify**. LINE pings the URL; you should see a 200.
4. Toggle **Use webhook** to **On**.

---

## Step 5: Run the gateway

```bash
sinria gateway run
```

The agent log shows:

```
LINE: webhook listening on 0.0.0.0:8646/line/webhook (public: https://my-tunnel.example.com)
```

Add the bot as a friend from the LINE app (scan the QR in the channel's **Messaging API** tab) and send it a message.

---

## Slow LLM responses

LINE's reply token is single-use and expires roughly 60 seconds after the inbound event. Slow LLMs can't reply in time, which would normally force a paid Push API call.

When the LLM is still running past `LINE_SLOW_RESPONSE_THRESHOLD` seconds (default `45`), the adapter consumes the original reply token to send a **Template Buttons** bubble:

> 🤔 Still thinking. Tap below to fetch the answer when it's ready.
>
> [ Get answer ]

The user taps **Get answer** when convenient — that postback delivers a *fresh* reply token, which the adapter uses to send the cached answer (still free).

State machine: `PENDING → READY → DELIVERED`, plus `ERROR` for cancelled runs (the orphan PENDING resolves to "Run was interrupted before completion." after `/stop` so the persistent button doesn't loop).

To disable the postback button and always Push-fallback instead:

```env
LINE_SLOW_RESPONSE_THRESHOLD=0
```

For the postback flow to fire reliably, suppress chatter that would consume the reply token before the threshold:

```yaml
# ~/.sinria/config.yaml
display:
  interim_assistant_messages: false
  platforms:
    line:
      tool_progress: off
```

---

## Cron / notification delivery

```env
LINE_HOME_CHANNEL=Uxxxxxxxxxxxxxxxxxxxx     # default delivery target
```

Cron jobs with `deliver: line` route to `LINE_HOME_CHANNEL`. The adapter ships a standalone Push-only sender so cron jobs work even when cron runs in a separate process from the gateway.

---

## Route one LINE account to member-owned Sinria instances

LINE permits only one LINE Official Account in a group. Keep that account as the
front door and route mapped DMs or explicitly prefixed group messages to the
member's own Sinria runtime.

On the member Mac, keep the following only in `~/.sinria/.env`:

```env
SINRIA_LINE_PEER_RELAY_TOKEN=<purpose-scoped-random-token>
SINRIA_MEMBER_ID=<member-id>
SINRIA_INSTANCE_ID=<instance-id>
SINRIA_LOCAL_API_URL=http://127.0.0.1:8642
SINRIA_LOCAL_API_KEY=<member-local-api-server-key>
```

Generate the purpose token locally with
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`; never invent a
memorable token or copy the value into chat, docs, or Company OS.

From the stable primary employee-distribution checkout:

```bash
python scripts/install-sinria-line-peer-relay.py --preflight
python scripts/install-sinria-line-peer-relay.py
```

Preflight makes an authenticated loopback request to `/health/detailed`; it is
not a configuration-only check and fails closed if the member runtime is
unavailable or rejects the local API key.

The relay accepts loopback binds only. Publish it only with private Tailscale
Serve HTTPS (`*.ts.net`), never Funnel, and never expose the member's general API
server. Configure the front door with non-secret route metadata:

```yaml
gateway:
  platforms:
    line:
      enabled: true
      extra:
        peer_routes:
          kikuchi:
            display_name: "Kikuchi"
            member_id: "member_kikuchi"
            instance_id: "inst_kikuchi_local"
            endpoint: "https://<private-host>.ts.net/v1/line-peer-relay"
            token_env: "SINRIA_LINE_PEER_KIKUCHI_TOKEN"
            dm_user_ids: ["U...verified-user-id..."]
            group_ids: ["C...approved-group-id..."]
            group_prefixes: ["@Kikuchi Sinria"]
```

Use a token environment name matching `SINRIA_LINE_PEER_*_TOKEN`; store the
matching purpose token only in the front-door local secret file. The member's
`SINRIA_LOCAL_API_KEY` never leaves that member machine.

A mapped DM never falls back to the front-door owner's agent. Group routing
requires an exact prefix. Only one-way identifier hashes cross the private relay;
raw text is never written to Company OS. Relay turns enforce an empty toolset,
so external actions cannot run and `externalActionPerformed=false` is grounded
in a runtime boundary. The relay cache stores only the answer; target-local
Sinria session retention follows the member's local policy.

The configured `dm_user_ids` and `group_ids` are scoped peer authorization;
unmatched peer-only traffic never reaches the front-door local agent. If a crash
leaves delivery in indeterminate `sending`, newer turns remain blocked until the
local hash-only state is reconciled; do not delete the database or assume send.
After a human checks whether LINE received the response, run exactly one of:

```bash
python scripts/reconcile-sinria-line-peer-delivery.py \
  --conversation-ref sha256:<hash> --message-ref sha256:<hash> \
  --decision retry --confirm-human-review
python scripts/reconcile-sinria-line-peer-delivery.py \
  --conversation-ref sha256:<hash> --message-ref sha256:<hash> \
  --decision delivered --confirm-human-review
```

Use `retry` only after confirming non-delivery and `delivered` only after
confirming delivery. The command accepts hash-only references and stores no raw
LINE body.

Completion requires a fresh real LINE round trip with the expected member and
instance receipt. Installation or a health check alone is not completion.

### Human acknowledgement evidence

Keep transport and human evidence separate. A LINE API 2xx is only
`api_accepted`; it is not proof that a person displayed, read, understood, or
accepted the message. State advances to `human_replied` only when the expected
sender replies in the expected conversation with the exact expected phrase
while quoting the specific outbound message.

The local `human-confirmation.sqlite3` stores conversation, sender, message, and
reply identities only as one-way hashes. Unquoted replies, another DM/group,
another participant, and generic acknowledgements such as `了解です` cannot
confirm. For local task classification, `quotedMessageId` is rendered only as a
role relationship such as `[sender replying_to=other_participant]`; raw LINE
IDs are not added to model context.

---

## Environment variable reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | yes | — | Long-lived channel access token |
| `LINE_CHANNEL_SECRET` | yes | — | Channel secret (HMAC-SHA256 webhook verification) |
| `LINE_HOST` | no | `0.0.0.0` | Webhook bind host |
| `LINE_PORT` | no | `8646` | Webhook bind port |
| `LINE_PUBLIC_URL` | for media | — | Public HTTPS base URL; required for image/voice/video sends |
| `LINE_ALLOWED_USERS` | one of | — | Comma-separated user IDs (U-prefixed) |
| `LINE_ALLOWED_GROUPS` | one of | — | Comma-separated group IDs (C-prefixed) |
| `LINE_ALLOWED_ROOMS` | one of | — | Comma-separated room IDs (R-prefixed) |
| `LINE_ALLOW_ALL_USERS` | dev only | `false` | Skip allowlist entirely |
| `LINE_HOME_CHANNEL` | no | — | Default cron / notification delivery target |
| `LINE_SLOW_RESPONSE_THRESHOLD` | no | `45` | Seconds before the postback button fires (`0` = disabled) |
| `LINE_PENDING_TEXT` | no | "🤔 Still thinking…" | Bubble text shown alongside the postback button |
| `LINE_BUTTON_LABEL` | no | "Get answer" | Button label |
| `LINE_DELIVERED_TEXT` | no | "Already replied ✅" | Reply when an already-delivered button is tapped again |
| `LINE_INTERRUPTED_TEXT` | no | "Run was interrupted before completion." | Reply when a `/stop` orphan button is tapped |
| `LINE_PEER_ROUTES_JSON` | no | — | JSON route map for private member-owned Sinria relays |

---

## Troubleshooting

**"invalid signature" on webhook verify.** The `Channel secret` was copied wrong, or your tunnel rewrote the request body. Verify with `curl -i https://<tunnel>/line/webhook/health` first — that should return `{"status":"ok","platform":"line"}`.

**Bot receives nothing in groups.** Check `LINE_ALLOWED_GROUPS` includes the `C...` group ID. During setup, temporarily permit only the controlled onboarding group, send a test message, and inspect the local Sinria webhook diagnostics under `~/.sinria/logs/`; never copy raw identifiers into shared logs or documents.

**`send_image` fails with "LINE_PUBLIC_URL must be set".** LINE's Messaging API does not accept binary uploads — images, audio, and video must be reachable HTTPS URLs. Set `LINE_PUBLIC_URL` to the tunnel's public hostname and the adapter will serve files from `/line/media/<token>/<filename>` automatically.

**Postback button never appears.** Either the LLM responded faster than `LINE_SLOW_RESPONSE_THRESHOLD`, or another bubble (tool-progress, streaming) consumed the reply token first. See the suppression block under "Slow LLM responses".

**"already in use by another profile".** The same channel access token is bound to another running Sinria profile. Stop the other gateway or use a separate channel.

---

## Passive task intake for a two-person conversation

LINE does not allow a bot to read an existing private 1:1 thread. Invite the
Sinria Official Account as a third participant; LINE then creates a group. The
two people keep their normal personal LINE accounts, and Sinria can process new
messages posted after it joins.

Task intake is opt-in per group. Sinria treats chat text as untrusted data and
uses an explicitly configured **local Ollama model on loopback** for a strict
task/no-task decision. The normal agent/session path is bypassed, so raw LINE
text cannot fall through to a cloud model. Non-task messages are silent. Only
a clear request creates a Company OS task and receives a short receipt.

```yaml
gateway:
  platforms:
    line:
      enabled: true
      extra:
        task_intake_groups: ["C...target-group-id..."]
        task_workspace_id: "<company-os-workspace-id>"
        task_intake_local_model: "qwen3.5:9b"
        task_intake_local_url: "http://127.0.0.1:11434"
        task_participants:
          "U...taro-line-user-id...":
            member_id: "member-taro"
            instance_id: "instance-taro"
          "U...kikuchi-line-user-id...":
            member_id: "member-kikuchi"
            instance_id: "instance-kikuchi"
```

Set the Company OS endpoint and bearer credential in the local Sinria secret
store, not in shared configuration:

```env
COMPANY_OS_BASE_URL=https://company-os.example.invalid
SINRIA_COMPANY_OS_TRANSPORT_TOKEN=YOUR_LOCAL_SECRET
SINRIA_COMPANY_CONTEXT_WORKSPACE_ID=YOUR_WORKSPACE_ID
```

Safety and behavior:

* Raw LINE text is written only under `~/.sinria/private/line/task-intake/`
  for messages that became tasks; directory mode is `0700` and files are `0600`.
* Raw LINE text is classified through loopback HTTP only. Non-loopback model
  URLs are rejected, and the task group never enters Sinria's normal chat
  session or cloud-model path.
* Company OS receives only a sanitized summary and a
  `local://line/task-intake/...` evidence reference. It never receives raw LINE
  text, LINE user IDs, credentials, or patient identifiers.
* The immutable LINE webhook/message identity generates the Company OS
  idempotency key, so webhook retries do not create duplicate tasks.
* Exactly two mapped human participants are required for
  `other_participant`; ambiguous or missing identity mappings fail closed.
* Created tasks disallow external actions and external egress. Later execution
  still follows the normal Sinria approval policy.
* Typing, streaming, tool-progress, and slow-response bubbles are suppressed
  in task-intake groups by the adapter itself; only a task receipt or a
  recoverable configuration/connection failure is sent.

---

## Limitations

* **Single bubble per chunk.** Each LINE text bubble is capped at 5000 characters, and at most 5 bubbles are sent per Reply/Push call. Longer responses are truncated with an ellipsis.
* **No native message editing.** LINE has no edit-message API — streaming responses always send fresh bubbles, never edit prior ones.
* **No Markdown rendering.** Bold (`**`), italics (`*`), code fences, and headings render as literal characters. The adapter strips them before sending; URLs are preserved (`[label](url)` becomes `label (url)`).
* **Loading indicator is DM-only.** LINE rejects the chat/loading API for groups and rooms, so the typing indicator only shows in 1:1 chats.
