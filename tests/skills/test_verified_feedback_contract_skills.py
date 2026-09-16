from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
MEDEVIDENCE_SKILL = (
    ROOT
    / "skills"
    / "healthcare-outreach-os"
    / "medevidence-feedback-remediation"
    / "SKILL.md"
)
CONTRACT_SKILL = (
    ROOT
    / "skills"
    / "productivity"
    / "company-contract-alignment"
    / "SKILL.md"
)


def _load(path: Path) -> tuple[dict, str]:
    content = path.read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    return yaml.safe_load(frontmatter), body


def test_medevidence_feedback_skill_is_verification_first() -> None:
    metadata, body = _load(MEDEVIDENCE_SKILL)

    assert metadata["name"] == "medevidence-feedback-remediation"
    assert metadata["description"].startswith("Use when ")
    assert "metadata" in metadata and "sinria" in metadata["metadata"]
    for required in (
        "production-equivalent path",
        "patient data",
        "source-of-truth repository",
        "full-page screenshot",
        "Do not merge or deploy",
        "Goal → Actual → Gap",
        "read back the pull request",
    ):
        assert required in body


def test_contract_alignment_skill_is_source_bounded_and_review_gated() -> None:
    metadata, body = _load(CONTRACT_SKILL)

    assert metadata["name"] == "company-contract-alignment"
    assert metadata["description"].startswith("Use when ")
    assert "metadata" in metadata and "sinria" in metadata["metadata"]
    for required in (
        "approved company sources",
        "Do not invent",
        "clause-by-clause gap matrix",
        "legal advice",
        "explicit human approval",
        "redline",
        "read back the pull request",
    ):
        assert required in body
