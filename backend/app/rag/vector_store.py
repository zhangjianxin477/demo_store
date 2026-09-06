import os
import re
import uuid
import json
import logging
import hashlib
import threading
import time
from typing import List, Dict, Any, Optional, Set

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

_faiss_available = False
try:
    import faiss
    _faiss_available = True
    logger.info("FAISS 可用，向量搜索将使用 FAISS IDMap 加速")
except ImportError:
    logger.info("FAISS 不可用，向量搜索将使用 NumPy 矩阵运算")

_milvus_available = False
try:
    from pymilvus import MilvusClient, DataType
    _milvus_available = True
except ImportError:
    MilvusClient = None
    DataType = None


class VectorStore:
    def __init__(self):
        self._storage_file = os.path.join(settings.VECTOR_STORE_DIR, "vectors.npz")
        self._faiss_index_file = os.path.join(settings.VECTOR_STORE_DIR, "faiss.index")
        self._meta_file = os.path.join(settings.VECTOR_STORE_DIR, "metadata.json")
        self._texts: List[str] = []
        self._doc_ids: List[str] = []
        self._chunk_ids: List[str] = []
        self._metadata: List[Dict] = []
        self._lock = threading.RLock()
        self._use_cloud = settings.use_supabase
        self._use_milvus = settings.use_milvus and _milvus_available
        self._milvus_client = None
        self._milvus_ready = False
        self._milvus_error = ""
        self._embedding_matrix: Optional[np.ndarray] = None
        self._kb_index: Dict[str, List[int]] = {}
        self._doc_index: Dict[str, List[int]] = {}
        self._inverted_index: Dict[str, Set[int]] = {}
        self._text_word_sets: List[Set[str]] = []
        # O3: FAISS IDMap 索引，支持增量添加
        self._faiss_index = None
        self._faiss_id_map = None  # IDMap 包装，支持按 ID 增量添加
        self._faiss_dirty = True
        self._next_faiss_id = 0  # FAISS 内部递增 ID
        if self._use_milvus:
            self._init_milvus()
        self._load()

    def _init_milvus(self) -> None:
        """连接 Zilliz Cloud，并按当前 embedding 维度创建 collection。

        Zilliz Serverless Endpoint 可能解析到多个公网地址；单个地址的
        gRPC 握手失败不应让整个进程永久回退到 local，因此这里做有限重试。
        """
        last_error = ""
        for attempt in range(1, 4):
            if self._init_milvus_once():
                return
            last_error = self._milvus_error
            if attempt < 3:
                time.sleep(0.8 * attempt)
        if last_error:
            self._milvus_error = last_error

    def _init_milvus_once(self) -> bool:
        """Perform one Milvus connection attempt."""
        if not self._use_milvus:
            return False
        try:
            self._milvus_client = MilvusClient(
                uri=settings.MILVUS_URI,
                token=settings.MILVUS_TOKEN,
                timeout=15,
            )
            name = settings.MILVUS_COLLECTION
            if not self._milvus_client.has_collection(collection_name=name):
                schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
                schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
                schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
                schema.add_field("kb_id", DataType.VARCHAR, max_length=256)
                schema.add_field("content", DataType.VARCHAR, max_length=65535)
                schema.add_field("metadata", DataType.JSON)
                schema.add_field("vector", DataType.FLOAT_VECTOR, dim=settings.EMBEDDING_DIMENSION)
                index_params = self._milvus_client.prepare_index_params()
                index_params.add_index(
                    field_name="vector",
                    index_type="AUTOINDEX",
                    metric_type=settings.MILVUS_METRIC_TYPE,
                )
                self._milvus_client.create_collection(
                    collection_name=name, schema=schema, index_params=index_params
                )
            else:
                desc = self._milvus_client.describe_collection(collection_name=name)
                fields = {f.get("name"): f for f in desc.get("fields", [])}
                vector_field = fields.get("vector", {})
                dim = (vector_field.get("params") or {}).get("dim")
                if dim and int(dim) != settings.EMBEDDING_DIMENSION:
                    raise ValueError(f"Milvus collection 向量维度为 {dim}，当前 embedding 为 {settings.EMBEDDING_DIMENSION}")
            self._milvus_client.load_collection(collection_name=name)
            self._milvus_ready = True
            self._milvus_error = ""
            logger.info("Milvus 已连接: collection=%s, dim=%s", name, settings.EMBEDDING_DIMENSION)
            return True
        except Exception as exc:
            self._milvus_error = str(exc)
            self._milvus_ready = False
            self._milvus_client = None
            logger.warning("Milvus 连接失败，回退本地向量库: %s", exc)
            return False

    @staticmethod
    def _active_embedding_model() -> str:
        """Resolve the runtime model, including settings saved from the UI."""
        try:
            from app.rag.embedding_service import embedding_service
            return str(getattr(embedding_service, "model", "") or settings.EMBEDDING_MODEL).strip()
        except Exception:
            return str(getattr(settings, "EMBEDDING_MODEL", "") or "").strip()

    def _load_from_milvus(self) -> bool:
        if not self._milvus_ready or self._milvus_client is None:
            return False
        try:
            rows = self._milvus_client.query(
                collection_name=settings.MILVUS_COLLECTION,
                filter="",
                output_fields=["chunk_id", "doc_id", "kb_id", "content", "metadata"],
                limit=10000,
            )
            self._chunk_ids = [str(r.get("chunk_id", "")) for r in rows]
            self._doc_ids = [str(r.get("doc_id", "")) for r in rows]
            self._texts = [r.get("content", "") for r in rows]
            self._metadata = []
            for r in rows:
                m = r.get("metadata") or {}
                if isinstance(m, str):
                    try: m = json.loads(m)
                    except Exception: m = {}
                m = dict(m)
                if r.get("kb_id"): m["kb_id"] = r["kb_id"]
                self._metadata.append(m)
            self._embedding_matrix = None
            self._rebuild_indexes()
            logger.info("Milvus 元数据加载完成: %s 条记录", len(rows))
            return True
        except Exception as exc:
            self._milvus_error = str(exc)
            logger.warning("Milvus 元数据加载失败: %s", exc)
            return False

    def _get_client(self):
        from app.core.supabase_client import get_supabase_client
        return get_supabase_client()

    def _rebuild_matrix(self):
        if not self._texts:
            self._embedding_matrix = None
            self._faiss_index = None
            self._faiss_dirty = True
            return
        dim = settings.EMBEDDING_DIMENSION
        n = len(self._texts)
        matrix = np.zeros((n, dim), dtype=np.float32)
        if os.path.exists(self._storage_file):
            try:
                data = np.load(self._storage_file, allow_pickle=True)
                if "embeddings" in data:
                    stored = data["embeddings"].astype(np.float32)
                    if stored.ndim == 2 and stored.shape[1] == dim:
                        matrix[:min(n, stored.shape[0])] = stored[:n]
                else:
                    for i in range(min(n, len(data.files))):
                        key = f"e_{i}"
                        if key in data:
                            vec = data[key].astype(np.float32)
                            if vec.shape[0] == dim:
                                matrix[i] = vec
            except Exception as e:
                logger.warning(f"加载向量文件失败，使用零矩阵: {e}")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        self._embedding_matrix = matrix / norms
        self._faiss_dirty = True

    def _rebuild_indexes(self):
        self._kb_index.clear()
        self._doc_index.clear()
        self._inverted_index.clear()
        self._text_word_sets.clear()
        for i, meta in enumerate(self._metadata):
            kb_id = meta.get("kb_id", "")
            if kb_id:
                self._kb_index.setdefault(kb_id, []).append(i)
            doc_id = self._doc_ids[i] if i < len(self._doc_ids) else ""
            if doc_id:
                self._doc_index.setdefault(doc_id, []).append(i)
        for i, text in enumerate(self._texts):
            words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', text.lower()))
            self._text_word_sets.append(words)
            for w in words:
                if len(w) >= 2:
                    self._inverted_index.setdefault(w, set()).add(i)

    def index_model_status(self, current_model: str = "", current_dimension: Optional[int] = None) -> Dict[str, Any]:
        """Return whether persisted vectors share the active model *and* dimension.

        Model names alone are not sufficient: legacy/degraded records may carry
        the same model name while having been generated at a different vector
        dimension.  The settings UI and retrieval gate both use this result.
        """
        models = sorted({str((meta or {}).get("embedding_model") or "").strip()
                         for meta in self._metadata if isinstance(meta, dict)
                         and str((meta or {}).get("embedding_model") or "").strip()})
        current = str(current_model or self._active_embedding_model()).strip()
        dimensions = sorted({int((meta or {}).get("embedding_dimension"))
                             for meta in self._metadata
                             if isinstance(meta, dict)
                             and str((meta or {}).get("embedding_dimension") or "").strip().lstrip("-").isdigit()})
        active_dimension = int(current_dimension or settings.EMBEDDING_DIMENSION)
        model_consistent = len(models) <= 1 and (not models or models[0] == current)
        dimension_consistent = len(dimensions) <= 1 and (not dimensions or dimensions[0] == active_dimension)
        def _meta_dimension(meta: Dict[str, Any]) -> int:
            try:
                return int(meta.get("embedding_dimension") or -1)
            except (TypeError, ValueError):
                return -1

        return {
            "models": models,
            "dimensions": dimensions,
            "current_model": current,
            "current_dimension": active_dimension,
            "model_consistent": model_consistent,
            "dimension_consistent": dimension_consistent,
            "consistent": model_consistent and dimension_consistent,
            "current_model_count": sum(1 for meta in self._metadata
                                        if isinstance(meta, dict)
                                        and str(meta.get("embedding_model") or "").strip() == current),
            "current_dimension_count": sum(1 for meta in self._metadata
                                            if isinstance(meta, dict)
                                            and _meta_dimension(meta) == active_dimension),
            "total_count": len(self._metadata),
        }

    def _try_build_faiss(self):
        """构建 FAISS IDMap 索引，支持增量添加和按 ID 检索。

        使用 IndexIDMap 包装基础索引，使每个向量关联一个唯一 int64 ID，
        支持增量 add_with_id 而无需全量重建。
        """
        if not _faiss_available or self._embedding_matrix is None:
            return
        if not self._faiss_dirty:
            return
        try:
            n, dim = self._embedding_matrix.shape
            if n == 0:
                self._faiss_index = None
                self._faiss_id_map = None
                self._faiss_dirty = False
                return

            # 尝试从文件加载已序列化的索引
            if os.path.exists(self._faiss_index_file) and not self._faiss_dirty:
                try:
                    self._faiss_id_map = faiss.read_index(self._faiss_index_file)
                    self._faiss_index = self._faiss_id_map
                    self._next_faiss_id = self._faiss_id_map.ntotal
                    self._faiss_dirty = False
                    logger.info(f"从文件加载 FAISS IDMap 索引: {n} 条向量")
                    return
                except Exception as e:
                    logger.warning(f"加载 FAISS 索引文件失败，重新构建: {e}")

            # 构建基础索引
            if n < 10000:
                base_index = faiss.IndexFlatIP(dim)
            else:
                nlist = min(int(np.sqrt(n)), 256)
                quantizer = faiss.IndexFlatIP(dim)
                base_index = faiss.IndexIVFFlat(quantizer, dim, nlist)
                base_index.train(self._embedding_matrix)

            # 使用 IDMap 包装，支持按 ID 增量添加
            id_map = faiss.IndexIDMap(base_index)
            ids = np.arange(n, dtype=np.int64)
            id_map.add_with_ids(self._embedding_matrix, ids)

            self._faiss_id_map = id_map
            self._faiss_index = id_map
            self._next_faiss_id = n
            self._faiss_dirty = False
            logger.info(f"FAISS IDMap 索引构建完成: {n} 条向量, dim={dim}")
        except Exception as e:
            logger.warning(f"FAISS 索引构建失败，降级到 NumPy: {e}")
            self._faiss_index = None
            self._faiss_id_map = None
            self._faiss_dirty = False

    def _invalidate_faiss(self):
        self._faiss_dirty = True
        self._faiss_index = None

    def _load(self):
        if self._use_milvus and self._milvus_ready:
            if self._load_from_milvus():
                return
            self._use_milvus = False
        if self._use_cloud:
            self._load_from_cloud()
        else:
            self._load_from_local()

    def _load_from_local(self):
        if os.path.exists(self._meta_file):
            try:
                with open(self._meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self._texts = meta.get("texts", [])
                self._doc_ids = meta.get("doc_ids", [])
                self._chunk_ids = meta.get("chunk_ids", [])
                self._metadata = meta.get("metadata", [])
                self._rebuild_matrix()
                self._rebuild_indexes()
                logger.info(f"本地向量库加载完成: {len(self._texts)} 条记录")
            except Exception as e:
                logger.error(f"本地向量库加载失败: {e}")
                self._reset_local()

    def _load_from_cloud(self):
        try:
            client = self._get_client()
            if not client:
                logger.warning("Supabase 客户端不可用，回退到本地存储")
                self._use_cloud = False
                self._load_from_local()
                return

            resp = client.table("vector_chunks").select("chunk_id, doc_id, kb_id, content, metadata").execute()
            rows = resp.data or []
            self._chunk_ids = [r["chunk_id"] for r in rows]
            self._doc_ids = [r["doc_id"] for r in rows]
            self._texts = [r["content"] for r in rows]
            self._metadata = []
            for r in rows:
                m = r.get("metadata", {})
                if isinstance(m, str):
                    m = json.loads(m)
                if r.get("kb_id"):
                    m["kb_id"] = r["kb_id"]
                self._metadata.append(m)
            self._rebuild_matrix()
            self._rebuild_indexes()
            logger.info(f"云端向量库加载完成: {len(self._texts)} 条记录")
        except Exception as e:
            logger.error(f"云端向量库加载失败: {e}，回退到本地存储")
            self._use_cloud = False
            self._load_from_local()

    def _reset_local(self):
        self._texts = []
        self._doc_ids = []
        self._chunk_ids = []
        self._metadata = []
        self._embedding_matrix = None
        self._faiss_index = None
        self._faiss_dirty = True
        self._kb_index.clear()
        self._doc_index.clear()
        self._inverted_index.clear()
        self._text_word_sets.clear()

    def _save(self):
        if self._use_cloud:
            return
        self._save_local()

    def _save_local(self):
        """持久化向量数据到本地。

        O3 优化：使用 FAISS serialize/deserialize 替代 pickle 全量加载，
        减少内存占用和加载时间。
        """
        try:
            # Write complete temporary files first so a restart cannot leave a half-written index.
            meta_tmp = self._meta_file + ".tmp"
            with open(meta_tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "texts": self._texts,
                    "doc_ids": self._doc_ids,
                    "chunk_ids": self._chunk_ids,
                    "metadata": self._metadata,
                }, f, ensure_ascii=False)
            os.replace(meta_tmp, self._meta_file)

            if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
                vector_tmp = self._storage_file + ".tmp"
                with open(vector_tmp, "wb") as f:
                    np.savez(f, embeddings=self._embedding_matrix)
                os.replace(vector_tmp, self._storage_file)

            # Keep the FAISS index as an optional acceleration artifact.
            if _faiss_available and self._faiss_id_map is not None:
                try:
                    faiss_tmp = self._faiss_index_file + ".tmp"
                    faiss.write_index(self._faiss_id_map, faiss_tmp)
                    os.replace(faiss_tmp, self._faiss_index_file)
                    logger.info(f"FAISS 索引已序列化保存: {self._faiss_index_file}")
                except Exception as e:
                    logger.warning(f"FAISS 索引序列化失败: {e}")
        except Exception as e:
            logger.error(f"本地向量库保存失败: {e}")

    def _add_single_embedding(self, embedding: np.ndarray):
        """添加单个嵌入向量到矩阵和 FAISS IDMap 索引。

        O3 优化：如果 FAISS IDMap 已构建，直接增量添加新向量，
        避免全量重建索引。
        """
        emb = embedding.astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        if self._embedding_matrix is None:
            self._embedding_matrix = emb.reshape(1, -1)
        else:
            current_dim = self._embedding_matrix.shape[1]
            if emb.shape[0] != current_dim:
                raise ValueError(
                    f"向量维度不一致：现有索引={current_dim}, 新向量={emb.shape[0]}。"
                    "请在更换 Embedding 模型后重建索引或新建知识库。"
                )
            self._embedding_matrix = np.vstack([self._embedding_matrix, emb.reshape(1, -1)])

        # O3: 增量添加到 FAISS IDMap（无需全量重建）
        if _faiss_available and self._faiss_id_map is not None:
            try:
                new_id = self._next_faiss_id
                self._faiss_id_map.add_with_ids(emb.reshape(1, -1), np.array([new_id], dtype=np.int64))
                self._next_faiss_id += 1
            except Exception as e:
                logger.warning(f"FAISS 增量添加失败: {e}")
                self._invalidate_faiss()
        else:
            self._invalidate_faiss()

    def _add_single_text_index(self, idx: int, text: str, doc_id: str, metadata: Dict):
        if doc_id:
            self._doc_index.setdefault(doc_id, []).append(idx)
        kb_id = metadata.get("kb_id", "")
        if kb_id:
            self._kb_index.setdefault(kb_id, []).append(idx)
        words = set(re.findall(r'[\u4e00-\u9fff]+|\w+', text.lower()))
        self._text_word_sets.append(words)
        for w in words:
            if len(w) >= 2:
                self._inverted_index.setdefault(w, set()).add(idx)

    async def add_documents_async(self, chunks: List[Dict], persist: bool = True) -> None:
        from app.rag.embedding_service import embedding_service

        valid_chunks = []
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", str(uuid.uuid4()))
            doc_id = chunk.get("doc_id", "")
            content = chunk.get("content", "")
            if not content.strip():
                continue
            metadata = dict(chunk.get("metadata", {}) or {})
            metadata.setdefault("embedding_model", embedding_service.model)
            metadata.setdefault("embedding_dimension", embedding_service.dimension)
            metadata.setdefault("content_hash", hashlib.sha256(content.encode("utf-8")).hexdigest())
            valid_chunks.append({
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "content": content,
                "metadata": metadata,
                "embedding": chunk.get("embedding"),
            })

        texts_to_embed = []
        chunks_need_embedding = []
        chunks_with_embedding = []
        with self._lock:
            existing_chunk_ids = set(self._chunk_ids)
            for chunk in valid_chunks:
                if chunk["chunk_id"] in existing_chunk_ids:
                    logger.warning(f"跳过重复 chunk_id: {chunk['chunk_id']}")
                    continue
                if chunk["embedding"] is not None:
                    chunks_with_embedding.append((chunk, chunk["embedding"]))
                else:
                    chunks_need_embedding.append(chunk)
                    texts_to_embed.append(chunk["content"])

        if texts_to_embed:
            embeddings = await embedding_service.embed_texts(texts_to_embed)
            degraded = embedding_service.is_degraded
            for chunk in chunks_need_embedding:
                chunk["metadata"]["embedding_degraded"] = degraded
                chunk["metadata"]["embedding_provider"] = embedding_service.provider
                if degraded:
                    chunk["metadata"]["embedding_error"] = embedding_service.last_error
            chunks_with_embedding.extend(zip(chunks_need_embedding, embeddings))

        added_chunks = []
        with self._lock:
            existing_chunk_ids = set(self._chunk_ids)
            pending_chunks = []
            pending_embeddings = []
            for chunk, embedding in chunks_with_embedding:
                if chunk["chunk_id"] in existing_chunk_ids:
                    continue
                emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
                chunk["metadata"]["embedding_dimension"] = int(emb.shape[0])
                pending_chunks.append(chunk)
                pending_embeddings.append(emb)
                existing_chunk_ids.add(chunk["chunk_id"])

            if pending_embeddings:
                embedding_batch = np.vstack(pending_embeddings)
                norms = np.linalg.norm(embedding_batch, axis=1, keepdims=True)
                embedding_batch = embedding_batch / np.where(norms > 0, norms, 1.0)
                if self._embedding_matrix is None:
                    self._embedding_matrix = embedding_batch
                else:
                    current_dim = self._embedding_matrix.shape[1]
                    if embedding_batch.shape[1] != current_dim:
                        raise ValueError(
                            f"Embedding dimension mismatch: existing={current_dim}, new={embedding_batch.shape[1]}"
                        )
                    self._embedding_matrix = np.vstack((self._embedding_matrix, embedding_batch))

                if _faiss_available and self._faiss_id_map is not None:
                    try:
                        ids = np.arange(
                            self._next_faiss_id,
                            self._next_faiss_id + len(embedding_batch),
                            dtype=np.int64,
                        )
                        self._faiss_id_map.add_with_ids(embedding_batch, ids)
                        self._next_faiss_id += len(embedding_batch)
                    except Exception as e:
                        logger.warning(f"FAISS batch append failed: {e}")
                        self._invalidate_faiss()
                else:
                    self._invalidate_faiss()

                for chunk in pending_chunks:
                    self._texts.append(chunk["content"])
                    self._doc_ids.append(chunk["doc_id"])
                    self._chunk_ids.append(chunk["chunk_id"])
                    self._metadata.append(chunk["metadata"])
                    self._add_single_text_index(
                        len(self._texts) - 1,
                        chunk["content"],
                        chunk["doc_id"],
                        chunk["metadata"],
                    )
                added_chunks = pending_chunks

            if persist and added_chunks:
                if self._use_milvus and self._milvus_ready:
                    self._save_chunks_to_milvus(added_chunks, embedding_batch)
                elif self._use_cloud:
                    self._save_chunks_to_cloud(added_chunks)
                else:
                    self._save_local()
        logger.info(f"向量库添加 {len(added_chunks)} 条记录，总计 {len(self._texts)} 条")

    def persist(self) -> None:
        """Persist pending local vector changes once after a batch import."""
        with self._lock:
            if not self._use_cloud:
                self._save_local()

    def clear_all(self, *, recreate_milvus: bool = True) -> Dict[str, Any]:
        """Remove vector indexes only; source documents and KB metadata remain.

        When the Milvus schema was created with an old dimension, dropping and
        recreating the collection is required before 1024-dim vectors can be
        inserted.  This method is intentionally explicit and is only exposed
        through the authenticated/admin settings action.
        """
        with self._lock:
            local_count = len(self._texts)
            milvus_dropped = False
            warnings: List[str] = []
            if settings.use_milvus:
                client = self._milvus_client
                if client is None and _milvus_available:
                    try:
                        client = MilvusClient(uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN, timeout=15)
                    except Exception as exc:
                        warnings.append(f"Milvus 客户端初始化失败：{str(exc)[:240]}")
                if client is not None:
                    try:
                        if client.has_collection(collection_name=settings.MILVUS_COLLECTION):
                            if recreate_milvus:
                                client.drop_collection(collection_name=settings.MILVUS_COLLECTION)
                                milvus_dropped = True
                            else:
                                client.delete(collection_name=settings.MILVUS_COLLECTION, filter='chunk_id != ""')
                    except Exception as exc:
                        warnings.append(f"Milvus 清理失败：{str(exc)[:240]}")

            self._texts = []
            self._doc_ids = []
            self._chunk_ids = []
            self._metadata = []
            self._embedding_matrix = None
            self._kb_index.clear()
            self._doc_index.clear()
            self._inverted_index.clear()
            self._text_word_sets.clear()
            self._faiss_index = None
            self._faiss_id_map = None
            self._faiss_dirty = True
            for path in (self._storage_file, self._meta_file, self._faiss_index_file):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError as exc:
                    warnings.append(f"本地索引文件清理失败：{os.path.basename(path)}：{exc}")

            self._milvus_client = None
            self._milvus_ready = False
            self._milvus_error = ""
            self._use_milvus = bool(settings.use_milvus and _milvus_available)
            if self._use_milvus and milvus_dropped:
                self._init_milvus()
            return {
                "local_removed": local_count,
                "milvus_recreated": milvus_dropped and self._milvus_ready,
                "milvus_ready": self._milvus_ready,
                "warnings": warnings,
            }

    def add_documents(self, chunks: List[Dict]) -> None:
        with self._lock:
            existing_chunk_ids = set(self._chunk_ids)
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id", str(uuid.uuid4()))
                doc_id = chunk.get("doc_id", "")
                content = chunk.get("content", "")
                embedding = chunk.get("embedding")

                if not content.strip():
                    continue

                if chunk_id in existing_chunk_ids:
                    logger.warning(f"跳过重复 chunk_id: {chunk_id}")
                    continue

                emb = np.array(embedding, dtype=np.float32) if embedding else np.zeros(settings.EMBEDDING_DIMENSION, dtype=np.float32)
                self._texts.append(content)
                self._doc_ids.append(doc_id)
                self._chunk_ids.append(chunk_id)
                self._metadata.append(chunk.get("metadata", {}))
                existing_chunk_ids.add(chunk_id)
                self._add_single_embedding(emb)
                self._add_single_text_index(len(self._texts) - 1, content, doc_id, chunk.get("metadata", {}))

            if self._use_milvus and self._milvus_ready:
                self._save_chunks_to_milvus(chunks, self._embedding_matrix[-len(chunks):] if self._embedding_matrix is not None else None)
                self._save_local()
            elif self._use_cloud:
                self._save_chunks_to_cloud(chunks)
            else:
                self._save_local()
        logger.info(f"向量库添加 {len(chunks)} 条记录，总计 {len(self._texts)} 条")

    def _save_chunks_to_cloud(self, chunks: List[Dict]):
        client = self._get_client()
        if not client:
            logger.warning("Supabase 不可用，保存到本地")
            self._save_local()

    def _save_chunks_to_milvus(self, chunks: List[Dict], embeddings: Optional[np.ndarray]) -> None:
        if not self._milvus_ready or self._milvus_client is None or embeddings is None:
            return
        rows = []
        for chunk, emb in zip(chunks, embeddings):
            meta = dict(chunk.get("metadata") or {})
            rows.append({
                "chunk_id": str(chunk.get("chunk_id", str(uuid.uuid4())))[:128],
                "doc_id": str(chunk.get("doc_id", ""))[:256],
                "kb_id": str(meta.get("kb_id", ""))[:256],
                "content": str(chunk.get("content", ""))[:65535],
                "metadata": meta,
                "vector": np.asarray(emb, dtype=np.float32).tolist(),
            })
        if rows:
            # 主键为 chunk_id，upsert 允许重试写入而不会产生重复主键错误。
            self._milvus_client.upsert(collection_name=settings.MILVUS_COLLECTION, data=rows)
            return

        rows = []
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id", str(uuid.uuid4()))
            doc_id = chunk.get("doc_id", "")
            content = chunk.get("content", "")
            metadata = chunk.get("metadata", {})
            kb_id = metadata.get("kb_id", "")

            row = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "kb_id": kb_id,
                "content": content,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            }
            rows.append(row)

        try:
            for row in rows:
                try:
                    client.table("vector_chunks").upsert(row, on_conflict="chunk_id").execute()
                except Exception:
                    row_copy = dict(row)
                    del row_copy["metadata"]
                    row_copy["metadata"] = json.dumps({}, ensure_ascii=False)
                    try:
                        client.table("vector_chunks").upsert(row_copy, on_conflict="chunk_id").execute()
                    except Exception as e2:
                        logger.error(f"云端向量写入失败: {row['chunk_id']}, {e2}")
            logger.info(f"云端向量写入 {len(rows)} 条")
        except Exception as e:
            logger.error(f"云端向量批量写入失败: {e}")
            self._save_local()

    def search(self, query_embedding: np.ndarray, top_k: int = 5, threshold: float = 0.0,
               kb_id: str = "", kb_ids: Optional[List[str]] = None) -> List[Dict]:
        with self._lock:
            if self._use_milvus and self._milvus_ready:
                return self._search_milvus(query_embedding, top_k, threshold, kb_id, kb_ids)
            if self._embedding_matrix is None or len(self._texts) == 0:
                return []

            if self._use_cloud:
                return self._search_cloud(query_embedding, top_k, threshold, kb_id)

            return self._search_local(query_embedding, top_k, threshold, kb_id, kb_ids)

    def _search_milvus(self, query_embedding: np.ndarray, top_k: int, threshold: float, kb_id: str, kb_ids: Optional[List[str]] = None) -> List[Dict]:
        try:
            query = np.asarray(query_embedding, dtype=np.float32).ravel()
            if query.shape[0] != settings.EMBEDDING_DIMENSION:
                logger.error("Milvus 查询向量维度错误: %s", query.shape[0])
                return []
            norm = np.linalg.norm(query)
            if norm: query = query / norm
            effective_ids = list(dict.fromkeys([x for x in (kb_ids or []) if x]))
            if kb_id and kb_id not in effective_ids:
                effective_ids.insert(0, kb_id)
            if len(effective_ids) == 1:
                safe_id = effective_ids[0].replace('"', '\\"')
                expr = f'kb_id == "{safe_id}"'
            elif effective_ids:
                quoted = ', '.join('"' + x.replace('"', '\\"') + '"' for x in effective_ids)
                expr = f'kb_id in [{quoted}]'
            else:
                expr = ""
            hits = self._milvus_client.search(
                collection_name=settings.MILVUS_COLLECTION,
                # Over-fetch because mixed/legacy collections may contain
                # vectors from a different embedding model.  Those vectors
                # must never be compared with the current query vector.
                data=[query.tolist()], anns_field="vector", limit=min(max(top_k * 4, top_k), 200),
                filter=expr, output_fields=["chunk_id", "doc_id", "kb_id", "content", "metadata"],
                search_params={"metric_type": settings.MILVUS_METRIC_TYPE},
            )
            results = []
            for hit in (hits[0] if hits else []):
                score = float(hit.get("distance", hit.get("score", 0.0)))
                if score < threshold: continue
                entity = hit.get("entity", hit)
                meta = entity.get("metadata") or {}
                if isinstance(meta, str):
                    try: meta = json.loads(meta)
                    except Exception: meta = {}
                indexed_model = str((meta or {}).get("embedding_model") or "").strip()
                current_model = self._active_embedding_model()
                if indexed_model and current_model and indexed_model != current_model:
                    continue
                results.append({"chunk_id": entity.get("chunk_id", ""), "doc_id": entity.get("doc_id", ""), "content": entity.get("content", ""), "score": score, "metadata": meta})
                if len(results) >= top_k:
                    break
            return results
        except Exception as exc:
            self._milvus_error = str(exc)
            logger.warning("Milvus 搜索失败，回退本地: %s", exc)
            self._milvus_ready = False
            self._use_milvus = False
            self._load_from_local()
            return self._search_local(query_embedding, top_k, threshold, kb_id)

    def _search_local(self, query_embedding: np.ndarray, top_k: int, threshold: float, kb_id: str, kb_ids: Optional[List[str]] = None) -> List[Dict]:
        if self._embedding_matrix is None or len(self._embedding_matrix) == 0:
            return []

        query = np.array(query_embedding, dtype=np.float32).ravel()
        matrix_dim = self._embedding_matrix.shape[1]
        if query.shape[0] != matrix_dim:
            logger.error(
                f"查询向量维度不一致：现有索引={matrix_dim}, 查询向量={query.shape[0]}。"
                "请在更换 Embedding 模型后重建索引。"
            )
            return []
        dimension_mask = np.array([
            int((meta or {}).get("embedding_dimension") or -1) == query.shape[0]
            for meta in self._metadata
        ], dtype=bool)
        semantic_mask = np.array([
            not bool((meta or {}).get("embedding_degraded", False))
            for meta in self._metadata
        ], dtype=bool)
        model_names = {
            str((meta or {}).get("embedding_model") or "").strip()
            for meta in self._metadata
            if isinstance(meta, dict) and str((meta or {}).get("embedding_model") or "").strip()
        }
        current_model = self._active_embedding_model()
        # If the index contains multiple embedding spaces, only vectors made
        # by the active model are semantically comparable.  The hybrid path
        # can still use BM25 for old documents until they are re-indexed.
        if model_names and current_model and model_names != {current_model}:
            model_mask = np.array([
                str((meta or {}).get("embedding_model") or "").strip() == current_model
                for meta in self._metadata
            ], dtype=bool)
        else:
            model_mask = np.ones(len(self._metadata), dtype=bool)
        searchable_mask = dimension_mask & semantic_mask & model_mask
        if not np.any(searchable_mask):
            logger.warning(
                f"没有找到可用于语义检索的 {query.shape[0]} 维向量。"
                "Embedding 服务恢复后请重建索引。"
            )
            return []
        query_norm = np.linalg.norm(query)
        if query_norm > 0:
            query = query / query_norm

        # 合并 kb_id 和 kb_ids 为统一过滤
        effective_kb_ids = set(kb_ids or [])
        if kb_id:
            effective_kb_ids.add(kb_id)

        if not effective_kb_ids and np.all(searchable_mask) and _faiss_available and not self._faiss_dirty:
            self._try_build_faiss()
            if self._faiss_index is not None:
                return self._search_faiss(query, top_k, threshold)

        similarities = self._embedding_matrix @ query
        similarities = np.where(searchable_mask, similarities, -np.inf)

        if effective_kb_ids:
            kb_indices = set()
            for kid in effective_kb_ids:
                kb_indices.update(self._kb_index.get(kid, []))
            if not kb_indices:
                return []
            kb_set = np.zeros(len(self._texts), dtype=bool)
            kb_set[list(kb_indices)] = True
            similarities = np.where(kb_set, similarities, -np.inf)

        mask = similarities >= threshold
        valid_indices = np.where(mask)[0]
        valid_scores = similarities[mask]

        if len(valid_scores) == 0:
            return []

        if len(valid_scores) > top_k:
            top_positions = np.argpartition(valid_scores, -top_k)[-top_k:]
            top_positions = top_positions[np.argsort(valid_scores[top_positions])[::-1]]
        else:
            top_positions = np.argsort(valid_scores)[::-1]

        results = []
        for pos in top_positions:
            idx = int(valid_indices[pos])
            results.append({
                "chunk_id": self._chunk_ids[idx],
                "doc_id": self._doc_ids[idx],
                "content": self._texts[idx],
                "score": float(valid_scores[pos]),
                "metadata": self._metadata[idx],
            })
        return results

    def _search_faiss(self, query: np.ndarray, top_k: int, threshold: float) -> List[Dict]:
        try:
            query_vec = query.reshape(1, -1).astype(np.float32)
            search_k = min(top_k * 3, len(self._texts))
            scores, indices = self._faiss_index.search(query_vec, search_k)
            results = []
            for i in range(len(indices[0])):
                idx = int(indices[0][i])
                score = float(scores[0][i])
                if idx < 0 or score < threshold:
                    continue
                if idx >= len(self._texts):
                    continue
                results.append({
                    "chunk_id": self._chunk_ids[idx],
                    "doc_id": self._doc_ids[idx],
                    "content": self._texts[idx],
                    "score": score,
                    "metadata": self._metadata[idx],
                })
                if len(results) >= top_k:
                    break
            return results
        except Exception as e:
            logger.warning(f"FAISS 搜索失败，降级到 NumPy: {e}")
            self._faiss_index = None
            return self._search_local_numpy(query, top_k, threshold, "")

    def _search_local_numpy(self, query: np.ndarray, top_k: int, threshold: float, kb_id: str) -> List[Dict]:
        if self._embedding_matrix is None or len(self._embedding_matrix) == 0:
            return []

        similarities = self._embedding_matrix @ query

        if kb_id:
            kb_indices = self._kb_index.get(kb_id, [])
            if not kb_indices:
                return []
            kb_set = np.zeros(len(self._texts), dtype=bool)
            kb_set[kb_indices] = True
            similarities = np.where(kb_set, similarities, -np.inf)

        mask = similarities >= threshold
        valid_indices = np.where(mask)[0]
        valid_scores = similarities[mask]

        if len(valid_scores) == 0:
            return []

        if len(valid_scores) > top_k:
            top_positions = np.argpartition(valid_scores, -top_k)[-top_k:]
            top_positions = top_positions[np.argsort(valid_scores[top_positions])[::-1]]
        else:
            top_positions = np.argsort(valid_scores)[::-1]

        results = []
        for pos in top_positions:
            idx = int(valid_indices[pos])
            results.append({
                "chunk_id": self._chunk_ids[idx],
                "doc_id": self._doc_ids[idx],
                "content": self._texts[idx],
                "score": float(valid_scores[pos]),
                "metadata": self._metadata[idx],
            })
        return results

    def _search_cloud(self, query_embedding: np.ndarray, top_k: int, threshold: float, kb_id: str) -> List[Dict]:
        client = self._get_client()
        if not client:
            return self._search_local(query_embedding, top_k, threshold, kb_id)

        try:
            emb_list = query_embedding.tolist() if isinstance(query_embedding, np.ndarray) else list(query_embedding)
            emb_str = "[" + ",".join(str(x) for x in emb_list) + "]"

            rpc_params = {
                "query_embedding": emb_str,
                "match_threshold": threshold,
                "match_count": top_k,
            }
            if kb_id:
                rpc_params["filter_kb_id"] = kb_id

            resp = client.rpc("match_vectors", rpc_params).execute()
            rows = resp.data or []

            results = []
            for r in rows:
                m = r.get("metadata", {})
                if isinstance(m, str):
                    m = json.loads(m)
                if m.get("embedding_degraded", False):
                    continue
                results.append({
                    "chunk_id": r.get("chunk_id", ""),
                    "doc_id": r.get("doc_id", ""),
                    "content": r.get("content", ""),
                    "score": r.get("similarity", 0.0),
                    "metadata": m,
                })
            return results
        except Exception as e:
            logger.error(f"云端向量搜索失败: {e}，回退到本地搜索")
            return self._search_local(query_embedding, top_k, threshold, kb_id)

    def keyword_search(self, query: str, top_k: int = 5, kb_id: str = "") -> List[Dict]:
        with self._lock:
            if not self._texts:
                return []

            if self._use_cloud:
                return self._keyword_search_cloud(query, top_k, kb_id)

            return self._keyword_search_local(query, top_k, kb_id)

    def _keyword_search_local(self, query: str, top_k: int, kb_id: str) -> List[Dict]:
        """O5 优化：优先使用倒排索引模块进行 BM25 搜索，回退到原有逻辑。"""
        try:
            from app.rag.inverted_index import inverted_index
            if inverted_index._total_docs > 0:
                results = inverted_index.search(
                    query, top_k=top_k, kb_id=kb_id, kb_index=self._kb_index
                )
                if results:
                    return [
                        {
                            "chunk_id": self._chunk_ids[r["doc_id"]],
                            "doc_id": self._doc_ids[r["doc_id"]],
                            "content": self._texts[r["doc_id"]],
                            "score": r["score"],
                            "metadata": self._metadata[r["doc_id"]],
                        }
                        for r in results
                        if r["doc_id"] < len(self._texts)
                    ]
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"倒排索引搜索失败，回退到原有逻辑: {e}")

        # 原有回退逻辑
        query_lower = query.lower()
        query_words = re.findall(r'[\u4e00-\u9fff]+|\w+', query_lower)
        query_word_set = set(query_words)
        if not query_word_set:
            return []

        candidate_indices: Set[int] = set()
        for w in query_words:
            if len(w) >= 2 and w in self._inverted_index:
                candidate_indices.update(self._inverted_index[w])

        if not candidate_indices:
            for w in query_words:
                if len(w) >= 2:
                    for idx_word, idx_set in self._inverted_index.items():
                        if w in idx_word or idx_word in w:
                            candidate_indices.update(idx_set)
                            if len(candidate_indices) > 500:
                                break
                    if len(candidate_indices) > 500:
                        break

        if not candidate_indices:
            return []

        if kb_id:
            kb_indices = set(self._kb_index.get(kb_id, []))
            candidate_indices = candidate_indices & kb_indices
            if not candidate_indices:
                return []

        query_bigrams = set()
        for w in query_words:
            if len(w) >= 2:
                for j in range(len(w) - 1):
                    query_bigrams.add(w[j:j + 2])

        scored = []
        for i in candidate_indices:
            if i >= len(self._text_word_sets):
                continue
            text_words = self._text_word_sets[i]
            overlap = len(query_word_set & text_words)
            if overlap == 0 and not query_bigrams:
                continue

            word_score = overlap / max(len(query_word_set), 1)

            bigram_score = 0.0
            if query_bigrams:
                text_bigrams = set()
                for tw in text_words:
                    if len(tw) >= 2:
                        for j in range(len(tw) - 1):
                            text_bigrams.add(tw[j:j + 2])
                bigram_overlap = len(query_bigrams & text_bigrams)
                bigram_score = bigram_overlap / max(len(query_bigrams), 1)

            exact_match_bonus = 0.0
            text_lower = self._texts[i].lower()
            for qw in query_words:
                if len(qw) >= 2 and qw in text_lower:
                    exact_match_bonus += 0.1
            exact_match_bonus = min(exact_match_bonus, 0.3)

            position_bonus = 0.0
            for qw in query_words:
                if len(qw) >= 2:
                    pos = text_lower.find(qw)
                    if pos >= 0:
                        position_bonus += max(0, 0.1 - pos / max(len(text_lower), 1) * 0.1)
            position_bonus = min(position_bonus, 0.2)

            total_score = word_score * 0.5 + bigram_score * 0.3 + exact_match_bonus + position_bonus
            if total_score > 0:
                scored.append((i, total_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in scored[:top_k]:
            results.append({
                "chunk_id": self._chunk_ids[idx],
                "doc_id": self._doc_ids[idx],
                "content": self._texts[idx],
                "score": score,
                "metadata": self._metadata[idx],
            })
        return results

    def _keyword_search_cloud(self, query: str, top_k: int, kb_id: str) -> List[Dict]:
        client = self._get_client()
        if not client:
            return self._keyword_search_local(query, top_k, kb_id)

        try:
            q = client.table("vector_chunks").select("chunk_id, doc_id, kb_id, content, metadata")
            if kb_id:
                q = q.eq("kb_id", kb_id)
            q = q.text_search("content", query)
            resp = q.limit(top_k).execute()
            rows = resp.data or []

            results = []
            for r in rows:
                m = r.get("metadata", {})
                if isinstance(m, str):
                    m = json.loads(m)
                results.append({
                    "chunk_id": r.get("chunk_id", ""),
                    "doc_id": r.get("doc_id", ""),
                    "content": r.get("content", ""),
                    "score": 0.5,
                    "metadata": m,
                })
            return results
        except Exception as e:
            logger.error(f"云端关键词搜索失败: {e}，回退到本地搜索")
            return self._keyword_search_local(query, top_k, kb_id)

    def delete_by_kb(self, kb_id: str) -> int:
        with self._lock:
            if not kb_id:
                return 0

            if self._use_milvus and self._milvus_ready:
                return self._delete_by_kb_milvus(kb_id)
            if self._use_cloud:
                return self._delete_by_kb_cloud(kb_id)

            return self._delete_by_kb_local(kb_id)

    def _delete_by_kb_local(self, kb_id: str) -> int:
        indices_to_remove = set(self._kb_index.get(kb_id, []))
        if not indices_to_remove:
            return 0

        removed = len(indices_to_remove)
        indices_to_keep = [i for i in range(len(self._texts)) if i not in indices_to_remove]

        self._texts = [self._texts[i] for i in indices_to_keep]
        self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
        self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
        self._metadata = [self._metadata[i] for i in indices_to_keep]

        if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
            self._embedding_matrix = self._embedding_matrix[indices_to_keep]

        self._invalidate_faiss()
        self._rebuild_indexes()
        self._save_local()
        logger.info(f"本地向量库删除知识库 {kb_id} 的 {removed} 条记录")
        return removed

    def _delete_by_kb_cloud(self, kb_id: str) -> int:
        client = self._get_client()
        if not client:
            return self._delete_by_kb_local(kb_id)

    def _delete_by_kb_milvus(self, kb_id: str) -> int:
        try:
            removed = len(self._kb_index.get(kb_id, []))
            safe_kb_id = kb_id.replace('"', '\\"')
            self._milvus_client.delete(
                collection_name=settings.MILVUS_COLLECTION,
                filter=f'kb_id == "{safe_kb_id}"',
            )
            # 同时更新本地镜像，保证 BM25、页面分块和回退行为一致。
            return self._delete_by_kb_local(kb_id)
        except Exception as exc:
            self._milvus_error = str(exc)
            logger.warning("Milvus 按知识库删除失败: %s", exc)
            return 0

        try:
            resp = client.table("vector_chunks").delete().eq("kb_id", kb_id).execute()
            removed = len(resp.data) if resp.data else 0

            indices_to_remove = set(self._kb_index.get(kb_id, []))
            indices_to_keep = [i for i in range(len(self._texts)) if i not in indices_to_remove]
            self._texts = [self._texts[i] for i in indices_to_keep]
            self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
            self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
            self._metadata = [self._metadata[i] for i in indices_to_keep]

            if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
                self._embedding_matrix = self._embedding_matrix[indices_to_keep]

            self._invalidate_faiss()
            self._rebuild_indexes()

            logger.info(f"云端向量库删除知识库 {kb_id} 的 {removed} 条记录")
            return removed
        except Exception as e:
            logger.error(f"云端向量删除失败: {e}，回退到本地删除")
            return self._delete_by_kb_local(kb_id)

    def delete_by_doc(self, doc_id: str) -> int:
        with self._lock:
            if not doc_id:
                return 0

            if self._use_milvus and self._milvus_ready:
                return self._delete_by_doc_milvus(doc_id)
            if self._use_cloud:
                return self._delete_by_doc_cloud(doc_id)

            return self._delete_by_doc_local(doc_id)

    def _delete_by_doc_local(self, doc_id: str) -> int:
        indices_to_remove = set(self._doc_index.get(doc_id, []))
        if not indices_to_remove:
            return 0

        removed = len(indices_to_remove)
        indices_to_keep = [i for i in range(len(self._texts)) if i not in indices_to_remove]

        self._texts = [self._texts[i] for i in indices_to_keep]
        self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
        self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
        self._metadata = [self._metadata[i] for i in indices_to_keep]

        if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
            self._embedding_matrix = self._embedding_matrix[indices_to_keep]

        self._invalidate_faiss()
        self._rebuild_indexes()
        self._save_local()
        logger.info(f"本地向量库删除文档 {doc_id} 的 {removed} 条记录")
        return removed

    def _delete_by_doc_cloud(self, doc_id: str) -> int:
        client = self._get_client()
        if not client:
            return self._delete_by_doc_local(doc_id)

    def _delete_by_doc_milvus(self, doc_id: str) -> int:
        try:
            safe_doc_id = doc_id.replace('"', '\\"')
            self._milvus_client.delete(
                collection_name=settings.MILVUS_COLLECTION,
                filter=f'doc_id == "{safe_doc_id}"',
            )
            return self._delete_by_doc_local(doc_id)
        except Exception as exc:
            self._milvus_error = str(exc)
            logger.warning("Milvus 按文档删除失败: %s", exc)
            return 0

    @property
    def backend_status(self) -> Dict[str, Any]:
        # ``_use_milvus`` reflects configuration, while ``_milvus_ready``
        # reflects the actually reachable backend.  Report the active
        # backend so the health page does not claim Milvus is serving queries
        # when the code has already fallen back to the local store.
        milvus_configured = bool(settings.use_milvus)
        active_backend = "milvus" if milvus_configured and self._milvus_ready else (
            "supabase" if self._use_cloud else "local"
        )
        error = self._milvus_error if milvus_configured else ""
        if milvus_configured and not self._milvus_ready and not error:
            error = "Milvus 已配置但当前连接不可用；系统正在使用本地检索。请检查公网访问、Endpoint、Token 和防火墙。"
        return {
            "backend": active_backend,
            # If Milvus is configured but unreachable, readiness must remain
            # false even after the query path falls back to local storage.
            "configured": milvus_configured,
            "ready": self._milvus_ready if milvus_configured else True,
            "collection": settings.MILVUS_COLLECTION if milvus_configured else "",
            "error": error,
        }

        try:
            resp = client.table("vector_chunks").delete().eq("doc_id", doc_id).execute()
            removed = len(resp.data) if resp.data else 0

            indices_to_remove = set(self._doc_index.get(doc_id, []))
            indices_to_keep = [i for i in range(len(self._texts)) if i not in indices_to_remove]
            self._texts = [self._texts[i] for i in indices_to_keep]
            self._doc_ids = [self._doc_ids[i] for i in indices_to_keep]
            self._chunk_ids = [self._chunk_ids[i] for i in indices_to_keep]
            self._metadata = [self._metadata[i] for i in indices_to_keep]

            if self._embedding_matrix is not None and len(self._embedding_matrix) > 0:
                self._embedding_matrix = self._embedding_matrix[indices_to_keep]

            self._invalidate_faiss()
            self._rebuild_indexes()

            logger.info(f"云端向量库删除文档 {doc_id} 的 {removed} 条记录")
            return removed
        except Exception as e:
            logger.error(f"云端向量删除失败: {e}，回退到本地删除")
            return self._delete_by_doc_local(doc_id)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._texts)

    def get_chunks_by_kb(self, kb_id: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
        """获取指定知识库的分块列表"""
        with self._lock:
            indices = self._kb_index.get(kb_id, [])
            total = len(indices)
            start = (page - 1) * page_size
            end = start + page_size
            page_indices = indices[start:end]

            chunks = []
            for idx in page_indices:
                meta = self._metadata[idx] if idx < len(self._metadata) else {}
                content = self._texts[idx] if idx < len(self._texts) else ""
                normalized = self._normalize_metadata(meta, self._doc_ids[idx] if idx < len(self._doc_ids) else "", idx, content)
                # 补充 parent_content（不在 _normalize_metadata 中因为太大）
                if meta.get("parent_content"):
                    normalized["parent_content"] = meta["parent_content"]
                chunks.append({
                    "chunk_id": self._chunk_ids[idx] if idx < len(self._chunk_ids) else "",
                    "content": content,
                    "doc_id": self._doc_ids[idx] if idx < len(self._doc_ids) else "",
                    "metadata": normalized,
                })
            return {"total": total, "page": page, "page_size": page_size, "chunks": chunks}

    def get_chunks_by_doc(self, doc_id: str) -> List[Dict]:
        """获取指定文档的分块列表"""
        with self._lock:
            indices = self._doc_index.get(doc_id, [])
            chunks = []
            for i, idx in enumerate(indices):
                meta = self._metadata[idx] if idx < len(self._metadata) else {}
                content = self._texts[idx] if idx < len(self._texts) else ""
                normalized = self._normalize_metadata(meta, self._doc_ids[idx] if idx < len(self._doc_ids) else "", i, content)
                if meta.get("parent_content"):
                    normalized["parent_content"] = meta["parent_content"]
                chunks.append({
                    "chunk_id": self._chunk_ids[idx] if idx < len(self._chunk_ids) else "",
                    "content": content,
                    "doc_id": self._doc_ids[idx] if idx < len(self._doc_ids) else "",
                    "metadata": normalized,
                })
            return chunks

    def _normalize_metadata(self, meta: Dict, doc_id: str, chunk_index: int, content: str = "") -> Dict:
        """规范化metadata字段，确保包含所有必需字段"""
        token_count = 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            token_count = len(enc.encode(content))
        except Exception:
            token_count = len(content) // 2

        return {
            "document_id": doc_id,
            "document_title": meta.get("title", ""),
            "source_type": meta.get("source_format", meta.get("chunk_strategy", "")),
            "page_number": meta.get("page_number", meta.get("page", "")),
            "chunk_index": chunk_index,
            "heading_path": meta.get("heading_path", meta.get("heading", "")),
            "token_count": token_count,
            "content_length": len(content),
            "overlap": meta.get("overlap", 0),
            "embedding_model": meta.get("embedding_model", "bge-large-zh-v1.5"),
            "kb_id": meta.get("kb_id", ""),
            "chunk_strategy": meta.get("chunk_strategy", ""),
            "block_type": meta.get("block_type", ""),
            # 父子分块关键字段
            "parent_chunk_id": meta.get("parent_chunk_id", ""),
            "is_child": meta.get("is_child", False),
            "parent_chunk_size": meta.get("parent_chunk_size", 0),
            "parent_chunk_overlap": meta.get("parent_chunk_overlap", 0),
            "child_chunk_size": meta.get("child_chunk_size", 0),
            "child_chunk_overlap": meta.get("child_chunk_overlap", 0),
        }


class LazyVectorStore:
    """Delay expensive vector index loading until RAG data is actually used."""

    def __init__(self):
        object.__setattr__(self, "_instance", None)
        object.__setattr__(self, "_instance_lock", threading.RLock())

    def _get_instance(self) -> VectorStore:
        instance = object.__getattribute__(self, "_instance")
        if instance is not None:
            return instance
        lock = object.__getattribute__(self, "_instance_lock")
        with lock:
            instance = object.__getattribute__(self, "_instance")
            if instance is None:
                logger.info("Lazy loading vector store")
                instance = VectorStore()
                object.__setattr__(self, "_instance", instance)
        return instance

    @property
    def is_loaded(self) -> bool:
        return object.__getattribute__(self, "_instance") is not None

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

    def __setattr__(self, name, value):
        if name in {"_instance", "_instance_lock"}:
            object.__setattr__(self, name, value)
            return
        setattr(self._get_instance(), name, value)


vector_store = LazyVectorStore()
