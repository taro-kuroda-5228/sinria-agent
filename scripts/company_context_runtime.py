"""Operational M6-M8 CLI using the durable SQLite ContextLedger entrypoint."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.company_context.operations import ContextLedger
from agent.company_context.operational_drill import run_synthetic_operational_drill

def main(argv=None) -> int:
    p=argparse.ArgumentParser(prog="company-context-runtime")
    p.add_argument("--db", required=True); p.add_argument("--profile", required=True)
    s=p.add_subparsers(dest="op", required=True)
    s.add_parser("status")
    r=s.add_parser("receipt"); r.add_argument("receipt_id"); r.add_argument("outcome"); r.add_argument("latency_ms", type=float); r.add_argument("--payload", default="{}")
    g=s.add_parser("gap"); g.add_argument("receipt_id"); g.add_argument("metric"); g.add_argument("expected", type=float); g.add_argument("actual", type=float)
    c=s.add_parser("candidate"); c.add_argument("gap_id"); c.add_argument("revision"); c.add_argument("content")
    x=s.add_parser("replay"); x.add_argument("candidate_id"); x.add_argument("--corpus", default="[]")
    v=s.add_parser("review"); v.add_argument("candidate_id"); v.add_argument("reviewer"); v.add_argument("decision", choices=("approve","reject"))
    a=s.add_parser("activate"); a.add_argument("revision"); a.add_argument("--index-revision")
    k=s.add_parser("kill-switch"); k.add_argument("state", choices=("on","off"))
    sl=s.add_parser("slo"); sl.add_argument("slo_ms", type=int); sl.add_argument("--latency-ms", type=float)
    b=s.add_parser("backup"); b.add_argument("destination")
    z=s.add_parser("restore"); z.add_argument("source")
    d=s.add_parser("drill"); d.add_argument("--run-id", default="synthetic-drill-1"); d.add_argument("--synthetic", action="store_true"); d.add_argument("--fail-step")
    args=p.parse_args(argv)
    if args.op == "drill":
        try:
            print(json.dumps(run_synthetic_operational_drill(args.db, profile=args.profile, run_id=args.run_id, synthetic=args.synthetic, fail_step=args.fail_step), sort_keys=True, ensure_ascii=False))
            return 0
        except Exception as exc:
            print(json.dumps({"error":type(exc).__name__,"message":str(exc)}), file=sys.stderr); return 1
    ledger=ContextLedger(args.db)
    try:
        if args.op=="status": out=ledger.runtime_status(args.profile)
        elif args.op=="receipt": ledger.record_receipt(args.profile,args.receipt_id,args.outcome,args.latency_ms,json.loads(args.payload)); out={"receipt_id":args.receipt_id}
        elif args.op=="gap": out={"gap_id":ledger.record_gap(args.profile,args.receipt_id,args.metric,args.expected,args.actual)}
        elif args.op=="candidate": out={"candidate_id":ledger.candidate(args.profile,args.gap_id,args.revision,args.content)}
        elif args.op=="replay": out={"result":ledger.replay_candidate(args.profile,args.candidate_id,json.loads(args.corpus))}
        elif args.op=="review": out={"binding_hash":ledger.review_candidate(args.profile,args.candidate_id,args.reviewer,args.decision)}
        elif args.op=="activate": out={"generation":ledger.activate_manifest(args.profile,args.revision,index_revision=args.index_revision)}
        elif args.op=="kill-switch": ledger.set_kill_switch(args.profile,args.state=="on"); out=ledger.runtime_status(args.profile)
        elif args.op=="slo": ledger.set_slo(args.profile,args.slo_ms); out={"breach":ledger.evaluate_alert(args.profile,args.latency_ms)} if args.latency_ms is not None else ledger.runtime_status(args.profile)
        elif args.op=="backup": ledger.backup(args.destination); out={"backup":str(Path(args.destination))}
        elif args.op=="restore": ledger.restore(args.source); out=ledger.runtime_status(args.profile)
        print(json.dumps(out, sort_keys=True, ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"error":type(exc).__name__,"message":str(exc)}), file=sys.stderr); return 1
    finally: ledger.close()
if __name__ == "__main__": raise SystemExit(main())
