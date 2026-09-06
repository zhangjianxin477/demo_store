import os
import json
import uuid
import logging
import re
import hashlib
from typing import Dict, List, Any, Optional
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.models.wiki import wiki_store, WikiPage

logger = logging.getLogger(__name__)


class BookmarkService:
    def __init__(self):
        self._bookmark_dir = os.path.join(settings.DATA_DIR, "bookmarks")
        os.makedirs(self._bookmark_dir, exist_ok=True)
        self._bookmarks_file = os.path.join(self._bookmark_dir, "bookmarks.json")
        self._bookmarks: Dict[str, Dict] = {}
        self._load_bookmarks()

    def _load_bookmarks(self):
        if os.path.exists(self._bookmarks_file):
            try:
                with open(self._bookmarks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._bookmarks = data.get("bookmarks", {})
            except Exception as e:
                logger.error(f"书签数据加载失败: {e}")

    def _save_bookmarks(self):
        data = {"bookmarks": self._bookmarks}
        with open(self._bookmarks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    async def quick_save(self, url: str, title: str = "",
                         selection: str = "", tags: List[str] = None,
                         space_id: str = "default",
                         author: str = "bookmarklet",
                         content: str = "", description: str = "",
                         author_name: str = "", published_at: str = "",
                         source_type: str = "web", captured_at: str = "") -> Dict[str, Any]:
        try:
            page_title = title or url
            captured_time = captured_at or datetime.now().isoformat()
            content_parts = []
            content_parts.append(f"# {page_title}\n")
            content_parts.append("## 页面信息\n")
            content_parts.append(f"- 来源 URL：{url}")
            content_parts.append(f"- 来源类型：{source_type or 'web'}")
            content_parts.append(f"- 采集时间：{captured_time}")
            if author_name:
                content_parts.append(f"- 作者：{author_name}")
            if published_at:
                content_parts.append(f"- 发布时间：{published_at}")
            if description:
                content_parts.append(f"- 摘要：{description}")
            content_parts.append("")

            if selection:
                content_parts.append(f"\n## 摘录内容\n\n> {selection}\n")

            # 插件直传完整正文时不再让服务器重复抓取，支持登录态/动态网页采集。
            if content and content.strip():
                content_parts.append("\n## 页面正文\n\n" + content.strip()[:300000])
            else:
                try:
                    import httpx
                    from bs4 import BeautifulSoup
                    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, "lxml")
                    for tag in soup(["script", "style", "nav", "footer", "header", "form", "aside"]):
                        tag.decompose()
                    page_title = title or (soup.title.string.strip() if soup.title and soup.title.string else url)
                    body = soup.find("article") or soup.find("main") or soup.find("body") or soup
                    text = body.get_text(separator="\n", strip=True)
                    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
                    if paragraphs:
                        content_parts.append("\n## 页面正文\n\n" + "\n\n".join(paragraphs)[:300000])
                    meta_desc = soup.find("meta", attrs={"name": "description"})
                    if meta_desc and meta_desc.get("content") and not description:
                        content_parts.append(f"\n- 摘要：{meta_desc['content']}")
                except Exception as e:
                    logger.warning(f"网页内容抓取失败，仅保存URL: {e}")

            content = "\n".join(content_parts)
            final_title = title or page_title or url

            page = wiki_store.create_page(
                title=final_title, content=content, space_id=space_id,
                page_type="markdown", author=author, tags=tags or [],
            )

            bookmark_id = str(uuid.uuid4())[:8]
            self._bookmarks[bookmark_id] = {
                "bookmark_id": bookmark_id,
                "url": url,
                "title": final_title,
                "page_id": page.page_id,
                "selection": selection,
                "content_length": len(content or ""),
                "source_type": source_type or "web",
                "captured_at": captured_time,
                "tags": tags or [],
                "space_id": space_id,
                "created_at": datetime.now().isoformat(),
            }
            self._save_bookmarks()

            return {
                "success": True,
                "bookmark_id": bookmark_id,
                "page_id": page.page_id,
                "title": final_title,
            }
        except Exception as e:
            logger.error(f"快速保存失败: {e}")
            return {"success": False, "error": str(e)}

    def list_bookmarks(self, space_id: str = None,
                       tag: str = None) -> Dict[str, Any]:
        bookmarks = list(self._bookmarks.values())
        if space_id:
            bookmarks = [b for b in bookmarks if b.get("space_id") == space_id]
        if tag:
            bookmarks = [b for b in bookmarks if tag in b.get("tags", [])]
        bookmarks.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"success": True, "bookmarks": bookmarks, "total": len(bookmarks)}

    def delete_bookmark(self, bookmark_id: str) -> Dict[str, Any]:
        if bookmark_id not in self._bookmarks:
            return {"success": False, "error": "书签不存在"}
        del self._bookmarks[bookmark_id]
        self._save_bookmarks()
        return {"success": True}

    def get_bookmarklet_script(self) -> str:
        base_url = getattr(settings, 'SERVER_URL', 'http://localhost:8080')
        script = f"""javascript:(function(){{var url=encodeURIComponent(window.location.href);var title=encodeURIComponent(document.title);var sel=encodeURIComponent(window.getSelection().toString());var w=window.open('{base_url}/api/v1/wiki/bookmarklet/save?url='+url+'&title='+title+'&selection='+sel,'_blank');if(!w){{alert('请允许弹出窗口以使用快速保存功能');}}}})();"""
        return script


bookmark_service = BookmarkService()
