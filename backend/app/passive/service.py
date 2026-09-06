from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app.agent.orchestrator import agent_orchestrator
from app.core.config import settings
from app.passive.collector import collect_sources
from app.passive.skill_registry import skill_registry
from app.services.email_notification import email_notification_service
from app.telemetry.recorder import telemetry

logger = logging.getLogger(__name__)


class PassiveBriefingService:
    def __init__(self) -> None:
        self.registry = skill_registry
        self.root = Path(settings.DATA_DIR) / "passive_briefings"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_file = self.root / "runs.json"
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._run_semaphore = asyncio.Semaphore(max(1, int(getattr(settings, "PASSIVE_MAX_CONCURRENT_RUNS", 1) or 1)))
        self._scheduler_task: Optional[asyncio.Task] = None
        self._load_runs()

    def _load_runs(self) -> None:
        try:
            data = json.loads(self.runs_file.read_text(encoding="utf-8"))
            self._runs = data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            self._runs = {}

    def _save_runs(self) -> None:
        tmp = self.runs_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._runs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.runs_file)

    def list_runs(self, limit: int = 50) -> list[Dict[str, Any]]:
        rows = sorted(self._runs.values(), key=lambda row: row.get("created_at", ""), reverse=True)
        return rows[:max(1, min(limit, 200))]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._runs.get(run_id)

    def delete_run(self, run_id: str) -> bool:
        """Remove a run record while preserving generated artifacts on disk."""
        if run_id not in self._runs:
            return False
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        self._runs.pop(run_id, None)
        self._save_runs()
        return True

    def _update(self, run_id: str, **changes: Any) -> None:
        row = self._runs.get(run_id)
        if not row:
            return
        row.update(changes)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_runs()

    def _trace(self, run_id: str, phase: str, message: str, **data: Any) -> None:
        """Append an auditable execution event without exposing model CoT."""
        row = self._runs.get(run_id)
        if not row:
            return
        trace = row.setdefault("trace", [])
        trace.append({
            "phase": phase,
            "message": message,
            "data": data,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        # Keep the run record small on the low-cost server.
        if len(trace) > 80:
            del trace[:-80]
        self._save_runs()

    def _skill_directives(self, skill: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the operational parts of SKILL.md for the Agent.

        A Skill is more than a URL list. We retain the mounted Markdown as the
        source of truth but pass only its actionable sections to the model so a
        very long README cannot crowd out evidence. The exact file hash is
        recorded in the run for auditability.
        """
        try:
            body = self.registry.read_body(str(skill.get("skill_id") or ""))
        except Exception:
            body = ""
        body = body or str(skill.get("body_excerpt") or "")
        headings = ("When to Use", "Workflow Overview", "Step-by-Step Instructions",
                    "生成要求", "Important Notes", "Error Recovery")
        sections: list[str] = []
        lines = body.splitlines()
        current: list[str] = []
        active = False
        base_level = 0
        for line in lines:
            heading_match = re.match(r"^(#{1,6})\s*(.*?)\s*$", line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip().lower()
                is_target = any(title == h.lower() or title.startswith(h.lower()) for h in headings)
                if is_target:
                    if current:
                        sections.extend(current)
                    current = [line]
                    active = True
                    base_level = level
                    continue
                # Nested headings belong to the active section. A heading at
                # the same or higher level starts the next section.
                if active and level <= base_level:
                    sections.extend(current)
                    current = []
                    active = False
            if active:
                current.append(line)
        if current:
            sections.extend(current)
        excerpt = "\n".join(sections).strip()
        if not excerpt:
            excerpt = body
        return {
            "content": excerpt[:10000],
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "length": len(body),
        }

    @staticmethod
    def _score(item: Dict[str, Any], filters: Dict[str, Any]) -> float:
        text = f"{item.get('title', '')} {item.get('content', '')}".lower()
        include = [str(value).lower() for value in filters.get("include_keywords", [])]
        exclude = [str(value).lower() for value in filters.get("exclude_keywords", [])]
        if exclude and any(value in text for value in exclude):
            return 0.0
        matches = sum(1 for value in include if value and value in text)
        length_score = min(len(str(item.get("content") or "")) / 1000, 1.0)
        # No relevance match must not enter the briefing merely because a page
        # contains a lot of navigation text. Length contributes only after a
        # topical keyword is present.
        freshness_penalty = 0
        metadata = item.get("metadata") or {}
        if metadata.get("date_missing") and metadata.get("source_type") == "web":
            # A page without a publication date can still be useful, but it
            # must lose to dated sources instead of dominating the daily list.
            freshness_penalty = 12
        return max(0.0, min(100.0, matches * 18 + (length_score * 35 if matches else 0) - freshness_penalty))

    @staticmethod
    def _trim_at_boundary(text: str, max_chars: int, minimum_boundary: int = 0) -> str:
        """Bound generated prose without cutting the last sentence in half."""
        text = str(text or "").strip()
        if len(text) <= max_chars:
            return text
        head = text[:max_chars]
        marks = list(re.finditer(r"[。！？；.!?]", head))
        if marks and marks[-1].end() >= max(minimum_boundary, int(max_chars * 0.62)):
            return head[:marks[-1].end()]
        return head.rstrip(" ，、；：") + "……"

    @staticmethod
    def _fallback_summary(item: Dict[str, Any]) -> str:
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        title = re.sub(r"\s+", " ", str(item.get("title") or "该条内容")).strip()
        # A deterministic fallback must never invent facts.  For Chinese
        # sources we keep a longer evidence-bound excerpt; for non-Chinese
        # sources we clearly label it as an excerpt so the report is still
        # deliverable when the model is temporarily unavailable.
        cjk = len(re.findall(r"[\u3400-\u9fff]", content))
        excerpt = content[:300].rstrip(" ，。；、")
        if excerpt and cjk >= max(8, len(content) // 20):
            return excerpt + ("……" if len(content) > len(excerpt) else "")
        if excerpt:
            return f"来源《{title[:48]}》的可用正文摘录：{excerpt}。本段仅依据已抓取原文生成，未补充外部事实。"
        return f"来源《{title[:48]}》已通过本期筛选，但当前未获得可用正文。请打开来源链接查看原文；本期未添加外部事实。"

    @classmethod
    def _normalize_summary(cls, value: Any, item: Dict[str, Any]) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        # English-only or model meta answers are not acceptable in a Chinese
        # daily briefing; use the evidence-bound Chinese fallback instead.
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        if not text or (len(text) >= 30 and cjk < max(5, len(text) // 30)) or re.search(r"模型未|无法|I cannot|I can't", text, re.I):
            text = cls._fallback_summary(item)
        # Keep enough detail for a useful daily briefing.  The prompt targets
        # ~300 Chinese characters; this bound prevents runaway model output
        # while avoiding the previous 140-character truncation.
        return cls._trim_at_boundary(text, 360, 220)

    @staticmethod
    def _within_window(item: Dict[str, Any], hours: int) -> bool:
        value = str(item.get("published_at") or "").strip()
        if not value:
            return True
        parsed = None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return age <= max(1, hours) * 3600

    async def _summarize(self, items: list[Dict[str, Any]], skill: Dict[str, Any], run_id: str = "") -> tuple[list[Dict[str, Any]], Optional[str]]:
        if not items:
            return [], None
        evidence = []
        for index, item in enumerate(items, 1):
            evidence.append({"id": str(index), "title": item.get("title"), "url": item.get("url"), "content": str(item.get("content") or "")[:1600]})
        directives = self._skill_directives(skill)
        skill_body = directives["content"]
        if run_id:
            self._trace(run_id, "skill", "已读取并解析挂载 Skill 正文", skill_sha256=directives["sha256"], skill_chars=directives["length"])

        def parse_json(raw: str) -> Dict[str, Any]:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                return {}
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}

        # Small batches avoid context overflow and make the Skill usable on a
        # 2 GB server. The second call writes the cross-item conclusion.
        all_summaries = []
        for start in range(0, len(evidence), 5):
            batch = evidence[start:start + 5]
            prompt = (
                "你是事实编辑。只能使用 EVIDENCE 中的内容，Skill 只规定工作流、栏目和文风，不能补充事实。系统本次输出契约优先于 Skill 中可能存在的旧字数示例：每条摘要必须约 300 字，总述约 500 字。\n"
                "严格只返回 JSON：{\"items\":[{\"id\":\"1\",\"summary\":\"250-320字简体中文\",\"why_it_matters\":null,\"uncertainties\":[]}]}.\n"
                "每条 summary 必须是简体中文，目标 300 字左右；英文来源也要根据证据翻译为中文，不得输出英文原文、思维过程或模板话术。\n"
                "每个 id 必须对应输入；证据没有说明的内容必须写入 uncertainties，不能猜测、不能编造日期、公司、数字或第二来源。\n"
                f"MOUNTED_SKILL_INSTRUCTIONS（已从 {directives['length']} 字正文提取）：{skill_body}\n"
                f"EVIDENCE={json.dumps(batch, ensure_ascii=False)}"
            )
            generated = {}
            try:
                response = await agent_orchestrator._chat_completion(
                    [{"role": "system", "content": "你是 Knowledge Hub 的证据驱动日报 Agent，只输出中文结构化结果，不输出思维过程。"}, {"role": "user", "content": prompt}],
                    max_tokens=2400, temperature=0.1, timeout=120,
                )
                data = parse_json(response.choices[0].message.content if response.choices else "")
                generated = {str(row.get("id")): row for row in data.get("items", []) if isinstance(row, dict)}
                # Providers occasionally ignore JSON mode and return a plain
                # numbered list.  Make a second, inexpensive request instead
                # of silently degrading every item to an excerpt.
                def needs_chinese_retry(row: Dict[str, Any]) -> bool:
                    summary = re.sub(r"\s+", "", str(row.get("summary") or ""))
                    cjk_count = len(re.findall(r"[\u3400-\u9fff]", summary))
                    # A valid daily item should contain meaningful Chinese,
                    # not merely a model preamble or an English translation
                    # request acknowledgement.
                    return len(summary) < 120 or cjk_count < max(12, len(summary) // 12)

                if len(generated) < len(batch) or any(needs_chinese_retry(row) for row in generated.values()):
                    plain_prompt = (
                        "请为 EVIDENCE 中每条内容各写一段 250-320 字的简体中文摘要。即使标题和正文是英文，也必须先理解后用自然、完整的简体中文重写；不得保留英文句子（专有名词、产品名、URL 和必要缩写除外）。\n"
                        "严格按以下格式逐行输出：1. 摘要文本；2. 摘要文本……不要标题、Markdown、引用编号或思维过程。\n"
                        "只能使用给出的证据，不能补充外部事实；证据不足时明确写‘原文未说明’。\n"
                        f"EVIDENCE={json.dumps(batch, ensure_ascii=False)}"
                    )
                    retry = await agent_orchestrator._chat_completion(
                        [{"role": "system", "content": "你是中文事实编辑，只输出逐条摘要，不展示思维过程。"}, {"role": "user", "content": plain_prompt}],
                        max_tokens=2400, temperature=0.1, timeout=120,
                    )
                    raw_plain = retry.choices[0].message.content if retry.choices else ""
                    plain_text = re.sub(r"\r\n?", "\n", str(raw_plain or "")).strip()
                    matches = list(re.finditer(r"(?:^|\n)\s*(\d+)\s*[.、:：)）-]\s*", plain_text))
                    if matches:
                        lines = []
                        for pos, match in enumerate(matches):
                            end = matches[pos + 1].start() if pos + 1 < len(matches) else len(plain_text)
                            lines.append(plain_text[match.end():end].strip())
                    else:
                        lines = [line.strip() for line in plain_text.splitlines()]
                    lines = [line for line in lines if len(line) >= 20]
                    for idx, line in enumerate(lines[:len(batch)], 1):
                        generated.setdefault(str(idx), {"id": str(idx), "summary": line, "uncertainties": []})
            except Exception as exc:
                logger.warning("被动日报批次模型生成失败: %s", exc)
                if run_id:
                    self._trace(run_id, "summarize", "逐条摘要模型调用失败，使用证据截断降级", error=str(exc)[:240], batch_start=start + 1)
            if run_id and generated:
                self._trace(run_id, "summarize", "已完成一批逐条摘要", batch_start=start + 1, batch_size=len(batch), returned=len(generated))
            for offset, evidence_item in enumerate(batch):
                item = items[start + offset]
                # Evidence IDs are global across the report. Accept both the
                # documented global id and a provider's local 1..5 numbering
                # so later batches never silently degrade to fallback text.
                row = generated.get(str(evidence_item.get("id"))) or generated.get(str(offset + 1), {})
                item["summary"] = self._normalize_summary(row.get("summary"), item)
                item["why_it_matters"] = row.get("why_it_matters")
                item["uncertainties"] = row.get("uncertainties") or ([] if row else ["模型未返回结构化摘要，仅展示清洗后的来源内容"])
                all_summaries.append({"id": str(start + offset + 1), "title": item.get("title"), "summary": item["summary"], "source": item.get("source_name")})

        conclusion = None
        try:
            response = await agent_orchestrator._chat_completion(
                [{"role": "system", "content": "你是日报主编，只输出 JSON，不要输出思维过程。"}, {"role": "user", "content": "根据已生成的逐条摘要，严格只返回 JSON：{\"conclusion\":\"约500字简体中文总述\"}。总述需包含本期主题、主要动态、共同趋势和不确定性；不得引入摘要之外的事实。\n摘要=" + json.dumps(all_summaries, ensure_ascii=False)}],
                max_tokens=900, temperature=0.1, timeout=75,
            )
            raw_conclusion = response.choices[0].message.content if response.choices else ""
            conclusion = self._trim_at_boundary(str(parse_json(raw_conclusion).get("conclusion") or ""), 700, 420) or None
            if not conclusion:
                # Accept a plain-text conclusion from providers that do not
                # honor JSON-only instructions, while still bounding length.
                plain = re.sub(r"^```[\s\S]*?```$", "", str(raw_conclusion or "").strip()).strip()
                if plain and len(plain) >= 40 and not re.search(r"模型未|资料不足|无法生成", plain):
                    conclusion = self._trim_at_boundary(plain, 700, 420)
            if run_id and conclusion:
                self._trace(run_id, "synthesize", "已根据逐条摘要生成日报结论")
        except Exception as exc:
            logger.warning("被动日报结论生成失败: %s", exc)
            if run_id:
                self._trace(run_id, "synthesize", "结论模型调用失败，使用确定性降级结论", error=str(exc)[:240])
        if not conclusion and all_summaries:
            # Never leave an apparently empty report when the model is
            # temporarily unavailable. This is a deterministic compression of
            # already captured evidence, not an invented external statement.
            titles = [str(row.get("title") or "").strip() for row in all_summaries[:3] if row.get("title")]
            conclusion = "本期围绕 " + "、".join(titles) + " 汇总了已抓取且通过质量筛选的内容；详情以各条来源和原文链接为准。"
        return items, conclusion

    @staticmethod
    def _render(skill: Dict[str, Any], run: Dict[str, Any], items: list[Dict[str, Any]], conclusion: Optional[str]) -> tuple[str, str]:
        date_label = run.get("logical_date", datetime.now().date().isoformat())
        lines = [f"# AI 热点日报｜{date_label}", "", f"> Skill：{skill.get('name')} v{skill.get('version')}", f"> 抓取时间：{run.get('started_at')}", "", "## 今日结论", "", conclusion or "今日未生成模型结论，以下为经过清洗的真实来源清单。", "", "## 热点列表", ""]
        for index, item in enumerate(items, 1):
            lines.extend([f"### {index}. {item.get('title') or '未命名内容'}", "", str(item.get("summary") or ""), "", f"- 来源：[{item.get('source_name')}]({item.get('url')})", f"- 抓取时间：{item.get('captured_at')}", f"- 质量分：{item.get('quality_score', 0):.1f}", ""])
            if item.get("why_it_matters"):
                lines.extend([f"- 影响：{item['why_it_matters']}", ""])
        lines.extend(["## 数据边界", "", f"本期候选 {run.get('candidate_count', 0)} 条，入选 {len(items)} 条；成功来源 {run.get('source_success', 0)}/{run.get('source_count', 0)}。来源失败、内容不完整或未通过质量阈值的项目未进入正文。", ""])
        markdown = "\n".join(lines)
        html_body = "<h1>AI 热点日报｜{}</h1><p>{}</p>".format(html_lib.escape(date_label), html_lib.escape(conclusion or "今日未生成模型结论"))
        html_body += "<h2>热点列表</h2><ol>" + "".join(f"<li><strong>{html_lib.escape(str(item.get('title') or '未命名内容'))}</strong><p>{html_lib.escape(str(item.get('summary') or ''))}</p><a href='{html_lib.escape(str(item.get('url') or ''), quote=True)}'>查看来源</a></li>" for item in items) + "</ol>"
        return markdown + "\n", f"<html><body style='font-family:Arial,sans-serif;max-width:760px;margin:auto;padding:20px'>{html_body}</body></html>"

    async def start_run(self, skill_id: str, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
        skill = self.registry.get(skill_id)
        if not skill or skill.get("status") == "archived":
            raise KeyError(skill_id)
        try:
            skill_tz = ZoneInfo(str((skill.get("schedule") or {}).get("timezone") or "Asia/Shanghai"))
        except Exception:
            skill_tz = timezone.utc
        logical_date = datetime.now(timezone.utc).astimezone(skill_tz).date().isoformat()
        idem = hashlib.sha256(f"{skill_id}:{skill.get('version')}:{logical_date}".encode()).hexdigest()[:24]
        if not force:
            for row in self._runs.values():
                if row.get("idempotency_key") == idem and row.get("status") in {"running", "completed", "partial"}:
                    return row
        run_id = f"passive-{uuid.uuid4().hex}"
        run = {"run_id": run_id, "skill_id": skill_id, "skill_version": skill.get("version"), "logical_date": logical_date, "dry_run": dry_run, "idempotency_key": idem, "status": "running", "stage": "collecting", "created_at": datetime.now(timezone.utc).isoformat(), "started_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(), "trace": []}
        self._runs[run_id] = run
        self._save_runs()
        telemetry.record("passive_run_started", source="passive", run_id=run_id,
                         properties={"skill_id": skill_id, "skill_version": skill.get("version"), "dry_run": dry_run})
        self._trace(run_id, "perception", "已选择启用的被动摄入 Skill", skill_id=skill_id, version=skill.get("version"), dry_run=dry_run)
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id, skill))
        return run

    async def _execute(self, run_id: str, skill: Dict[str, Any]) -> None:
        run = self._runs[run_id]
        started = time.monotonic()
        try:
            async with self._run_semaphore:
                limits = skill.get("limits") or {}
                sources = skill.get("sources") or []
                collected = await collect_sources(sources, max_sources=int(limits.get("max_sources", 20)), timeout=min(30, float(limits.get("source_timeout_seconds", 15))))
                self._trace(run_id, "collect", "来源采集完成", source_count=collected.get("source_count", 0), success_count=collected.get("success_count", 0), item_count=len(collected.get("items", [])))
                self._update(run_id, stage="processing", source_count=collected.get("source_count", 0), source_success=collected.get("success_count", 0), source_failures=collected.get("failures", []), candidate_count=len(collected.get("items", [])))
                filters = skill.get("filters") or {}
                candidates = []
                seen_content: set[str] = set()
                lookback_hours = int((skill.get("schedule") or {}).get("lookback_hours", 24) or 24)
                for item in collected.get("items", [])[:int(limits.get("max_candidates", 100))]:
                    if not self._within_window(item, lookback_hours):
                        continue
                    item["quality_score"] = self._score(item, filters)
                    if item["quality_score"] >= float(filters.get("min_quality_score", 45)):
                        # Different feeds frequently syndicate the same story
                        # under different URLs. De-duplicate on normalized
                        # title + leading body before ranking so one story
                        # cannot consume the daily quota multiple times.
                        fingerprint = hashlib.sha256(
                            re.sub(r"\W+", "", f"{item.get('title','')} {str(item.get('content',''))[:1200]}".lower()).encode("utf-8", errors="ignore")
                        ).hexdigest()
                        if fingerprint in seen_content:
                            continue
                        seen_content.add(fingerprint)
                        candidates.append(item)
                candidates.sort(key=lambda value: value.get("quality_score", 0), reverse=True)
                items = candidates[:int((skill.get("output") or {}).get("max_items", 10))]
                self._trace(run_id, "process", "已完成时间窗、关键词、质量评分和去重", candidate_count=len(candidates), selected_count=len(items))
                self._update(run_id, stage="writing", selected_count=len(items))
                items, conclusion = await self._summarize(items, skill, run_id)
                telemetry.record("briefing_summary_generated", source="passive", run_id=run_id,
                                 properties={"skill_id": skill.get("skill_id"), "item_count": len(items),
                                             "has_conclusion": bool(conclusion), "model_degraded": any(bool(item.get("uncertainties")) for item in items)})
                self._update(run_id, stage="saving")
                output_dir = self.root / skill["skill_id"] / run["logical_date"]
                output_dir.mkdir(parents=True, exist_ok=True)
                markdown, html_body = self._render(skill, run, items, conclusion)
                md_path = output_dir / "briefing.md"
                html_path = output_dir / "briefing.html"
                sources_path = output_dir / "sources.json"
                md_path.write_text(markdown, encoding="utf-8")
                html_path.write_text(html_body, encoding="utf-8")
                sources_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
                delivery = skill.get("delivery") or {}
                recipients = list(delivery.get("recipients") or [])
                if not recipients and delivery.get("email"):
                    recipients = [str(delivery.get("email"))]
                if not recipients and delivery.get("use_default_recipient", True):
                    # Convenient default for the single-user workbench: after
                    # SMTP is configured, a scheduled/manual run goes to the
                    # configured sender address unless the Skill specifies a
                    # separate recipient list.
                    mail_config = email_notification_service.get_config()
                    default_recipient = mail_config.get("from_address") or mail_config.get("smtp_user")
                    if default_recipient:
                        recipients = [str(default_recipient)]
                email_status = {"status": "skipped", "reason": "dry_run" if run.get("dry_run") else "未配置收件人"}
                if recipients and not run.get("dry_run"):
                    email_status = await asyncio.to_thread(email_notification_service.queue_briefing,
                        title=f"AI 热点日报｜{run['logical_date']}", markdown=markdown, html=html_body, recipients=recipients,
                        idempotency_key=run["idempotency_key"],
                    )
                    self._trace(run_id, "delivery", "日报已进入真实 SMTP 邮件发送流程", recipients=recipients, status=email_status.get("status"))
                    telemetry.record("email_sent" if email_status.get("status") == "sent" else "email_send_failed",
                                     source="passive", run_id=run_id,
                                     properties={"status": email_status.get("status"), "recipient_count": len(recipients),
                                                 "error_type": email_status.get("error", "")[:120] if email_status.get("error") else ""})
                elif run.get("dry_run"):
                    self._trace(run_id, "delivery", "试跑模式：已生成邮件内容但未发送", recipients=recipients)
                else:
                    self._trace(run_id, "delivery", "未发送邮件：未配置收件人或通知未启用")
                if not recipients or run.get("dry_run"):
                    telemetry.record("email_send_skipped", source="passive", run_id=run_id,
                                     properties={"reason": email_status.get("reason", "dry_run" if run.get("dry_run") else "no_recipient"),
                                                 "dry_run": bool(run.get("dry_run"))})
                status = "completed" if items else "partial"
                self._update(run_id, status=status, stage="completed", item_count=len(items), artifact_path=str(md_path), html_artifact_path=str(html_path), sources_path=str(sources_path), email_status=email_status, finished_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic() - started, 2))
                telemetry.record("passive_run_completed", source="passive", run_id=run_id,
                                 properties={"skill_id": skill.get("skill_id"), "status": status,
                                             "item_count": len(items), "email_status": email_status.get("status"),
                                             "duration_ms": round((time.monotonic() - started) * 1000, 1)})
        except Exception as exc:
            logger.exception("被动日报任务失败 run_id=%s", run_id)
            self._update(run_id, status="failed", stage="failed", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic() - started, 2))
            telemetry.record("passive_run_failed", source="passive", run_id=run_id,
                             properties={"skill_id": skill.get("skill_id"), "error_type": type(exc).__name__, "error": str(exc)[:180]})
        finally:
            self._tasks.pop(run_id, None)

    @staticmethod
    def _cron_due(cron: str, now: datetime) -> bool:
        parts = str(cron or "").split()
        if len(parts) != 5:
            return False
        minute, hour = parts[0], parts[1]
        def match(value: int, expression: str, maximum: int) -> bool:
            if expression == "*": return True
            values = set()
            for part in expression.split(","):
                part = part.strip()
                if part.startswith("*/"):
                    try:
                        step = max(1, int(part[2:]))
                        values.update(range(0, maximum + 1, step))
                    except ValueError:
                        return False
                elif "-" in part:
                    try:
                        left, right = [int(v) for v in part.split("-", 1)]
                        values.update(range(left, right + 1))
                    except ValueError:
                        return False
                else:
                    try:
                        values.add(int(part))
                    except ValueError:
                        return False
            return value in values
        return match(now.minute, minute, 59) and match(now.hour, hour, 23)

    async def start_scheduler(self) -> None:
        if self._scheduler_task or not getattr(settings, "PASSIVE_INGEST_ENABLED", True):
            return
        async def loop() -> None:
            last_ticks: Dict[str, str] = {}
            while True:
                if getattr(settings, "PASSIVE_WEEKLY_REVIEW_ENABLED", True):
                    weekly_tz = ZoneInfo("Asia/Shanghai")
                    weekly_now = datetime.now(timezone.utc).astimezone(weekly_tz)
                    weekly_tick = weekly_now.strftime("%Y-%m-%d %H:%M")
                    if self._cron_due(getattr(settings, "PASSIVE_WEEKLY_REVIEW_CRON", "0 8 * * 1"), weekly_now) and weekly_tick != last_ticks.get("__weekly_review__"):
                        try:
                            await self.start_weekly_review()
                        except Exception as exc:
                            logger.warning("调度每周主题综述失败: %s", exc)
                        last_ticks["__weekly_review__"] = weekly_tick
                for skill in self.registry.list():
                    if skill.get("status") != "enabled":
                        continue
                    schedule = skill.get("schedule") or {}
                    try:
                        tz = ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
                    except Exception:
                        tz = timezone.utc
                    now = datetime.now(timezone.utc).astimezone(tz)
                    tick = now.strftime("%Y-%m-%d %H:%M")
                    skill_key = str(skill.get("skill_id"))
                    if self._cron_due(schedule.get("cron", ""), now) and tick != last_ticks.get(skill_key):
                        try:
                            await self.start_run(skill["skill_id"])
                        except Exception as exc:
                            logger.warning("调度 Skill 失败 %s: %s", skill.get("skill_id"), exc)
                    if self._cron_due(schedule.get("cron", ""), now):
                        last_ticks[skill_key] = tick
                await asyncio.sleep(30)
        self._scheduler_task = asyncio.create_task(loop())

    async def stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

    async def start_weekly_review(self, force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """Generate a weekly topic review from recently added file resources.

        This is intentionally separate from web-hotspot Skills: the source
        boundary is the user's file-resource tree and the output is a single
        evidence-bound Chinese review of roughly 800 characters.
        """
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(timezone.utc).astimezone(tz)
        week_key = now.strftime("%G-W%V")
        idem = hashlib.sha256(f"weekly-review:{week_key}".encode()).hexdigest()[:24]
        if not force:
            for row in self._runs.values():
                if row.get("idempotency_key") == idem and row.get("status") in {"running", "completed", "partial"}:
                    return row
        run_id = f"weekly-{uuid.uuid4().hex}"
        run = {"run_id": run_id, "skill_id": "weekly-review", "skill_version": "1.0.0",
               "logical_date": now.date().isoformat(), "week_key": week_key, "dry_run": dry_run,
               "idempotency_key": idem, "status": "running", "stage": "collecting",
               "created_at": datetime.now(timezone.utc).isoformat(), "started_at": datetime.now(timezone.utc).isoformat(), "trace": []}
        self._runs[run_id] = run; self._save_runs()
        self._tasks[run_id] = asyncio.create_task(self._execute_weekly_review(run_id, run))
        return run

    async def _execute_weekly_review(self, run_id: str, run: Dict[str, Any]) -> None:
        started = time.monotonic()
        try:
            tz = ZoneInfo("Asia/Shanghai")
            now = datetime.now(timezone.utc).astimezone(tz)
            cutoff = now - timedelta(days=max(1, int(getattr(settings, "PASSIVE_WEEKLY_REVIEW_LOOKBACK_DAYS", 7))))
            base = Path(settings.DATA_DIR) / "file_resources"
            files = []
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
                    continue
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz)
                if modified < cutoff or "agent-results" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    files.append({"path": path.relative_to(base).as_posix(), "title": path.stem, "content": text[:5000], "updated_at": modified.isoformat()})
            files.sort(key=lambda x: x["updated_at"], reverse=True)
            files = files[:max(1, int(getattr(settings, "PASSIVE_WEEKLY_REVIEW_MAX_FILES", 30)))]
            self._update(run_id, stage="writing", source_count=len(files))
            model_degraded = False
            if files:
                prompt = (
                    "你是 Knowledge Hub 的周度主题综述主编。请严格只使用 ARTICLES 中提供的文章内容，不能调用外部知识，不能把单篇观点写成事实共识。\n"
                    "先在内部识别 2-5 个跨文章主题，再只输出最终 Markdown，不要输出思维过程。\n"
                    "输出结构必须是：# 本周主题综述；## 总览；## 主题一/主题二（按实际主题数量）；## 共同趋势与分歧；## 可执行行动；## 资料边界。\n"
                    "总览和正文合计约 800 个中文字符（允许 650-950 字），每个主题必须标注涉及的文章文件名；没有证据的内容写‘原文未说明’，不要编造数字、日期、因果或第二来源。\n"
                    f"时间范围：{cutoff.date().isoformat()} 至 {now.date().isoformat()}（Asia/Shanghai）\n"
                    f"ARTICLES={json.dumps(files, ensure_ascii=False)}"
                )
                try:
                    response = await agent_orchestrator._chat_completion(
                        [{"role": "system", "content": "你是证据驱动的中文周报编辑，只输出正式 Markdown。"}, {"role": "user", "content": prompt}],
                        max_tokens=1800, temperature=0.1, timeout=120)
                    review = (response.choices[0].message.content if response.choices else "").strip()
                except Exception as exc:
                    model_degraded = True
                    logger.warning("每周主题综述模型调用失败，使用证据边界降级: %s", exc)
                    review = "# 本周主题综述\n\n## 总览\n\n本期模型暂不可用，以下仅整理最近文件资源中的可见标题和正文摘录，未补充外部事实。\n\n"
                    for index, item in enumerate(files[:8], 1):
                        excerpt = re.sub(r"\s+", " ", item["content"]).strip()[:180]
                        review += f"## 资料 {index}：{item['title']}\n\n{excerpt}\n\n"
            else:
                review = "# 本周主题综述\n\n## 总览\n\n本周时间范围内没有发现新增且可用的文件资源。\n\n## 资料边界\n\n本期未调用外部来源，也未补充外部事实。"
            if len(review) < 120:
                review += "\n\n## 资料边界\n\n以上内容仅依据本周文件资源生成；未提供的事实不作推断。"
            output_dir = self.root / "weekly-review" / run["week_key"]; output_dir.mkdir(parents=True, exist_ok=True)
            md_path = output_dir / "briefing.md"; md_path.write_text(review + "\n", encoding="utf-8")
            resource_copy = Path(settings.DATA_DIR) / "file_resources" / "weekly-reviews" / f"{run['week_key']}.md"
            resource_copy.parent.mkdir(parents=True, exist_ok=True)
            resource_copy.write_text(review + "\n", encoding="utf-8")
            recipients = [x.strip() for x in str(getattr(settings, "PASSIVE_WEEKLY_REVIEW_RECIPIENTS", "") or "").split(",") if x.strip()]
            if not recipients:
                cfg = email_notification_service.get_config(); default = cfg.get("from_address") or cfg.get("smtp_user")
                if default: recipients = [str(default)]
            email_status = {"status": "skipped", "reason": "dry_run" if run.get("dry_run") else "未配置收件人"}
            if recipients and not run.get("dry_run"):
                email_status = await asyncio.to_thread(email_notification_service.queue_briefing, f"知识库每周主题综述｜{run['week_key']}", review, f"<html><body><pre style='white-space:pre-wrap;font-family:Arial'>{html_lib.escape(review)}</pre></body></html>", recipients, idem)
            self._update(run_id, status="completed", stage="completed", item_count=len(files), artifact_path=str(md_path), resource_path=str(resource_copy), email_status=email_status, finished_at=datetime.now(timezone.utc).isoformat(), duration_seconds=round(time.monotonic() - started, 2))
            telemetry.record("weekly_review_completed", source="passive", run_id=run_id, properties={"week_key": run["week_key"], "source_count": len(files), "email_status": email_status.get("status"), "review_chars": len(review), "model_degraded": model_degraded})
        except Exception as exc:
            self._update(run_id, status="failed", stage="failed", error=str(exc)[:1000], finished_at=datetime.now(timezone.utc).isoformat())
            telemetry.record("weekly_review_failed", source="passive", run_id=run_id, properties={"error_type": type(exc).__name__})
        finally:
            self._tasks.pop(run_id, None)


passive_service = PassiveBriefingService()
