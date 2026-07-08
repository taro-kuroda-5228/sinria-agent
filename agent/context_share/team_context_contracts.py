"""Organization-scale metadata-only contracts for Context Share v2."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .safety import assert_safe_identifier, assert_sanitized_metadata, assert_sanitized_text


class TeamContextBoundaryError(ValueError):
    """Raised when shared Team Mode context would leak raw/private material."""


@dataclass(frozen=True)
class SharedContextRow:
    workspaceId: str
    memberId: str
    instanceId: str
    taskClass: str
    status: str
    risk: str
    sanitizedSummary: str
    sourceRefs: list[str]
    reviewer: str | None
    sensitivity: str
    approvalState: str
    rawContextStored: bool = False
    rawSkillBodyStored: bool = False
    credentialStored: bool = False
    externalEgress: bool = False
    extraMetadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("workspaceId", "memberId", "instanceId"):
            value = getattr(self, name)
            if not value:
                raise TeamContextBoundaryError(f"{name} is required for Team Mode context rows")
            assert_safe_identifier(value, field=name, error_cls=TeamContextBoundaryError)
        for name in ("taskClass", "status", "risk", "sensitivity", "approvalState"):
            assert_safe_identifier(getattr(self, name), field=name, error_cls=TeamContextBoundaryError)
        if self.reviewer:
            assert_sanitized_text(self.reviewer, field="reviewer", error_cls=TeamContextBoundaryError)
        if self.rawContextStored or self.rawSkillBodyStored or self.credentialStored or self.externalEgress:
            raise TeamContextBoundaryError("shared context rows must be metadata-only with no raw context, skill bodies, credentials, or external egress")
        assert_sanitized_text(self.sanitizedSummary, field="sanitizedSummary", error_cls=TeamContextBoundaryError)
        for ref in self.sourceRefs:
            assert_safe_identifier(ref, field="sourceRefs", error_cls=TeamContextBoundaryError)
        assert_sanitized_metadata(self.extraMetadata, field="extraMetadata", error_cls=TeamContextBoundaryError)

    def to_metadata(self) -> dict:
        return asdict(self)
