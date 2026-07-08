from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_claude_code_entrypoint_points_back_to_sinria_source_of_truth():
    claude = _read("CLAUDE.md")

    assert "AGENTS.md" in claude
    assert ".claude/CLAUDE.md" in claude
    assert "docs/plans/2026-06-06-context-share-v2-self-improving-agent-os.md" in claude
    assert "Sinria" in claude
    assert "Confidentiality first" in claude
    assert "Practical completion" in claude
    assert "explicit human approval" in claude
    assert "scripts/run_tests.sh" in claude


def test_claude_side_context_share_rule_preserves_sinria_constraints():
    project_local = _read(".claude/CLAUDE.md")
    rule = _read(".claude/skills/sinria-context-share.md")

    assert "Sinria is the active knowledge-sharing center" in project_local
    assert "OpenClaw is legacy/read-only" in project_local
    assert "raw/inbox/sinria/" in project_local
    assert "execution substrate, not a separate policy authority" in rule
    assert "CLAUDE.md" in rule and "AGENTS.md" in rule
    assert "metadata-only" in rule
    assert "Completion claims require real workflow verification" in rule
    assert "static invariant test" in rule


def test_agents_declares_claude_code_context_share_parity():
    agents = _read("AGENTS.md")

    assert "Claude Code / Context Share Parity" in agents
    assert "CLAUDE.md" in agents
    assert ".claude/skills/sinria-context-share.md" in agents
    assert "Context Share v2 constraints apply before action regardless of entrypoint" in agents
    assert "prefer the Sinria" in agents
