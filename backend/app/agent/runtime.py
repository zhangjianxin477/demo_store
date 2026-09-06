"""轻量 Agent Runtime。

这里实现的是可审计、可限制的工具编排层，不模拟或复刻 Codex 的内部实现。
Tool、Skill、MCP 均使用统一注册协议，后续可以替换为真正的外部服务。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.telemetry.recorder import telemetry
from app.core.config import settings
import httpx
try:
    from jsonschema import validate as validate_jsonschema
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
except Exception:  # pragma: no cover - optional dependency fallback
    validate_jsonschema = None
    JsonSchemaValidationError = ValueError

logger = logging.getLogger(__name__)


ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class AgentEvent:
    phase: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolHandler] = {}
        self._specs: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, handler: ToolHandler, spec: Optional[Dict[str, Any]] = None) -> None:
        self._tools[name] = handler
        self._specs[name] = dict(spec or {"name": name, "risk_level": "low"})

    async def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        handler = self._tools.get(name)
        if not handler:
            raise ValueError(f"未注册工具: {name}")
        started = __import__("time").monotonic()
        telemetry.record("tool_call_started", source="agent", properties={"tool_name": name})
        try:
            result = await handler(args)
            telemetry.record("tool_call_completed", source="agent", properties={
                "tool_name": name,
                "duration_ms": round((__import__("time").monotonic() - started) * 1000, 1),
                "result_count": len(result.get("evidence", [])) if isinstance(result, dict) else 0,
            })
            return result
        except Exception as exc:
            telemetry.record("tool_call_failed", source="agent", properties={
                "tool_name": name,
                "duration_ms": round((__import__("time").monotonic() - started) * 1000, 1),
                "error_type": type(exc).__name__,
            })
            raise

    def list_tools(self) -> List[str]:
        return sorted(self._tools)

    def list_specs(self) -> List[Dict[str, Any]]:
        return [dict(self._specs[name], name=name) for name in sorted(self._tools)]


class SkillRegistry:
    """Skill 是面向业务目标的可复用策略，不直接执行外部副作用。"""

    def __init__(self) -> None:
        self._skills = {
            "research": {"label": "主题研究", "steps": ["retrieve", "synthesize", "verify"]},
            "hotspot": {"label": "热点汇总", "steps": ["retrieve_recent", "cluster", "synthesize", "verify"]},
            "wechat": {"label": "公众号文章", "steps": ["retrieve", "outline", "synthesize", "verify"]},
            "xiaohongshu": {"label": "小红书笔记", "steps": ["retrieve", "extract_points", "synthesize", "verify"]},
            "weekly": {"label": "周报", "steps": ["retrieve_recent", "synthesize", "verify"]},
        }

    def get(self, name: str) -> Dict[str, Any]:
        return self._skills.get(name, self._skills["research"])

    def list_skills(self) -> List[Dict[str, Any]]:
        return [{"name": k, **v} for k, v in self._skills.items()]


class MCPRegistry:
    """MCP 兼容入口。

    当前只登记本地安全工具，不主动连接未知 MCP Server；后续接入时可将
    server tool 映射为 ToolRegistry handler，并在配置中做 allowlist。
    """

    def __init__(self) -> None:
        self._servers: Dict[str, Dict[str, Any]] = {}

    def register_server(self, name: str, endpoint: str, tools: Optional[List[str]] = None,
                       headers: Optional[Dict[str, str]] = None, enabled: bool = True) -> None:
        self._servers[name] = {"name": name, "endpoint": endpoint, "tools": tools or [],
                               "headers": headers or {}, "enabled": bool(enabled), "discovered_at": 0.0,
                               "tool_specs": []}

    async def _rpc(self, server: Dict[str, Any], method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        endpoint = str(server.get("endpoint") or "").strip()
        if not endpoint:
            raise ValueError("MCP endpoint 为空")
        payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params or {}}
        headers = {"Content-Type": "application/json", **(server.get("headers") or {})}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        if data.get("error"):
            raise RuntimeError(str(data["error"].get("message") or data["error"]))
        return data.get("result") or {}

    async def discover(self, name: str) -> List[Dict[str, Any]]:
        server = self._servers.get(name)
        if not server or not server.get("enabled", True):
            return []
        result = await self._rpc(server, "tools/list")
        tools = result.get("tools") or []
        server["tools"] = [item.get("name") for item in tools if isinstance(item, dict) and item.get("name")]
        server["tool_specs"] = [item for item in tools if isinstance(item, dict)]
        server["discovered_at"] = time.time()
        telemetry.record("mcp_tool_discovered", source="mcp", properties={"server_name": name, "tool_count": len(tools)})
        return tools

    async def ensure_tool(self, server_name: str, tool_name: str) -> Dict[str, Any]:
        server = self._servers.get(server_name)
        if not server or not server.get("enabled", True):
            raise ValueError(f"MCP Server 不存在或未启用: {server_name}")
        configured_names = {str(item) for item in (server.get("tools") or []) if item}
        # An explicit allowlist is authoritative and avoids a network call for
        # an obviously invalid tool name.
        if configured_names and tool_name not in configured_names:
            raise ValueError(f"MCP Server 未发现工具: {server_name}/{tool_name}")
        # Refresh discovery every five minutes, or whenever no schema exists.
        if not server.get("tool_specs") or time.time() - float(server.get("discovered_at") or 0) > 300:
            await self.discover(server_name)
            server = self._servers.get(server_name) or server
        spec = next((item for item in server.get("tool_specs", []) if item.get("name") == tool_name), None)
        if not spec:
            raise ValueError(f"MCP Server 未发现工具: {server_name}/{tool_name}")
        return spec

    async def call(self, server_name: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        spec = await self.ensure_tool(server_name, tool_name)
        args = arguments or {}
        schema = spec.get("inputSchema") or spec.get("input_schema")
        if schema and validate_jsonschema:
            try:
                validate_jsonschema(instance=args, schema=schema)
            except JsonSchemaValidationError as exc:
                telemetry.record("mcp_tool_validation_failed", source="mcp", properties={
                    "server_name": server_name, "tool_name": tool_name,
                    "error_type": type(exc).__name__,
                })
                raise ValueError(f"MCP 工具参数校验失败: {exc.message[:240]}")
        server = self._servers.get(server_name)
        result = await self._rpc(server, "tools/call", {"name": tool_name, "arguments": args})
        telemetry.record("mcp_tool_called", source="mcp", properties={"server_name": server_name, "tool_name": tool_name, "status": "completed"})
        return result

    def list_servers(self) -> List[Dict[str, Any]]:
        return [{"name": item.get("name"), "endpoint": item.get("endpoint"),
                 "tools": item.get("tools", []), "enabled": item.get("enabled", True)}
                for item in self._servers.values()]


class AgentRuntime:
    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self.skills = SkillRegistry()
        self.mcp = MCPRegistry()

    def describe(self) -> Dict[str, Any]:
        mounted_skills = []
        try:
            from app.passive.skill_registry import skill_registry
            mounted_skills = skill_registry.list()
        except Exception:
            mounted_skills = []
        return {
            "tools": self.tools.list_tools(),
            "tool_specs": self.tools.list_specs(),
            "skills": self.skills.list_skills(),
            "mounted_skills": mounted_skills,
            "mcp_servers": self.mcp.list_servers(),
            # These limits describe the bounded dynamic Agent Loop.
            "limits": {"max_steps": 8, "max_tool_calls": 12, "max_model_calls": 8,
                       "timeout_seconds": int(getattr(settings, "AGENT_TASK_TIMEOUT_SECONDS", 180) or 180)},
        }
