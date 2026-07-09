<p align="center">
  <img src="assets/sinria-logo.jpeg" alt="Sinria logo" width="220">
</p>

# Sinria

**Sinria is an autonomous AI AgentOS for medical institutions and other confidential organizations.**

It runs as a local/on-prem AI agent brain for private context, tools, approvals, audits, and self-improvement loops, while optionally connecting to cloud apps such as dashboards, CRMs, portals, and collaboration tools through outbound-only bridges.

Sinria is built for teams that need more than a chatbot: it should remember institutional context, operate workflows, surface risks, ask humans for high-stakes decisions, and improve from repeated work without leaking confidential data. It is based on a fork of Hermes Agent, with Sinria-specific runtime identity, safety policy, and regulated-workflow architecture layered on top.

---

## Why Sinria exists

Busy medical and operational teams do not need another isolated chat window. They need an agent that can safely help run the organization:

- **Local confidential brain** — private memories, credentials, tools, and clinical/operational context stay in the Sinria runtime.
- **Hybrid app architecture** — cloud apps provide shared UI, task boards, and review buttons; local Sinria performs sensitive reasoning and tool execution.
- **Human-in-the-loop safety** — medical, legal, financial, external-send, credential, and irreversible actions are drafted or queued for approval instead of silently executed.
- **Self-improving workflows** — repeated failures, missing knowledge, and manual corrections become tests, skills, runbooks, and product improvements.
- **Messaging-native operation** — interact through CLI, Discord, Telegram, Slack, email, and other gateway platforms.
- **Extensible tools and skills** — connect local files, terminals, browsers, SaaS metadata, MCP servers, scheduled jobs, and project-specific procedures.

Sinria’s initial focus is Medical Horizon and healthcare workflows, but the same pattern applies to law firms, finance, research organizations, government teams, and any group that handles confidential institutional knowledge.

---

## Current status

Sinria is actively being shaped into a standalone, installable agent product. The repository currently includes:

- Sinria-branded CLI and runtime entrypoints (`sinria`, `sinria_cli`, `setup-sinria.sh`).
- Dedicated Sinria home/profile behavior for `~/.sinria`.
- Messaging gateway support for Discord and other platforms.
- Cron/autonomous job support for long-running implementation and operations loops.
- Safe external-egress policy components for confidential data boundaries.
- Hybrid Agent Bridge components for cloud UI + on-prem agent operation.
- Reference app work under `apps/`, including Sinria Sales Agent OS / CRM Workspace and a Sierra-like service workflow.
- Tests covering branding, runtime isolation, egress guards, hybrid bridge behavior, integrations, and app-level safety gates.

Some internals still retain Hermes-derived module names for compatibility while the Sinria rename/standalone migration is completed.

---

## Quick start for Kikuchi / collaborators

> Recommended first path on macOS or Linux: clone the repo, run setup, then start Sinria locally.

```bash
git clone https://github.com/taro-kuroda-5228/sinria-agent.git sinria
cd sinria
./setup-sinria.sh
./sinria
```

If the `sinria` command is installed into your shell path, you can also run:

```bash
sinria
```

Useful first commands:

```bash
sinria setup        # configure providers, tools, and gateway options
sinria model        # choose or switch model/provider
sinria tools        # enable/disable toolsets
sinria doctor       # diagnose local setup problems
sinria gateway      # run messaging gateway integrations
sinria update       # update Sinria from GitHub
```

### Runtime locations

Sinria keeps user/runtime state outside the repository:

```text
~/.sinria/                 # Sinria runtime home: config, memory, skills, cron output, logs
~/sinria or ./sinria repo  # source checkout
```

Do not commit `.env`, credentials, patient data, private vault content, or local runtime exports.

---

## Installation notes

