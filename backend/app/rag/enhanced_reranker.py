import re
import math
import asyncio
import logging
from typing import List, Dict, Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class EnhancedReranker:
    def __init__(self):
        self._glm = None
        self._init_llm()

    def _init_llm(self):
        if not settings.OPENAI_API_KEY:
            return
        try:
            from openai import OpenAI
            self._glm = OpenAI(
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
        except Exception as e:
            logger.error(f"EnhancedReranker LLM 初始化失败: {e}")
            self._glm = None

    async def rerank(self, query: str, results: List[Dict], top_k: int = 5) -> List[Dict]:
        if not results:
            return []

        if len(results) <= top_k:
            return results

        scored_results = []
        for r in results:
            score = self._compute_composite_score(query, r)
            scored_results.append((r, score))

        scored_results.sort(key=lambda x: x[1], reverse=True)

        top_results = [r for r, s in scored_results[:top_k * 2]]

        if self._glm and len(top_results) > top_k:
            try:
                top_results = await self._rerank_with_llm(query, top_results, top_k)
            except Exception as e:
                logger.warning(f"LLM 重排序失败，使用规则回退: {e}")
                top_results = top_results[:top_k]
        else:
            top_results = top_results[:top_k]

        for r in top_results:
            if "rerank_score" not in r:
                r["rerank_score"] = r.get("score", 0)

        return top_results

    def _compute_composite_score(self, query: str, result: Dict) -> float:
        base_score = result.get("score", 0)
        content = result.get("content", "").lower()
        metadata = result.get("metadata", {})

        scores = {
            "base": base_score,
            "exact_match": self._exact_match_score(query, content),
            "keyword_coverage": self._keyword_coverage_score(query, content),
            "phrase_match": self._phrase_match_score(query, content),
            "position_score": self._position_score(query, content),
            "heading_relevance": self._heading_relevance(query, metadata),
            "block_type_score": self._block_type_score(metadata),
            "length_quality": self._length_quality_score(content),
            "semantic_density": self._semantic_density_score(query, content),
        }

        weights = {
            "base": 0.20,
            "exact_match": 0.15,
            "keyword_coverage": 0.15,
            "phrase_match": 0.12,
            "position_score": 0.05,
            "heading_relevance": 0.10,
            "block_type_score": 0.05,
            "length_quality": 0.08,
            "semantic_density": 0.10,
        }

        composite = sum(scores[k] * weights[k] for k in scores)
        result["rerank_score"] = composite
        result["rerank_details"] = {k: round(v, 4) for k, v in scores.items()}

        return composite

    def _exact_match_score(self, query: str, content: str) -> float:
        query_lower = query.lower()
        if query_lower in content:
            content_len = len(content)
            query_len = len(query_lower)
            density = query_len / max(content_len, 1)
            return min(1.0, 0.5 + density * 10)

        query_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', query_lower))
        if not query_words:
            return 0.0

        matched = sum(1 for w in query_words if len(w) >= 2 and w in content)
        return matched / max(len(query_words), 1)

    def _keyword_coverage_score(self, query: str, content: str) -> float:
        query_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', query.lower()))
        content_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', content.lower()))

        if not query_words:
            return 0.0

        meaningful_query = {w for w in query_words if len(w) >= 2}
        if not meaningful_query:
            return 0.0

        covered = len(meaningful_query & content_words)
        return covered / max(len(meaningful_query), 1)

    def _phrase_match_score(self, query: str, content: str) -> float:
        query_lower = query.lower()
        phrases = re.findall(r'[\u4e00-\u9fff]{2,}|\w{2,}', query_lower)

        if not phrases:
            return 0.0

        matched = 0
        total_len = 0
        for phrase in phrases:
            if len(phrase) >= 2 and phrase in content:
                matched += len(phrase)
            total_len += len(phrase)

        return matched / max(total_len, 1)

    def _position_score(self, query: str, content: str) -> float:
        query_lower = query.lower()
        first_pos = content.find(query_lower)
        if first_pos == -1:
            query_words = re.findall(r'[\u4e00-\u9fff]+|\w+', query_lower)
            positions = []
            for w in query_words:
                if len(w) >= 2:
                    pos = content.find(w)
                    if pos != -1:
                        positions.append(pos)
            if positions:
                first_pos = min(positions)
            else:
                return 0.0

        content_len = max(len(content), 1)
        relative_pos = first_pos / content_len
        return max(0, 1.0 - relative_pos)

    def _heading_relevance(self, query: str, metadata: Dict) -> float:
        heading = metadata.get("heading", "").lower()
        if not heading:
            return 0.3

        query_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', query.lower()))
        heading_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', heading))

        if not query_words:
            return 0.3

        overlap = len(query_words & heading_words)
        coverage = overlap / max(len(query_words), 1)

        heading_level = metadata.get("heading_level", 0)
        level_bonus = {1: 0.3, 2: 0.2, 3: 0.1}.get(heading_level, 0.05)

        return min(1.0, coverage + level_bonus)

    def _block_type_score(self, metadata: Dict) -> float:
        block_type = metadata.get("block_type", "")
        type_scores = {
            "title": 0.9,
            "heading_h1": 0.85,
            "heading_h2": 0.8,
            "heading_h3": 0.75,
            "paragraph": 0.6,
            "table": 0.7,
            "code_block": 0.5,
            "image": 0.3,
            "caption": 0.4,
            "footnote": 0.2,
            "abstract": 0.8,
            "references": 0.3,
        }
        return type_scores.get(block_type, 0.5)

    def _length_quality_score(self, content: str) -> float:
        length = len(content)
        if length < 20:
            return 0.2
        elif length < 50:
            return 0.4
        elif length < 200:
            return 0.7
        elif length < 1000:
            return 1.0
        elif length < 2000:
            return 0.8
        else:
            return 0.5

    def _semantic_density_score(self, query: str, content: str) -> float:
        query_words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', query.lower()))
        content_words = re.findall(r'[\u4e00-\u9fff]+|\w+', content.lower())

        if not query_words or not content_words:
            return 0.0

        meaningful_query = {w for w in query_words if len(w) >= 2}
        if not meaningful_query:
            return 0.0

        content_counter = {}
        for w in content_words:
            if len(w) >= 2:
                content_counter[w] = content_counter.get(w, 0) + 1

        density = 0.0
        for w in meaningful_query:
            if w in content_counter:
                tf = content_counter[w]
                density += math.log1p(tf)

        max_possible = math.log1p(len(content_words)) * len(meaningful_query)
        return density / max(max_possible, 1e-6)

    async def _rerank_with_llm(self, query: str, results: List[Dict], top_k: int) -> List[Dict]:
        system_prompt = """你是一个专业的内容相关性评估专家。给定一个查询和多个文本片段，你需要：
1. 评估每个片段与查询的语义相关性（0-10分）
2. 评估每个片段的信息密度和质量（0-10分）
3. 综合考虑相关性、信息量和准确性，选出最精准的top_k个片段

返回一个JSON数组，每个元素是 {"index": 片段索引, "relevance": 相关性分数, "quality": 质量分数}
按综合得分从高到低排列，只返回top_k个。格式示例：
[{"index": 2, "relevance": 9, "quality": 8}, {"index": 0, "relevance": 7, "quality": 7}]"""

        snippets = ""
        for i, r in enumerate(results[:15]):
            content_preview = r['content'][:400]
            meta = r.get('metadata', {})
            block_type = meta.get('block_type', 'unknown')
            heading = meta.get('heading', '')
            snippets += f"\n[{i}] (类型:{block_type}, 标题:{heading})\n{content_preview}"

        user_message = f"查询：{query}\n\n文本片段：{snippets}\n\n请评估并返回最相关的{top_k}个片段："

        def _call():
            return self._glm.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=512,
                temperature=0.0,
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
        raw = response.choices[0].message.content.strip()

        import json
        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if json_match:
            try:
                scored_list = json.loads(json_match.group())
                reranked = []
                for item in scored_list:
                    idx = item.get("index", -1)
                    if isinstance(idx, int) and 0 <= idx < len(results):
                        r = results[idx]
                        relevance = item.get("relevance", 5)
                        quality = item.get("quality", 5)
                        r["rerank_score"] = (relevance * 0.6 + quality * 0.4) / 10.0
                        r["rerank_details"] = {
                            "llm_relevance": relevance,
                            "llm_quality": quality,
                        }
                        reranked.append(r)
                if reranked:
                    return reranked[:top_k]
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"LLM 重排序结果解析失败: {e}")

        return results[:top_k]


enhanced_reranker = EnhancedReranker()
