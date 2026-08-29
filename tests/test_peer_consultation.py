import importlib.util
from pathlib import Path
import pytest
from sinria_consultation import validate_consultation
from sinria_peer_collaboration import ConversationEvent, safe_metadata

ROOT = Path(__file__).resolve().parents[1]

def request():
    return {'schemaVersion':'consultation.v1','type':'consultation_request','consultationId':'consult_1',
      'questionSummary':'Recommend the next internal peer operating step from the shared source.',
      'sourceRefs':[{'provider':'google_workspace','resourceId':'1D6SACTdRdCtAaXcQcLYqohJg8ncKwAKFekr9DfDSHbc','range':'📱 今日の進捗!A1:E8'}],
      'humanDecisionRequired':False,'allowedOperations':['read','draft'],'sensitivity':'internal','rawContextStored':False,'externalActionPerformed':False}

def test_consultation_contract_rejects_raw_fields_and_send():
    validated = validate_consultation(request())
    assert validated is not None and validated['consultationId'] == 'consult_1'
    with pytest.raises(ValueError): validate_consultation({**request(), 'rawPrompt':'x'})
    with pytest.raises(ValueError): validate_consultation({**request(), 'allowedOperations':['send']})

def test_event_and_safe_metadata_preserve_only_typed_consultation():
    event = ConversationEvent.from_payload({'eventId':'e','workspaceId':'w','spaceId':'s','conversationId':'c','kind':'user_message','authorKind':'sinria','authorMemberId':'t','authorInstanceId':'ti','sanitizedPreview':'queued','consultationMetadata':request(),'bodyRef':None})
    assert event.callback_payload()['consultationMetadata']['type'] == 'consultation_request'
    response = {**request(), 'type':'consultation_response','recommendation':'Continue peer consultation with approval gates.', 'confidence':.9,
                'assumptions':[],'dissent':[],'unresolvedQuestions':[]}
    response.pop('questionSummary')
    assert safe_metadata({'summary':'ok','consultationMetadata':response})['consultationMetadata']['confidence'] == .9

def test_executor_resolves_workspace_locally_and_returns_no_body(monkeypatch):
    path = ROOT/'scripts/peer-consultation-executor.py'
    spec = importlib.util.spec_from_file_location('consult_exec', path)
    assert spec is not None and spec.loader is not None
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, '_sheet_values', lambda *_: [['菊地','Sinria peer executor running','承認 gate']])
    result = mod.execute({'event':{'eventId':'e','sanitizedPreview':'queued','consultationMetadata':request(),'bodyRef':None}})
    assert result['consultationMetadata']['type'] == 'consultation_response'
    assert result['consultationMetadata']['confidence'] == .9
    assert 'body' not in result and result['rawContextStored'] is False

def test_queue_prefers_subject_scoped_transport_token(monkeypatch):
    path = ROOT/'scripts/queue-peer-consultation.py'
    spec = importlib.util.spec_from_file_location('consult_queue', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    monkeypatch.setenv('SINRIA_COMPANY_OS_TRANSPORT_TOKEN', 'present')
    assert mod.transport_token_env() == 'SINRIA_COMPANY_OS_TRANSPORT_TOKEN'


def test_workspace_preflight_returns_only_safe_machine_codes(monkeypatch):
    path = ROOT / 'scripts/peer-consultation-executor.py'
    spec = importlib.util.spec_from_file_location('consult_executor_preflight', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, '_sheet_values', lambda *_: [["private cell text"]])
    assert mod.workspace_preflight() == {
        'ok': True,
        'workspaceAccess': True,
        'resourceId': mod.DASHBOARD_ID,
        'range': mod.PREFLIGHT_RANGE,
        'rawContextStored': False,
    }

    def reject(*_):
        raise mod.WorkspaceResolverError('workspace_token_missing')

    monkeypatch.setattr(mod, '_sheet_values', reject)
    assert mod.workspace_preflight() == {
        'ok': False,
        'workspaceAccess': False,
        'errorCode': 'workspace_token_missing',
        'rawContextStored': False,
    }
