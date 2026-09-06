from fastapi.testclient import TestClient
from pathlib import Path


def _client():
    from app.main import app
    return TestClient(app)


def test_copilot_related_returns_only_retrieved_evidence(monkeypatch):
    import app.api.wiki_routes as routes

    async def fake_search(query, top_k=5, kb_id="", space_id=None):
        assert query == "Agent Harness"
        return {"success": True, "results": [
            {"title": "Harness 设计", "snippet": "模型与工具之间的编排层", "source": "wiki", "page_id": "p1", "score": 3.0},
            {"title": "无关笔记", "snippet": "不应被模型改写为事实", "source": "wiki", "page_id": "p2", "score": 1.0},
        ]}

    monkeypatch.setattr(routes.semantic_search_service, "natural_language_search", fake_search)
    response = _client().post("/api/v1/wiki/ai/copilot", json={
        "action": "related", "selected_text": "Agent Harness", "top_k": 5,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["preview"] == ""
    assert [item["title"] for item in data["evidence"]] == ["Harness 设计", "无关笔记"]


def test_copilot_outline_safe_fallback_does_not_invent_content(monkeypatch):
    import app.api.wiki_routes as routes

    async def fake_search(*args, **kwargs):
        return {"success": True, "results": []}

    async def empty_llm(*args, **kwargs):
        return ""

    monkeypatch.setattr(routes.semantic_search_service, "natural_language_search", fake_search)
    monkeypatch.setattr(routes.ai_doc_service, "_call_llm", empty_llm)
    original = "# 已有标题\n正文只描述当前项目。\n## 子标题\n更多内容。"
    response = _client().post("/api/v1/wiki/ai/copilot", json={
        "action": "outline", "selected_text": original,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["degraded"] is True
    assert "已有标题" in data["preview"]
    assert "子标题" in data["preview"]
    assert "外部事实" not in data["preview"]


def test_copilot_expand_returns_preview_without_page_mutation(monkeypatch):
    import app.api.wiki_routes as routes

    async def fake_search(*args, **kwargs):
        return {"success": True, "results": [{"title": "证据", "snippet": "只允许使用这条证据", "source": "wiki", "page_id": "p1", "score": 2.0}]}

    async def fake_llm(*args, **kwargs):
        return "基于证据补充的预览内容。"

    monkeypatch.setattr(routes.semantic_search_service, "natural_language_search", fake_search)
    monkeypatch.setattr(routes.ai_doc_service, "_call_llm", fake_llm)
    response = _client().post("/api/v1/wiki/ai/copilot", json={
        "action": "expand", "selected_text": "当前段落", "instruction": "补充",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["degraded"] is False
    assert data["preview"] == "基于证据补充的预览内容。"
    # The API response deliberately has no mutation/page update field.
    assert "page" not in data


def test_knowledge_review_prefers_unreused_sources(tmp_path):
    from app.services.knowledge_review import KnowledgeReviewService

    base = tmp_path / "resources"
    base.mkdir()
    first = base / "新文章.md"
    second = base / "已复用.md"
    first.write_text("# 新文章\n## 方法\n正文", encoding="utf-8")
    second.write_text("# 已复用\n## 结论\n正文", encoding="utf-8")
    service = KnowledgeReviewService()
    service.base = base
    result = service.daily([{"result": {"evidence": [{"source_path": "已复用.md"}]}}], limit=2)
    assert result["unreused_source_files"] == 1
    assert result["questions"][0]["source_path"] == "新文章.md"
    assert "新文章" in result["questions"][0]["question"]


def test_knowledge_review_mark_handled_is_persistent(tmp_path):
    from app.services.knowledge_review import KnowledgeReviewService

    base = tmp_path / "resources"
    base.mkdir()
    source = base / "待复习.md"
    source.write_text("# 待复习\n## 观点\n正文", encoding="utf-8")
    service = KnowledgeReviewService()
    service.base = base
    service.state_path = tmp_path / "review-state.json"
    service._handled = {}
    service.mark_handled("待复习.md")
    result = service.daily([], limit=3)
    assert result["unhandled_source_files"] == 0
    assert result["questions"][0]["handled"] is True


def test_copilot_save_creates_new_note(monkeypatch):
    from app.models.wiki import wiki_store
    from app.core.config import settings
    monkeypatch.setattr(settings, "ACCESS_CONTROL_ENABLED", False)
    before = {p.page_id for p in wiki_store.list_pages()}
    response = _client().post("/api/v1/wiki/ai/copilot/save", json={
        "title": "Copilot 保存测试", "content": "基于证据的笔记内容", "tags": ["copilot", "测试"],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["page"]["title"] == "Copilot 保存测试"
    assert set(data["page"]["tags"]) == {"copilot", "测试"}
    assert data["page"]["page_id"] not in before
    wiki_store.delete_page(data["page"]["page_id"])
