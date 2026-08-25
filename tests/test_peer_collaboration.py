from sinria_peer_collaboration import ConversationEvent, ConversationRun, PeerCollaborationRunner, safe_metadata


def run_payload(rid, target, instance, event, status='queued', revision=0):
    return {'runId': rid, 'workspaceId': 'w', 'spaceId': 's', 'conversationId': 'c', 'triggeredByEventId': event,
            'sourceMemberId': 'source', 'targetMemberId': target, 'targetInstanceId': instance, 'status': status, 'revision': revision, 'humanRelayCount': 0}


def event(eid, kind='user_message', author='taro', instance='taro-1', preview='hello'):
    return {'eventId': eid, 'workspaceId': 'w', 'spaceId': 's', 'conversationId': 'c', 'kind': kind,
            'authorKind': 'member', 'authorMemberId': author, 'authorInstanceId': instance, 'sanitizedPreview': preview, 'bodyRef': None}


def test_strict_payload_uses_status_and_rejects_state():
    assert ConversationRun.from_payload(run_payload('r', 'm', 'i', 'e')).status == 'queued'
    bad = run_payload('r', 'm', 'i', 'e'); bad['state'] = 'queued'
    try: ConversationRun.from_payload(bad)
    except ValueError: pass
    else: assert False


def test_metadata_redaction_and_refs():
    out = safe_metadata({'summary': 'token=[REDACTED] patient John', 'refs': ['run://r1', 'https://unsafe'],
                         'bodyRef': {'mode': 'local_only', 'ref': 'local://answer/1', 'keyEnvelopeId': None}})
    assert 'abc' not in out['summary'] and 'John' not in out['summary']
    assert out['refs'] == ['run://r1']
    assert out['bodyRef'] == {'mode': 'local_only', 'ref': 'local://answer/1', 'keyEnvelopeId': None}
    assert 'phi' not in out['safetyFlags']


class Store:
    def __init__(self):
        self.events = [event('e0')]
        self.runs = [run_payload('r0', 'kikuchi', 'k-1', 'e0')]
        self.calls = []
        self.n = 1
    def list_conversation_runs(self, identity, **kw):
        if identity.member_id != kw.get('targetMemberId') or identity.instance_id != kw.get('targetInstanceId'): return {'runs': []}
        return {'runs': [r for r in self.runs if r['targetMemberId'] == kw.get('targetMemberId') and r['targetInstanceId'] == kw.get('targetInstanceId')]}
    def list_conversation_events(self, identity, **kw): return {'events': list(self.events)}
    def claim_conversation_run(self, identity, **kw):
        self.calls.append('claim'); row = next(r for r in self.runs if r['runId'] == kw['runId']); row['status'] = 'claimed'; return {'run': dict(row)}
    def append_conversation_event(self, identity, **kw):
        eid = f'e{self.n}'; self.n += 1
        e = event(eid, kw['kind'], identity.member_id, identity.instance_id, kw['sanitizedPreview']); self.events.append(e); return {'event': e}
    def create_conversation_run(self, identity, **kw):
        rid = f'r{self.n}'; self.n += 1; row = run_payload(rid, kw['targetMemberId'], kw['targetInstanceId'], kw['triggeredByEventId'], revision=1); self.runs.append(row); return {'run': row}
    def complete_conversation_run(self, identity, **kw): self.calls.append('complete'); return {'run': {}}
    def fail_conversation_run(self, identity, **kw): self.calls.append('fail')

class Identity:
    def __init__(self, member, instance): self.member_id, self.instance_id = member, instance


def test_retry_attempts_get_distinct_idempotency_keys():
    runner = PeerCollaborationRunner(None, None, target_member_id='taro', target_instance_id='taro-1', executor=lambda *_: {}, validator=lambda *_: 'accepted')
    assert runner._key('run-1', 'complete', 1) == runner._key('run-1', 'complete', 1)
    assert runner._key('run-1', 'complete', 1) != runner._key('run-1', 'complete', 2)


def test_decision_required_stops_without_creating_revision_run():
    store = Store()
    store.events.append(event('e1', kind='assistant_message', author='kikuchi', instance='k-1'))
    store.runs.append(run_payload('r1', 'taro', 'taro-1', 'e1'))
    validator = PeerCollaborationRunner(store, Identity('taro', 'taro-1'), target_member_id='taro', target_instance_id='taro-1', executor=lambda r,e: {}, validator=lambda r,e: 'decision_required', mode='validator')
    before = len(store.runs)
    result = validator.run_once()
    assert result is not None
    assert result['status'] == 'decision_required'
    assert len(store.runs) == before
    assert store.calls == ['claim', 'complete']


def test_executor_and_validator_flow_with_wrong_instance_rejection():
    store = Store(); wrong = PeerCollaborationRunner(store, Identity('kikuchi', 'wrong'), target_member_id='kikuchi', target_instance_id='k-1', executor=lambda r,e: {}, validator=lambda r,e: 'accepted')
    assert wrong.run_once() is None
    executor = PeerCollaborationRunner(store, Identity('kikuchi', 'k-1'), target_member_id='kikuchi', target_instance_id='k-1', executor=lambda r,e: {'summary':'answer'}, validator=lambda r,e: 'accepted')
    result = executor.run_once(); assert result['validationRunId']
    validator = PeerCollaborationRunner(store, Identity('taro', 'taro-1'), target_member_id='taro', target_instance_id='taro-1', executor=lambda r,e: {}, validator=lambda r,e: 'accepted', mode='validator')
    assert validator.run_once()['status'] == 'accepted'
    assert store.calls == ['claim', 'complete', 'claim', 'complete']
