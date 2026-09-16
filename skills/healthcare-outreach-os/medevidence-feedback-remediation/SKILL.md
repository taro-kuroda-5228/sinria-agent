---
name: medevidence-feedback-remediation
description: "Use when fixing verified MedEvidence product feedback."
version: 1.0.0
author: Sinria Agent
license: MIT
platforms: [macos, linux, windows]
metadata:
  sinria:
    tags: [medevidence, feedback, regression, healthcare, pull-request, verification]
    related_skills: [medevidence, systematic-debugging, test-driven-development, github-pr-workflow]
---

# MedEvidence Feedback Remediation

Use this workflow when a teammate reports a MedEvidence defect or requests a product correction and expects a verified pull request rather than an untested patch.

## Safety and scope

- Treat screenshots, queries, patient data, hospital identifiers, credentials, and tenant context as confidential. Keep raw material local and use sanitized fixtures in tests, commits, pull requests, and shared notes.
- Lock the product and source-of-truth repository before editing. A similarly named checkout, preview, or deployment is not interchangeable.
- Read repository instructions and inspect current branch, dirty state, linked worktrees, open pull requests, and recent equivalent changes before creating a branch.
- If the same defect exists in multiple maintained MedEvidence editions, patch each applicable source-of-truth repository or explicitly document why one is out of scope.
- A pull-request request authorizes branch, commit, push, and PR creation for that scope. It does not authorize merge, production deploy, secret changes, patient-data access, or Gateway restart. Do not merge or deploy without separate approval.

## Workflow

### 1. Convert feedback into an acceptance case

Record a sanitized Goal → Actual → Gap:

- **Goal:** the expected clinical or product behavior.
- **Actual:** the observed behavior, including the exact surface and evidence.
- **Gap:** the smallest reproducible mismatch and the user impact.

Preserve the reporter and source citation. Do not convert an opinion into an organizational requirement without approved adoption evidence.

### 2. Reproduce the real path

- Reproduce through the production-equivalent path, including authentication, request routing, citation validation, streaming, and fallback behavior where relevant.
- Prefer a deterministic sanitized fixture that fails before the fix.
- Distinguish root cause from nearby symptoms. Do not weaken citation, provenance, tenant, or fabrication guards merely to make the example pass.
- Check current `main` and open PRs for patch equivalence before replaying historical commits.

### 3. Implement the class-level fix

- Add or update a regression test first.
- Make the narrowest implementation change that addresses the root-cause class.
- Preserve request-scoped evidence identity, source citations, audit context, model/provider selection, and tenant boundaries.
- Never log raw confidential prompts, patient data, credentials, or full clinical documents.

### 4. Verify locally

Run the repository's targeted test, lint/type checks, and build commands. Then exercise the exact repaired workflow with sanitized data. A green unit test alone is not completion.

For user-visible output, inspect a full-page screenshot and verify answer quality, element ordering, duplication, abnormal vertical length or whitespace, and raw payload leakage. Machine metrics alone are insufficient.

### 5. Create and verify the pull request

- Use a focused branch and commit message.
- The PR body must include the sanitized Goal/Actual/Gap, root cause, changed behavior, tests executed, known pre-existing failures, confidentiality statement, and deployment/merge boundary.
- Push the intended commit and create the PR against the verified base branch.
- Query the hosting service to read back the pull request number, URL, title, head/base branches, commit SHA, changed files, and check state. A successful create command alone is not completion.

## Completion report

Report:

- source-of-truth repository and branch;
- PR URL and read-back evidence;
- exact tests and real workflow exercised;
- whether any patient or confidential data was touched;
- unresolved risks, check failures, and the approval needed for merge or deploy.
