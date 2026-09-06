import os
import json
import uuid
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.wiki import wiki_store, WikiPage, PageVersion, Comment

logger = logging.getLogger(__name__)


class RecycleBinService:
    def __init__(self):
        self._bin_dir = os.path.join(settings.DATA_DIR, "recycle_bin")
        os.makedirs(self._bin_dir, exist_ok=True)
        self._bin_file = os.path.join(self._bin_dir, "recycle_bin.json")
        self._deleted_pages: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._bin_file):
            try:
                with open(self._bin_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._deleted_pages = data.get("deleted_pages", {})
                logger.info(f"回收站加载完成: {len(self._deleted_pages)} 条记录")
            except Exception as e:
                logger.error(f"回收站加载失败: {e}")

    def _save(self):
        data = {"deleted_pages": self._deleted_pages}
        with open(self._bin_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def soft_delete(self, page_id: str, deleted_by: str = "system") -> bool:
        page = wiki_store.get_page(page_id)
        if not page:
            return False

        versions = wiki_store.get_page_versions(page_id)
        comments = wiki_store.get_comments(page_id)

        deleted_entry = {
            "page_data": page.to_dict(),
            "versions": [v.to_dict() for v in versions],
            "comments": [c.to_dict() for c in comments],
            "deleted_by": deleted_by,
            "deleted_at": datetime.now().isoformat(),
            "original_space_id": page.space_id,
            "original_parent_id": page.parent_id,
        }

        self._deleted_pages[page_id] = deleted_entry
        self._save()

        wiki_store.delete_page(page_id)
        logger.info(f"页面软删除: {page.title} ({page_id})")
        return True

    def restore(self, page_id: str) -> Optional[Dict]:
        entry = self._deleted_pages.get(page_id)
        if not entry:
            return None

        page_data = entry["page_data"]
        existing = wiki_store.get_page(page_id)
        if existing:
            return {"success": False, "error": "目标页面ID已存在，无法恢复"}

        page = WikiPage.from_dict(page_data)
        wiki_store._pages[page_id] = page

        versions_data = entry.get("versions", [])
        versions = [PageVersion.from_dict(v) for v in versions_data]
        wiki_store._versions[page_id] = versions

        comments_data = entry.get("comments", [])
        comments = [Comment.from_dict(c) for c in comments_data]
        wiki_store._comments[page_id] = comments

        wiki_store._save_registry()
        wiki_store._save_versions(page_id)
        wiki_store._save_comments(page_id)

        del self._deleted_pages[page_id]
        self._save()

        logger.info(f"页面恢复: {page.title} ({page_id})")
        return {"success": True, "page_id": page_id, "title": page.title}

    def permanent_delete(self, page_id: str) -> bool:
        if page_id not in self._deleted_pages:
            return False
        del self._deleted_pages[page_id]
        self._save()
        logger.info(f"页面永久删除: {page_id}")
        return True

    def empty_bin(self) -> int:
        count = len(self._deleted_pages)
        self._deleted_pages.clear()
        self._save()
        logger.info(f"回收站已清空: {count} 条记录")
        return count

    def list_deleted(self, space_id: Optional[str] = None) -> List[Dict]:
        results = []
        for page_id, entry in self._deleted_pages.items():
            page_data = entry.get("page_data", {})
            if space_id and page_data.get("space_id") != space_id:
                continue
            results.append({
                "page_id": page_id,
                "title": page_data.get("title", ""),
                "author": page_data.get("author", ""),
                "deleted_by": entry.get("deleted_by", ""),
                "deleted_at": entry.get("deleted_at", ""),
                "original_space_id": entry.get("original_space_id", ""),
                "version_count": len(entry.get("versions", [])),
                "comment_count": len(entry.get("comments", [])),
            })
        results.sort(key=lambda x: x.get("deleted_at", ""), reverse=True)
        return results

    def get_deleted_page(self, page_id: str) -> Optional[Dict]:
        entry = self._deleted_pages.get(page_id)
        if not entry:
            return None
        return {
            "page_data": entry.get("page_data", {}),
            "deleted_by": entry.get("deleted_by", ""),
            "deleted_at": entry.get("deleted_at", ""),
            "version_count": len(entry.get("versions", [])),
            "comment_count": len(entry.get("comments", [])),
        }

    @property
    def count(self) -> int:
        return len(self._deleted_pages)


recycle_bin_service = RecycleBinService()
