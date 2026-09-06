from __future__ import annotations

from typing import Any, Dict


class AgentEvaluator:
    def evaluate(self, state: Any) -> Dict[str, Any]:
        if state.step_index >= 8:
            return {"done": True, "reason": "step_budget_exceeded"}
        if state.draft and state.quality:
            return {"done": state.quality.get("passed") is not False or state.retry_count >= 2,
                    "reason": "quality_gate_complete" if state.quality.get("passed") is not False else "quality_retry_budget_exceeded"}
        if any(item.get("blocked") for item in state.observations):
            return {"done": True, "reason": "policy_blocked"}
        return {"done": False, "reason": "continue"}


evaluator = AgentEvaluator()
