import pytest

from agent.context_share.team_context_contracts import SharedContextRow, TeamContextBoundaryError


def test_shared_context_row_requires_team_identity_and_metadata_only_flags():
    row = SharedContextRow(
        workspaceId="workspace-medical-horizon",
        memberId="taro",
        instanceId="sinria-local-1",
        taskClass="context_improvement",
        status="waiting_review",
        risk="internal",
        sanitizedSummary="Context Share v2 candidate: inject prior correction constraints before action.",
        sourceRefs=["ev-loop"],
        reviewer="taro",
        sensitivity="internal",
        approvalState="proposed",
        rawContextStored=False,
        rawSkillBodyStored=False,
        credentialStored=False,
        externalEgress=False,
        extraMetadata={"evidence_count": 1},
    )

    assert row.to_metadata()["workspaceId"] == "workspace-medical-horizon"
    assert row.to_metadata()["rawContextStored"] is False


def test_shared_context_row_rejects_raw_evidence_or_credentials():
    with pytest.raises(TeamContextBoundaryError):
        SharedContextRow(
            workspaceId="workspace",
            memberId="member",
            instanceId="instance",
            taskClass="context_improvement",
            status="waiting_review",
            risk="confidential",
            sanitizedSummary="Contains raw patient MRN-123456 and token placeholdertoken",
            sourceRefs=["ev-bad"],
            reviewer="taro",
            sensitivity="clinical",
            approvalState="proposed",
            rawContextStored=True,
            rawSkillBodyStored=False,
            credentialStored=False,
            externalEgress=False,
        )


def test_shared_context_row_recursively_rejects_raw_extra_metadata():
    with pytest.raises(TeamContextBoundaryError):
        SharedContextRow(
            workspaceId="workspace",
            memberId="member",
            instanceId="instance",
            taskClass="context_improvement",
            status="waiting_review",
            risk="internal",
            sanitizedSummary="Safe summary",
            sourceRefs=["ev-safe"],
            reviewer="taro",
            sensitivity="internal",
            approvalState="proposed",
            extraMetadata={"debug": {"raw": "patient MRN-123456"}},
        )
