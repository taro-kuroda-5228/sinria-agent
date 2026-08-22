# Company Knowledge runtime

Sinria can synchronize reviewed, metadata-only Company Knowledge into a member-scoped encrypted local index and inject matching citations into individual model turns.

## Required identity

Each installation must use its own identity. Do not share profiles or indexes between employees.

```bash
SINRIA_COMPANY_CONTEXT_ENABLED=true
SINRIA_COMPANY_CONTEXT_PROFILE_ID=profile-kikuchi
SINRIA_COMPANY_CONTEXT_WORKSPACE_ID=medical-horizon
SINRIA_COMPANY_CONTEXT_OWNER_ID=member_kikuchi
SINRIA_COMPANY_CONTEXT_MANIFEST_URL=https://medical-horizon-company-os.vercel.app/api/knowledge-assets/manifest
```

Recommended bounded policy:

```bash
SINRIA_COMPANY_CONTEXT_CLASSIFICATION_ALLOWLIST=Public,Internal
SINRIA_COMPANY_CONTEXT_MAX_CITATIONS=3
SINRIA_COMPANY_CONTEXT_MAX_CITATION_CHARS=1200
SINRIA_COMPANY_CONTEXT_MAX_CHARS=4000
```

Store the transport bearer token only in the local Sinria secret store as `SINRIA_COMPANY_OS_TRANSPORT_TOKEN`. Set `SINRIA_COMPANY_OS_TRANSPORT_SUBJECT` to the exact subject provisioned by Company OS. Never send or log the bearer token.

## Safety boundary

- Only reviewed, non-expired entries are synchronized.
- Candidate, rejected, revoked, and expired entries are excluded.
- Local indexes are profile-scoped and encrypted. The profile directory is repaired to `0700`; SQLite files and sidecars are repaired to `0600`.
- Context is injected as an API-only per-turn message. It is not appended to the transcript or cached system prompt.
- Missing identity, keychain, manifest authentication, or runtime wiring fails closed.
- Company Knowledge does not authorize external sends, production writes, clinical actions, or self-approval.

## Verification

After installation or upgrade:

1. Confirm the resolved profile/workspace/member/instance identity.
2. Synchronize the reviewed manifest and record only counts/statuses.
3. Verify profile directory `0700` and `index.db` `0600`.
4. Confirm a reviewed synthetic entry appears with a `company-os:<assetId>` citation in a normal turn.
5. Revoke it, synchronize again, and confirm manifest removal, local retrieval `0`, and citation disappearance.
6. Keep tokens, raw source text, PHI/PII, and patient records out of receipts and logs.
