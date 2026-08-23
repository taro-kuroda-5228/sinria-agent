from pathlib import Path

import yaml


SKILL_PATH = (
    Path(__file__).parents[2]
    / "skills"
    / "creative"
    / "google-creative-apps-computer-use"
    / "SKILL.md"
)


def _load_skill() -> tuple[dict, str]:
    content = SKILL_PATH.read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    return yaml.safe_load(frontmatter), body


def _section(body: str, heading: str) -> str:
    start = body.index(heading)
    end = body.find("\n## ", start + len(heading))
    return body[start:] if end == -1 else body[start:end]


def test_google_creative_skill_meets_bundled_skill_contract() -> None:
    metadata, body = _load_skill()

    assert metadata["name"] == "google-creative-apps-computer-use"
    assert metadata["description"].endswith(".")
    assert len(metadata["description"]) <= 60
    assert metadata["author"].startswith("Taro Kuroda")
    assert "sinria" in metadata["metadata"]

    headings = [
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ]
    positions = [body.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_google_creative_skill_enforces_gui_only_verified_completion() -> None:
    _, body = _load_skill()
    prerequisites = _section(body, "## Prerequisites")
    procedure = _section(body, "## Procedure")
    pitfalls = _section(body, "## Pitfalls")
    verification = _section(body, "## Verification")

    for model_label in ("Imagen 2", "Nano Banana 2", "Gemini Omni"):
        assert model_label in body

    assert "PHI/PII" in prerequisites
    assert "proceed only with `public` or adequately `sanitized`" in prerequisites

    assert 'computer_use(action="capture"' in procedure
    assert "Do not silently continue" in procedure
    assert "passwords, 2FA codes, recovery codes, or cookies" in procedure
    assert "payment, CAPTCHA, permission, or safety dialog" in procedure
    assert "### 8. Download through the GUI" in procedure
    assert "verify the file is non-empty and decodes" in procedure
    assert "vision_analyze" in procedure
    assert "video_analyze" in procedure

    assert "Using an API because GUI capture failed" in pitfalls
    assert "Silent model substitution" in pitfalls
    assert "Generation API used: no" in verification
