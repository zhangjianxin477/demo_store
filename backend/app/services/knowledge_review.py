"""Deterministic knowledge-review suggestions backed by local source files.

This deliberately does not ask an LLM to invent questions.  Every suggestion
links to one file, so users can review it or turn it into an Agent research
task while keeping an auditable source boundary.
"""
from __future__ import annotations

import re
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from app.core.config import settings


class KnowledgeReviewService:
    def __init__(self) -> None:
        self.base = Path(settings.DATA_DIR, "file_resources")
        self.state_path = Path(settings.DATA_DIR, "knowledge_review_state.json")
        self.preferences_path = Path(settings.DATA_DIR, "knowledge_review_preferences.json")
        self._lock = threading.RLock()
        self._handled: Dict[str, Dict[str, Any]] = {}
        self._preferences: Dict[str, Any] = {"enabled": True, "topics": [], "frequency": "daily", "max_items": 3}
        self._load_state()
        self._load_preferences()

    def _load_state(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._handled = data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            self._handled = {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._handled, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _load_preferences(self) -> None:
        try:
            value = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if isinstance(value, dict): self._preferences.update(value)
        except (OSError, json.JSONDecodeError):
            pass

    def get_preferences(self) -> Dict[str, Any]:
        with self._lock: return dict(self._preferences)

    def update_preferences(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if "enabled" in patch: self._preferences["enabled"] = bool(patch["enabled"])
            if "frequency" in patch and str(patch["frequency"]) in {"daily", "weekdays", "weekly"}: self._preferences["frequency"] = str(patch["frequency"])
            if "max_items" in patch: self._preferences["max_items"] = max(1, min(int(patch["max_items"]), 10))
            if "topics" in patch and isinstance(patch["topics"], list): self._preferences["topics"] = [str(x).strip() for x in patch["topics"] if str(x).strip()][:30]
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.preferences_path.with_suffix(".tmp"); tmp.write_text(json.dumps(self._preferences, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(self.preferences_path)
            return dict(self._preferences)

    @staticmethod
    def _title(path: Path, content: str) -> str:
        match = re.search(r"^#\s+(.+?)\s*$", content or "", flags=re.M)
        return (match.group(1).strip() if match else path.stem).replace("_", " ")[:100]

    @staticmethod
    def _focus(content: str) -> str:
        headings = re.findall(r"^#{2,3}\s+(.+?)\s*$", content or "", flags=re.M)
        return (headings[0].strip() if headings else "核心观点").replace("_", " ")[:80]

    @staticmethod
    def _used_paths(tasks: Iterable[Dict[str, Any]]) -> Set[str]:
        used: Set[str] = set()
        for task in tasks:
            result = task.get("result") or {}
            for item in result.get("evidence") or []:
                path = str(item.get("source_path") or "").replace("\\", "/").strip("/")
                if path:
                    used.add(path)
        return used

    def mark_handled(self, source_path: str, action: str = "reviewed") -> Dict[str, Any]:
        normalized = str(source_path or "").replace("\\", "/").strip("/")
        if not normalized:
            raise ValueError("source_path 不能为空")
        with self._lock:
            self._handled[normalized] = {"action": action[:40], "handled_at": datetime.now().isoformat()}
            self._save_state()
            return {"source_path": normalized, **self._handled[normalized]}

    def daily(self, tasks: Iterable[Dict[str, Any]], limit: int = 3, include_handled: bool = False) -> Dict[str, Any]:
        self.base.mkdir(parents=True, exist_ok=True)
        if not self._preferences.get("enabled", True):
            return {"questions": [], "total_source_files": 0, "unreused_source_files": 0,
                    "unhandled_source_files": 0, "disabled": True, "preferences": self.get_preferences(),
                    "generated_at": datetime.now().isoformat()}
        used = self._used_paths(tasks)
        rows: List[Dict[str, Any]] = []
        for path in self.base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")[:12000]
                relative = path.relative_to(self.base).as_posix()
                rows.append({"path": relative, "title": self._title(path, content),
                             "focus": self._focus(content), "used": relative in used,
                             "handled": relative in self._handled,
                             "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()})
            except OSError:
                continue
        # Prefer material that has not contributed to a completed Agent task;
        # then use recent files to keep the daily review actionable.
        topics = [str(x).lower() for x in self._preferences.get("topics", []) if str(x).strip()]
        if topics:
            topical = [item for item in rows if any(term in f"{item['title']} {item['focus']}".lower() for term in topics)]
            rows = topical or rows
        available = rows if include_handled else [item for item in rows if not item["handled"]]
        # If everything has been handled, return the most recent items but
        # mark them explicitly; this keeps the panel useful without repeating
        # an unacknowledged reminder.
        if not available:
            available = rows
        available.sort(key=lambda item: (item["handled"], item["used"], item["updated_at"]), reverse=False)
        configured_limit = int(self._preferences.get("max_items", 3) or 3)
        selected = available[: max(1, min(int(limit or configured_limit), configured_limit, 10))]
        questions = [{
            "source_path": item["path"], "title": item["title"], "updated_at": item["updated_at"], "handled": item["handled"],
            "question": f"回顾《{item['title']}》：它关于“{item['focus']}”的核心观点、依据和可执行启示分别是什么？",
            "suggested_goal": f"基于文件 {item['path']}，整理其核心观点、依据、不确定性和一个可执行行动项。",
        } for item in selected]
        return {"questions": questions, "total_source_files": len(rows),
                "unreused_source_files": sum(1 for item in rows if not item["used"]),
                "unhandled_source_files": sum(1 for item in rows if not item["handled"]),
                "preferences": self.get_preferences(),
                "generated_at": datetime.now().isoformat()}


knowledge_review_service = KnowledgeReviewService()
