#!/usr/bin/env python3
"""Queue a metadata-only consultation.v1 request to a peer Sinria."""
from __future__ import annotations
import argparse, json, os, time, uuid
from pathlib import Path
from dotenv import load_dotenv
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.company_os_transport import CompanyOsTransportClient, CompanyOsTransportIdentity
from sinria_consultation import validate_consultation

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--space-id', required=True); p.add_argument('--conversation-id', required=True)
    p.add_argument('--target-member-id', required=True); p.add_argument('--target-instance-id', required=True)
    p.add_argument('--question', required=True); p.add_argument('--resource-id', required=True); p.add_argument('--range', required=True)
    p.add_argument('--version', default=''); a = p.parse_args()
    load_dotenv(Path.home()/'.sinria/.env', override=False)
    if len(a.question) > 500 or any(x in a.question.lower() for x in ('patient:', 'mrn:', 'password:', 'token:', 'secret:')):
        raise SystemExit('unsafe consultation question')
    cid = 'consult_' + uuid.uuid4().hex[:20]
    meta = validate_consultation({
        'schemaVersion':'consultation.v1','type':'consultation_request','consultationId':cid,'questionSummary':a.question,
        'sourceRefs':[{'provider':'google_workspace','resourceId':a.resource_id,'range':a.range,**({'version':a.version} if a.version else {})}],
        'humanDecisionRequired':False,'allowedOperations':['read','draft'],'sensitivity':'internal','rawContextStored':False,'externalActionPerformed':False})
    identity = CompanyOsTransportIdentity(os.environ['COMPANY_OS_TRANSPORT_SUBJECT'], os.environ['COMPANY_OS_MEMBER_ID'], os.environ.get('COMPANY_OS_INSTANCE_ID'))
    client = CompanyOsTransportClient(os.environ['COMPANY_OS_BASE_URL'])
    key = f'{cid}:{int(time.time())}'
    out = client.append_conversation_event(identity, spaceId=a.space_id, conversationId=a.conversation_id, kind='user_message',
        sanitizedPreview='Internal consultation request queued.', consultationMetadata=meta, bodyRef=None, idempotencyKey=key+':event')
    event = out.get('event', out)
    run = client.create_conversation_run(identity, spaceId=a.space_id, conversationId=a.conversation_id,
        triggeredByEventId=event['eventId'], targetMemberId=a.target_member_id, targetInstanceId=a.target_instance_id, idempotencyKey=key+':run')
    value = run.get('run', run)
    print(json.dumps({'ok':True,'consultationId':cid,'eventId':event['eventId'],'runId':value['runId'],'rawContextStored':False,'externalActionPerformed':False}, ensure_ascii=False))
    return 0
if __name__ == '__main__': raise SystemExit(main())
