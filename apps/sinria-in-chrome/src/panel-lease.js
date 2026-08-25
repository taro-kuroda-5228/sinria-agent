export const PANEL_LEASE_KEY = "sinriaPanelLeaseV1";
export const PANEL_LEASE_TTL_MS = 15000;

export function claimPanelLease(current, owner, now = Date.now(), ttlMs = PANEL_LEASE_TTL_MS) {
  const valid = current && typeof current.owner === "string" && Number(current.expiresAt) > now;
  if (valid && current.owner !== owner) return { granted: false, lease: current };
  return { granted: true, lease: { owner, expiresAt: now + ttlMs } };
}

export function releasePanelLease(current, owner) {
  return current?.owner === owner ? null : current || null;
}
