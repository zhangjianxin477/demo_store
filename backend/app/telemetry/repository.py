from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

from .recorder import telemetry


def _range_clause(start: str, end: str) -> tuple[str, list[str]]:
    clauses, params = [], []
    if start:
        clauses.append("occurred_at >= ?"); params.append(start)
    if end:
        clauses.append("occurred_at <= ?"); params.append(end)
    return ((" WHERE " + " AND ".join(clauses)) if clauses else ""), params


def overview(start: str = "", end: str = "", mode: str = "", intent: str = "") -> Dict[str, Any]:
    where, params = _range_clause(start, end)
    with telemetry._lock, telemetry._connect() as conn:
        # Do not cap this query at an arbitrary number of events.  A fixed
        # LIMIT made the dashboard silently report a partial history once the
        # event table grew beyond 10,000 rows.
        rows = conn.execute(
            f"SELECT event_id, event_name, occurred_at, task_id, properties_json "
            f"FROM telemetry_events{where} ORDER BY occurred_at DESC",
            params,
        ).fetchall()
    filtered_rows = []
    for row in rows:
        try: props = json.loads(row["properties_json"] or "{}")
        except Exception: props = {}
        row_mode = str(props.get("mode") or props.get("route") or "agent")
        row_intent = str(props.get("intent") or "")
        if mode and row_mode != mode: continue
        if intent and row_intent != intent: continue
        filtered_rows.append({
            "event_id": row["event_id"],
            "event_name": row["event_name"],
            "occurred_at": row["occurred_at"],
            "task_id": str(row["task_id"] or ""),
            "properties": props,
        })
    by_event: Dict[str, int] = {}
    for row in filtered_rows:
        name = row["event_name"]
        by_event[name] = by_event.get(name, 0) + 1

    # A task may emit started/replanned/citation events multiple times and can
    # be resumed after a restart.  KPI counts therefore use distinct task IDs,
    # with an event fallback only for legacy events that have no task ID.
    def distinct_task_count(event_name: str) -> int:
        task_ids = {row["task_id"] for row in filtered_rows
                    if row["event_name"] == event_name and row["task_id"]}
        anonymous = sum(1 for row in filtered_rows
                        if row["event_name"] == event_name and not row["task_id"])
        return len(task_ids) + anonymous

    tasks = {name: distinct_task_count(name)
             for name in ("agent_task_completed", "agent_task_failed", "agent_task_created")}
    intents: Dict[str, int] = {}
    modes: Dict[str, int] = {}
    intent_seen: set[str] = set()
    mode_seen: set[str] = set()
    for row in filtered_rows:
        event_name, props = row["event_name"], row["properties"]
        if event_name == "agent_perception_completed":
            value = str(props.get("intent") or "unknown")
            task_key = row["task_id"]
            if task_key:
                # Perception can be emitted again after a retry/restart.
                if task_key in intent_seen:
                    continue
                intent_seen.add(task_key)
            else:
                pass
            intents[value] = intents.get(value, 0) + 1
        if event_name in {"query_submitted", "agent_task_created"}:
            value = str(props.get("mode") or props.get("route") or "agent")
            task_key = row["task_id"]
            if task_key:
                mode_key = f"{task_key}:{value}"
                if mode_key in mode_seen:
                    continue
                mode_seen.add(mode_key)
            else:
                pass
            modes[value] = modes.get(value, 0) + 1
    completed = tasks.get("agent_task_completed", 0)
    failed = tasks.get("agent_task_failed", 0)
    created = tasks.get("agent_task_created", 0)
    terminal_tasks = completed + failed
    # Keep only the latest citation verification for each task.  Counting all
    # retries would make the pass rate depend on how many times the planner
    # retried rather than on the final quality result.
    latest_citations: Dict[str, Dict[str, Any]] = {}
    anonymous_citations: list[Dict[str, Any]] = []
    for row in filtered_rows:
        if row["event_name"] != "citation_verified":
            continue
        task_id = row["task_id"]
        if task_id and task_id not in latest_citations:
            latest_citations[task_id] = row
        elif not task_id:
            anonymous_citations.append(row)
    citation_rows = list(latest_citations.values()) + anonymous_citations
    quality_events = len(citation_rows)
    quality_passed = sum(int(bool(row["properties"].get("passed"))) for row in citation_rows)

    # Delivery events represent products actually handed off.  De-duplicate
    # retries for a task while retaining anonymous legacy events.
    delivery_names = {"artifact_exported", "email_send_completed", "email_sent", "wechat_draft_synced"}
    deliveries = set()
    for row in filtered_rows:
        if row["event_name"] not in delivery_names:
            continue
        deliveries.add((row["event_name"], row["task_id"] or row["event_id"]))
    freshness_seconds = None
    if filtered_rows:
        try:
            newest = datetime.fromisoformat(str(filtered_rows[0]["occurred_at"]).replace("Z", "+00:00"))
            freshness_seconds = max(0, int((datetime.now(timezone.utc) - newest).total_seconds()))
        except (TypeError, ValueError):
            freshness_seconds = None
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {"start": start, "end": end, "mode": mode, "intent": intent},
        "data_freshness_seconds": freshness_seconds,
        "total_events": len(filtered_rows),
        "kpi": {
            "agent_tasks_created": created,
            "agent_tasks_completed": completed,
            "agent_tasks_failed": failed,
            # None means there is no denominator yet; the UI renders this as
            # “—” instead of presenting a misleading 0%.
            "task_success_rate": round(completed / terminal_tasks, 4) if terminal_tasks else None,
            "citation_pass_rate": round(quality_passed / quality_events, 4) if quality_events else None,
            "effective_delivery_count": len(deliveries),
            "task_terminal_count": terminal_tasks,
            "citation_verified_count": quality_events,
        },
        "events_by_name": by_event,
        "intents": intents,
        "modes": modes,
    }
