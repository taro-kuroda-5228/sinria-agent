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


def test_executor_routes_team_project_request_to_a_local_capability_handler():
    path = ROOT / 'scripts/peer-consultation-executor.py'
    spec = importlib.util.spec_from_file_location('team_project_exec', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    request_meta = {
        'schemaVersion': 'team-project.v1',
        'type': 'task_request',
        'dispatchId': 'dispatch-1',
        'projectId': 'project-1',
        'taskId': 'research',
        'capability': 'research',
        'summary': 'Collect approved internal facts',
        'operation': 'read',
        'scope': 'company_knowledge',
        'reversible': False,
        'inputRefs': ['company-knowledge://briefs/source-1'],
        'acceptanceCriteria': ['facts-grounded'],
        'attempt': 1,
        'approvalRef': None,
        'rawContextStored': False,
        'externalActionPerformed': False,
    }

    result = mod.execute(
        {'event': {'eventId': 'event-1', 'consultationMetadata': request_meta}},
        team_executor=lambda meta: {
            'summary': f"{meta['taskId']} completed",
            'evidence': ['company-knowledge://projects/project-1/research'],
            'criteriaEvidence': {
                'facts-grounded': 'company-knowledge://projects/project-1/research'
            },
            'verdict': 'accepted',
            'externalActionPerformed': False,
        },
    )

    response = result['consultationMetadata']
    assert response['type'] == 'task_response'
    assert response['dispatchId'] == 'dispatch-1'
    assert response['criteriaEvidence'] == {
        'facts-grounded': 'company-knowledge://projects/project-1/research'
    }
    assert result['rawContextStored'] is False


def test_executor_completes_plain_user_message_with_safe_decision_required_receipt():
    path = ROOT / 'scripts/peer-consultation-executor.py'
    spec = importlib.util.spec_from_file_location('consult_exec_plain', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = mod.execute({
        'event': {
            'eventId': 'evt_plain_1',
            'kind': 'user_message',
            'sanitizedPreview': 'Check whether a contact was already approached.',
            'bodyRef': None,
        }
    })

    assert result == {
        'summary': (
            'Peer request received; automatic execution was not performed because '
            'consultation.v1 metadata is absent. Resend as a structured consultation '
            'or request human review.'
        ),
        'refs': ['run://event/evt_plain_1'],
        'rawContextStored': False,
        'externalActionPerformed': False,
    }


def test_executor_rejects_plain_user_message_with_body_reference():
    path = ROOT / 'scripts/peer-consultation-executor.py'
    spec = importlib.util.spec_from_file_location('consult_exec_plain_body', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with pytest.raises(ValueError, match='unsupported peer event'):
        mod.execute({
            'event': {
                'eventId': 'evt_plain_2',
                'kind': 'user_message',
                'sanitizedPreview': 'Process this body.',
                'bodyRef': {'mode': 'local_only', 'ref': 'local://private'},
            }
        })

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
