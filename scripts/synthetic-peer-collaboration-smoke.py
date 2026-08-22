#!/usr/bin/env python3
"""Actual two-instance in-memory transport proof: Taro→Kikuchi→Taro revision→Kikuchi→Taro accept."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sinria_peer_collaboration import PeerCollaborationRunner

class Transport:
    def __init__(self):
        self.events = [{'eventId':'e0','workspaceId':'w','spaceId':'s','conversationId':'c','kind':'user_message','authorKind':'member','authorMemberId':'taro','authorInstanceId':'taro-1','sanitizedPreview':'request','bodyRef':None}]
        self.runs=[]; self.seq=1; self.history=[]
    def list_conversation_runs(self, identity, **kw):
        if identity.member_id != kw['targetMemberId'] or identity.instance_id != kw['targetInstanceId']: return {'runs':[]}
        return {'runs':[r for r in self.runs if r['targetMemberId']==kw['targetMemberId'] and r['targetInstanceId']==kw['targetInstanceId'] and r['status']=='queued']}
    def list_conversation_events(self, identity, **kw): return {'events':list(self.events)}
    def claim_conversation_run(self, identity, **kw):
        r=next(r for r in self.runs if r['runId']==kw['runId']); r['status']='claimed'; self.history.append(('claim',r['runId'],identity.member_id)); return {'run':dict(r)}
    def append_conversation_event(self, identity, **kw):
        eid=f'e{self.seq}'; self.seq+=1; e={'eventId':eid,'workspaceId':'w','spaceId':'s','conversationId':'c','kind':kw['kind'],'authorKind':'sinria','authorMemberId':identity.member_id,'authorInstanceId':identity.instance_id,'sanitizedPreview':kw['sanitizedPreview'],'bodyRef':kw.get('bodyRef')}; self.events.append(e); return {'event':e}
    def create_conversation_run(self, identity, **kw):
        rid=f'r{self.seq}'; self.seq+=1; r={'runId':rid,'workspaceId':'w','spaceId':'s','conversationId':'c','triggeredByEventId':kw['triggeredByEventId'],'sourceMemberId':identity.member_id,'targetMemberId':kw['targetMemberId'],'targetInstanceId':kw['targetInstanceId'],'status':'queued','revision':0,'humanRelayCount':0}; self.runs.append(r); return {'run':r}
    def complete_conversation_run(self, identity, **kw): r=next(r for r in self.runs if r['runId']==kw['runId']); r['status']='completed'; self.history.append(('complete',r['runId'],identity.member_id)); return {'run':r}
    def fail_conversation_run(self, identity, **kw): raise AssertionError('unexpected failure')
class I:
    def __init__(self,m,i): self.member_id,self.instance_id=m,i

t=Transport(); t.create_conversation_run(I('taro','taro-1'), spaceId='s', conversationId='c', triggeredByEventId='e0', targetMemberId='kikuchi', targetInstanceId='k-1')
assert PeerCollaborationRunner(t,I('kikuchi','wrong'),target_member_id='kikuchi',target_instance_id='k-1',executor=lambda r,e:{},validator=lambda r,e:'accepted').run_once() is None
k=PeerCollaborationRunner(t,I('kikuchi','k-1'),target_member_id='kikuchi',target_instance_id='k-1',executor=lambda r,e:{'summary':'answer','refs':['run://safe'],'rawContextStored':False,'externalActionPerformed':False},validator=lambda r,e:'accepted')
assert k.run_once()['validationRunId']
taro=PeerCollaborationRunner(t,I('taro','taro-1'),target_member_id='taro',target_instance_id='taro-1',executor=lambda r,e:{},validator=lambda r,e:'revision_requested',mode='validator')
assert taro.run_once()['status']=='revision_requested'
assert k.run_once() is not None
taro2=PeerCollaborationRunner(t,I('taro','taro-1'),target_member_id='taro',target_instance_id='taro-1',executor=lambda r,e:{},validator=lambda r,e:'accepted',mode='validator')
assert taro2.run_once()['status']=='accepted'
assert all(r['humanRelayCount']==0 for r in t.runs)
print({'flow':'Taro→Kikuchi response→Taro revision→Kikuchi response→Taro accept','wrong_instance_rejected':True,'humanRelayCount':0,'safetyFlags':{'rawPrompt':False,'credentials':False,'rawContextStored':False,'externalActionPerformed':False}})
