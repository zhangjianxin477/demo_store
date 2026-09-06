"""结构化日志与监控模块 - O13 优化。

使用 structlog 实现结构化日志输出，支持 JSON 格式和 Prometheus 指标暴露。
"""

import time
import logging
import threading
from typing import Dict, Any, Optional
from collections import defaultdict
from contextlib import contextmanager

try:
    import structlog
    _structlog_available = True
except ImportError:
    _structlog_available = False

logger = logging.getLogger(__name__)

# ============ Prometheus 风格指标收集 ============

class MetricsCollector:
    """轻量级 Prometheus 风格指标收集器。

    支持 Counter、Gauge、Histogram 三种指标类型，
    提供 /metrics 端点格式的输出。
    """

    def __init__(self):
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, list] = defaultdict(list)
        self._lock = threading.RLock()
        self._labels: Dict[str, Dict[str, str]] = {}

    def inc_counter(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        """递增计数器。"""
        with self._lock:
            key = self._make_key(name, labels)
            self._counters[key] += value
            if labels and key not in self._labels:
                self._labels[key] = labels

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """设置仪表盘值。"""
        with self._lock:
            key = self._make_key(name, labels)
            self._gauges[key] = value
            if labels and key not in self._labels:
                self._labels[key] = labels

    def observe_histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """记录直方图观测值。"""
        with self._lock:
            key = self._make_key(name, labels)
            self._histograms[key].append(value)
            if labels and key not in self._labels:
                self._labels[key] = labels

    @contextmanager
    def time_histogram(self, name: str, labels: Optional[Dict[str, str]] = None):
        """计时上下文管理器，自动记录耗时到直方图。"""
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            self.observe_histogram(name, duration, labels)

    def _make_key(self, name: str, labels: Optional[Dict[str, str]] = None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def get_metrics_text(self) -> str:
        """生成 Prometheus 文本格式指标输出。"""
        lines = []
        with self._lock:
            for key, value in sorted(self._counters.items()):
                base_name = key.split("{")[0] if "{" in key else key
                lines.append(f"# HELP {base_name} Counter")
                lines.append(f"# TYPE {base_name} counter")
                lines.append(f"{key} {value}")

            for key, value in sorted(self._gauges.items()):
                base_name = key.split("{")[0] if "{" in key else key
                lines.append(f"# HELP {base_name} Gauge")
                lines.append(f"# TYPE {base_name} gauge")
                lines.append(f"{key} {value}")

            for key, values in sorted(self._histograms.items()):
                base_name = key.split("{")[0] if "{" in key else key
                if not values:
                    continue
                lines.append(f"# HELP {base_name} Histogram")
                lines.append(f"# TYPE {base_name} histogram")
                sorted_vals = sorted(values)
                count = len(sorted_vals)
                total = sum(sorted_vals)
                # 分位数
                for q in [0.5, 0.9, 0.95, 0.99]:
                    idx = min(int(count * q), count - 1)
                    lines.append(f'{key}_bucket{{le="{q}"}} {sorted_vals[idx]:.6f}')
                lines.append(f"{key}_count {count}")
                lines.append(f"{key}_sum {total:.6f}")

        return "\n".join(lines) + "\n"

    def get_stats(self) -> Dict[str, Any]:
        """获取指标统计摘要。"""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histogram_counts": {k: len(v) for k, v in self._histograms.items()},
            }


# 全局指标收集器
metrics = MetricsCollector()

# ============ structlog 配置 ============

def setup_structlog():
    """配置 structlog 结构化日志。

    如果 structlog 可用，配置 JSON 输出格式；
    否则使用标准 logging。
    """
    if not _structlog_available:
        logger.info("structlog 不可用，使用标准 logging")
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logger.info("structlog 已配置为 JSON 格式输出")


def get_structlog(name: str = __name__):
    """获取 structlog 实例，不可用时回退到标准 logging。"""
    if _structlog_available:
        return structlog.get_logger(name)
    return logging.getLogger(name)


# ============ 请求监控中间件 ============

class RequestMetricsMiddleware:
    """FastAPI 中间件：记录请求耗时和状态码到 Prometheus 指标。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.time()
        path = scope.get("path", "")
        method = scope.get("method", "")

        status_code = 200

        async def send_with_metrics(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        finally:
            duration = time.time() - start_time
            metrics.inc_counter(
                "http_requests_total",
                labels={"method": method, "path": path, "status": str(status_code)}
            )
            metrics.observe_histogram(
                "http_request_duration_seconds",
                duration,
                labels={"method": method, "path": path}
            )
