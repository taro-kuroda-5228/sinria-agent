"""Synthetic-only knowledge regressions for the distribution runtime."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from agent.context_source_policy import ContextSourcePolicy, guidance_for_agent


@pytest.mark.parametrize('enabled', [False, 'false', 'true', 0, 1, 1.0, None, [], {}])
def test_master_requires_literal_true(enabled):
    policy = ContextSourcePolicy.from_config({'enabled': enabled, 'accumulation': {'enabled': True}, 'company': {'hints': ['team']}})
    assert policy.guidance_for('team task') == ''


@pytest.mark.parametrize('config', [None, {}, {'accumulation': {'enabled': True}}])
def test_missing_master_is_disabled(config):
    assert ContextSourcePolicy.from_config(config).guidance_for('task') == ''


def test_multimodal_policy_extracts_only_task_text():
    agent = SimpleNamespace(_context_source_policy=ContextSourcePolicy.from_config({'enabled': True, 'company': {'hints': ['team']}}))
    assert 'Company source selected' in guidance_for_agent(agent, [{'type': 'text', 'text': 'team task'}])
    assert guidance_for_agent(agent, [{'type': 'image_url', 'text': 'team'}]) == ''
    assert guidance_for_agent(agent, [{'type': 'file', 'text': 'team'}]) == ''


def test_multimodal_delivery_and_fixed_sidecar_replay():
    from agent.conversation_loop import _compose_user_context, _project_user_context
    original = [{'type': 'text', 'text': 'synthetic task'}, {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/image'}}]
    before = deepcopy(original)
    wire = _compose_user_context(original, ['fixed policy'])
    assert wire == before + [{'type': 'text', 'text': 'fixed policy'}]
    message = {'role': 'user', 'content': 'synthetic task\n[screenshot]', '_injected_user_context': [{'type': 'sinria_api_content_v1', 'content': wire}]}
    _project_user_context(message)
    assert message['content'] == wire
    assert '_injected_user_context' not in message
    message['content'][1]['image_url']['url'] = 'changed'
    assert original == before
