from __future__ import annotations

from typing import Any, Dict


class AgentPolicy:
    MAX_STEPS = 8
    MAX_TOOL_CALLS = 12
    MAX_MODEL_CALLS = 8

    def authorize(self, action: str, state: Any) -> Dict[str, Any]:
        if state.step_index >= self.MAX_STEPS:
            return {"allowed": False, "reason": "step_budget_exceeded"}
        if len(state.tool_history) >= self.MAX_TOOL_CALLS:
            return {"allowed": False, "reason": "tool_budget_exceeded"}
        if action in {"wechat_publish", "delete_document"}:
            return {"allowed": False, "reason": "manual_approval_required"}
        mounted = (state.constraints.get("mounted_skill") or {}) if hasattr(state, "constraints") else {}
        allowed_tools = set(str(item) for item in (mounted.get("allowed_tools") or []))
        # Core evidence operations remain available to every Skill. A Skill
        # contract controls optional side-effect/external tools explicitly.
        core_actions = {"context_search", "evidence_deduplicate", "multi_document_coverage",
                        "evidence_cluster", "draft_generate", "citation_verify"}
        if allowed_tools and action not in core_actions and action not in allowed_tools:
            return {"allowed": False, "reason": "skill_tool_not_allowed"}
        return {"allowed": True, "reason": "allowlisted"}


policy = AgentPolicy()
