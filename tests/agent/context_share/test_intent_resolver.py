
from agent.context_share.evidence import ContextEvidence, EvidenceLedger
from agent.context_share.intent_resolver import IntentResolver, derive_topic_keys


def _ev(evidence_id, summary, applies_to):
    return ContextEvidence(
        evidence_id=evidence_id,
        source_session_id="session-ctx",
        source_kind="user_correction",
        scope="personal",
        summary=summary,
        sanitized_sample=summary[:80],
        sensitivity="internal",
        applies_to=applies_to,
        valid_from="2026-06-06T00:00:00Z",
        confidence=0.95,
        human_approved=True,
    )


def test_context_share_complaint_retrieves_prior_identity_team_mode_and_self_improvement_constraints():
    ledger = EvidenceLedger([
        _ev("ev-identity", "Sinria is independent; use Sinria-native identity and paths, not Hermes residue.", ["sinria", "identity"]),
        _ev("ev-team", "Team Mode: shared Company OS cloud rows are metadata-only; raw context stays local/on-prem.", ["team_mode", "company_os", "org_context"]),
        _ev("ev-tacit", "Tacit Skill OS should infer tacit knowledge from observed workflows, not interviews.", ["tacit_skill_os", "self_improvement"]),
        _ev("ev-loop", "Self-improvement loop converts repeated corrections into memory, skills, tests, and runbooks.", ["self_improvement", "context_share"]),
    ])
    resolver = IntentResolver(ledger=ledger)

    result = resolver.resolve("Sinriaのコンテキストシェア機能が弱すぎる。自己改善できていない", platform="discord")

    assert result.risk_level == "regulated_org"
    assert set(result.retrieval_evidence_ids) >= {"ev-identity", "ev-team", "ev-tacit", "ev-loop"}
    assert "sinria-agent" in result.recommended_skills
    assert any("metadata-only" in constraint for constraint in result.applicable_constraints)
    assert any("memory, skills, tests" in constraint for constraint in result.applicable_constraints)


def test_code_touching_request_recommends_implementation_skills_and_no_egress_boundary():
    resolver = IntentResolver(ledger=EvidenceLedger())
    result = resolver.resolve("docs/plans/context-share-v2.md の通りに実装して完成まで進めて", project="sinria")

    assert "test-driven-development" in result.recommended_skills
    assert "writing-plans" in result.recommended_skills
    assert any("raw/private context stays local" in constraint for constraint in result.applicable_constraints)


def test_claude_code_context_share_request_gets_parity_constraint_and_skill():
    resolver = IntentResolver(ledger=EvidenceLedger())
    result = resolver.resolve("sinriaレポジトリ内の.claudeとClaude Codeの方針をContext Shareと一貫させて", project="sinria")

    assert "claude-code" in result.recommended_skills
    assert "sinria-claude-code-parity-default" in result.retrieval_evidence_ids
    assert any("Claude Code" in constraint and "practical-completion" in constraint for constraint in result.applicable_constraints)


def test_claude_only_request_retrieves_parity_without_sinria_default_masking():
    resolver = IntentResolver(include_durable=False)
    result = resolver.resolve("Claude Codeの作業ルールを確認して", project=None)

    assert "sinria-claude-code-parity-default" in result.retrieval_evidence_ids
    assert "claude-code" in result.recommended_skills


def test_medspot_productionization_request_retrieves_active_project_correction():
    ledger = EvidenceLedger([
        _ev(
            "ev-medspot",
            "Current MedSpot productionization context must resolve to the MedSpot repo and not Company OS or Sales Agent OS.",
            ["medspot", "productionization", "honban_plan", "healthcare_marketplace"],
        )
    ])
    resolver = IntentResolver(ledger=ledger)

    explicit = resolver.resolve("MedSpotの本番化計画を作成して", platform="discord")
    ambiguous = resolver.resolve("本番化計画を先に作成しよう", platform="discord")

    assert "ev-medspot" in explicit.retrieval_evidence_ids
    assert "ev-medspot" in ambiguous.retrieval_evidence_ids
    assert any("MedSpot productionization" in c for c in ambiguous.applicable_constraints)

    prompt = ambiguous.format_for_prompt()
    assert "Active project override" in prompt
    assert "before any repo/file/tool action" in prompt
    assert "older durable project context" in prompt


