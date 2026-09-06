#!/usr/bin/env python3
"""Persistent peer worker. Commands receive only validated metadata JSON on stdin."""
import argparse, json, os, shlex, subprocess, sys, time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.company_os_transport import CompanyOsTransportClient, CompanyOsTransportIdentity
from sinria_peer_collaboration import PeerCollaborationRunner, SAFE_PEER_ERROR_CODES, sanitize_summary
from sinria_team_project_transport import CompanyOsTeamProjectAdapter
from sinria_team_projects import Worker


def parse_team_capabilities(value):
    capabilities = {item.strip() for item in str(value or '').split(',') if item.strip()}
    if any(len(item) > 80 or not all(char.isalnum() or char in '._:-' for char in item) for item in capabilities):
        raise ValueError('invalid team capability')
    return capabilities


def publish_team_presence(adapter, *, member_id, instance_id, capabilities):
    if not capabilities:
        return None
    return adapter.publish_heartbeat(
        Worker(member_id, instance_id, set(capabilities), fresh=True)
    )


def command_adapter(name, *, mode):
    command = os.environ.get(name, "").strip()
    if not command:
        raise RuntimeError(f"{name} is not configured; refusing dummy peer completion")
    argv = shlex.split(command)
    if not argv:
        raise RuntimeError(f"{name} is empty")
    def invoke(run, event):
        payload = json.dumps({"run": asdict(run) if is_dataclass(run) else run, "event": event}, ensure_ascii=False)
        try:
            proc = subprocess.run(argv, input=payload, text=True, capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("local peer command timed out") from exc
        if proc.returncode != 0:
            try:
                parsed = json.loads(proc.stdout)
                code = parsed.get("errorCode") if isinstance(parsed, dict) else None
                if isinstance(code, str) and code in SAFE_PEER_ERROR_CODES:
                    raise RuntimeError(code)
            except (TypeError, json.JSONDecodeError):
                pass
            raise RuntimeError("peer command failed")
        try:
            value = json.loads(proc.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("local peer command must return valid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("local peer command must return a JSON object")
        if mode == "executor":
            result = value.get("result", value)
            if not isinstance(result, dict): raise RuntimeError("executor result must be an object")
            forbidden = {"prompt", "rawPrompt", "body", "rawContext"}
            if forbidden.intersection(result): raise RuntimeError("executor returned forbidden raw content")
            return result
        verdict = value.get("verdict", value.get("result"))
        if verdict not in {"accepted", "revision_requested", "decision_required"}:
            raise RuntimeError("validator returned an invalid verdict")
        return verdict
    return invoke


def notification_receipt(completed):
    failure = {'status': 'notify_error', 'error': 'peer notification delivery failed'}
    if completed.returncode != 0:
        return failure
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError):
        return failure
    if not isinstance(payload, dict) or payload.get('success') is not True:
        return failure
    target = payload.get('target')
    message_id = payload.get('messageId')
    if not isinstance(target, str) or not target or not isinstance(message_id, str) or not message_id:
        return failure
    return {'status': 'notified', 'target': target, 'messageId': message_id}


def main():
    load_dotenv(Path.home() / '.sinria' / '.env', override=False)
    if os.environ.get('SINRIA_COMPANY_OS_TRANSPORT_SUBJECT'):
        os.environ.setdefault('COMPANY_OS_TRANSPORT_SUBJECT', os.environ['SINRIA_COMPANY_OS_TRANSPORT_SUBJECT'])
    p = argparse.ArgumentParser()
    p.add_argument('--once', action='store_true')
    p.add_argument('--preflight', action='store_true', help='verify transport identity and polling without executing work')
    p.add_argument('--poll-interval', type=float, default=float(os.environ.get('PEER_POLL_INTERVAL', '15')))
    p.add_argument('--mode', choices=('executor','validator'), default=os.environ.get('PEER_MODE','executor'))
    a = p.parse_args()
    if a.poll_interval <= 0: p.error('--poll-interval must be positive')
    required = ('COMPANY_OS_BASE_URL','COMPANY_OS_MEMBER_ID','COMPANY_OS_INSTANCE_ID','COMPANY_OS_TRANSPORT_SUBJECT','SINRIA_COMPANY_OS_TRANSPORT_TOKEN')
    if not all(os.environ.get(x) for x in required): p.error('peer worker requires explicit Company OS identity configuration')
    client = CompanyOsTransportClient(
        os.environ['COMPANY_OS_BASE_URL'],
        token_env='SINRIA_COMPANY_OS_TRANSPORT_TOKEN',
    )
    ident = CompanyOsTransportIdentity(os.environ['COMPANY_OS_TRANSPORT_SUBJECT'], os.environ['COMPANY_OS_MEMBER_ID'], os.environ['COMPANY_OS_INSTANCE_ID'])
    capabilities = parse_team_capabilities(os.environ.get('SINRIA_TEAM_CAPABILITIES', ''))
    team_adapter = None
    if capabilities:
        space_id = os.environ.get('SINRIA_TEAM_SPACE_ID', '').strip()
        conversation_id = os.environ.get('SINRIA_TEAM_CONVERSATION_ID', '').strip()
        if not space_id or not conversation_id:
            p.error('team capabilities require explicit space and conversation configuration')
        team_adapter = CompanyOsTeamProjectAdapter(
            client,
            ident,
            space_id=space_id,
            conversation_id=conversation_id,
        )
    if a.preflight:
        canary = client.canary(ident)
        listed = client.list_conversation_runs(
            ident,
            targetMemberId=ident.member_id,
            targetInstanceId=ident.instance_id,
        )
        resolved = canary.get('resolvedIdentity') or canary.get('identity') or {}
        runs = listed.get('runs') or []
        print(json.dumps({
            'ok': bool(canary.get('ok')),
            'memberId': resolved.get('memberId', ident.member_id),
            'instanceId': resolved.get('instanceId', ident.instance_id),
            'workspaceId': resolved.get('workspaceId'),
            'queuedRuns': sum(1 for run in runs if run.get('status') in {'queued', 'failed_recoverable'}),
        }, ensure_ascii=False))
        return
    command = command_adapter('PEER_EXECUTOR_COMMAND' if a.mode == 'executor' else 'PEER_VALIDATOR_COMMAND', mode=a.mode)
    runner = PeerCollaborationRunner(client, ident, target_member_id=ident.member_id, target_instance_id=ident.instance_id,
                                     executor=command, validator=command, mode=a.mode)
    if a.once:
        if team_adapter is not None:
            publish_team_presence(
                team_adapter,
                member_id=ident.member_id,
                instance_id=ident.instance_id,
                capabilities=capabilities,
            )
        print(json.dumps(runner.run_once(), ensure_ascii=False), flush=True)
        return
    last_team_heartbeat = 0.0
    while True:
        try:
            now = time.time()
            if team_adapter is not None and now - last_team_heartbeat >= 60:
                publish_team_presence(
                    team_adapter,
                    member_id=ident.member_id,
                    instance_id=ident.instance_id,
                    capabilities=capabilities,
                )
                last_team_heartbeat = now
            result = runner.run_once()
            if result is not None:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                notify_target = os.environ.get('PEER_NOTIFY_TARGET', '').strip()
                if a.mode == 'validator' and notify_target:
                    notifier = Path(__file__).resolve().with_name('sinria-peer-notify.py')
                    completed = subprocess.run(
                        [sys.executable, str(notifier)],
                        input=json.dumps(result, ensure_ascii=False),
                        text=True,
                        capture_output=True,
                        env={**os.environ, 'PEER_NOTIFY_TARGET': notify_target},
                        timeout=30,
                        check=False,
                    )
                    receipt = notification_receipt(completed)
                    print(json.dumps(receipt, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "poll_error", "error": sanitize_summary(exc)}, ensure_ascii=False), flush=True)
        time.sleep(a.poll_interval)
if __name__ == '__main__': main()
