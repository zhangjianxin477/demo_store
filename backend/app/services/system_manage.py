import os
import json
import logging
import time
import re
from urllib.parse import urlsplit, urlunsplit
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings
from app.rag.vector_store import vector_store
from app.rag.kb_manager import kb_manager

logger = logging.getLogger(__name__)


class SystemManageService:
    def __init__(self):
        self._config_dir = os.path.join(settings.DATA_DIR, "system_config")
        os.makedirs(self._config_dir, exist_ok=True)
        self._model_config: Dict = {}
        self._prompt_config: Dict = {}
        self._chunk_config: Dict = {}
        self._load_configs()
        self._apply_runtime_model_config()

    def _load_configs(self):
        model_file = os.path.join(self._config_dir, "model_config.json")
        if os.path.exists(model_file):
            try:
                with open(model_file, "r", encoding="utf-8") as f:
                    self._model_config = json.load(f)
            except Exception as e:
                logger.error(f"模型配置加载失败: {e}")

        prompt_file = os.path.join(self._config_dir, "prompt_config.json")
        if os.path.exists(prompt_file):
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    self._prompt_config = json.load(f)
            except Exception as e:
                logger.error(f"Prompt配置加载失败: {e}")

        chunk_file = os.path.join(self._config_dir, "chunk_config.json")
        if os.path.exists(chunk_file):
            try:
                with open(chunk_file, "r", encoding="utf-8") as f:
                    self._chunk_config = json.load(f)
            except Exception as e:
                logger.error(f"切片配置加载失败: {e}")

    def _save_model_config(self):
        model_file = os.path.join(self._config_dir, "model_config.json")
        with open(model_file, "w", encoding="utf-8") as f:
            json.dump(self._model_config, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(model_file, 0o600)
        except OSError:
            pass

    def _apply_runtime_model_config(self) -> None:
        """Overlay persisted model settings onto process settings at startup."""
        collection = self._model_config.get("api_key_collection", {})
        base = collection.get("base_llm", {}) if isinstance(collection, dict) else {}
        fallback = collection.get("fallback_llm", {}) if isinstance(collection, dict) else {}
        embedding = collection.get("embedding", {}) if isinstance(collection, dict) else {}
        if not isinstance(base, dict):
            base = {}
        if not isinstance(fallback, dict):
            fallback = {}
        if not isinstance(embedding, dict):
            embedding = {}
        active_llm = base if base.get("enabled", True) and base.get("api_key") else fallback
        if active_llm.get("api_key"):
            settings.OPENAI_API_KEY = str(active_llm["api_key"])
        if active_llm.get("base_url"):
            settings.OPENAI_BASE_URL = str(active_llm["base_url"])
        if active_llm.get("model"):
            settings.OPENAI_MODEL = str(active_llm["model"])
        if embedding.get("api_key"):
            settings.EMBEDDING_API_KEY = str(embedding["api_key"])
        if embedding.get("base_url"):
            settings.EMBEDDING_BASE_URL = str(embedding["base_url"])
        if embedding.get("model"):
            settings.EMBEDDING_MODEL = str(embedding["model"])
        # Normalize legacy configs that stored an operation URL instead of the
        # OpenAI-compatible API root (for example .../v1/chat/completions).
        if active_llm.get("base_url"):
            settings.OPENAI_BASE_URL = self._normalize_endpoint(str(active_llm["base_url"]))
        if embedding.get("base_url"):
            settings.EMBEDDING_BASE_URL = self._normalize_endpoint(str(embedding["base_url"]))
        if self._model_config.get("embedding_dimension"):
            try:
                settings.EMBEDDING_DIMENSION = int(self._model_config["embedding_dimension"])
            except (TypeError, ValueError):
                pass

    def _default_model_key_collection(self) -> Dict[str, Dict[str, Any]]:
        return {
            "base_llm": {
                "label": "基础模型",
                "provider": "智谱 GLM",
                "model": "glm-4-flash",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key": "",
                "enabled": True,
                "note": "主问答、摘要、文档理解优先使用",
            },
            "fallback_llm": {
                "label": "兜底基础模型",
                "provider": "DeepSeek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "api_key": "",
                "enabled": True,
                "note": "主模型失败或限流时兜底",
            },
            "embedding": {
                "label": "Embedding 模型",
                "provider": "SiliconFlow",
                "model": "Pro/BAAI/bge-m3",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "",
                "enabled": True,
                "dimension": 1024,
                "note": "统一中文/多语言语义向量；切换后需重建向量索引",
            },
            "rerank": {
                "label": "Rerank 模型",
                "provider": "SiliconFlow",
                "model": "BAAI/bge-reranker-v2-m3",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "",
                "enabled": True,
                "note": "SiliconFlow 原生 /v1/rerank；需填 SiliconFlow API Key",
            },
        }

    def _mask_secret(self, value: str) -> str:
        if not value:
            return ""
        value = str(value)
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"

    def get_model_api_keys(self, reveal: bool = False) -> Dict:
        saved = self._model_config.get("api_key_collection", {})
        collection = self._default_model_key_collection()
        for key, value in saved.items():
            if key in collection and isinstance(value, dict):
                collection[key].update(value)

        safe_collection = {}
        for key, value in collection.items():
            item = dict(value)
            raw_key = item.get("api_key") or ""
            item["has_api_key"] = bool(raw_key)
            item["api_key_masked"] = raw_key if reveal else self._mask_secret(raw_key)
            if not reveal:
                item.pop("api_key", None)
            safe_collection[key] = item

        return {
            "collection": safe_collection,
            "updated_at": self._model_config.get("api_key_collection_updated_at", ""),
        }

    def set_model_api_keys(self, collection: Dict[str, Dict[str, Any]]) -> Dict:
        defaults = self._default_model_key_collection()
        current = self._model_config.get("api_key_collection", {})
        previous_embedding = current.get("embedding", {}) if isinstance(current, dict) else {}
        previous_embedding_model = str(previous_embedding.get("model") or "") if isinstance(previous_embedding, dict) else ""
        next_collection = {}

        for slot, default_item in defaults.items():
            existing = current.get(slot, {}) if isinstance(current.get(slot, {}), dict) else {}
            incoming = collection.get(slot, {}) if isinstance(collection.get(slot, {}), dict) else {}
            item = dict(default_item)
            item.update(existing)
            for field in ("label", "provider", "model", "base_url", "note", "dimension"):
                if field in incoming:
                    if field == "dimension":
                        try:
                            item[field] = int(incoming.get(field))
                        except (TypeError, ValueError):
                            pass
                    else:
                        value = str(incoming.get(field) or "").strip()
                        item[field] = self._normalize_endpoint(value) if field == "base_url" else value
            if "enabled" in incoming:
                item["enabled"] = bool(incoming.get("enabled"))
            if item.get("base_url"):
                item["base_url"] = self._normalize_endpoint(item["base_url"])
            if "bge-m3" in str(item.get("model") or "").lower():
                item["dimension"] = 1024
            if "api_key" in incoming:
                api_key = str(incoming.get("api_key") or "").strip()
                if api_key:
                    item["api_key"] = api_key
                elif incoming.get("clear_api_key") is True:
                    item["api_key"] = ""
            next_collection[slot] = item

        self._model_config["api_key_collection"] = next_collection
        self._model_config["api_key_collection_updated_at"] = datetime.now().isoformat()

        base_llm = next_collection.get("base_llm", {})
        embedding = next_collection.get("embedding", {})
        if base_llm.get("model"):
            self._model_config["llm_model"] = base_llm["model"]
            self._model_config["llm_base_url"] = base_llm.get("base_url", "")
            if base_llm.get("api_key"):
                self._model_config["llm_api_key"] = base_llm["api_key"]
        if embedding.get("model"):
            self._model_config["embedding_model"] = embedding["model"]
            self._model_config["embedding_base_url"] = embedding.get("base_url", "")
            embedding_model_lower = str(embedding["model"]).lower()
            if "kinfra-text-embedding-4b" in embedding_model_lower:
                self._model_config["embedding_dimension"] = 2560
            elif "bge-m3" in embedding_model_lower:
                self._model_config["embedding_dimension"] = 1024
            elif "gemini-embedding" in embedding_model_lower:
                self._model_config["embedding_dimension"] = int(embedding.get("dimension") or settings.GEMINI_EMBEDDING_DIMENSION)
            if embedding.get("api_key"):
                self._model_config["embedding_api_key"] = embedding["api_key"]

        self._model_config["updated_at"] = datetime.now().isoformat()
        self._save_model_config()
        try:
            from app.rag.embedding_service import embedding_service
            embedding_service.reload_config()
        except Exception as e:
            logger.warning(f"Embedding config saved but live reload failed: {e}")
        # Apply saved credentials immediately. Existing services are long-lived
        # singletons, so a file write alone would leave the running process on
        # the previous .env configuration until restart.
        try:
            self._apply_runtime_model_config()
            from app.rag.service import rag_service
            rag_service.reload_config()
        except Exception as e:
            logger.warning("LLM config saved but live reload failed: %s", e)
        next_embedding_model = str(next_collection.get("embedding", {}).get("model") or "")
        embedding_changed = bool(previous_embedding_model and next_embedding_model and previous_embedding_model != next_embedding_model)
        return {
            "success": True,
            "embedding_model_changed": embedding_changed,
            "reindex_required": embedding_changed,
            "reindex_message": "Embedding 模型已变更，必须对现有文档执行全量重建向量后再进行可靠检索" if embedding_changed else "",
            **self.get_model_api_keys(reveal=False),
        }

    def test_model_connection(self, slot: str, incoming: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Perform a real, minimal request against the selected provider."""
        if slot not in {"base_llm", "fallback_llm", "embedding", "rerank"}:
            return {"success": False, "slot": slot, "error_code": "INVALID_SLOT", "message": "不支持的模型槽位"}
        saved = self.get_model_api_keys(reveal=True)["collection"].get(slot, {})
        config = dict(saved)
        for key, value in (incoming or {}).items():
            if key != "api_key" or value:
                config[key] = value
        api_key = str(config.get("api_key") or "").strip()
        base_url = self._normalize_endpoint(str(config.get("base_url") or "").strip()) or None
        model = str(config.get("model") or "").strip()
        provider_lower = str(config.get("provider") or "").lower()
        # SiliconFlow's catalog uses the bare BAAI model id for this
        # reranker; normalize a commonly pasted Pro/BAAI alias.
        if "siliconflow" in provider_lower and model.lower().startswith("pro/baai/"):
            model = model[4:]
        identity = {
            "slot": slot,
            "provider": str(config.get("provider") or "未配置服务商"),
            "model": model,
            "base_url": self._safe_endpoint(base_url),
        }
        if not api_key:
            return {**identity, "success": False, "error_code": "MISSING_API_KEY", "message": "未提供 API Key，无法进行真实测试", "hint": "填写当前模型服务的真实 API Key 后再测试"}
        if not model:
            return {**identity, "success": False, "error_code": "MISSING_MODEL", "message": "未提供模型名，无法进行真实测试", "hint": "填写服务商实际支持的模型名"}
        started = time.perf_counter()
        try:
            if slot == "rerank":
                if not base_url:
                    return {**identity, "success": False, "latency_ms": round((time.perf_counter() - started) * 1000), "error_code": "INVALID_BASE_URL", "message": "Rerank 测试需要填写 /rerank 接口 Base URL", "hint": "填写兼容 Cohere/Jina 风格的 /rerank 服务地址"}
                import httpx
                endpoint = base_url.rstrip("/")
                if not endpoint.endswith("/rerank"):
                    endpoint += "/rerank"
                request_headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                if "moark" in provider_lower or "moark.com" in endpoint.lower():
                    request_headers["X-Failover-Enabled"] = "true"
                response = httpx.post(
                    endpoint,
                    # Native rerank contract: model, query, top_n, documents.
                    headers=request_headers,
                    json={
                        "model": model,
                        "query": "connection test",
                        "top_n": 1,
                        "documents": ["connection test document"],
                    },
                    timeout=20.0,
                )
                if response.status_code >= 400:
                    # Preserve the provider's validation message.  A bare
                    # ``raise_for_status`` only exposed "400 Bad Request",
                    # which made it impossible to tell whether the issue was
                    # the model id, request schema, or account entitlement.
                    detail = ""
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            detail = str(body.get("error") or body.get("message") or body.get("detail") or body)
                        else:
                            detail = str(body)
                    except Exception:
                        detail = response.text
                    detail = detail[:500]
                    exc = httpx.HTTPStatusError(
                        f"Rerank 服务返回 HTTP {response.status_code}: {detail}",
                        request=response.request,
                        response=response,
                    )
                    raise exc
                body = response.json() if response.content else {}
                return {**identity, "success": True, "latency_ms": round((time.perf_counter() - started) * 1000), "model": body.get("model", model), "result_count": len(body.get("results") or body.get("data") or [])}

            from openai import OpenAI
            if slot == "embedding" and ("gemini" in model.lower() or "google" in str(config.get("provider") or "").lower()):
                import httpx
                gemini_base = (base_url or "").rstrip("/")
                if "generativelanguage.googleapis.com" not in gemini_base.lower():
                    gemini_base = settings.GEMINI_EMBEDDING_BASE_URL.rstrip("/")
                model_path = model if model.startswith("models/") else f"models/{model}"
                endpoint = f"{gemini_base}/{model_path}:embedContent"
                response = httpx.post(
                    endpoint,
                    params={"key": api_key},
                    json={"model": model_path, "content": {"parts": [{"text": "knowledge hub connection test"}]}, "taskType": "RETRIEVAL_DOCUMENT", "outputDimensionality": int(config.get("dimension") or settings.GEMINI_EMBEDDING_DIMENSION)},
                    timeout=20.0,
                )
                response.raise_for_status()
                values = ((response.json().get("embedding") or {}).get("values") or [])
                return {**identity, "success": bool(values), "latency_ms": round((time.perf_counter() - started) * 1000), "dimension": len(values), "model": model}
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=20.0, max_retries=0)
            if slot == "embedding":
                response = client.embeddings.create(model=model, input="knowledge hub connection test")
                vector = response.data[0].embedding if response.data else []
                detail = {"dimension": len(vector), "model": getattr(response, "model", model)}
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with OK only."}],
                    max_tokens=8,
                    temperature=0,
                )
                detail = {"model": getattr(response, "model", model), "response": (response.choices[0].message.content or "").strip()[:80]}
            return {**identity, "success": True, "latency_ms": round((time.perf_counter() - started) * 1000), **detail}
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            error = self._diagnose_model_error(exc, base_url)
            logger.warning("模型连接测试失败 slot=%s code=%s status=%s", slot, error["error_code"], error.get("http_status"))
            return {**identity, "success": False, "latency_ms": latency_ms, **error}

    @staticmethod
    def _normalize_endpoint(value: str) -> str:
        """Accept either an API root or a pasted OpenAI operation URL."""
        normalized = str(value or "").strip().rstrip("/")
        for suffix in ("/chat/completions", "/embeddings", "/rerank"):
            if normalized.lower().endswith(suffix):
                normalized = normalized[:-len(suffix)].rstrip("/")
                break
        return normalized

    @staticmethod
    def _safe_endpoint(value: Optional[str]) -> str:
        """Return a display-safe URL without query credentials or fragments."""
        if not value:
            return ""
        try:
            parsed = urlsplit(str(value))
            # Rebuild netloc from hostname/port so accidental URL userinfo is never echoed.
            hostname = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path.rstrip("/"), "", ""))
        except Exception:
            return str(value).split("?", 1)[0].split("#", 1)[0]

    @staticmethod
    def _diagnose_model_error(exc: Exception, base_url: Optional[str]) -> Dict[str, Any]:
        """Map provider/client exceptions to actionable, non-secret diagnostics."""
        import httpx

        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status is None and isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code if exc.response else None
        raw = str(exc or "").strip()
        lowered = raw.lower()
        # Moark may return HTTP 400 when authentication succeeds but the
        # account has no billable inference resource.  This is distinct from
        # a malformed request and should tell the user to activate quota.
        quota_markers = (
            "没有可用的计费资源", "无可用的计费资源", "计费资源不足",
            "购买套餐", "充值", "余额不足", "insufficient quota",
            "billing resource", "no available.*resource",
        )
        if any(re.search(marker, raw, flags=re.IGNORECASE) for marker in quota_markers):
            code, hint = "QUOTA_INSUFFICIENT", "API Key 已到达服务商，但当前账号没有可用计费资源；请在 Moark 开通/购买对应模型套餐或充值后重试"
        elif status in (401, 403) or any(term in lowered for term in ("invalid api key", "authentication", "unauthorized", "forbidden")):
            code, hint = "AUTH_ERROR", "检查 API Key 是否有效、是否有该模型权限，以及服务商账号余额"
        elif status == 404 or "model not found" in lowered or "does not exist" in lowered:
            code, hint = "MODEL_NOT_FOUND", "检查模型名和 Base URL，确认该模型属于此服务商"
        elif status == 429 or "rate limit" in lowered or "too many requests" in lowered:
            code, hint = "RATE_LIMIT", "服务商限流或余额不足，请稍后重试并检查配额"
        elif isinstance(exc, (httpx.TimeoutException, TimeoutError)) or "timeout" in lowered:
            code, hint = "TIMEOUT", "模型响应超时，检查服务状态或降低请求复杂度"
        elif isinstance(exc, (httpx.ConnectError, httpx.NetworkError)) or any(term in lowered for term in ("connection error", "connecterror", "dns", "name or service not known")):
            code = "NETWORK_ERROR"
            try:
                target = urlsplit(str(base_url or "")).hostname or str(base_url or "模型服务")
            except Exception:
                target = str(base_url or "模型服务")
            # This failure happens before an HTTP response exists, so the key
            # and model have not been validated by the provider yet.
            message = f"服务器无法连接到 {target}:443，未收到服务商 HTTP 响应（API Key 尚未被校验）"
            hint = "检查服务器网络出口、DNS、防火墙或代理；可在同一台服务器执行 curl/Invoke-WebRequest 验证"
        elif status is not None and 400 <= int(status) < 500:
            code, hint = "INVALID_REQUEST", "请求参数不被服务商接受，检查模型类型、Base URL 和接口协议"
        elif status is not None and int(status) >= 500:
            code, hint = "PROVIDER_ERROR", "服务商暂时异常，请稍后重试或切换备用模型"
        elif not base_url:
            code, hint = "INVALID_BASE_URL", "未配置 Base URL；OpenAI 兼容服务需要填写 API 根地址"
        else:
            code, hint = "UNKNOWN_ERROR", "查看服务商返回信息并确认模型协议与请求格式"
        safe_message = raw[:500] if raw else "模型服务返回未知错误"
        if code == "NETWORK_ERROR":
            safe_message = message
        for secret_marker in ("api_key", "authorization", "bearer"):
            if secret_marker in lowered:
                safe_message = "服务商拒绝了请求（详细响应已隐藏敏感字段）"
                break
        return {"error_code": code, "message": safe_message, "hint": hint, **({"http_status": int(status)} if status is not None else {})}

    def _save_prompt_config(self):
        with open(os.path.join(self._config_dir, "prompt_config.json"), "w", encoding="utf-8") as f:
            json.dump(self._prompt_config, f, ensure_ascii=False, indent=2)

    def _save_chunk_config(self):
        with open(os.path.join(self._config_dir, "chunk_config.json"), "w", encoding="utf-8") as f:
            json.dump(self._chunk_config, f, ensure_ascii=False, indent=2)

    # ==================== Model Config ====================

    def get_model_config(self) -> Dict:
        safe_custom_configs = dict(self._model_config)
        for secret_key in ("llm_api_key", "embedding_api_key"):
            if safe_custom_configs.get(secret_key):
                safe_custom_configs[secret_key] = self._mask_secret(safe_custom_configs[secret_key])
        if "api_key_collection" in safe_custom_configs:
            safe_custom_configs["api_key_collection"] = self.get_model_api_keys(reveal=False)["collection"]
        return {
            "current_llm_model": settings.OPENAI_MODEL,
            "current_embedding_model": settings.EMBEDDING_MODEL,
            "embedding_dimension": settings.EMBEDDING_DIMENSION,
            "temperature": settings.OPENAI_TEMPERATURE,
            "max_tokens": settings.OPENAI_MAX_TOKENS,
            "custom_configs": safe_custom_configs,
        }

    def set_llm_model(self, model_name: str, base_url: Optional[str] = None,
                      api_key: Optional[str] = None) -> Dict:
        self._model_config["llm_model"] = model_name
        if base_url:
            self._model_config["llm_base_url"] = self._normalize_endpoint(base_url)
        if api_key:
            self._model_config["llm_api_key"] = api_key
        self._model_config["updated_at"] = datetime.now().isoformat()
        self._save_model_config()
        return {"success": True, "model": model_name}

    def set_embedding_model(self, model_name: str, dimension: int = 1024,
                            base_url: Optional[str] = None) -> Dict:
        # Never persist a stale dimension for BGE-M3.  The model's contract is
        # 1024 dimensions and the vector store relies on this invariant.
        if "bge-m3" in str(model_name or "").lower():
            dimension = 1024
        self._model_config["embedding_model"] = model_name
        self._model_config["embedding_dimension"] = dimension
        if base_url:
            self._model_config["embedding_base_url"] = self._normalize_endpoint(base_url)
        self._model_config["updated_at"] = datetime.now().isoformat()
        self._save_model_config()
        try:
            from app.rag.embedding_service import embedding_service
            embedding_service.reload_config()
        except Exception as e:
            logger.warning(f"Embedding model saved but live reload failed: {e}")
        return {"success": True, "model": model_name, "dimension": dimension}

    def list_available_models(self) -> Dict:
        return {
            "llm_models": [
                {"name": "glm-4-flash", "description": "智谱GLM-4-Flash，快速响应"},
                {"name": "glm-4", "description": "智谱GLM-4，标准模型"},
                {"name": "glm-4-plus", "description": "智谱GLM-4-Plus，增强模型"},
                {"name": "glm-4-long", "description": "智谱GLM-4-Long，长文本模型"},
            ],
            "embedding_models": [
                {"name": "Pro/BAAI/bge-m3", "description": "BGE-M3，多语言 Embedding，1024维", "dimension": 1024, "provider": "SiliconFlow"},
                {"name": "gemini-embedding-001", "description": "Google Gemini Embedding，原生 API，默认 2560 维", "dimension": 2560, "provider": "Google Gemini"},
                {"name": "embedding-3", "description": "智谱Embedding-3，2048维", "dimension": 2048},
                {"name": "embedding-2", "description": "智谱Embedding-2，1024维", "dimension": 1024},
            ],
        }

    # ==================== Prompt Config ====================

    def get_prompt_config(self, kb_id: str = "") -> Dict:
        if kb_id and kb_id in self._prompt_config:
            return self._prompt_config[kb_id]
        return {
            "system_prompt": "你是一个知识库问答助手，请基于提供的上下文内容回答问题。",
            "kb_id": kb_id,
        }

    def set_prompt_config(self, kb_id: str, system_prompt: str,
                          custom_instructions: str = "") -> Dict:
        self._prompt_config[kb_id] = {
            "kb_id": kb_id,
            "system_prompt": system_prompt,
            "custom_instructions": custom_instructions,
            "updated_at": datetime.now().isoformat(),
        }
        self._save_prompt_config()
        return self._prompt_config[kb_id]

    def list_prompt_configs(self) -> List[Dict]:
        return list(self._prompt_config.values())

    def delete_prompt_config(self, kb_id: str) -> bool:
        if kb_id not in self._prompt_config:
            return False
        del self._prompt_config[kb_id]
        self._save_prompt_config()
        return True

    # ==================== Chunk Config ====================

    def get_chunk_config(self, kb_id: str = "") -> Dict:
        if kb_id and kb_id in self._chunk_config:
            return self._chunk_config[kb_id]
        return {
            "chunk_size": settings.CHUNK_SIZE,
            "chunk_overlap": settings.CHUNK_OVERLAP,
            "strategy": "general",
            "kb_id": kb_id,
        }

    def set_chunk_config(self, kb_id: str, chunk_size: int = 512,
                         chunk_overlap: int = 64, strategy: str = "general") -> Dict:
        self._chunk_config[kb_id] = {
            "kb_id": kb_id,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "strategy": strategy,
            "updated_at": datetime.now().isoformat(),
        }
        self._save_chunk_config()
        return self._chunk_config[kb_id]

    def list_chunk_configs(self) -> List[Dict]:
        return list(self._chunk_config.values())

    # ==================== Vector Store Management ====================

    def get_vector_store_stats(self) -> Dict:
        """Return vector-store counters using the current storage representation.

        The local store used to expose an ``_embeddings`` list.  It now keeps
        embeddings in a NumPy matrix (``_embedding_matrix``), so this method
        must not reach into the removed field.  ``getattr`` keeps the endpoint
        compatible with older stores while the lazy proxy is initialized.
        """
        texts = getattr(vector_store, "_texts", []) or []
        doc_ids = getattr(vector_store, "_doc_ids", []) or []
        chunk_ids = getattr(vector_store, "_chunk_ids", []) or []
        matrix = getattr(vector_store, "_embedding_matrix", None)
        total_vectors = int(matrix.shape[0]) if matrix is not None and hasattr(matrix, "shape") else len(texts)
        return {
            "total_vectors": total_vectors,
            "total_documents": len(set(doc_ids)),
            "total_chunks": len(chunk_ids),
            "storage_file": getattr(vector_store, "_storage_file", ""),
            "meta_file": getattr(vector_store, "_meta_file", ""),
        }

    def _remove_local_indices(self, indices: List[int]) -> int:
        """Remove records from the local mirror and rebuild derived indexes."""
        if not indices:
            return 0
        texts = getattr(vector_store, "_texts", [])
        keep = [i for i in range(len(texts)) if i not in set(indices)]
        vector_store._texts = [texts[i] for i in keep]
        vector_store._doc_ids = [vector_store._doc_ids[i] for i in keep]
        vector_store._chunk_ids = [vector_store._chunk_ids[i] for i in keep]
        vector_store._metadata = [vector_store._metadata[i] for i in keep]
        matrix = getattr(vector_store, "_embedding_matrix", None)
        if matrix is not None and len(matrix) > 0:
            vector_store._embedding_matrix = matrix[keep]
        vector_store._invalidate_faiss()
        vector_store._rebuild_indexes()
        vector_store._save()
        return len(indices)

    def cleanup_vectors(self, kb_id: Optional[str] = None,
                        doc_id: Optional[str] = None) -> Dict:
        if doc_id:
            removed = vector_store.delete_by_doc(doc_id)
        elif kb_id:
            removed = vector_store.delete_by_kb(kb_id)
        else:
            empty_indices = [
                i for i, text in enumerate(getattr(vector_store, "_texts", []))
                if not text or not text.strip()
            ]
            removed = self._remove_local_indices(empty_indices)
        return {"success": True, "removed_count": removed}

    def revectorize(self, kb_id: Optional[str] = None) -> Dict:
        return {
            "success": True,
            "message": "重新向量化任务已提交",
            "kb_id": kb_id,
            "note": "重新向量化需要重新处理文档，请通过文档上传接口重新导入文档",
        }

    # ==================== System Info ====================

    def get_system_info(self) -> Dict:
        return {
            "app_name": settings.APP_NAME,
            "app_version": settings.APP_VERSION,
            "app_env": settings.APP_ENV,
            "python_version": __import__("sys").version,
            "data_dir": settings.DATA_DIR,
            "vector_store_stats": self.get_vector_store_stats(),
            "kb_count": len(kb_manager._bases),
            "model_config": self.get_model_config(),
        }


system_manage_service = SystemManageService()
