# Sinria Local k3s Deployment Runbook

This directory is the Phase 5 starter package for running on-prem Sinria as a
small Kubernetes/k3s AgentOS runtime that connects to cloud apps without opening
inbound ports.

## Components

- `sinria-bridge`: outbound-only cloud event bridge. It polls/subscribes to the
  cloud app task layer and writes sanitized status/results/review requests back.
- `sinria-worker-short`: placeholder for short agent runs.
- `sinria-tool-executor`: isolated executor for approved side-effect tools.
- `sinria-bridge-secrets`: bridge token secret placeholder. Replace via
  `kubectl create secret` or External Secrets; never commit production values.
- `NetworkPolicy`: default deny, bridge HTTPS-only egress starter, tool executor
  no-egress default until an institution-approved connector policy exists.

## Install on a local k3s host

```bash
kubectl apply -f deploy/k8s/sinria-local/base/namespace-config.yaml
kubectl apply -f deploy/k8s/sinria-local/base/deployments.yaml
kubectl apply -f deploy/k8s/sinria-local/base/network-policies.yaml
```

## Production hardening before real use

1. Replace `SINRIA_BRIDGE_TOKEN: change-me` with a secret manager / sealed secret.
2. Replace the image tag with a pinned digest or release tag.
3. Replace `args: ["--dry-run"]` with an approved transport adapter mode.
4. Restrict bridge egress to the approved Vercel/Supabase/API endpoint or CNI FQDN policy.
5. Keep tool executor egress denied by default; add connector-specific policies only after approval.
6. Mount Sinria local state, Obsidian/Exbrain, logs, and backup volumes explicitly.
7. Configure backups for local Postgres/audit logs before handling regulated data.

## Safety boundary

Cloud apps are shared UI/event surfaces. On-prem Sinria remains the private
agent runtime, context owner, credential holder, tool executor, audit producer,
and self-improvement loop. This deployment must not expose inbound ports to the
public Internet by default.
