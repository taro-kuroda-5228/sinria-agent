"""Model-facing metadata-only peer collaboration tools."""
import hashlib, json, os
from typing import Any
from gateway.company_os_transport import CompanyOsTransportClient, CompanyOsTransportIdentity
from tools.registry import registry


def _configured():
    return all(os.environ.get(k) for k in ("COMPANY_OS_BASE_URL", "COMPANY_OS_MEMBER_ID", "COMPANY_OS_INSTANCE_ID", "COMPANY_OS_TRANSPORT_SUBJECT"))
def _client():
    return CompanyOsTransportClient(os.environ["COMPANY_OS_BASE_URL"]), CompanyOsTransportIdentity(os.environ["COMPANY_OS_TRANSPORT_SUBJECT"], os.environ["COMPANY_OS_MEMBER_ID"], os.environ["COMPANY_OS_INSTANCE_ID"])
def _key(args):
    raw = "|".join(str(args[k]) for k in ("space_id", "conversation_id", "target_member_id", "target_instance_id", "summary"))
    return hashlib.sha256(("peer-delegate:" + raw).encode()).hexdigest()
def _handle_delegate(args, **_):
    required = ("space_id", "conversation_id", "target_member_id", "target_instance_id", "summary")
    if any(not isinstance(args.get(k), str) or not args[k].strip() for k in required):
        return json.dumps({"error": "space_id, conversation_id, target_member_id, target_instance_id, and summary are required"})
    if not _configured(): return json.dumps({"error": "peer collaboration is not configured"})
    client, identity = _client(); key = args.get("idempotency_key") or _key(args)
    refs = args.get("refs", [])
    if not isinstance(refs, list): return json.dumps({"error": "refs must be a list"})
    from sinria_peer_collaboration import safe_metadata
    payload = safe_metadata({"summary": args["summary"], "refs": refs})
    event = client.append_conversation_event(identity, spaceId=args["space_id"], conversationId=args["conversation_id"], kind="user_message",
        sanitizedPreview=payload["summary"], bodyRef=payload.get("bodyRef"), idempotencyKey=key + ":event")
    event_obj = event.get("event", event)
    run = client.create_conversation_run(identity, spaceId=args["space_id"], conversationId=args["conversation_id"],
        triggeredByEventId=event_obj["eventId"], idempotencyKey=key + ":run", targetMemberId=args["target_member_id"], targetInstanceId=args["target_instance_id"])
    return json.dumps(run, ensure_ascii=False)
def _handle_status(args, **_):
    if not _configured(): return json.dumps({"error": "peer collaboration is not configured"})
    client, identity = _client()
    result = client.list_conversation_runs(identity, targetMemberId=identity.member_id, targetInstanceId=identity.instance_id)
    if args.get("run_id"):
        result["runs"] = [r for r in result.get("runs", []) if r.get("runId") == args["run_id"]]
    return json.dumps(result, ensure_ascii=False)
def _check(): return _configured()

registry.register(name="peer_delegate", toolset="peer_collaboration", schema={"name":"peer_delegate","description":"Append a sanitized user message and delegate it to a configured peer.","parameters":{"type":"object","properties":{"space_id":{"type":"string"},"conversation_id":{"type":"string"},"target_member_id":{"type":"string"},"target_instance_id":{"type":"string"},"summary":{"type":"string"},"refs":{"type":"array","items":{"type":"string"}}},"required":["space_id","conversation_id","target_member_id","target_instance_id","summary"]}}, handler=_handle_delegate, check_fn=_check)
registry.register(name="peer_collaboration_status", toolset="peer_collaboration", schema={"name":"peer_collaboration_status","description":"List peer collaboration runs targeted to this actor and instance.","parameters":{"type":"object","properties":{"run_id":{"type":"string"}}}}, handler=_handle_status, check_fn=_check)
