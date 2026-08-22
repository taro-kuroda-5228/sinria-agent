"""Sinria organizational context-sharing boundary."""

from .bridge import candidate_payloads, validate_metadata_only
from .client import CompanyOsKnowledgeClient, ProposalResult, TransportOutcomeUnknown
from .policy import KillSwitch, ScopePolicy, WorkspaceIdentity
from .readback import apply_review_readback
from .source_client import TeamSourceClient, WorkspaceSource
from .google_adapters import (CredentialError, JsonCheckpoint, GoogleDriveChangesAdapter,
                              GoogleGmailMetadataAdapter, GoogleGmailApprovalAdapter,
                              load_stored_user_credentials, build_google_workspace_adapters)
from .state import ReceiptLedger
from .workflow import apply_remote_reviews, sync_review_queue
from .execution import OAuthLifecycle, SyntheticDrive, SyntheticGmail, TeamKnowledge, run_synthetic_full_loop
from .operational_drill import DrillError, STEPS, run_synthetic_operational_drill
from .full_loop import (AssetProfile, AssetProfiler, Evidence, ExecutionEngine, FakeProvider,
                        Opportunity, OpportunityLedger, Plan, Rulebook, RulebookStore,
                        Task, TaskContinuation, WorkerQueue)

__all__ = [
    "CompanyOsKnowledgeClient",
    "KillSwitch",
    "ProposalResult",
    "ReceiptLedger",
    "ScopePolicy",
    "TeamSourceClient",
    "TransportOutcomeUnknown",
    "WorkspaceIdentity",
    "WorkspaceSource",
    "CredentialError", "JsonCheckpoint", "GoogleDriveChangesAdapter",
    "GoogleGmailMetadataAdapter", "GoogleGmailApprovalAdapter", "load_stored_user_credentials", "build_google_workspace_adapters",
    "apply_remote_reviews",
    "apply_review_readback",
    "candidate_payloads",
    "sync_review_queue",
    "validate_metadata_only",
    "DrillError", "STEPS", "run_synthetic_operational_drill",
    "AssetProfile", "AssetProfiler", "Evidence", "ExecutionEngine", "FakeProvider",
    "Opportunity", "OpportunityLedger", "Plan", "Rulebook", "RulebookStore",
    "Task", "TaskContinuation", "WorkerQueue",
]
