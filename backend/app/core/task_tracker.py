"""异步任务追踪模块 - 文档上传异步化支持。

使用 FastAPI BackgroundTasks 实现异步文档处理，
上传接口立即返回 task_id，客户端可轮询进度。
"""

import time
import logging
import threading
import json
import os
from typing import Dict, Optional, Any
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
_UNSET = object()


class TaskStatus(str, Enum):
    """任务状态枚举。"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """异步任务信息。"""
    task_id: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class TaskTracker:
    """异步任务追踪器，管理所有后台任务的状态。"""

    def __init__(self, max_tasks: int = 1000, ttl_seconds: int = 3600, storage_path: Optional[str] = None):
        self._tasks: Dict[str, TaskInfo] = {}
        self._lock = threading.RLock()
        self._max_tasks = max_tasks
        self._ttl = ttl_seconds
        # Persist Agent task metadata so artifacts and in-flight task payloads
        # remain discoverable after a process restart. The API layer decides
        # whether pending/processing tasks are safe to resume.
        self._storage_path = storage_path
        if self._storage_path:
            self._load()

    def _load(self) -> None:
        try:
            with open(self._storage_path, "r", encoding="utf-8") as handle:
                rows = json.load(handle)
            now = time.time()
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or row.get("task_type") != "agent_rag":
                    continue
                if not isinstance(row, dict) or now - float(row.get("updated_at", now)) > self._ttl:
                    continue
                try:
                    self._tasks[row["task_id"]] = TaskInfo(
                        task_id=str(row["task_id"]),
                        task_type=str(row.get("task_type") or "document_upload"),
                        status=TaskStatus(row.get("status", TaskStatus.PENDING.value)),
                        progress=float(row.get("progress", 0)),
                        message=str(row.get("message") or ""),
                        created_at=float(row.get("created_at", now)),
                        updated_at=float(row.get("updated_at", now)),
                        result=row.get("result"),
                        error=row.get("error"),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning("加载任务状态失败: %s", exc)

    def _persist_locked(self) -> None:
        if not self._storage_path:
            return
        try:
            parent = os.path.dirname(self._storage_path)
            os.makedirs(parent, exist_ok=True)
            # Agent artifacts are the only tasks that need cross-restart
            # discovery. Upload/index jobs keep the historical in-memory
            # behavior and are intentionally excluded from this small file.
            rows = [task.to_dict() for task in self._tasks.values() if task.task_type == "agent_rag"]
            temporary = self._storage_path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(rows, handle, ensure_ascii=False, default=str)
            os.replace(temporary, self._storage_path)
        except Exception as exc:
            logger.warning("保存任务状态失败: %s", exc)

    def create_task(self, task_id: str, task_type: str = "document_upload") -> TaskInfo:
        """创建新任务。

        Args:
            task_id: 任务唯一标识
            task_type: 任务类型

        Returns:
            TaskInfo 实例
        """
        with self._lock:
            self._cleanup_expired()
            task = TaskInfo(task_id=task_id, task_type=task_type)
            self._tasks[task_id] = task
            self._persist_locked()
            logger.info(f"创建异步任务: {task_id} (type={task_type})")
            return task

    def update_task(self, task_id: str, status: Optional[TaskStatus] = None,
                    progress: Optional[float] = None, message: Optional[str] = None,
                    result: Optional[Any] = None, error: Any = _UNSET) -> Optional[TaskInfo]:
        """更新任务状态。

        Args:
            task_id: 任务 ID
            status: 新状态
            progress: 进度百分比 (0-100)
            message: 状态消息
            result: 任务结果
            error: 错误信息

        Returns:
            更新后的 TaskInfo，任务不存在返回 None
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if status is not None:
                task.status = status
            if progress is not None:
                task.progress = min(max(progress, 0), 100)
            if message is not None:
                task.message = message
            if result is not None:
                task.result = result
            # ``None`` is meaningful here: callers such as retry need to
            # clear a previous failure.  Omitted error keeps the old value.
            if error is not _UNSET:
                task.error = error
            task.updated_at = time.time()
            self._persist_locked()
            return task

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        """获取任务信息。"""
        with self._lock:
            return self._tasks.get(task_id)

    def delete_task(self, task_id: str) -> bool:
        """删除任务。"""
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._persist_locked()
                return True
            return False

    def cancel_task(self, task_id: str) -> Optional[TaskInfo]:
        """标记任务取消；后台协程在下一工具边界停止。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                return task
            task.status = TaskStatus.CANCELLED
            task.message = "任务已取消"
            task.updated_at = time.time()
            self._persist_locked()
            return task

    def list_tasks(self, task_type: Optional[str] = None,
                   status: Optional[TaskStatus] = None) -> list:
        """列出任务。"""
        with self._lock:
            tasks = list(self._tasks.values())
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return [t.to_dict() for t in sorted(tasks, key=lambda t: t.created_at, reverse=True)]

    def _cleanup_expired(self):
        """清理过期任务。"""
        now = time.time()
        expired = [
            tid for tid, task in self._tasks.items()
            if now - task.updated_at > self._ttl
        ]
        for tid in expired:
            del self._tasks[tid]
        if expired:
            logger.info(f"清理 {len(expired)} 个过期任务")


# 全局任务追踪器启用持久化；单元测试和临时调用的 TaskTracker 默认保持
# 内存模式，避免相互污染。
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
task_tracker = TaskTracker(storage_path=os.path.join(_project_root, "data", "task_tracker.json"))
