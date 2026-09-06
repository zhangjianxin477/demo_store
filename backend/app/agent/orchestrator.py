"""Agent RAG 编排器。

Agent 的成功条件是“资料调取 -> 证据绑定 -> 写作 -> 引用核验 -> 产物”，
而不是在界面上显示若干固定步骤。所有生成工具都接收 evidence，避免无资料
时直接使用模型常识生成内容。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.agent.context import context_resolver, extract_resource_path, extract_resource_paths
from app.rag.media import collect_evidence_images
from app.agent.runtime import AgentRuntime
from app.core.config import settings
from app.core.task_tracker import TaskStatus, task_tracker
from app.rag.service import rag_service
from app.rag.conversation_memory import conversation_memory
from app.mcp import wechat_mcp
from app.telemetry.recorder import telemetry
from app.agent.state import AgentState
from app.agent.planner import planner
from app.agent.policy import policy
from app.agent.executor import AgentExecutor
from app.agent.evaluator import evaluator

logger = logging.getLogger(__name__)

TEMPLATE_LABELS = {
    "research": "主题研究报告",
    "hotspot": "热点汇总",
    "wechat": "微信公众号文章",
    "xiaohongshu": "小红书笔记",
    "weekly": "周报",
}

# Built-in intents are deliberately explicit.  The intent recognizer may use
# the model to understand a request, but execution is always routed through
# this allow-listed table; unknown requests use the bounded generic route.
INTENT_SPECS = {
    "chat": {
        "label": "即时问答",
        "description": "简单问候、能力询问或不需要知识库的直接回答",
        "template": "chat",
        "structure": "直接回答问题；不检索、不生成报告、不虚构资料。",
        "system_prompt": "你是 Knowledge Hub 的对话助手。简洁、直接回答，不要把简单问题改写成研究报告。",
    },
    "qa": {
        "label": "知识库问答",
        "description": "基于授权资料回答一个明确问题",
        "template": "qa",
        "structure": "先给直接答案，再给依据；资料不足时明确说明，不要猜测。",
        "system_prompt": "你是严格基于证据的知识库问答助手，只回答证据支持的内容。",
    },
    "summarize": {
        "label": "文章摘要",
        "description": "总结一篇文章或当前文件",
        "template": "research",
        "structure": "按结论、背景、关键要点、证据和不确定性组织，忠实压缩原文。",
        "system_prompt": "你是文章摘要 Agent。只压缩输入资料，不补充外部事实。",
    },
    "research": {
        "label": "主题研究报告",
        "description": "围绕主题进行资料检索、分析和报告写作",
        "template": "research",
        "structure": "先给结论，再按背景、关键发现、证据、分歧与不确定性、建议组织。",
        "system_prompt": "你是资料驱动的主题研究 Agent。严格区分事实、推断和建议。",
    },
    "compare": {
        "label": "对比分析",
        "description": "比较两个或多个对象、方案、产品或观点",
        "template": "research",
        "structure": "先给结论，再用 Markdown 表格比较维度，最后说明适用场景和证据边界。",
        "system_prompt": "你是对比分析 Agent。所有比较维度必须能在证据中找到依据，缺失项写‘资料未提供’。",
    },
    "fact_check": {
        "label": "事实核查",
        "description": "核验说法是否被知识库资料支持",
        "template": "research",
        "structure": "逐条列出待核查说法、判定（支持/部分支持/无法确认/冲突）、证据和理由。",
        "system_prompt": "你是事实核查 Agent。不能把相关内容当作证明，无法确认就明确标记。",
    },
    "timeline": {
        "label": "时间线梳理",
        "description": "整理事件、项目或产品的时间节点",
        "template": "research",
        "structure": "按时间顺序列出日期、事件、来源和不确定日期；禁止从采集时间推断事件日期。",
        "system_prompt": "你是时间线分析 Agent。日期必须来自原文，模糊日期保持模糊。",
    },
    "hotspot": {
        "label": "热点汇总",
        "description": "按时间范围或文件夹聚合多篇内容，提取热点主题",
        "template": "hotspot",
        "structure": "按热点主题分组，分别写摘要、关键事实、变化、影响、观点分歧和来源日期。",
        "system_prompt": "你是热点汇总 Agent。先聚类资料，再总结主题，不把单篇观点扩展成全网结论。",
    },
    "wechat": {
        "label": "微信公众号文章",
        "description": "将资料改写为公众号长文草稿",
        "template": "wechat",
        "structure": "输出标题、摘要、导语、分节正文、结尾；保留事实依据，避免夸大标题。",
        "system_prompt": "你是微信公众号内容 Agent。可读性优先，但不得突破证据边界。",
    },
    "xiaohongshu": {
        "label": "小红书笔记",
        "description": "将资料改写为短内容笔记",
        "template": "xiaohongshu",
        "structure": "输出标题、短段落、要点、话题标签和配图建议；事实和个人化表达分开。",
        "system_prompt": "你是小红书内容 Agent。表达轻量有吸引力，事实必须来自证据。",
    },
    "weekly": {
        "label": "周报",
        "description": "汇总一段时间内的资料变化",
        "template": "weekly",
        "structure": "按本期重点、资料变化、关键结论、风险和下一步行动组织。",
        "system_prompt": "你是周报 Agent。仅总结指定时间范围的资料，不能混入其他周期内容。",
    },
    "generic": {
        "label": "通用 Agent 任务",
        "description": "未匹配内置意图的开放式任务",
        "template": "research",
        "structure": "先说明对任务的理解，再给基于证据的结果、限制和下一步。",
        "system_prompt": "你是通用资料 Agent。先理解任务，再基于证据完成；不确定时提出限制，不要伪造能力或来源。",
    },
}

INTENT_ALIASES = {
    "summary": "summarize", "摘要": "summarize", "总结": "summarize",
    "qa": "qa", "问答": "qa", "提问": "qa",
    "report": "research", "研究": "research", "报告": "research",
    "对比": "compare", "比较": "compare", "竞品": "compare",
    "核查": "fact_check", "事实": "fact_check", "验证": "fact_check",
    "时间线": "timeline", "历程": "timeline",
    "热点": "hotspot", "热搜": "hotspot",
    "公众号": "wechat", "微信文章": "wechat",
    "小红书": "xiaohongshu", "笔记": "xiaohongshu",
    "周报": "weekly", "日报": "weekly", "月报": "weekly",
}


class AgentOrchestrator:
    """感知 -> 上下文 -> 推理 -> 行动 -> 反馈 -> 记忆的受控闭环。"""

    def __init__(self) -> None:
        self.runtime = AgentRuntime()
        self._load_mcp_config()
        self.runtime.mcp.register_server(
            "wechat_official_account",
            wechat_mcp.BASE_URL,
            ["wechat_account_status", "wechat_create_draft", "wechat_submit_publish", "wechat_publish_status"],
        )
        self._register_builtin_tools()

    def _load_mcp_config(self) -> None:
        raw = getattr(settings, "MCP_SERVERS_JSON", None)
        if not raw:
            return
        try:
            configs = json.loads(raw)
            if isinstance(configs, dict):
                configs = [configs]
            for item in configs or []:
                if item.get("name") and item.get("endpoint"):
                    self.runtime.mcp.register_server(item["name"], item["endpoint"], item.get("tools", []), item.get("headers", {}), item.get("enabled", True))
        except Exception as exc:
            logger.warning("MCP_SERVERS_JSON 配置无效: %s", exc)

    def _register_builtin_tools(self) -> None:
        async def context_search(args: Dict[str, Any]) -> Dict[str, Any]:
            return await context_resolver.resolve(
                args.get("goal", ""),
                kb_id=args.get("kb_id", ""),
                kb_ids=args.get("kb_ids"),
                scope=args.get("scope"),
                selected_paths=args.get("selected_paths"),
                top_k=min(max(int(args.get("top_k", 8)), 5), 12),
                date_range=args.get("date_range"),
                web_search_enabled=bool(args.get("web_search_enabled", False)),
            )

        async def rag_query(args: Dict[str, Any]) -> Dict[str, Any]:
            """兼容旧调用；新 Agent 统一走 context_search。"""
            return await context_resolver.resolve(
                args.get("goal", ""), kb_id=args.get("kb_id", ""), kb_ids=args.get("kb_ids"),
                scope=["knowledge_bases"], top_k=min(int(args.get("top_k", 8)), 12),
                web_search_enabled=bool(args.get("web_search_enabled", False)),
            )

        async def evidence_deduplicate(args: Dict[str, Any]) -> Dict[str, Any]:
            raw = args.get("evidence") or []
            seen = set()
            output = []
            # ContextResolver already applies ranking (or explicit file
            # selection order). Do not resort here: resorting made article
            # numbers unstable and made multi-document output look like only
            # the highest-scoring article was selected first.
            for item in raw:
                key = item.get("source_id") or re.sub(r"\s+", " ", item.get("content", "")).strip()[:160]
                if not key or key in seen:
                    continue
                seen.add(key)
                item = dict(item)
                item["evidence_index"] = len(output) + 1
                output.append(item)
            return {"evidence": output, "deduplicated_count": len(output)}

        async def evidence_cluster(args: Dict[str, Any]) -> Dict[str, Any]:
            groups: Dict[str, List[Dict[str, Any]]] = {}
            for item in args.get("evidence") or []:
                metadata = item.get("metadata") or {}
                key = metadata.get("updated_at") or item.get("title") or "未分类"
                groups.setdefault(str(key)[:40], []).append(item)
            return {"clusters": [{"label": key, "evidence": value} for key, value in groups.items()]}

        async def multi_document_coverage(args: Dict[str, Any]) -> Dict[str, Any]:
            expected = [str(item).replace("\\", "/") for item in (args.get("expected_source_paths") or []) if str(item).strip()]
            actual = {str(item.get("source_path") or "").replace("\\", "/") for item in (args.get("evidence") or [])}
            matched = [path for path in expected if path in actual]
            missing = [path for path in expected if path not in actual]
            return {"requested_count": len(expected), "read_count": len(matched), "missing": missing,
                    "coverage_rate": 1.0 if not expected else round(len(matched) / len(expected), 4)}

        async def draft_generate(args: Dict[str, Any]) -> Dict[str, Any]:
            evidence = args.get("evidence") or []
            draft = await self._generate_draft(
                args.get("goal", ""), args.get("template", "research"), evidence,
                args.get("clusters") or [],
                bool(args.get("single_document", False)),
                bool(args.get("multi_document_summary", False)),
                args.get("output_contract") or {},
            )
            return {"draft": draft, "evidence_count": len(evidence), "requires_evidence": True}

        async def citation_verify(args: Dict[str, Any]) -> Dict[str, Any]:
            return self._verify_draft(args.get("draft", ""), args.get("evidence") or [], args.get("expected_source_paths") or [])

        async def memory_compress(args: Dict[str, Any]) -> Dict[str, Any]:
            session_id = str(args.get("session_id") or "").strip()
            if not session_id:
                return {"compressed": False, "reason": "missing_session"}
            before = conversation_memory.get_summary(session_id)
            await rag_service._compress_session_if_needed(session_id)
            after = conversation_memory.get_summary(session_id)
            return {"compressed": bool(after and after != before), "has_summary": bool(after)}

        async def passive_skill_run(args: Dict[str, Any]) -> Dict[str, Any]:
            """调用已挂载的被动摄入 Skill；默认试跑，不自动发邮件。"""
            from app.passive.service import passive_service
            skill_id = str(args.get("skill_id") or "").strip()
            if not skill_id:
                matched = passive_service.registry.match_enabled(str(args.get("goal") or ""))
                skill_id = str((matched or {}).get("skill_id") or "")
            if not skill_id:
                return {"success": False, "error": "没有匹配的已挂载 Skill"}
            run = await passive_service.start_run(skill_id, dry_run=bool(args.get("dry_run", True)), force=bool(args.get("force", False)))
            return {"success": True, "skill_id": skill_id, "run": run}

        async def wechat_account_status(args: Dict[str, Any]) -> Dict[str, Any]:
            """检查公众号 MCP 配置和服务连通性，不返回任何凭据。"""
            return await wechat_mcp.account_status()

        async def wechat_create_draft(args: Dict[str, Any]) -> Dict[str, Any]:
            """将已审核前的 Agent 草稿写入公众号草稿箱。"""
            article = dict(args.get("article") or args)
            result = await wechat_mcp.create_draft(article)
            return {"success": True, "media_id": result.get("media_id"), "status": "draft", "requires_user_confirmation": True}

        async def wechat_submit_publish(args: Dict[str, Any]) -> Dict[str, Any]:
            """高风险发布动作，必须由显式审批接口调用，Agent 不可自行发布。"""
            if not bool(args.get("approved")):
                return {"success": False, "blocked": True, "requires_approval": True, "message": "公众号发布必须先完成人工审批"}
            media_id = str(args.get("media_id") or "")
            if not media_id:
                raise ValueError("缺少公众号 media_id")
            result = await wechat_mcp.submit_publish(media_id)
            return {"success": True, "status": "submitted", "publish_id": result.get("publish_id")}

        async def wechat_publish_status(args: Dict[str, Any]) -> Dict[str, Any]:
            publish_id = str(args.get("publish_id") or "")
            if not publish_id:
                raise ValueError("缺少公众号 publish_id")
            return await wechat_mcp.publish_status(publish_id)

        async def mcp_tool_call(args: Dict[str, Any]) -> Dict[str, Any]:
            server = str(args.get("server") or "").strip()
            tool = str(args.get("tool") or "").strip()
            if not server or not tool:
                raise ValueError("MCP 调用缺少 server 或 tool")
            arguments = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
            return await self.runtime.mcp.call(server, tool, arguments)

        self.runtime.tools.register("context_search", context_search)
        self.runtime.tools.register("rag_query", rag_query)
        self.runtime.tools.register("evidence_deduplicate", evidence_deduplicate)
        self.runtime.tools.register("evidence_cluster", evidence_cluster)
        self.runtime.tools.register("multi_document_coverage", multi_document_coverage)
        self.runtime.tools.register("draft_generate", draft_generate)
        self.runtime.tools.register("citation_verify", citation_verify)
        self.runtime.tools.register("memory_compress", memory_compress)
        self.runtime.tools.register("passive_skill_run", passive_skill_run)
        self.runtime.tools.register("wechat_account_status", wechat_account_status)
        self.runtime.tools.register("wechat_create_draft", wechat_create_draft)
        self.runtime.tools.register("wechat_submit_publish", wechat_submit_publish)
        self.runtime.tools.register("wechat_publish_status", wechat_publish_status)
        self.runtime.tools.register("mcp_tool_call", mcp_tool_call)

    def perceive(self, goal: str, payload: Any) -> Dict[str, Any]:
        text = re.sub(r"\s+", " ", goal.strip())
        explicit_scope = list(getattr(payload, "scope", None) or [])
        requested_paths = extract_resource_paths(text, context_resolver.base)
        requested_path = requested_paths[0] if requested_paths else ""
        requested_scope = explicit_scope
        if not requested_scope:
            # An explicit folder reference is a hard boundary.  Do not mix the
            # whole knowledge base into a folder-summary task unless the user
            # explicitly selects a scope in the request payload.
            requested_scope = ["file_resources"] if requested_path else ["file_resources", "knowledge_bases"]
            if bool(getattr(payload, "web_search_enabled", False)) and not requested_path:
                requested_scope.append("web")
        selected_paths = list(getattr(payload, "selected_paths", None) or [])
        # Explicit paths written in the user's query take precedence over the
        # implicit currently-open file sent by the frontend. This enables a
        # single task to summarize multiple files across folders/dates.
        if requested_paths:
            selected_paths = requested_paths
        elif requested_path and not selected_paths:
            selected_paths = [requested_path]
        selected_file_paths = context_resolver._selected_file_paths(selected_paths)
        folder_scope = bool(selected_paths) and not selected_file_paths
        # A multi-document task is explicit state, not an inference left to
        # the writing model. This prevents the intent model from changing a
        # request such as “梳理这些文章” into a single-document route.
        multi_document = (
            len(selected_file_paths) > 1
            or folder_scope
            or bool(re.search(r"(多篇|这些文章|分别|综述|多文档|多份资料|[两三四五六七八九十\d]+篇)", text))
        )
        multi_document_summary = multi_document and bool(
            re.search(r"(摘要|总结|概括|梳理|讲的什么|说了什么|内容是什么|综述)", text)
        )
        if selected_file_paths and not explicit_scope:
            requested_scope = ["file_resources"]
        kb_ids = list(dict.fromkeys((getattr(payload, "kb_ids", None) or []) + ([payload.kb_id] if getattr(payload, "kb_id", "") else [])))
        intent = self._classify_intent(text, getattr(payload, "template", None))
        spec = INTENT_SPECS.get(intent, INTENT_SPECS["generic"])
        mounted_skill = None
        try:
            from app.passive.skill_registry import skill_registry
            mounted_skill = skill_registry.match_enabled(text)
        except Exception:
            mounted_skill = None
        skill_constraints = {}
        if mounted_skill:
            skill_constraints = {
                "skill_id": mounted_skill.get("skill_id"),
                "skill_version": mounted_skill.get("version"),
                "allowed_tools": mounted_skill.get("allowed_tools") or [],
                "output_contract": mounted_skill.get("output_contract") or {},
            }
        mcp_request = {}
        mcp_match = re.search(r"(?:mcp)\s*[:：\s]\s*([\w-]+)\s*[/.:]\s*([\w-]+)", text, flags=re.I)
        if mcp_match:
            mcp_request = {"server": mcp_match.group(1), "tool": mcp_match.group(2), "arguments": {}}
        return {
            "goal": text,
            "intent": intent,
            "route": "builtin" if intent in INTENT_SPECS and intent != "generic" else "generic",
            "intent_confidence": 1.0 if intent != "generic" else 0.25,
            "intent_reason": "规则命中或界面模式指定" if intent != "generic" else "暂未匹配内置意图，等待通用 Agent 理解",
            "deliverable": spec["label"],
            "constraints": {
                "output_format": getattr(payload, "output_format", None) or "markdown",
                "scope": requested_scope,
                "web_search_enabled": bool(getattr(payload, "web_search_enabled", False)),
                "kb_ids": kb_ids,
                "selected_paths": selected_paths,
                "selected_file_paths": selected_file_paths,
                "single_document": len(selected_file_paths) == 1,
                "multi_document": multi_document,
                "multi_document_summary": multi_document_summary,
                "date_range": getattr(payload, "date_range", None) or {},
                "mounted_skill": skill_constraints,
                "mcp_request": mcp_request,
            },
        }

    @staticmethod
    def _classify_intent(text: str, template: Optional[str]) -> str:
        """Fast, deterministic route for obvious requests; ambiguous text uses the model."""
        normalized = re.sub(r"[\s！？!，。,.、？?：:]+", "", text.lower())
        light_patterns = (
            r"^(你好|您好|嗨|hi|hello|hey)$",
            r"(你是谁|你是什么模型|你用的什么模型|当前模型|模型名称|介绍一下自己|你能做什么|有什么能力|帮助)$",
        )
        if len(normalized) <= 40 and any(re.search(pattern, normalized) for pattern in light_patterns):
            return "chat"
        keyword_rules = (
            ("hotspot", ("热点", "热搜", "舆情汇总")),
            ("wechat", ("公众号", "微信文章", "推文")),
            ("xiaohongshu", ("小红书", "笔记")),
            ("weekly", ("周报", "日报", "月报")),
            ("compare", ("对比", "比较", "竞品")),
            ("fact_check", ("事实核查", "核查", "是否属实", "真假")),
            ("timeline", ("时间线", "时间轴", "发展历程")),
            # Multi-document requests are commonly phrased as “这些文章讲的
            # 什么/帮我梳理一下”, without using the word “总结”. Keep this
            # route deterministic so a temporary model outage cannot send the
            # task through the generic evidence-snippet fallback.
            ("summarize", ("摘要", "总结", "概括", "梳理", "讲的什么", "说了什么", "内容是什么")),
            ("research", ("研究报告", "专题研究", "深度分析", "分析报告", "分析")),
        )
        for intent, keywords in keyword_rules:
            if any(keyword in normalized for keyword in keywords):
                return intent
        if any(keyword in normalized for keyword in ("为什么", "是什么", "怎么做", "如何", "能否", "是否")):
            return "qa"
        # The UI template is an explicit user choice and therefore wins over
        # model classification, except for `auto`, which intentionally asks
        # the model to understand the task.
        if template in {name for name in INTENT_SPECS if name not in {"chat", "generic"}}:
            return template
        if template == "research" and any(keyword in normalized for keyword in ("研究", "报告", "分析")):
            return "research"
        return "generic"

    async def _understand_intent(self, goal: str, hinted_intent: str) -> Dict[str, Any]:
        """Use the configured model only for ambiguous requests.

        The model returns a small JSON decision, never a chain-of-thought.  A
        failed or malformed response safely falls back to the generic route.
        """
        if hinted_intent == "chat":
            return {"intent": "chat", "confidence": 1.0, "reason": "简单对话无需检索"}
        client = getattr(rag_service, "_glm", None)
        if not client or not settings.OPENAI_API_KEY:
            fallback = hinted_intent if hinted_intent in INTENT_SPECS else "generic"
            return {"intent": fallback, "confidence": 0.55 if fallback != "generic" else 0.2, "reason": "模型不可用，使用规则路由"}
        allowed = [name for name in INTENT_SPECS if name not in {"chat", "generic"}]
        prompt = (
            "判断用户任务最匹配的意图，只返回 JSON，不要解释推理过程。\n"
            f"可选意图：{', '.join(allowed + ['generic'])}\n"
            "字段：intent（必须是选项之一）、confidence（0 到 1）、reason（不超过 30 字）。\n"
            f"规则初判（仅作参考）：{hinted_intent}\n"
            f"用户任务：{goal}"
        )
        try:
            def _call():
                return client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "你是任务路由器，只输出合法 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=120,
                    temperature=0,
                )
            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=8)
            raw = (response.choices[0].message.content or "").strip()
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group(0)) if match else {}
            intent = str(data.get("intent") or "generic").strip()
            if intent not in INTENT_SPECS:
                intent = "generic"
            confidence = min(max(float(data.get("confidence", 0.5)), 0.0), 1.0)
            return {"intent": intent, "confidence": confidence, "reason": str(data.get("reason") or "模型完成意图识别")[:80]}
        except Exception as exc:
            logger.info("意图识别模型调用失败，转通用 Agent: %s", exc)
            fallback = hinted_intent if hinted_intent in INTENT_SPECS else "generic"
            return {"intent": fallback, "confidence": 0.45 if fallback != "generic" else 0.2, "reason": "意图模型不可用，使用规则路由"}

    @staticmethod
    def _light_answer(goal: str) -> str:
        normalized = re.sub(r"[\s！？!，。,.、？?：:]+", "", goal.lower())
        if "模型" in normalized or "你是谁" in normalized or "能力" in normalized or "能做什么" in normalized:
            model = settings.OPENAI_MODEL or "未配置"
            base = settings.OPENAI_BASE_URL or "默认兼容接口"
            llm_state = "已配置" if settings.OPENAI_API_KEY else "未配置 API Key"
            return f"我是 Knowledge Hub 的智能助手，当前配置模型：{model}（{base}，LLM {llm_state}）。我可以基于你授权的文件资源库和知识库进行检索、问答、总结和内容草稿生成。"
        return "你好！我是 Knowledge Hub 的智能助手。你可以直接提问，也可以切换 Agent 工作台，让我基于文件资源库和知识库撰写报告、周报或公众号草稿。"

    def plan(self, perception: Dict[str, Any]) -> List[Dict[str, Any]]:
        if perception.get("intent") == "chat":
            return [{"step": 1, "name": "direct_response", "status": "pending", "depends_on": []}]
        names = ["context_search", "evidence_deduplicate"]
        if perception["intent"] in {"hotspot", "weekly"}:
            names.append("evidence_cluster")
        names.extend(["draft_generate", "citation_verify"])
        return [
            {"step": index + 1, "name": name, "status": "pending", "depends_on": [index] if index else []}
            for index, name in enumerate(names)
        ]

    @staticmethod
    def _context_text(evidence: List[Dict[str, Any]]) -> str:
        parts = []
        for index, item in enumerate(evidence, 1):
            source = item.get("source_path") or item.get("title") or item.get("source_id") or "来源"
            content = str(item.get("content") or "")[:5000]
            parts.append(f"【{index}】来源：{source}\n{content}")
        return "\n\n".join(parts)

    def _llm_candidates(self) -> List[Dict[str, Any]]:
        """Return configured OpenAI-compatible clients in primary-first order.

        The persisted API-key collection may contain a fallback provider. The
        old implementation only used ``rag_service._glm``; when that provider
        was unreachable the whole Agent immediately degraded. Keeping the
        clients local to the request lets us fail over without changing the
        existing RAG singleton or exposing credentials.
        """
        candidates: List[Dict[str, Any]] = []
        seen = set()

        def add(client: Any, model: str, name: str, base_url: str) -> None:
            if not client or not model:
                return
            key = (str(base_url or "").rstrip("/"), str(model))
            if key in seen:
                return
            seen.add(key)
            candidates.append({"client": client, "model": model, "name": name, "base_url": base_url})

        add(getattr(rag_service, "_glm", None), settings.OPENAI_MODEL, "当前模型", settings.OPENAI_BASE_URL or "")
        config_path = os.path.join(settings.DATA_DIR, "system_config", "model_config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            collection = saved.get("api_key_collection", {}) if isinstance(saved, dict) else {}
            from openai import OpenAI
            for slot in ("base_llm", "fallback_llm"):
                item = collection.get(slot, {}) if isinstance(collection, dict) else {}
                if not isinstance(item, dict) or not item.get("enabled", True) or not item.get("api_key"):
                    continue
                model = str(item.get("model") or "").strip()
                base_url = str(item.get("base_url") or "").strip().rstrip("/")
                client = OpenAI(api_key=str(item["api_key"]), base_url=base_url or None, timeout=30.0, max_retries=0)
                add(client, model, str(item.get("provider") or slot), base_url)
        except Exception as exc:
            logger.info("读取 Agent 主备模型配置失败: %s", exc)
        return candidates

    async def _chat_completion(self, messages: List[Dict[str, str]], max_tokens: int,
                               temperature: float, timeout: int = 120) -> Any:
        """Call the first reachable configured LLM, then try the fallback."""
        last_error: Optional[Exception] = None
        candidates = self._llm_candidates()
        for item in candidates:
            try:
                def _call():
                    return item["client"].chat.completions.create(
                        model=item["model"], messages=messages,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)
                # Some reasoning-enabled OpenAI-compatible providers may spend
                # the whole budget in ``reasoning_content`` and return an empty
                # formal answer. Never surface that hidden field as the answer;
                # retry once with thinking disabled for a usable result.
                formal = ""
                if getattr(response, "choices", None):
                    formal = str(getattr(response.choices[0].message, "content", "") or "").strip()
                if not formal:
                    def _call_no_thinking():
                        return item["client"].chat.completions.create(
                            model=item["model"], messages=messages,
                            max_tokens=max_tokens, temperature=temperature,
                            extra_body={"enable_thinking": False},
                        )
                    try:
                        retry_response = await asyncio.wait_for(asyncio.to_thread(_call_no_thinking), timeout=timeout)
                        retry_formal = ""
                        if getattr(retry_response, "choices", None):
                            retry_formal = str(getattr(retry_response.choices[0].message, "content", "") or "").strip()
                        if retry_formal:
                            response = retry_response
                            logger.info("Agent 检测到空正式回答，已关闭思考模式重试: %s", item["name"])
                    except Exception as retry_exc:
                        logger.info("关闭思考模式重试失败，继续使用原响应: %s", retry_exc)
                item_name = item["name"]
                logger.info("Agent 使用模型完成请求: %s", item_name)
                return response
            except Exception as exc:
                last_error = exc
                logger.warning("Agent 模型调用失败，尝试下一模型 name=%s: %s", item["name"], exc)
        if last_error:
            raise last_error
        raise RuntimeError("未配置可用的模型 API Key")

    async def _summarize_one_document(self, item: Dict[str, Any], index: int) -> Dict[str, Any]:
        """Produce a bounded intermediate summary for one source document."""
        title = str(item.get("title") or f"文章 {index}")
        content = str(item.get("content") or "")[:9000]
        source = item.get("source_path") or title
        fallback = re.sub(r"\s+", " ", content).strip()[:900]
        if not self._llm_candidates():
            return {"index": index, "title": title, "source": source, "summary": fallback, "fallback": True}
        prompt = (
            "请只根据下面这一篇文章生成中间摘要，供后续多篇综述使用。不要补充外部事实，不要输出隐藏推理过程。\n"
            "输出四项：核心结论、关键要点（3-5条）、文章中的明确事实、未确定或未展开之处。\n\n"
            f"文章标题：{title}\n来源：{source}\n正文：\n{content}"
        )
        try:
            response = await self._chat_completion([
                {"role": "system", "content": "你是严格基于原文的单篇文章摘要器，只输出摘要，不展示推理过程。"},
                {"role": "user", "content": prompt},
            ], max_tokens=4200, temperature=0.1, timeout=100)
            message = response.choices[0].message if response.choices else None
            summary = (getattr(message, "content", None) or "").strip()
            if len(summary) < 40:
                return {"index": index, "title": title, "source": source, "summary": fallback, "fallback": True}
            return {"index": index, "title": title, "source": source, "summary": summary[:5000], "fallback": False}
        except Exception as exc:
            logger.warning("单篇文章摘要失败 index=%s: %s", index, exc)
            return {"index": index, "title": title, "source": source, "summary": fallback, "fallback": True}

    async def _generate_multi_document_summary(self, goal: str, evidence: List[Dict[str, Any]]) -> str:
        """Two-pass multi-document synthesis with a deterministic final shape.

        The model is deliberately not allowed to own the final document
        structure. It summarizes each source first, then returns only the
        cross-document sections. The backend assembles every source summary,
        so a short/partial model response can never silently drop article 2+.
        """
        # Parallel per-document calls reduce wall-clock time without merging
        # the full documents into one oversized reasoning prompt.
        summaries = await asyncio.gather(*(
            self._summarize_one_document(item, index)
            for index, item in enumerate(evidence, 1)
        ))
        synthesis_context = "\n\n".join(
            f"【{item['index']}】文章：{item['title']}\n来源：{item['source']}\n单篇摘要：{item['summary']}"
            for item in summaries
        )
        if not self._llm_candidates():
            return self._multi_summary_fallback(goal, summaries)
        prompt = (
            "你是多篇文章综述 Agent。下方是已经完成的逐篇摘要。\n"
            "只能使用这些摘要中的信息，不得补充外部事实，不得创建不存在的来源。"
            "请只返回一个合法 JSON 对象，不要 Markdown 包裹，不要输出隐藏推理过程。\n"
            "JSON 字段必须是：overall（总体结论，约150-300字）、common（共同主题与关系）、"
            "differences（主要差异）、implications（对用户的启示）、boundary（资料边界）。"
            "每个字段都必须是字符串；如果资料不足，明确写‘资料未提供’，不要猜测。"
            "跨文章判断请在句末使用已有编号【1】、【2】等，不能使用不存在的编号。\n\n"
            f"任务目标：{goal}\n\n资料摘要：\n{synthesis_context}\n\n请返回 JSON："
        )

        def _compose(sections: Dict[str, Any]) -> str:
            def section(name: str, fallback: str) -> str:
                value = sections.get(name)
                if isinstance(value, list):
                    value = "\n".join(f"- {item}" for item in value)
                value = str(value or "").strip()
                return value or fallback

            lines = ["# 多篇文章综述", "", "## 任务目标", goal.strip(), "",
                     "## 总体结论", section("overall", "资料不足，无法形成跨文章结论。"),
                     "", "## 分篇梳理"]
            for item in summaries:
                lines.extend([
                    "", f"### 【{item['index']}】{item['title']}",
                    f"来源：{item['source']} 【{item['index']}】", "", item["summary"].strip(),
                ])
            lines.extend([
                "", "## 共同主题与关系", section("common", "资料未提供共同主题与关系。"),
                "", "## 主要差异", section("differences", "资料未提供足够信息比较差异。"),
                "", "## 对用户的启示", section("implications", "资料未提供可执行启示。"),
                "", "## 资料边界", section("boundary", "仅使用本次任务指定的文章，未补充外部事实。"),
            ])
            return "\n".join(lines).strip() + "\n"

        fallback_sections = {
            "overall": "已完成逐篇摘要；当前模型未完成跨文章综合。",
            "common": "当前模型未完成跨文章综合，请结合分篇梳理阅读。",
            "differences": "当前模型未完成跨文章差异分析。",
            "implications": "请基于分篇梳理进一步判断。",
            "boundary": "仅使用本次任务指定的文章，未补充外部事实。",
        }
        try:
            messages = [
                {"role": "system", "content": "你是严格基于多来源证据的综述 Agent，只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ]
            response = await self._chat_completion(messages, max_tokens=2800, temperature=0.1, timeout=120)
            message = response.choices[0].message if response.choices else None
            raw = (getattr(message, "content", None) or "").strip()
            match = re.search(r"\{[\s\S]*\}", raw)
            sections = json.loads(match.group(0)) if match else {}
            if not isinstance(sections, dict):
                sections = {}
            if not any(str(sections.get(key) or "").strip() for key in fallback_sections):
                response = await self._chat_completion(
                    messages + [{"role": "user", "content": "上次不是合法 JSON。请只返回包含 overall、common、differences、implications、boundary 五个字符串字段的 JSON。"}],
                    max_tokens=3200, temperature=0.1, timeout=140,
                )
                message = response.choices[0].message if response.choices else None
                raw = (getattr(message, "content", None) or "").strip()
                match = re.search(r"\{[\s\S]*\}", raw)
                sections = json.loads(match.group(0)) if match else {}
                if not isinstance(sections, dict):
                    sections = {}
            for key, default in fallback_sections.items():
                if not str(sections.get(key) or "").strip():
                    sections[key] = default
            return _compose(sections)
        except Exception as exc:
            logger.warning("多篇综述合并失败，使用分篇摘要降级: %s", exc)
            return _compose(fallback_sections)

    @staticmethod
    def _multi_summary_fallback(goal: str, summaries: List[Dict[str, Any]]) -> str:
        lines = ["# 多篇文章综述", "", "## 任务目标", goal, "", "## 分篇梳理"]
        for item in summaries:
            lines.extend([
                "", f"### 【{item['index']}】{item['title']}",
                f"来源：{item['source']} 【{item['index']}】", "", item["summary"],
            ])
        lines.extend([
            "", "## 共同主题与关系", "", "当前模型未完成跨文章综合，以上为逐篇摘要。",
            "", "## 资料边界", "", "仅使用本次任务指定的文章，未补充外部事实。",
        ])
        return "\n".join(lines) + "\n"

    async def _generate_draft(self, goal: str, template: str, evidence: List[Dict[str, Any]], clusters: List[Dict[str, Any]], single_document: bool = False, multi_document_summary: bool = False, output_contract: Optional[Dict[str, Any]] = None) -> str:
        if not evidence:
            return "# 资料不足\n\n当前授权范围内没有检索到足够证据，未生成无依据的内容。请补充或选择文件/知识库后重试。"

        # Multi-document summaries use a two-pass pipeline.  This avoids
        # exhausting a reasoning model's completion budget on a single huge
        # prompt and guarantees that every selected article is represented.
        if len(evidence) > 1 and (
            template == "summarize"
            or multi_document_summary
            or bool(re.search(r"(多篇|这些文章|分别|综述|多文档|多份资料|[两三四五六七八九十\d]+篇).*(摘要|总结|概括|梳理|讲的什么|说了什么|内容是什么|综述)", goal))
        ):
            return await self._generate_multi_document_summary(goal, evidence)

        spec = INTENT_SPECS.get(template, INTENT_SPECS["generic"])
        label = spec["label"]
        context = self._context_text(evidence)
        structure = spec["structure"]

        if not self._llm_candidates():
            lines = [f"# {label}", "", f"## 任务目标\n{goal}", "", "## 基于资料的整理"]
            for index, item in enumerate(evidence[:8], 1):
                snippet = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:320]
                citation = "" if single_document else f"【{index}】"
                lines.append(f"\n- {snippet}{citation}")
            lines.extend(["", "## 资料范围", "", "本文仅基于当前任务检索到的资料整理。"])
            return "\n".join(lines) + "\n"

        citation_rule = (
            "当前只有一个来源，不要输出【1】等引用编号，也不要创建第二来源。"
            if single_document else
            "每个关键事实或结论句末必须添加对应引用，引用编号只能来自下方证据。"
        )
        boundary_rule = (
            "这是单篇文章摘要任务，只能总结目标文件本身。禁止提及其他文章、第二条证据或未出现在目标文件中的事件。"
            "采集时间/文章发布时间不是事件或公告日期；除非正文明确说明，否则使用‘近日’等表述。"
            "评论区内容必须明确标注为‘评论区用户观点’，不得写成官方事实。不要输出超出原文的建议。"
            if single_document else
            "不得把不同来源的内容拼接成一个事实；信息不足时明确说明。"
        )
        contract_rule = ""
        if output_contract:
            contract_rule = f"Skill 输出契约（必须遵守）：{json.dumps(output_contract, ensure_ascii=False)[:2000]}。"
        prompt = (
            f"你是资料驱动的{label}写作 Agent。用户目标：{goal}\n\n"
            f"写作要求：{structure}\n"
            "严格规则：只能使用下方证据，不得补充证据之外的事实、数字、人物或案例。"
            f"{citation_rule}{boundary_rule}{contract_rule}"
            "如果证据不足，明确写出资料不足，不要猜测。输出 Markdown，不要寒暄，不要额外生成虚构的参考资料列表。\n\n"
            f"证据：\n{context}\n\n请直接输出最终草稿："
        )

        try:
            max_tokens = min(max(settings.OPENAI_MAX_TOKENS, 7000), 10000)
            messages = [
                {"role": "system", "content": spec["system_prompt"] + " 不展示隐藏推理过程，只输出最终 Markdown。"},
                {"role": "user", "content": prompt},
            ]
            response = await self._chat_completion(messages, max_tokens=max_tokens, temperature=0.2, timeout=120)
            message = response.choices[0].message if response.choices else None
            draft = (getattr(message, "content", None) or "").strip()
            # An empty/near-empty final answer with a non-empty reasoning trace
            # indicates that the model exhausted its reasoning budget. Retry
            # once with a larger budget and an explicit final-answer reminder.
            if len(draft) < 40 and len(evidence) > 1:
                response = await self._chat_completion(
                    messages + [{"role": "user", "content": "请现在输出完整的最终 Markdown 答案，不要只输出分析过程。"}],
                    max_tokens=min(max(max_tokens + 2000, 9000), 12000),
                    temperature=0.2, timeout=150,
                )
                message = response.choices[0].message if response.choices else None
                draft = (getattr(message, "content", None) or "").strip()
            if single_document:
                draft = re.sub(r"【\d+】", "", draft)
            if draft:
                return draft
            return await self._generate_draft_fallback(goal, label, evidence, single_document)
        except Exception as exc:
            logger.warning("Agent 草稿生成失败，使用证据式降级输出: %s", exc)
            return await self._generate_draft_fallback(goal, label, evidence, single_document)

    @staticmethod
    async def _generate_draft_fallback(goal: str, label: str, evidence: List[Dict[str, Any]], single_document: bool = False) -> str:
        lines = [f"# {label}", "", f"## 任务目标\n{goal}", "", "## 证据整理"]
        for index, item in enumerate(evidence[:8], 1):
            snippet = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()[:500]
            citation = "" if single_document else f"【{index}】"
            lines.append(f"\n### {item.get('title') or '资料 ' + str(index)}\n{snippet}{citation}")
        lines.extend(["", "## 说明", "", "模型未返回可用的完整正文，以上为基于已检索证据的降级整理，未补充外部事实。"])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _verify_draft(draft: str, evidence: List[Dict[str, Any]], expected_source_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        indexes = [int(value) for value in re.findall(r"【(\d+)】", draft or "")]
        valid = [value for value in indexes if 1 <= value <= len(evidence)]
        invalid = [value for value in indexes if value < 1 or value > len(evidence)]
        distinct = sorted(set(valid))
        has_evidence = bool(evidence)
        expected = set(expected_source_paths or [])
        actual = {str(item.get("source_path") or "") for item in evidence if item.get("source_path")}
        unexpected_sources = sorted(actual - expected) if expected else []
        single_document = len(expected) == 1
        redundant_single_source_citations = bool(single_document and indexes)
        coverage = len(distinct) / max(1, min(len(evidence), 5)) if has_evidence else 0.0
        return {
            "source_count": len(evidence),
            "citation_count": len(valid),
            "cited_source_count": len(distinct),
            "invalid_citations": invalid,
            "citation_coverage": round(min(coverage, 1.0), 4),
            "has_evidence": has_evidence,
            "single_document": single_document,
            "unexpected_sources": unexpected_sources,
            "redundant_single_source_citations": redundant_single_source_citations,
            "passed": has_evidence and not invalid and not unexpected_sources and not redundant_single_source_citations and (bool(valid) or single_document),
        }

    @staticmethod
    def _is_cancelled(task_id: str) -> bool:
        current = task_tracker.get_task(task_id)
        return bool(current and current.status == TaskStatus.CANCELLED)

    async def _run_dynamic_loop(self, task_id: str, task_goal: str, payload: Any,
                                perception: Dict[str, Any]) -> Dict[str, Any]:
        """Run the Agent-only bounded decision/observation loop.

        Standard RAG never enters this method. Existing tools are reused, but
        the next action is selected from the current state after every tool
        result, with explicit budget and policy checks.
        """
        state = AgentState(task_id=task_id, goal=task_goal, intent=perception["intent"],
                           constraints=perception["constraints"])
        mcp_request = perception["constraints"].get("mcp_request") or {}
        if mcp_request:
            try:
                state.constraints["mcp_tools"] = await self.runtime.mcp.discover(str(mcp_request.get("server") or ""))
            except Exception as exc:
                state.observations.append({"action": "mcp_discover", "ok": False, "error_type": type(exc).__name__})
        executor = AgentExecutor(self.runtime, policy)
        trace: List[Dict[str, Any]] = []

        async def model_decider(current_state: AgentState, options: Dict[str, Any]) -> Dict[str, Any]:
            if state.model_calls >= policy.MAX_MODEL_CALLS or not self._llm_candidates():
                return {}
            state.model_calls += 1
            prompt = (
                "你是 Agent Planner，只从候选动作中选择下一步。只返回 JSON，不输出思维过程。\n"
                f"候选动作：{json.dumps(options.get('candidates') or [], ensure_ascii=False)}\n"
                f"当前状态：{json.dumps(current_state.snapshot(), ensure_ascii=False)}\n"
                "字段：action、reason_code；如果 action=mcp_tool_call，还要返回 server、tool、arguments。arguments 必须符合对应 inputSchema。不要创建候选列表之外的动作。"
            )
            response = await self._chat_completion([
                {"role": "system", "content": "你是受控 Agent 规划器，只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ], max_tokens=160, temperature=0, timeout=8)
            raw = (getattr(response.choices[0].message, "content", "") if response.choices else "") or ""
            match = re.search(r"\{[\s\S]*\}", raw)
            return json.loads(match.group(0)) if match else {}

        while state.status == "running" and state.step_index < policy.MAX_STEPS:
            if self._is_cancelled(task_id):
                state.status = "cancelled"; state.stop_reason = "cancelled_by_user"; break
            decision = await planner.choose_async(state, model_decider)
            action_name = str(decision.get("action") or "finish")
            state.current_action = action_name
            if decision.get("finish"):
                state.stop_reason = str(decision.get("reason_code") or "planner_finished")
                state.status = "failed" if state.stop_reason in {"evidence_unavailable", "step_budget_exceeded"} else "completed"
                trace.append({"phase": "decision", "action": "finish", "reason": state.stop_reason, "step": state.step_index})
                break
            state.step_index += 1
            task_tracker.update_task(task_id, progress=min(90, 15 + state.step_index * 9),
                                     message=f"Agent Loop：决策执行 {action_name}")
            trace.append({"phase": "decision", "action": action_name, "reason": decision.get("reason_code", ""),
                          "step": state.step_index, "model_selected": bool(decision.get("model_selected"))})
            args: Dict[str, Any] = {}
            if action_name == "context_search":
                args = {"goal": task_goal, "kb_id": getattr(payload, "kb_id", "") or "",
                        "kb_ids": getattr(payload, "kb_ids", None),
                        "scope": perception["constraints"]["scope"],
                        "selected_paths": perception["constraints"]["selected_paths"],
                        "date_range": perception["constraints"]["date_range"],
                        "top_k": 12 if decision.get("retry") else (getattr(payload, "top_k", None) or 8),
                        "web_search_enabled": getattr(payload, "web_search_enabled", False)}
            elif action_name == "mcp_tool_call":
                args = {"server": str(decision.get("server") or mcp_request.get("server") or ""),
                        "tool": str(decision.get("tool") or mcp_request.get("tool") or ""),
                        "arguments": decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}}
                options = getattr(payload, "output_options", None) or {}
                if isinstance(options, dict) and isinstance(options.get("mcp_arguments"), dict):
                    args["arguments"] = options["mcp_arguments"]
            elif action_name == "evidence_deduplicate":
                args = {"evidence": state.evidence}
            elif action_name == "evidence_cluster":
                args = {"evidence": state.evidence}
            elif action_name == "multi_document_coverage":
                args = {"evidence": state.evidence, "expected_source_paths": perception["constraints"].get("selected_file_paths", [])}
            elif action_name == "draft_generate":
                args = {"goal": task_goal, "template": perception["intent"], "evidence": state.evidence,
                        "clusters": state.clusters,
                        "single_document": perception["constraints"].get("single_document", False),
                        "multi_document_summary": perception["constraints"].get("multi_document_summary", False),
                        "output_contract": (perception["constraints"].get("mounted_skill") or {}).get("output_contract") or {}}
            elif action_name == "citation_verify":
                args = {"draft": state.draft, "evidence": state.evidence,
                        "expected_source_paths": perception["constraints"].get("selected_file_paths", [])}
            else:
                state.status = "failed"; state.stop_reason = "unknown_action"; break
            result = await executor.execute(decision, state, args)
            if result.get("ok"):
                value = result.get("result") or {}
                if action_name == "context_search": state.evidence = value.get("evidence") or []
                elif action_name == "evidence_deduplicate": state.evidence = value.get("evidence") or []
                elif action_name == "evidence_cluster": state.clusters = value.get("clusters") or []
                elif action_name == "multi_document_coverage": state.coverage = value
                elif action_name == "mcp_tool_call":
                    # MCP results become auditable evidence when they contain
                    # textual content, allowing the normal draft/citation
                    # stages to use the remote tool output.
                    raw_content = value.get("content") if isinstance(value, dict) else value
                    if isinstance(raw_content, list):
                        raw_content = "\n".join(str(item.get("text") or item) if isinstance(item, dict) else str(item) for item in raw_content)
                    if isinstance(raw_content, (dict, list)):
                        raw_content = json.dumps(raw_content, ensure_ascii=False)
                    if str(raw_content or "").strip():
                        state.evidence.append({
                            "source_type": "mcp", "source_id": f"mcp:{args.get('server')}/{args.get('tool')}",
                            "source_path": f"mcp://{args.get('server')}/{args.get('tool')}",
                            "title": f"MCP {args.get('tool')}", "content": str(raw_content)[:12000],
                            "snippet": str(raw_content)[:800], "score": 1.0,
                            "metadata": {"server": args.get("server"), "tool": args.get("tool")},
                        })
                elif action_name == "draft_generate": state.draft = value.get("draft") or ""
                elif action_name == "citation_verify": state.quality = value
                trace.append({"phase": "observation", "action": action_name, "ok": True,
                              "evidence_count": len(state.evidence), "draft_chars": len(state.draft)})
            else:
                trace.append({"phase": "observation", "action": action_name, "ok": False,
                              "error": str(result.get("error") or result.get("reason") or "tool_failed")[:240]})
            verdict = evaluator.evaluate(state)
            if verdict.get("done") and state.draft and state.quality:
                state.status = "completed"; state.stop_reason = verdict.get("reason", "quality_gate_complete")
            elif verdict.get("done") and verdict.get("reason") in {"policy_blocked", "step_budget_exceeded"}:
                state.status = "failed"; state.stop_reason = verdict["reason"]
            telemetry.record("agent_replanned", source="agent", task_id=task_id, session_id=getattr(payload, "session_id", "") or "",
                             properties={"step_index": state.step_index, "next_action": "pending",
                                         "observation_ok": bool(result.get("ok")), "stop": state.status != "running"})
        if state.status == "running":
            state.status = "failed"; state.stop_reason = "step_budget_exceeded"
        if not state.draft:
            state.draft = await self._generate_draft(task_goal, perception["intent"], state.evidence, state.clusters,
                                                     bool(perception["constraints"].get("single_document", False)),
                                                     bool(perception["constraints"].get("multi_document_summary", False)),
                                                     (perception["constraints"].get("mounted_skill") or {}).get("output_contract") or {})
        return {"evidence": state.evidence, "clusters": state.clusters, "draft": state.draft,
                "coverage": state.coverage,
                "quality": state.quality or {"passed": bool(state.evidence), "has_evidence": bool(state.evidence)},
                "tool_calls": state.tool_history, "trace": trace, "stop_reason": state.stop_reason,
                "status": state.status, "steps": state.step_index}

    async def run(self, task_id: str, payload: Any) -> Dict[str, Any]:
        if self._is_cancelled(task_id):
            raise RuntimeError("任务已取消")

        session_id = str(getattr(payload, "session_id", "") or "").strip()
        task_goal = payload.goal
        telemetry.record("agent_task_started", source="agent", task_id=task_id, session_id=session_id,
                         properties={"template": getattr(payload, "template", "auto"), "goal_length": len(str(payload.goal or ""))})
        if session_id:
            conversation_memory.create_session(session_id)
            # Resolve short follow-ups such as “那它呢？” against the
            # previous turn before planning and retrieval.
            task_goal = conversation_memory.rewrite_with_context(session_id, payload.goal)

        # A mounted passive Skill is a first-class Agent route. When the user
        # asks for a matching daily/hotspot briefing, execute the Skill
        # workflow (rather than treating its Markdown as a prompt snippet).
        # The conversational route is a dry-run by default; scheduled jobs or
        # the explicit “运行并发送” action are the only paths that deliver mail.
        try:
            from app.passive.service import passive_service
            mounted_skill = passive_service.registry.match_enabled(task_goal)
            if mounted_skill and any(token in task_goal.lower() for token in ("热点", "日报", "趋势", "资讯")):
                task_tracker.update_task(task_id, progress=15, message=f"Agent：已挂载 Skill {mounted_skill.get('name')}，正在执行其采集与日报流程")
                run = await passive_service.start_run(str(mounted_skill["skill_id"]), dry_run=True, force=True)
                deadline = time.monotonic() + min(240, int((mounted_skill.get("limits") or {}).get("max_run_seconds", 300)))
                while time.monotonic() < deadline:
                    current = passive_service.get_run(run["run_id"]) or run
                    if current.get("status") in {"completed", "partial", "failed"}:
                        break
                    await asyncio.sleep(0.5)
                current = passive_service.get_run(run["run_id"]) or run
                artifact = str(current.get("artifact_path") or "")
                draft = ""
                if artifact and os.path.isfile(artifact):
                    with open(artifact, "r", encoding="utf-8") as handle:
                        draft = handle.read()
                if not draft:
                    draft = f"# {mounted_skill.get('name', '日报')}\n\n日报任务状态：{current.get('status')}。\n\n{current.get('error') or '尚未生成可用产物，请查看运行记录。'}"
                quality = {"passed": current.get("status") in {"completed", "partial"}, "mode": "mounted_passive_skill", "run_id": run["run_id"], "item_count": current.get("item_count", 0), "trace_count": len(current.get("trace") or [])}
                memory = {"task_id": task_id, "session_id": session_id, "goal": task_goal, "intent": "hotspot", "skill_id": mounted_skill.get("skill_id"), "run_id": run["run_id"], "created_at": datetime.utcnow().isoformat() + "Z"}
                trace = current.get("trace") or []
                return {"rag_result": {"answer": draft, "sources": [], "chunks": [], "images": [], "metadata": {"mode": "mounted_passive_skill", "run": current, "skill_execution_trace": trace}}, "draft": draft, "evidence": [], "images": [], "perception": {"intent": "hotspot", "deliverable": mounted_skill.get("name"), "mounted_skill": mounted_skill.get("skill_id")}, "plan": [{"step": 1, "name": "passive_skill_run", "status": "completed", "depends_on": []}], "tool_calls": [{"name": "passive_skill_run", "status": "completed", "skill_id": mounted_skill.get("skill_id"), "run_id": run["run_id"], "trace": trace}], "quality": quality, "memory": memory}
        except Exception as exc:
            logger.warning("挂载被动 Skill 执行失败，回退标准 Agent: %s", exc)

        perception = self.perceive(task_goal, payload)
        # Ambiguous requests are understood by the configured model before a
        # route is chosen.  The returned intent is still constrained to the
        # allow-listed INTENT_SPECS table.
        intent_decision = await self._understand_intent(task_goal, perception["intent"])
        resolved_intent = intent_decision["intent"]
        resolved_spec = INTENT_SPECS.get(resolved_intent, INTENT_SPECS["generic"])
        perception["intent"] = resolved_intent
        perception["route"] = "builtin" if resolved_intent != "generic" else "generic"
        perception["intent_confidence"] = intent_decision["confidence"]
        perception["intent_reason"] = intent_decision["reason"]
        perception["deliverable"] = resolved_spec["label"]
        plan = self.plan(perception)
        telemetry.record("agent_perception_completed", source="agent", task_id=task_id, session_id=session_id,
                         properties={"intent": resolved_intent, "confidence": intent_decision["confidence"],
                                     "route": perception["route"], "selected_file_count": len(perception["constraints"].get("selected_file_paths", []))})
        telemetry.record("agent_plan_created", source="agent", task_id=task_id, session_id=session_id,
                         properties={"planned_steps": [step["name"] for step in plan], "planner_mode": "bounded_workflow"})
        tool_calls: List[Dict[str, Any]] = []
        base_result = {"phase": "perception", "perception": perception, "plan": plan, "tool_calls": tool_calls}
        task_tracker.update_task(task_id, progress=10, message=f"感知层：已识别为{resolved_spec['label']}，正在选择专属执行路径", result=base_result)

        if perception["intent"] == "chat":
            # A selected Agent mode must not turn a greeting into a research
            # report or wait on vector/LLM timeouts.
            draft = self._light_answer(task_goal)
            plan[0]["status"] = "completed"
            quality = {"source_count": 0, "citation_count": 0, "citation_coverage": 0.0, "has_evidence": False, "passed": True, "mode": "light_chat"}
            memory = {"task_id": task_id, "session_id": session_id, "goal": task_goal, "intent": "chat", "source_ids": [], "created_at": datetime.utcnow().isoformat() + "Z"}
            return {"rag_result": {"answer": draft, "sources": [], "chunks": [], "images": [], "metadata": {"mode": "light_chat"}}, "draft": draft, "evidence": [], "images": [], "perception": perception, "plan": plan, "tool_calls": [], "quality": quality, "memory": memory}

        # Agent-only dynamic loop. The standard RAG endpoint does not call
        # this method and keeps its original streaming pipeline unchanged.
        try:
            dynamic = await self._run_dynamic_loop(task_id, task_goal, payload, perception)
            evidence = dynamic["evidence"]
            images = collect_evidence_images(evidence)
            quality = dynamic["quality"]
            memory = {"task_id": task_id, "session_id": session_id, "goal": task_goal,
                      "intent": perception["intent"], "scope": perception["constraints"]["scope"],
                      "source_ids": [item.get("source_id") for item in evidence if item.get("source_id")],
                      "created_at": datetime.utcnow().isoformat() + "Z",
                      "agent_loop": {"status": dynamic["status"], "steps": dynamic["steps"], "stop_reason": dynamic["stop_reason"]}}
            return {"rag_result": {"answer": dynamic["draft"], "sources": evidence,
                                    "chunks": [item for item in evidence if item.get("source_type") == "knowledge_base"],
                                    "images": images,
                                    "metadata": {"mode": "agent_loop", "evidence_count": len(evidence), "citation_quality": quality}},
                    "draft": dynamic["draft"], "evidence": evidence, "images": images, "perception": perception,
                    "plan": [{"step": i + 1, "name": event.get("action", ""), "status": "completed" if event.get("ok", True) else "failed"}
                             for i, event in enumerate(dynamic.get("trace") or []) if event.get("phase") == "decision" and event.get("action") != "finish"],
                    "tool_calls": dynamic["tool_calls"], "trace": dynamic.get("trace") or [], "quality": quality, "memory": memory}
        except Exception as exc:
            # Safety fallback: retain the existing fixed Agent pipeline when
            # the new loop encounters an unexpected integration error.
            logger.exception("动态 Agent Loop 失败，回退兼容编排: %s", exc)
            telemetry.record("agent_loop_fallback", source="agent", task_id=task_id, session_id=session_id,
                             properties={"error_type": type(exc).__name__})

        search_args = {
            "goal": task_goal,
            "kb_id": getattr(payload, "kb_id", "") or "",
            "kb_ids": getattr(payload, "kb_ids", None),
            "scope": perception["constraints"]["scope"],
            "selected_paths": perception["constraints"]["selected_paths"],
            "date_range": perception["constraints"]["date_range"],
            "top_k": getattr(payload, "top_k", None) or 8,
            "web_search_enabled": getattr(payload, "web_search_enabled", False),
        }
        tool_calls.append({"name": "context_search", "status": "running", "input": {"scope": search_args["scope"], "kb_ids": search_args["kb_ids"], "selected_paths": search_args["selected_paths"]}})
        task_tracker.update_task(task_id, progress=25, message="行动层：正在调取文件资源库和知识库证据", result={**base_result, "phase": "action"})
        search_result = await self.runtime.tools.call("context_search", search_args)
        evidence = search_result.get("evidence") or []
        images = collect_evidence_images(evidence)
        tool_calls[-1].update({"status": "completed", "result_count": len(evidence), "metadata": search_result.get("metadata", {})})

        if not evidence and "web" not in perception["constraints"]["scope"]:
            tool_calls.append({"name": "context_search", "status": "retrying", "reason": "首次未命中证据"})
            retry_result = await self.runtime.tools.call("context_search", dict(search_args, top_k=12))
            evidence = retry_result.get("evidence") or []
            images = collect_evidence_images(evidence)
            tool_calls[-1].update({"status": "completed", "result_count": len(evidence)})

        if self._is_cancelled(task_id):
            raise RuntimeError("任务已取消")

        plan[0]["status"] = "completed"
        plan[1]["status"] = "in_progress"
        dedup = await self.runtime.tools.call("evidence_deduplicate", {"evidence": evidence})
        evidence = dedup.get("evidence") or []
        images = collect_evidence_images(evidence)
        tool_calls.append({"name": "evidence_deduplicate", "status": "completed", "result_count": len(evidence)})
        plan[1]["status"] = "completed"

        clusters: List[Dict[str, Any]] = []
        cluster_step = next((item for item in plan if item["name"] == "evidence_cluster"), None)
        if cluster_step:
            cluster_step["status"] = "in_progress"
            cluster_result = await self.runtime.tools.call("evidence_cluster", {"evidence": evidence})
            clusters = cluster_result.get("clusters") or []
            cluster_step["status"] = "completed"
            tool_calls.append({"name": "evidence_cluster", "status": "completed", "cluster_count": len(clusters)})

        task_tracker.update_task(task_id, progress=55, message=f"行动层：已绑定 {len(evidence)} 条真实证据，正在生成{perception['deliverable']}", result={**base_result, "phase": "action", "evidence": evidence})
        draft_step = next(item for item in plan if item["name"] == "draft_generate")
        draft_step["status"] = "in_progress"
        draft_result = await self.runtime.tools.call("draft_generate", {"goal": task_goal, "template": perception["intent"], "evidence": evidence, "clusters": clusters, "single_document": perception["constraints"].get("single_document", False), "multi_document_summary": perception["constraints"].get("multi_document_summary", False)})
        draft = draft_result.get("draft") or ""
        draft_step["status"] = "completed"
        tool_calls.append({"name": "draft_generate", "status": "completed", "evidence_count": len(evidence), "draft_chars": len(draft)})

        verify_step = next(item for item in plan if item["name"] == "citation_verify")
        verify_step["status"] = "in_progress"
        quality = await self.runtime.tools.call("citation_verify", {"draft": draft, "evidence": evidence, "expected_source_paths": perception["constraints"].get("selected_file_paths", [])})
        verify_step["status"] = "completed"
        tool_calls.append({"name": "citation_verify", "status": "completed", "quality": quality})
        telemetry.record("citation_verified", source="agent", task_id=task_id, session_id=session_id,
                         properties={"passed": quality.get("passed", False), "coverage": quality.get("citation_coverage", 0),
                                     "invalid_count": len(quality.get("invalid_citations", [])),
                                     "source_count": len(evidence),
                                     "multi_document": perception["constraints"].get("multi_document", False)})
        task_tracker.update_task(task_id, progress=85, message="反馈层：已完成证据和引用核验", result={**base_result, "phase": "feedback", "evidence": evidence, "draft": draft, "quality": quality})

        memory = {
            "task_id": task_id, "session_id": session_id, "goal": task_goal, "intent": perception["intent"],
            "scope": perception["constraints"]["scope"],
            "source_ids": [item.get("source_id") for item in evidence if item.get("source_id")],
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        memory_dir = os.path.join(settings.DATA_DIR, "agent_memory")
        os.makedirs(memory_dir, exist_ok=True)
        with open(os.path.join(memory_dir, f"{task_id}.json"), "w", encoding="utf-8") as handle:
            json.dump(memory, handle, ensure_ascii=False, indent=2)

        rag_result = {
            "answer": draft,
            "sources": evidence,
            "chunks": [item for item in evidence if item.get("source_type") == "knowledge_base"],
            "images": images,
            "metadata": {**(search_result.get("metadata") or {}), "evidence_count": len(evidence), "citation_quality": quality},
        }
        return {
            "rag_result": rag_result, "draft": draft, "evidence": evidence, "images": images,
            "perception": perception, "plan": plan, "tool_calls": tool_calls,
            "quality": quality, "memory": memory,
        }


agent_orchestrator = AgentOrchestrator()
