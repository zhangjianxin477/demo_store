import os
import uuid
import json
import logging
import hashlib
import shutil
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.models.wiki import wiki_store

logger = logging.getLogger(__name__)


class Attachment:
    def __init__(self, attach_id: str = "", page_id: str = "", filename: str = "",
                 original_name: str = "", file_size: int = 0, mime_type: str = "",
                 author: str = "system", description: str = ""):
        self.attach_id = attach_id or str(uuid.uuid4())
        self.page_id = page_id
        self.filename = filename
        self.original_name = original_name
        self.file_size = file_size
        self.mime_type = mime_type
        self.author = author
        self.description = description
        self.file_hash = ""
        self.created_at = ""
        self.updated_at = ""

    def to_dict(self) -> Dict:
        return {
            "attach_id": self.attach_id,
            "page_id": self.page_id,
            "filename": self.filename,
            "original_name": self.original_name,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "author": self.author,
            "description": self.description,
            "file_hash": self.file_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Attachment":
        a = cls(
            attach_id=data.get("attach_id", ""),
            page_id=data.get("page_id", ""),
            filename=data.get("filename", ""),
            original_name=data.get("original_name", ""),
            file_size=data.get("file_size", 0),
            mime_type=data.get("mime_type", ""),
            author=data.get("author", "system"),
            description=data.get("description", ""),
        )
        a.file_hash = data.get("file_hash", "")
        a.created_at = data.get("created_at", "")
        a.updated_at = data.get("updated_at", "")
        return a


class AttachmentService:
    PREVIEW_MIMES = {
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
        "application/pdf", "text/plain", "text/markdown", "text/csv",
    }

    def __init__(self):
        self._attach_dir = os.path.join(settings.DATA_DIR, "wiki_attachments")
        self._meta_dir = os.path.join(self._attach_dir, "meta")
        self._files_dir = os.path.join(self._attach_dir, "files")
        os.makedirs(self._meta_dir, exist_ok=True)
        os.makedirs(self._files_dir, exist_ok=True)
        self._attachments: Dict[str, Attachment] = {}
        self._load_all()

    def _load_all(self):
        for f in os.listdir(self._meta_dir):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(self._meta_dir, f), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    att = Attachment.from_dict(data)
                    self._attachments[att.attach_id] = att
                except Exception as e:
                    logger.error(f"附件元数据加载失败 {f}: {e}")
        logger.info(f"附件数据加载完成: {len(self._attachments)} 个")

    def _save_attachment(self, att: Attachment):
        meta_file = os.path.join(self._meta_dir, f"{att.attach_id}.json")
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(att.to_dict(), f, ensure_ascii=False, indent=2)

    def _get_mime_type(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".ppt": "application/vnd.ms-powerpoint",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".csv": "text/csv",
            ".json": "application/json",
            ".zip": "application/zip",
            ".mp4": "video/mp4",
            ".mp3": "audio/mpeg",
        }
        return mime_map.get(ext, "application/octet-stream")

    def upload(self, page_id: str, file_data: bytes, original_name: str,
               author: str = "system", description: str = "") -> Attachment:
        file_hash = hashlib.sha256(file_data).hexdigest()[:16]
        ext = os.path.splitext(original_name)[1]
        stored_name = f"{uuid.uuid4().hex[:12]}{ext}"
        file_path = os.path.join(self._files_dir, stored_name)

        with open(file_path, "wb") as f:
            f.write(file_data)

        att = Attachment(
            page_id=page_id,
            filename=stored_name,
            original_name=original_name,
            file_size=len(file_data),
            mime_type=self._get_mime_type(original_name),
            author=author,
            description=description,
        )
        att.file_hash = file_hash
        now = datetime.now().isoformat()
        att.created_at = now
        att.updated_at = now

        self._attachments[att.attach_id] = att
        self._save_attachment(att)

        logger.info(f"附件上传: {original_name} -> {page_id} ({att.attach_id})")
        return att

    def get(self, attach_id: str) -> Optional[Attachment]:
        return self._attachments.get(attach_id)

    def get_file_path(self, attach_id: str) -> Optional[str]:
        att = self._attachments.get(attach_id)
        if not att:
            return None
        path = os.path.join(self._files_dir, att.filename)
        return path if os.path.exists(path) else None

    def list_by_page(self, page_id: str) -> List[Attachment]:
        return [a for a in self._attachments.values() if a.page_id == page_id]

    def delete(self, attach_id: str) -> bool:
        att = self._attachments.get(attach_id)
        if not att:
            return False
        file_path = os.path.join(self._files_dir, att.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        meta_file = os.path.join(self._meta_dir, f"{attach_id}.json")
        if os.path.exists(meta_file):
            os.remove(meta_file)
        del self._attachments[attach_id]
        logger.info(f"附件删除: {att.original_name} ({attach_id})")
        return True

    def can_preview(self, attach_id: str) -> bool:
        att = self._attachments.get(attach_id)
        if not att:
            return False
        return att.mime_type in self.PREVIEW_MIMES

    def get_stats(self) -> Dict[str, Any]:
        total_size = sum(a.file_size for a in self._attachments.values())
        return {
            "total_files": len(self._attachments),
            "total_size": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }


attachment_service = AttachmentService()
