"""Stable advisory-correction topic classification.

Topic keys only improve retrieval. They never create execution permissions,
blocks, approvals, or completion requirements.
"""

from __future__ import annotations


def derive_topic_keys(text: str, *, project: str | None = None) -> list[str]:
    value = (text or "").lower()
    keys: list[str] = []
    if "sinria" in value or project == "sinria":
        keys.extend(("sinria", "agent_os"))
    if "自己改善" in value or "self-improvement" in value or "self_improvement" in value:
        keys.append("self_improvement")
    if "team" in value or "組織" in value or "company os" in value or "agent os" in value:
        keys.extend(("team_mode", "company_os"))
    if "medevidence" in value or "メドエビデンス" in value:
        keys.extend(("medevidence", "medical_evidence", "gcp", "cloud_run"))
    if any(token in value for token in ("本番", "production", "deploy", "launch", "ローンチ")):
        keys.extend(("production", "deployment"))
    if "暗黙知" in value or "tacit" in value:
        keys.append("tacit_skill_os")
    if any(token in value for token in ("実装", "implement", "完成", "改善", "fix")):
        keys.extend(("implementation", "completion"))
    if "claude" in value or ".claude" in value:
        keys.extend(("claude_code", "local_execution"))
    return list(dict.fromkeys(keys or ("general",)))