### macOS / Linux / WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.sh | bash
```

Then reload your shell and run:

```bash
sinria
```

### Windows

Native Windows support is still early. Prefer WSL2 when possible:

```powershell
wsl --install
```

Then install Sinria inside WSL using the Linux/macOS command above.

To install natively on Windows instead, run the PowerShell installer
(`scripts/install.ps1`) from an elevated PowerShell prompt:

```powershell
irm https://raw.githubusercontent.com/taro-kuroda-5228/sinria-agent/main/scripts/install.ps1 | iex
```

---

## Core concepts

### 1. Local agent brain

Sinria is designed so the most sensitive work happens locally or on-prem:

- private institutional context,
- credentials and API keys,
- audit logs,
- local tools and terminals,
- patient/confidential data boundaries,
- skills and memory,
- approval decisions and recoverable failure reports.

### 2. Cloud UI as shared surface

When collaboration needs a web app, Sinria uses a hybrid pattern:

```text
Cloud app
  - login / shared UI
  - event board
  - sanitized task metadata
  - review buttons
  - minimal state

Local/on-prem Sinria
  - private context
  - AI reasoning loop
  - tool execution
  - approval policy
  - audit and memory
  - self-improvement
```

The local agent initiates outbound polling or queue access. Inbound access to hospital/private networks should not be required by default.

### 3. Approval-first automation

Sinria distinguishes between:

| Class | Default behavior |
| --- | --- |
| Low-risk informational work | Answer or summarize with citations when available |
| Medium-risk workflow updates | Draft, preview, or queue for review |
| High-risk clinical/legal/financial actions | Human approval required |
| Prohibited or unsafe requests | Refuse safely with cause, risk, and next choices |

The invariant for real-world side effects is:

```json
{
  "humanApprovalRequired": true,
  "externalActionPerformed": false,
  "recoverable": true
}
```

---

## Repository map

Important paths for contributors:

```text
sinria                       # local launcher script
setup-sinria.sh              # local setup helper
sinria_cli/                  # Sinria CLI compatibility/entry package
hermes_cli/                  # inherited CLI implementation, being Sinria-branded over time
agent/                       # agent loop, prompts, model/provider adapters
cron/                        # scheduled autonomous jobs
gateway/                     # messaging gateway runtime
tools/                       # tool implementations exposed to the agent
 skills/                     # bundled skills and workflows
 apps/chatops-crm/           # Sinria Sales Agent OS / CRM Workspace reference app
 apps/sinria-sierra-service/ # Sierra-like service/reference workflow
 docs/                       # architecture, plans, integration docs
 deploy/                     # local/on-prem deployment starters
 tests/                      # regression and safety tests
```

---

## Reference implementations

### Sinria Sales Agent OS / CRM Workspace

`apps/chatops-crm/` is the Sinria Sales Agent OS / CRM Workspace reference app for showing sales task metadata, review queues, operational notes, and audit artifacts. It is intended to demonstrate how a shared UI can expose Sinria-operated workflows without moving private context into the cloud.

Typical checks:

```bash
cd apps/chatops-crm
npm install
npm run typecheck
npm run build
```

### Sierra-like service

`apps/sinria-sierra-service/` is a service-operations reference workflow. It classifies requests, drafts safe responses/actions, records audit events, and routes risky operations to human approval.

Typical checks:

```bash
cd apps/sinria-sierra-service
npm install
npm test
node scripts/autonomous-improve.mjs
```

---

## Development workflow

From the repo root:

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

Focused test examples:

```bash
scripts/run_tests.sh tests/test_sinria_constants.py -v
scripts/run_tests.sh tests/test_sinria_egress_classifier.py -v
scripts/run_tests.sh tests/test_sinria_hybrid_bridge.py -v
```

Before pushing, run the smallest relevant test set plus any app-level checks touched by your change.

---

## Safety and data handling

Sinria is intended for regulated/confidential environments, so the default posture is conservative:

- Keep PHI/PII/secrets in `~/.sinria` or approved local systems, not in Git.
- Use sanitized task metadata for cloud-visible rows.
- Never send confidential content to external APIs unless the deployment policy explicitly allows it.
- External messages, irreversible updates, clinical advice, legal/financial decisions, and credential operations require human approval.
- Safe-blocks should explain the cause, risk category, stop point, and next available choices.

---

## Updating this GitHub repository

For maintainers:

```bash
git status
git add README.md assets/sinria-logo.jpeg <changed files>
git commit -m "docs: refresh Sinria README and branding"
git push
```

If the autonomous implementation loop is running, pause it briefly before a large Git operation and resume it afterwards to avoid file races.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgement

Sinria builds on a Hermes Agent fork, while evolving toward a Medical Horizon-focused AgentOS for healthcare and other confidential organizations.
