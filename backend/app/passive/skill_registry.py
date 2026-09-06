from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.core.config import settings


class SkillRegistry:
    """声明式 Skill 注册表。

    只保存 Markdown 和 JSON 配置，不执行 Skill 中的脚本。这样前端可以
    安装/卸载/调整 Skill，同时 Agent 只能使用后端白名单工具。
    """

    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or Path(settings.DATA_DIR) / "passive_skills")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._lock = threading.RLock()
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._index = data
        except (FileNotFoundError, json.JSONDecodeError):
            self._index = {}
        # Repair manifests created by the first MVP, which incorrectly
        # treated every example URL in ai-daily-briefing/SKILL.md as a source.
        builtin = self._index.get("ai-daily-briefing")
        if isinstance(builtin, dict) and len(builtin.get("sources") or []) > 16:
            builtin["sources"] = self._default_sources({"skill_id": "ai-daily-briefing"})
            self._save()
        # Repair manifests created before trigger extraction was scoped to the
        # explicit "When to Use" section.
        changed = False
        for item in self._index.values():
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            try:
                body = Path(path).read_text(encoding="utf-8") if path else ""
            except OSError:
                body = ""
            parsed = self.parse_markdown(body, "SKILL.md") if body else None
            if parsed and parsed.get("triggers") and parsed.get("triggers") != item.get("triggers"):
                item["triggers"] = parsed["triggers"]
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    @staticmethod
    def _slug(value: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+", "-", value.strip().lower()).strip("-")
        return value[:80] or "skill"

    @staticmethod
    def parse_markdown(content: str, filename: str = "SKILL.md") -> Dict[str, Any]:
        text = str(content or "")
        frontmatter: Dict[str, str] = {}
        match = re.match(r"^\s*---\s*\n([\s\S]*?)\n---\s*(?:\n|$)", text)
        body = text
        if match:
            body = text[match.end():]
            current_key = ""
            for line in match.group(1).splitlines():
                if not line.strip():
                    continue
                item = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
                if item:
                    current_key = item.group(1)
                    frontmatter[current_key] = item.group(2).strip().strip('"\'')
                elif current_key and line.startswith((" ", "\t")):
                    frontmatter[current_key] = (frontmatter.get(current_key, "") + " " + line.strip()).strip()
        name = frontmatter.get("name") or Path(filename).stem or "未命名 Skill"
        description = frontmatter.get("description") or ""
        skill_id = SkillRegistry._slug(frontmatter.get("skill_id") or name)
        # Only phrases explicitly listed in the "When to Use" section are
        # triggers. Scanning every quoted string used to register URLs,
        # filenames and error messages as accidental intents.
        triggers = []
        when_match = re.search(r"(?im)^#{1,6}\s*When to Use\s*$([\s\S]*?)(?=^#{1,6}\s+|\Z)", body)
        trigger_text = when_match.group(1) if when_match else ""
        for line in trigger_text.splitlines():
            line = line.strip()
            if not line.startswith(("-", "*")):
                continue
            line = re.sub(r"^[-*]\s*", "", line)
            # A line may contain alternatives separated by slash; keep each
            # concise phrase and strip Markdown quoting.
            for value in re.split(r"\s*/\s*|\s*或\s*", line):
                value = re.sub(r"[`\"“”']", "", value).strip()
                if 2 <= len(value) <= 80 and value not in triggers:
                    triggers.append(value)
        urls = []
        for value in re.findall(r"https?://[^\s)\]>`]+", body):
            value = value.rstrip("。，、；;。\"'")
            if value not in urls:
                urls.append(value)
        return {
            "skill_id": skill_id,
            "name": name,
            "description": re.sub(r"\s+", " ", description).strip()[:1000],
            "filename": filename,
            "body": body,
            "frontmatter": frontmatter,
            "triggers": triggers[:30],
            "discovered_urls": urls[:100],
        }

    @staticmethod
    def _default_sources(parsed: Dict[str, Any]) -> list[Dict[str, Any]]:
        skill_id = parsed.get("skill_id", "")
        if skill_id == "ai-daily-briefing":
            # The Markdown contains example URLs (README endpoints, output
            # paths, placeholder repos and article links). They are not source
            # declarations. Keep a curated source set for this built-in Skill;
            # users can edit it from the UI after mounting.
            curated = [
                ("github-trending", "GitHub Trending", "github_trending", "https://github.com/trending?since=daily", 1),
                ("github-search", "GitHub Search API", "github_search", "https://api.github.com/search/repositories?q=ai+OR+llm+OR+agent+OR+rag&sort=stars&order=desc&per_page=20", 2),
                ("openai", "OpenAI", "web", "https://openai.com/index/blog/", 3),
                ("anthropic", "Anthropic", "web", "https://www.anthropic.com/blog", 3),
                ("google-ai", "Google AI", "web", "https://blog.google/technology/ai/", 3),
                ("microsoft", "Microsoft", "web", "https://news.microsoft.com/source/", 3),
                ("meta-ai", "Meta AI", "web", "https://ai.meta.com/blog/", 3),
                ("aws-ml", "AWS Machine Learning", "web", "https://aws.amazon.com/blogs/machine-learning/", 3),
                ("nvidia-ai", "NVIDIA AI", "web", "https://blogs.nvidia.com/category/ai/", 3),
                ("xai", "xAI", "web", "https://x.ai/blog", 3),
                ("mistral", "Mistral AI", "web", "https://mistral.ai/news/", 3),
                ("huggingface", "Hugging Face", "web", "https://huggingface.co/blog", 3),
                ("zhipu", "智谱 AI", "web", "https://www.zhipuai.cn/", 3),
                ("deepseek", "DeepSeek", "web", "https://www.deepseek.com/", 3),
                ("minimax", "MiniMax", "web", "https://www.minimaxi.com/", 3),
                ("moonshot", "月之暗面", "web", "https://github.com/MoonshotAI/", 3),
            ]
            return [{"id": item[0], "name": item[1], "type": item[2], "url": item[3], "enabled": True, "priority": item[4]} for item in curated]
        urls = list(parsed.get("discovered_urls") or [])
        sources: list[Dict[str, Any]] = []
        if skill_id == "ai-daily-briefing":
            sources.extend([
                {"id": "github-trending", "name": "GitHub Trending", "type": "github_trending", "url": "https://github.com/trending?since=daily", "enabled": True, "priority": 1},
                {"id": "github-search", "name": "GitHub Search API", "type": "github_search", "url": "https://api.github.com/search/repositories?q=ai+OR+llm+OR+agent+OR+rag&sort=stars&order=desc&per_page=20", "enabled": True, "priority": 2},
            ])
        for index, url in enumerate(urls):
            host = urlparse(url).netloc.lower()
            if not host or "api.github.com/search" in url:
                continue
            if any(existing.get("url") == url for existing in sources):
                continue
            source_type = "rss" if any(token in url.lower() for token in ("feed", "rss", "atom")) else "web"
            sources.append({"id": f"source-{index + 1}", "name": host, "type": source_type, "url": url, "enabled": True, "priority": 3})
        return sources[:40]

    def install(self, content: str, filename: str = "SKILL.md", config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        parsed = self.parse_markdown(content, filename)
        skill_id = parsed["skill_id"]
        with self._lock:
            existing = self._index.get(skill_id, {})
            version_no = int(existing.get("version_no", 0)) + 1
            version = f"1.0.{version_no}"
            version_dir = self.root / skill_id / version
            version_dir.mkdir(parents=True, exist_ok=True)
            (version_dir / "SKILL.md").write_text(content, encoding="utf-8")
            user_config = config if isinstance(config, dict) else {}
            manifest = {
                "skill_id": skill_id,
                "name": parsed["name"],
                "description": parsed["description"],
                "version": version,
                "version_no": version_no,
                "status": "disabled",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "path": str(version_dir / "SKILL.md"),
                "triggers": parsed["triggers"],
                "sources": user_config.get("sources") or self._default_sources(parsed),
                "schedule": user_config.get("schedule") or {"timezone": "Asia/Shanghai", "cron": "0 8 * * *", "lookback_hours": 24},
                "filters": user_config.get("filters") or {"include_keywords": ["AI", "Agent", "RAG", "大模型"], "exclude_keywords": ["招聘", "广告"]},
                "output": user_config.get("output") or {"format": "markdown_and_html", "max_items": 10},
                "delivery": user_config.get("delivery") or {"channel": "qq_email", "recipients": []},
                "limits": user_config.get("limits") or {"max_sources": 20, "max_candidates": 100, "max_model_calls": 4, "max_run_seconds": 300},
                "workflow": user_config.get("workflow") or {"clean": True, "deduplicate": True, "cluster": True, "summarize_each": True, "synthesize": True, "validate_evidence": True},
                "allowed_tools": user_config.get("allowed_tools") or [],
                "output_contract": user_config.get("output_contract") or {},
                "body_excerpt": parsed["body"][:1200],
                "versions": list(existing.get("versions") or []) + [{"version": version, "path": str(version_dir / "SKILL.md"), "created_at": datetime.now(timezone.utc).isoformat()}],
            }
            self._index[skill_id] = manifest
            self._save()
            return self._public(manifest)

    def _public(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        result.pop("body_excerpt", None)
        result.pop("path", None)
        return result

    def list(self) -> list[Dict[str, Any]]:
        with self._lock:
            # Archived Skills remain in the registry for audit/history, but
            # must not appear in the active management list after uninstall.
            return [self._public(item) for item in self._index.values() if item.get("status") != "archived"]

    def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._index.get(skill_id)
            return dict(item) if item else None

    def enable(self, skill_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            item = self._index.get(skill_id)
            if not item:
                raise KeyError(skill_id)
            if version and version != item.get("version"):
                matches = [v for v in item.get("versions", []) if v.get("version") == version]
                if not matches:
                    raise KeyError(version)
                item["version"] = version
                item["path"] = matches[0]["path"]
            item["status"] = "enabled"
            item["enabled_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return self._public(item)

    def disable(self, skill_id: str) -> Dict[str, Any]:
        with self._lock:
            item = self._index.get(skill_id)
            if not item:
                raise KeyError(skill_id)
            item["status"] = "disabled"
            item["disabled_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return self._public(item)

    def update_config(self, skill_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"schedule", "sources", "filters", "output", "delivery", "limits", "workflow", "triggers", "allowed_tools", "output_contract"}
        with self._lock:
            item = self._index.get(skill_id)
            if not item:
                raise KeyError(skill_id)
            merged = {key: item.get(key) for key in allowed if item.get(key) is not None}
            for key, value in (patch or {}).items():
                if key in allowed and isinstance(value, (dict, list)):
                    merged[key] = value
            # Configuration edits are immutable versions too. Carry the
            # current enabled/disabled state to the new version.
            body = self.read_body(skill_id)
            previous_status = item.get("status", "disabled")
            self.install(body or f"---\nname: {item.get('name', skill_id)}\n---\n", "SKILL.md", merged)
            self._index[skill_id]["status"] = previous_status
            self._index[skill_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return self._public(self._index[skill_id])

    def uninstall(self, skill_id: str) -> bool:
        with self._lock:
            item = self._index.get(skill_id)
            if not item:
                return False
            # Mounted Skills are live automation dependencies. Keep the
            # invariant in the registry itself as well as in the HTTP route,
            # so an internal caller cannot accidentally remove an enabled
            # Skill. Disable first, then explicitly uninstall.
            if item.get("status") == "enabled":
                raise ValueError("已挂载 Skill 不能直接删除，请先停用后再卸载")
            item["status"] = "archived"
            item["archived_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return True

    def read_body(self, skill_id: str) -> str:
        item = self.get(skill_id)
        if not item:
            return ""
        try:
            return Path(item["path"]).read_text(encoding="utf-8")
        except OSError:
            return ""

    def match_enabled(self, goal: str) -> Optional[Dict[str, Any]]:
        text = str(goal or "").lower()
        with self._lock:
            candidates = [item for item in self._index.values() if item.get("status") == "enabled"]
            for item in candidates:
                terms = [item.get("skill_id", ""), item.get("name", "")] + list(item.get("triggers") or [])
                if any(term and str(term).lower() in text for term in terms):
                    return dict(item)
        return None


skill_registry = SkillRegistry()
