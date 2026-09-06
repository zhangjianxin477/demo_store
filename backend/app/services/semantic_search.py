import asyncio
import logging
import json
from typing import List, Optional, Dict, Any

from app.core.config import settings
from app.rag.vector_store import vector_store
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class SemanticSearchService:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY 未设置，语义检索将使用关键词回退模式")
            return
        try:
            from zai import ZhipuAiClient
            self._glm = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            logger.info("语义检索 GLM 初始化成功 (zai-sdk)")
        except ImportError:
            try:
                from openai import OpenAI
                self._glm = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                logger.info("语义检索 GLM 初始化成功 (openai-sdk)")
            except Exception as e:
                logger.error(f"语义检索 GLM 初始化失败: {e}")
                self._glm = None
        except Exception as e:
            logger.error(f"语义检索 GLM 初始化失败: {e}")
            self._glm = None

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        if not self._glm:
            return None
        try:
            from zai import ZhipuAiClient
            client = ZhipuAiClient(api_key=settings.OPENAI_API_KEY)
            response = client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=text,
            )
            return response.data[0].embedding
        except Exception:
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=settings.OPENAI_BASE_URL,
                )
                response = client.embeddings.create(
                    model=settings.EMBEDDING_MODEL,
                    input=text,
                )
                return response.data[0].embedding
            except Exception as e:
                logger.error(f"获取嵌入向量失败: {e}")
                return None

    async def natural_language_search(self, query: str, top_k: int = 10,
                                      kb_id: str = "", space_id: Optional[str] = None) -> Dict[str, Any]:
        wiki_results = self._search_wiki_pages(query, space_id)
        vector_results = self._search_vector_store(query, top_k, kb_id)
        combined = self._merge_results(wiki_results, vector_results, query)
        return {
            "success": True,
            "query": query,
            "total": len(combined),
            "results": combined[:top_k],
        }

    def _search_wiki_pages(self, query: str, space_id: Optional[str] = None) -> List[Dict]:
        results = []
        query_lower = query.lower()
        for page in wiki_store.list_pages(space_id=space_id):
            score = 0.0
            if query_lower in page.title.lower():
                score += 3.0
            if query_lower in page.content.lower():
                score += 2.0
                idx = page.content.lower().find(query_lower)
                snippet_start = max(0, idx - 50)
                snippet_end = min(len(page.content), idx + len(query) + 100)
                snippet = page.content[snippet_start:snippet_end]
            else:
                snippet = page.content[:200] if page.content else ""
            for tag in page.tags:
                if query_lower in tag.lower():
                    score += 1.5
            if score > 0:
                results.append({
                    "source": "wiki",
                    "page_id": page.page_id,
                    "title": page.title,
                    "snippet": snippet,
                    "score": score,
                    "space_id": page.space_id,
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _search_vector_store(self, query: str, top_k: int, kb_id: str) -> List[Dict]:
        embedding = self._get_embedding(query)
        if not embedding:
            return vector_store.keyword_search(query, top_k=top_k, kb_id=kb_id)
        import numpy as np
        query_embedding = np.array(embedding, dtype=np.float32)
        return vector_store.search(query_embedding=query_embedding, top_k=top_k, threshold=0.0, kb_id=kb_id)

    def _merge_results(self, wiki_results: List[Dict], vector_results: List[Dict],
                       query: str) -> List[Dict]:
        seen_titles = set()
        merged = []
        for r in wiki_results:
            key = r.get("title", "").lower()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append(r)
        for r in vector_results:
            title = r.get("doc_name", r.get("source", ""))
            key = title.lower()
            if key not in seen_titles:
                seen_titles.add(key)
                merged.append({
                    "source": "vector",
                    "title": title,
                    "snippet": r.get("text", r.get("content", ""))[:300],
                    "score": r.get("score", 0.0) * 2.0,
                    "doc_id": r.get("doc_id", ""),
                    "chunk_id": r.get("chunk_id", ""),
                })
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged

    async def semantic_similarity_match(self, text1: str, text2: str) -> Dict[str, Any]:
        emb1 = self._get_embedding(text1)
        emb2 = self._get_embedding(text2)
        if not emb1 or not emb2:
            return {"success": False, "error": "无法获取嵌入向量", "similarity": 0.0}
        import numpy as np
        a = np.array(emb1, dtype=np.float32)
        b = np.array(emb2, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            similarity = 0.0
        else:
            similarity = float(np.dot(a, b) / (norm_a * norm_b))
        return {"success": True, "similarity": round(similarity, 4)}

    async def fuzzy_knowledge_search(self, query: str, top_k: int = 10,
                                     kb_id: str = "") -> Dict[str, Any]:
        expanded_queries = await self._expand_query(query)
        all_results = []
        for q in expanded_queries:
            results = self._search_vector_store(q, top_k, kb_id)
            all_results.extend(results)
        unique_results = {}
        for r in all_results:
            key = r.get("chunk_id", r.get("doc_id", ""))
            if key not in unique_results or r.get("score", 0) > unique_results[key].get("score", 0):
                unique_results[key] = r
        sorted_results = sorted(unique_results.values(), key=lambda x: x.get("score", 0), reverse=True)
        return {
            "success": True,
            "query": query,
            "expanded_queries": expanded_queries,
            "total": len(sorted_results),
            "results": sorted_results[:top_k],
        }

    async def _expand_query(self, query: str) -> List[str]:
        if not self._glm:
            return [query]
        system_prompt = """你是一个查询扩展助手。请将用户的查询扩展为3-5个语义相关的查询变体，
包括同义词替换、概念扩展、相关术语等。每行一个查询，不要编号。"""
        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query},
                    ],
                    max_tokens=256,
                    temperature=0.5,
                )
            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
            content = response.choices[0].message.content.strip() if response.choices[0].message.content else ""
            expanded = [query] + [line.strip() for line in content.split("\n") if line.strip()]
            return expanded[:5]
        except Exception as e:
            logger.error(f"查询扩展失败: {e}")
            return [query]

    async def recommend_related(self, page_id: str, top_k: int = 5) -> Dict[str, Any]:
        page = wiki_store.get_page(page_id)
        if not page:
            return {"success": False, "error": "页面不存在"}
        query_text = f"{page.title} {page.content[:500]}"
        wiki_results = self._search_wiki_pages(page.title, space_id=page.space_id)
        recommendations = [r for r in wiki_results if r.get("page_id") != page_id][:top_k]
        return {"success": True, "page_id": page_id, "recommendations": recommendations}


semantic_search_service = SemanticSearchService()
