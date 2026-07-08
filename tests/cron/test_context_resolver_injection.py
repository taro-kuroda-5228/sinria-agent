
from cron.scheduler import _build_job_prompt


def test_cron_prompt_includes_context_resolver_guidance_without_skills():
    prompt = _build_job_prompt({
        "id": "abc123abc123",
        "name": "Context Share implementation",
        "prompt": "Sinria context share self-improvement implementationを進めて",
        "skills": [],
    })

    assert "Context Share Resolver" in prompt
    assert "prior corrections" in prompt
    assert "self-improvement" in prompt
    assert "raw/private context stays local" in prompt
