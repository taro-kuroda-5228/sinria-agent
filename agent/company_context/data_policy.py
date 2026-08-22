"""Data classification and fail-closed egress decision."""
from enum import Enum
class Classification(str, Enum): Public="Public"; Internal="Internal"; Confidential="Confidential"; Restricted="Restricted"; PHI="PHI"; Secret="Secret"; LegalHold="LegalHold"; Unknown="Unknown"
DENY_REMOTE = frozenset({Classification.PHI, Classification.Secret, Classification.Unknown})
class EgressDecision:
    def __init__(self, allowed: bool, reason: str): self.allowed, self.reason = allowed, reason

def classify(labels: list[str] | None = None, *, title: str = "", value: str = "") -> Classification:
    text = " ".join((labels or []) + [title, value]).lower()
    if not text.strip(): return Classification.Unknown
    if any(x in text for x in ("phi", "patient", "diagnosis", "mrn")): return Classification.PHI
    if any(x in text for x in ("secret", "token", "password", "credential")): return Classification.Secret
    if "legal hold" in text: return Classification.LegalHold
    if "restricted" in text: return Classification.Restricted
    if "confidential" in text: return Classification.Confidential
    if "internal" in text: return Classification.Internal
    return Classification.Public

def allow_egress(classification: Classification, destination: str, *, approved_provider=False, log=False) -> EgressDecision:
    if classification in DENY_REMOTE: return EgressDecision(False, f"{classification.value} fail-closed")
    if destination in {"company_os", "sheet", "remote_model", "shared_publish"} and not approved_provider: return EgressDecision(False, "destination not allowlisted")
    if classification == Classification.LegalHold and destination in {"delete", "purge"}: return EgressDecision(False, "legal hold")
    if log and classification in DENY_REMOTE: return EgressDecision(False, "sensitive logging denied")
    return EgressDecision(True, "approved metadata egress")
