import asyncio
import pytest

from app.agent.evaluator import AgentEvaluator
from app.agent.planner import AgentPlanner
from app.agent.state import AgentState


def test_planner_changes_action_after_observation():
    state = AgentState(task_id="t", goal="总结", intent="research")
    planner = AgentPlanner()
    assert planner.choose(state)["action"] == "context_search"
    state.evidence = [{"source_id": "a"}]
    state.observations.append({"action": "context_search", "ok": True})
    assert planner.choose(state)["action"] == "evidence_deduplicate"
    state.observations.append({"action": "evidence_deduplicate", "ok": True})
    assert planner.choose(state)["action"] == "draft_generate"


def test_multi_document_coverage_triggers_retry():
    state = AgentState(task_id="t", goal="梳理多篇", intent="summarize",
                       constraints={"multi_document": True})
    state.evidence = [{"source_path": "one.md"}]
    state.observations.extend([{"action": "context_search"}, {"action": "evidence_deduplicate"}])
    state.coverage = {"coverage_rate": 0.5}
    decision = AgentPlanner().choose(state)
    assert decision["action"] == "context_search"
    assert decision["retry"] is True


def test_evaluator_quality_gate():
    state = AgentState(task_id="t", goal="", intent="research", draft="# x", quality={"passed": True})
    result = AgentEvaluator().evaluate(state)
    assert result["done"] is True


def test_planner_supports_explicit_mcp_action():
    state = AgentState(task_id="t", goal="调用 MCP", intent="generic",
                       constraints={"mcp_request": {"server": "demo", "tool": "ping"}})
    assert AgentPlanner().choose(state)["action"] == "mcp_tool_call"


def test_query_cache_isolated_by_knowledge_base_selection():
    from app.rag.service import QueryCache

    cache = QueryCache(ttl=60, max_size=10)
    cache.put("问题", "hybrid", "", 5, {"answer": "A"}, ["kb-a"], False, corpus_version=3)
    assert cache.get("问题", "hybrid", "", 5, ["kb-a"], False, corpus_version=3)["answer"] == "A"
    assert cache.get("问题", "hybrid", "", 5, ["kb-b"], False, corpus_version=3) is None
    assert cache.get("问题", "hybrid", "", 5, ["kb-a"], False, corpus_version=4) is None


@pytest.mark.asyncio
async def test_mcp_rejects_unknown_tool_without_call():
    from app.agent.runtime import MCPRegistry
    registry = MCPRegistry()
    registry.register_server("demo", "http://127.0.0.1:9", ["known"])
    with pytest.raises(ValueError, match="未发现工具"):
        await registry.ensure_tool("demo", "missing")


@pytest.mark.asyncio
async def test_mcp_schema_rejects_invalid_arguments():
    from app.agent.runtime import MCPRegistry
    registry = MCPRegistry()
    registry.register_server("demo", "http://127.0.0.1:9", ["known"])
    registry._servers["demo"]["tool_specs"] = [{"name": "known", "inputSchema": {
        "type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}
    }}]
    registry._servers["demo"]["discovered_at"] = 9999999999
    with pytest.raises(ValueError, match="参数校验失败"):
        await registry.call("demo", "known", {})


@pytest.mark.asyncio
async def test_pending_agent_task_is_resumed_once(monkeypatch):
    """服务重启时应重新排队任务，且同一 task_id 不应重复启动。"""
    import app.api.routes as routes

    class FakeTracker:
        def __init__(self):
            self.rows = [{
                "task_id": "agent-restart-1",
                "task_type": "agent_rag",
                "status": "processing",
                "result": {"_payload": {"goal": "总结文章", "template": "research"}},
            }]
            self.updates = []

        def list_tasks(self, task_type=None, status=None):
            return list(self.rows)

        def update_task(self, task_id, **kwargs):
            self.updates.append((task_id, kwargs))

    tracker = FakeTracker()
    started = []

    async def fake_run(task_id, payload):
        started.append((task_id, payload.goal))

    monkeypatch.setattr(routes, "task_tracker", tracker)
    monkeypatch.setattr(routes, "_run_agent_task", fake_run)
    routes._agent_resume_tasks.clear()
    routes._agent_resume_task_ids.clear()

    assert await routes.resume_pending_agent_tasks() == 1
    # A second lifecycle invocation must not enqueue the same task again.
    assert await routes.resume_pending_agent_tasks() == 0
    await asyncio.sleep(0)
    assert started == [("agent-restart-1", "总结文章")]
    assert any(update[1].get("message") == "服务已恢复，Agent 任务重新排队" for update in tracker.updates)


@pytest.mark.asyncio
async def test_pending_agent_task_without_payload_is_failed(monkeypatch):
    import app.api.routes as routes

    class FakeTracker:
        def list_tasks(self, task_type=None, status=None):
            return [{"task_id": "agent-invalid", "task_type": "agent_rag", "status": "pending", "result": {}}]

        def update_task(self, task_id, **kwargs):
            self.updated = (task_id, kwargs)

    tracker = FakeTracker()
    monkeypatch.setattr(routes, "task_tracker", tracker)
    routes._agent_resume_tasks.clear()
    routes._agent_resume_task_ids.clear()

    assert await routes.resume_pending_agent_tasks() == 0
    assert tracker.updated[1]["status"].value == "failed"
    assert tracker.updated[1]["error"] == "missing_persisted_payload"
