import os
import json
import uuid
import hashlib
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class ApiKeyService:
    def __init__(self):
        self._key_dir = os.path.join(settings.DATA_DIR, "api_keys")
        os.makedirs(self._key_dir, exist_ok=True)
        self._keys_file = os.path.join(self._key_dir, "keys.json")
        self._keys: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._keys_file):
            try:
                with open(self._keys_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._keys = data.get("keys", {})
                logger.info(f"API密钥加载完成: {len(self._keys)} 个")
            except Exception as e:
                logger.error(f"API密钥加载失败: {e}")

    def _save(self):
        data = {"keys": self._keys}
        with open(self._keys_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _hash_key(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def create_key(self, name: str, permissions: Optional[List[str]] = None,
                   expires_days: int = 0, created_by: str = "admin") -> Dict:
        raw_key = f"kh_{uuid.uuid4().hex[:32]}"
        key_hash = self._hash_key(raw_key)
        key_id = f"key_{uuid.uuid4().hex[:8]}"

        now = datetime.now()
        expires_at = ""
        if expires_days > 0:
            from datetime import timedelta
            expires_at = (now + timedelta(days=expires_days)).isoformat()

        key_data = {
            "key_id": key_id,
            "name": name,
            "key_hash": key_hash,
            "key_prefix": raw_key[:8] + "..." + raw_key[-4:],
            "permissions": permissions or ["read"],
            "created_by": created_by,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
            "last_used_at": "",
            "usage_count": 0,
            "is_active": True,
        }
        self._keys[key_id] = key_data
        self._save()

        return {
            "success": True,
            "key_id": key_id,
            "api_key": raw_key,
            "name": name,
            "permissions": key_data["permissions"],
            "expires_at": expires_at,
            "warning": "请妥善保管API密钥，创建后仅显示一次",
        }

    def validate_key(self, api_key: str, required_permission: str = "read") -> Dict:
        key_hash = self._hash_key(api_key)
        for key_id, key_data in self._keys.items():
            if key_data.get("key_hash") == key_hash:
                if not key_data.get("is_active", True):
                    return {"valid": False, "error": "API密钥已禁用"}
                if key_data.get("expires_at"):
                    if datetime.now().isoformat() > key_data["expires_at"]:
                        return {"valid": False, "error": "API密钥已过期"}
                if required_permission not in key_data.get("permissions", ["read"]):
                    return {"valid": False, "error": "权限不足"}
                key_data["last_used_at"] = datetime.now().isoformat()
                key_data["usage_count"] = key_data.get("usage_count", 0) + 1
                self._save()
                return {"valid": True, "key_id": key_id, "name": key_data["name"],
                        "permissions": key_data["permissions"]}
        return {"valid": False, "error": "无效的API密钥"}

    def list_keys(self) -> List[Dict]:
        results = []
        for key_data in self._keys.values():
            safe = key_data.copy()
            safe.pop("key_hash", None)
            results.append(safe)
        return results

    def revoke_key(self, key_id: str) -> bool:
        key_data = self._keys.get(key_id)
        if not key_data:
            return False
        key_data["is_active"] = False
        key_data["revoked_at"] = datetime.now().isoformat()
        self._save()
        return True

    def delete_key(self, key_id: str) -> bool:
        if key_id not in self._keys:
            return False
        del self._keys[key_id]
        self._save()
        return True

    def get_stats(self) -> Dict:
        total = len(self._keys)
        active = sum(1 for k in self._keys.values() if k.get("is_active", True))
        expired = 0
        for k in self._keys.values():
            if k.get("expires_at") and datetime.now().isoformat() > k["expires_at"]:
                expired += 1
        return {
            "total": total,
            "active": active,
            "expired": expired,
            "revoked": total - active,
        }


api_key_service = ApiKeyService()
