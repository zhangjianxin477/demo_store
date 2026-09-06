import os
import json
import re
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class FullTextSearchService:
    def __init__(self):
        self._index_dir = os.path.join(settings.DATA_DIR, "search_index")
        os.makedirs(self._index_dir, exist_ok=True)
        self._index_file = os.path.join(self._index_dir, "pages_index.json")
        self._inverted_index: Dict[str, Dict[str, List[int]]] = {}
        self._page_index: Dict[str, Dict] = {}
        self._meilisearch_available = False
        self._meili_client = None
        self._load_index()
        self._init_meilisearch()

    def _load_index(self):
        if os.path.exists(self._index_file):
            try:
                with open(self._index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._inverted_index = data.get("inverted_index", {})
                self._page_index = data.get("page_index", {})
                logger.info(f"搜索索引加载完成: {len(self._page_index)} 页面, {len(self._inverted_index)} 词项")
            except Exception as e:
                logger.error(f"搜索索引加载失败: {e}")

    def _save_index(self):
        data = {
            "inverted_index": self._inverted_index,
            "page_index": self._page_index,
        }
        with open(self._index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _init_meilisearch(self):
        try:
            from meilisearch import Client
            meili_url = os.environ.get("MEILISEARCH_URL", "http://localhost:7700")
            meili_key = os.environ.get("MEILISEARCH_API_KEY", "")
            self._meili_client = Client(meili_url, meili_key)
            self._meili_client.health()
            self._meilisearch_available = True
            self._ensure_meili_index()
            logger.info("MeiliSearch连接成功")
        except ImportError:
            logger.info("meilisearch未安装，使用内置全文检索")
        except Exception as e:
            logger.warning(f"MeiliSearch不可用，使用内置全文检索: {e}")

    def _ensure_meili_index(self):
        if not self._meilisearch_available:
            return
        try:
            index = self._meili_client.index("wiki_pages")
            index.update_filterable_attributes(["space_id", "tags", "author"])
            index.update_searchable_attributes(["title", "content", "tags"])
            index.update_ranking_rules([
                "words", "typo", "proximity", "attribute", "sort", "exactness",
            ])
        except Exception as e:
            logger.warning(f"MeiliSearch索引配置失败: {e}")

    def _tokenize(self, text: str) -> List[str]:
        tokens = []
        text = text.lower()
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        for segment in chinese_chars:
            tokens.extend(self._segment_chinese(segment))
        english_words = re.findall(r'[a-z0-9]+', text)
        tokens.extend(english_words)
        return tokens

    def _segment_chinese(self, text: str) -> List[str]:
        try:
            import jieba
            return list(jieba.cut(text))
        except ImportError:
            pass
        tokens = []
        i = 0
        while i < len(text):
            tokens.append(text[i])
            if i + 1 < len(text):
                tokens.append(text[i:i + 2])
            if i + 2 < len(text):
                tokens.append(text[i:i + 3])
            i += 1
        return tokens

    def index_page(self, page_id: str, title: str, content: str,
                   space_id: str = "default", tags: List[str] = None,
                   author: str = "system"):
        self._page_index[page_id] = {
            "page_id": page_id,
            "title": title,
            "content_preview": content[:500],
            "space_id": space_id,
            "tags": tags or [],
            "author": author,
            "indexed_at": datetime.now().isoformat(),
        }

        full_text = f"{title} {content} {' '.join(tags or [])}"
        tokens = self._tokenize(full_text)
        token_set = set(tokens)

        for term in self._inverted_index:
            if page_id in self._inverted_index[term]:
                self._inverted_index[term].pop(page_id, None)
                if not self._inverted_index[term]:
                    del self._inverted_index[term]

        for token in token_set:
            if token not in self._inverted_index:
                self._inverted_index[token] = {}
            positions = [i for i, t in enumerate(tokens) if t == token]
            self._inverted_index[token][page_id] = positions

        self._save_index()

        if self._meilisearch_available:
            self._index_meili_page(page_id, title, content, space_id, tags, author)

    def _index_meili_page(self, page_id: str, title: str, content: str,
                          space_id: str, tags: List[str], author: str):
        try:
            index = self._meili_client.index("wiki_pages")
            doc = {
                "id": page_id,
                "title": title,
                "content": content[:5000],
                "space_id": space_id,
                "tags": tags or [],
                "author": author,
            }
            index.add_documents([doc])
        except Exception as e:
            logger.warning(f"MeiliSearch索引页面失败: {e}")

    def remove_page(self, page_id: str):
        self._page_index.pop(page_id, None)
        for term in list(self._inverted_index.keys()):
            self._inverted_index[term].pop(page_id, None)
            if not self._inverted_index[term]:
                del self._inverted_index[term]
        self._save_index()

        if self._meilisearch_available:
            try:
                self._meili_client.index("wiki_pages").delete_document(page_id)
            except Exception:
                pass

    def search(self, query: str, space_id: Optional[str] = None,
               tags: Optional[List[str]] = None, author: Optional[str] = None,
               page: int = 1, page_size: int = 20,
               fuzzy: bool = True, highlight: bool = True) -> Dict[str, Any]:
        if self._meilisearch_available:
            result = self._search_meili(query, space_id, tags, author, page, page_size)
            if result.get("success"):
                return result

        return self._search_builtin(query, space_id, tags, author, page, page_size, fuzzy, highlight)

    def _search_meili(self, query: str, space_id: Optional[str],
                      tags: Optional[List[str]], author: Optional[str],
                      page: int, page_size: int) -> Dict[str, Any]:
        try:
            index = self._meili_client.index("wiki_pages")
            search_params = {
                "offset": (page - 1) * page_size,
                "limit": page_size,
                "attributesToHighlight": ["title", "content"],
                "highlightPreTag": "<mark>",
                "highlightPostTag": "</mark>",
            }
            filters = []
            if space_id:
                filters.append(f'space_id = "{space_id}"')
            if author:
                filters.append(f'author = "{author}"')
            if tags:
                for tag in tags:
                    filters.append(f'tags = "{tag}"')
            if filters:
                search_params["filter"] = " AND ".join(filters)

            results = index.search(query, search_params)
            hits = []
            for hit in results.get("hits", []):
                formatted = hit.get("_formatted", {})
                hits.append({
                    "page_id": hit.get("id"),
                    "title": formatted.get("title", hit.get("title", "")),
                    "content_preview": formatted.get("content", hit.get("content", ""))[:300],
                    "space_id": hit.get("space_id"),
                    "tags": hit.get("tags", []),
                    "author": hit.get("author"),
                    "score": 1.0,
                })
            return {
                "success": True,
                "engine": "meilisearch",
                "total": results.get("estimatedTotalHits", len(hits)),
                "page": page,
                "page_size": page_size,
                "results": hits,
                "query": query,
            }
        except Exception as e:
            logger.warning(f"MeiliSearch搜索失败，回退到内置搜索: {e}")
            return {"success": False}

    def _search_builtin(self, query: str, space_id: Optional[str],
                        tags: Optional[List[str]], author: Optional[str],
                        page: int, page_size: int,
                        fuzzy: bool, highlight: bool) -> Dict[str, Any]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return {
                "success": True,
                "engine": "builtin",
                "total": 0,
                "page": page,
                "page_size": page_size,
                "results": [],
                "query": query,
            }

        scores: Dict[str, float] = {}
        for token in query_tokens:
            exact_matches = self._inverted_index.get(token, {})
            for page_id, positions in exact_matches.items():
                page_info = self._page_index.get(page_id)
                if not page_info:
                    continue
                title_tokens = self._tokenize(page_info.get("title", ""))
                in_title = token in title_tokens
                base_score = len(positions) * (3.0 if in_title else 1.0)
                scores[page_id] = scores.get(page_id, 0) + base_score

            if fuzzy:
                for term, page_positions in self._inverted_index.items():
                    if term == token:
                        continue
                    if self._fuzzy_match(token, term):
                        for page_id, positions in page_positions.items():
                            page_info = self._page_index.get(page_id)
                            if not page_info:
                                continue
                            scores[page_id] = scores.get(page_id, 0) + len(positions) * 0.5

        filtered_ids = set(scores.keys())
        if space_id:
            filtered_ids = {pid for pid in filtered_ids
                            if self._page_index.get(pid, {}).get("space_id") == space_id}
        if author:
            filtered_ids = {pid for pid in filtered_ids
                            if self._page_index.get(pid, {}).get("author") == author}
        if tags:
            filtered_ids = {pid for pid in filtered_ids
                            if any(t in self._page_index.get(pid, {}).get("tags", []) for t in tags)}

        sorted_results = sorted(
            [(pid, scores[pid]) for pid in filtered_ids],
            key=lambda x: x[1],
            reverse=True,
        )

        total = len(sorted_results)
        start = (page - 1) * page_size
        end = start + page_size
        page_results = sorted_results[start:end]

        results = []
        for page_id, score in page_results:
            page_info = self._page_index.get(page_id, {})
            content_preview = page_info.get("content_preview", "")
            if highlight:
                content_preview = self._highlight_text(content_preview, query_tokens)
                title_highlighted = self._highlight_text(page_info.get("title", ""), query_tokens)
            else:
                title_highlighted = page_info.get("title", "")
            results.append({
                "page_id": page_id,
                "title": title_highlighted,
                "content_preview": content_preview[:300],
                "space_id": page_info.get("space_id"),
                "tags": page_info.get("tags", []),
                "author": page_info.get("author"),
                "score": round(score, 2),
            })

        return {
            "success": True,
            "engine": "builtin",
            "total": total,
            "page": page,
            "page_size": page_size,
            "results": results,
            "query": query,
        }

    def _fuzzy_match(self, a: str, b: str) -> bool:
        if len(a) < 2 or len(b) < 2:
            return a == b
        if a in b or b in a:
            return True
        if len(a) == len(b):
            diff = sum(1 for ca, cb in zip(a, b) if ca != cb)
            return diff <= 1
        return False

    def _highlight_text(self, text: str, query_tokens: List[str]) -> str:
        result = text
        for token in query_tokens:
            if len(token) < 2:
                continue
            pattern = re.compile(re.escape(token), re.IGNORECASE)
            result = pattern.sub(f'<mark>{token}</mark>', result)
        return result

    def rebuild_index(self) -> Dict[str, Any]:
        self._inverted_index.clear()
        self._page_index.clear()
        count = 0
        pages = list(wiki_store._pages.values())
        logger.info(f"重建索引: 发现 {len(pages)} 个页面")
        for page in pages:
            self.index_page(
                page_id=page.page_id,
                title=page.title,
                content=page.content,
                space_id=page.space_id,
                tags=page.tags,
                author=page.author,
            )
            count += 1

        if self._meilisearch_available:
            try:
                self._meili_client.index("wiki_pages").delete_all_documents()
                docs = []
                for page in wiki_store._pages.values():
                    docs.append({
                        "id": page.page_id,
                        "title": page.title,
                        "content": page.content[:5000],
                        "space_id": page.space_id,
                        "tags": page.tags,
                        "author": page.author,
                    })
                if docs:
                    self._meili_client.index("wiki_pages").add_documents(docs)
            except Exception as e:
                logger.warning(f"MeiliSearch重建索引失败: {e}")

        return {"success": True, "indexed_pages": count}

    def get_search_stats(self) -> Dict[str, Any]:
        return {
            "success": True,
            "engine": "meilisearch" if self._meilisearch_available else "builtin",
            "indexed_pages": len(self._page_index),
            "vocabulary_size": len(self._inverted_index),
            "meilisearch_available": self._meilisearch_available,
        }


fulltext_search_service = FullTextSearchService()
