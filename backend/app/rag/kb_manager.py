import os
import json
import uuid
import logging
import shutil
import hashlib
import threading
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

CORE_KB_ID = "core"


class KnowledgeBase:
    def __init__(self, kb_id: str, name: str, description: str = "", chunk_strategy: str = "auto",
                 chunk_size: int = 0, chunk_overlap: int = 0):
        self.kb_id = kb_id
        self.name = name
        self.description = description
        self.chunk_strategy = chunk_strategy
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.doc_count = 0
        self.chunk_count = 0
        self.created_at = ""
        self.updated_at = ""

    def to_dict(self) -> Dict:
        return {
            "kb_id": self.kb_id,
            "name": self.name,
            "description": self.description,
            "chunk_strategy": self.chunk_strategy,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "doc_count": self.doc_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_core": self.kb_id == CORE_KB_ID,
        }


class KnowledgeBaseManager:
    def __init__(self):
        self._kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases")
        self._meta_file = os.path.join(self._kb_dir, "registry.json")
        self._bases: Dict[str, KnowledgeBase] = {}
        self._lock = threading.RLock()
        self._use_cloud = settings.use_supabase
        os.makedirs(self._kb_dir, exist_ok=True)
        self._load()

    def _get_client(self):
        from app.core.supabase_client import get_supabase_client
        return get_supabase_client()

    def _load(self):
        if self._use_cloud:
            self._load_from_cloud()
        else:
            self._load_from_local()
        self.ensure_core()

    def _load_from_local(self):
        if os.path.exists(self._meta_file):
            try:
                with open(self._meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for kb_data in data.get("bases", []):
                    kb = KnowledgeBase(
                        kb_id=kb_data["kb_id"],
                        name=kb_data["name"],
                        description=kb_data.get("description", ""),
                        chunk_strategy=kb_data.get("chunk_strategy", "auto"),
                        chunk_size=kb_data.get("chunk_size", 0),
                        chunk_overlap=kb_data.get("chunk_overlap", 0),
                    )
                    kb.doc_count = kb_data.get("doc_count", 0)
                    kb.chunk_count = kb_data.get("chunk_count", 0)
                    kb.created_at = kb_data.get("created_at", "")
                    kb.updated_at = kb_data.get("updated_at", "")
                    self._bases[kb.kb_id] = kb
                logger.info(f"本地知识库注册表加载完成: {len(self._bases)} 个知识库")
            except Exception as e:
                logger.error(f"本地知识库注册表加载失败: {e}")

    def _load_from_cloud(self):
        client = self._get_client()
        if not client:
            logger.warning("Supabase 客户端不可用，回退到本地存储")
            self._use_cloud = False
            self._load_from_local()
            return

        try:
            resp = client.table("kb_registry").select("*").execute()
            rows = resp.data or []
            for r in rows:
                kb = KnowledgeBase(
                    kb_id=r["kb_id"],
                    name=r["name"],
                    description=r.get("description", ""),
                    chunk_strategy=r.get("chunk_strategy", "auto"),
                    chunk_size=r.get("chunk_size", 0),
                    chunk_overlap=r.get("chunk_overlap", 0),
                )
                kb.doc_count = r.get("doc_count", 0)
                kb.chunk_count = r.get("chunk_count", 0)
                kb.created_at = r.get("created_at", "")
                kb.updated_at = r.get("updated_at", "")
                self._bases[kb.kb_id] = kb
            logger.info(f"云端知识库注册表加载完成: {len(self._bases)} 个知识库")
        except Exception as e:
            logger.error(f"云端知识库注册表加载失败: {e}，回退到本地存储")
            self._use_cloud = False
            self._load_from_local()

    def _save(self):
        if self._use_cloud:
            return
        self._save_local()

    def _save_local(self):
        with self._lock:
            data = {
                "bases": [kb.to_dict() for kb in self._bases.values()],
            }
            temp_file = self._meta_file + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self._meta_file)

    def _save_kb_to_cloud(self, kb: KnowledgeBase):
        client = self._get_client()
        if not client:
            self._save_local()
            return

        try:
            row = kb.to_dict()
            client.table("kb_registry").upsert(row, on_conflict="kb_id").execute()
        except Exception as e:
            logger.error(f"云端知识库保存失败: {kb.kb_id}, {e}")

    def ensure_core(self) -> KnowledgeBase:
        kb = self._bases.get(CORE_KB_ID)
        if kb:
            return kb

        kb = KnowledgeBase(
            kb_id=CORE_KB_ID,
            name="核心知识库",
            description="固定的核心知识库，用于批量入库、清洗、切分和向量化。",
            chunk_strategy="auto",
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        )
        now = datetime.now().isoformat()
        kb.created_at = now
        kb.updated_at = now

        kb_dir = os.path.join(self._kb_dir, CORE_KB_ID)
        os.makedirs(os.path.join(kb_dir, "documents"), exist_ok=True)
        self._bases[CORE_KB_ID] = kb

        if self._use_cloud:
            self._save_kb_to_cloud(kb)
        else:
            self._save_local()

        logger.info("固定核心知识库已初始化: core")
        return kb

    def create(self, name: str, description: str = "", chunk_strategy: str = "auto",
               chunk_size: int = 0, chunk_overlap: int = 0) -> KnowledgeBase:
        kb_id = str(uuid.uuid4())
        kb = KnowledgeBase(
            kb_id=kb_id,
            name=name,
            description=description,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        from datetime import datetime
        kb.created_at = datetime.now().isoformat()
        kb.updated_at = kb.created_at

        kb_dir = os.path.join(self._kb_dir, kb_id)
        os.makedirs(kb_dir, exist_ok=True)
        os.makedirs(os.path.join(kb_dir, "documents"), exist_ok=True)

        with self._lock:
            self._bases[kb_id] = kb
            if self._use_cloud:
                self._save_kb_to_cloud(kb)
            else:
                self._save_local()

        logger.info(f"知识库创建成功: {name} ({kb_id})")
        return kb

    def get(self, kb_id: str) -> Optional[KnowledgeBase]:
        return self._bases.get(kb_id)

    def list_all(self) -> List[KnowledgeBase]:
        self.ensure_core()
        with self._lock:
            return sorted(
                list(self._bases.values()),
                key=lambda kb: (kb.kb_id != CORE_KB_ID, kb.created_at or "", kb.name or ""),
            )

    def delete(self, kb_id: str) -> bool:
        if kb_id == CORE_KB_ID:
            return False
        if kb_id not in self._bases:
            return False

        if self._use_cloud:
            client = self._get_client()
            if client:
                try:
                    client.table("kb_registry").delete().eq("kb_id", kb_id).execute()
                    client.table("kb_documents").delete().eq("kb_id", kb_id).execute()
                except Exception as e:
                    logger.error(f"云端知识库删除失败: {kb_id}, {e}")

            from app.core.cloud_storage import cloud_storage
            try:
                files = cloud_storage.list_files(f"knowledge_bases/{kb_id}")
                for f in files:
                    cloud_storage.delete_file(f"knowledge_bases/{kb_id}/{f['name']}")
            except Exception:
                pass
        else:
            kb_dir = os.path.join(self._kb_dir, kb_id)
            if os.path.exists(kb_dir):
                shutil.rmtree(kb_dir, ignore_errors=True)

        del self._bases[kb_id]
        self._save()
        logger.info(f"知识库删除成功: {kb_id}")
        return True

    def update_counts(self, kb_id: str, doc_delta: int = 0, chunk_delta: int = 0):
        with self._lock:
            kb = self._bases.get(kb_id)
            if not kb:
                return
            kb.doc_count = max(0, kb.doc_count + doc_delta)
            kb.chunk_count = max(0, kb.chunk_count + chunk_delta)
            kb.updated_at = datetime.now().isoformat()

            if self._use_cloud:
                self._save_kb_to_cloud(kb)
            else:
                self._save()

    def set_counts(self, kb_id: str, doc_count: int, chunk_count: int) -> bool:
        """Set authoritative counts after a mutation instead of accumulating drift."""
        with self._lock:
            kb = self._bases.get(kb_id)
            if not kb:
                return False
            kb.doc_count = max(0, int(doc_count or 0))
            kb.chunk_count = max(0, int(chunk_count or 0))
            kb.updated_at = datetime.now().isoformat()
            if self._use_cloud:
                self._save_kb_to_cloud(kb)
            else:
                self._save()
            return True

    def get_kb_dir(self, kb_id: str) -> str:
        return os.path.join(self._kb_dir, kb_id)

    def get_doc_dir(self, kb_id: str) -> str:
        doc_dir = os.path.join(self._kb_dir, kb_id, "documents")
        os.makedirs(doc_dir, exist_ok=True)
        return doc_dir

    def save_document(self, kb_id: str, doc_id: str, content: str, title: str = "", filename: str = "",
                      content_hash: str = "", original_size: int = 0, chunk_count: int = 0,
                      detected_doc_type: str = "", chunk_strategy: str = "") -> bool:
        if self._use_cloud:
            return self._save_document_cloud(
                kb_id, doc_id, content, title, filename,
                content_hash=content_hash,
                original_size=original_size,
                chunk_count=chunk_count,
                detected_doc_type=detected_doc_type,
                chunk_strategy=chunk_strategy,
            )
        else:
            return self._save_document_local(
                kb_id, doc_id, content, title=title, filename=filename,
                content_hash=content_hash,
                original_size=original_size,
                chunk_count=chunk_count,
                detected_doc_type=detected_doc_type,
                chunk_strategy=chunk_strategy,
            )

    def _save_document_local(self, kb_id: str, doc_id: str, content: str, title: str = "",
                             filename: str = "", content_hash: str = "", original_size: int = 0,
                             chunk_count: int = 0, detected_doc_type: str = "",
                             chunk_strategy: str = "") -> bool:
        try:
            doc_dir = self.get_doc_dir(kb_id)
            doc_path = os.path.join(doc_dir, f"{doc_id}.md")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(content)
            now = datetime.now().isoformat()
            meta_path = os.path.join(doc_dir, f"{doc_id}.meta.json")
            meta = {
                "doc_id": doc_id,
                "kb_id": kb_id,
                "title": title or Path(filename).stem or doc_id,
                "filename": filename or f"{doc_id}.md",
                "original_filename": filename or f"{doc_id}.md",
                "content_path": f"{doc_id}.md",
                "content_hash": content_hash or hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "file_size": original_size or len(content.encode("utf-8")),
                "stored_size": os.path.getsize(doc_path),
                "chunk_count": chunk_count,
                "detected_doc_type": detected_doc_type,
                "chunk_strategy": chunk_strategy,
                "created_at": now,
                "updated_at": now,
            }
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        old_meta = json.load(f)
                    meta["created_at"] = old_meta.get("created_at") or now
                except Exception:
                    pass
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"本地文档保存失败: {doc_id}, {e}")
            return False

    def _save_document_cloud(self, kb_id: str, doc_id: str, content: str, title: str = "", filename: str = "",
                             content_hash: str = "", original_size: int = 0, chunk_count: int = 0,
                             detected_doc_type: str = "", chunk_strategy: str = "") -> bool:
        from app.core.cloud_storage import cloud_storage
        cloud_path = f"knowledge_bases/{kb_id}/documents/{doc_id}.md"
        cloud_storage.upload_file(cloud_path, content.encode("utf-8"), "text/markdown")

        client = self._get_client()
        if client:
            try:
                row = {
                    "doc_id": doc_id,
                    "kb_id": kb_id,
                    "title": title,
                    "filename": filename,
                    "content_path": cloud_path,
                    "chunk_count": chunk_count,
                    "file_size": original_size or len(content.encode("utf-8")),
                    "content_hash": content_hash,
                    "detected_doc_type": detected_doc_type,
                    "chunk_strategy": chunk_strategy,
                    "created_at": self._bases[kb_id].updated_at if kb_id in self._bases else "",
                }
                try:
                    client.table("kb_documents").upsert(row, on_conflict="doc_id").execute()
                except Exception:
                    legacy_row = {k: row[k] for k in ("doc_id", "kb_id", "title", "filename", "content_path", "chunk_count", "file_size", "created_at")}
                    client.table("kb_documents").upsert(legacy_row, on_conflict="doc_id").execute()
            except Exception as e:
                logger.error(f"云端文档元数据保存失败: {doc_id}, {e}")

        self._save_document_local(
            kb_id, doc_id, content, title=title, filename=filename,
            content_hash=content_hash,
            original_size=original_size,
            chunk_count=chunk_count,
            detected_doc_type=detected_doc_type,
            chunk_strategy=chunk_strategy,
        )
        return True

    def load_document(self, kb_id: str, doc_id: str) -> Optional[str]:
        if self._use_cloud:
            return self._load_document_cloud(kb_id, doc_id)
        else:
            return self._load_document_local(kb_id, doc_id)

    def _load_document_local(self, kb_id: str, doc_id: str) -> Optional[str]:
        try:
            doc_path = os.path.join(self.get_doc_dir(kb_id), f"{doc_id}.md")
            if os.path.exists(doc_path):
                with open(doc_path, "r", encoding="utf-8") as f:
                    return f.read()
            return None
        except Exception as e:
            logger.error(f"本地文档加载失败: {doc_id}, {e}")
            return None

    def _load_document_cloud(self, kb_id: str, doc_id: str) -> Optional[str]:
        from app.core.cloud_storage import cloud_storage
        cloud_path = f"knowledge_bases/{kb_id}/documents/{doc_id}.md"
        data = cloud_storage.download_file(cloud_path)
        if data:
            return data.decode("utf-8")
        return self._load_document_local(kb_id, doc_id)

    def list_documents(self, kb_id: str) -> List[Dict]:
        if self._use_cloud:
            client = self._get_client()
            if client:
                try:
                    resp = client.table("kb_documents").select("*").eq("kb_id", kb_id).execute()
                    return resp.data or []
                except Exception as e:
                    logger.error(f"云端文档列表失败: {kb_id}, {e}")

        doc_dir = self.get_doc_dir(kb_id)
        docs = []
        if os.path.exists(doc_dir):
            for fname in os.listdir(doc_dir):
                if not fname.endswith(".md"):
                    continue
                doc_id = fname[:-3]
                fpath = os.path.join(doc_dir, fname)
                meta = self.get_document_meta(kb_id, doc_id) or {}
                filename = meta.get("filename") or meta.get("original_filename") or fname
                docs.append({
                    "doc_id": doc_id,
                    "filename": filename,
                    "original_filename": meta.get("original_filename") or filename,
                    "title": meta.get("title") or Path(filename).stem,
                    "file_size": meta.get("file_size") or os.path.getsize(fpath),
                    "stored_size": meta.get("stored_size") or os.path.getsize(fpath),
                    "content_hash": meta.get("content_hash", ""),
                    "chunk_count": meta.get("chunk_count", 0),
                    "detected_doc_type": meta.get("detected_doc_type", ""),
                    "chunk_strategy": meta.get("chunk_strategy", ""),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "content_path": fname,
                })
        return docs

    def get_document_meta(self, kb_id: str, doc_id: str) -> Optional[Dict]:
        try:
            meta_path = os.path.join(self.get_doc_dir(kb_id), f"{doc_id}.meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            return None
        except Exception as e:
            logger.warning(f"读取文档元数据失败: kb={kb_id}, doc={doc_id}, {e}")
            return None

    def delete_document(self, kb_id: str, doc_id: str) -> List[str]:
        deleted_files: List[str] = []

        if self._use_cloud:
            client = self._get_client()
            if client:
                try:
                    client.table("kb_documents").delete().eq("kb_id", kb_id).eq("doc_id", doc_id).execute()
                except Exception as e:
                    logger.error(f"云端文档元数据删除失败: kb={kb_id}, doc={doc_id}, {e}")
            try:
                from app.core.cloud_storage import cloud_storage
                cloud_storage.delete_file(f"knowledge_bases/{kb_id}/documents/{doc_id}.md")
            except Exception:
                pass

        doc_dir = self.get_doc_dir(kb_id)
        for suffix in (".md", ".meta.json"):
            path = os.path.join(doc_dir, f"{doc_id}{suffix}")
            if os.path.isfile(path):
                os.remove(path)
                deleted_files.append(os.path.basename(path))

        return deleted_files

    def find_duplicate_document(self, kb_id: str, content_hash: str = "", filename: str = "") -> Optional[Dict]:
        normalized_name = (filename or "").strip().lower()
        filename_match = None
        for doc in self.list_documents(kb_id):
            if content_hash and doc.get("content_hash") == content_hash:
                return {**doc, "duplicate_reason": "content_hash"}
            if normalized_name and (doc.get("original_filename") or doc.get("filename") or "").strip().lower() == normalized_name:
                filename_match = {**doc, "duplicate_reason": "filename"}
        return filename_match


class LazyKnowledgeBaseManager:
    """Delay KB registry loading until a knowledge-base endpoint needs it."""

    def __init__(self):
        object.__setattr__(self, "_instance", None)
        object.__setattr__(self, "_instance_lock", threading.RLock())

    def _get_instance(self) -> KnowledgeBaseManager:
        instance = object.__getattribute__(self, "_instance")
        if instance is not None:
            return instance
        lock = object.__getattribute__(self, "_instance_lock")
        with lock:
            instance = object.__getattribute__(self, "_instance")
            if instance is None:
                logger.info("Lazy loading knowledge base manager")
                instance = KnowledgeBaseManager()
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


kb_manager = LazyKnowledgeBaseManager()
