"""JWT 认证中间件 - 支持 API Key 和 Token 双模式认证。

认证方式：
1. Bearer Token: Authorization: Bearer <jwt_token>
2. API Key: X-API-Key: <api_key> 或 Authorization: ApiKey <api_key>
"""

import os
import time
import hashlib
import logging
from typing import Optional
from datetime import datetime, timedelta

import jwt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings

logger = logging.getLogger(__name__)

# JWT 配置
JWT_SECRET = settings.SECRET_KEY
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24
JWT_REFRESH_EXPIRATION_DAYS = 30

# API Key 存储（生产环境应使用数据库）
_api_keys_file = os.path.join(settings.DATA_DIR, "api_keys.json")
_api_keys: dict = {}  # key_hash -> {name, created_at, permissions}
_api_keys_loaded = False

security = HTTPBearer(auto_error=False)


def _load_api_keys():
    """从文件加载 API Keys。"""
    global _api_keys, _api_keys_loaded
    if _api_keys_loaded:
        return
    _api_keys_loaded = True
    if os.path.exists(_api_keys_file):
        try:
            import json
            with open(_api_keys_file, "r", encoding="utf-8") as f:
                _api_keys = json.load(f)
            logger.info(f"已加载 {len(_api_keys)} 个 API Keys")
        except Exception as e:
            logger.error(f"加载 API Keys 失败: {e}")
            _api_keys = {}


def _save_api_keys():
    """持久化 API Keys 到文件。"""
    try:
        import json
        os.makedirs(os.path.dirname(_api_keys_file), exist_ok=True)
        with open(_api_keys_file, "w", encoding="utf-8") as f:
            json.dump(_api_keys, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 API Keys 失败: {e}")


def _hash_api_key(key: str) -> str:
    """对 API Key 进行哈希处理，避免明文存储。"""
    return hashlib.sha256(key.encode()).hexdigest()


def create_access_token(
    user_id: str,
    extra_claims: Optional[dict] = None,
    permissions: Optional[list] = None,
) -> str:
    """生成 JWT access token。

    Args:
        user_id: 用户标识
        extra_claims: 额外的 JWT claims

    Returns:
        JWT token 字符串
    """
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRATION_HOURS),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    if permissions is not None:
        payload["permissions"] = permissions
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, extra_claims: Optional[dict] = None) -> str:
    """生成 JWT refresh token。

    Args:
        user_id: 用户标识

    Returns:
        JWT refresh token 字符串
    """
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=JWT_REFRESH_EXPIRATION_DAYS),
        "type": "refresh",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码并验证 JWT token。

    Args:
        token: JWT token 字符串

    Returns:
        解码后的 payload；token 无效或过期时返回 None
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def generate_api_key(name: str, permissions: Optional[list] = None) -> dict:
    """生成新的 API Key。

    Args:
        name: API Key 名称/描述
        permissions: 权限列表，如 ["read", "write"]

    Returns:
        生成的 API Key（仅在此处可见，之后只存哈希）
    """
    import secrets
    raw_key = f"kh_{secrets.token_hex(24)}"
    key_hash = _hash_api_key(raw_key)
    _api_keys[key_hash] = {
        "name": name,
        "created_at": time.time(),
        "permissions": permissions or ["read", "write"],
    }
    _save_api_keys()
    logger.info(f"创建 API Key: {name}")
    return {"key": raw_key, "name": name, "permissions": permissions or ["read", "write"]}


def verify_api_key(raw_key: str) -> Optional[dict]:
    """验证 API Key。

    Args:
        raw_key: 原始 API Key

    Returns:
        验证成功返回 key 信息，失败返回 None
    """
    _load_api_keys()
    key_hash = _hash_api_key(raw_key)
    return _api_keys.get(key_hash)


def revoke_api_key(raw_key: str) -> bool:
    """撤销 API Key。

    Args:
        raw_key: 原始 API Key

    Returns:
        是否成功撤销
    """
    _load_api_keys()
    key_hash = _hash_api_key(raw_key)
    if key_hash in _api_keys:
        del _api_keys[key_hash]
        _save_api_keys()
        return True
    return False


def list_api_keys() -> list:
    """列出所有 API Key（不含原始 key）。"""
    _load_api_keys()
    return [
        {"hash": h[:12] + "...", "name": v["name"], "created_at": v["created_at"],
         "permissions": v["permissions"]}
        for h, v in _api_keys.items()
    ]


# 认证开关 - 开发模式可关闭认证
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").lower() == "true"


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """获取当前认证用户，支持 JWT Token 和 API Key 双模式。

    当 AUTH_ENABLED=false 时，跳过认证（开发模式）。

    Args:
        request: FastAPI 请求对象
        credentials: HTTP Bearer 凭证

    Returns:
        用户信息字典

    Raises:
        HTTPException: 认证失败
    """
    if not AUTH_ENABLED:
        return {"user_id": "anonymous", "permissions": ["read", "write"]}

    # 尝试从 X-API-Key header 获取
    api_key = request.headers.get("X-API-Key")
    if api_key:
        key_info = verify_api_key(api_key)
        if key_info:
            return {"user_id": f"apikey:{key_info['name']}", "permissions": key_info["permissions"]}
        raise HTTPException(status_code=401, detail="无效的 API Key")

    # 尝试从 Authorization header 获取
    if credentials:
        token = credentials.credentials
        # 检查是否为 API Key 格式（kh_ 开头）
        if token.startswith("kh_"):
            key_info = verify_api_key(token)
            if key_info:
                return {"user_id": f"apikey:{key_info['name']}", "permissions": key_info["permissions"]}
            raise HTTPException(status_code=401, detail="无效的 API Key")

        # JWT Token 验证
        payload = decode_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="无效的 Token")
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="需要 access token")
        return {
            "user_id": payload.get("sub"),
            "permissions": payload.get("permissions", ["read", "write"]),
        }

    raise HTTPException(status_code=401, detail="未提供认证凭证")


async def require_write_permission(
    user: Optional[dict] = Depends(get_current_user),
) -> dict:
    """要求写权限的依赖项。"""
    if not AUTH_ENABLED:
        return user
    if "write" not in (user.get("permissions") or []):
        raise HTTPException(status_code=403, detail="需要写权限")
    return user
