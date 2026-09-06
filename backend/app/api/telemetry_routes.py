from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.telemetry.recorder import telemetry
from app.telemetry.repository import overview

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


class TelemetryEventRequest(BaseModel):
    event_name: str = Field(min_length=1, max_length=120)
    source: str = "frontend"
    properties: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    task_id: str = ""
    run_id: str = ""
    trace_id: str = ""


@router.post("/events")
async def record_event(payload: TelemetryEventRequest):
    event_id = telemetry.record(payload.event_name, source=payload.source, properties=payload.properties,
                                 session_id=payload.session_id, task_id=payload.task_id,
                                 run_id=payload.run_id, trace_id=payload.trace_id)
    return {"success": bool(event_id), "event_id": event_id}


@router.get("/overview")
async def telemetry_overview(start: str = "", end: str = "", mode: str = "", intent: str = ""):
    return overview(start=start, end=end, mode=mode, intent=intent)


@router.get("/events")
async def telemetry_events(start: str = "", end: str = "", event_name: str = "", limit: int = 100):
    return {"events": telemetry.query_events(start=start, end=end, event_name=event_name, limit=limit)}
