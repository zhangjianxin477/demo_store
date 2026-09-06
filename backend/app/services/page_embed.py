import os
import re
import uuid
import json
import shutil
import logging
import hashlib
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class PageEmbedService:
    EMBED_PATTERN = re.compile(r'\{\{>\s*([^}]+)\s*\}\}')

    def resolve_embeds(self, content: str, visited: Optional[set] = None,
                       depth: int = 0, max_depth: int = 5) -> str:
        if depth >= max_depth:
            return content
        if visited is None:
            visited = set()

        def replace_embed(match):
            ref = match.group(1).strip()
            if ref in visited:
                return f'*[循环引用: {ref}]*'
            visited.add(ref)

            page = wiki_store.get_page(ref)
            if not page:
                for p in wiki_store._pages.values():
                    if p.title == ref:
                        page = p
                        break

            if not page:
                return f'*[页面不存在: {ref}]*'

            embedded = self.resolve_embeds(page.content, visited, depth + 1, max_depth)
            return f'---\n**📄 嵌入: [{page.title}]**\n\n{embedded}\n---'

        return self.EMBED_PATTERN.sub(replace_embed, content)

    def find_embed_references(self, content: str) -> List[str]:
        return [m.group(1).strip() for m in self.EMBED_PATTERN.finditer(content)]


page_embed_service = PageEmbedService()
