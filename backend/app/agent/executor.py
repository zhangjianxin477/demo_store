from __future__ import annotations

import time
from typing import Any, Dict

from app.telemetry.recorder import telemetry


class AgentExecutor:
    def __init__(self, runtime: Any, policy: Any) -> None:
        self.runtime = runtime
        self.policy = policy

    async def execute(self, action: Dict[str, Any], state: Any, args: Dict[str, Any]) -> Dict[str, Any]:
        name = str(action.get("action") or "")
        permission = self.policy.authorize(name, state)
        if not permission["allowed"]:
            return {"ok": False, "action": name, "blocked": True, "reason": permission["reason"]}
        started = time.monotonic()
        telemetry.record("agent_decision_made", source="agent", task_id=state.task_id,
                         properties={"action": name, "step_index": state.step_index,
                                     "retry": bool(action.get("retry")),
                                     "model_selected": bool(action.get("model_selected")),
                                     "reason_code": action.get("reason_code", "")})
        try:
            result = await self.runtime.tools.call(name, args)
            elapsed = round((time.monotonic() - started) * 1000, 1)
            observation = {"ok": True, "action": name, "duration_ms": elapsed,
                           "result_keys": sorted(result.keys()) if isinstance(result, dict) else []}
            state.observations.append(observation)
            state.tool_history.append({"name": name, "status": "completed", "duration_ms": elapsed})
            return {"ok": True, "result": result, "observation": observation}
        except Exception as exc:
            elapsed = round((time.monotonic() - started) * 1000, 1)
            observation = {"ok": False, "action": name, "duration_ms": elapsed, "error_type": type(exc).__name__}
            state.observations.append(observation)
            state.tool_history.append({"name": name, "status": "failed", "duration_ms": elapsed, "error": str(exc)[:240]})
            return {"ok": False, "error": str(exc), "observation": observation}
