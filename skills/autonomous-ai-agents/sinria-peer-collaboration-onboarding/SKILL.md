---
name: sinria-peer-collaboration-onboarding
description: "Onboard employee Sinria instances for confidential metadata-only peer collaboration and verify a live completed/accepted round trip."
version: 1.0.0
author: Sinria
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [sinria, peer-collaboration, employee-onboarding, company-os, launchd, confidential]
    related_skills: [sinria-agent]
---

# Sinria Peer Collaboration Onboarding

## Trigger

Use this skill when adding an employee Sinria to an organization's peer network, or when two employee Sinria instances must collaborate without routine human relay.

This runbook covers explicit member/instance routing. It does **not** claim that DRI-based automatic routing or human delivery receipts exist unless those capabilities are separately implemented and verified.

## Completion standard

Do not report setup complete from an installed plist or a successful preflight alone. Completion requires one fresh, non-sensitive production consultation to satisfy all of the following:

1. The target executor claims and completes the request run.
2. A `consultation_response` is authored by the expected target member and instance.
3. The requester validator creates a validation run that reaches `completed` with sanitized note `accepted`.
4. Request and response metadata both show `rawContextStored: false`, no raw body/body reference, and `externalActionPerformed: false`.
5. Source references, assumptions, confidence, dissent/alternatives, unresolved questions, and `humanDecisionRequired` are read back from typed metadata where applicable.

## Safety boundary

- Keep PHI, PII, secrets, credentials, access tokens, raw documents, and patient data out of Company OS events, run notes, commands, chat, and logs.
- Company OS is metadata-only transport and audit state. Resolve source content locally from the organization's system of record.
- Each employee uses their own local read-only OAuth access. Never copy one employee's OAuth files or transport tokens to another machine.
- Keep credentials only in that employee's profile-aware Sinria home, normally `~/.sinria/.env` and the local OAuth store. Never put them in a plist.
- A successful preflight proves bounded read access at that moment; it is not permission to write, send externally, delete, purchase, change access, or process patient data.
- External sends, contracts, billing, auth/permission changes, deletions, and clinical/patient-data actions require the appropriate human decision gate.
- Fail closed on missing/expired credentials, insufficient source access, malformed typed metadata, PHI/secret-shaped input, lease loss, or absent revision identity.
- `decision_required` stops the collaboration. Only `revision_requested` may create a revision run.
- Never infer `SINRIA_PROFILE` from transport subject. Subject, member, instance, and local profile are separate identities.

## Inputs

Collect only non-secret setup metadata:

- `REPO_ROOT`: stable primary checkout on the employee Mac, not a linked worktree.
- `BASE_URL`: approved Company OS endpoint.
- `SPACE_ID` and `CONVERSATION_ID`.
- `MEMBER_ID`: stable organization member identity.
- `INSTANCE_ID`: stable Sinria instance identity.
- `SUBJECT`: subject-scoped transport identity.
- Approved Google Workspace `RESOURCE_ID`, bounded `RANGE`, and source `VERSION`.

Before installation, confirm that the employee has independently completed local read-only Workspace authorization and subject-scoped Company OS credential setup. Do not ask them to paste credential values.

## Procedure

### 1. Update the stable checkout

On the employee Mac:

```bash
cd "$REPO_ROOT"
git pull --ff-only origin main
git rev-parse --short HEAD
```

Stop on local changes, a non-fast-forward result, or the wrong repository. Do not install from a temporary worktree.

### 2. Run safe preflight

Executor preflight must prove local source access without printing source contents:

```bash
python scripts/install-sinria-peer-service.py \
  --mode executor \
  --member-id "$MEMBER_ID" \
  --instance-id "$INSTANCE_ID" \
  --subject "$SUBJECT" \
  --base-url "$BASE_URL" \
  --preflight
```

Required executor result:

```json
{
  "exit": 0,
  "result": {
    "ok": true,
    "workspaceAccess": true,
    "rawContextStored": false
  },
  "error": null
}
```

Treat `workspace_token_missing`, token refresh/invalid errors, source access denial, unavailable source, timeout, or malformed output as blocked. Return only the safe error code; do not request credential values.

Validator preflight verifies transport identity and queue access. It does not need to resolve the executor's source.

