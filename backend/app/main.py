import os
import sys
import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from app.core.config import settings
from app.api.routes import router, resume_pending_agent_tasks
from app.api.wiki_routes import wiki_router
from app.api.passive_routes import router as passive_router
from app.api.telemetry_routes import router as telemetry_router
from app.passive.service import passive_service
from app.core.auth import decode_token, verify_api_key


class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_entry["data"] = record.extra_data
        return json.dumps(log_entry, ensure_ascii=False)


class DevelopmentFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        fmt = f"{color}%(asctime)s [%(levelname)s] %(name)s: %(message)s{self.RESET}"
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


def setup_logging():
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if settings.APP_ENV in ("production", "staging"):
        handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    else:
        handler.setFormatter(DevelopmentFormatter())

    root_logger.addHandler(handler)


setup_logging()
logger = logging.getLogger(__name__)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    logger.info(f"租户ID: {settings.TENANT_ID}")
    logger.info(f"数据目录: {settings.DATA_DIR}")
    logger.info(f"前端目录: {FRONTEND_DIR}")
    await passive_service.start_scheduler()
    resumed = await resume_pending_agent_tasks()
    if resumed:
        logger.info("已恢复 %s 个未完成 Agent 任务", resumed)
    yield
    await passive_service.stop_scheduler()
    logger.info(f"{settings.APP_NAME} 关闭中...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="知识图谱 + RAG 知识库整合系统",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    contact={"name": "Knowledge Hub Team"},
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


READ_ONLY_POST_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/guest",
    "/api/v1/auth/refresh",
    "/api/v1/documents/analyze-layout",
    "/api/v1/documents/convert",
    "/api/v1/file-resources/query",
    "/api/v1/query",
    "/api/v1/rag/query",
    "/api/v1/rag/query/stream",
    "/api/v1/rag/rewrite-query",
    "/api/v1/rag/web-search",
    "/api/v1/rag/session/create",
    "/api/v1/knowledge-graph/query",
    "/api/v1/knowledge-graph/rewrite-query",
    "/api/v1/kg/rewrite-query",
    "/api/v1/evaluate",
    "/api/v1/wiki/share/access",
    "/api/v1/wiki/versions/diff",
    "/api/v1/wiki/versions/text-diff",
    "/api/v1/wiki/embeds/resolve",
    "/api/v1/wiki/api-keys/validate",
    "/api/v1/wiki/ai/copilot",
    "/api/v1/telemetry/events",
}
READ_ONLY_POST_PREFIXES = (
    "/api/v1/wiki/search/",
    "/api/v1/wiki/ai/summarize/",
    "/api/v1/wiki/ai/chapter-summary/",
    "/api/v1/wiki/ai/mindmap/",
    "/api/v1/wiki/ai/explain-terms/",
)


def _requires_write_access(request: Request) -> bool:
    method = request.method.upper()
    path = request.url.path.rstrip("/") or "/"
    if not path.startswith("/api/v1/") or method in {"GET", "HEAD", "OPTIONS"}:
        return False
    if method in {"PUT", "PATCH", "DELETE"}:
        return True
    if method != "POST":
        return False
    if path in READ_ONLY_POST_PATHS or path.startswith(READ_ONLY_POST_PREFIXES):
        return False
    return True


@app.middleware("http")
async def enforce_write_access(request: Request, call_next):
    if settings.ACCESS_CONTROL_ENABLED and _requires_write_access(request):
        authorization = request.headers.get("authorization", "")
        payload = None
        if authorization.lower().startswith("bearer "):
            try:
                payload = decode_token(authorization.split(" ", 1)[1].strip())
            except Exception:
                payload = None
            if not payload:
                return JSONResponse(status_code=401, content={"detail": "登录已失效，请重新登录"})
            if payload.get("type") != "access":
                return JSONResponse(status_code=401, content={"detail": "登录凭证无效"})
            if "write" not in (payload.get("permissions") or []):
                return JSONResponse(status_code=403, content={"detail": "游客模式仅支持阅读和检索"})
        else:
            raw_key = request.headers.get("x-api-key", "")
            key_info = verify_api_key(raw_key) if raw_key else None
            if not key_info or "write" not in (key_info.get("permissions") or []):
                return JSONResponse(status_code=401, content={"detail": "请先登录后再执行此操作"})
            payload = {"sub": key_info.get("name", "api-key"), "permissions": key_info.get("permissions", [])}
        request.state.user = payload
    return await call_next(request)

# 生产环境安全检查
if settings.APP_ENV == "production":
    if settings.CORS_ORIGINS == ["*"]:
        logger.warning("生产环境 CORS_ORIGINS 为 [*]，建议设置为具体域名！")
    if settings.SECRET_KEY == "knowledge-hub-secret-key-change-in-production":
        logger.warning("生产环境 SECRET_KEY 未修改，请设置强密钥！")

# HTTPS 安全头中间件
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html", "/sw.js", "/manifest.json"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.startswith(("/js/", "/css/", "/assets/", "/models/")):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # 仅在 HTTPS 时添加 HSTS
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Allow same-origin previews such as embedded PDF viewers.
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

app.include_router(router, prefix="/api/v1")
app.include_router(wiki_router, prefix="/api/v1")
app.include_router(passive_router, prefix="/api/v1")
app.include_router(telemetry_router, prefix="/api/v1")

# O13: 集成请求监控中间件
from app.core.monitoring import RequestMetricsMiddleware, metrics
app.add_middleware(RequestMetricsMiddleware)


@app.get("/api/v1/info")
async def app_info():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "tenant_id": settings.TENANT_ID,
        "modules": ["knowledge_graph", "rag", "document_processor", "wiki"],
    }


# O13: Prometheus 指标端点
@app.get("/metrics")
async def prometheus_metrics():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics.get_metrics_text(), media_type="text/plain")


HF_MIRROR_BASE = "https://hf-mirror.com/"


@app.get("/hf-proxy/{path:path}")
async def hf_proxy(path: str, request: Request):
    target_url = f"{HF_MIRROR_BASE}{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"
    local_models_dir = os.path.join(FRONTEND_DIR, "models")
    local_path = path
    resolve_prefix_pattern = None
    for prefix_candidate in path.split("/"):
        if prefix_candidate == "resolve":
            idx = path.index("/resolve/")
            local_path = path[:idx] + path[path.index("/", idx + 9):]
            break
    local_file_path = os.path.join(local_models_dir, local_path)
    if os.path.isfile(local_file_path):
        return FileResponse(local_file_path)
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(target_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            if resp.status_code != 200:
                return {"error": f"Upstream returned {resp.status_code}"}, resp.status_code
            content_type = resp.headers.get("content-type", "application/octet-stream")
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    except Exception as e:
        logger.error(f"HF proxy error for {path}: {e}")
        return {"error": str(e)}, 502


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")
    assets_dir = os.path.join(FRONTEND_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    models_dir = os.path.join(FRONTEND_DIR, "models")
    if os.path.isdir(models_dir):
        app.mount("/models", StaticFiles(directory=models_dir), name="models")


@app.get("/")
async def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION, "status": "running"}


@app.get("/manifest.json")
async def serve_manifest():
    manifest_path = os.path.join(FRONTEND_DIR, "manifest.json")
    if os.path.exists(manifest_path):
        return FileResponse(manifest_path, media_type="application/manifest+json")
    return JSONResponse(status_code=404, content={"detail": "manifest not found"})


@app.get("/{page_name}")
async def serve_page(page_name: str):
    page_path = os.path.join(FRONTEND_DIR, f"{page_name}.html")
    if os.path.exists(page_path):
        return FileResponse(page_path)
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
