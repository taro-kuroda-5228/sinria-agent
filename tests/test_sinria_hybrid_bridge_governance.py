from sinria_hybrid_bridge_governance import (
    ImprovementKind,
    ReviewDecision,
    ReviewRequest,
    apply_review_decision,
    propose_improvement_candidate,
)


def test_approval_allows_execution_only_when_required_role_matches():
    request = ReviewRequest(
        review_id="rev_1",
        task_id="task_1",
        required_role="admin",
        action_summary="Send CRM email",
    )

    denied = apply_review_decision(request, ReviewDecision(approved=True, decided_by="kikuchi", role="user"))
    approved = apply_review_decision(request, ReviewDecision(approved=True, decided_by="taro", role="admin"))

    assert denied.execution_allowed is False
    assert "requires admin" in denied.reason
    assert approved.execution_allowed is True


def test_rejection_never_allows_execution():
    request = ReviewRequest(
        review_id="rev_2",
        task_id="task_2",
        required_role="admin",
        action_summary="Write CRM field",
    )

    result = apply_review_decision(request, ReviewDecision(approved=False, decided_by="taro", role="admin", comment="Needs rewrite"))

    assert result.execution_allowed is False
    assert result.next_status == "rejected"


def test_human_correction_becomes_skill_improvement_candidate():
    candidate = propose_improvement_candidate(
        tenant_id="medical_horizon",
        source_run_id="run_1",
        signal="human_correction",
        summary="Kikuchi repeatedly changed hospital outreach tone to be shorter and more direct.",
    )

    assert candidate.kind == ImprovementKind.SKILL_UPDATE
    assert candidate.requires_human_approval is True
    assert "shorter" in candidate.summary


def test_repeated_safe_block_becomes_policy_improvement_candidate():
    candidate = propose_improvement_candidate(
        tenant_id="medical_horizon",
        source_run_id="run_2",
        signal="repeated_safe_block",
        summary="CRM draft blocked despite sanitized public hospital profile.",
    )

    assert candidate.kind == ImprovementKind.POLICY_CHANGE
    assert candidate.requires_human_approval is True


def test_improvement_candidate_redacts_identifiers_and_never_records_external_action():
    candidate = propose_improvement_candidate(
        tenant_id="medical_horizon",
        source_run_id="run_sensitive",
        signal="human_correction",
        summary=(
            "山田太郎 MRN-123456 taro@example.com 090-1234-5678 "
            "100-0001 4111-1111-1111-1111 の回答テンプレートを修正"
        ),
    )

    assert candidate.external_action_performed is False
    assert "[REDACTED" in candidate.summary
    assert "山田太郎" not in candidate.summary
    assert "MRN-123456" not in candidate.summary
    assert "taro@example.com" not in candidate.summary
    assert "090-1234-5678" not in candidate.summary
    assert "100-0001" not in candidate.summary
    assert "4111-1111-1111-1111" not in candidate.summary
