"""RAG 服务模块 - 检索增强生成的核心逻辑。

提供向量搜索、关键词搜索、混合检索、查询改写、重排序等功能，
支持本地和云端两种运行模式。
"""

import asyncio
import logging
import time
import hashlib
import threading
import re
import os
import json
from typing import AsyncGenerator, List, Optional, Dict, Any, Tuple

from app.core.config import settings
from app.rag.vector_store import vector_store
from app.rag.embedding_service import embedding_service
from app.rag.conversation_memory import conversation_memory
from app.rag.media import extract_images
from app.telemetry.recorder import telemetry

logger = logging.getLogger(__name__)


def _load_runtime_llm_config() -> Dict[str, str]:
    """Read persisted model settings when the RAG singleton initializes."""
    path = os.path.join(settings.DATA_DIR, "system_config", "model_config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        collection = saved.get("api_key_collection", {}) if isinstance(saved, dict) else {}
        candidates = [collection.get("base_llm", {}), collection.get("fallback_llm", {})]
        for item in candidates:
            if isinstance(item, dict) and item.get("enabled", True) and item.get("api_key"):
                config = {
                    "api_key": str(item["api_key"]),
                    "base_url": str(item.get("base_url") or ""),
                    "model": str(item.get("model") or settings.OPENAI_MODEL),
                }
                settings.OPENAI_API_KEY = config["api_key"]
                if config["base_url"]:
                    settings.OPENAI_BASE_URL = config["base_url"]
                settings.OPENAI_MODEL = config["model"]
                return config
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _load_runtime_rerank_config() -> Dict[str, str]:
    """Load the optional remote reranker slot from persisted settings."""
    path = os.path.join(settings.DATA_DIR, "system_config", "model_config.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        item = (saved.get("api_key_collection", {}) or {}).get("rerank", {})
        if isinstance(item, dict) and item.get("enabled", False) and item.get("api_key"):
            return {
                "api_key": str(item.get("api_key") or ""),
                "base_url": str(item.get("base_url") or ""),
                "model": str(item.get("model") or "bge-reranker-v2-m3"),
                "provider": str(item.get("provider") or ""),
            }
    except (OSError, ValueError, TypeError):
        pass
    return {}


class QueryCache:
    """查询结果缓存，基于 TTL 和最大容量淘汰策略。

    Attributes:
        _ttl: 缓存过期时间（秒）
        _max_size: 最大缓存条目数
    """

    def __init__(self, ttl: int = 300, max_size: int = 200):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._ttl = ttl
        self._max_size = max_size
        self._lock = threading.Lock()

    def _make_key(self, question: str, mode: str, kb_id: str, top_k: int,
                  kb_ids: Optional[List[str]] = None, web_search: bool = False,
                  corpus_version: Any = "") -> str:
        """生成缓存键。"""
        normalized_ids = ",".join(sorted({str(item) for item in (kb_ids or []) if str(item)}))
        raw = f"{question}|{mode}|{kb_id}|{normalized_ids}|{top_k}|web={int(bool(web_search))}|corpus={corpus_version}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, question: str, mode: str, kb_id: str, top_k: int,
            kb_ids: Optional[List[str]] = None, web_search: bool = False,
            corpus_version: Any = "") -> Optional[Dict]:
        """获取缓存结果，过期返回 None。"""
        if not settings.QUERY_CACHE_ENABLED:
            return None
        key = self._make_key(question, mode, kb_id, top_k, kb_ids, web_search, corpus_version)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.time() - ts > self._ttl:
                del self._cache[key]
                return None
            return value

    def put(self, question: str, mode: str, kb_id: str, top_k: int, result: Dict,
            kb_ids: Optional[List[str]] = None, web_search: bool = False,
            corpus_version: Any = "") -> None:
        """存入缓存，超出容量时淘汰最旧条目。"""
        if not settings.QUERY_CACHE_ENABLED:
            return
        key = self._make_key(question, mode, kb_id, top_k, kb_ids, web_search, corpus_version)
        with self._lock:
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[key] = (time.time(), result)

    def invalidate_kb(self, kb_id: str) -> None:
        """使指定知识库相关的缓存失效。"""
        with self._lock:
            keys_to_remove = [k for k, (_, v) in self._cache.items()
                              if isinstance(v, dict) and kb_id in str(v)]
            for k in keys_to_remove:
                del self._cache[k]

    def clear(self) -> None:
        """清空所有缓存。"""
        with self._lock:
            self._cache.clear()


class Reranker:
    """搜索结果重排序器，支持 LLM 和规则两种模式。

    LLM 模式使用大模型对搜索结果重新评分排序，
    规则模式基于关键词匹配和位置特征进行排序。
    """

    def __init__(self):
        self._glm = None
        self._remote_config = {}
        self._init_llm()
        self._init_remote()

    def _init_llm(self):
        saved = _load_runtime_llm_config()
        api_key = saved.get("api_key") or settings.OPENAI_API_KEY
        base_url = saved.get("base_url") or settings.OPENAI_BASE_URL
        if not api_key:
            return
        try:
            from openai import OpenAI
            self._glm = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as e:
            logger.error(f"Reranker LLM 初始化失败: {e}")
            self._glm = None

    def _init_remote(self):
        """Initialize a Cohere/Jina-compatible remote rerank endpoint."""
        self._remote_config = _load_runtime_rerank_config()
        if self._remote_config:
            logger.info("远程 Reranker 已配置: provider=%s model=%s", self._remote_config.get("provider"), self._remote_config.get("model"))

    def reload_config(self):
        """Reload credentials/model settings for the already running process."""
        self._glm = None
        self._init_llm()
        self._init_remote()

    async def rerank(self, query: str, results: List[Dict], top_k: int = 3) -> List[Dict]:
        if not results or len(results) <= top_k:
            return results

        if self._remote_config:
            try:
                remote = await self._rerank_with_remote(query, results, top_k)
                if remote:
                    return remote
            except Exception as e:
                logger.warning("远程 Reranker 调用失败，继续回退: %s", e)

        if self._glm:
            try:
                return await self._rerank_with_llm(query, results, top_k)
            except Exception as e:
                logger.error(f"LLM 重排序失败，使用规则回退: {e}")

        return self._rerank_rule_based(query, results, top_k)

    async def _rerank_with_remote(self, query: str, results: List[Dict], top_k: int) -> List[Dict]:
        """Call a provider's native ``/v1/rerank`` API (SiliconFlow/Moark/etc.)."""
        import httpx

        cfg = self._remote_config
        endpoint = str(cfg.get("base_url") or "").rstrip("/")
        if not endpoint:
            return []
        if not endpoint.lower().endswith("/rerank"):
            endpoint += "/rerank"
        documents = [str(item.get("content") or "")[:4000] for item in results[:50]]
        model = str(cfg.get("model") or "BAAI/bge-reranker-v2-m3")
        if "siliconflow" in str(cfg.get("provider") or "").lower() and model.lower().startswith("pro/baai/"):
            model = model[4:]
        payload = {
            "model": model,
            "query": query,
            "top_n": max(1, min(top_k, len(documents))),
            "documents": documents,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            provider_lower = str(cfg.get("provider") or "").lower()
            request_headers = {
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            }
            if "moark" in provider_lower or "moark.com" in endpoint.lower():
                request_headers["X-Failover-Enabled"] = "true"
            response = await client.post(
                endpoint,
                # The service returns ranked documents; top_k is also sent in
                # the provider request and applied locally for compatibility.
                headers=request_headers,
                json=payload,
            )
            if response.status_code >= 400:
                detail = ""
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        detail = str(body.get("error") or body.get("message") or body.get("detail") or body)
                    else:
                        detail = str(body)
                except Exception:
                    detail = response.text
                raise httpx.HTTPStatusError(
                    f"Rerank 服务返回 HTTP {response.status_code}: {detail[:500]}",
                    request=response.request,
                    response=response,
                )
            body = response.json() if response.content else {}

        raw_items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(raw_items, list):
            raw_items = (body.get("data") or []) if isinstance(body, dict) else []
        ranked = []
        for rank, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= index < len(results):
                continue
            result = dict(results[index])
            score = item.get("relevance_score", item.get("score", item.get("relevance", 0)))
            try:
                result["rerank_score"] = float(score)
            except (TypeError, ValueError):
                result["rerank_score"] = 0.0
            result["rerank_provider"] = cfg.get("provider") or "remote"
            result["rerank_rank"] = rank + 1
            ranked.append(result)
        return ranked[:top_k]

    async def _rerank_with_llm(self, query: str, results: List[Dict], top_k: int) -> List[Dict]:
        system_prompt = """你是一个相关性评估专家。给定一个查询和多个文本片段，评估每个片段与查询的相关性。
返回一个JSON数组，包含top_k个最相关片段的索引（从0开始），按相关性从高到低排列。
格式：[2, 0, 5] 表示第3、1、6个片段最相关。只返回JSON数组，不要其他内容。"""

        snippets = ""
        for i, r in enumerate(results[:10]):
            snippets += f"\n[{i}] {r['content'][:300]}"

        user_message = f"查询：{query}\n\n文本片段：{snippets}\n\n请返回最相关的{top_k}个片段的索引："

        def _call():
            return self._glm.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=256,
                temperature=0.0,
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
        raw = response.choices[0].message.content.strip()

        import json
        import re
        json_match = re.search(r'\[.*?\]', raw)
        if json_match:
            indices = json.loads(json_match.group())
            reranked = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(results):
                    reranked.append(results[idx])
            if reranked:
                return reranked[:top_k]

        return self._rerank_rule_based(query, results, top_k)

    def _rerank_rule_based(self, query: str, results: List[Dict], top_k: int) -> List[Dict]:
        import re
        query_lower = query.lower()
        query_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', query_lower))
        query_bigrams = set()
        for w in query_words:
            if len(w) >= 2:
                for j in range(len(w) - 1):
                    query_bigrams.add(w[j:j + 2])

        kw_bonus = settings.RERANK_KEYWORD_BONUS
        bg_bonus = settings.RERANK_BIGRAM_BONUS
        ex_bonus = settings.RERANK_EXACT_BONUS
        ex_cap = settings.RERANK_EXACT_CAP
        ti_bonus = settings.RERANK_TITLE_BONUS
        ti_cap = settings.RERANK_TITLE_CAP
        len_penalty = settings.RERANK_LENGTH_PENALTY_FACTOR

        def compute_score(r: Dict) -> float:
            base_score = r.get("score", 0)
            content = r.get("content", "").lower()
            content_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', content))

            overlap = len(query_words & content_words)
            keyword_score = overlap / max(len(query_words), 1) * kw_bonus

            bigram_score = 0.0
            if query_bigrams:
                content_bigrams = set()
                for cw in content_words:
                    if len(cw) >= 2:
                        for j in range(len(cw) - 1):
                            content_bigrams.add(cw[j:j + 2])
                bigram_overlap = len(query_bigrams & content_bigrams)
                bigram_score = bigram_overlap / max(len(query_bigrams), 1) * bg_bonus

            exact_score = 0.0
            for qw in query_words:
                if len(qw) >= 2 and qw in content:
                    exact_score += ex_bonus
            exact_score = min(exact_score, ex_cap)

            title_score = 0.0
            meta = r.get("metadata", {})
            title = meta.get("title", "").lower()
            if title:
                for qw in query_words:
                    if len(qw) >= 2 and qw in title:
                        title_score += ti_bonus
                title_score = min(title_score, ti_cap)

            length_pen = min(len(content) / 500, 1.0) * len_penalty

            return base_score + keyword_score + bigram_score + exact_score + title_score - length_pen

        sorted_results = sorted(results, key=compute_score, reverse=True)
        return sorted_results[:top_k]


class RAGService:
    """RAG 核心服务，提供检索增强生成的完整流水线。

    支持：
    - 向量搜索 / 关键词搜索 / 混合搜索
    - 查询改写与扩展
    - 搜索结果重排序
    - 上下文感知的多轮对话
    - 查询缓存
    """

    def __init__(self):
        self._glm = None
        self._reranker = Reranker()
        self._enhanced_reranker = None
        self._hybrid_engine = None
        self._cache = QueryCache(
            ttl=settings.QUERY_CACHE_TTL,
            max_size=settings.QUERY_CACHE_MAX_SIZE,
        )
        self._init_llm()
        self._init_enhanced_modules()

    def _init_llm(self):
        saved = _load_runtime_llm_config()
        api_key = saved.get("api_key") or settings.OPENAI_API_KEY
        base_url = saved.get("base_url") or settings.OPENAI_BASE_URL
        if not api_key:
            logger.warning("OPENAI_API_KEY 未设置，RAG 将使用规则回退模式")
            return
        try:
            from openai import OpenAI
            self._glm = OpenAI(
                api_key=api_key,
                base_url=base_url,
            )
            logger.info("RAG GLM 服务初始化成功 (openai-compatible sdk)")
        except Exception as e:
            logger.error(f"GLM 初始化失败: {e}")
            self._glm = None

    def reload_config(self):
        """Reload model and reranker settings without restarting the API process."""
        self._cache.clear()
        self._glm = None
        self._init_llm()
        self._reranker.reload_config()
        try:
            if self._enhanced_reranker:
                self._enhanced_reranker._glm = None
                self._enhanced_reranker._init_llm()
        except Exception as exc:
            logger.warning("增强重排器配置刷新失败: %s", exc)

    def _init_enhanced_modules(self):
        try:
            from app.rag.bm25_search import hybrid_search_engine
            self._hybrid_engine = hybrid_search_engine
            logger.info("BM25 混合检索引擎初始化成功")
            self._warm_hybrid_index_from_vector_store()
        except Exception as e:
            logger.warning(f"BM25 混合检索引擎初始化失败: {e}")
            self._hybrid_engine = None

        try:
            from app.rag.enhanced_reranker import enhanced_reranker
            self._enhanced_reranker = enhanced_reranker
            logger.info("增强重排器初始化成功")
        except Exception as e:
            logger.warning(f"增强重排器初始化失败: {e}")
            self._enhanced_reranker = None

    def _warm_hybrid_index_from_vector_store(self):
        if not self._hybrid_engine or vector_store.count <= 0:
            return

        stats = self._hybrid_engine.get_stats()
        if stats.get("corpus_size", 0) > 0:
            return

        chunks = []
        texts = getattr(vector_store, "_texts", [])
        metadata_list = getattr(vector_store, "_metadata", [])
        chunk_ids = getattr(vector_store, "_chunk_ids", [])
        doc_ids = getattr(vector_store, "_doc_ids", [])

        for idx, content in enumerate(texts):
            if not content or not content.strip():
                continue
            chunks.append({
                "chunk_id": chunk_ids[idx] if idx < len(chunk_ids) else f"chunk_{idx}",
                "doc_id": doc_ids[idx] if idx < len(doc_ids) else "",
                "content": content,
                "metadata": metadata_list[idx] if idx < len(metadata_list) and metadata_list[idx] else {},
            })

        if chunks:
            self._hybrid_engine.add_documents(chunks)
            logger.info(f"BM25 index warmed from vector store: {len(chunks)} chunks")

    async def query(self, question: str, mode: str = "hybrid",
                    kb_id: str = "", top_k: int = 5,
                    enable_web_search: bool = False,
                    session_id: Optional[str] = None,
                    kb_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        # Conversation-dependent and web-search queries must never be served
        # from a shared cache. Static knowledge-base queries can safely reuse
        # the configured TTL cache, now keyed by the complete KB selection.
        cacheable = not session_id and not enable_web_search
        corpus_version = getattr(vector_store, "count", 0)
        if cacheable:
            cached = self._cache.get(question, mode, kb_id, top_k, kb_ids, enable_web_search, corpus_version)
            if cached is not None:
                return cached
        if session_id:
            conversation_memory.create_session(session_id)
            conversation_memory.add_message(session_id, "user", question)

        # 自动链路只做轻量规范化/上下文补全；LLM 标准改写由前端按钮显式触发。
        effective_question = await self._smart_rewrite(question, session_id)

        # 查询扩展：生成多个同义查询提升召回（仅在混合模式下启用）
        expanded_queries = []
        if mode == "hybrid" and self._glm and settings.QUERY_EXPANSION_ENABLED:
            try:
                expanded_queries = await self.expand_query(effective_question, num_variants=2)
                if expanded_queries and len(expanded_queries) > 1:
                    logger.info(f"查询扩展: {expanded_queries}")
            except Exception as e:
                logger.warning(f"查询扩展失败，使用单一查询: {e}")
                expanded_queries = []

        retrieval_top_k = max(
            top_k * settings.RAG_RETRIEVAL_MULTIPLIER,
            settings.RAG_RETRIEVAL_MIN_K,
            30,
            settings.RAG_TOP_K,
        )
        retrieval_started = time.monotonic()

        # 合并 kb_id 和 kb_ids
        effective_kb_ids = list(set(kb_ids or []) | ({kb_id} if kb_id else set()))

        if mode == "keyword":
            if self._hybrid_engine:
                results = self._hybrid_engine.bm25_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id, kb_ids=kb_ids)
            else:
                results = vector_store.keyword_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id)
        elif mode == "semantic":
            query_embedding = await self._get_query_embedding_async(effective_question)
            if embedding_service.is_degraded:
                if self._hybrid_engine:
                    results = self._hybrid_engine.bm25_search(
                        effective_question,
                        top_k=retrieval_top_k,
                        kb_id=kb_id,
                        kb_ids=kb_ids,
                    )
                else:
                    results = vector_store.keyword_search(
                        effective_question,
                        top_k=retrieval_top_k,
                        kb_id=kb_id,
                    )
            else:
                results = vector_store.search(
                    query_embedding=query_embedding,
                    top_k=retrieval_top_k,
                    threshold=0.0,
                    kb_id=kb_id,
                    kb_ids=kb_ids,
                )
        else:
            query_embedding = await self._get_query_embedding_async(effective_question)
            if embedding_service.is_degraded:
                vector_results = []
            else:
                vector_results = vector_store.search(
                    query_embedding=query_embedding,
                    top_k=retrieval_top_k,
                    threshold=0.0,
                    kb_id=kb_id,
                    kb_ids=kb_ids,
                )

            if self._hybrid_engine:
                results = self._hybrid_engine.search(
                    query=effective_question,
                    top_k=retrieval_top_k,
                    kb_id=kb_id,
                    kb_ids=kb_ids,
                    vector_results=vector_results,
                )
            else:
                keyword_results = vector_store.keyword_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id)
                original_embedding = None
                if effective_question != question:
                    original_embedding = await self._get_query_embedding_async(question)
                    original_results = vector_store.search(
                        query_embedding=original_embedding,
                        top_k=retrieval_top_k,
                        threshold=0.0,
                        kb_id=kb_id,
                    )
                else:
                    original_results = []
                results = self._merge_results_enhanced(
                    vector_results, keyword_results, original_results
                )

            # 扩展查询检索：对每个扩展查询进行检索并合并结果
            if expanded_queries and len(expanded_queries) > 1:
                for eq in expanded_queries[1:]:  # 跳过第一个（即原查询）
                    try:
                        if embedding_service.is_degraded:
                            eq_vector_results = []
                        else:
                            eq_embedding = await self._get_query_embedding_async(eq)
                            eq_vector_results = vector_store.search(
                                query_embedding=eq_embedding,
                                top_k=retrieval_top_k,
                                threshold=0.0,
                                kb_id=kb_id,
                            )
                        eq_keyword_results = vector_store.keyword_search(eq, top_k=retrieval_top_k, kb_id=kb_id)
                        if self._hybrid_engine:
                            eq_results = self._hybrid_engine.search(
                                query=eq, top_k=retrieval_top_k, kb_id=kb_id,
                                kb_ids=kb_ids, vector_results=eq_vector_results,
                            )
                        else:
                            eq_results = self._merge_results_enhanced(eq_vector_results, eq_keyword_results, [])
                        # Fuse the base query and variants by rank, then apply
                        # a modest discount to variant-only hits.  This avoids
                        # score-scale artifacts from the old max normalization.
                        results = self._fuse_ranked_results([results, eq_results], retrieval_top_k, [1.0, 0.7])
                    except Exception as e:
                        logger.warning(f"扩展查询 '{eq}' 检索失败: {e}")

        telemetry.record("retrieval_completed", source="rag", session_id=session_id or "",
                         properties={"mode": mode, "vector_candidates": len(vector_results) if isinstance(locals().get("vector_results"), list) else 0,
                                     "hybrid_candidates": len(results), "retrieval_top_k": retrieval_top_k,
                                     "duration_ms": round((time.monotonic() - retrieval_started) * 1000, 1),
                                     "embedding_degraded": embedding_service.is_degraded})

        # Prefer a configured dedicated reranker (e.g. bge-reranker-v2-m3)
        # over the generic LLM-based enhanced reranker.  Otherwise the remote
        # slot would be configured successfully but never called.
        rerank_started = time.monotonic()
        remote_rerank_ready = bool(getattr(self._reranker, "_remote_config", {}))
        if settings.RERANK_ENABLED and remote_rerank_ready:
            reranked_results = await self._reranker.rerank(effective_question, results, top_k=top_k)
        elif settings.RERANK_ENABLED and self._enhanced_reranker:
            reranked_results = await self._enhanced_reranker.rerank(effective_question, results, top_k=top_k)
        else:
            reranked_results = await self._reranker.rerank(effective_question, results, top_k=top_k)
        telemetry.record("rerank_completed", source="rag", session_id=session_id or "",
                         properties={"provider": next((str(item.get("rerank_provider") or "") for item in reranked_results if isinstance(item, dict) and item.get("rerank_provider")), "rule_based"),
                                     "candidates": len(results), "returned": len(reranked_results),
                                     "duration_ms": round((time.monotonic() - rerank_started) * 1000, 1),
                                     "degraded": not bool(getattr(self._reranker, "_remote_config", {})) and not bool(self._enhanced_reranker)})

        web_results = []
        if enable_web_search:
            web_results = await self._web_search(effective_question)

        chunks = self._prepare_display_chunks(reranked_results)
        backend_status = getattr(vector_store, "backend_status", {}) or {}
        index_status = vector_store.index_model_status(getattr(embedding_service, "model", settings.EMBEDDING_MODEL))
        rerank_provider = next((str(item.get("rerank_provider") or "") for item in reranked_results
                                if isinstance(item, dict) and item.get("rerank_provider")), "rule_based")
        vector_candidate_count = len(vector_results) if isinstance(locals().get("vector_results"), list) else (len(results) if mode == "semantic" else 0)
        images = []
        seen_image_urls = set()
        for chunk in chunks[:top_k]:
            for image in extract_images(
                chunk.get("display_text") or chunk.get("content") or "",
                source_path=(chunk.get("metadata") or {}).get("source_path")
                or (chunk.get("metadata") or {}).get("path") or "",
                max_images=max(1, 12 - len(images)),
            ):
                if image.get("url") not in seen_image_urls:
                    seen_image_urls.add(image.get("url"))
                    images.append(image)
            if len(images) >= 12:
                break
        has_kb_results = bool(chunks)
        context = self._build_context(chunks) if has_kb_results else ""

        if web_results:
            web_context = "\n\n".join([
                f"[网页{i + 1}] ({wr.get('title', '')})\n{wr.get('snippet', '')}"
                for i, wr in enumerate(web_results)
            ])
            context = f"{context}\n\n---联网搜索结果---\n{web_context}" if context else f"---联网搜索结果---\n{web_context}"

        conversation_history = []
        if session_id:
            conversation_history = conversation_memory.build_context_for_llm(
                session_id, question, max_tokens=1500
            )

        generation_started = time.monotonic()
        answer = await self._generate_answer(
            question, context,
            has_kb_results=has_kb_results,
            has_web=enable_web_search and bool(web_results),
            conversation_history=conversation_history,
            citation_count=min(len(chunks), top_k),
        )
        telemetry.record("generation_completed", source="rag", session_id=session_id or "",
                         properties={"mode": mode, "duration_ms": round((time.monotonic() - generation_started) * 1000, 1),
                                     "has_evidence": has_kb_results, "answer_chars": len(answer or ""),
                                     "degraded": not bool(self._glm)})

        if session_id:
            conversation_memory.add_message(
                session_id,
                "assistant",
                answer,
                metadata={"chunks": chunks[:top_k], "images": images},
            )
            await self._compress_session_if_needed(session_id)

        result = {
            "answer": answer,
            "sources": chunks[:top_k],
            "chunks": chunks[:top_k],
            "images": images,
            "web_results": web_results,
            "metadata": {
                "has_results": True,
                "result_count": len(reranked_results),
                "display_chunk_count": len(chunks),
                "web_result_count": len(web_results),
                "rewritten_query": effective_question if effective_question != question else None,
                "session_id": session_id,
                "embedding_degraded": embedding_service.is_degraded,
                "embedding_error": embedding_service.last_error,
                "embedding_provider": embedding_service.provider,
                "retrieval_top_k": retrieval_top_k,
                "top_k_used": top_k,
                "retrieval_diagnostics": {
                    "vector_backend": backend_status.get("backend", "local"),
                    "milvus_ready": bool(backend_status.get("backend") == "milvus" and backend_status.get("ready")),
                    "embedding_model": getattr(embedding_service, "model", settings.EMBEDDING_MODEL),
                    "index_models": index_status.get("models", []),
                    "index_model_consistent": index_status.get("consistent", False),
                    "vector_candidates": vector_candidate_count,
                    "hybrid_candidates": len(results),
                    "rerank_provider": rerank_provider,
                    "rerank_candidates": len(results),
                    "rerank_returned": len(reranked_results),
                },
            },
        }
        if cacheable:
            self._cache.put(question, mode, kb_id, top_k, result, kb_ids, enable_web_search, corpus_version)
        return result

    async def stream_query(self, question: str, mode: str = "hybrid",
                           kb_id: str = "", top_k: int = 5,
                           enable_web_search: bool = False,
                           session_id: Optional[str] = None,
                           kb_ids: Optional[List[str]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        if session_id:
            conversation_memory.create_session(session_id)
            conversation_memory.add_message(session_id, "user", question)

        effective_question = await self._smart_rewrite(question, session_id)
        retrieval_top_k = max(
            top_k * settings.RAG_RETRIEVAL_MULTIPLIER,
            settings.RAG_RETRIEVAL_MIN_K,
            30,
            settings.RAG_TOP_K,
        )

        retrieval_started = time.monotonic()
        expanded_queries = []
        if mode == "hybrid" and self._glm and settings.QUERY_EXPANSION_ENABLED:
            try:
                expanded_queries = await self.expand_query(effective_question, num_variants=2)
            except Exception as e:
                logger.warning(f"stream query expansion failed: {e}")

        if mode == "keyword":
            if self._hybrid_engine:
                results = self._hybrid_engine.bm25_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id, kb_ids=kb_ids)
            else:
                results = vector_store.keyword_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id)
        elif mode == "semantic":
            query_embedding = await self._get_query_embedding_async(effective_question)
            if embedding_service.is_degraded:
                if self._hybrid_engine:
                    results = self._hybrid_engine.bm25_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id, kb_ids=kb_ids)
                else:
                    results = vector_store.keyword_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id)
            else:
                results = vector_store.search(
                    query_embedding=query_embedding,
                    top_k=retrieval_top_k,
                    threshold=0.0,
                    kb_id=kb_id,
                    kb_ids=kb_ids,
                )
        else:
            query_embedding = await self._get_query_embedding_async(effective_question)
            vector_results = [] if embedding_service.is_degraded else vector_store.search(
                query_embedding=query_embedding,
                top_k=retrieval_top_k,
                threshold=0.0,
                kb_id=kb_id,
                kb_ids=kb_ids,
            )

            if self._hybrid_engine:
                results = self._hybrid_engine.search(
                    query=effective_question,
                    top_k=retrieval_top_k,
                    kb_id=kb_id,
                    kb_ids=kb_ids,
                    vector_results=vector_results,
                )
            else:
                keyword_results = vector_store.keyword_search(effective_question, top_k=retrieval_top_k, kb_id=kb_id)
                original_results = []
                if effective_question != question and not embedding_service.is_degraded:
                    original_embedding = await self._get_query_embedding_async(question)
                    original_results = vector_store.search(
                        query_embedding=original_embedding,
                        top_k=retrieval_top_k,
                        threshold=0.0,
                        kb_id=kb_id,
                    )
                results = self._merge_results_enhanced(vector_results, keyword_results, original_results)

            if expanded_queries and len(expanded_queries) > 1:
                for eq in expanded_queries[1:]:
                    try:
                        eq_vector_results = [] if embedding_service.is_degraded else vector_store.search(
                            query_embedding=await self._get_query_embedding_async(eq),
                            top_k=retrieval_top_k,
                            threshold=0.0,
                            kb_id=kb_id,
                            kb_ids=kb_ids,
                        )
                        eq_keyword_results = vector_store.keyword_search(eq, top_k=retrieval_top_k, kb_id=kb_id)
                        if self._hybrid_engine:
                            eq_results = self._hybrid_engine.search(
                                query=eq, top_k=retrieval_top_k, kb_id=kb_id,
                                kb_ids=kb_ids, vector_results=eq_vector_results,
                            )
                        else:
                            eq_results = self._merge_results_enhanced(eq_vector_results, eq_keyword_results, [])
                        results = self._fuse_ranked_results([results, eq_results], retrieval_top_k, [1.0, 0.7])
                    except Exception as e:
                        logger.warning(f"stream expanded query failed: {e}")

        telemetry.record("retrieval_completed", source="rag", session_id=session_id or "",
                         properties={"mode": mode, "vector_candidates": len(vector_results) if isinstance(locals().get("vector_results"), list) else 0,
                                     "hybrid_candidates": len(results), "retrieval_top_k": retrieval_top_k,
                                     "duration_ms": round((time.monotonic() - retrieval_started) * 1000, 1),
                                     "embedding_degraded": embedding_service.is_degraded, "stream": True})
        rerank_started = time.monotonic()
        remote_rerank_ready = bool(getattr(self._reranker, "_remote_config", {}))
        if settings.RERANK_ENABLED and remote_rerank_ready:
            reranked_results = await self._reranker.rerank(effective_question, results, top_k=top_k)
        elif settings.RERANK_ENABLED and self._enhanced_reranker:
            reranked_results = await self._enhanced_reranker.rerank(effective_question, results, top_k=top_k)
        else:
            reranked_results = await self._reranker.rerank(effective_question, results, top_k=top_k)
        telemetry.record("rerank_completed", source="rag", session_id=session_id or "",
                         properties={"provider": next((str(item.get("rerank_provider") or "") for item in reranked_results if isinstance(item, dict) and item.get("rerank_provider")), "rule_based"),
                                     "candidates": len(results), "returned": len(reranked_results),
                                     "duration_ms": round((time.monotonic() - rerank_started) * 1000, 1), "stream": True})

        web_results = await self._web_search(effective_question) if enable_web_search else []
        chunks = self._prepare_display_chunks(reranked_results)
        images = []
        seen_image_urls = set()
        for chunk in chunks[:top_k]:
            for image in extract_images(
                chunk.get("display_text") or chunk.get("content") or "",
                source_path=(chunk.get("metadata") or {}).get("source_path")
                or (chunk.get("metadata") or {}).get("path") or "",
                max_images=max(1, 12 - len(images)),
            ):
                if image.get("url") not in seen_image_urls:
                    seen_image_urls.add(image.get("url"))
                    images.append(image)
            if len(images) >= 12:
                break
        has_kb_results = bool(chunks)
        context = self._build_context(chunks) if has_kb_results else ""
        if web_results:
            web_context = "\n\n".join([
                f"[Web {i + 1}] ({wr.get('title', '')})\n{wr.get('snippet', '')}"
                for i, wr in enumerate(web_results)
            ])
            context = f"{context}\n\n--- Web search results ---\n{web_context}" if context else f"--- Web search results ---\n{web_context}"

        metadata = {
            "has_results": True,
            "result_count": len(reranked_results),
            "display_chunk_count": len(chunks),
            "web_result_count": len(web_results),
            "rewritten_query": effective_question if effective_question != question else None,
            "session_id": session_id,
            "embedding_degraded": embedding_service.is_degraded,
            "embedding_error": embedding_service.last_error,
            "embedding_provider": embedding_service.provider,
            "retrieval_top_k": retrieval_top_k,
            "top_k_used": top_k,
            "stream": True,
        }
        backend_status = getattr(vector_store, "backend_status", {}) or {}
        index_status = vector_store.index_model_status(getattr(embedding_service, "model", settings.EMBEDDING_MODEL))
        rerank_provider = next((str(item.get("rerank_provider") or "") for item in reranked_results
                                if isinstance(item, dict) and item.get("rerank_provider")), "rule_based")
        metadata["retrieval_diagnostics"] = {
            "vector_backend": backend_status.get("backend", "local"),
            "milvus_ready": bool(backend_status.get("backend") == "milvus" and backend_status.get("ready")),
            "embedding_model": getattr(embedding_service, "model", settings.EMBEDDING_MODEL),
            "index_models": index_status.get("models", []),
            "index_model_consistent": index_status.get("consistent", False),
            "vector_candidates": len(vector_results) if isinstance(locals().get("vector_results"), list) else (len(results) if mode == "semantic" else 0),
            "hybrid_candidates": len(results),
            "rerank_provider": rerank_provider,
            "rerank_candidates": len(results),
            "rerank_returned": len(reranked_results),
        }
        yield {
            "type": "meta",
            "sources": chunks[:top_k],
            "chunks": chunks[:top_k],
            "images": images,
            "web_results": web_results,
            "metadata": metadata,
        }

        conversation_history = []
        if session_id:
            conversation_history = conversation_memory.build_context_for_llm(
                session_id, question, max_tokens=1500
            )

        generation_started = time.monotonic()
        answer_parts: List[str] = []
        if not self._glm:
            fallback = self._rule_based_answer(question, context, has_kb_results)
            answer_parts.append(fallback)
            yield {"type": "token", "content": fallback}
        else:
            messages = self._build_stream_answer_messages(
                question=question,
                context=context,
                has_kb_results=has_kb_results,
                has_web=enable_web_search and bool(web_results),
                conversation_history=conversation_history,
                citation_count=min(len(chunks), top_k),
            )
            try:
                async for token in self._stream_llm_tokens(messages):
                    answer_parts.append(token)
                    yield {"type": "token", "content": token}
            except Exception as e:
                logger.error(f"stream answer generation failed: {e}")
                fallback = self._rule_based_answer(question, context, has_kb_results)
                answer_parts = [fallback]
                yield {"type": "replace", "content": fallback}

        answer = "".join(answer_parts).strip()
        telemetry.record("generation_completed", source="rag", session_id=session_id or "",
                         properties={"mode": mode, "duration_ms": round((time.monotonic() - generation_started) * 1000, 1) if 'generation_started' in locals() else 0,
                                     "has_evidence": has_kb_results, "answer_chars": len(answer), "degraded": not bool(self._glm), "stream": True})
        citation_count = min(len(chunks), top_k)
        if has_kb_results and citation_count > 0 and answer and not re.search(r"(\[\d+\]|【\d+】)", answer):
            repaired = await self._repair_answer_citations(question, answer, context, citation_count)
            if repaired and repaired != answer:
                answer = repaired
                yield {"type": "replace", "content": repaired}

        if session_id:
            conversation_memory.add_message(
                session_id,
                "assistant",
                answer,
                metadata={"chunks": chunks[:top_k], "images": images},
            )
            await self._compress_session_if_needed(session_id)

        yield {
            "type": "done",
            "answer": answer,
            "sources": chunks[:top_k],
            "chunks": chunks[:top_k],
            "images": images,
            "web_results": web_results,
            "metadata": metadata,
        }

    def _build_stream_answer_messages(self, question: str, context: str,
                                      has_kb_results: bool = True,
                                      has_web: bool = False,
                                      conversation_history: Optional[List[Dict[str, str]]] = None,
                                      citation_count: int = 0) -> List[Dict[str, str]]:
        web_note = "\nIf web search content is used, mark it as [web source]." if has_web else ""
        if has_kb_results or has_web:
            system_prompt = (
                "你是企业级知识库问答助手。必须优先基于给定上下文回答，不要编造。"
                "回答要简洁、准确、结构化。只要一句话基于上下文，就在句末标注引用编号，格式使用 [1]、[2]。"
                f"只能使用本次提供的来源编号 1 到 {max(citation_count, 1)}。"
                "不要在末尾额外输出相关文档列表。"
                "如果内容属于概括、解释或补充，请明确保持克制；上下文不能支持时直接说明。"
                f"{web_note}"
            )
        else:
            system_prompt = (
                "你是智能问答助手。当前知识库没有命中相关上下文时，可以基于通用知识简洁回答，"
                "但必须说明这不是来自知识库原文。"
            )

        user_message = (
            f"上下文信息：\n{context}\n\n用户问题：{question}\n\n请基于以上上下文回答："
            if context
            else f"用户问题：{question}\n\n请直接回答："
        )
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            for msg in conversation_history[-6:]:
                if msg.get("role") in {"system", "user", "assistant"}:
                    messages.append({"role": msg["role"], "content": msg.get("content", "")})
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _stream_llm_tokens(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _worker():
            try:
                stream = self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.0,
                    stream=True,
                )
                for chunk in stream:
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = getattr(chunk.choices[0], "delta", None)
                    token = getattr(delta, "content", None) if delta else None
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, ("token", token))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=_worker, daemon=True).start()

        while True:
            kind, payload = await asyncio.wait_for(queue.get(), timeout=120)
            if kind == "token":
                yield payload
            elif kind == "error":
                raise RuntimeError(payload)
            else:
                break

    async def _compress_session_if_needed(self, session_id: str) -> None:
        if not conversation_memory.needs_compression(session_id):
            return

        payload = conversation_memory.get_messages_for_compression(session_id)
        if not payload:
            return

        started = time.monotonic()
        telemetry.record("context_compression_started", source="rag", session_id=session_id,
                         properties={"message_count": len(payload.get("messages") or []),
                                     "has_existing_summary": bool(payload.get("existing_summary"))})
        if not self._glm:
            telemetry.record("context_compression_skipped", source="rag", session_id=session_id,
                             properties={"reason": "llm_unavailable"})
            return

        existing_summary = payload.get("existing_summary", "")
        messages = payload.get("messages", [])
        indices = payload.get("indices", [])
        transcript = "\n".join(
            f"{'用户' if msg.get('role') == 'user' else '助手'}：{msg.get('content', '')}"
            for msg in messages
        )
        prompt = (
            "你是对话记忆压缩助手。请只根据给定历史对话生成一段连贯摘要，用于后续问答记忆。\n"
            "严格要求：\n"
            "1. 只保留用户目标、关键约束、已经确认的决定、未解决事项和重要上下文。\n"
            "2. 不得新增、推测或改写成对话中没有的信息。\n"
            "3. 如果已有历史摘要，请把新对话合并进去，形成更新后的摘要。\n"
            "4. 输出一段摘要，不要标题、编号、解释。\n\n"
            f"已有历史摘要：{existing_summary or '无'}\n\n"
            f"待压缩对话：\n{transcript}\n\n"
            "更新后的历史对话摘要："
        )

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=700,
                    temperature=0.0,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)
            summary = (response.choices[0].message.content or "").strip()
            if summary:
                applied = conversation_memory.apply_summary_compression(session_id, indices, summary)
                telemetry.record("context_compressed", source="rag", session_id=session_id,
                                 properties={"compressed_messages": len(indices), "summary_chars": len(summary),
                                             "applied": applied, "duration_ms": round((time.monotonic() - started) * 1000, 1)})
            else:
                telemetry.record("context_compression_failed", source="rag", session_id=session_id,
                                 properties={"reason": "empty_summary", "duration_ms": round((time.monotonic() - started) * 1000, 1)})
        except Exception as e:
            logger.warning(f"会话摘要压缩失败，保留原始历史: {e}")
            telemetry.record("context_compression_failed", source="rag", session_id=session_id,
                             properties={"error_type": type(e).__name__, "duration_ms": round((time.monotonic() - started) * 1000, 1)})

    async def _web_search(self, query: str) -> List[Dict]:
        try:
            from app.rag.web_search import web_search_service
            results = await web_search_service.search(query, max_results=3)
            return results
        except Exception as e:
            logger.error(f"联网搜索失败: {e}")
            return []

    def _get_query_embedding(self, text: str) -> list:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = loop.run_in_executor(pool, self._get_query_embedding_sync, text)
                return asyncio.get_event_loop().run_until_complete(result)
        except RuntimeError:
            return self._get_query_embedding_sync(text)

    def _get_query_embedding_sync(self, text: str) -> list:
        if self._glm and settings.OPENAI_API_KEY:
            try:
                response = self._glm.embeddings.create(input=text, model=settings.EMBEDDING_MODEL)
                return response.data[0].embedding
            except Exception as e:
                logger.error(f"获取嵌入向量失败: {e}")

        import hashlib
        import numpy as np
        hash_val = hashlib.md5(text.encode()).hexdigest()
        seed = int(hash_val[:8], 16)
        rng = np.random.RandomState(seed)
        return rng.randn(settings.EMBEDDING_DIMENSION).tolist()

    async def _get_query_embedding_async(self, text: str) -> list:
        from app.rag.embedding_service import embedding_service
        return await embedding_service.embed_text(text)

    def _merge_results(self, vector_results: List[Dict], keyword_results: List[Dict]) -> List[Dict]:
        return self._merge_results_enhanced(vector_results, keyword_results, [])

    @staticmethod
    def _fuse_ranked_results(result_lists: List[List[Dict]], top_k: int,
                             weights: Optional[List[float]] = None) -> List[Dict]:
        """Fuse already ranked candidate lists without comparing score scales."""
        fused: Dict[str, Dict[str, Any]] = {}
        weights = weights or [1.0] * len(result_lists)
        for list_index, items in enumerate(result_lists):
            weight = float(weights[list_index] if list_index < len(weights) else 1.0)
            for rank, item in enumerate(items or [], 1):
                cid = str(item.get("chunk_id") or "")
                if not cid:
                    continue
                target = fused.setdefault(cid, dict(item))
                target["score"] = float(target.get("score", 0.0) or 0.0) + weight / (60.0 + rank)
                target.setdefault("fusion_sources", []).append(list_index)
        return sorted(fused.values(), key=lambda item: item.get("score", 0.0), reverse=True)[:top_k]

    def _merge_results_enhanced(self, vector_results: List[Dict],
                                 keyword_results: List[Dict],
                                 original_query_results: List[Dict]) -> List[Dict]:
        score_map = {}
        v_weight = settings.HYBRID_SEARCH_WEIGHT_VECTOR
        k_weight = settings.HYBRID_SEARCH_WEIGHT_KEYWORD
        o_weight = 0.2

        max_v_score = max((r["score"] for r in vector_results), default=1.0) or 1.0
        max_k_score = max((r["score"] for r in keyword_results), default=1.0) or 1.0
        max_o_score = max((r["score"] for r in original_query_results), default=1.0) or 1.0

        for r in vector_results:
            cid = r["chunk_id"]
            normalized_score = r["score"] / max_v_score if max_v_score > 0 else 0
            score_map[cid] = {
                "chunk_id": cid,
                "doc_id": r["doc_id"],
                "content": r["content"],
                "score": normalized_score * v_weight,
                "metadata": r.get("metadata", {}),
                "_source": "vector",
            }

        for r in keyword_results:
            cid = r["chunk_id"]
            normalized_score = r["score"] / max_k_score if max_k_score > 0 else 0
            if cid in score_map:
                score_map[cid]["score"] += normalized_score * k_weight
                score_map[cid]["score"] += 0.1
                score_map[cid]["_source"] = "both"
            else:
                score_map[cid] = {
                    "chunk_id": cid,
                    "doc_id": r["doc_id"],
                    "content": r["content"],
                    "score": normalized_score * k_weight,
                    "metadata": r.get("metadata", {}),
                    "_source": "keyword",
                }

        for r in original_query_results:
            cid = r["chunk_id"]
            if cid in score_map:
                continue
            normalized_score = r["score"] / max_o_score if max_o_score > 0 else 0
            score_map[cid] = {
                "chunk_id": cid,
                "doc_id": r["doc_id"],
                "content": r["content"],
                "score": normalized_score * o_weight,
                "metadata": r.get("metadata", {}),
                "_source": "original",
            }

        results = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
        output = []
        for r in results:
            r.pop("_source", None)
            output.append(r)
        return output[:max(settings.RAG_TOP_K * 2, 20)]

    def _prepare_display_chunks(self, results: List[Dict]) -> List[Dict]:
        chunks = []
        seen_display_chunks = set()

        for r in results:
            meta = r.get("metadata", {})
            child_content = r.get("content", "")
            parent_content = meta.get("parent_content", "") or ""
            parent_chunk_id = meta.get("parent_chunk_id", "") or ""
            display_content = parent_content or child_content
            display_chunk_id = parent_chunk_id or r["chunk_id"]

            if display_chunk_id in seen_display_chunks:
                continue
            seen_display_chunks.add(display_chunk_id)

            raw_score = float(r.get("score", 0) or 0)
            chunks.append({
                "chunk_id": r["chunk_id"],
                "display_chunk_id": display_chunk_id,
                "text": display_content,
                "content": display_content,
                "display_text": display_content,
                "child_text": child_content,
                "parent_text": parent_content,
                "score": raw_score,
                "raw_score": raw_score,
                "rerank_score": r.get("rerank_score"),
                "source": r.get("doc_id", ""),
                "title": meta.get("title") or meta.get("document_title") or r.get("doc_id", ""),
                "page": meta.get("page") or meta.get("page_number", ""),
                "metadata": meta,
                "is_child": bool(meta.get("is_child", False)),
                "parent_chunk_id": parent_chunk_id,
                "display_content_length": len(display_content),
                "child_content_length": len(child_content),
                "parent_content_length": len(parent_content),
            })

        return self._calibrate_display_scores(chunks)

    def _calibrate_display_scores(self, chunks: List[Dict]) -> List[Dict]:
        if not chunks:
            return chunks

        primary_scores = []
        for chunk in chunks:
            rerank_score = chunk.get("rerank_score")
            raw_score = float(chunk.get("score", 0) or 0)
            if isinstance(rerank_score, (int, float)) and rerank_score > 0:
                primary_scores.append(float(rerank_score))
            else:
                primary_scores.append(raw_score)
        max_score = max(primary_scores) if primary_scores else 0.0
        min_score = min(primary_scores) if primary_scores else 0.0
        span = max(max_score - min_score, 1e-9)
        total = max(len(chunks), 1)

        for idx, chunk in enumerate(chunks):
            raw_score = float(chunk.get("raw_score", chunk.get("score", 0)) or 0)
            primary = primary_scores[idx]
            normalized = (primary - min_score) / span if total > 1 else 1.0
            rank_strength = 1.0 - (idx / max(total - 1, 1)) if total > 1 else 1.0
            source_bonus = 0.04 if chunk.get("metadata", {}).get("_source") == "both" else 0.0
            calibrated = 0.52 + normalized * 0.28 + rank_strength * 0.15 + source_bonus
            if idx == 0:
                calibrated = max(calibrated, 0.86)
            calibrated = max(0.35, min(0.96, calibrated))

            chunk["score"] = calibrated
            chunk["display_score"] = calibrated
            chunk["similarity"] = raw_score
            chunk["score_label"] = "相关度"
            chunk["score_detail"] = {
                "raw_similarity": round(raw_score, 4),
                "rank_score": round(primary, 4),
                "normalized": round(normalized, 4),
                "rank": idx + 1,
            }

        return chunks

    def _build_context(self, chunks: List[Dict]) -> str:
        parts = []
        for i, chunk in enumerate(chunks[:8]):
            content = chunk.get("display_text") or chunk.get("text") or chunk.get("content") or ""
            if len(content) > 1500:
                content = content[:1500] + "..."
            source_name = chunk.get("title") or chunk.get("source") or f"来源{i + 1}"
            page = chunk.get("page")
            meta_line = f"文件：{source_name}"
            if page:
                meta_line += f" | 页码：{page}"
            meta_line += f" | 相关度：{chunk.get('score', 0):.2f}"
            parts.append(f"【{i + 1}】 {meta_line}\n{content}")
        return "\n\n".join(parts)

    async def _generate_answer(self, question: str, context: str,
                               has_kb_results: bool = True,
                               has_web: bool = False,
                               conversation_history: Optional[List[Dict[str, str]]] = None,
                               citation_count: int = 0) -> str:
        if not self._glm:
            return self._rule_based_answer(question, context, has_kb_results)

        web_note = "\n\n注意：回答中如果引用了联网搜索结果，请标注【联网来源】。" if has_web else ""

        if has_kb_results or has_web:
            system_prompt = f"""你是一个企业级知识库问答助手。请严格基于提供的上下文信息回答问题。
如果上下文中没有相关信息，请明确告知用户。不要编造任何信息。回答要简洁、准确、结构化。
优先使用知识库中的内容回答，联网搜索结果仅作为补充参考。
注意保持对话连贯性，参考之前的对话历史理解用户意图。

输出规则：
1. 只要一句话直接基于知识库上下文，就必须在句末添加引用编号，格式只能是【1】、【2】这样的样式。
2. 你只能使用本次提供的来源编号 1 到 {max(citation_count, 1)}，不要编造不存在的编号。
3. 如果某句话属于你的归纳、解释、补充或推断，而不是上下文直接明确写出的内容，请在句末标注【模型推断】；但只要上下文能支持，就优先给来源编号，不要滥用【模型推断】。
4. 不要把【模型推断】和来源编号混在同一个标签里；直接依据就标来源编号，推断就标【模型推断】。
5. 如果原问题可以从上下文直接回答，就不要回避作答，也不要说“用户没有提供具体问题”之类的话。
6. 不要在文末额外再写“参考资料”清单，引用必须直接落在对应句子后面。{web_note}"""
        else:
            system_prompt = """你是一个智能问答助手。用户提出了一个知识库中没有相关内容的问题，请你基于自身知识直接回答。
回答要简洁、准确、友好。注意保持对话连贯性，参考之前的对话历史理解用户意图。
回答完毕后，在末尾添加一行提示：
"[提示] 以上回答基于通用知识，如需查询知识库中的专业内容，请上传相关文档到知识库后提问。"
"""

        user_message = f"上下文信息：\n{context}\n\n用户问题：{question}\n\n请基于以上上下文回答问题：" if context else f"用户问题：{question}\n\n请直接回答："

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history[-6:]:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_message})

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.0,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=60)
            answer = response.choices[0].message.content or ""
            if has_kb_results and citation_count > 0 and not re.search(r"【\d+】", answer):
                answer = await self._repair_answer_citations(question, answer, context, citation_count)
            return answer
        except Exception as e:
            logger.error(f"GLM 生成回答失败: {e}")
            return self._rule_based_answer(question, context, has_kb_results)

    async def _repair_answer_citations(self, question: str, answer: str, context: str, citation_count: int) -> str:
        if not self._glm:
            return answer

        messages = [
            {
                "role": "system",
                "content": (
                    "你是知识库回答的引用修复助手。你的任务不是重写观点，而是在尽量保持原回答含义的前提下，"
                    "为每个基于上下文的句子补上来源编号【1】、【2】。如果上下文能够支持一句话，就必须补上来源编号；"
                    "只有完全无法从上下文支持时，才允许标【模型推断】。"
                    "禁止输出额外说明，直接返回修复后的最终答案。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"来源上下文：\n{context}\n\n用户问题：{question}\n\n原始回答：\n{answer}\n\n"
                    f"请为上面的回答补上内联引用。只能使用 1 到 {max(citation_count, 1)} 号来源。"
                ),
            },
        ]

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=messages,
                    max_tokens=2048,
                    temperature=0.0,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)
            repaired = response.choices[0].message.content or answer
            return repaired
        except Exception as e:
            logger.warning(f"引用修复失败，返回原回答: {e}")
            return answer

    def _rule_based_answer(self, question: str, context: str,
                           has_kb_results: bool = True) -> str:
        if not context.strip() and not has_kb_results:
            return "暂无知识库相关内容。如需查询专业知识，请上传文档到知识库后提问。"

        if not context.strip():
            return "暂无相关有效信息，知识库中未找到匹配内容。"

        parts = ["基于知识库检索结果，以下是相关信息：", ""]
        lines = context.strip().split("\n")
        count = 0
        for line in lines:
            if line.strip() and count < 8:
                parts.append(line.strip())
                count += 1

        return "\n".join(parts)

    async def rewrite_query(self, question: str, session_id: Optional[str] = None) -> str:
        normalized = conversation_memory.normalize_query_text(question)
        if session_id:
            normalized = conversation_memory.rewrite_with_context(session_id, normalized)

        if not self._glm:
            return self._rewrite_rule_based(normalized)

        context_hint = ""
        if session_id:
            recent = conversation_memory.get_history(session_id, last_n=6) or []
            recent_pairs = [
                f"{m.get('role')}: {m.get('content', '')[:300]}"
                for m in recent
                if m.get("content") and m.get("content") != question
            ]
            if recent_pairs:
                context_hint = "可参考的最近对话（只用于指代消解，不得新增事实）：\n" + "\n".join(recent_pairs[-4:]) + "\n\n"

        prompt = (
            "你是专业的知识库查询改写助手，请将用户的原始问题改写为适合向量检索的标准查询，严格遵守以下规则：\n"
            "1. 完整保留用户原始核心意图，不得添加、删减、篡改任何信息，不得引入外部假设\n"
            "2. 去除口语化语气词、冗余铺垫，改写为清晰、正式的完整疑问句\n"
            "3. 补全省略的主语、指代成分，消除表述歧义，主语必须完整明确\n"
            "4. 适当补充1个对应领域的标准术语，提升检索匹配度\n"
            "5. 只输出改写后的查询内容，不要任何解释、前缀、编号或后缀\n\n"
            f"{context_hint}原始问题：{normalized}\n"
            "改写后的查询："
        )

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=256,
                    temperature=0.0,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
            rewritten = self._clean_rewrite_output(response.choices[0].message.content or "")
            if rewritten and self._is_safe_rewrite(normalized, rewritten):
                return rewritten
            logger.info("查询改写安全校验未通过，回退到规则改写: %s -> %s", normalized, rewritten)
            return self._rewrite_rule_based(normalized)
        except Exception as e:
            logger.error(f"查询改写失败: {e}")
            return self._rewrite_rule_based(normalized)

    async def expand_query(self, question: str, num_variants: int = 3) -> List[str]:
        """查询扩展：生成多个同义/相关查询，提升检索召回率"""
        if not self._glm:
            return [self._rewrite_rule_based(question)]

        system_prompt = (
            "你是查询扩展引擎。为给定查询生成多个同义/相关变体，用于提升检索召回率。\n"
            "## 扩展策略\n"
            "1. 同义替换：用同义词替换核心词（如\"消防电梯\"↔\"消防梯\"）\n"
            "2. 上下位扩展：上位泛化（\"防火门\"→\"防火分隔设施\"）和下位具体化\n"
            "3. 补全表述：补全省略的限定词或上下文\n"
            "4. 视角转换：从不同角度描述同一需求\n"
            "## 要求\n"
            "- 每个变体必须语义等价或高度相关，不能偏移原意\n"
            "- 变体之间要有差异化，不要简单重复\n"
            f"- 生成{num_variants}个变体\n"
            "## 输出格式\n"
            "每行一个变体，不要编号、不要解释。"
        )

        try:
            def _call():
                return self._glm.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"扩展：{question}"},
                    ],
                    max_tokens=512,
                    temperature=0.5,
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
            content = response.choices[0].message.content.strip()
            variants = [v.strip().strip('"\'""''') for v in content.split('\n') if v.strip()]
            # 过滤无效或偏移过大的变体
            variants = [
                v for v in variants
                if len(v) >= 2 and v != question and self._is_safe_rewrite(question, v)
            ]
            # 合并原查询和变体，去重
            all_queries = [question] + variants[:num_variants]
            seen = set()
            unique = []
            for q in all_queries:
                if q not in seen:
                    seen.add(q)
                    unique.append(q)
            return unique
        except Exception as e:
            logger.error(f"查询扩展失败: {e}")
            return [self._rewrite_rule_based(question)]

    def _context_aware_rewrite(self, session_id: str, question: str) -> str:
        return conversation_memory.rewrite_with_context(session_id, self._rewrite_rule_based(question))

    async def _smart_rewrite(self, question: str, session_id: Optional[str] = None) -> str:
        """普通问答只做轻量规范化；LLM 标准改写由按钮显式触发。"""
        question = self._rewrite_rule_based(question)
        if session_id:
            question = conversation_memory.rewrite_with_context(session_id, question)
        if self._glm and settings.QUERY_REWRITE_ENABLED and settings.QUERY_REWRITE_AUTO_LLM_ENABLED:
            rewritten = await self.rewrite_query(question, session_id=session_id)
            if self._is_safe_rewrite(question, rewritten):
                return rewritten
        return question

    def _is_safe_rewrite(self, original: str, rewritten: str) -> bool:
        if not rewritten:
            return False
        original = original.strip()
        rewritten = rewritten.strip()
        if not original or not rewritten or rewritten == original:
            return False
        if len(rewritten) > max(len(original) * 4, 80):
            return False
        reject_markers = (
            "无法", "不能", "不清楚", "未提供", "没有提供", "输入为空",
            "请提供", "抱歉", "作为", "我无法", "I cannot", "cannot",
        )
        if any(marker.lower() in rewritten.lower() for marker in reject_markers):
            return False

        original_terms = self._rewrite_terms(original)
        rewritten_terms = self._rewrite_terms(rewritten)
        if not original_terms or not rewritten_terms:
            return len(rewritten) >= 2

        overlap = len(original_terms & rewritten_terms) / max(len(original_terms), 1)
        return overlap >= settings.QUERY_REWRITE_MIN_OVERLAP

    def _clean_rewrite_output(self, text: str) -> str:
        text = (text or "").strip()
        text = re.sub(r"^(改写后的查询|改写|查询|答案)[:：]\s*", "", text)
        text = re.sub(r"^[\-\*\d一二三四五六七八九十]+[\.、\)]\s*", "", text)
        text = text.strip().strip("\"'“”‘’`")
        return conversation_memory.normalize_query_text(text)

    @staticmethod
    def _rewrite_terms(text: str) -> set:
        import re
        terms = set(re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}', text.lower()))
        for segment in re.findall(r'[\u4e00-\u9fff]+', text):
            for i in range(len(segment) - 1):
                terms.add(segment[i:i + 2])
        return terms

    def _rewrite_rule_based(self, question: str) -> str:
        result = conversation_memory.normalize_query_text(question)
        replacements = {
            "rag": "RAG 检索增强生成",
            "llm": "LLM 大语言模型",
            "kg": "KG 知识图谱",
            "qa": "QA 问答系统",
            "向量库": "向量数据库",
            "重排": "重排序",
            "召回": "检索召回",
            "切片": "分块",
            "chunk切分": "chunk 分块",
            "embedding": "Embedding 向量化",
        }
        lowered = result.lower()
        for source, target in replacements.items():
            if source in lowered and target not in result:
                result = re.sub(re.escape(source), target, result, flags=re.IGNORECASE)

        return re.sub(r"\s+", " ", result).strip()


rag_service = RAGService()
