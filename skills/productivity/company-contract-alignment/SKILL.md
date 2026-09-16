---
name: company-contract-alignment
description: "Use when aligning a contract to approved company policy."
version: 1.0.0
author: Sinria Agent
license: MIT
platforms: [macos, linux, windows]
metadata:
  sinria:
    tags: [contracts, legal, compliance, redline, company-policy, review-gate]
    related_skills: [google-workspace, document-to-action-items, github-pr-workflow]
---

# Company Contract Alignment

Use this workflow to compare or revise a contract against formally approved company requirements while preserving legal-review and signature gates.

## Boundary

- This skill supports structured review and drafting; it does not provide legal advice, replace counsel, approve a contract, or authorize signature.
- Use only approved company sources: the current contract template, clause library, security/privacy policy, data-processing requirements, delegation matrix, and explicit legal or executive decisions.
- Search before drafting and record each source's title, owner, version/date, status, and source citation. Discussion, seniority, or an old contract is not proof of formal adoption.
- Keep raw contracts, party identities, pricing, credentials, and confidential negotiation context local. Do not paste them into public issues, commits, prompts, or shared notes.
- Do not invent company policy, counterpart facts, legal conclusions, governing law, liability limits, security commitments, or approval authority. Missing authority is a flagged gap, not a drafting opportunity.

## Workflow

### 1. Establish scope and authority

Identify the exact document/version, counterpart, transaction type, requested output, deadline, governing jurisdiction if stated, and approval roles. Confirm which approved company sources apply. If the source set is incomplete, continue only with a marked draft and list the missing decision owners.

### 2. Build a clause-by-clause gap matrix

For every relevant clause, capture:

- contract section and current wording summary;
- applicable approved company requirement and citation;
- status: aligned, conflict, missing, ambiguous, or not applicable;
- risk and operational impact;
- proposed change;
- required reviewer and approval state.

Preserve neutral wording. Separate verified fact, interpretation, proposal, and decision.

### 3. Prepare the revision

Produce both:

1. a redline or patch that preserves unrelated wording and formatting; and
2. a clean draft generated from the same accepted edits.

Never silently remove a counterparty term. Mark assumptions and unresolved variables explicitly. For source-controlled templates, use a focused branch; for office documents, create a versioned draft without overwriting the signed or received original.

### 4. Validate consistency

Check defined terms, internal cross-references, dates, party names, exhibits, order-of-precedence, survival, security/privacy obligations, data handling, incident notice, IP, confidentiality, payment, termination, liability, indemnity, dispute resolution, signature blocks, and approval routing. Re-run the gap matrix after editing and require zero unexplained changes.

### 5. Human review gate

Legal or the designated company approver must review material legal changes. External sharing, acceptance, signature, repository merge, and production use require explicit human approval for that exact document/version and recipient. Approval to draft is not approval to send or sign.

### 6. Create and verify a pull request when applicable

For source-controlled templates:

- include the source inventory and sanitized clause-by-clause gap matrix in the PR body;
- describe changed clauses, assumptions, unresolved issues, and named review gates;
- avoid confidential counterparty data in the branch and PR;
- push the exact commit and read back the pull request URL, title, head/base branches, commit SHA, changed files, and check state.

A created PR is a draft deliverable, not legal approval or execution authority.

## Completion report

Report:

- exact document/version reviewed;
- approved sources used and missing sources;
- redline and clean-draft locations;
- gap matrix summary and unresolved high-risk clauses;
- PR read-back evidence when used;
- required legal/company approver and current approval state;
- confirmation that nothing was externally sent or signed without approval.
