import os
import re
import json
import math
import logging
import threading
from typing import List, Dict, Any, Optional, Set
from collections import Counter

from app.core.config import settings

logger = logging.getLogger(__name__)


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self.k1 = k1
        self.b = b
        self.epsilon = epsilon
        self._corpus_size: int = 0
        self._avg_dl: float = 0.0
        self._doc_freqs: List[Dict[str, int]] = []
        self._doc_lens: List[int] = []
        self._idf: Dict[str, float] = {}
        self._doc_contents: List[str] = []
        self._doc_ids: List[str] = []
        self._chunk_ids: List[str] = []
        self._metadata: List[Dict] = []
        self._lock = threading.RLock()
        self._kb_index: Dict[str, List[int]] = {}
        self._doc_index: Dict[str, List[int]] = {}

    def _tokenize(self, text: str) -> List[str]:
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        tokens = []
        for segment in chinese_chars:
            for i in range(len(segment)):
                tokens.append(segment[i])
                if i < len(segment) - 1:
                    tokens.append(segment[i:i + 2])
                    if i < len(segment) - 2:
                        tokens.append(segment[i:i + 3])

        english_words = re.findall(r'[a-zA-Z][a-zA-Z0-9]*', text.lower())
        tokens.extend(english_words)

        numbers = re.findall(r'\d+(?:\.\d+)?', text)
        tokens.extend(numbers)

        return tokens

    def add_documents(self, chunks: List[Dict], recalculate: bool = True) -> None:
        with self._lock:
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id", "")
                doc_id = chunk.get("doc_id", "")
                content = chunk.get("content", "")
                metadata = chunk.get("metadata", {})

                if not content.strip():
                    continue

                tokens = self._tokenize(content)
                doc_freq = Counter(tokens)

                self._doc_contents.append(content)
                self._doc_ids.append(doc_id)
                self._chunk_ids.append(chunk_id)
                self._metadata.append(metadata)
                self._doc_freqs.append(dict(doc_freq))
                self._doc_lens.append(len(tokens))
                self._corpus_size += 1

                idx = self._corpus_size - 1
                if doc_id:
                    self._doc_index.setdefault(doc_id, []).append(idx)
                kb_id = metadata.get("kb_id", "")
                if kb_id:
                    self._kb_index.setdefault(kb_id, []).append(idx)

            if recalculate:
                self._calc_idf()
                self._calc_avg_dl()

    def finalize_batch(self) -> None:
        with self._lock:
            self._calc_idf()
            self._calc_avg_dl()

    def delete_by_doc(self, doc_id: str) -> int:
        """删除指定文档的所有记录"""
        with self._lock:
            indices_to_remove = set(self._doc_index.get(doc_id, []))
            if not indices_to_remove:
                return 0

            removed = len(indices_to_remove)
            indices_to_keep = [i for i in range(self._corpus_size) if i not in indices_to_remove]

            self._doc_contents = [self._doc_contents[i] for i in indices_to_keep]
            self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
            self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
            self._metadata = [self._metadata[i] for i in indices_to_keep]
            self._doc_freqs = [self._doc_freqs[i] for i in indices_to_keep]
            self._doc_lens = [self._doc_lens[i] for i in indices_to_keep]
            self._corpus_size = len(indices_to_keep)

            # 重建索引
            self._doc_index = {}
            self._kb_index = {}
            for i in range(self._corpus_size):
                did = self._doc_ids[i]
                if did:
                    self._doc_index.setdefault(did, []).append(i)
                kb_id = self._metadata[i].get("kb_id", "")
                if kb_id:
                    self._kb_index.setdefault(kb_id, []).append(i)

            self._calc_idf()
            self._calc_avg_dl()
            return removed

    def _calc_idf(self):
        idf = {}
        for doc_freq in self._doc_freqs:
            for word in doc_freq:
                idf[word] = idf.get(word, 0) + 1

        for word, freq in idf.items():
            idf[word] = math.log((self._corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)

        self._idf = idf

    def _calc_avg_dl(self):
        if self._corpus_size == 0:
            self._avg_dl = 0
            return
        self._avg_dl = sum(self._doc_lens) / self._corpus_size

    def search(self, query: str, top_k: int = 10, kb_id: str = "", kb_ids: Optional[List[str]] = None) -> List[Dict]:
        with self._lock:
            if self._corpus_size == 0:
                return []

            query_tokens = self._tokenize(query)
            if not query_tokens:
                return []

            scores = [0.0] * self._corpus_size

            for token in query_tokens:
                if token not in self._idf:
                    continue
                token_idf = self._idf[token]

                for idx in range(self._corpus_size):
                    doc_freq = self._doc_freqs[idx]
                    if token not in doc_freq:
                        continue

                    tf = doc_freq[token]
                    dl = self._doc_lens[idx]
                    numerator = tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (1 - self.b + self.b * dl / max(self._avg_dl, 1e-6))
                    scores[idx] += token_idf * numerator / denominator

            effective_kb_ids = set(kb_ids or [])
            if kb_id:
                effective_kb_ids.add(kb_id)
            if effective_kb_ids:
                kb_indices = set()
                for selected_kb in effective_kb_ids:
                    kb_indices.update(self._kb_index.get(selected_kb, []))
                for i in range(self._corpus_size):
                    if i not in kb_indices:
                        scores[i] = -float('inf')

            scored_docs = [(i, s) for i, s in enumerate(scores) if s > 0]
            scored_docs.sort(key=lambda x: x[1], reverse=True)

            results = []
            for idx, score in scored_docs[:top_k]:
                results.append({
                    "chunk_id": self._chunk_ids[idx],
                    "doc_id": self._doc_ids[idx],
                    "content": self._doc_contents[idx],
                    "score": float(score),
                    "metadata": self._metadata[idx],
                })

            return results

    def remove_by_doc_id(self, doc_id: str) -> int:
        with self._lock:
            indices = self._doc_index.get(doc_id, [])
            if not indices:
                return 0

            remove_set = set(indices)
            new_contents = []
            new_doc_ids = []
            new_chunk_ids = []
            new_metadata = []
            new_doc_freqs = []
            new_doc_lens = []
            new_kb_index: Dict[str, List[int]] = {}
            new_doc_index: Dict[str, List[int]] = {}

            for i in range(self._corpus_size):
                if i in remove_set:
                    continue
                new_idx = len(new_contents)
                new_contents.append(self._doc_contents[i])
                new_doc_ids.append(self._doc_ids[i])
                new_chunk_ids.append(self._chunk_ids[i])
                new_metadata.append(self._metadata[i])
                new_doc_freqs.append(self._doc_freqs[i])
                new_doc_lens.append(self._doc_lens[i])

                doc_id_i = self._doc_ids[i]
                if doc_id_i:
                    new_doc_index.setdefault(doc_id_i, []).append(new_idx)

                kb_id_i = self._metadata[i].get("kb_id", "")
                if kb_id_i:
                    new_kb_index.setdefault(kb_id_i, []).append(new_idx)

            self._doc_contents = new_contents
            self._doc_ids = new_doc_ids
            self._chunk_ids = new_chunk_ids
            self._metadata = new_metadata
            self._doc_freqs = new_doc_freqs
            self._doc_lens = new_doc_lens
            self._corpus_size = len(new_contents)
            self._kb_index = new_kb_index
            self._doc_index = new_doc_index

            self._calc_idf()
            self._calc_avg_dl()

            return len(indices)

    def delete_by_kb(self, kb_id: str) -> int:
        """删除指定知识库的所有记录"""
        with self._lock:
            indices_to_remove = set(self._kb_index.get(kb_id, []))
            if not indices_to_remove:
                return 0

            removed = len(indices_to_remove)
            indices_to_keep = [i for i in range(self._corpus_size) if i not in indices_to_remove]

            self._doc_contents = [self._doc_contents[i] for i in indices_to_keep]
            self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
            self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
            self._metadata = [self._metadata[i] for i in indices_to_keep]
            self._doc_freqs = [self._doc_freqs[i] for i in indices_to_keep]
            self._doc_lens = [self._doc_lens[i] for i in indices_to_keep]
            self._corpus_size = len(indices_to_keep)

            # 重建索引
            self._doc_index = {}
            self._kb_index = {}
            for i in range(self._corpus_size):
                did = self._doc_ids[i]
                if did:
                    self._doc_index.setdefault(did, []).append(i)
                kid = self._metadata[i].get("kb_id", "")
                if kid:
                    self._kb_index.setdefault(kid, []).append(i)

            self._calc_idf()
            self._calc_avg_dl()
            return removed

    def clear(self) -> int:
        """Clear the in-memory keyword index and return removed chunk count."""
        with self._lock:
            removed = self._corpus_size
            self._corpus_size = 0
            self._avg_dl = 0.0
            self._doc_freqs.clear()
            self._doc_lens.clear()
            self._idf.clear()
            self._doc_contents.clear()
            self._doc_ids.clear()
            self._chunk_ids.clear()
            self._metadata.clear()
            self._kb_index.clear()
            self._doc_index.clear()
            return removed

    def get_stats(self) -> Dict[str, Any]:
        return {
            "corpus_size": self._corpus_size,
            "avg_doc_length": self._avg_dl,
            "vocab_size": len(self._idf),
        }


class HybridSearchEngine:
    def __init__(self):
        self._bm25 = BM25(k1=1.5, b=0.75)
        self._vector_weight = settings.HYBRID_SEARCH_WEIGHT_VECTOR
        self._keyword_weight = settings.HYBRID_SEARCH_WEIGHT_KEYWORD

    def add_documents(self, chunks: List[Dict], recalculate: bool = True) -> None:
        self._bm25.add_documents(chunks, recalculate=recalculate)

    def finalize_batch(self) -> None:
        self._bm25.finalize_batch()

    def delete_by_doc(self, doc_id: str) -> int:
        """删除指定文档的所有记录"""
        return self._bm25.delete_by_doc(doc_id)

    def delete_by_kb(self, kb_id: str) -> int:
        """删除指定知识库的所有记录"""
        return self._bm25.delete_by_kb(kb_id)

    def clear(self) -> int:
        return self._bm25.clear()

    def search(self, query: str, top_k: int = 10, kb_id: str = "",
               kb_ids: Optional[List[str]] = None,
               vector_results: Optional[List[Dict]] = None) -> List[Dict]:
        bm25_results = self._bm25.search(query, top_k=top_k * 3, kb_id=kb_id, kb_ids=kb_ids)

        if vector_results is None:
            return bm25_results[:top_k]

        return self._merge_results(vector_results, bm25_results, top_k)

    def _merge_results(self, vector_results: List[Dict],
                       bm25_results: List[Dict], top_k: int) -> List[Dict]:
        # Reciprocal Rank Fusion is robust to incompatible score scales.  The
        # previous max-score normalization made one unusually high BM25 score
        # suppress otherwise relevant vector results.
        score_map: Dict[str, Dict[str, Any]] = {}
        rrf_k = 60.0
        for rank, result in enumerate(vector_results, 1):
            cid = str(result.get("chunk_id") or "")
            if not cid:
                continue
            item = score_map.setdefault(cid, {
                "chunk_id": cid, "doc_id": result.get("doc_id", ""),
                "content": result.get("content", ""),
                "metadata": result.get("metadata", {}),
                "_sources": set(), "score": 0.0,
            })
            item["score"] += self._vector_weight / (rrf_k + rank)
            item["_sources"].add("vector")
            item["vector_score"] = result.get("score", 0.0)

        for rank, result in enumerate(bm25_results, 1):
            cid = str(result.get("chunk_id") or "")
            if not cid:
                continue
            item = score_map.setdefault(cid, {
                "chunk_id": cid, "doc_id": result.get("doc_id", ""),
                "content": result.get("content", ""),
                "metadata": result.get("metadata", {}),
                "_sources": set(), "score": 0.0,
            })
            item["score"] += self._keyword_weight / (rrf_k + rank)
            item["_sources"].add("keyword")
            item["keyword_score"] = result.get("score", 0.0)

        results = sorted(score_map.values(), key=lambda x: x["score"], reverse=True)
        for result in results:
            result["retrieval_sources"] = sorted(result.pop("_sources", set()))
        return results[:top_k]

    def remove_by_doc_id(self, doc_id: str) -> int:
        return self._bm25.remove_by_doc_id(doc_id)

    def bm25_search(self, query: str, top_k: int = 10, kb_id: str = "", kb_ids: Optional[List[str]] = None) -> List[Dict]:
        return self._bm25.search(query, top_k=top_k, kb_id=kb_id, kb_ids=kb_ids)

    def get_stats(self) -> Dict[str, Any]:
        return self._bm25.get_stats()


hybrid_search_engine = HybridSearchEngine()
