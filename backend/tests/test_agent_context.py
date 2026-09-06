import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class TestAgentResourceScope(unittest.TestCase):
    def test_extracts_folder_from_chinese_query(self):
        from app.agent.context import extract_resource_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "自动抓取" / "2026-08-30").mkdir(parents=True)
            result = extract_resource_path(
                "帮我总结一下自动抓取/2026-08-30的文章，形成摘要", root
            )
            self.assertEqual(result, "自动抓取/2026-08-30")

    def test_perception_turns_explicit_folder_into_hard_scope(self):
        from app.agent.orchestrator import agent_orchestrator

        base = agent_orchestrator.runtime  # keep import/runtime initialization explicit
        del base
        original = agent_orchestrator  # avoid mocking the global singleton
        payload = SimpleNamespace(
            scope=None,
            web_search_enabled=False,
            kb_ids=None,
            kb_id="",
            template="research",
            output_format="markdown",
            selected_paths=None,
            date_range=None,
        )
        # The real date folder is present in the checked-out fixture data.
        perception = original.perceive(
            "帮我总结一下自动抓取/2026-08-30的文章，形成摘要", payload
        )
        self.assertEqual(perception["constraints"]["selected_paths"], ["自动抓取/2026-08-30"])
        self.assertEqual(perception["constraints"]["scope"], ["file_resources"])

    def test_explicit_scope_is_not_overridden(self):
        from app.agent.orchestrator import agent_orchestrator

        payload = SimpleNamespace(
            scope=["file_resources", "knowledge_bases"],
            web_search_enabled=False,
            kb_ids=["kb-test"],
            kb_id="",
            template="research",
            output_format="markdown",
            selected_paths=None,
            date_range=None,
        )
        perception = agent_orchestrator.perceive(
            "总结自动抓取/2026-08-30的文章", payload
        )
        self.assertEqual(
            perception["constraints"]["scope"], ["file_resources", "knowledge_bases"]
        )
        self.assertEqual(perception["constraints"]["selected_paths"], ["自动抓取/2026-08-30"])

    def test_selected_file_is_single_document_boundary(self):
        from app.agent.context import ContextResolver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "自动抓取" / "2026-08-30"
            folder.mkdir(parents=True)
            target = folder / "目标文章.md"
            unrelated = folder / "无关文章.md"
            target.write_text("Claude Code 的额度方案和评论区反应", encoding="utf-8")
            unrelated.write_text("亚航客机起落架舱发现尸体", encoding="utf-8")

            resolver = ContextResolver()
            resolver.base = root
            result = __import__("asyncio").run(
                resolver.resolve(
                    "帮我总结这篇文章",
                    scope=["file_resources"],
                    selected_paths=["自动抓取/2026-08-30/目标文章.md"],
                    top_k=8,
                )
            )
            self.assertTrue(result["metadata"]["single_document"])
            self.assertEqual(result["metadata"]["selected_file_paths"], ["自动抓取/2026-08-30/目标文章.md"])
            self.assertEqual(len(result["evidence"]), 1)
            self.assertEqual(result["evidence"][0]["source_path"], "自动抓取/2026-08-30/目标文章.md")

    def test_selected_folder_returns_all_articles_not_only_keyword_hits(self):
        from app.agent.context import ContextResolver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "自动抓取" / "2026-08-30"
            folder.mkdir(parents=True)
            (folder / "文章一.md").write_text("第一篇文章：模型额度变化。", encoding="utf-8")
            (folder / "文章二.md").write_text("第二篇文章：向量数据库部署经验。", encoding="utf-8")
            (folder / "文章三.md").write_text("第三篇文章：浏览器采集和清洗。", encoding="utf-8")

            resolver = ContextResolver()
            resolver.base = root
            result = __import__("asyncio").run(
                resolver.resolve(
                    "帮我总结这个文件夹的文章，形成综述",
                    scope=["file_resources"],
                    selected_paths=["自动抓取/2026-08-30"],
                    top_k=1,
                )
            )
            self.assertTrue(result["metadata"]["folder_scope"])
            self.assertEqual(len(result["evidence"]), 3)

    def test_query_can_select_multiple_files_and_markdown_escaped_names(self):
        from app.agent.context import ContextResolver, extract_resource_paths

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "自动抓取" / "2026-08-30" / "我的_obsidian_人生系统.md"
            second = root / "自动抓取" / "2026-08-30" / "Agent_Harness_三种范式.md"
            third = root / "自动抓取" / "2026-08-29" / "AI常见概念和算法.md"
            for path, text in ((first, "Obsidian 内容"), (second, "Agent Harness 内容"), (third, "AI 算法内容")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            query = "总结 自动抓取/2026-08-30/我的\\_obsidian\\_人生系统.md，自动抓取/2026-08-30/Agent\\_Harness\\_三种范式.md，自动抓取/2026-08-29/AI常见概念和算法.md"
            resolver = ContextResolver()
            resolver.base = root
            paths = extract_resource_paths(query, root)
            self.assertEqual(len(paths), 3)
            result = __import__("asyncio").run(
                resolver.resolve(query, scope=["file_resources"], selected_paths=paths, top_k=1)
            )
            self.assertEqual(len(result["evidence"]), 3)

    def test_single_source_draft_does_not_require_citation_marker(self):
        from app.agent.orchestrator import AgentOrchestrator

        evidence = [{"source_path": "文章.md", "source_id": "file:文章.md", "content": "正文"}]
        quality = AgentOrchestrator._verify_draft("# 摘要\n正文", evidence, ["文章.md"])
        self.assertTrue(quality["passed"])
        self.assertEqual(quality["citation_count"], 0)
        quality_with_marker = AgentOrchestrator._verify_draft("正文【1】", evidence, ["文章.md"])
        self.assertFalse(quality_with_marker["passed"])

    def test_builtin_intent_routes_are_explicit(self):
        from app.agent.orchestrator import AgentOrchestrator

        self.assertEqual(AgentOrchestrator._classify_intent("帮我做热点汇总", "auto"), "hotspot")
        self.assertEqual(AgentOrchestrator._classify_intent("请对比两个方案", "auto"), "compare")
        self.assertEqual(AgentOrchestrator._classify_intent("这是真的吗？请核查", "auto"), "fact_check")
        self.assertEqual(AgentOrchestrator._classify_intent("请分析这份资料", "auto"), "research")
        self.assertEqual(AgentOrchestrator._classify_intent("这些文章讲的什么，帮我梳理一下", "auto"), "summarize")

    def test_unknown_intent_uses_generic_route(self):
        from app.agent.orchestrator import AgentOrchestrator

        self.assertEqual(AgentOrchestrator._classify_intent("把这件事处理得更专业一点", "auto"), "generic")
        self.assertEqual(AgentOrchestrator._classify_intent("你好，你是谁？", "auto"), "chat")


if __name__ == "__main__":
    unittest.main()
