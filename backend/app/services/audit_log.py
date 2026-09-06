import os
import json
import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class AuditLogService:
    def __init__(self):
        self._log_dir = os.path.join(settings.DATA_DIR, "audit_logs")
        os.makedirs(self._log_dir, exist_ok=True)
        self._current_log_file = os.path.join(
            self._log_dir, f"audit_{datetime.now().strftime('%Y%m')}.json"
        )
        self._logs: List[Dict] = []
        self._max_memory_logs = 500
        self._load_recent()

    def _load_recent(self):
        if os.path.exists(self._current_log_file):
            try:
                with open(self._current_log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._logs = data.get("logs", [])[-self._max_memory_logs:]
            except Exception as e:
                logger.error(f"审计日志加载失败: {e}")

    def _save(self):
        data = {"logs": self._logs[-self._max_memory_logs:]}
        with open(self._current_log_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def log(self, action: str, resource_type: str = "", resource_id: str = "",
            user_id: str = "system", details: Optional[Dict] = None,
            ip_address: str = "") -> Dict:
        entry = {
            "log_id": str(uuid.uuid4())[:12],
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "details": details or {},
            "ip_address": ip_address,
            "timestamp": datetime.now().isoformat(),
        }
        self._logs.append(entry)
        if len(self._logs) % 10 == 0:
            self._save()
        return entry

    def query(self, action: Optional[str] = None,
              resource_type: Optional[str] = None,
              resource_id: Optional[str] = None,
              user_id: Optional[str] = None,
              start_time: Optional[str] = None,
              end_time: Optional[str] = None,
              page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        results = self._logs

        if action:
            results = [l for l in results if l.get("action") == action]
        if resource_type:
            results = [l for l in results if l.get("resource_type") == resource_type]
        if resource_id:
            results = [l for l in results if l.get("resource_id") == resource_id]
        if user_id:
            results = [l for l in results if l.get("user_id") == user_id]
        if start_time:
            results = [l for l in results if l.get("timestamp", "") >= start_time]
        if end_time:
            results = [l for l in results if l.get("timestamp", "") <= end_time]

        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        page_results = results[start:end]

        return {
            "success": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "logs": page_results,
        }

    def get_stats(self) -> Dict[str, Any]:
        action_counts = {}
        for log in self._logs:
            action = log.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = sum(1 for l in self._logs if l.get("timestamp", "").startswith(today))

        return {
            "total_logs": len(self._logs),
            "today_count": today_count,
            "action_counts": action_counts,
        }

    def flush(self):
        self._save()


audit_log_service = AuditLogService()
