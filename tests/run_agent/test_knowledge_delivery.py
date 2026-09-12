"""Knowledge delivery through the distribution conversation loop (no network)."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from tests.run_agent.test_run_agent import agent, _mock_response
from agent.context_source_policy import ContextSourcePolicy
from tests.run_agent.test_codex_app_server_integration import fake_session, _make_codex_agent


@pytest.mark.parametrize('multimodal', [False, True])
def test_codex_knowledge_sqlite_roundtrip(fake_session, monkeypatch, tmp_path, multimodal):
    from hermes_state import SessionDB
    from agent.conversation_loop import _project_user_context
    agent = _make_codex_agent()
    agent._context_source_policy = ContextSourcePolicy.from_config({'enabled': True, 'accumulation': {'enabled': True}})
    original = ([{'type': 'text', 'text': 'synthetic task'}, {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/image'}}] if multimodal else 'synthetic task')
    before = deepcopy(original)
    monkeypatch.setattr(agent, '_model_supports_vision', lambda: True)
    db = SessionDB(tmp_path / 'knowledge.db')
    agent._session_db = db
    try:
        with patch.object(agent, '_spawn_background_review', return_value=None):
            result = agent.run_conversation(original)
        assert original == before
        assert next(m['content'] for m in result['messages'] if m['role'] == 'user') == before
        replay = next(m for m in db.get_messages_as_conversation(agent.session_id) if m['role'] == 'user')
        _project_user_context(replay)
        assert replay['content'] == result['final_response'].removeprefix('echo: ')
        assert 'Persist reusable verified findings' in replay['content']
    finally:
        db.close()



@pytest.mark.parametrize('mode', ['chat_completions', 'codex_app_server'])
@pytest.mark.parametrize('multimodal', [False, True])
def test_knowledge_delivery_preserves_input_and_replay(agent, monkeypatch, mode, multimodal):
    agent.api_mode = mode
    agent._context_source_policy = ContextSourcePolicy.from_config({'enabled': True, 'accumulation': {'enabled': True}})
    original = ([{'type': 'text', 'text': 'synthetic task'}, {'type': 'image_url', 'image_url': {'url': 'https://example.invalid/image'}}] if multimodal else 'synthetic task')
    before = deepcopy(original)
    monkeypatch.setattr(agent, '_model_supports_vision', lambda: True)
    agent.client.chat.completions.create.return_value = _mock_response(content='ok')
    captured = []
    def codex(**kwargs):
        captured.append(deepcopy(kwargs))
        return {'messages': kwargs['messages']}
    monkeypatch.setattr(agent, '_run_codex_app_server_turn', codex)
    result = agent.run_conversation(original)
    user = next(m for m in result['messages'] if m['role'] == 'user')
    assert user['content'] == before
    assert original == before
    if mode == 'codex_app_server':
        wire = captured[0]['user_message']
        assert isinstance(wire, str)
        assert captured[0]['original_user_message'] == before
    else:
        wire = next(m['content'] for m in agent.client.chat.completions.create.call_args.kwargs['messages'] if m['role'] == 'user')
        if multimodal:
            assert wire[:-1] == before
    assert 'Persist reusable verified findings' in str(wire)
    from agent.conversation_loop import _project_user_context
    replay = deepcopy(user)
    _project_user_context(replay)
    assert replay['content'] == wire
