"""微信公众号开放平台 MCP 适配器。

这里提供业务级工具，而不是把任意 HTTP 请求暴露给 Agent。所有密钥只在
服务端使用，默认只允许状态检查和草稿操作，群发必须通过一次性审批令牌。
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WeChatMCPError(RuntimeError):
    pass


class WeChatMCPAdapter:
    BASE_URL = "https://api.weixin.qq.com"

    def __init__(self) -> None:
        self._access_token = ""
        self._expires_at = 0.0
        self._last_error = ""
        self._lock = asyncio.Lock()
        self._load_persisted_config()

    def _config_path(self) -> str:
        return os.path.join(settings.DATA_DIR, "system_config", "wechat_config.json")

    def _load_persisted_config(self) -> None:
        try:
            with open(self._config_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            for key in ("WECHAT_MCP_ENABLED", "WECHAT_APP_ID", "WECHAT_APP_SECRET", "WECHAT_DEFAULT_COVER_MEDIA_ID"):
                if key in data and data[key] is not None:
                    setattr(settings, key, data[key])
        except (OSError, ValueError, TypeError):
            pass

    def configure(self, enabled: bool, app_id: str, app_secret: str, default_cover_media_id: str = "") -> Dict[str, Any]:
        next_app_id = str(app_id or "").strip() or str(getattr(settings, "WECHAT_APP_ID", "") or "")
        next_secret = str(app_secret or "").strip() or str(getattr(settings, "WECHAT_APP_SECRET", "") or "")
        next_cover = str(default_cover_media_id or "").strip() or str(getattr(settings, "WECHAT_DEFAULT_COVER_MEDIA_ID", "") or "")
        payload = {
            "WECHAT_MCP_ENABLED": bool(enabled),
            "WECHAT_APP_ID": next_app_id,
            "WECHAT_APP_SECRET": next_secret,
            "WECHAT_DEFAULT_COVER_MEDIA_ID": next_cover,
        }
        os.makedirs(os.path.dirname(self._config_path()), exist_ok=True)
        with open(self._config_path(), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        try:
            os.chmod(self._config_path(), 0o600)
        except OSError:
            pass
        for key, value in payload.items():
            setattr(settings, key, value)
        self._access_token = ""
        self._expires_at = 0.0
        return self.status()

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "WECHAT_MCP_ENABLED", False))

    @property
    def configured(self) -> bool:
        return bool(getattr(settings, "WECHAT_APP_ID", None) and getattr(settings, "WECHAT_APP_SECRET", None))

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "token_cached": bool(self._access_token and self._expires_at > time.time()),
            "last_error": self._last_error,
            "capabilities": [
                "account_status", "image_upload", "cover_upload", "draft_create", "draft_update", "draft_get",
                "draft_list", "publish_submit", "publish_status",
            ] if self.enabled and self.configured else [],
            "publish_requires_approval": True,
            "default_cover_configured": bool(getattr(settings, "WECHAT_DEFAULT_COVER_MEDIA_ID", None)),
        }

    def _require_config(self) -> None:
        if not self.enabled:
            raise WeChatMCPError("微信公众号 MCP 未启用")
        if not self.configured:
            raise WeChatMCPError("未配置 WECHAT_APP_ID 和 WECHAT_APP_SECRET")

    async def _get_access_token(self) -> str:
        self._require_config()
        async with self._lock:
            if self._access_token and self._expires_at > time.time() + 120:
                return self._access_token
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        f"{self.BASE_URL}/cgi-bin/token",
                        params={"grant_type": "client_credential", "appid": settings.WECHAT_APP_ID, "secret": settings.WECHAT_APP_SECRET},
                    )
                payload = response.json()
                if payload.get("errcode"):
                    raise WeChatMCPError(f"微信 token 获取失败: {payload.get('errmsg', payload.get('errcode'))}")
                token = str(payload.get("access_token") or "")
                if not token:
                    raise WeChatMCPError("微信 token 响应缺少 access_token")
                self._access_token = token
                self._expires_at = time.time() + int(payload.get("expires_in") or 7200)
                self._last_error = ""
                return token
            except Exception as exc:
                self._last_error = str(exc)
                raise

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        token = await self._get_access_token()
        params = dict(kwargs.pop("params", {}) or {})
        params["access_token"] = token
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(method, f"{self.BASE_URL}{path}", params=params, **kwargs)
            payload = response.json()
            if payload.get("errcode"):
                raise WeChatMCPError(f"微信 API 失败: {payload.get('errmsg', payload.get('errcode'))}")
            self._last_error = ""
            return payload
        except Exception as exc:
            self._last_error = str(exc)
            raise

    async def account_status(self) -> Dict[str, Any]:
        self._require_config()
        await self._get_access_token()
        return self.status()

    @staticmethod
    def normalize_content(content_html: str) -> str:
        """将 Markdown/任意 HTML 限制为公众号文章所需的安全标签子集。"""
        text = str(content_html or "")
        text = re.sub(r"```(?:html)?\s*([\s\S]*?)```", r"<pre>\1</pre>", text, flags=re.I)
        text = re.sub(r"^###?\s+(.+)$", r"<h2>\1</h2>", text, flags=re.M)
        text = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", text, flags=re.M)
        text = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", text, flags=re.M)
        allowed = {"p", "br", "strong", "em", "h1", "h2", "h3", "ul", "ol", "li", "blockquote", "pre", "code", "a", "img", "table", "thead", "tbody", "tr", "th", "td"}

        def clean_tag(match: re.Match) -> str:
            closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3) or ""
            if name not in allowed:
                return ""
            if closing:
                return f"</{name}>"
            if name == "img":
                src = re.search(r"(?:src|data-src)\s*=\s*[\"']([^\"']+)", attrs, flags=re.I)
                return f'<img src="{html.escape(src.group(1), quote=True)}" />' if src else ""
            if name == "a":
                href = re.search(r"href\s*=\s*[\"']([^\"']+)", attrs, flags=re.I)
                return f'<a href="{html.escape(href.group(1), quote=True)}">' if href else "<a>"
            return f"<{name}>"

        text = re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>", clean_tag, text)
        paragraphs = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if not re.match(r"^<(h[1-3]|p|ul|ol|blockquote|pre|table|img|li|tr|thead|tbody)", line, flags=re.I):
                line = f"<p>{html.escape(line)}</p>"
            paragraphs.append(line)
        return "\n".join(paragraphs)[:90000]

    async def create_draft(self, article: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "articles": [{
                "title": str(article.get("title") or "未命名文章")[:64],
                "author": str(article.get("author") or "")[:16],
                "digest": str(article.get("digest") or "")[:120],
                "content": self.normalize_content(article.get("content_html") or article.get("content") or ""),
                "content_source_url": str(article.get("content_source_url") or "")[:255],
                "thumb_media_id": str(article.get("thumb_media_id") or getattr(settings, "WECHAT_DEFAULT_COVER_MEDIA_ID", "") or ""),
                "need_open_comment": int(article.get("need_open_comment") or 0),
                "only_fans_can_comment": int(article.get("only_fans_can_comment") or 0),
            }]
        }
        return await self._request("POST", "/cgi-bin/draft/add", json=payload)

    async def upload_article_image(self, data: bytes, filename: str = "image.jpg", mime: str = "image/jpeg") -> Dict[str, Any]:
        """Upload an article image and return the URL accepted by WeChat."""
        self._require_config()
        token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/cgi-bin/media/uploadimg",
                    params={"access_token": token},
                    files={"media": (filename, data, mime)},
                )
            payload = response.json()
            if payload.get("errcode"):
                raise WeChatMCPError(f"微信图片上传失败: {payload.get('errmsg', payload.get('errcode'))}")
            url = str(payload.get("url") or "")
            if not url:
                raise WeChatMCPError("微信图片上传响应缺少 URL")
            self._last_error = ""
            return {"url": url}
        except Exception as exc:
            self._last_error = str(exc)
            raise

    async def upload_cover_image(self, data: bytes, filename: str = "cover.jpg", mime: str = "image/jpeg") -> Dict[str, Any]:
        """Upload a permanent material used as the draft cover image."""
        self._require_config()
        token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/cgi-bin/material/add_material",
                    params={"access_token": token, "type": "image"},
                    files={"media": (filename, data, mime)},
                )
            payload = response.json()
            if payload.get("errcode"):
                raise WeChatMCPError(f"微信封面上传失败: {payload.get('errmsg', payload.get('errcode'))}")
            media_id = str(payload.get("media_id") or "")
            if not media_id:
                raise WeChatMCPError("微信封面上传响应缺少 media_id")
            self._last_error = ""
            return {"media_id": media_id, "url": payload.get("url", "")}
        except Exception as exc:
            self._last_error = str(exc)
            raise

    async def update_draft(self, media_id: str, article: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"media_id": media_id, "index": 0, "articles": {
            "title": str(article.get("title") or "未命名文章")[:64],
            "author": str(article.get("author") or "")[:16],
            "digest": str(article.get("digest") or "")[:120],
            "content": self.normalize_content(article.get("content_html") or article.get("content") or ""),
            "content_source_url": str(article.get("content_source_url") or "")[:255],
            "thumb_media_id": str(article.get("thumb_media_id") or getattr(settings, "WECHAT_DEFAULT_COVER_MEDIA_ID", "") or ""),
        }}
        return await self._request("POST", "/cgi-bin/draft/update", json=payload)

    async def get_draft(self, media_id: str) -> Dict[str, Any]:
        return await self._request("POST", "/cgi-bin/draft/get", json={"media_id": media_id})

    async def list_drafts(self, offset: int = 0, count: int = 20) -> Dict[str, Any]:
        return await self._request("POST", "/cgi-bin/draft/batchget", json={"offset": max(0, offset), "count": min(max(1, count), 20), "no_content": 1})

    async def submit_publish(self, media_id: str) -> Dict[str, Any]:
        return await self._request("POST", "/cgi-bin/freepublish/submit", json={"media_id": media_id})

    async def publish_status(self, publish_id: str) -> Dict[str, Any]:
        return await self._request("POST", "/cgi-bin/freepublish/get", json={"publish_id": publish_id})


wechat_mcp = WeChatMCPAdapter()
