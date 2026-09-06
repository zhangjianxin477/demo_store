from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from .state import AgentState


class AgentPlanner:
    """State-aware bounded planner.

    The planner chooses the next allow-listed action from observations rather
    than blindly returning a prebuilt list. It is intentionally deterministic
    for the MVP, which keeps behavior stable when the model is unavailable.
    """

    def choose(self, state: AgentState) -> Dict[str, Any]:
        if state.status != "running":
            return {"action": "finish", "finish": True, "reason_code": state.stop_reason or "stopped"}
        mcp_request = state.constraints.get("mcp_request") or {}
        if mcp_request and not any(item.get("action") == "mcp_tool_call" for item in state.observations):
            return {"action": "mcp_tool_call", "reason_code": "explicit_mcp_request"}
        if not state.evidence:
            if any(item.get("action") == "context_search" for item in state.observations):
                if state.retry_count < 1:
                    state.retry_count += 1
                    return {"action": "context_search", "retry": True, "reason_code": "evidence_empty_retry"}
                return {"action": "finish", "finish": True, "reason_code": "evidence_unavailable"}
            return {"action": "context_search", "reason_code": "load_evidence"}
        if state.constraints.get("multi_document") and state.coverage and float(state.coverage.get("coverage_rate", 0)) < 1 and state.retry_count < 2:
            state.retry_count += 1
            return {"action": "context_search", "retry": True, "reason_code": "multi_document_coverage_retry"}
        if not any(item.get("action") == "evidence_deduplicate" for item in state.observations):
            return {"action": "evidence_deduplicate", "reason_code": "normalize_evidence"}
        if state.constraints.get("multi_document") and not state.coverage:
            return {"action": "multi_document_coverage", "reason_code": "check_all_documents_read"}
        if state.intent in {"hotspot", "weekly"} and not any(item.get("action") == "evidence_cluster" for item in state.observations):
            return {"action": "evidence_cluster", "reason_code": "group_related_sources"}
        if not state.draft:
            return {"action": "draft_generate", "reason_code": "write_deliverable"}
        if not state.quality:
            return {"action": "citation_verify", "reason_code": "verify_grounding"}
        if state.quality.get("passed") is False and state.retry_count < 2:
            state.retry_count += 1
            state.draft = ""
            return {"action": "draft_generate", "retry": True, "reason_code": "quality_gate_failed"}
        return {"action": "finish", "finish": True, "reason_code": "quality_gate_complete"}

    async def choose_async(self, state: AgentState, decider: Optional[Callable[[AgentState, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None) -> Dict[str, Any]:
        baseline = self.choose(state)
        if not decider or baseline.get("finish"):
            return baseline
        # The model may choose among actions that are valid for the current
        # state. It cannot invent tools or skip mandatory evidence gates.
        candidates = [baseline["action"]]
        if baseline["action"] == "context_search" and state.evidence:
            candidates.append("evidence_deduplicate")
        if baseline["action"] == "draft_generate" and state.quality:
            candidates.append("citation_verify")
        try:
            mcp_tools = state.constraints.get("mcp_tools") or []
            candidate_specs = list(candidates) + [
                {"action": "mcp_tool_call", "server": item.get("server"), "tool": item.get("name"),
                 "description": str(item.get("description") or "")[:240],
                 "inputSchema": item.get("inputSchema") or item.get("input_schema") or {}}
                for item in mcp_tools if isinstance(item, dict) and item.get("name")
            ]
            decision = await decider(state, {"candidates": candidate_specs, "baseline": baseline})
            action = str((decision or {}).get("action") or "")
            if action in candidates or (action == "mcp_tool_call" and
                any(decision.get("server") == item.get("server") and decision.get("tool") == item.get("tool")
                    for item in candidate_specs if isinstance(item, dict))):
                return {**baseline, **decision, "action": action, "model_selected": True}
        except Exception:
            pass
        return baseline


planner = AgentPlanner()