def test_project_action_adds_source_lock_gate_for_ambiguous_work():
    resolver = IntentResolver(include_durable=False)

    result = resolver.resolve("本番化計画を先に作成しよう", platform="discord")

    assert result.project_source_lock
    prompt = result.format_for_prompt()
    assert "Project Source-Lock Gate" in prompt
    assert "/Users/tarokuroda/exbrain-vault/workspaces/sinria/handoff.md" in prompt
    assert "session_search" in prompt
    assert "older durable project context" in prompt


def test_generic_document_request_requires_existing_canonical_artifact_inventory():
    resolver = IntentResolver(include_durable=False)

    result = resolver.resolve("社内DashboardにCompany OSの状況を追加して", platform="discord")
    prompt = result.format_for_prompt()

    assert "Project Source-Lock Gate" in prompt
    assert "Search for existing canonical docs/specs/plans/dashboards" in prompt
    assert "Do not create a new artifact" in prompt
    assert "deployment/runtime target" in prompt


def test_ux_complaint_about_wrong_deploy_targets_gets_generic_root_cause_gate():
    resolver = IntentResolver(include_durable=False)

    result = resolver.resolve(
        "その場限りではなく、デプロイ先を間違える原因を特定して汎用的に直して",
        platform="discord",
    )
    prompt = result.format_for_prompt()

    assert "Project Source-Lock Gate" in prompt
    assert "root-cause fix" in prompt
    assert "deployment/runtime target" in prompt
    assert "systematic-debugging" in result.recommended_skills


def test_medspot_source_lock_includes_exact_artifacts_and_boundaries():
    resolver = IntentResolver(include_durable=False)

    result = resolver.resolve("MedSpotのUIをmockupに合わせて実装して", platform="discord")

    prompt = result.format_for_prompt()
    assert "Project Source-Lock Gate" in prompt
    assert "/Users/tarokuroda/projects/medspot" in prompt
    assert "medspot-mvp-spec-v0.md" in prompt
    assert "2026-06-23-medspot-complete-mvp-claude-code-implementation-plan.md" in prompt
    assert "not Company OS, Sales Agent OS, MedEvidence, or a landing page" in prompt
    assert "approval-gated" in prompt


def test_discord_reply_quote_does_not_override_explicit_medevidence_project():
    resolver = IntentResolver(include_durable=False)
    message = (
        '[Replying to: "確認しました。Cloud Run proxy は既に終了しています。"]\n\n'
        "[Taro Kuroda] メドエビデンスレポジトリのconflictを解消してmainにマージして"
    )

    result = resolver.resolve(message, platform="discord")
    prompt = result.format_for_prompt()

    assert "Project Source-Lock Gate" in prompt
    assert "MedEvidence GCP implementation lane: /Users/tarokuroda/medevidence-gcp." in prompt
    assert "MedEvidence Vercel/LTS baseline: /Users/tarokuroda/med_evi-2" in prompt
    assert "Do not substitute MedSpot, Company OS, Sales Agent OS, or Sinria core" in prompt
    assert "Cloud Run proxy は既に終了" not in prompt


def test_medevidence_source_lock_includes_gcp_ui_freeze_constraints():
    resolver = IntentResolver(include_durable=False)

    result = resolver.resolve("メドエビデンスGCP版の検索品質を改善して", platform="discord")
    prompt = result.format_for_prompt()

    assert "MedEvidence GCP implementation lane: /Users/tarokuroda/medevidence-gcp." in prompt
    assert "MedEvidence Vercel/LTS baseline: /Users/tarokuroda/med_evi-2" in prompt
    assert "GCP/Cloud Run is the default target" in prompt
    assert "preserve the existing UI unless UI changes are explicitly requested" in prompt


def test_improvement_requests_derive_implementation_keys_for_recall():
    keys = derive_topic_keys("メドエビデンスgcp版の検索品質を改善して")

    assert "medevidence" in keys
    assert "gcp" in keys
    assert "implementation" in keys
    assert "completion" in keys
