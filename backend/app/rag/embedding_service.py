import asyncio
import logging
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)


class LocalEmbeddingModel:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model = None
        self._model_name = model_name
        self._dimension = 384
        self._available = False
        self._attempted = False

    def initialize(self) -> bool:
        if self._attempted:
            return self._available
        self._attempted = True
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            test_vec = self._model.encode(["test"], show_progress_bar=False)
            self._dimension = test_vec.shape[1]
            self._available = True
            logger.info(f"鏈湴宓屽叆妯″瀷鍔犺浇鎴愬姛: {self._model_name}, dim={self._dimension}")
            return True
        except ImportError:
            logger.warning("sentence-transformers 鏈畨瑁咃紝鏈湴宓屽叆妯″瀷涓嶅彲鐢ㄣ€傚畨瑁? pip install sentence-transformers")
            return False
        except Exception as e:
            logger.warning(f"鏈湴宓屽叆妯″瀷鍔犺浇澶辫触: {e}")
            return False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not self._available or not self._model:
            return []
        embeddings = self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return embeddings.tolist()


class EmbeddingService:
    def __init__(self):
        self._client = None
        self._model = settings.EMBEDDING_MODEL
        self._dimension = settings.EMBEDDING_DIMENSION
        self._cache = {}
        self._cache_max = 2000
        self._local_model = LocalEmbeddingModel(model_name=settings.LOCAL_EMBEDDING_MODEL)
        self._using_local = False
        self._degraded = False  # 鏍囪鏄惁闄嶇骇涓哄搱甯屼吉鍚戦噺
        self._last_error = ""
        self._last_provider = ""
        self._gemini_api_key = ""
        self._gemini_base_url = ""
        self._gemini_model = ""
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._init_client()

    def _infer_dimension(self, model: str, configured_dimension: Optional[int] = None) -> int:
        model_lower = (model or "").lower()
        # Model-specific dimensions take precedence over legacy persisted
        # values.  This prevents a stale 2560/2048 setting from silently
        # overriding the required 1024-dim BGE-M3 index.
        if "bge-m3" in model_lower:
            return 1024
        if "kinfra-text-embedding-4b" in model_lower:
            return 2560
        if "embedding-3" in model_lower:
            return 2048
        if "embedding-2" in model_lower:
            return 1024
        if "gemini-embedding" in model_lower:
            return settings.GEMINI_EMBEDDING_DIMENSION
        if configured_dimension:
            try:
                return int(configured_dimension)
            except (TypeError, ValueError):
                pass
        return settings.EMBEDDING_DIMENSION

    def _load_saved_embedding_config(self) -> Dict[str, Any]:
        model_file = os.path.join(settings.DATA_DIR, "system_config", "model_config.json")
        if not os.path.exists(model_file):
            return {}

        try:
            with open(model_file, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load saved embedding config, falling back to .env: {e}")
            return {}

        collection = config.get("api_key_collection", {}) if isinstance(config, dict) else {}
        embedding = collection.get("embedding", {}) if isinstance(collection, dict) else {}
        if isinstance(embedding, dict) and embedding.get("enabled", True) and embedding.get("api_key"):
            model = str(embedding.get("model") or config.get("embedding_model") or settings.EMBEDDING_MODEL).strip()
            return {
                "api_key": str(embedding.get("api_key") or "").strip(),
                "base_url": str(embedding.get("base_url") or "").strip(),
                "model": model,
                "dimension": self._infer_dimension(model, config.get("embedding_dimension")),
                "provider": str(embedding.get("provider") or "").strip(),
                "source": "model-api-keys",
            }

        if isinstance(config, dict) and config.get("embedding_api_key"):
            model = str(config.get("embedding_model") or settings.EMBEDDING_MODEL).strip()
            return {
                "api_key": str(config.get("embedding_api_key") or "").strip(),
                "base_url": str(config.get("embedding_base_url") or "").strip(),
                "model": model,
                "dimension": self._infer_dimension(model, config.get("embedding_dimension")),
                "provider": "",
                "source": "model-config",
            }

        return {}

    def _init_client(self):
        self._client = None
        self._using_local = False
        self._last_provider = ""
        self._gemini_api_key = ""
        self._gemini_base_url = ""
        self._gemini_model = ""
        self._model = settings.EMBEDDING_MODEL
        self._dimension = settings.EMBEDDING_DIMENSION

        local_enabled = settings.EMBEDDING_LOCAL_ENABLED
        prefer_local = settings.EMBEDDING_PREFER_LOCAL and local_enabled

        if prefer_local:
            if self._local_model.initialize():
                self._dimension = self._local_model.dimension
                self._using_local = True
                logger.info(f"浼樺厛浣跨敤鏈湴宓屽叆妯″瀷, dim={self._dimension}")
            else:
                logger.warning("EMBEDDING_PREFER_LOCAL=True 浣嗘湰鍦版ā鍨嬪姞杞藉け璐ワ紝灏濊瘯 API")

        if not self._using_local:
            saved_config = self._load_saved_embedding_config()
            embedding_api_key = saved_config.get("api_key") or settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY
            embedding_base_url = saved_config.get("base_url") or settings.EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL
            self._model = saved_config.get("model") or settings.EMBEDDING_MODEL
            self._dimension = saved_config.get("dimension") or self._infer_dimension(self._model)
            provider_source = saved_config.get("source") or ".env"
            provider_name = str(saved_config.get("provider") or "").lower()

            # Gemini Embeddings uses Google's native REST API.  It is not
            # OpenAI-compatible, so constructing an OpenAI client here would
            # silently send an invalid /embeddings request.
            if "gemini" in self._model.lower() or "google" in provider_name:
                self._gemini_api_key = (
                    saved_config.get("api_key")
                    or settings.GEMINI_API_KEY
                    or ""
                ).strip()
                self._gemini_model = self._model or settings.GEMINI_EMBEDDING_MODEL
                self._dimension = self._infer_dimension(self._gemini_model, saved_config.get("dimension"))
                # A previously configured Tencent/OpenAI-compatible URL may
                # remain in the slot when the model is changed in the UI. Do
                # not append Google's native model path to that old endpoint.
                configured_host = (embedding_base_url or "").lower()
                if "generativelanguage.googleapis.com" not in configured_host:
                    embedding_base_url = settings.GEMINI_EMBEDDING_BASE_URL
                self._gemini_base_url = (embedding_base_url or settings.GEMINI_EMBEDDING_BASE_URL).rstrip("/")
                self._last_provider = f"{provider_source}:google-gemini"
                if not self._gemini_api_key:
                    logger.warning("Gemini Embedding API Key 未配置")
                else:
                    logger.info("Gemini Embedding 服务已初始化: model=%s, dim=%s", self._gemini_model, self._dimension)
                return

            self._last_provider = f"{provider_source}:{embedding_base_url or 'local/hash'}"

            if not embedding_api_key:
                logger.warning("Embedding API Key 未配置，尝试加载本地嵌入模型")
                if local_enabled and self._local_model.initialize():
                    self._dimension = self._local_model.dimension
                    self._using_local = True
                    logger.info(f"已切换到本地嵌入模型, dim={self._dimension}")
                else:
                    logger.warning("本地嵌入模型也不可用，Embedding 将使用哈希向量回退")
                return

            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=embedding_api_key,
                    base_url=embedding_base_url,
                    timeout=15.0,
                    max_retries=0,
                )
                logger.info(f"Embedding 鏈嶅姟鍒濆鍖栨垚鍔? model={self._model}, dim={self._dimension}, base_url={embedding_base_url}")
            except Exception as e:
                logger.error(f"Embedding 瀹㈡埛绔垵濮嬪寲澶辫触: {e}")
                self._client = None
                if local_enabled and self._local_model.initialize():
                    self._dimension = self._local_model.dimension
                    self._using_local = True
                    logger.info(f"API 涓嶅彲鐢紝宸插垏鎹㈠埌鏈湴宓屽叆妯″瀷, dim={self._dimension}")

    def reload_config(self):
        self._cache.clear()
        self._degraded = False
        self._last_error = ""
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._init_client()

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def using_local_model(self) -> bool:
        return self._using_local

    @property
    def is_degraded(self) -> bool:
        """Whether the service has fallen back to hash embeddings."""
        return self._degraded

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def provider(self) -> str:
        if self._using_local:
            return f"local:{self._local_model._model_name}"
        return self._last_provider or (settings.EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL or "hash")

    def _mark_success(self):
        self._degraded = False
        self._last_error = ""
        self._failure_count = 0
        self._circuit_open_until = 0.0

    def _mark_error(self, error: Exception):
        message = str(error)
        lowered = message.lower()
        quota_exhausted = any(marker in lowered for marker in (
            "error code: 402",
            "status_code=402",
            "free trial quota",
            "quota exhausted",
            "insufficient quota",
            "postpaid billing",
            "额度已耗尽",
            "余额不足",
            "资源包",
            "401008",
        ))
        rate_limited = any(marker in lowered for marker in (
            "error code: 429",
            "status_code=429",
            "too many requests",
            "rate limit",
            "1113",
        ))
        auth_failed = (
            "unauthorized" in lowered
            or "invalid api key" in lowered
            or "invalid apipassword" in lowered
            or bool(re.search(r"(?:error code|status(?:_code)?)\s*[:=]?\s*(?:401|403)\b", lowered))
        )

        if quota_exhausted:
            self._last_error = "Embedding 服务额度已耗尽或未开通后付费，当前已自动切换为关键词检索"
            cooldown = 300
        elif rate_limited:
            self._last_error = "Embedding API 正在限流，当前已自动切换为关键词检索"
            cooldown = 60
        elif auth_failed:
            self._last_error = "Embedding API 鉴权失败，请检查 Key 或 APIPassword"
            cooldown = 300
        elif "404" in lowered or "model" in lowered:
            self._last_error = "Embedding API 地址或模型名不正确"
            cooldown = 300
        else:
            self._last_error = message[:300]
            cooldown = 30

        self._failure_count += 1
        self._circuit_open_until = time.monotonic() + cooldown

    def _api_available(self) -> bool:
        return (self._client is not None or bool(self._gemini_api_key)) and time.monotonic() >= self._circuit_open_until

    def _hash_embedding(self, text: str) -> List[float]:
        self._degraded = True
        if not self._last_error:
            self._last_error = "Embedding API 鍜屾湰鍦版ā鍨嬮兘涓嶅彲鐢紝宸蹭娇鐢ㄥ搱甯屽悜閲忓洖閫€"
        h = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(h[:4], "big")
        rng = np.random.RandomState(seed)
        vec = rng.randn(self._dimension).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    def _get_cache_key(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _cache_put(self, key: str, value: List[float]):
        if len(self._cache) >= self._cache_max:
            keys = list(self._cache.keys())
            for k in keys[:len(keys) // 2]:
                del self._cache[k]
        self._cache[key] = value

    async def embed_text(self, text: str) -> List[float]:
        if not text or not text.strip():
            return [0.0] * self._dimension

        cache_key = self._get_cache_key(text)
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._using_local and self._local_model.available:
            try:
                result = await asyncio.to_thread(self._local_model.encode, [text])
                if result and len(result) > 0:
                    embedding = result[0]
                    self._cache_put(cache_key, embedding)
                    self._mark_success()
                    return embedding
            except Exception as e:
                logger.error(f"鏈湴宓屽叆妯″瀷璋冪敤澶辫触: {e}")
                self._mark_error(e)

        if self._api_available():
            try:
                result = await self._call_embedding_api(text)
                if result:
                    self._cache_put(cache_key, result)
                    self._mark_success()
                    return result
            except Exception as e:
                logger.error(f"Embedding API 璋冪敤澶辫触: {e}")
                self._mark_error(e)

        if settings.EMBEDDING_LOCAL_ENABLED and not self._using_local:
            try:
                if not self._local_model.available:
                    await asyncio.wait_for(asyncio.to_thread(self._local_model.initialize), timeout=8)
                result = await asyncio.to_thread(self._local_model.encode, [text])
                if result and len(result) > 0:
                    embedding = result[0]
                    self._cache_put(cache_key, embedding)
                    self._mark_success()
                    return embedding
            except Exception as e:
                logger.error(f"鏈湴宓屽叆妯″瀷 fallback 璋冪敤澶辫触: {e}")
                self._mark_error(e)

        return self._hash_embedding(text)

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        results = []
        uncached_indices = []
        uncached_texts = []

        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)
            if cache_key in self._cache:
                results.append((i, self._cache[cache_key]))
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if not uncached_texts:
            results.sort(key=lambda x: x[0])
            return [r[1] for r in results]

        if self._using_local and self._local_model.available:
            try:
                local_results = await asyncio.to_thread(self._local_model.encode, uncached_texts)
                if local_results:
                    for idx, emb in zip(uncached_indices, local_results):
                        cache_key = self._get_cache_key(texts[idx])
                        self._cache_put(cache_key, emb)
                        results.append((idx, emb))
                    results.sort(key=lambda x: x[0])
                    self._mark_success()
                    return [r[1] for r in results]
            except Exception as e:
                logger.error(f"鏈湴宓屽叆妯″瀷鎵归噺璋冪敤澶辫触: {e}")
                self._mark_error(e)

        if uncached_texts and self._api_available():
            try:
                batch_embeddings = await self._call_embedding_api_batch(uncached_texts)
                for idx, emb in zip(uncached_indices, batch_embeddings):
                    cache_key = self._get_cache_key(texts[idx])
                    self._cache_put(cache_key, emb)
                    results.append((idx, emb))
                self._mark_success()
            except Exception as e:
                logger.error(f"鎵归噺 Embedding API 璋冪敤澶辫触: {e}")
                self._mark_error(e)
                if settings.EMBEDDING_LOCAL_ENABLED and not self._using_local:
                    try:
                        if not self._local_model.available:
                            await asyncio.wait_for(asyncio.to_thread(self._local_model.initialize), timeout=8)
                        local_results = await asyncio.to_thread(self._local_model.encode, uncached_texts)
                        if local_results:
                            for idx, emb in zip(uncached_indices, local_results):
                                cache_key = self._get_cache_key(texts[idx])
                                self._cache_put(cache_key, emb)
                                results.append((idx, emb))
                            results.sort(key=lambda x: x[0])
                            self._mark_success()
                            return [r[1] for r in results]
                    except Exception as le:
                        logger.error(f"鏈湴宓屽叆妯″瀷鎵归噺 fallback 涔熷け璐? {le}")
                        self._mark_error(le)
                for idx in uncached_indices:
                    if idx not in [r[0] for r in results]:
                        results.append((idx, self._hash_embedding(texts[idx])))
        else:
            if settings.EMBEDDING_LOCAL_ENABLED and not self._using_local:
                try:
                    if not self._local_model.available:
                        await asyncio.wait_for(asyncio.to_thread(self._local_model.initialize), timeout=8)
                    local_results = await asyncio.to_thread(self._local_model.encode, uncached_texts)
                    if local_results:
                        for idx, emb in zip(uncached_indices, local_results):
                            cache_key = self._get_cache_key(texts[idx])
                            self._cache_put(cache_key, emb)
                            results.append((idx, emb))
                        results.sort(key=lambda x: x[0])
                        self._mark_success()
                        return [r[1] for r in results]
                except Exception as e:
                    logger.error(f"鏈湴宓屽叆妯″瀷鎵归噺璋冪敤澶辫触: {e}")
                    self._mark_error(e)
            for idx in uncached_indices:
                if idx not in [r[0] for r in results]:
                    results.append((idx, self._hash_embedding(texts[idx])))

        results.sort(key=lambda x: x[0])
        return [r[1] for r in results]

    async def _call_embedding_api(self, text: str) -> Optional[List[float]]:
        if self._gemini_api_key:
            return await self._call_gemini_embedding(text, task_type="RETRIEVAL_QUERY")
        def _call():
            return self._client.embeddings.create(
                input=text,
                model=self._model,
                encoding_format="float",
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=30)
        if response.data and len(response.data) > 0:
            embedding = response.data[0].embedding
            if len(embedding) != self._dimension:
                logger.warning(f"Embedding 缁村害涓嶅尮閰? 鏈熸湜={self._dimension}, 瀹為檯={len(embedding)}")
                self._dimension = len(embedding)
            return embedding
        return None

    async def _call_embedding_api_batch(self, texts: List[str]) -> List[List[float]]:
        if self._gemini_api_key:
            return await self._call_gemini_embedding_batch(texts)
        batch_size = 16
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            def _call(b=batch):
                return self._client.embeddings.create(
                    input=b,
                    model=self._model,
                    encoding_format="float",
                )

            response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=60)
            if response.data:
                sorted_data = sorted(response.data, key=lambda x: x.index)
                for item in sorted_data:
                    all_embeddings.append(item.embedding)
            else:
                all_embeddings.extend([self._hash_embedding(t) for t in batch])

        return all_embeddings

    async def _call_gemini_embedding(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> Optional[List[float]]:
        """Call Gemini's native embedContent endpoint (not OpenAI compatible)."""
        import httpx
        model = self._gemini_model or settings.GEMINI_EMBEDDING_MODEL
        model_path = model if model.startswith("models/") else f"models/{model}"
        endpoint = f"{self._gemini_base_url.rstrip('/')}/{model_path}:embedContent"
        payload = {
            "model": model_path,
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
            "outputDimensionality": int(self._dimension),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(endpoint, params={"key": self._gemini_api_key}, json=payload)
            response.raise_for_status()
            body = response.json()
        values = ((body.get("embedding") or {}).get("values") if isinstance(body, dict) else None) or []
        if not values:
            return None
        if len(values) != self._dimension:
            self._dimension = len(values)
        vector = np.asarray([float(v) for v in values], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = vector / norm
        return vector.tolist()

    async def _call_gemini_embedding_batch(self, texts: List[str]) -> List[List[float]]:
        import httpx
        model = self._gemini_model or settings.GEMINI_EMBEDDING_MODEL
        model_path = model if model.startswith("models/") else f"models/{model}"
        endpoint = f"{self._gemini_base_url.rstrip('/')}/{model_path}:batchEmbedContents"
        requests = []
        for text in texts:
            requests.append({
                "model": model_path,
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_DOCUMENT",
                "outputDimensionality": int(self._dimension),
            })
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(endpoint, params={"key": self._gemini_api_key}, json={"requests": requests})
            response.raise_for_status()
            body = response.json()
        embeddings = body.get("embeddings") if isinstance(body, dict) else None
        result = []
        for item in (embeddings or []):
            vector = np.asarray(list(map(float, (item or {}).get("values") or [])), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if norm > 0:
                vector = vector / norm
            result.append(vector.tolist())
        if result and result[0]:
            self._dimension = len(result[0])
        return result

    async def validate_embedding_quality(self, text: str) -> dict:
        embedding = await self.embed_text(text)

        vec = np.array(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        mean_val = float(np.mean(vec))
        std_val = float(np.std(vec))

        is_valid = norm > 0.1 and std_val > 0.01

        return {
            "is_valid": is_valid,
            "dimension": len(embedding),
            "norm": round(norm, 4),
            "mean": round(mean_val, 6),
            "std": round(std_val, 6),
            "using_api": self._client is not None or bool(self._gemini_api_key),
            "using_local_model": self._local_model.available,
            "prefer_local": self._using_local,
            "model": self._model if self._client else self._local_model._model_name,
        }


embedding_service = EmbeddingService()
