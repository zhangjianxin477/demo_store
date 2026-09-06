from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_context: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar("telemetry_context", default={})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_properties(value: Any) -> Any:
    """Remove credentials and bound payload size before persistence."""
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"query", "goal", "prompt", "text", "content", "body", "raw", "input", "request"}:
                raw = str(item or "")
                output[str(key)] = {"length": len(raw), "sha256": hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]}
                continue
            if any(token in lowered for token in ("api_key", "apikey", "token", "password", "secret", "authorization", "content_html")):
                output[str(key)] = "[REDACTED]"
            else:
                output[str(key)] = _safe_properties(item)
        return output
    if isinstance(value, (list, tuple)):
        return [_safe_properties(item) for item in value[:50]]
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [REDACTED]", value, flags=re.I)
        return value[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


class TelemetryRecorder:
    def __init__(self, db_path: Optional[str] = None) -> None:
        default_path = os.path.join(settings.DATA_DIR, "telemetry", "telemetry.db")
        self.db_path = db_path or default_path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    event_id TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    session_id TEXT,
                    task_id TEXT,
                    run_id TEXT,
                    trace_id TEXT,
                    user_id_hash TEXT,
                    source TEXT NOT NULL,
                    app_version TEXT,
                    properties_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_time ON telemetry_events(occurred_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_name_time ON telemetry_events(event_name, occurred_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_task ON telemetry_events(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_session ON telemetry_events(session_id)")
            conn.commit()

    @contextmanager
    def context(self, **values: str) -> Iterator[None]:
        current = dict(_context.get())
        current.update({k: str(v) for k, v in values.items() if v is not None and str(v)})
        token = _context.set(current)
        try:
            yield
        finally:
            _context.reset(token)

    def record(self, event_name: str, *, source: str = "backend", properties: Optional[Dict[str, Any]] = None,
               session_id: str = "", task_id: str = "", run_id: str = "", trace_id: str = "",
               user_id_hash: str = "") -> Optional[str]:
        """Best-effort event write. Telemetry failures never break the main task."""
        try:
            ctx = _context.get()
            event_id = str(uuid.uuid4())
            occurred = _now()
            payload = _safe_properties(properties or {})
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO telemetry_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, str(event_name)[:120], occurred, session_id or ctx.get("session_id", ""),
                     task_id or ctx.get("task_id", ""), run_id or ctx.get("run_id", ""),
                     trace_id or ctx.get("trace_id", ""), user_id_hash or "", source[:40],
                     str(getattr(settings, "APP_VERSION", ""))[:80], json.dumps(payload, ensure_ascii=False), occurred),
                )
                conn.commit()
            return event_id
        except Exception:
            logger.warning("telemetry write failed event=%s", event_name, exc_info=True)
            return None

    def query_events(self, *, start: str = "", end: str = "", event_name: str = "", task_id: str = "", limit: int = 100) -> list[dict]:
        clauses, params = [], []
        if start:
            clauses.append("occurred_at >= ?"); params.append(start)
        if end:
            clauses.append("occurred_at <= ?"); params.append(end)
        if event_name:
            clauses.append("event_name = ?"); params.append(event_name)
        if task_id:
            clauses.append("task_id = ?"); params.append(task_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM telemetry_events{where} ORDER BY occurred_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            try: item["properties"] = json.loads(item.pop("properties_json") or "{}")
            except Exception: item["properties"] = {}
            output.append(item)
        return output


telemetry = TelemetryRecorder()
telemetry_context = telemetry.context
