"""倒排索引模块 - O5 优化：替代全文档扫描，实现 O(log n) 关键词搜索。

倒排索引将每个词项映射到包含该词项的文档列表，
搜索时只需查找查询词项对应的倒排列表，避免全文档扫描。
支持增量索引更新。
"""

import os
import re
import json
import math
import logging
import threading
from typing import List, Dict, Any, Optional, Set
from collections import defaultdict, Counter

from app.core.config import settings

logger = logging.getLogger(__name__)


class InvertedIndex:
    """倒排索引，支持增量更新和快速关键词检索。

    数据结构：
    - _postings: Dict[str, Set[int]] - 词项 -> 包含该词项的文档ID集合
    - _doc_tokens: Dict[int, Counter] - 文档ID -> 词项频率
    - _doc_lengths: Dict[int, int] - 文档ID -> 文档长度（token数）
    - _idf_cache: Dict[str, float] - 词项 -> IDF值缓存
    """

    def __init__(self):
        self._postings: Dict[str, Set[int]] = defaultdict(set)
        self._doc_tokens: Dict[int, Counter] = {}
        self._doc_lengths: Dict[int, int] = {}
        self._idf_cache: Dict[str, float] = {}
        self._total_docs: int = 0
        self._avg_dl: float = 0.0
        self._lock = threading.RLock()
        self._index_file = os.path.join(settings.DATA_DIR, "inverted_index.json")
        self._dirty = False
        self._load()

    def _tokenize(self, text: str) -> List[str]:
        """分词：中文逐字+bigram+trigram，英文按词，数字保留。"""
        tokens = []
        # 中文：单字 + bigram + trigram
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        for segment in chinese_chars:
            for i in range(len(segment)):
                tokens.append(segment[i])
                if i < len(segment) - 1:
                    tokens.append(segment[i:i + 2])
                if i < len(segment) - 2:
                    tokens.append(segment[i:i + 3])

        # 英文
        english_words = re.findall(r'[a-zA-Z][a-zA-Z0-9]*', text.lower())
        tokens.extend(english_words)

        # 数字
        numbers = re.findall(r'\d+(?:\.\d+)?', text)
        tokens.extend(numbers)

        return tokens

    def add_document(self, doc_id: int, text: str) -> None:
        """添加文档到倒排索引。

        Args:
            doc_id: 文档内部 ID
            text: 文档文本内容
        """
        with self._lock:
            tokens = self._tokenize(text)
            token_freq = Counter(tokens)

            # 如果文档已存在，先移除旧索引
            if doc_id in self._doc_tokens:
                self._remove_document_internal(doc_id)

            self._doc_tokens[doc_id] = token_freq
            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1

            # 更新倒排列表
            for token in token_freq:
                self._postings[token].add(doc_id)

            # 更新 IDF 缓存和平均文档长度
            self._update_stats()
            self._dirty = True

    def add_documents_batch(self, docs: List[tuple]) -> None:
        """批量添加文档。

        Args:
            docs: [(doc_id, text), ...] 列表
        """
        with self._lock:
            for doc_id, text in docs:
                tokens = self._tokenize(text)
                token_freq = Counter(tokens)

                if doc_id in self._doc_tokens:
                    self._remove_document_internal(doc_id)

                self._doc_tokens[doc_id] = token_freq
                self._doc_lengths[doc_id] = len(tokens)
                self._total_docs += 1

                for token in token_freq:
                    self._postings[token].add(doc_id)

            self._update_stats()
            self._dirty = True

    def remove_document(self, doc_id: int) -> bool:
        """从倒排索引中移除文档。

        Args:
            doc_id: 文档 ID

        Returns:
            是否成功移除
        """
        with self._lock:
            if doc_id not in self._doc_tokens:
                return False
            self._remove_document_internal(doc_id)
            self._update_stats()
            self._dirty = True
            return True

    def _remove_document_internal(self, doc_id: int) -> None:
        """内部移除文档（不加锁）。"""
        old_tokens = self._doc_tokens.pop(doc_id, Counter())
        self._doc_lengths.pop(doc_id, None)
        self._total_docs -= 1

        for token in old_tokens:
            self._postings[token].discard(doc_id)
            if not self._postings[token]:
                del self._postings[token]
                self._idf_cache.pop(token, None)

    def _update_stats(self) -> None:
        """更新 IDF 缓存和平均文档长度。"""
        if self._total_docs > 0:
            self._avg_dl = sum(self._doc_lengths.values()) / self._total_docs
        else:
            self._avg_dl = 0.0

        # 增量更新 IDF
        for token in self._postings:
            df = len(self._postings[token])
            self._idf_cache[token] = math.log(
                (self._total_docs - df + 0.5) / (df + 0.5) + 1.0
            )

    def search(self, query: str, top_k: int = 10, kb_id: str = "",
               kb_index: Optional[Dict[str, List[int]]] = None) -> List[Dict]:
        """使用倒排索引进行 BM25 搜索。

        Args:
            query: 查询文本
            top_k: 返回前 k 个结果
            kb_id: 知识库 ID 过滤
            kb_index: 知识库索引 {kb_id: [doc_ids]}

        Returns:
            搜索结果列表
        """
        with self._lock:
            if self._total_docs == 0:
                return []

            query_tokens = self._tokenize(query)
            if not query_tokens:
                return []

            # 通过倒排索引快速找到候选文档
            candidate_docs: Set[int] = set()
            for token in query_tokens:
                if token in self._postings:
                    candidate_docs.update(self._postings[token])

            if not candidate_docs:
                return []

            # 知识库过滤
            if kb_id and kb_index:
                kb_doc_ids = set(kb_index.get(kb_id, []))
                candidate_docs = candidate_docs & kb_doc_ids

            # BM25 评分
            k1 = settings.BM25_K1
            b = settings.BM25_B
            scores: Dict[int, float] = {}

            for doc_id in candidate_docs:
                score = 0.0
                doc_tokens = self._doc_tokens.get(doc_id, Counter())
                dl = self._doc_lengths.get(doc_id, 0)

                for token in query_tokens:
                    tf = doc_tokens.get(token, 0)
                    if tf == 0:
                        continue
                    idf = self._idf_cache.get(token, 0.0)
                    numerator = tf * (k1 + 1)
                    denominator = tf + k1 * (1 - b + b * dl / max(self._avg_dl, 1e-6))
                    score += idf * numerator / denominator

                if score > 0:
                    scores[doc_id] = score

            # 排序返回
            sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            return [{"doc_id": doc_id, "score": score} for doc_id, score in sorted_docs[:top_k]]

    def get_posting_list(self, token: str) -> Set[int]:
        """获取词项的倒排列表。"""
        return self._postings.get(token, set())

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息。"""
        return {
            "total_docs": self._total_docs,
            "vocab_size": len(self._postings),
            "avg_doc_length": self._avg_dl,
            "index_size_mb": sum(len(v) for v in self._postings.values()) * 8 / (1024 * 1024),
        }

    def _load(self) -> None:
        """从文件加载倒排索引。"""
        if not os.path.exists(self._index_file):
            return
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._total_docs = data.get("total_docs", 0)
            self._avg_dl = data.get("avg_dl", 0.0)

            for token, doc_ids in data.get("postings", {}).items():
                self._postings[token] = set(doc_ids)

            for doc_id_str, token_freq in data.get("doc_tokens", {}).items():
                self._doc_tokens[int(doc_id_str)] = Counter(token_freq)

            for doc_id_str, length in data.get("doc_lengths", {}).items():
                self._doc_lengths[int(doc_id_str)] = length

            self._update_stats()
            logger.info(f"倒排索引加载完成: {self._total_docs} 文档, {len(self._postings)} 词项")
        except Exception as e:
            logger.error(f"倒排索引加载失败: {e}")

    def save(self) -> None:
        """持久化倒排索引到文件。"""
        if not self._dirty:
            return
        with self._lock:
            try:
                data = {
                    "total_docs": self._total_docs,
                    "avg_dl": self._avg_dl,
                    "postings": {token: list(doc_ids) for token, doc_ids in self._postings.items()},
                    "doc_tokens": {str(doc_id): dict(freq) for doc_id, freq in self._doc_tokens.items()},
                    "doc_lengths": {str(doc_id): length for doc_id, length in self._doc_lengths.items()},
                }
                os.makedirs(os.path.dirname(self._index_file), exist_ok=True)
                with open(self._index_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                self._dirty = False
                logger.info(f"倒排索引已保存: {self._total_docs} 文档")
            except Exception as e:
                logger.error(f"倒排索引保存失败: {e}")


# 全局倒排索引实例
inverted_index = InvertedIndex()
