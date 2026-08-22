"""Scoped feature flags and kill-switch precedence."""
from dataclasses import dataclass, field
DEFAULT_FLAGS={"company_context_read_enabled":True,"company_context_cloud_metadata_write_enabled":False,"curation_shadow_enabled":False,"curation_provider_mutation_enabled":False,"growth_detection_enabled":False,"growth_action_enabled":False,"learning_replay_enabled":False,"learning_canary_enabled":False,"artifact_activation_enabled":False}
@dataclass
class FeatureFlags:
    values: dict[str,bool] = field(default_factory=dict); global_kill: bool=False; instance_kill: bool=False; connector_kill: bool=False; source_kill: bool=False; tier_kill: bool=False; egress_kill: bool=False
    def __post_init__(self): self.values={**DEFAULT_FLAGS, **self.values}
    def enabled(self, name: str) -> bool:
        if name not in self.values: return False
        if any((self.global_kill,self.instance_kill,self.connector_kill,self.source_kill,self.tier_kill,self.egress_kill)): return False
        return self.values[name]
    def kill(self): self.global_kill=True
