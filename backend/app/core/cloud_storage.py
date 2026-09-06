import os
import io
import json
import logging
import tempfile
from typing import Optional, List, Dict, Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class CloudFileStorage:
    def __init__(self):
        self._client = None
        self._bucket = settings.SUPABASE_BUCKET

    def _get_client(self):
        if self._client is not None:
            return self._client
        from app.core.supabase_client import get_supabase_client
        self._client = get_supabase_client()
        return self._client

    def upload_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        if not settings.use_supabase:
            return self._local_save(path, data)

        client = self._get_client()
        if not client:
            return self._local_save(path, data)

        try:
            client.storage.from_(self._bucket).upload(
                path, data, {"content-type": content_type, "upsert": "true"}
            )
            logger.info(f"云端文件上传成功: {path}")
            return True
        except Exception as e:
            logger.error(f"云端文件上传失败: {path}, {e}")
            return self._local_save(path, data)

    def download_file(self, path: str) -> Optional[bytes]:
        if not settings.use_supabase:
            return self._local_load(path)

        client = self._get_client()
        if not client:
            return self._local_load(path)

        try:
            resp = client.storage.from_(self._bucket).download(path)
            return resp
        except Exception as e:
            logger.error(f"云端文件下载失败: {path}, {e}")
            return self._local_load(path)

    def delete_file(self, path: str) -> bool:
        if not settings.use_supabase:
            return self._local_delete(path)

        client = self._get_client()
        if not client:
            return self._local_delete(path)

        try:
            client.storage.from_(self._bucket).remove([path])
            logger.info(f"云端文件删除成功: {path}")
            return True
        except Exception as e:
            logger.error(f"云端文件删除失败: {path}, {e}")
            return self._local_delete(path)

    def list_files(self, prefix: str = "") -> List[Dict[str, Any]]:
        if not settings.use_supabase:
            return self._local_list(prefix)

        client = self._get_client()
        if not client:
            return self._local_list(prefix)

        try:
            resp = client.storage.from_(self._bucket).list(prefix)
            return [{"name": f.get("name", ""), "size": f.get("metadata", {}).get("size", 0)} for f in resp]
        except Exception as e:
            logger.error(f"云端文件列表失败: {prefix}, {e}")
            return self._local_list(prefix)

    def get_public_url(self, path: str) -> Optional[str]:
        if not settings.use_supabase:
            return None

        client = self._get_client()
        if not client:
            return None

        try:
            resp = client.storage.from_(self._bucket).get_public_url(path)
            return resp
        except Exception as e:
            logger.error(f"获取文件URL失败: {path}, {e}")
            return None

    def _local_save(self, path: str, data: bytes) -> bool:
        try:
            full_path = os.path.join(settings.DATA_DIR, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            logger.error(f"本地文件保存失败: {path}, {e}")
            return False

    def _local_load(self, path: str) -> Optional[bytes]:
        try:
            full_path = os.path.join(settings.DATA_DIR, path)
            if os.path.exists(full_path):
                with open(full_path, "rb") as f:
                    return f.read()
            return None
        except Exception as e:
            logger.error(f"本地文件加载失败: {path}, {e}")
            return None

    def _local_delete(self, path: str) -> bool:
        try:
            full_path = os.path.join(settings.DATA_DIR, path)
            if os.path.exists(full_path):
                os.remove(full_path)
            return True
        except Exception as e:
            logger.error(f"本地文件删除失败: {path}, {e}")
            return False

    def _local_list(self, prefix: str = "") -> List[Dict[str, Any]]:
        try:
            full_dir = os.path.join(settings.DATA_DIR, prefix)
            if not os.path.isdir(full_dir):
                return []
            result = []
            for name in os.listdir(full_dir):
                fp = os.path.join(full_dir, name)
                if os.path.isfile(fp):
                    result.append({"name": name, "size": os.path.getsize(fp)})
            return result
        except Exception as e:
            logger.error(f"本地文件列表失败: {prefix}, {e}")
            return []


cloud_storage = CloudFileStorage()