### 3. Install both roles for bidirectional collaboration

A machine that only executes incoming requests needs `executor`; one that only validates responses to its requests needs `validator`. For normal bidirectional employee collaboration, install both as separate LaunchAgents:

```bash
python scripts/install-sinria-peer-service.py \
  --mode executor \
  --member-id "$MEMBER_ID" \
  --instance-id "$INSTANCE_ID" \
  --subject "$SUBJECT" \
  --base-url "$BASE_URL"

python scripts/install-sinria-peer-service.py \
  --mode validator \
  --member-id "$MEMBER_ID" \
  --instance-id "$INSTANCE_ID" \
  --subject "$SUBJECT" \
  --base-url "$BASE_URL"
```

The installer must replace drifted plists, pin the stable primary checkout, preserve the virtualenv entry point, pin the profile-aware `SINRIA_HOME`, load the employee's own local environment at runtime, and keep executor/validator under different labels:

- `ai.sinria.peer-worker.executor`
- `ai.sinria.peer-worker.validator`

Expected receipt fields include `installed: true`, the correct label/root, `loaded: true`, and a successful `workspacePreflight` for executor mode.

### 4. Verify the real service state

Read back both installed plists and `launchctl` state. Verify:

- root and exact command point to `REPO_ROOT`;
- `KeepAlive` is enabled;
- `SINRIA_HOME` is explicitly pinned;
- `SINRIA_PROFILE` is not inferred from subject;
- no token, password, OAuth credential, secret, or connection string appears in plist arguments/environment;
- service is loaded and running.

Kickstart each label once and verify it returns with a changed PID and remains running. A poll timeout or temporary transport failure must not terminate the worker loop.

### 5. Queue one bounded live consultation

Use `scripts/queue-peer-consultation.py`. The question must be non-PHI, non-secret, internal, and small enough for typed metadata. Source body resolution occurs only on the target machine.

```bash
python scripts/queue-peer-consultation.py \
  --space-id "$SPACE_ID" \
  --conversation-id "$CONVERSATION_ID" \
  --target-member-id "$TARGET_MEMBER_ID" \
  --target-instance-id "$TARGET_INSTANCE_ID" \
  --question "$SAFE_NON_PHI_QUESTION" \
  --resource-id "$RESOURCE_ID" \
  --range "$RANGE" \
  --version "$VERSION"
```

Record the returned `consultationId`, `eventId`, and `runId`. Do not record or print token values or source contents.

### 6. Read back the complete round trip

Using the authenticated `CompanyOsTransportClient`, read only the relevant conversation events and runs. Filter by the returned `consultationId`, request `runId`, and response event ID.

Verify:

- request run: `completed`, claimed by the expected target member/instance;
- response event: `type=consultation_response`, authored by that target;
- typed source references include the expected provider/resource/range/version and optional citation/hash without source body;
- response includes recommendation, assumptions, confidence, dissent/alternatives, unresolved questions, and human decision flag;
- requester validation run: `status=completed`, `sanitizedStatusNote=accepted`;
- raw body/context remains absent and external action remains false.

Use bounded polling with backoff. Do not create a dedicated infinite canary loop, cron job, or gateway restart for verification.

## Failure handling

- `workspace_token_missing` after installer preflight succeeded: compare installer-shell and LaunchAgent `SINRIA_HOME`; reinstall with a version that pins it explicitly.
- Generic executor failure: update to a runtime that propagates only allowlisted safe error codes. Never expose raw stderr in production notes.
- `failed_recoverable`: let lease/retry logic re-claim with a new attempt-specific idempotency key.
- Repeated 503 on `decision_required`: ensure the runtime completes with a status note only and does not append unsupported system events.
- Response exists but request appears `running`: wait briefly and read back again; response validation may win the read race before request completion is persisted.
- Installer receipt succeeds but no live response appears: setup is not complete. Inspect sanitized worker status, service root, exact command, subject-scoped token selection, and local Workspace access.

## Human-facing reporting

Report the verified run IDs and state transitions, but never credential values or source bodies. Distinguish clearly between:

- delivery to a peer Sinria;
- peer Sinria processing/acceptance; and
- confirmed delivery to the employee as a human.

A Sinria receipt alone is not proof that the employee personally read a message.
