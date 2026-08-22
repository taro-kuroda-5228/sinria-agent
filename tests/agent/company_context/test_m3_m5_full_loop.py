import pytest
from agent.company_context.asset import AssetProfiler, Rulebook, RulebookStore
from agent.company_context.execution import ExecutionEngine, FakeProvider, WorkerQueue
from agent.company_context.opportunities import Evidence, OpportunityLedger
from agent.company_context.tasks import TaskContinuation
from agent.company_context.full_loop import AssetCurationWorkflow, OpportunityWorkflow


def test_asset_rulebook_confidence_and_readback():
    store = RulebookStore(Rulebook("1", {"invoice": "invoice"}))
    profiler = AssetProfiler(store)
    profile = profiler.profile("a1", {"kind": "document", "name": "invoice-1"})
    assert profile.classifier == "invoice" and profile.confidence == .95
    assert profiler.read("a1").rulebook_hash == store.read().hash
    assert store.sync(Rulebook("1", {"invoice": "invoice"})) == "no-op"
    assert profiler.profile("a2", {"kind": "unknown", "name": "other"}).confidence == 0


def test_preview_execute_undo_are_idempotent_and_readback_only():
    provider = FakeProvider(); engine = ExecutionEngine(provider)
    plan = engine.preview("task", {"status": "ready"})
    assert provider.writes == []
    first = engine.execute(plan); second = engine.execute(plan)
    assert first == second and provider.writes == [plan.idempotency_key]
    assert engine.undo(plan)["deleted"] and engine.undo(plan)["deleted"]


def test_lease_fencing_and_opportunity_deduplication():
    now = [0.0]; queue = WorkerQueue(lease_seconds=5, clock=lambda: now[0])
    first = queue.claim("t", "w1"); assert queue.claim("t", "w2") is None
    now[0] = 6; second = queue.claim("t", "w2")
    with pytest.raises(PermissionError): queue.commit(first)
    assert queue.commit(second) and not queue.commit(second)
    ledger = OpportunityLedger(); one = ledger.detect("same"); two = ledger.detect("same")
    assert one is two
    ledger.claim(one.opportunity_id, "w2")
    ledger.add_evidence(one.opportunity_id, Evidence("provider-readback", "ready", "now", "1"))
    assert len(ledger.read(one.opportunity_id).evidence) == 1


def test_activation_waits_for_approval_and_continues_with_same_plan():
    provider = FakeProvider(); execution = ExecutionEngine(provider)
    plan = execution.preview("approval", {"approved": True})
    tasks = TaskContinuation(execution); task = tasks.activate("approval", plan)
    assert task.status == "awaiting_approval" and provider.writes == []
    done = tasks.approve("approval", "approve")
    assert done.status == "completed" and provider.read(plan.idempotency_key) == done.result
    assert tasks.approve("approval", "approve").result == done.result
    with pytest.raises(RuntimeError): tasks.approve("approval", "reject")


def test_asset_curation_single_entrypoint_preview_approval_readback_and_undo():
    provider = FakeProvider()
    execution = ExecutionEngine(provider)
    workflow = AssetCurationWorkflow(
        profiler=AssetProfiler(RulebookStore(Rulebook("1", {"invoice": "invoice"}))),
        execution=execution,
        tasks=TaskContinuation(execution),
    )
    profile, task = workflow.preview("asset-1", {"kind": "document", "name": "invoice-1"}, {"gmailDraft": "metadata-only"})
    assert profile.classifier == "invoice" and task.status == "awaiting_approval"
    assert provider.writes == []
    result = workflow.decide("asset-1", "approve")
    assert result["status"] == "completed" and result["providerReadback"]["gmailDraft"] == "metadata-only"
    assert workflow.undo("asset-1")["deleted"] is True


def test_opportunity_single_entrypoint_claim_approval_result_outcome():
    provider = FakeProvider()
    execution = ExecutionEngine(provider)
    workflow = OpportunityWorkflow(
        opportunities=OpportunityLedger(),
        tasks=TaskContinuation(execution),
        execution=execution,
    )
    result = workflow.run(
        fingerprint="source:gap:1",
        worker_id="worker-a",
        evidence=Evidence("provider-readback", "gap", "now", "1"),
        desired={"action": "prepare-approved-draft"},
        decision="approve",
        metric="qualified_result",
        value=1,
    )
    assert result["status"] == "completed"
    assert result["providerReadback"]["action"] == "prepare-approved-draft"
    assert result["rawContextStored"] is False
    assert len(workflow.outcomes) == 1
