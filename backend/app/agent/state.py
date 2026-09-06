from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentState:
    """One bounded Agent run state. Hidden model reasoning is never stored."""
    task_id: str
    goal: str
    intent: str
    constraints: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    clusters: List[Dict[str, Any]] = field(default_factory=list)
    draft: str = ""
    quality: Dict[str, Any] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    current_action: str = ""
    retry_count: int = 0
    step_index: int = 0
    model_calls: int = 0
    status: str = "running"
    stop_reason: str = ""

    def snapshot(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "intent": self.intent, "current_action": self.current_action,
            "step_index": self.step_index, "retry_count": self.retry_count,
            "evidence_count": len(self.evidence), "cluster_count": len(self.clusters),
            "draft_chars": len(self.draft), "quality": self.quality,
            "coverage": self.coverage,
            "status": self.status, "stop_reason": self.stop_reason,
        }
