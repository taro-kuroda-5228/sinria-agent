# Sinria release runbook

Sinria public releases use one semantic version across the Python package, CLI,
ACP registry manifest, Helm runtime metadata, Git tag, and GitHub Release.
Institution-specific workflows, runtime data, credentials, PHI/PII, reports, and
`~/.sinria/` contents are never release inputs.

## Approval boundary

Creating or publishing a release is an external side effect. Prepare and verify
the release locally first. Run `--publish` only after a human reviewer approves
the exact diff, version, and release notes.

## Prepare

```bash
git switch main
git pull --ff-only
scripts/run_tests.sh
python scripts/release.py --bump patch --output /tmp/sinria-release-notes.md
```

The preview must show a tag matching the next product version, for example
`v0.14.1`. Review the generated notes and the repository diff before proceeding.

## Publish after approval

```bash
python scripts/release.py --bump patch --publish
```

The release script updates version metadata in lockstep, builds and validates the
Python distributions, commits the version bump, creates the semantic-version Git
tag, and pushes it. The tag-triggered `.github/workflows/release.yml` then reruns
the distribution contracts, verifies that the tag matches `pyproject.toml`,
builds the wheel and source archive, checks their metadata, generates
`SHA256SUMS`, and attaches immutable artifacts plus both installers to the GitHub
Release.

## Readback verification

Do not call the release complete until all checks succeed:

1. The GitHub Actions release workflow is green.
2. The Release tag equals the package and CLI version.
3. Wheel, source archive, `install.sh`, `install.ps1`, and `SHA256SUMS` are present.
4. Freshly computed hashes match `SHA256SUMS`.
5. A clean machine can install, run `sinria --help`, run `sinria doctor`, and
   execute `sinria update --check` without reading another user's `~/.sinria/`.
6. No customer data, PHI/PII, secrets, reports, or institution-specific runtime
   files are present in the artifacts.

If any readback fails, keep the release marked incomplete and repair the shared
release path rather than publishing a one-off replacement artifact.

## Rollback

Rollback is also an external side effect and requires human approval. Do not
silently move or delete a published tag. Preserve the failed release and its
audit evidence, mark it as affected in GitHub after approval, and publish a new
patch version from the last verified tag. For an urgent workstation rollback,
install the last verified tag into a new directory with `install.sh --branch
vX.Y.Z --dir <new-path>`, verify `sinria doctor`, then switch the service or
launcher to that directory. Keep the existing `~/.sinria/` backup and compare
its checksum before and after; do not replace user configuration during code
rollback.
