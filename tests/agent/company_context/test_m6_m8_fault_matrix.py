"""Synthetic A-D fault matrix against the real durable ledger/CLI path."""
import json, subprocess, sys
from pathlib import Path
from agent.company_context.operations import ContextLedger, LedgerError, ApprovalBindingError

ROOT = str(Path(__file__).resolve().parents[3] / "scripts/company_context_runtime.py")
def cli(db, profile, *args):
    return subprocess.run([sys.executable, ROOT, "--db", str(db), "--profile", profile, *args], text=True, capture_output=True)

def test_workflow_a_receipt_gap_candidate_replay_review_restart(tmp_path):
    db=tmp_path/"a.db"; assert cli(db,"team-a","receipt","r1","ok","12","--payload",'{}').returncode==0
    assert cli(db,"team-a","gap","r1","quality","1","0").returncode==0
    gap=json.loads(cli(db,"team-a","gap","r1","quality","1","0").stdout)["gap_id"]
    cid=json.loads(cli(db,"team-a","candidate",gap,"r2","new behavior").stdout)["candidate_id"]
    assert cli(db,"team-a","replay",cid,"--corpus",'[{"pass": true}]').returncode==0
    assert cli(db,"team-a","review",cid,"human","approve").returncode==0
    assert "team-a" not in cli(db,"team-b","status").stdout

def test_workflow_b_atomic_activation_and_restart_rollback(tmp_path):
    db=tmp_path/"b.db"; l=ContextLedger(db); assert l.activate_manifest("p","rev-1")==1
    try: l.activate_manifest("p","rev-2",fail=True)
    except LedgerError: pass
    assert l.runtime_status("p")["active_revision"]=="rev-1"; l.close()
    l=ContextLedger(db); assert l.activate_manifest("p","rev-2")==2; assert l.runtime_status("p")["index_revision"]=="rev-2"; l.close()

def test_workflow_c_kill_switch_quota_and_restore(tmp_path):
    db=tmp_path/"c.db"; l=ContextLedger(db); l.set_kill_switch("p",True)
    try: l.record_receipt("p","r","ok",1,{})
    except LedgerError: pass
    else: assert False
    l.set_kill_switch("p",False); l.set_quota("p",1); l.reserve("p",1)
    backup=tmp_path/"backup.db"; l.backup(backup); l.close(); restored=ContextLedger(tmp_path/"restored.db"); restored.restore(backup)
    assert restored.runtime_status("p")["kill_switch"]==0; restored.close()

def test_workflow_d_profile_binding_and_review_fault(tmp_path):
    l=ContextLedger(tmp_path/"d.db"); l.record_receipt("a","r", "ok", 1, {}); gap=l.record_gap("a","r","m",1,0); cid=l.candidate("a",gap,"r","x"); l.replay_candidate("a",cid,[{"pass":True}])
    try: l.review_candidate("b",cid,"h","approve")
    except ApprovalBindingError: pass
    else: assert False
    assert l.runtime_status("b")["active_revision"] is None; l.close()
