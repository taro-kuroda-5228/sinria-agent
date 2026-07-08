# Sinria Local Helm Chart

Starter Helm packaging for the on-prem Sinria Hybrid Agent Bridge runtime.

Install dry-run bridge into k3s:

```bash
helm upgrade --install sinria-local deploy/helm/sinria-local \
  --namespace sinria-local --create-namespace
```

Before production:

1. Create `sinria-bridge-secrets` via a real secret manager.
2. Pin `image.tag` to a release or digest.
3. Set `bridge.dryRun=false` only after selecting an approved cloud adapter.
4. Keep network policy default-deny and add connector-specific egress only after approval.
