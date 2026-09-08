from agent.context_source_policy import ContextSourcePolicy


def test_active_accumulation_applies_without_keyword_match():
    policy = ContextSourcePolicy.from_config({
        'enabled': True, 'accumulation': {'enabled': True},
        'personal': {'kind': 'obsidian_vault', 'location': '/private/alice'},
        'company': {'kind': 'google_workspace_sheet', 'spreadsheet_id': 'private-id'},
    })
    text = policy.guidance_for('The experiment is verified and complete.')
    assert 'Persist reusable verified findings' in text
    assert 'formal organizational adoption' in text
    assert 'read back' in text
    assert '/private/alice' not in text
    assert 'private-id' not in text


def test_accumulation_requires_explicit_boolean_opt_in():
    for enabled in [False, 'false', 'true', 1, None]:
        p = ContextSourcePolicy.from_config({'enabled': True, 'accumulation': {'enabled': enabled}})
        assert p.guidance_for('ordinary task') == ''


def test_disabled_policy_never_injects_accumulation():
    p = ContextSourcePolicy.from_config({'enabled': False, 'accumulation': {'enabled': True}})
    assert p.guidance_for('ordinary task') == ''
