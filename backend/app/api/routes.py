import os
import uuid
import logging
import re
import asyncio
import hashlib
import shutil
import json
import mimetypes
import tempfile
import zipfile
import secrets
import hmac
import urllib.parse
import httpx
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends, BackgroundTasks, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
import time
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.auth import get_current_user, require_write_permission
from app.core.path_security import sanitize_filename as secure_sanitize_filename, validate_path as secure_validate_path
from app.doc_processor import file_converter, doc_processor, SUPPORTED_FORMATS
from app.doc_processor.processor import SmartChunker
from app.doc_processor.layout_chunker import layout_doc_processor
from app.kg import kg_store, kg_builder, kg_rag_service
from app.rag import rag_service, vector_store
from app.rag.embedding_service import embedding_service
from app.rag.bm25_search import hybrid_search_engine
from app.rag.kb_manager import kb_manager, CORE_KB_ID
from app.rag.evaluator import rag_evaluator
from app.agent.orchestrator import agent_orchestrator
from app.mcp import wechat_mcp
from app.mcp.wechat_content import prepare_article
from app.telemetry.recorder import telemetry
from app.services.knowledge_review import knowledge_review_service

logger = logging.getLogger(__name__)
router = APIRouter()
_kb_ingest_semaphore = asyncio.Semaphore(1)
_kb_mutation_lock = asyncio.Lock()


class ErrorCode:
    INVALID_PARAM = ("INVALID_PARAM", 400)
    FILE_NOT_FOUND = ("FILE_NOT_FOUND", 404)
    KB_NOT_FOUND = ("KB_NOT_FOUND", 404)
    NODE_NOT_FOUND = ("NODE_NOT_FOUND", 404)
    UNSUPPORTED_FORMAT = ("UNSUPPORTED_FORMAT", 400)
    FILE_TOO_LARGE = ("FILE_TOO_LARGE", 400)
    PATH_TRAVERSAL = ("PATH_TRAVERSAL", 400)
    UPLOAD_FAILED = ("UPLOAD_FAILED", 500)
    PROCESS_FAILED = ("PROCESS_FAILED", 500)
    QUERY_FAILED = ("QUERY_FAILED", 500)
    KG_BUILD_FAILED = ("KG_BUILD_FAILED", 500)
    CONVERSION_FAILED = ("CONVERSION_FAILED", 400)
    EMBEDDING_FAILED = ("EMBEDDING_FAILED", 503)
    LLM_UNAVAILABLE = ("LLM_UNAVAILABLE", 503)


def error_response(code_tuple: tuple, detail: str) -> HTTPException:
    code, status = code_tuple
    return HTTPException(status_code=status, detail={"code": code, "message": detail})


class VectorizeDocumentsRequest(BaseModel):
    doc_ids: Optional[List[str]] = None
    force: bool = False


class FileResourceCaptureRequest(BaseModel):
    url: str
    title: Optional[str] = ""
    content: str
    selection: Optional[str] = ""
    description: Optional[str] = ""
    author_name: Optional[str] = ""
    published_at: Optional[str] = ""
    source_type: Optional[str] = "edge"
    captured_at: Optional[str] = ""
    folder_prefix: Optional[str] = "自动抓取"
    images: List[Dict[str, Any]] = Field(default_factory=list)
    capture_mode: Optional[str] = "full"
    quality_score: Optional[float] = None
    warnings: List[str] = Field(default_factory=list)


_CAPTURE_STRONG_END = re.compile(
    r"^(原创声明|版权声明|加入讨论|发表评论|相关产品与服务|相关课程|问题归档|"
    r"专栏文章归档|快讯文章归档|开发者手册归档|关于社区规范|免责声明|联系我们|"
    r"友情链接|返回腾讯云官网|期待你的精彩评论|\d+条评论)(?:[：:]|$)", re.I,
)
_CAPTURE_EXACT_NOISE = re.compile(
    r"^(上一页|下一页|返回顶部|分享|收藏|点赞|评论|首页|学习|活动|专区|圈层|工具|"
    r"文档|建议反馈|控制台|个人中心|职业认证|通知设置|消息中心|退出登录|关注作者|"
    r"原创|作者相关精选|作者相关|社区首页|专栏|举报|发表|热度|最新|图片|加入讨论|"
    r"期待你的精彩评论|用户\d+|腾讯云TVP|LV\.\d+|CTO|获赞|排名|交个朋友|字数统计|"
    r"导入md|导入markdown|有序列表|无序列表|行内代码|代码块|行内公式|公式|常用符号|"
    r"代数|矩阵|几何|三角|颜色|字号|文本颜色|背景颜色|根式|上下标|极限|对数指数|"
    r"三角函数|双曲函数|Binary operations|Binary relations|Arrows|Others|Radicals|"
    r"Sub(?:&|and)Super|Limits|Trigonometric function|Hyperbolic function|H[1-6])$",
    re.I,
)


def _tiptap_json_to_markdown(value: Any) -> str:
    """Convert common Tiptap/ProseMirror nodes to readable Markdown."""
    if not isinstance(value, dict):
        return ""

    def inline(nodes: Any) -> str:
        if not isinstance(nodes, list):
            return ""
        parts: List[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = str(node.get("type") or "").lower()
            if node_type == "text":
                text = str(node.get("text") or "")
                for mark in node.get("marks") or []:
                    mark_type = str((mark or {}).get("type") or "").lower()
                    if mark_type in {"bold", "strong"}:
                        text = f"**{text}**"
                    elif mark_type in {"italic", "em"}:
                        text = f"*{text}*"
                    elif mark_type in {"strike", "strikethrough"}:
                        text = f"~~{text}~~"
                    elif mark_type == "code":
                        text = f"`{text}`"
                    elif mark_type == "link" and (mark.get("attrs") or {}).get("href"):
                        text = f"[{text}]({mark['attrs']['href']})"
                parts.append(text)
            elif node_type == "hardbreak":
                parts.append("\n")
            elif node_type == "image":
                src = str((node.get("attrs") or {}).get("src") or "")
                alt = str((node.get("attrs") or {}).get("alt") or "图片")
                if src:
                    parts.append(f"![{alt}]({src})")
            else:
                parts.append(inline(node.get("content")))
        return "".join(parts)

    def render(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        node_type = str(node.get("type") or "").lower()
        children = node.get("content") or []
        if node_type == "doc":
            return "\n\n".join(filter(None, (render(child) for child in children)))
        if node_type == "paragraph":
            return inline(children).strip()
        if node_type == "heading":
            level = max(1, min(6, int((node.get("attrs") or {}).get("level") or 1)))
            return f"{'#' * level} {inline(children).strip()}".strip()
        if node_type in {"bulletlist", "orderedlist"}:
            ordered = node_type == "orderedlist"
            start = int((node.get("attrs") or {}).get("start") or 1)
            rows = []
            for index, child in enumerate(children):
                rendered = render(child)
                if rendered:
                    marker = f"{start + index}. " if ordered else "- "
                    rows.append(marker + rendered.replace("\n", "\n  "))
            return "\n".join(rows)
        if node_type == "listitem":
            return "\n\n".join(filter(None, (render(child) for child in children)))
        if node_type == "blockquote":
            body = "\n\n".join(filter(None, (render(child) for child in children)))
            return "\n".join(f"> {line}" for line in body.splitlines())
        if node_type in {"codeblock", "code-block"}:
            language = str((node.get("attrs") or {}).get("language") or "")
            return f"```{language}\n{inline(children)}\n```"
        if node_type == "image":
            attrs = node.get("attrs") or {}
            src = str(attrs.get("src") or "")
            return f"![{attrs.get('alt') or '图片'}]({src})" if src else ""
        if node_type == "table":
            rows = []
            for row in children:
                if str((row or {}).get("type") or "").lower() != "tablerow":
                    continue
                cells = []
                for cell in row.get("content") or []:
                    cell_text = inline([part for block in (cell.get("content") or []) for part in (block.get("content") or [block])])
                    cells.append(cell_text.replace("|", "\\|").replace("\n", " ").strip())
                if cells:
                    rows.append(cells)
            if not rows:
                return ""
            width = max(len(row) for row in rows)
            normalized = [row + [""] * (width - len(row)) for row in rows]
            return "\n".join([
                "| " + " | ".join(normalized[0]) + " |",
                "| " + " | ".join("---" for _ in normalized[0]) + " |",
                *["| " + " | ".join(row) + " |" for row in normalized[1:]],
            ])
        return "\n\n".join(filter(None, (render(child) for child in children))) or inline(children)

    return render(value).strip()


def _sanitize_captured_markdown(content: str, title: str = "") -> str:
    """防止旧版插件或复杂站点把页面壳层写入文件资源库。

    浏览器端负责 DOM 级清洗，这里是保存前的轻量兜底：只删除明确的
    编辑器/评论/页脚标记和完全为空的 Markdown 表格，不对普通正文做
    大范围关键词替换，避免误删文章内容。
    """
    source = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    if source.lstrip().startswith("{"):
        try:
            structured = json.loads(source)
            converted = _tiptap_json_to_markdown(structured)
            if converted:
                source = converted
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    lines = [line.strip() for line in source.split("\n") if line.strip()]
    if not lines:
        return ""

    def norm(value: str) -> str:
        return re.sub(r"[\s|·•]+", "", re.sub(r"^\s*#{1,6}\s*", "", value or "")).strip("：:").lower()

    title_key = norm(title)
    start = 0
    if len(title_key) >= 8:
        matches = [
            index for index, line in enumerate(lines)
            if norm(line) == title_key
            or (len(norm(line)) >= 12 and title_key in norm(line))
            or (len(title_key) >= 12 and norm(line) in title_key)
        ]
        heading_matches = [index for index in matches if re.match(r"^#{1,6}\s+", lines[index])]
        if heading_matches:
            start = heading_matches[0]
        elif matches:
            start = matches[-1]

    trimmed = []
    editor_terms = (
        "h1", "h2", "h3", "h4", "h5", "h6", "有序列表", "无序列表", "行内代码", "代码块",
        "行内公式", "公式", "导入md", "导入markdown", "常用符号", "二元计算符", "二元关系符",
        "箭头符号", "根式", "上下标", "极限", "对数指数", "三角函数", "双曲函数",
        "binaryoperations", "binaryrelations", "trigonometricfunction", "hyperbolicfunction",
    )
    for index, line in enumerate(lines[start:], start=start):
        if index >= start + 4 and _CAPTURE_STRONG_END.match(line):
            break
        # A row made only of pipes/dashes is the editor's empty table widget.
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not any(cell and not re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue
        if _CAPTURE_EXACT_NOISE.match(line):
            continue
        compact = re.sub(r"[\s·•|]+", "", line).lower()
        if sum(1 for term in editor_terms if term in compact) >= 3 and len(line) <= 260:
            continue
        if re.fullmatch(r"\d{1,6}", line) or re.match(r"^发布于\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}", line, re.I):
            continue
        if trimmed and len(line) <= 80 and len(trimmed[-1]) <= 80 and norm(line) == norm(trimmed[-1]):
            continue
        trimmed.append(line)

    if trimmed and title_key and re.match(r"^#{1,6}\s+", trimmed[0]):
        first_key = norm(trimmed[0])
        if first_key == title_key or (len(first_key) >= 12 and title_key in first_key) or (len(title_key) >= 12 and first_key in title_key):
            trimmed.pop(0)
    return "\n\n".join(trimmed).strip()


def _build_doc_vectorization_status(doc: Dict[str, Any], indexed_chunk_count: int) -> Dict[str, Any]:
    expected = int(doc.get("chunk_count") or 0)
    indexed = max(0, int(indexed_chunk_count or 0))
    if expected <= 0:
        status = "无分块"
        status_key = "empty"
        progress = 0
        needs_vectorization = False
    elif indexed <= 0:
        status = "未入库"
        status_key = "missing"
        progress = 0
        needs_vectorization = True
    elif indexed < expected:
        status = "部分入库"
        status_key = "partial"
        progress = round(indexed / expected * 100, 1)
        needs_vectorization = True
    else:
        status = "已入库"
        status_key = "complete"
        progress = 100
        needs_vectorization = False
    return {
        "vectorized_chunk_count": indexed,
        "expected_chunk_count": expected,
        "embedding_status": status,
        "embedding_status_key": status_key,
        "embedding_progress": progress,
        "needs_vectorization": needs_vectorization,
    }


def sanitize_filename(filename: str) -> str:
    """Sanitize uploaded file names."""
    try:
        return secure_sanitize_filename(filename)
    except ValueError as e:
        raise error_response(ErrorCode.INVALID_PARAM, str(e))


def validate_file_path(base_dir: str, filename: str) -> str:
    """Validate that a file path stays inside the allowed base directory."""
    try:
        return secure_validate_path(base_dir, filename, check_extension=False)
    except ValueError as e:
        raise error_response(ErrorCode.PATH_TRAVERSAL, str(e))


FILE_RESOURCE_DIR = os.path.join(settings.DATA_DIR, "file_resources")
FILE_RESOURCE_ORDER_FILE = os.path.join(settings.DATA_DIR, "file_resource_order.json")


def _ensure_file_resource_dir() -> str:
    os.makedirs(FILE_RESOURCE_DIR, exist_ok=True)
    return FILE_RESOURCE_DIR


def _normalize_resource_path(raw_path: str) -> str:
    path = (raw_path or "").replace("\\", "/").strip().strip("/")
    if not path:
        raise error_response(ErrorCode.INVALID_PARAM, "path is required")
    parts = []
    for part in path.split("/"):
        if not part or part in {".", ".."}:
            raise error_response(ErrorCode.PATH_TRAVERSAL, f"invalid path: {raw_path}")
        parts.append(sanitize_filename(part))
    return "/".join(parts)


def _resource_abs_path(raw_path: str) -> str:
    base = Path(_ensure_file_resource_dir()).resolve()
    safe_rel = _normalize_resource_path(raw_path)
    target = (base / safe_rel).resolve()
    if target != base and base not in target.parents:
        raise error_response(ErrorCode.PATH_TRAVERSAL, f"invalid path: {raw_path}")
    return str(target)


def _load_file_resource_order() -> dict:
    try:
        if os.path.exists(FILE_RESOURCE_ORDER_FILE):
            with open(FILE_RESOURCE_ORDER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"load file resource order failed: {e}")
    return {}


def _save_file_resource_order(order_map: dict) -> None:
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    tmp_path = FILE_RESOURCE_ORDER_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(order_map, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, FILE_RESOURCE_ORDER_FILE)


def _remap_order_path(path: str, old_prefix: str, new_prefix: str) -> str:
    if path == old_prefix:
        return new_prefix
    if path.startswith(old_prefix + "/"):
        return new_prefix + path[len(old_prefix):]
    return path


def _remap_file_resource_order(old_prefix: str, new_prefix: str) -> None:
    order_map = _load_file_resource_order()
    if not order_map:
        return
    next_order = {}
    for parent, items in order_map.items():
        next_parent = _remap_order_path(parent, old_prefix, new_prefix)
        next_items = [_remap_order_path(item, old_prefix, new_prefix) for item in items if isinstance(item, str)]
        next_order[next_parent] = next_items
    _save_file_resource_order(next_order)


def _prune_file_resource_order(prefix: str) -> None:
    order_map = _load_file_resource_order()
    if not order_map:
        return
    next_order = {}
    for parent, items in order_map.items():
        if parent == prefix or parent.startswith(prefix + "/"):
            continue
        next_order[parent] = [
            item for item in items
            if isinstance(item, str) and item != prefix and not item.startswith(prefix + "/")
        ]
    _save_file_resource_order(next_order)


def _build_file_resource_tree(base_dir: str) -> List[dict]:
    base = Path(base_dir)
    if not base.exists():
        return []
    order_map = _load_file_resource_order()

    def sort_children(parent_rel: str, children: List[Path]) -> List[Path]:
        ordered = order_map.get(parent_rel, [])
        order_index = {
            rel: idx
            for idx, rel in enumerate(ordered)
            if isinstance(rel, str)
        }

        def key(path: Path):
            rel = path.relative_to(base).as_posix()
            if rel in order_index:
                return (0, order_index[rel])
            return (1, not path.is_dir(), path.name.lower())

        return sorted(children, key=key)

    def build_entry(path: Path) -> dict:
        rel = path.relative_to(base).as_posix()
        if path.is_dir():
            children = [
                build_entry(child)
                for child in sort_children(rel, list(path.iterdir()))
            ]
            return {
                "name": path.name,
                "path": rel,
                "isDirectory": True,
                "children": children,
                "source": "server",
            }
        return {
            "name": path.name,
            "path": rel,
            "isDirectory": False,
            "size": path.stat().st_size,
            "updated_at": path.stat().st_mtime,
            "source": "server",
        }

    return [
        build_entry(child)
        for child in sort_children("", list(base.iterdir()))
    ]


class QueryRequest(BaseModel):
    question: str
    mode: Optional[str] = "rag"


class FileResourceCreateRequest(BaseModel):
    path: str
    content: Optional[str] = ""
    is_directory: Optional[bool] = False


class FileResourceUpdateRequest(BaseModel):
    content: str


class FileResourceMoveOperation(BaseModel):
    source_path: str
    target_path: str


class FileResourceMoveRequest(BaseModel):
    operations: List[FileResourceMoveOperation]


class FileResourceReorderRequest(BaseModel):
    parent_path: Optional[str] = ""
    ordered_paths: List[str]


class FileResourceQueryRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5


class RAGQueryRequest(BaseModel):
    query: str
    mode: Optional[str] = "hybrid"
    top_k: Optional[int] = 5
    kb_id: Optional[str] = ""
    kb_ids: Optional[List[str]] = None  # P5: 鏀寔澶氱煡璇嗗簱鑱斿悎妫€绱?
    enable_web_search: Optional[bool] = False
    session_id: Optional[str] = ""


class AgentTaskRequest(BaseModel):
    goal: str
    template: Optional[str] = "auto"
    output_format: Optional[str] = "markdown"
    kb_id: Optional[str] = ""
    kb_ids: Optional[List[str]] = None
    top_k: Optional[int] = 8
    date_range: Optional[dict] = None
    web_search_enabled: Optional[bool] = False
    scope: Optional[List[str]] = None
    selected_paths: Optional[List[str]] = None
    approval_mode: Optional[str] = "manual"
    output_options: Optional[dict] = None
    session_id: Optional[str] = ""


class AgentDraftUpdateRequest(BaseModel):
    content: str


class AgentFeedbackRequest(BaseModel):
    rating: str = "negative"
    reason: Optional[str] = ""
    comment: Optional[str] = ""
    selected_evidence: Optional[List[str]] = None


class WeChatPublishRequest(BaseModel):
    confirm: bool = False
    media_id: Optional[str] = ""
    approval_token: Optional[str] = ""


class WeChatDraftPrepareRequest(BaseModel):
    title: Optional[str] = ""
    digest: Optional[str] = ""
    cover_media_id: Optional[str] = ""


class WeChatDraftSyncRequest(WeChatDraftPrepareRequest):
    idempotency_key: Optional[str] = ""


class WeChatConfigRequest(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    default_cover_media_id: str = ""


class WeChatApprovalRequest(BaseModel):
    media_id: Optional[str] = ""


class KGQueryRequest(BaseModel):
    query: str


class KGBuildRequest(BaseModel):
    text: str
    title: Optional[str] = ""
    struct_tags: Optional[str] = ""
    doc_key: Optional[str] = ""


class KGNodeCreate(BaseModel):
    label: str
    node_type: str
    properties: Optional[dict] = None


class KGEdgeCreate(BaseModel):
    source_id: str
    target_id: str
    relation_type: str
    weight: Optional[float] = 1.0
    properties: Optional[dict] = None


class RewriteQueryRequest(BaseModel):
    query: str
    session_id: Optional[str] = ""


class ConvertFileRequest(BaseModel):
    filename: str


class KBCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    chunk_strategy: Optional[str] = "auto"
    chunk_size: Optional[int] = 0
    chunk_overlap: Optional[int] = 0


class EvalRequest(BaseModel):
    question: str
    answer: str
    contexts: List[str] = []
    ground_truth: Optional[str] = ""


class KBUploadRequest(BaseModel):
    kb_id: str
    chunk_strategy: Optional[str] = ""
    chunk_size: Optional[int] = 0
    chunk_overlap: Optional[int] = 0


class KBFileResourceImportRequest(BaseModel):
    paths: List[str]
    chunk_strategy: Optional[str] = ""
    chunk_size: Optional[int] = 0
    chunk_overlap: Optional[int] = 0


def _detect_chunk_metadata(chunks) -> dict:
    if not chunks:
        return {"chunk_strategy": "", "detected_doc_type": ""}
    meta = chunks[0].metadata or {}
    strategy = meta.get("chunk_strategy", "")
    detected_doc_type = meta.get("detected_doc_type") or meta.get("document_type") or strategy
    return {"chunk_strategy": strategy, "detected_doc_type": detected_doc_type}


LAYOUT_FRIENDLY_EXTS = {".pdf", ".docx", ".doc", ".pptx"}


def _should_use_layout_processor(strategy: str, filename: str) -> bool:
    if not settings.LAYOUT_ANALYSIS_ENABLED:
        return False
    normalized_strategy = (strategy or "").strip().lower()
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in LAYOUT_FRIENDLY_EXTS and normalized_strategy in {"layout_aware", "auto"}


def _effective_text_strategy(strategy: str, filename: str) -> str:
    normalized_strategy = (strategy or "").strip().lower()
    ext = os.path.splitext(filename or "")[1].lower()
    if normalized_strategy == "layout_aware" and ext not in LAYOUT_FRIENDLY_EXTS:
        return "auto"
    return strategy or "auto"


async def _ingest_kb_document_bytes(
    kb_id: str,
    filename: str,
    content: bytes,
    chunk_strategy: str = "",
    chunk_size: int = 0,
    chunk_overlap: int = 0,
    persist_indexes: bool = True,
    source_path: str = "",
) -> Dict[str, Any]:
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "知识库不存在")

    if not filename:
        raise error_response(ErrorCode.INVALID_PARAM, "未提供文件名")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise error_response(ErrorCode.UNSUPPORTED_FORMAT, f"不支持的格式: {ext}")

    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "文件过大")

    file_hash = hashlib.sha256(content).hexdigest()
    duplicate_doc = kb_manager.find_duplicate_document(kb_id, content_hash=file_hash, filename=filename)
    if duplicate_doc:
        duplicate_doc_id = duplicate_doc.get("doc_id", "")
        expected_chunks = int(duplicate_doc.get("chunk_count") or 0)
        indexed_chunks = len(vector_store.get_chunks_by_doc(duplicate_doc_id)) if duplicate_doc_id else 0
        if expected_chunks > 0 and indexed_chunks < expected_chunks:
            logger.warning(
                "Recovering incomplete KB document %s: expected %s chunks, found %s",
                duplicate_doc_id,
                expected_chunks,
                indexed_chunks,
            )
            await asyncio.to_thread(vector_store.delete_by_doc, duplicate_doc_id)
            await asyncio.to_thread(hybrid_search_engine.delete_by_doc, duplicate_doc_id)
            await asyncio.to_thread(kb_manager.delete_document, kb_id, duplicate_doc_id)
            await asyncio.to_thread(kb_manager.update_counts, kb_id, -1, -expected_chunks)
            duplicate_doc = None
    if duplicate_doc:
        reason = duplicate_doc.get("duplicate_reason")
        duplicate_name = duplicate_doc.get("original_filename") or duplicate_doc.get("filename") or filename
        if reason == "content_hash":
            return {
                "success": False,
                "duplicate": True,
                "doc_id": duplicate_doc.get("doc_id", ""),
                "filename": duplicate_name,
                "message": f"文档 '{filename}' 已存在，已跳过重复上传",
            }
        return {
            "success": False,
            "conflict": True,
            "doc_id": duplicate_doc.get("doc_id", ""),
            "filename": duplicate_name,
            "message": f"知识库中已存在同名文档 '{filename}'，请先删除旧文档或重命名后再上传",
        }

    strategy = chunk_strategy or kb.chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
    size = chunk_size or kb.chunk_size or settings.CHUNK_SIZE
    overlap = chunk_overlap or kb.chunk_overlap or settings.CHUNK_OVERLAP

    try:
        async with _kb_ingest_semaphore:
            use_layout_processor = _should_use_layout_processor(strategy, filename)
            if use_layout_processor:
                doc = await asyncio.to_thread(
                    layout_doc_processor.process_upload,
                    content, filename,
                    chunk_strategy=strategy,
                    chunk_size=size,
                    chunk_overlap=overlap,
                    kb_id=kb_id,
                )
            else:
                text_strategy = _effective_text_strategy(strategy, filename)
                doc = await asyncio.to_thread(
                    doc_processor.process_upload,
                    content, filename,
                    chunk_strategy=text_strategy,
                    chunk_size=size,
                    chunk_overlap=overlap,
                    kb_id=kb_id,
                )

        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in doc.chunks
        ]
        if source_path:
            for chunk in chunks_data:
                chunk.setdefault("metadata", {})["source_path"] = source_path.replace("\\", "/").strip("/")
        if chunks_data:
            await vector_store.add_documents_async(chunks_data, persist=persist_indexes)
            await asyncio.to_thread(
                hybrid_search_engine.add_documents,
                chunks_data,
                persist_indexes,
            )

        await asyncio.to_thread(kb_manager.update_counts, kb_id, 1, len(doc.chunks))

        chunk_meta = _detect_chunk_metadata(doc.chunks)
        document_saved = await asyncio.to_thread(
            kb_manager.save_document,
            kb_id, doc.doc_id, doc.content,
            title=doc.title or Path(filename).stem,
            filename=filename,
            content_hash=file_hash,
            original_size=len(content),
            chunk_count=len(doc.chunks),
            detected_doc_type=chunk_meta["detected_doc_type"],
            chunk_strategy=chunk_meta["chunk_strategy"] or strategy,
        )
        indexed_count = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)

        if settings.use_supabase:
            from app.core.cloud_storage import cloud_storage
            original_path = f"knowledge_bases/{kb_id}/originals/{doc.doc_id}_{Path(filename).name}"
            await asyncio.to_thread(cloud_storage.upload_file, original_path, content)

        return {
            "success": True,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "chunk_count": len(doc.chunks),
            "detected_doc_type": chunk_meta["detected_doc_type"],
            "chunk_strategy": chunk_meta["chunk_strategy"] or strategy,
            "indexed_chunk_count": indexed_count,
            "kb_id": kb_id,
            "conversion": (getattr(doc, "metadata", {}) or {}).get("conversion", {}),
            "pipeline": {
                "converted": True,
                "cleaned": True,
                "chunked": len(doc.chunks) > 0,
                "embedded": bool(chunks_data),
                "vector_indexed": indexed_count > 0,
                "document_saved": document_saved,
                "storage": "supabase" if settings.use_supabase else "local",
            },
            "message": f"文档 '{filename}' 已上传到知识库 '{kb.name}'，生成 {len(doc.chunks)} 个分块",
        }
    except Exception as e:
        logger.error(f"知识库文档上传失败: {e}")
        raise error_response(ErrorCode.UPLOAD_FAILED, str(e))


@router.get("/health")
async def health_check():
    storage_info = {"mode": "local"}
    if settings.use_supabase:
        storage_info = {
            "mode": "supabase",
            "url": settings.SUPABASE_URL,
            "bucket": settings.SUPABASE_BUCKET,
        }
    vector_status = getattr(vector_store, "backend_status", {}) or {}
    llm_ready = bool(getattr(rag_service, "_glm", None) and settings.OPENAI_API_KEY)
    embedding_ready = not bool(getattr(embedding_service, "is_degraded", False))
    milvus_required = settings.use_milvus
    milvus_ready = bool(vector_status.get("ready", False)) if milvus_required else True
    dependency_status = {
        "llm": {"ready": llm_ready, "model": settings.OPENAI_MODEL},
        "embedding": {"ready": embedding_ready, "model": embedding_service.model,
                       "provider": embedding_service.provider,
                       "degraded": bool(getattr(embedding_service, "is_degraded", False))},
        "vector_store": {"ready": milvus_ready, "backend": vector_status.get("backend", "local"),
                         "error": vector_status.get("error", "") if milvus_required else ""},
    }
    # Keep HTTP 200 for compatibility with existing clients, but accurately
    # expose degraded dependencies instead of claiming the whole service is
    # healthy while silently serving local/hash fallbacks.
    overall_status = "healthy" if llm_ready and embedding_ready and milvus_ready else "degraded"
    return {
        "status": overall_status,
        "ready": overall_status == "healthy",
        "version": settings.APP_VERSION,
        "storage": storage_info,
        "dependencies": dependency_status,
        "modules": {
            "knowledge_graph": {"nodes": getattr(kg_store, 'node_count', 0) or 0, "edges": getattr(kg_store, 'edge_count', 0) or 0},
            "rag": {"documents": vector_store.count},
            "knowledge_bases": len(kb_manager.list_all()),
            "layout_analysis": {
                "enabled": settings.LAYOUT_ANALYSIS_ENABLED,
                "table_extraction": settings.TABLE_STRUCTURED_EXTRACTION,
                "image_extraction": settings.IMAGE_EXTRACTION_ENABLED,
                "ocr_mode": settings.OCR_MODE,
            },
            "hybrid_search": {
                "bm25_stats": hybrid_search_engine.get_stats(),
                "vector_weight": settings.HYBRID_SEARCH_WEIGHT_VECTOR,
                "keyword_weight": settings.HYBRID_SEARCH_WEIGHT_KEYWORD,
            },
            "reranker": {
                "enabled": settings.RERANK_ENABLED,
                "use_llm": settings.RERANK_USE_LLM,
            },
        },
    }


@router.get("/supported-formats")
async def get_supported_formats():
    return {"formats": list(SUPPORTED_FORMATS.keys())}


@router.post("/documents/analyze-layout")
async def analyze_document_layout(file: UploadFile = File(...)):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "鏂囦欢杩囧ぇ")

    try:
        from app.doc_processor.layout_analyzer import layout_analyzer
        layout_result = layout_analyzer.analyze_content(content, file.filename)

        blocks_summary = []
        for block in layout_result.blocks:
            blocks_summary.append({
                "type": block.block_type.value,
                "content_preview": block.content[:200] if block.content else "",
                "page": block.page,
                "level": block.level,
                "confidence": block.confidence,
            })

        return {
            "success": True,
            "filename": file.filename,
            "page_count": layout_result.page_count,
            "total_blocks": len(layout_result.blocks),
            "blocks": blocks_summary,
            "tables_count": len(layout_result.tables),
            "images_count": len(layout_result.images),
            "block_type_distribution": {
                block_type.value: sum(1 for b in layout_result.blocks if b.block_type == block_type)
                for block_type in set(b.block_type for b in layout_result.blocks)
            },
        }
    except Exception as e:
        logger.error(f"鐗堥潰鍒嗘瀽澶辫触: {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


# ==================== Knowledge Base Management ====================

@router.post("/kb/create")
async def create_knowledge_base(request: KBCreateRequest):
    if not request.name.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "知识库名称不能为空")
    try:
        async with _kb_mutation_lock:
            kb = await asyncio.to_thread(
                kb_manager.create,
                name=request.name,
                description=request.description,
                chunk_strategy=request.chunk_strategy,
                chunk_size=request.chunk_size or settings.CHUNK_SIZE,
                chunk_overlap=request.chunk_overlap or settings.CHUNK_OVERLAP,
            )
        return {"success": True, "kb": kb.to_dict()}
    except Exception as e:
        logger.error(f"鍒涘缓鐭ヨ瘑搴撳け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.get("/kb/list")
async def list_knowledge_bases():
    kb_manager.ensure_core()
    bases = kb_manager.list_all()
    return {"success": True, "bases": [kb.to_dict() for kb in bases]}


@router.get("/kb/{kb_id}")
async def get_knowledge_base(kb_id: str):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    return {"success": True, "kb": kb.to_dict()}


@router.delete("/kb/{kb_id}")
async def delete_knowledge_base(kb_id: str):
    async with _kb_mutation_lock:
        return await _delete_knowledge_base_locked(kb_id)


async def _delete_knowledge_base_locked(kb_id: str):
    if kb_id == CORE_KB_ID:
        raise error_response(ErrorCode.INVALID_PARAM, "核心知识库不能删除")
    if not kb_manager.get(kb_id):
        raise error_response(ErrorCode.KB_NOT_FOUND, "知识库不存在")

    documents = await asyncio.to_thread(kb_manager.list_documents, kb_id)
    doc_ids = [doc.get("doc_id", "") for doc in documents if doc.get("doc_id")]
    removed_vectors = await asyncio.to_thread(vector_store.delete_by_kb, kb_id)
    await asyncio.to_thread(hybrid_search_engine.delete_by_kb, kb_id)
    success = await asyncio.to_thread(kb_manager.delete, kb_id)
    if not success:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    deleted_files, cleanup_warnings = await asyncio.to_thread(
        _remove_kb_document_artifacts,
        doc_ids,
    )
    return {
        "success": True,
        "removed_vectors": removed_vectors,
        "deleted_files": deleted_files,
        "cleanup_warnings": cleanup_warnings,
    }


@router.get("/kb/{kb_id}/documents")
async def list_kb_documents(kb_id: str):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    docs = kb_manager.list_documents(kb_id)
    doc_index_counts = {}
    try:
        doc_index_counts = {
            doc_id: len(indices)
            for doc_id, indices in getattr(vector_store, "_doc_index", {}).items()
        }
    except Exception:
        doc_index_counts = {}
    for doc in docs:
        doc["size"] = doc.get("file_size") or doc.get("stored_size") or 0
        doc_id = doc.get("doc_id", "")
        indexed_chunk_count = doc_index_counts.get(doc_id, 0) if doc_id else 0
        doc.update(_build_doc_vectorization_status(doc, indexed_chunk_count))
    return {"success": True, "kb_id": kb_id, "documents": docs, "doc_count": len(docs)}


@router.get("/kb/{kb_id}/chunks")
async def get_kb_chunks(kb_id: str, page: int = 1, page_size: int = 50):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    result = vector_store.get_chunks_by_kb(kb_id, page=page, page_size=page_size)
    return {"success": True, **result}


@router.delete("/kb/{kb_id}/documents/{doc_id}")
async def delete_kb_document(kb_id: str, doc_id: str):
    async with _kb_mutation_lock:
        return await _delete_kb_document_locked(kb_id, doc_id)


async def _delete_kb_document_locked(kb_id: str, doc_id: str):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")

    try:
        doc_meta = kb_manager.get_document_meta(kb_id, doc_id)
        if not doc_meta:
            doc_meta = next((doc for doc in kb_manager.list_documents(kb_id) if doc.get("doc_id") == doc_id), None)
        if not doc_meta:
            removed_chunks = await asyncio.to_thread(vector_store.delete_by_doc, doc_id)
            await asyncio.to_thread(hybrid_search_engine.delete_by_doc, doc_id)
            remaining_docs = await asyncio.to_thread(kb_manager.list_documents, kb_id)
            remaining_chunks = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)
            await asyncio.to_thread(kb_manager.set_counts, kb_id, len(remaining_docs), remaining_chunks)
            return {
                "success": True,
                "doc_id": doc_id,
                "removed_chunks": removed_chunks,
                "deleted_files": [],
                "already_deleted": True,
                "message": "文档已不存在，相关索引已确认清理",
            }

        # Remove vector and keyword indexes before publishing the metadata deletion.
        removed_chunks = await asyncio.to_thread(vector_store.delete_by_doc, doc_id)

        # 2. 鍒犻櫎娣峰悎妫€绱㈠紩鎿庝腑鐨勮褰?
        await asyncio.to_thread(hybrid_search_engine.delete_by_doc, doc_id)

        deleted_files = await asyncio.to_thread(kb_manager.delete_document, kb_id, doc_id)

        artifact_files, cleanup_warnings = await asyncio.to_thread(
            _remove_kb_document_artifacts,
            [doc_id],
        )
        deleted_files.extend(artifact_files)

        # Reconcile counters from authoritative storage to prevent cumulative drift.
        remaining_docs = await asyncio.to_thread(kb_manager.list_documents, kb_id)
        remaining_chunks = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)
        await asyncio.to_thread(kb_manager.set_counts, kb_id, len(remaining_docs), remaining_chunks)

        logger.info(f"鏂囨。鍒犻櫎鎴愬姛: kb={kb_id}, doc={doc_id}, 鍒犻櫎鍒嗗潡={removed_chunks}, 鍒犻櫎鏂囦欢={deleted_files}")
        return {
            "success": True,
            "doc_id": doc_id,
            "removed_chunks": removed_chunks,
            "deleted_files": deleted_files,
            "cleanup_warnings": cleanup_warnings,
            "message": f"文档已删除，共移除 {removed_chunks} 个分块",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"鏂囨。鍒犻櫎澶辫触: {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, f"鏂囨。鍒犻櫎澶辫触: {str(e)}")


def _remove_kb_document_artifacts(doc_ids: List[str]) -> tuple[List[str], List[str]]:
    """Remove source and normalized files that live outside a KB directory."""
    deleted: List[str] = []
    warnings: List[str] = []
    unique_doc_ids = {doc_id for doc_id in doc_ids if doc_id}

    for doc_id in unique_doc_ids:
        upload_dir = Path(settings.UPLOAD_DIR)
        upload_candidates = []
        try:
            if upload_dir.is_dir():
                upload_candidates = [
                    path
                    for path in upload_dir.glob(f"{doc_id}*")
                    if path.is_file()
                    and (path.name == doc_id or path.name.startswith(f"{doc_id}_"))
                ]
        except OSError as exc:
            warnings.append(f"无法扫描上传目录 {doc_id}: {exc}")

        candidates = upload_candidates + [
            Path(settings.KNOWLEDGE_BASE_DIR) / f"{doc_id}.md",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError as exc:
                warnings.append(f"无法删除 {path.name}: {exc}")

    if warnings:
        logger.warning("知识库附件清理未完全成功: %s", "; ".join(warnings))
    return deleted, warnings


@router.get("/kb/{kb_id}/documents/{doc_id}/chunks")
async def get_doc_chunks(kb_id: str, doc_id: str):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    chunks = vector_store.get_chunks_by_doc(doc_id)
    return {"success": True, "chunks": chunks, "total": len(chunks)}


async def _vectorize_kb_documents_locked(
    kb_id: str,
    doc_ids: Optional[List[str]] = None,
    force: bool = False,
):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "知识库不存在")

    doc_list = await asyncio.to_thread(kb_manager.list_documents, kb_id)
    if doc_ids:
        target_ids = {doc_id for doc_id in doc_ids if doc_id}
        doc_list = [doc for doc in doc_list if doc.get("doc_id") in target_ids]

    if not doc_list:
        return {
            "success": True,
            "kb_id": kb_id,
            "documents": 0,
            "removed_chunks": 0,
            "new_chunks": 0,
            "skipped_docs": [],
            "failed_docs": [],
        }

    strategy = kb.chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
    if strategy == "layout_aware":
        strategy = "auto"
    size = kb.chunk_size or settings.CHUNK_SIZE
    overlap = kb.chunk_overlap or settings.CHUNK_OVERLAP

    processed_docs = 0
    removed_total = 0
    new_total = 0
    skipped_docs = []
    failed_docs = []

    for doc_info in doc_list:
        doc_id = doc_info.get("doc_id", "")
        if not doc_id:
            continue

        expected_chunks = int(doc_info.get("chunk_count") or 0)
        existing_chunks = vector_store.get_chunks_by_doc(doc_id)
        if not force and expected_chunks > 0 and len(existing_chunks) >= expected_chunks:
            skipped_docs.append({
                "doc_id": doc_id,
                "title": doc_info.get("title") or doc_info.get("filename") or doc_id,
                "reason": "already_vectorized",
                "existing_chunks": len(existing_chunks),
                "expected_chunks": expected_chunks,
            })
            continue

        content = await asyncio.to_thread(kb_manager.load_document, kb_id, doc_id)
        if not content:
            failed_docs.append({"doc_id": doc_id, "error": "文档内容为空或不存在"})
            continue

        filename = doc_info.get("filename") or f"{doc_id}.md"
        title = doc_info.get("title") or Path(filename).stem

        try:
            existing_meta = existing_chunks[0].get("metadata", {}) if existing_chunks else {}
            title = existing_meta.get("document_title") or existing_meta.get("title") or title

            removed = await asyncio.to_thread(vector_store.delete_by_doc, doc_id)
            await asyncio.to_thread(hybrid_search_engine.delete_by_doc, doc_id)

            chunker = SmartChunker(chunk_size=size, chunk_overlap=overlap, strategy=strategy)
            chunks = chunker.chunk(content, doc_id, filename=filename)
            detected_strategy = strategy
            if strategy == "auto" and chunks:
                detected_strategy = chunks[0].metadata.get("chunk_strategy", strategy)

            for chunk in chunks:
                chunk.metadata["title"] = title
                chunk.metadata["kb_id"] = kb_id
                chunk.metadata["chunk_strategy"] = detected_strategy

            chunks_data = [
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "content": c.content,
                    "metadata": c.metadata,
                }
                for c in chunks
            ]

            if chunks_data:
                await vector_store.add_documents_async(chunks_data)
                await asyncio.to_thread(hybrid_search_engine.add_documents, chunks_data)

            processed_docs += 1
            removed_total += removed
            new_total += len(chunks_data)
        except Exception as e:
            logger.error(f"文档向量化失败: kb={kb_id}, doc={doc_id}, error={e}")
            failed_docs.append({"doc_id": doc_id, "error": str(e)})

    authoritative_chunks = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)
    await asyncio.to_thread(kb_manager.set_counts, kb_id, len(doc_list), authoritative_chunks)

    return {
        "success": len(failed_docs) == 0,
        "kb_id": kb_id,
        "documents": processed_docs,
        "removed_chunks": removed_total,
        "new_chunks": new_total,
        "skipped_docs": skipped_docs,
        "failed_docs": failed_docs,
        "chunk_size": size,
        "chunk_overlap": overlap,
        "message": f"已向量化 {processed_docs} 个文档，新分块 {new_total} 个",
    }


@router.post("/kb/{kb_id}/rebuild-index")
async def rebuild_kb_index(kb_id: str):
    async with _kb_mutation_lock:
        return await _rebuild_kb_index_locked(kb_id)


async def _rebuild_kb_index_locked(kb_id: str):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    return await _vectorize_kb_documents_locked(kb_id, force=True)

    doc_list = await asyncio.to_thread(kb_manager.list_documents, kb_id)
    if not doc_list:
        return {"success": True, "kb_id": kb_id, "documents": 0, "removed_chunks": 0, "new_chunks": 0}

    strategy = kb.chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
    if strategy == "layout_aware":
        strategy = "auto"
    size = kb.chunk_size or settings.CHUNK_SIZE
    overlap = kb.chunk_overlap or settings.CHUNK_OVERLAP

    rebuilt_docs = 0
    removed_total = 0
    new_total = 0
    failed_docs = []

    for doc_info in doc_list:
        doc_id = doc_info.get("doc_id", "")
        if not doc_id:
            continue

        content = await asyncio.to_thread(kb_manager.load_document, kb_id, doc_id)
        if not content:
            failed_docs.append({"doc_id": doc_id, "error": "鏂囨。鍐呭涓虹┖鎴栦笉瀛樺湪"})
            continue

        filename = doc_info.get("filename") or f"{doc_id}.md"
        title = doc_info.get("title") or Path(filename).stem

        try:
            existing_chunks = vector_store.get_chunks_by_doc(doc_id)
            existing_meta = existing_chunks[0].get("metadata", {}) if existing_chunks else {}
            title = existing_meta.get("document_title") or existing_meta.get("title") or title

            removed = await asyncio.to_thread(vector_store.delete_by_doc, doc_id)
            await asyncio.to_thread(hybrid_search_engine.delete_by_doc, doc_id)

            chunker = SmartChunker(chunk_size=size, chunk_overlap=overlap, strategy=strategy)
            chunks = chunker.chunk(content, doc_id, filename=filename)
            detected_strategy = strategy
            if strategy == "auto" and chunks:
                detected_strategy = chunks[0].metadata.get("chunk_strategy", strategy)

            for chunk in chunks:
                chunk.metadata["title"] = title
                chunk.metadata["kb_id"] = kb_id
                chunk.metadata["chunk_strategy"] = detected_strategy

            chunks_data = [
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "content": c.content,
                    "metadata": c.metadata,
                }
                for c in chunks
            ]

            if chunks_data:
                await vector_store.add_documents_async(chunks_data)
                await asyncio.to_thread(hybrid_search_engine.add_documents, chunks_data)

            rebuilt_docs += 1
            removed_total += removed
            new_total += len(chunks_data)
        except Exception as e:
            logger.error(f"閲嶅缓鏂囨。绱㈠紩澶辫触: kb={kb_id}, doc={doc_id}, error={e}")
            failed_docs.append({"doc_id": doc_id, "error": str(e)})

    authoritative_chunks = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)
    await asyncio.to_thread(kb_manager.set_counts, kb_id, len(doc_list), authoritative_chunks)

    return {
        "success": len(failed_docs) == 0,
        "kb_id": kb_id,
        "documents": rebuilt_docs,
        "removed_chunks": removed_total,
        "new_chunks": new_total,
        "chunk_size": size,
        "chunk_overlap": overlap,
        "child_chunk_size": settings.CHILD_CHUNK_SIZE,
        "child_chunk_overlap": settings.CHILD_CHUNK_OVERLAP,
        "failed_docs": failed_docs,
        "message": f"已重建 {rebuilt_docs} 个文档，旧分块 {removed_total} 个，新分块 {new_total} 个",
    }
@router.post("/kb/{kb_id}/documents/vectorize")
async def vectorize_kb_documents(kb_id: str, request: VectorizeDocumentsRequest):
    async with _kb_mutation_lock:
        return await _vectorize_kb_documents_locked(
            kb_id,
            doc_ids=request.doc_ids,
            force=request.force,
        )


# P3: 鏂囨。鍘婚噸妫€娴?
@router.post("/kb/{kb_id}/dedup")
async def detect_duplicates(kb_id: str):
    """API route."""
    import hashlib
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    if not os.path.isdir(kb_dir):
        return {"success": True, "duplicates": [], "count": 0}
    # 璁＄畻鏂囦欢鍝堝笇
    file_hashes = {}
    for fname in os.listdir(kb_dir):
        fpath = os.path.join(kb_dir, fname)
        if not os.path.isfile(fpath):
            continue
        h = hashlib.sha256()
        with open(fpath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        file_hash = h.hexdigest()
        file_hashes.setdefault(file_hash, []).append({
            "filename": fname,
            "size": os.path.getsize(fpath),
            "modified": os.path.getmtime(fpath),
        })
    # 鎵惧嚭閲嶅
    duplicates = []
    for hash_val, files in file_hashes.items():
        if len(files) > 1:
            duplicates.append({"hash": hash_val, "files": files, "count": len(files)})
    return {"success": True, "duplicates": duplicates, "count": len(duplicates)}


# P3: 鍚堝苟閲嶅鏂囨。锛堜繚鐣欐渶鏂扮殑锛屽垹闄ゅ叾浣欙級
@router.post("/kb/{kb_id}/dedup/merge")
async def merge_duplicates(kb_id: str):
    """API route."""
    import hashlib
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    if not os.path.isdir(kb_dir):
        return {"success": True, "removed": 0}
    file_hashes = {}
    for fname in os.listdir(kb_dir):
        fpath = os.path.join(kb_dir, fname)
        if not os.path.isfile(fpath):
            continue
        h = hashlib.sha256()
        with open(fpath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        file_hashes.setdefault(h.hexdigest(), []).append((fname, os.path.getmtime(fpath)))
    removed = 0
    for hash_val, files in file_hashes.items():
        if len(files) > 1:
            # 鎸変慨鏀规椂闂存帓搴忥紝淇濈暀鏈€鏂扮殑
            files.sort(key=lambda x: x[1], reverse=True)
            for fname, _ in files[1:]:
                os.remove(os.path.join(kb_dir, fname))
                removed += 1
    return {"success": True, "removed": removed}


# P7: 鏁版嵁瀵煎叆澧炲己 - 鎵归噺瀵煎叆Markdown鏂囦欢
@router.post("/kb/{kb_id}/import/markdown")
async def import_markdown_files(kb_id: str, files: List[UploadFile] = File(...)):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    os.makedirs(kb_dir, exist_ok=True)
    imported = []
    for f in files:
        if not f.filename.endswith(('.md', '.markdown', '.txt')):
            continue
        safe_name = "".join(c for c in f.filename if c.isalnum() or c in '._-')
        fpath = os.path.join(kb_dir, safe_name)
        content = await f.read()
        with open(fpath, 'wb') as out:
            out.write(content)
        imported.append(safe_name)
    return {"success": True, "imported": imported, "count": len(imported)}


# P7: Notion 瀵煎叆锛堣В鏋怤otion瀵煎嚭鐨刏IP锛?
@router.post("/kb/{kb_id}/import/notion")
async def import_notion_export(kb_id: str, file: UploadFile = File(...)):
    """API route."""
    import zipfile
    import io
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    os.makedirs(kb_dir, exist_ok=True)
    content = await file.read()
    imported = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.endswith(('.md', '.markdown', '.txt')) and not name.startswith('__MACOSX'):
                    basename = os.path.basename(name)
                    safe_name = "".join(c for c in basename if c.isalnum() or c in '._-')
                    if not safe_name:
                        continue
                    with zf.open(name) as src, open(os.path.join(kb_dir, safe_name), 'wb') as dst:
                        dst.write(src.read())
                    imported.append(safe_name)
    except zipfile.BadZipFile:
        raise error_response(ErrorCode.INVALID_PARAM, "鏃犳晥鐨刏IP鏂囦欢")
    return {"success": True, "imported": imported, "count": len(imported)}


@router.get("/kb/{kb_id}/preview/{filename}")
async def preview_kb_document(kb_id: str, filename: str):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_doc_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    fpath = validate_file_path(kb_doc_dir, filename)
    if not os.path.isfile(fpath):
        raise error_response(ErrorCode.FILE_NOT_FOUND, "文件不存在")

    ext = os.path.splitext(filename)[1].lower()
    content = ""
    file_type = "text"

    try:
        if ext == '.pdf':
            file_type = "pdf"
            import pdfplumber
            with pdfplumber.open(fpath) as pdf:
                pages = []
                for i, page in enumerate(pdf.pages[:10]):
                    text = page.extract_text() or ""
                    pages.append({"page": i + 1, "text": text})
                content = pages
        elif ext in ('.xlsx', '.xls'):
            file_type = "excel"
            import openpyxl
            wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
            sheets = []
            for sheet_name in wb.sheetnames[:5]:
                ws = wb[sheet_name]
                rows = []
                for i, row in enumerate(ws.iter_rows(max_row=20, values_only=True)):
                    rows.append([str(c) if c is not None else "" for c in row])
                sheets.append({"sheet": sheet_name, "rows": rows})
            wb.close()
            content = sheets
        elif ext == '.csv':
            file_type = "csv"
            import csv as csv_mod
            encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']
            for enc in encodings:
                try:
                    with open(fpath, 'r', encoding=enc) as f:
                        reader = csv_mod.reader(f)
                        rows = [row for _, row in zip(range(30), reader)]
                    content = rows
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            else:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    reader = csv_mod.reader(f)
                    rows = [row for _, row in zip(range(30), reader)]
                content = rows
        elif ext in ('.docx', '.doc'):
            file_type = "docx"
            from docx import Document as DocxDocument
            doc = DocxDocument(fpath)
            paragraphs = [p.text for p in doc.paragraphs[:50] if p.text.strip()]
            content = paragraphs
        elif ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
            file_type = "image"
            import base64
            with open(fpath, 'rb') as f:
                img_data = f.read()
            content = base64.b64encode(img_data).decode('ascii')
        else:
            encodings = ['utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'latin-1']
            for enc in encodings:
                try:
                    with open(fpath, 'r', encoding=enc) as f:
                        content = f.read(50000)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            else:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(50000)
    except Exception as e:
        logger.error(f"鏂囦欢棰勮澶辫触: {e}")
        content = f"棰勮澶辫触: {str(e)}"
        file_type = "error"

    return {
        "success": True,
        "filename": filename,
        "file_type": file_type,
        "content": content,
        "size": os.path.getsize(fpath),
    }


@router.post("/kb/{kb_id}/upload")
async def upload_to_knowledge_base(
    kb_id: str,
    file: UploadFile = File(...),
    chunk_strategy: str = Form(""),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    content = await file.read()
    async with _kb_mutation_lock:
        return await _ingest_kb_document_bytes(
            kb_id,
            file.filename or "",
            content,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )


@router.post("/kb/{kb_id}/import/file-resources")
async def import_file_resources_to_kb(kb_id: str, payload: KBFileResourceImportRequest):
    async with _kb_mutation_lock:
        return await _import_file_resources_to_kb_locked(kb_id, payload)


async def _import_file_resources_to_kb_locked(kb_id: str, payload: KBFileResourceImportRequest):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "知识库不存在")
    if not payload.paths:
        raise error_response(ErrorCode.INVALID_PARAM, "paths is required")

    base = Path(_ensure_file_resource_dir()).resolve()
    selected_files: List[Path] = []
    seen_rel_paths = set()
    skipped: List[Dict[str, Any]] = []

    for raw_path in payload.paths:
        try:
            rel_path = _normalize_resource_path(raw_path)
            target = Path(_resource_abs_path(rel_path))
        except HTTPException as exc:
            skipped.append({"path": raw_path, "reason": exc.detail if isinstance(exc.detail, str) else "invalid path"})
            continue

        if not target.exists():
            skipped.append({"path": rel_path, "reason": "file not found"})
            continue

        candidate_files = [p for p in target.rglob("*") if p.is_file()] if target.is_dir() else [target]
        for file_path in candidate_files:
            try:
                rel = file_path.relative_to(base).as_posix()
            except Exception:
                continue
            if rel in seen_rel_paths:
                continue
            seen_rel_paths.add(rel)
            selected_files.append(file_path)

    imported: List[Dict[str, Any]] = []
    duplicate: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for file_path in selected_files:
        ext = file_path.suffix.lower()
        rel_path = file_path.relative_to(base).as_posix()
        if ext not in SUPPORTED_FORMATS:
            skipped.append({"path": rel_path, "reason": f"unsupported format: {ext}"})
            continue

        try:
            content = file_path.read_bytes()
            result = await _ingest_kb_document_bytes(
                kb_id,
                file_path.name,
                content,
                chunk_strategy=payload.chunk_strategy or "",
                chunk_size=payload.chunk_size or 0,
                chunk_overlap=payload.chunk_overlap or 0,
                persist_indexes=settings.use_supabase,
                source_path=rel_path,
            )
            if result.get("success"):
                imported.append({
                    "path": rel_path,
                    "filename": file_path.name,
                    "doc_id": result.get("doc_id", ""),
                    "chunk_count": result.get("chunk_count", 0),
                    "detected_doc_type": result.get("detected_doc_type", ""),
                    "source_format": (result.get("conversion") or {}).get("source_format", ext.lstrip(".")),
                })
            elif result.get("duplicate") or result.get("conflict"):
                duplicate.append({
                    "path": rel_path,
                    "filename": file_path.name,
                    "message": result.get("message", "duplicate"),
                })
            else:
                failed.append({
                    "path": rel_path,
                    "filename": file_path.name,
                    "message": result.get("message", "import failed"),
                })
        except HTTPException as exc:
            failed.append({
                "path": rel_path,
                "filename": file_path.name,
                "message": exc.detail.get("message") if isinstance(exc.detail, dict) else str(exc.detail),
            })
        except Exception as exc:
            failed.append({
                "path": rel_path,
                "filename": file_path.name,
                "message": str(exc),
            })

    if imported:
        if not settings.use_supabase:
            await asyncio.to_thread(vector_store.persist)
        await asyncio.to_thread(hybrid_search_engine.finalize_batch)

    authoritative_docs = await asyncio.to_thread(kb_manager.list_documents, kb_id)
    authoritative_chunks = vector_store.get_chunks_by_kb(kb_id, page=1, page_size=1).get("total", 0)
    await asyncio.to_thread(kb_manager.set_counts, kb_id, len(authoritative_docs), authoritative_chunks)

    return {
        "success": len(failed) == 0,
        "kb_id": kb_id,
        "requested_count": len(payload.paths),
        "selected_count": len(selected_files),
        "imported_count": len(imported),
        "duplicate_count": len(duplicate),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "imported": imported,
        "duplicates": duplicate,
        "skipped": skipped,
        "failed": failed,
        "message": f"批量入库完成：成功 {len(imported)} 个，重复 {len(duplicate)} 个，跳过 {len(skipped)} 个",
    }


# ==================== Cloud File Resources ====================

@router.get("/file-resources/tree")
async def list_file_resources():
    base_dir = await asyncio.to_thread(_ensure_file_resource_dir)
    files = await asyncio.to_thread(_build_file_resource_tree, base_dir)
    return {
        "success": True,
        "root": "我的文档库",
        "files": files,
    }


@router.get("/file-resources/download-all")
async def download_all_file_resources():
    base = Path(_ensure_file_resource_dir()).resolve()
    root_name = "CoreNote文件资源"

    def _build_archive() -> str:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        temp_path = temp.name
        temp.close()

        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{root_name}/", "")
            for root, dirs, files in os.walk(base, followlinks=False):
                root_path = Path(root)
                dirs[:] = [
                    name for name in dirs
                    if not (root_path / name).is_symlink()
                    and (root_path / name).resolve().is_relative_to(base)
                ]

                relative_dir = root_path.relative_to(base).as_posix()
                if relative_dir and relative_dir != ".":
                    archive.writestr(f"{root_name}/{relative_dir}/", "")

                for name in files:
                    file_path = root_path / name
                    if file_path.is_symlink() or not file_path.resolve().is_relative_to(base):
                        continue
                    relative_path = file_path.relative_to(base).as_posix()
                    archive.write(file_path, arcname=f"{root_name}/{relative_path}")
        return temp_path

    archive_path = await asyncio.to_thread(_build_archive)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"CoreNote文件资源_{timestamp}.zip",
        background=BackgroundTask(os.unlink, archive_path),
    )


@router.post("/file-resources/create")
async def create_file_resource(payload: FileResourceCreateRequest):
    target = _resource_abs_path(payload.path)
    if payload.is_directory:
        os.makedirs(target, exist_ok=True)
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            raise error_response(ErrorCode.INVALID_PARAM, f"file already exists: {payload.path}")
        with open(target, "w", encoding="utf-8") as f:
            f.write(payload.content or "")
    return {
        "success": True,
        "path": _normalize_resource_path(payload.path),
        "files": _build_file_resource_tree(_ensure_file_resource_dir()),
    }


@router.post("/file-resources/upload")
async def upload_file_resource(file: UploadFile = File(...), folder: str = Form("")):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "filename is required")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "file too large")

    folder = (folder or "").replace("\\", "/").strip().strip("/")
    rel_path = f"{folder}/{file.filename}" if folder else file.filename
    rel_path = _normalize_resource_path(rel_path)
    target = _resource_abs_path(rel_path)

    if os.path.exists(target):
        stem, ext = os.path.splitext(target)
        suffix = 1
        while os.path.exists(f"{stem}_{suffix}{ext}"):
            suffix += 1
        target = f"{stem}_{suffix}{ext}"
        rel_path = Path(target).resolve().relative_to(Path(_ensure_file_resource_dir()).resolve()).as_posix()

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(content)

    return {
        "success": True,
        "path": rel_path,
        "name": os.path.basename(rel_path),
        "indexed": False,
        "doc": None,
        "message": f"文件 '{file.filename}' 已保存到文件资源库，未进入知识库分块索引",
        "files": _build_file_resource_tree(_ensure_file_resource_dir()),
    }


@router.post("/file-resources/capture")
async def capture_file_resource(payload: FileResourceCaptureRequest):
    """将浏览器采集的结构化网页内容保存到自动抓取/日期目录。"""
    if not payload.url.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "url is required")
    if not payload.content.strip() and not payload.selection.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "content or selection is required")

    captured_time = payload.captured_at or datetime.now().isoformat()
    try:
        captured_date = datetime.fromisoformat(captured_time.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        captured_date = datetime.now().strftime("%Y-%m-%d")

    folder_prefix = (payload.folder_prefix or "自动抓取").replace("\\", "/").strip("/")
    folder_parts = [sanitize_filename(part) for part in folder_prefix.split("/") if part.strip()]
    if not folder_parts:
        folder_parts = ["自动抓取"]
    folder = "/".join(folder_parts + [captured_date])

    title = (payload.title or "网页采集").strip()[:120]
    filename = sanitize_filename(title) or "网页采集"
    if not filename.lower().endswith(".md"):
        filename += ".md"
    rel_path = _normalize_resource_path(f"{folder}/{filename}")
    target = _resource_abs_path(rel_path)
    if os.path.exists(target):
        stem, ext = os.path.splitext(target)
        suffix = 1
        while os.path.exists(f"{stem}_{suffix}{ext}"):
            suffix += 1
        target = f"{stem}_{suffix}{ext}"
        rel_path = Path(target).resolve().relative_to(Path(_ensure_file_resource_dir()).resolve()).as_posix()

    body = _sanitize_captured_markdown(payload.content.strip() or payload.selection.strip(), title)
    if not body:
        body = payload.selection.strip() or payload.content.strip()
    metadata = [
        f"- 来源 URL：{payload.url.strip()}",
        f"- 来源类型：{payload.source_type or 'edge'}",
        f"- 采集时间：{captured_time}",
    ]
    if payload.author_name:
        metadata.append(f"- 作者：{payload.author_name.strip()}")
    if payload.published_at:
        metadata.append(f"- 发布时间：{payload.published_at.strip()}")
    if payload.description:
        metadata.append(f"- 摘要：{payload.description.strip()}")
    if payload.capture_mode:
        metadata.append(f"- 采集模式：{payload.capture_mode}")
    if payload.quality_score is not None:
        metadata.append(f"- 质量评分：{payload.quality_score}")
    if payload.warnings:
        metadata.append(f"- 采集提示：{'；'.join(str(item) for item in payload.warnings[:5])}")
    sections = [f"# {title}", "## 页面信息", "\n".join(metadata)]
    if payload.selection.strip() and payload.selection.strip() != body:
        sections.extend(["## 摘录内容", f"> {payload.selection.strip()}"])
    sections.extend(["## 页面正文", body[:300000]])

    # Download a bounded number of body images concurrently. The previous
    # sequential loop made pages with many images appear frozen in the
    # browser extension (12 images × 12 seconds could block for minutes).
    saved_images = []
    image_inputs = list((payload.images or [])[:12])
    image_semaphore = asyncio.Semaphore(4)

    async def download_image(index: int, image: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        image_url = str(image.get("url") or image.get("src") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            return None
        try:
            async with image_semaphore:
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=httpx.Timeout(10.0, connect=4.0),
                    headers={"User-Agent": "Mozilla/5.0", "Referer": payload.url.strip()},
                ) as client:
                    response = await client.get(image_url)
            if response.status_code != 200 or not response.content:
                return None
            content_type = (response.headers.get("content-type") or "").split(";")[0].lower()
            ext = mimetypes.guess_extension(content_type) or os.path.splitext(urllib.parse.urlparse(image_url).path)[1].lower()
            if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
                ext = ".jpg"
            image_bytes = response.content[:5 * 1024 * 1024]
            if not image_bytes:
                return None
            image_name = f"image_{index}{ext}"
            image_rel_path = f"{folder}/assets/{image_name}"
            image_target = _resource_abs_path(image_rel_path)
            os.makedirs(os.path.dirname(image_target), exist_ok=True)
            with open(image_target, "wb") as image_file:
                image_file.write(image_bytes)
            return {
                "name": image_name,
                "path": image_rel_path,
                "alt": str(image.get("alt") or f"图片 {index}").strip()[:120],
                "url": image_url,
            }
        except Exception as exc:
            logger.debug("capture image failed: %s", exc)
            return None

    downloaded = await asyncio.gather(*(download_image(index, image) for index, image in enumerate(image_inputs, start=1)), return_exceptions=True)
    saved_images = [item for item in downloaded if isinstance(item, dict)]
    # Rewrite image sources in Markdown/HTML, including URLs with query
    # strings, fragments, or percent-encoding. Exact string replacement is
    # too brittle because browsers commonly normalize these URLs differently
    # between the DOM and the serialized article content.
    for item in saved_images:
        image_path = urllib.parse.quote(item["path"], safe="/")
        local_url = f"/api/v1/file-resources/raw/{image_path}"
        source_url = str(item.get("url") or "").strip()
        def canonical_image_url(value: str) -> str:
            value = urllib.parse.unquote(str(value or "")).strip()
            value = value.replace("&amp;", "&")
            try:
                parsed = urllib.parse.urlsplit(value)
                return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))
            except ValueError:
                return value.split("#", 1)[0]

        source_key = canonical_image_url(source_url)
        source_variants = {
            source_url,
            urllib.parse.unquote(source_url),
            source_url.split("#", 1)[0],
            source_key,
        }

        def replace_markdown_image(match: re.Match) -> str:
            prefix, src, suffix = match.groups()
            decoded_src = urllib.parse.unquote(src)
            if (src in source_variants or decoded_src in source_variants
                    or decoded_src.split("#", 1)[0] in source_variants
                    or canonical_image_url(src) == source_key):
                return f"{prefix}{local_url}{suffix}"
            return match.group(0)

        body = re.sub(r"(!\[[^\]]*\()([^\s)]+)(\))", replace_markdown_image, body)
        body = re.sub(
            r"(<img\b[^>]*?\bsrc=[\"'])([^\"']+)([\"'][^>]*>)",
            lambda match: (
                f"{match.group(1)}{local_url}{match.group(3)}"
                if (match.group(2) in source_variants
                    or urllib.parse.unquote(match.group(2)) in source_variants
                    or canonical_image_url(match.group(2)) == source_key)
                else match.group(0)
            ),
            body,
            flags=re.IGNORECASE,
        )
    # If the extracted article text did not contain an image node (common for
    # pages that expose images only through lazy-load attributes), still keep
    # every successfully downloaded asset visible and recoverable in the
    # saved Markdown document.
    if saved_images:
        referenced_paths = {item["path"] for item in saved_images if f"/file-resources/raw/" in body and item["name"] in body}
        gallery = []
        for item in saved_images:
            if item["path"] in referenced_paths:
                continue
            image_path = urllib.parse.quote(item["path"], safe="/")
            gallery.append(f"![{item['alt']}]( /api/v1/file-resources/raw/{image_path})".replace("]( /", "](/"))
        if gallery:
            body = body.rstrip() + "\n\n## 页面图片\n\n" + "\n\n".join(gallery)
    sections[-1] = body[:300000]
    markdown = "\n\n".join(sections).strip() + "\n"

    os.makedirs(os.path.dirname(target), exist_ok=True)
    temp_target = target + ".tmp"
    try:
        with open(temp_target, "w", encoding="utf-8") as f:
            f.write(markdown)
        os.replace(temp_target, target)
    finally:
        if os.path.exists(temp_target):
            os.unlink(temp_target)

    return {
        "success": True,
        "path": rel_path,
        "name": os.path.basename(rel_path),
        "folder": folder,
        "indexed": False,
        "content_length": len(body),
        "images_count": len(saved_images),
        "images": saved_images,
        "capture_mode": payload.capture_mode or "full",
        "quality_score": payload.quality_score,
        "warnings": payload.warnings[:5],
        "message": f"网页内容已保存到文件资源库：{rel_path}",
    }


TEXT_RESOURCE_EXTS = {".md", ".markdown", ".txt", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml"}
QUERY_SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff", ".tif", ".ico"}


def _decode_resource_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_resource_as_text(path: Path, max_chars: int = 12000) -> str:
    ext = path.suffix.lower()
    if ext in QUERY_SKIP_EXTS:
        return ""
    if ext in TEXT_RESOURCE_EXTS:
        return _decode_resource_text(path.read_bytes())[:max_chars]
    if ext in SUPPORTED_FORMATS:
        try:
            result = file_converter.convert(path.read_bytes(), path.name)
            if result.success:
                return (result.markdown_content or "")[:max_chars]
        except Exception as e:
            logger.warning(f"file resource query conversion failed ({path.name}): {e}")
    return ""


def _query_terms(query: str) -> List[str]:
    q = (query or "").lower()
    terms = set(re.findall(r"[a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{1,}", q))
    stop_terms = {
        "现在", "当前", "这个", "那个", "多少", "几个", "几份", "总数", "数量",
        "文件", "文档", "资料", "内容", "里面", "有没有", "是什么", "什么是",
        "请问", "帮我", "一下", "你好", "您好",
    }
    for word in list(terms):
        if re.search(r"[\u4e00-\u9fff]", word) and len(word) > 2:
            for i in range(len(word) - 1):
                terms.add(word[i:i + 2])
    return [t for t in terms if t.strip() and t not in stop_terms]


def _score_resource_text(query: str, terms: List[str], rel_path: str, content: str) -> float:
    haystack = f"{rel_path}\n{content}".lower()
    score = 0.0
    q = (query or "").lower().strip()
    if q and q in haystack:
        score += 8.0
    rel_lower = rel_path.lower()
    for term in terms:
        count = haystack.count(term)
        if not count:
            continue
        score += min(5, count) * (1.0 if len(term) > 1 else 0.35)
        if term in rel_lower:
            score += 2.5
    return score


def _resource_snippet(content: str, terms: List[str], max_len: int = 420) -> str:
    if not content:
        return ""
    lower = content.lower()
    hit_pos = -1
    for term in terms:
        hit_pos = lower.find(term)
        if hit_pos >= 0:
            break
    if hit_pos < 0:
        hit_pos = 0
    start = max(0, hit_pos - max_len // 3)
    end = min(len(content), start + max_len)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet += "..."
    return re.sub(r"\s+", " ", snippet)


def _clean_resource_markdown(content: str, max_chars: int = 12000) -> str:
    text = str(content or "")
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[(?P<label>[^\]]+)]\([^)]+\)", r"\g<label>", text)
    text = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:max_chars]


def _extract_resource_outline(content: str, max_items: int = 8) -> List[str]:
    outline: List[str] = []
    for line in str(content or "").splitlines():
        heading = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", line)
        if heading:
            title = re.sub(r"[*_`#]+", "", heading.group(1)).strip()
            if title and title not in outline:
                outline.append(title)
        if len(outline) >= max_items:
            break
    return outline


def _extract_resource_key_points(content: str, max_items: int = 5) -> List[str]:
    cleaned = _clean_resource_markdown(content, 9000)
    points: List[str] = []
    for line in cleaned.splitlines():
        item = re.match(r"^\s*(?:[-*+]|(?:\d+|[一二三四五六七八九十]+)[、.．])\s+(.+)$", line)
        if item:
            point = re.sub(r"\s+", " ", item.group(1)).strip()
            point = re.sub(r"[*_`]+", "", point)
            if 8 <= len(point) <= 120 and point not in points:
                points.append(point)
        if len(points) >= max_items:
            return points

    paragraphs = [
        re.sub(r"\s+", " ", p).strip()
        for p in re.split(r"\n{2,}", cleaned)
        if len(re.sub(r"\s+", "", p)) >= 24
    ]
    for para in paragraphs:
        para = re.sub(r"^#+\s*", "", para)
        para = re.sub(r"[*_`]+", "", para)
        if para and para not in points:
            points.append(para[:140] + ("..." if len(para) > 140 else ""))
        if len(points) >= max_items:
            break
    return points


def _extract_resource_thesis(content: str) -> str:
    cleaned = _clean_resource_markdown(content, 6000)
    paragraphs = [
        re.sub(r"\s+", " ", p).strip()
        for p in re.split(r"\n{2,}", cleaned)
    ]
    for para in paragraphs:
        para = re.sub(r"^#+\s*", "", para)
        para = re.sub(r"[*_`]+", "", para).strip()
        if not para or para.startswith("http"):
            continue
        compact_len = len(re.sub(r"\s+", "", para))
        if 35 <= compact_len <= 220:
            return para
        if compact_len > 220:
            return para[:180] + "..."
    return ""


def _looks_like_summary_query(query: str) -> bool:
    compact = re.sub(r"\s+", "", query or "").lower()
    return bool(re.search(r"(讲的是什么|讲什么|说的是什么|说什么|主要内容|总结|概括|摘要|核心观点|核心内容|这篇|这份|这篇文章|这个文档)", compact))


def _find_requested_resource(query: str, base: Path) -> Optional[Dict[str, Any]]:
    raw_query = (query or "").replace("\\", "/")
    compact_query = re.sub(r"\s+", "", raw_query).lower()
    files = [p for p in base.rglob("*") if p.is_file() and p.suffix.lower() not in QUERY_SKIP_EXTS]
    best: Optional[Path] = None
    best_score = 0
    for path in files:
        rel_path = path.relative_to(base).as_posix()
        rel_compact = re.sub(r"\s+", "", rel_path).lower()
        name_compact = re.sub(r"\s+", "", path.name).lower()
        stem_compact = re.sub(r"\s+", "", path.stem).lower()
        score = 0
        if rel_compact and rel_compact in compact_query:
            score = 1000 + len(rel_compact)
        elif name_compact and name_compact in compact_query:
            score = 700 + len(name_compact)
        elif stem_compact and len(stem_compact) >= 6 and stem_compact in compact_query:
            score = 500 + len(stem_compact)
        if score > best_score:
            best_score = score
            best = path
    if not best:
        return None
    content = _read_resource_as_text(best, max_chars=20000)
    if not content.strip():
        return None
    return {
        "path": best.relative_to(base).as_posix(),
        "name": best.name,
        "score": float(best_score),
        "content": content,
        "snippet": _resource_snippet(content, _query_terms(query), max_len=500),
        "targeted": True,
    }


def _fallback_file_resource_summary(query: str, sources: List[Dict[str, Any]]) -> str:
    if not sources:
        return "没有在左侧文档库中找到足够相关的内容。可以换一个更具体的问题，或先打开/上传对应文档。"

    targeted = len(sources) == 1 and sources[0].get("targeted")
    if targeted or _looks_like_summary_query(query):
        src = sources[0]
        content = src.get("content") or src.get("snippet") or ""
        title = re.sub(r"\.(md|markdown|txt|html?)$", "", src.get("name") or src.get("path") or "这篇文档", flags=re.I)
        thesis = _extract_resource_thesis(content)
        outline = _extract_resource_outline(content)
        points = _extract_resource_key_points(content)
        lines = [
            f"这篇文档主要讲：**{title}**。",
            "",
            f"核心意思：{thesis or '它围绕文档标题对应的主题，提炼问题背景、判断标准、操作方法和落地建议，帮助读者快速理解该主题应该怎么判断、怎么执行、怎么避坑。'}",
        ]
        if outline:
            lines.extend(["", "主要结构："])
            lines.extend(f"- {item}" for item in outline[:6])
        if points:
            lines.extend(["", "关键信息："])
            lines.extend(f"- {item}" for item in points[:5])
        lines.extend(["", "一句话总结：这是一篇偏实战方法论的文章，重点不是解释概念，而是帮助你把文档主题落到可判断、可执行的场景里。"])
        return "\n".join(lines)

    lines = ["综合左侧文档库中命中的内容，可以这样概括："]
    for src in sources[:4]:
        content = src.get("content") or src.get("snippet") or ""
        title = re.sub(r"\.(md|markdown)$", "", src.get("name") or src.get("path") or "文档", flags=re.I)
        points = _extract_resource_key_points(content, max_items=2)
        if points:
            lines.append(f"- **{title}**：{points[0]}")
        else:
            lines.append(f"- **{title}**：{src.get('snippet') or '命中了相关内容，但文本较短。'}")
    lines.append("")
    lines.append("整体来看，这些文档更适合做主题梳理、方案归纳和知识点对照；如果你要我继续整理，可以问“按场景/方法/结论分组总结”。")
    return "\n".join(lines)


def _normalize_file_resource_answer_citations(answer: str, source_count: int) -> str:
    if not answer:
        return ""

    def replace_citation(match: re.Match) -> str:
        try:
            index = int(match.group(1))
        except ValueError:
            return ""
        return match.group(0) if 1 <= index <= source_count else ""

    cleaned = re.sub(r"【(\d+)】", replace_citation, answer)
    cleaned = re.sub(r"[ \t]+([。；，、,.!?！？])", r"\1", cleaned)
    return cleaned.strip()


async def _generate_file_resource_answer(query: str, sources: List[Dict[str, Any]]) -> str:
    if not sources:
        return "当前文件资源库没有检索到足够相关的内容。可以换一个更具体的问题，或先在左侧上传/创建相关文档。"

    context_parts = []
    for i, src in enumerate(sources, 1):
        content = (src.get("content") or src.get("snippet") or "")[:1800]
        context_parts.append(f"【{i}】文件：{src.get('path')}\n{content}")
    context = "\n\n".join(context_parts)

    if not settings.OPENAI_API_KEY:
        return _fallback_file_resource_summary(query, sources)

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )

        def _call():
            return client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个高质量的个人文档库问答助手，擅长摘要、跨文档综合和实战化归纳。"
                            "只基于给定文件内容回答，不要编造未出现的事实、数字、案例或结论。"
                            "不要输出“相关文档”“来源列表”“引用编号”，不要输出 HTML。"
                            "如果用户问某篇文档讲什么，要像读完全文后给人解释：先一句话概括，再列主要结构、核心观点、适用场景。"
                            "如果用户问跨文档问题，要合并相同观点、区分不同观点，并给出清晰分组。"
                            "回答格式要求：使用 Markdown；标题最多两级；少废话；不要寒暄；不要大段粘贴原文。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"用户问题：{query}\n\n文件资源库片段：\n{context}\n\n请按指定格式回答：",
                    },
                ],
                max_tokens=min(settings.OPENAI_MAX_TOKENS, 1000),
                temperature=0.0,
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)
        answer = response.choices[0].message.content or ""
        return _normalize_file_resource_answer_citations(answer, len(sources)) or "当前文件资源库没有生成可用回答。"
    except Exception as e:
        logger.warning(f"file resource query LLM failed: {e}")
        return _fallback_file_resource_summary(query, sources)


def _file_resource_direct_answer(query: str, base: Path) -> Optional[str]:
    q = (query or "").strip().lower()
    compact = re.sub(r"\s+", "", q)
    if re.fullmatch(r"(你好|您好|hi|hello|hey|嗨|在吗|在不在|哈喽)[!！。,.，？?]*", compact):
        return "你好，我在。"
    if re.fullmatch(r"(谢谢|thanks|thankyou|ok|好的|好|收到|明白)[!！。,.，]*", compact):
        return "好的。"

    asks_count = (
        re.search(r"(多少|几个|几份|总数|数量|统计|count).*(文件|文档|资料|markdown|md)", compact, re.I)
        or re.search(r"(文件|文档|资料|markdown|md).*(多少|几个|几份|总数|数量|统计|count)", compact, re.I)
    )
    asks_list = (
        re.search(r"(列出|显示|有哪些|有什么|目录|清单|列表).*(文件|文档|资料|markdown|md)", compact, re.I)
        or re.search(r"(文件|文档|资料|markdown|md).*(列出|显示|有哪些|有什么|目录|清单|列表)", compact, re.I)
    )
    if not asks_count and not asks_list:
        return None

    files = [p for p in base.rglob("*") if p.is_file()]
    folders = [p for p in base.rglob("*") if p.is_dir()]
    markdown_files = [p for p in files if p.suffix.lower() in {".md", ".markdown"}]
    if asks_count:
        return f"当前文件资源库共有 {len(files)} 个文件、{len(folders)} 个文件夹，其中 Markdown 文档 {len(markdown_files)} 个。"

    samples = [p.relative_to(base).as_posix() for p in files[:12]]
    if not samples:
        return "当前文件资源库暂无文件。"
    sample_lines = "\n".join(f"{i + 1}. {path}" for i, path in enumerate(samples))
    return f"当前文件资源库共有 {len(files)} 个文件。先列出前 {len(samples)} 个：\n\n{sample_lines}"


@router.post("/file-resources/query")
async def query_file_resources(payload: FileResourceQueryRequest):
    query = (payload.query or "").strip()
    if not query:
        raise error_response(ErrorCode.INVALID_PARAM, "query is required")

    base = Path(_ensure_file_resource_dir()).resolve()
    direct_answer = _file_resource_direct_answer(query, base)
    if direct_answer:
        return {
            "success": True,
            "answer": direct_answer,
            "sources": [],
            "searched_files": 0,
            "direct": True,
        }

    requested_resource = await asyncio.to_thread(_find_requested_resource, query, base)
    if requested_resource:
        answer = await _generate_file_resource_answer(query, [requested_resource])
        return {
            "success": True,
            "answer": answer,
            "sources": [
                {k: v for k, v in requested_resource.items() if k != "content"}
            ],
            "searched_files": 1,
            "targeted": True,
        }

    terms = _query_terms(query)
    candidates: List[Dict[str, Any]] = []
    max_files = 240
    scanned_files = 0

    for idx, path in enumerate(p for p in base.rglob("*") if p.is_file()):
        if idx >= max_files:
            break
        scanned_files += 1
        rel_path = path.relative_to(base).as_posix()
        content = _read_resource_as_text(path)
        if not content.strip():
            continue
        score = _score_resource_text(query, terms, rel_path, content)
        if score <= 0:
            continue
        candidates.append({
            "path": rel_path,
            "name": path.name,
            "score": round(score, 4),
            "content": content[:2200],
            "snippet": _resource_snippet(content, terms),
        })

    candidates.sort(key=lambda item: item["score"], reverse=True)
    top_sources = candidates[:max(1, min(payload.top_k or 5, 8))]
    answer = await _generate_file_resource_answer(query, top_sources)

    return {
        "success": True,
        "answer": answer,
        "sources": [
            {k: v for k, v in src.items() if k != "content"}
            for src in top_sources
        ],
        "searched_files": scanned_files,
    }


@router.get("/file-resources/content/{resource_path:path}")
async def read_file_resource(resource_path: str):
    target = _resource_abs_path(resource_path)
    if not os.path.isfile(target):
        raise error_response(ErrorCode.FILE_NOT_FOUND, f"file not found: {resource_path}")

    ext = os.path.splitext(target)[1].lower()
    text_exts = {".md", ".markdown", ".txt", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml"}
    if ext in text_exts:
        raw = await asyncio.to_thread(Path(target).read_bytes)
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            content = raw.decode("utf-8", errors="replace")
    elif ext == ".pdf":
        content = ""
    elif ext in SUPPORTED_FORMATS:
        raw = await asyncio.to_thread(Path(target).read_bytes)
        result = await asyncio.to_thread(file_converter.convert, raw, os.path.basename(target))
        content = result.markdown_content if result.success else f"[鏃犳硶瑙ｆ瀽鏂囦欢: {result.error}]"
    else:
        content = "[璇ユ枃浠剁被鍨嬫殏涓嶆敮鎸侀瑙圿"

    return {
        "success": True,
        "path": _normalize_resource_path(resource_path),
        "name": os.path.basename(target),
        "content": content,
        "is_binary": ext == ".pdf",
        "raw_url": f"/api/v1/file-resources/raw/{_normalize_resource_path(resource_path)}" if ext == ".pdf" else "",
    }


@router.get("/file-resources/raw/{resource_path:path}")
async def read_file_resource_raw(resource_path: str):
    target = _resource_abs_path(resource_path)
    if not os.path.isfile(target):
        raise error_response(ErrorCode.FILE_NOT_FOUND, f"file not found: {resource_path}")
    media_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Content-Disposition": "inline"},
    )


@router.put("/file-resources/content/{resource_path:path}")
async def update_file_resource(resource_path: str, payload: FileResourceUpdateRequest):
    target = _resource_abs_path(resource_path)
    if os.path.isdir(target):
        raise error_response(ErrorCode.INVALID_PARAM, "cannot write content to a folder")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    def _atomic_write_text():
        temp_target = target + ".tmp"
        with open(temp_target, "w", encoding="utf-8") as f:
            f.write(payload.content)
        os.replace(temp_target, target)

    await asyncio.to_thread(_atomic_write_text)
    stat = await asyncio.to_thread(os.stat, target)
    return {
        "success": True,
        "path": _normalize_resource_path(resource_path),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


@router.post("/file-resources/move")
async def move_file_resources(payload: FileResourceMoveRequest):
    if not payload.operations:
        raise error_response(ErrorCode.INVALID_PARAM, "operations is required")

    moved = []
    for op in payload.operations:
        source_rel = _normalize_resource_path(op.source_path)
        target_rel = _normalize_resource_path(op.target_path)
        source = Path(_resource_abs_path(source_rel))
        target = Path(_resource_abs_path(target_rel))

        if not source.exists():
            raise error_response(ErrorCode.FILE_NOT_FOUND, f"file not found: {source_rel}")
        if source.resolve() == target.resolve():
            continue
        if source.is_dir() and source.resolve() in target.resolve().parents:
            raise error_response(ErrorCode.INVALID_PARAM, "cannot move a folder into itself")
        if target.exists():
            raise error_response(ErrorCode.INVALID_PARAM, f"target already exists: {target_rel}")

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        _remap_file_resource_order(source_rel, target_rel)
        moved.append({"source_path": source_rel, "target_path": target_rel})

    return {
        "success": True,
        "moved": moved,
        "files": _build_file_resource_tree(_ensure_file_resource_dir()),
}


@router.post("/file-resources/reorder")
async def reorder_file_resources(payload: FileResourceReorderRequest):
    parent_rel = _normalize_resource_path(payload.parent_path) if payload.parent_path else ""
    base = Path(_ensure_file_resource_dir()).resolve()
    parent = base if not parent_rel else Path(_resource_abs_path(parent_rel))

    if not parent.exists() or not parent.is_dir():
        raise error_response(ErrorCode.FILE_NOT_FOUND, f"folder not found: {parent_rel or '/'}")

    current_paths = [child.relative_to(base).as_posix() for child in parent.iterdir()]
    current_set = set(current_paths)
    ordered = []
    seen = set()
    for raw_path in payload.ordered_paths:
        rel = _normalize_resource_path(raw_path)
        if rel in current_set and rel not in seen:
            ordered.append(rel)
            seen.add(rel)

    for rel in sorted(current_set - seen, key=lambda p: (not (base / p).is_dir(), (base / p).name.lower())):
        ordered.append(rel)

    order_map = _load_file_resource_order()
    order_map[parent_rel] = ordered
    _save_file_resource_order(order_map)

    return {
        "success": True,
        "parent_path": parent_rel,
        "ordered_paths": ordered,
        "files": _build_file_resource_tree(_ensure_file_resource_dir()),
    }


@router.delete("/file-resources/{resource_path:path}")
async def delete_file_resource(resource_path: str):
    target = _resource_abs_path(resource_path)
    if not os.path.exists(target):
        raise error_response(ErrorCode.FILE_NOT_FOUND, f"file not found: {resource_path}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
    _prune_file_resource_order(_normalize_resource_path(resource_path))
    return {
        "success": True,
        "files": _build_file_resource_tree(_ensure_file_resource_dir()),
    }


# ==================== Document Upload ====================

@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise error_response(ErrorCode.UNSUPPORTED_FORMAT, f"涓嶆敮鎸佺殑鏍煎紡: {ext}")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "鏂囦欢杩囧ぇ")

    try:
        strategy = settings.CHUNK_STRATEGY_DEFAULT
        if _should_use_layout_processor(strategy, file.filename):
            processor = layout_doc_processor
            processing_strategy = strategy
        else:
            processor = doc_processor
            processing_strategy = _effective_text_strategy(strategy, file.filename)
        loop = asyncio.get_event_loop()
        doc = await loop.run_in_executor(
            None,
            lambda: processor.process_upload(content, file.filename, chunk_strategy=processing_strategy),
        )

        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in doc.chunks
        ]
        await vector_store.add_documents_async(chunks_data)
        hybrid_search_engine.add_documents(chunks_data)

        return {
            "success": True,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "chunk_count": len(doc.chunks),
            "message": f"文档 '{file.filename}' 处理完成，生成 {len(doc.chunks)} 个分块",
        }
    except Exception as e:
        logger.error(f"鏂囨。涓婁紶澶勭悊澶辫触: {e}")
        raise error_response(ErrorCode.UPLOAD_FAILED, str(e))


@router.post("/documents/upload-and-build-kg")
async def upload_and_build_kg(file: UploadFile = File(...)):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise error_response(ErrorCode.UNSUPPORTED_FORMAT, f"涓嶆敮鎸佺殑鏍煎紡: {ext}")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "鏂囦欢杩囧ぇ")

    try:
        doc = doc_processor.process_upload(content, file.filename)

        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in doc.chunks
        ]
        vector_store.add_documents(chunks_data)

        kg_result = await kg_builder.build_from_text(doc.content, doc.title)

        return {
            "success": True,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "chunk_count": len(doc.chunks),
            "kg_result": kg_result,
            "message": "文档处理并构建知识图谱完成",
        }
    except Exception as e:
        logger.error(f"鏂囨。涓婁紶+鏋勫缓鍥捐氨澶辫触: {e}")
        raise error_response(ErrorCode.UPLOAD_FAILED, str(e))


@router.post("/documents/convert")
async def convert_document(file: UploadFile = File(...), force_ocr: bool = Form(False)):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, f"鏂囦欢杩囧ぇ: {len(content)} 瀛楄妭 (鏈€澶?{settings.MAX_FILE_SIZE // (1024*1024)}MB)")

    loop = asyncio.get_event_loop()
    if force_ocr and file.filename.lower().endswith(".pdf"):
        result = await loop.run_in_executor(None, file_converter.convert_pdf_ocr, content, file.filename)
    else:
        result = await loop.run_in_executor(None, file_converter.convert, content, file.filename)

    if not result.success:
        raise error_response(ErrorCode.CONVERSION_FAILED, result.error)

    return {
        "success": True,
        "original_name": result.original_name,
        "markdown_content": result.markdown_content,
        "metadata": result.metadata,
    }


@router.post("/documents/convert-and-send-to-kb")
async def convert_and_send_to_kb(
    kb_id: str = Form(...),
    file: UploadFile = File(...),
    chunk_strategy: str = Form(""),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")

    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    content = await file.read()
    result = file_converter.convert(content, file.filename)
    if not result.success:
        raise error_response(ErrorCode.CONVERSION_FAILED, result.error)

    strategy = chunk_strategy or kb.chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
    size = chunk_size or kb.chunk_size or settings.CHUNK_SIZE
    overlap = chunk_overlap or kb.chunk_overlap or settings.CHUNK_OVERLAP

    try:
        md_filename = os.path.splitext(file.filename)[0] + ".md"
        if _should_use_layout_processor(strategy, file.filename):
            doc = layout_doc_processor.process_upload(
                content,
                file.filename,
                chunk_strategy=strategy,
                chunk_size=size,
                chunk_overlap=overlap,
                kb_id=kb_id,
            )
        else:
            text_strategy = _effective_text_strategy(strategy, md_filename)
            doc = doc_processor.process_upload(
                result.markdown_content.encode("utf-8"),
                md_filename,
                chunk_strategy=text_strategy,
                chunk_size=size,
                chunk_overlap=overlap,
                kb_id=kb_id,
            )

        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in doc.chunks
        ]
        await vector_store.add_documents_async(chunks_data)
        hybrid_search_engine.add_documents(chunks_data)
        kb_manager.update_counts(kb_id, doc_delta=1, chunk_delta=len(doc.chunks))

        chunk_meta = _detect_chunk_metadata(doc.chunks)
        kb_manager.save_document(
            kb_id, doc.doc_id, doc.content,
            title=doc.title,
            filename=file.filename,
            content_hash=file_hash,
            original_size=len(content),
            chunk_count=len(doc.chunks),
            detected_doc_type=chunk_meta["detected_doc_type"],
            chunk_strategy=chunk_meta["chunk_strategy"] or strategy,
        )

        if settings.use_supabase:
            from app.core.cloud_storage import cloud_storage
            original_path = f"knowledge_bases/{kb_id}/originals/{doc.doc_id}_{file.filename}"
            cloud_storage.upload_file(original_path, content)

        return {
            "success": True,
            "doc_id": doc.doc_id,
            "title": doc.title,
            "chunk_count": len(doc.chunks),
            "kb_id": kb_id,
            "original_name": result.original_name,
            "converted_length": len(result.markdown_content),
            "conversion_metadata": result.metadata,
            "message": f"文档 '{file.filename}' 已上传到知识库 '{kb.name}'，生成 {len(doc.chunks)} 个分块",
        }
    except Exception as e:
        logger.error(f"杞寲骞跺彂閫佸埌鐭ヨ瘑搴撳け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.post("/documents/convert-and-build-kg")
async def convert_and_build_kg(file: UploadFile = File(...), doc_key: Optional[str] = Form(None)):
    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    content = await file.read()
    result = file_converter.convert(content, file.filename)
    if not result.success:
        raise error_response(ErrorCode.CONVERSION_FAILED, result.error)

    try:
        title = os.path.splitext(file.filename)[0]
        effective_doc_key = doc_key or file.filename or title
        kg_result = await kg_builder.build_from_text(result.markdown_content, title, doc_key=effective_doc_key)

        return {
            "success": True,
            "original_name": result.original_name,
            "converted_length": len(result.markdown_content),
            "conversion_metadata": result.metadata,
            "kg_result": kg_result,
            "doc_key": effective_doc_key,
            "message": f"鏂囦欢 '{file.filename}' 宸茶浆鍖栧苟鏋勫缓鐭ヨ瘑鍥捐氨",
        }
    except Exception as e:
        logger.error(f"杞寲骞舵瀯寤虹煡璇嗗浘璋卞け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.post("/documents/convert-and-send-to-both")
async def convert_and_send_to_both(
    kb_id: str = Form(...),
    file: UploadFile = File(...),
    chunk_strategy: str = Form(""),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
    build_kg: bool = Form(True),
    doc_key: Optional[str] = Form(None),
):
    """API route."""
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")

    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    duplicate_doc = kb_manager.find_duplicate_document(kb_id, content_hash=file_hash, filename=file.filename)
    if duplicate_doc:
        return {
            "success": False,
            "duplicate": duplicate_doc.get("duplicate_reason") == "content_hash",
            "conflict": duplicate_doc.get("duplicate_reason") == "filename",
            "doc_id": duplicate_doc.get("doc_id", ""),
            "filename": duplicate_doc.get("original_filename") or duplicate_doc.get("filename") or file.filename,
            "message": f"知识库中已存在同名文档 '{file.filename}'，请先删除旧文档或重命名后再上传",
        }
    result = file_converter.convert(content, file.filename)
    if not result.success:
        raise error_response(ErrorCode.CONVERSION_FAILED, result.error)

    md_content = result.markdown_content
    title = os.path.splitext(file.filename)[0]
    md_filename = title + ".md"
    kb_result_info = {}
    kg_result_info = {}

    try:
        # 1. 鍙戦€佸埌鐭ヨ瘑搴?
        strategy = chunk_strategy or kb.chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
        size = chunk_size or kb.chunk_size or settings.CHUNK_SIZE
        overlap = chunk_overlap or kb.chunk_overlap or settings.CHUNK_OVERLAP

        if _should_use_layout_processor(strategy, file.filename):
            doc = layout_doc_processor.process_upload(
                content, file.filename,
                chunk_strategy=strategy, chunk_size=size, chunk_overlap=overlap, kb_id=kb_id,
            )
        else:
            text_strategy = _effective_text_strategy(strategy, md_filename)
            doc = doc_processor.process_upload(
                md_content.encode("utf-8"), md_filename,
                chunk_strategy=text_strategy, chunk_size=size, chunk_overlap=overlap, kb_id=kb_id,
            )

        chunks_data = [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "content": c.content, "metadata": c.metadata} for c in doc.chunks]
        await vector_store.add_documents_async(chunks_data)
        hybrid_search_engine.add_documents(chunks_data)
        kb_manager.update_counts(kb_id, doc_delta=1, chunk_delta=len(doc.chunks))
        chunk_meta = _detect_chunk_metadata(doc.chunks)
        kb_manager.save_document(
            kb_id, doc.doc_id, doc.content,
            title=doc.title,
            filename=file.filename,
            content_hash=file_hash,
            original_size=len(content),
            chunk_count=len(doc.chunks),
            detected_doc_type=chunk_meta["detected_doc_type"],
            chunk_strategy=chunk_meta["chunk_strategy"] or strategy,
        )

        kb_result_info = {"doc_id": doc.doc_id, "chunk_count": len(doc.chunks)}

        # 2. 鏋勫缓鐭ヨ瘑鍥捐氨
        if build_kg:
            effective_doc_key = doc_key or file.filename or title
            kg_result = await kg_builder.build_from_text(md_content, title, doc_key=effective_doc_key)
            kg_result_info = {"kg_result": kg_result, "doc_key": effective_doc_key}

        return {
            "success": True,
            "title": doc.title,
            "original_name": result.original_name,
            "converted_length": len(md_content),
            "kb_id": kb_id,
            "kb": kb_result_info,
            "kg": kg_result_info,
            "message": f"文件 '{file.filename}' 已转化并发送到知识库 '{kb.name}'，{kb_result_info.get('chunk_count', 0)} 个分块" +
                       (f"，并构建知识图谱（{kg_result_info.get('kg_result', {}).get('entity_count', 0)} 个实体）" if build_kg else ""),
        }
    except Exception as e:
        logger.error(f"杞寲骞跺彂閫佸け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.post("/knowledge-graph/build-folder-graph")
async def kg_build_folder_graph(kb_id: str = ""):
    """API route."""
    if not kb_id:
        raise error_response(ErrorCode.INVALID_PARAM, "璇锋寚瀹氱煡璇嗗簱ID")

    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")

    try:
        from app.kg.anchor_extractor import anchor_extractor, relation_builder

        # 鑾峰彇鐭ヨ瘑搴撲腑鎵€鏈夋枃妗?
        doc_list = kb_manager.list_documents(kb_id)
        if not doc_list:
            return {"success": True, "nodes": [], "edges": [], "message": "鐭ヨ瘑搴撲腑鏆傛棤鏂囨。"}

        doc_dir = kb_manager.get_doc_dir(kb_id)
        documents = []
        for doc_info in doc_list:
            doc_id = doc_info.get("doc_id", "")
            fname = doc_info.get("filename", f"{doc_id}.md")
            fpath = os.path.join(doc_dir, fname)
            if os.path.exists(fpath):
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                doc_title = doc_info.get("title", doc_id)
                documents.append({"title": doc_title, "content": content, "filename": fname, "doc_id": doc_id})

        if not documents:
            return {"success": True, "nodes": [], "edges": [], "message": "鐭ヨ瘑搴撲腑鏆傛棤鏂囨。"}

        # 绗竴姝ワ細LLM 鎻愮偧姣忕瘒鏂囨。鐨勮蹇嗛敋鐐?
        profiles = []
        for doc_info in documents:
            try:
                profile = await anchor_extractor.extract_profile(
                    doc_id=doc_info["doc_id"],
                    title=doc_info["title"],
                    content=doc_info["content"],
                    filename=doc_info["filename"],
                )
                profiles.append(profile)
                logger.info(f"文档 '{doc_info['title']}' 提取了 {len(profile.anchors)} 个记忆锚点")
            except Exception as e:
                logger.warning(f"鏂囨。 '{doc_info['title']}' 閿氱偣鎻愬彇澶辫触: {e}")

        # 绗簩姝ワ細鍩轰簬閿氱偣鏋勫缓鏂囨。闂村叧绯?
        doc_relations = await relation_builder.build_relations(profiles)

        # 绗笁姝ワ細鏋勫缓鍥捐氨
        folder_doc_key = f"folder:{kb_id}"
        folder_graph = kg_store.get_or_create_doc_graph(folder_doc_key)
        folder_graph.clear()

        # 鍒涘缓鏂囨。鑺傜偣
        doc_node_map = {}
        for profile in profiles:
            doc_node = folder_graph.add_node(
                label=profile.doc_title,
                node_type="document",
                properties={
                    "filename": profile.filename,
                    "doc_id": profile.doc_id,
                    "content_preview": profile.summary or "",
                    "content_length": profile.content_length,
                    "kb_id": kb_id,
                    "topic_tags": ",".join(profile.topic_tags),
                    "anchor_count": len(profile.anchors),
                },
            )
            doc_node_map[profile.doc_id] = doc_node

            # 鍒涘缓閿氱偣鑺傜偣骞惰繛鎺ュ埌鏂囨。
            for anchor in profile.anchors:
                anchor_node = folder_graph.add_node(
                    label=anchor.content[:60],
                    node_type="anchor",
                    properties={
                        "anchor_type": anchor.anchor_type,
                        "doc_title": profile.doc_title,
                        "keywords": ",".join(anchor.keywords),
                        "content": anchor.content,
                    },
                )
                # 閿氱偣棰滆壊鏍规嵁绫诲瀷
                anchor_colors = {
                    "core_topic": "#3b82f6",
                    "key_argument": "#8b5cf6",
                    "key_entity": "#f59e0b",
                    "method_conclusion": "#10b981",
                    "data_finding": "#ef4444",
                }
                folder_graph.add_edge(
                    source_id=doc_node.id,
                    target_id=anchor_node.id,
                    relation_type="has_anchor",
                    weight=0.9,
                    properties={
                        "style": "solid",
                        "color": anchor_colors.get(anchor.anchor_type, "#6b7280"),
                    },
                )

        # 鍒涘缓鏂囨。闂村叧绯昏竟
        for rel in doc_relations:
            source_node = doc_node_map.get(rel.source_doc_id)
            target_node = doc_node_map.get(rel.target_doc_id)
            if not source_node or not target_node:
                continue

            rel_style = relation_builder.RELATION_TYPES.get(rel.relation_type, {
                "style": "dashed", "color": "#6b7280", "label": "鍏宠仈"
            })
            folder_graph.add_edge(
                source_id=source_node.id,
                target_id=target_node.id,
                relation_type=rel.relation_type,
                weight=rel.weight,
                properties={
                    "style": rel_style.get("style", "dashed"),
                    "color": rel_style.get("color", "#6b7280"),
                    "reason": rel.reason,
                    "label": rel_style.get("label", "鍏宠仈"),
                },
            )

        kg_store._save_doc(folder_doc_key)

        nodes = folder_graph.get_all_nodes()
        edges = folder_graph.get_all_edges()

        # 缁熻鍏崇郴绫诲瀷
        relation_type_counts = {}
        for rel in doc_relations:
            relation_type_counts[rel.relation_type] = relation_type_counts.get(rel.relation_type, 0) + 1

        return {
            "success": True,
            "doc_key": folder_doc_key,
            "document_count": len(profiles),
            "anchor_count": sum(len(p.anchors) for p in profiles),
            "relation_count": len(doc_relations),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relation_types": relation_type_counts,
            "profiles": [p.to_dict() for p in profiles],
            "message": f"已构建文档关系图谱：{len(profiles)} 篇文档，{sum(len(p.anchors) for p in profiles)} 个记忆锚点，{len(doc_relations)} 条关联关系",
        }
    except Exception as e:
        logger.error(f"鏂囦欢澶瑰浘璋辨瀯寤哄け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.get("/knowledge-graph/node-content/{node_id}")
async def kg_get_node_content(node_id: str, doc_key: str = ""):
    """API route."""
    try:
        if doc_key:
            graph = kg_store.get_doc_graph(doc_key)
        else:
            graph = kg_store.get_doc_graph("__global__")

        if not graph:
            return {"success": False, "content": "", "message": "图谱不存在"}

        # 鏌ユ壘鑺傜偣
        node = graph._nodes.get(node_id)
        if not node:
            # 灏濊瘯鐢?get_all_nodes 鏌ユ壘
            for n in graph.get_all_nodes():
                if n.id == node_id:
                    node = n
                    break

        if not node:
            # 鍦ㄦ墍鏈夋枃妗ｅ浘璋变腑鎼滅储
            for dk, g in kg_store._doc_graphs.items():
                if g and node_id in g._nodes:
                    node = g._nodes[node_id]
                    doc_key = dk
                    break

        if not node:
            all_node_ids = [n.id for n in graph.get_all_nodes()[:5]]
            logger.warning(f"鑺傜偣 {node_id} 涓嶅瓨鍦? doc_key={doc_key}, graph nodes(sample)={all_node_ids}")
            return {"success": False, "content": "", "message": "节点不存在"}

        result = {
            "success": True,
            "node_id": node_id,
            "label": node.label,
            "node_type": node.node_type,
            "properties": node.properties or {},
            "doc_key": doc_key,
        }

        # 濡傛灉鏄枃妗ｇ被鍨嬭妭鐐癸紝杩斿洖鍐呭棰勮
        if node.node_type == "document" and node.properties:
            result["content_preview"] = node.properties.get("content_preview", "")
            result["filename"] = node.properties.get("filename", "")
            result["kb_id"] = node.properties.get("kb_id", "")

            # 濡傛灉鏈塳b_id鍜宖ilename锛屽皾璇曡鍙栧畬鏁村唴瀹?
            kb_id = node.properties.get("kb_id", "")
            filename = node.properties.get("filename", "")
            if kb_id:
                doc_dir = kb_manager.get_doc_dir(kb_id)
                # 灏濊瘯澶氱鏂囦欢鍚嶅尮閰?
                possible_paths = []
                if filename:
                    possible_paths.append(os.path.join(doc_dir, filename))
                doc_id = node.properties.get("doc_id", "")
                if doc_id:
                    possible_paths.append(os.path.join(doc_dir, f"{doc_id}.md"))
                for doc_path in possible_paths:
                    if os.path.exists(doc_path):
                        with open(doc_path, 'r', encoding='utf-8') as f:
                            result["full_content"] = f.read()
                        break

        # 鑾峰彇鑺傜偣鐨勫叧鑱旇妭鐐?
        related_nodes = []
        for edge in graph.get_all_edges():
            if edge.source == node_id:
                target = graph._nodes.get(edge.target)
                if target:
                    related_nodes.append({"label": target.label, "type": target.node_type, "relation": edge.relation_type, "direction": "outgoing"})
            elif edge.target == node_id:
                source = graph._nodes.get(edge.source)
                if source:
                    related_nodes.append({"label": source.label, "type": source.node_type, "relation": edge.relation_type, "direction": "incoming"})

        result["related_nodes"] = related_nodes[:20]

        return result
    except Exception as e:
        logger.error(f"鑾峰彇鑺傜偣鍐呭澶辫触: {e}")
        return {"success": False, "content": "", "message": str(e)}


# ==================== Query ====================

@router.post("/query")
async def query(request: QueryRequest):
    if not request.question.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "闂涓嶈兘涓虹┖")

    try:
        if request.mode == "kg":
            result = await kg_rag_service.query(request.question)
        else:
            result = await rag_service.query(request.question)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"鏌ヨ澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


# ==================== RAG ====================

@router.post("/rag/rewrite-query")
async def rag_rewrite_query(request: RewriteQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        rewritten = await rag_service.rewrite_query(request.query, session_id=request.session_id or None)
        return {"success": True, "original": request.query, "rewritten": rewritten}
    except Exception as e:
        logger.error(f"鏌ヨ鏀瑰啓澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.get("/rag/stats")
async def rag_stats():
    return {
        "document_count": vector_store.count,
    }


@router.get("/rag/status")
async def rag_status():
    bm25_stats = hybrid_search_engine.get_stats()
    index_models = sorted({
        str(meta.get("embedding_model"))
        for meta in (getattr(vector_store, "_metadata", []) or [])
        if isinstance(meta, dict) and meta.get("embedding_model")
    })
    current_embedding_model = getattr(embedding_service, "model", settings.EMBEDDING_MODEL)
    current_embedding_dimension = getattr(embedding_service, "dimension", settings.EMBEDDING_DIMENSION)
    index_model_status = vector_store.index_model_status(current_embedding_model, current_embedding_dimension)
    return {
        "backend": {
            "online": True,
            "version": settings.APP_VERSION,
            "storage_mode": "supabase" if settings.use_supabase else "local",
        },
        "llm": {
            "configured": bool(settings.OPENAI_API_KEY),
            "available": bool(getattr(rag_service, "_glm", None)),
            "model": settings.OPENAI_MODEL,
            "base_url": settings.OPENAI_BASE_URL,
        },
        "embedding": {
            "model": getattr(embedding_service, "model", settings.EMBEDDING_MODEL),
            "dimension": getattr(embedding_service, "dimension", settings.EMBEDDING_DIMENSION),
            "api_client_ready": bool(getattr(embedding_service, "_client", None) or getattr(embedding_service, "_gemini_api_key", "")),
            "using_local_model": embedding_service.using_local_model,
            "degraded_to_hash": embedding_service.is_degraded,
            "provider": embedding_service.provider,
            "last_error": embedding_service.last_error,
            "index_embedding_models": index_models,
            "index_embedding_dimensions": index_model_status.get("dimensions", []),
            "index_rebuild_required": not index_model_status["consistent"] if index_model_status.get("total_count", 0) else False,
            "index_model_status": index_model_status,
        },
        "vector_store": {
            "count": vector_store.count,
            "backend": getattr(vector_store, "backend_status", {}).get("backend", "local"),
            "milvus_ready": getattr(vector_store, "backend_status", {}).get("ready", False),
            "milvus_collection": getattr(vector_store, "backend_status", {}).get("collection", ""),
            "milvus_error": getattr(vector_store, "backend_status", {}).get("error", ""),
            "storage_file": getattr(vector_store, "_storage_file", ""),
            "metadata_file": getattr(vector_store, "_meta_file", ""),
            "faiss_ready": bool(getattr(vector_store, "_faiss_index", None)),
            "active_query_backend": getattr(vector_store, "backend_status", {}).get("backend", "local"),
        },
        "hybrid_search": {
            "bm25_stats": bm25_stats,
            "bm25_ready": bm25_stats.get("corpus_size", 0) > 0,
            "vector_weight": settings.HYBRID_SEARCH_WEIGHT_VECTOR,
            "keyword_weight": settings.HYBRID_SEARCH_WEIGHT_KEYWORD,
        },
        "reranker": {
            "enabled": settings.RERANK_ENABLED,
            "enhanced_ready": bool(getattr(rag_service, "_enhanced_reranker", None)),
            "llm_rerank_enabled": settings.RERANK_USE_LLM,
            "remote_configured": bool(getattr(getattr(rag_service, "_reranker", None), "_remote_config", {})),
            "remote_model": (getattr(getattr(rag_service, "_reranker", None), "_remote_config", {}) or {}).get("model", ""),
        },
        "chunking": {
            "strategy_default": settings.CHUNK_STRATEGY_DEFAULT,
            "chunk_size": settings.CHUNK_SIZE,
            "chunk_overlap": settings.CHUNK_OVERLAP,
            "child_chunk_size": settings.CHILD_CHUNK_SIZE,
            "child_chunk_min_size": settings.CHILD_CHUNK_MIN_SIZE,
            "child_chunk_max_size": settings.CHILD_CHUNK_MAX_SIZE,
            "child_chunk_overlap": settings.CHILD_CHUNK_OVERLAP,
        },
        "retrieval": {
            "top_k_default": settings.RAG_TOP_K,
            "retrieval_min_k": settings.RAG_RETRIEVAL_MIN_K,
            "retrieval_multiplier": settings.RAG_RETRIEVAL_MULTIPLIER,
            "query_rewrite_enabled": settings.QUERY_REWRITE_ENABLED,
            "query_expansion_enabled": settings.QUERY_EXPANSION_ENABLED,
            "query_rewrite_min_overlap": settings.QUERY_REWRITE_MIN_OVERLAP,
        },
    }


@router.post("/rag/query")
async def rag_query(request: RAGQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        # P5: 鏀寔澶氱煡璇嗗簱鑱斿悎妫€绱?
        kb_ids = request.kb_ids or []
        if request.kb_id and request.kb_id not in kb_ids:
            kb_ids.insert(0, request.kb_id)

        if len(kb_ids) > 1:
            # 澶氬簱鑱斿悎妫€绱細閫愬簱妫€绱㈠悗鍚堝苟缁撴灉锛屽啀璧?LLM 鐢熸垚鍥炵瓟
            all_results = []
            all_images = []
            seen_images = set()
            for kid in kb_ids:
                try:
                    r = await rag_service.query(
                        request.query,
                        mode=request.mode,
                        kb_id=kid,
                        top_k=request.top_k or settings.RAG_TOP_K,
                        enable_web_search=False,
                        session_id=request.session_id or None,
                    )
                    if r and r.get("sources"):
                        for src in r["sources"]:
                            src["kb_id"] = kid
                        all_results.extend(r["sources"])
                    for image in (r or {}).get("images") or []:
                        image_key = image.get("url") if isinstance(image, dict) else ""
                        if image_key and image_key not in seen_images:
                            seen_images.add(image_key)
                            all_images.append(image)
                except Exception:
                    pass
            # 鎸夊垎鏁版帓搴忥紝鍙?top_k
            all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            top_results = all_results[:request.top_k or settings.RAG_TOP_K]
            # 鎸夌煡璇嗗簱鍒嗙粍
            grouped = {}
            for r in top_results:
                kid = r.get("kb_id", "unknown")
                grouped.setdefault(kid, []).append(r)

            # 鏋勫缓涓婁笅鏂囧苟璋冪敤 LLM 鐢熸垚鍥炵瓟
            context = rag_service._build_context(top_results) if top_results else ""
            answer = await rag_service._generate_answer(
                request.query, context,
                has_kb_results=bool(top_results),
                has_web=False,
                citation_count=min(len(top_results), request.top_k or settings.RAG_TOP_K),
            )

            return {
                "answer": answer,
                "sources": top_results,
                "chunks": top_results,
                "images": all_images[:12],
                "grouped_sources": grouped,
                "mode": request.mode,
                "kb_ids": kb_ids,
                "metadata": {
                    "has_results": bool(top_results),
                    "result_count": len(top_results),
                    "multi_kb": True,
                },
            }
        else:
            result = await rag_service.query(
                request.query,
                mode=request.mode,
                kb_id=request.kb_id or "",
                top_k=request.top_k or settings.RAG_TOP_K,
                enable_web_search=request.enable_web_search or False,
                session_id=request.session_id or None,
            )
            return result
    except Exception as e:
        logger.error(f"RAG鏌ヨ澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.post("/rag/query/stream")
async def rag_query_stream(request: RAGQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "查询不能为空")

    async def event_generator():
        try:
            kb_ids = request.kb_ids or []
            if request.kb_id and request.kb_id not in kb_ids:
                kb_ids.insert(0, request.kb_id)
            async for event in rag_service.stream_query(
                request.query,
                mode=request.mode,
                kb_id=request.kb_id or "",
                top_k=request.top_k or settings.RAG_TOP_K,
                enable_web_search=request.enable_web_search or False,
                session_id=request.session_id or None,
                kb_ids=kb_ids or None,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"RAG stream query failed: {e}")
            payload = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/rag/chunks/{chunk_id}")
async def rag_get_chunk(chunk_id: str):
    for i, cid in enumerate(vector_store._chunk_ids):
        if cid == chunk_id:
            return {
                "chunk_id": cid,
                "doc_id": vector_store._doc_ids[i],
                "content": vector_store._texts[i],
                "metadata": vector_store._metadata[i],
            }
        raise error_response(ErrorCode.FILE_NOT_FOUND, "文件不存在")


@router.post("/rag/web-search")
async def rag_web_search(request: RewriteQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        from app.rag.web_search import web_search_service
        results = await web_search_service.search(request.query, max_results=3)
        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"鑱旂綉鎼滅储澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.post("/rag/session/create")
async def create_rag_session():
    from app.rag.conversation_memory import conversation_memory
    session_id = conversation_memory.create_session()
    return {"success": True, "session_id": session_id}


@router.get("/rag/session/{session_id}/history")
async def get_rag_session_history(session_id: str):
    from app.rag.conversation_memory import conversation_memory
    history = conversation_memory.get_history(session_id)
    if history is None:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "会话不存在或已过期")
    return {"success": True, "session_id": session_id, "history": history}


@router.delete("/rag/session/{session_id}")
async def delete_rag_session(session_id: str):
    from app.rag.conversation_memory import conversation_memory
    success = conversation_memory.clear_session(session_id)
    if not success:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "文件不存在")
    return {"success": True}


@router.get("/rag/sessions")
async def list_rag_sessions():
    from app.rag.conversation_memory import conversation_memory
    sessions = conversation_memory.list_sessions()
    return {"success": True, "sessions": sessions}


# ==================== Knowledge Graph ====================

@router.get("/knowledge-graph/stats")
async def kg_stats():
    # P8: 澧炲己缁熻淇℃伅
    all_nodes = []
    doc_graph_count = 0
    try:
        all_nodes = kg_store.get_all_nodes() or []
    except Exception:
        pass
    try:
        doc_graph_count = len([g for g in kg_store._doc_graphs.values() if g is not None])
    except Exception:
        pass
    type_counts = {}
    for n in all_nodes:
        t = getattr(n, 'node_type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + 1
    node_count = 0
    edge_count = 0
    try:
        node_count = kg_store.node_count or 0
    except Exception:
        pass
    try:
        edge_count = kg_store.edge_count or 0
    except Exception:
        pass
    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "doc_graph_count": doc_graph_count,
        "node_types": type_counts,
    }


@router.get("/knowledge-graph/doc-graphs")
async def kg_list_doc_graphs():
    graphs = kg_store.list_doc_graphs()
    return {"success": True, "graphs": graphs}


@router.get("/knowledge-graph/doc-graph/{doc_key:path}")
async def kg_get_doc_graph(doc_key: str):
    doc_graph = kg_store.get_doc_graph(doc_key)
    if not doc_graph:
        return {"success": True, "nodes": [], "edges": [], "doc_key": doc_key, "cached": False}

    nodes = doc_graph.get_all_nodes()
    edges = doc_graph.get_all_edges()

    connection_count = {}
    for edge in edges:
        connection_count[edge.source] = connection_count.get(edge.source, 0) + 1
        connection_count[edge.target] = connection_count.get(edge.target, 0) + 1

    nodes_data = []
    node_id_to_label = {}
    for n in nodes:
        nd = n.to_dict()
        nd["connections"] = connection_count.get(n.id, 0)
        nodes_data.append(nd)
        node_id_to_label[n.id] = n.label

    edges_data = []
    for e in edges:
        ed = e.to_dict()
        ed["source_label"] = node_id_to_label.get(e.source, "")
        ed["target_label"] = node_id_to_label.get(e.target, "")
        edges_data.append(ed)

    return {
        "success": True,
        "nodes": nodes_data,
        "edges": edges_data,
        "doc_key": doc_key,
        "cached": True,
        "node_count": doc_graph.node_count,
        "edge_count": doc_graph.edge_count,
    }


@router.delete("/knowledge-graph/doc-graph/{doc_key:path}")
async def kg_delete_doc_graph(doc_key: str):
    success = kg_store.delete_doc_graph(doc_key)
    return {"success": success}


@router.get("/knowledge-graph/nodes")
async def kg_get_nodes(search: Optional[str] = None, limit: int = 100, node_type: Optional[str] = None):
    if search:
        nodes = kg_store.search_nodes(search, limit=limit)
    else:
        nodes = kg_store.get_all_nodes()[:limit]
    # P8: 鏀寔鎸夌被鍨嬭繃婊?
    if node_type:
        nodes = [n for n in nodes if getattr(n, 'node_type', None) == node_type]
    return {"nodes": [n.to_dict() for n in nodes]}


@router.get("/knowledge-graph/edges")
async def kg_get_edges(limit: int = 200):
    edges = kg_store.get_all_edges()[:limit]
    return {"edges": [e.to_dict() for e in edges]}


@router.get("/knowledge-graph/graph")
async def kg_get_graph(doc_key: Optional[str] = None):
    if doc_key:
        doc_graph = kg_store.get_doc_graph(doc_key)
        if not doc_graph:
            return {"nodes": [], "edges": [], "doc_key": doc_key}
        nodes = doc_graph.get_all_nodes()
        edges = doc_graph.get_all_edges()
    else:
        nodes = kg_store.get_all_nodes()
        edges = kg_store.get_all_edges()

    connection_count = {}
    for edge in edges:
        connection_count[edge.source] = connection_count.get(edge.source, 0) + 1
        connection_count[edge.target] = connection_count.get(edge.target, 0) + 1

    nodes_data = []
    node_id_to_label = {}
    for n in nodes:
        nd = n.to_dict()
        nd["connections"] = connection_count.get(n.id, 0)
        nodes_data.append(nd)
        node_id_to_label[n.id] = n.label

    edges_data = []
    for e in edges:
        ed = e.to_dict()
        ed["source_label"] = node_id_to_label.get(e.source, "")
        ed["target_label"] = node_id_to_label.get(e.target, "")
        edges_data.append(ed)

    return {
        "success": True,
        "nodes": nodes_data,
        "edges": edges_data,
        "doc_key": doc_key,
    }


@router.get("/knowledge-graph/subgraph/{node_id}")
async def kg_get_subgraph(node_id: str, depth: int = 2):
    nodes, edges = kg_store.get_subgraph(node_id, depth)

    connection_count = {}
    for edge in edges:
        connection_count[edge.source] = connection_count.get(edge.source, 0) + 1
        connection_count[edge.target] = connection_count.get(edge.target, 0) + 1

    nodes_data = []
    for n in nodes:
        nd = n.to_dict()
        nd["connections"] = connection_count.get(n.id, 0)
        nodes_data.append(nd)

    return {
        "nodes": nodes_data,
        "edges": [e.to_dict() for e in edges],
    }


@router.get("/knowledge-graph/reasoning-path/{node_id}")
async def kg_get_reasoning_path(node_id: str, max_depth: int = 3):
    visited_nodes = {node_id}
    visited_edges = set()
    path = []
    frontier = [node_id]

    for step in range(max_depth):
        if not frontier:
            break
        new_frontier = []
        step_edges = []
        for nid in frontier:
            for edge in kg_store.get_all_edges():
                if edge.id in visited_edges:
                    continue
                if edge.source == nid and edge.target not in visited_nodes:
                    visited_nodes.add(edge.target)
                    new_frontier.append(edge.target)
                    visited_edges.add(edge.id)
                    step_edges.append({
                        "edge": edge.to_dict(),
                        "step": step + 1,
                    })
                elif edge.target == nid and edge.source not in visited_nodes:
                    visited_nodes.add(edge.source)
                    new_frontier.append(edge.source)
                    visited_edges.add(edge.id)
                    step_edges.append({
                        "edge": edge.to_dict(),
                        "step": step + 1,
                    })
                elif edge.source == nid or edge.target == nid:
                    if edge.id not in visited_edges:
                        visited_edges.add(edge.id)
                        step_edges.append({
                            "edge": edge.to_dict(),
                            "step": step + 1,
                        })
        path.append({
            "step": step + 1,
            "new_nodes": list(new_frontier),
            "edges": step_edges,
        })
        frontier = new_frontier

    nodes_data = []
    for nid in visited_nodes:
        node = kg_store.get_node(nid)
        if node:
            nodes_data.append(node.to_dict())

    return {
        "start_node": node_id,
        "path": path,
        "nodes": nodes_data,
        "total_steps": len(path),
    }


@router.post("/knowledge-graph/nodes")
async def kg_create_node(request: KGNodeCreate):
    node = kg_store.add_node(label=request.label, node_type=request.node_type, properties=request.properties)
    return {"success": True, "node": node.to_dict()}


@router.post("/knowledge-graph/edges")
async def kg_create_edge(request: KGEdgeCreate):
    edge = kg_store.add_edge(
        source_id=request.source_id,
        target_id=request.target_id,
        relation_type=request.relation_type,
        weight=request.weight,
        properties=request.properties,
    )
    if not edge:
        raise error_response(ErrorCode.NODE_NOT_FOUND, "源节点或目标节点不存在")
    return {"success": True, "edge": edge.to_dict()}


@router.post("/knowledge-graph/rewrite-query")
async def kg_rewrite_query(request: RewriteQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        rewritten = await kg_rag_service.rewrite_query(request.query)
        return {"success": True, "original": request.query, "rewritten": rewritten}
    except Exception as e:
        logger.error(f"KG鏌ヨ鏀瑰啓澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.delete("/knowledge-graph/nodes/{node_id}")
async def kg_delete_node(node_id: str):
    if node_id == "all":
        for key in list(kg_store._doc_graphs.keys()):
            kg_store.delete_doc_graph(key)
        return {"success": True, "message": "鎵€鏈夊浘璋卞凡娓呯┖"}
    for graph in kg_store._doc_graphs.values():
        if graph.delete_node(node_id):
            kg_store._save_doc(graph.doc_key)
            return {"success": True}
    raise error_response(ErrorCode.NODE_NOT_FOUND, "节点不存在")


@router.post("/knowledge-graph/build")
async def kg_build(request: KGBuildRequest):
    if not request.text.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏂囨湰涓嶈兘涓虹┖")

    try:
        doc_key = request.doc_key or request.title or ""
        result = await kg_builder.build_from_text(
            request.text,
            request.title or "",
            request.struct_tags or "",
            doc_key=doc_key,
        )
        extraction = await kg_builder.extract_from_text(
            request.text,
            request.title or "",
            request.struct_tags or "",
        )
        triples = []
        for rel in extraction.get("relations", []):
            triples.append({
                "subject": rel.get("head", ""),
                "relation": rel.get("relation", ""),
                "object": rel.get("tail", ""),
            })

        doc_graph = kg_store.get_doc_graph(doc_key) if doc_key else kg_store.get_doc_graph("__global__")
        if doc_graph:
            nodes = [n.to_dict() for n in doc_graph.get_all_nodes()]
            edges = [e.to_dict() for e in doc_graph.get_all_edges()]
        else:
            nodes = []
            edges = []

        return {"success": True, "triples": triples, "nodes": nodes, "edges": edges, "doc_key": doc_key, **result}
    except Exception as e:
        logger.error(f"鐭ヨ瘑鍥捐氨鏋勫缓澶辫触: {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.post("/knowledge-graph/extract")
async def kg_extract(request: KGBuildRequest):
    if not request.text.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏂囨湰涓嶈兘涓虹┖")

    try:
        result = await kg_builder.extract_from_text(
            request.text,
            request.title or "",
            request.struct_tags or "",
        )
        triples = []
        for rel in result.get("relations", []):
            triples.append({
                "subject": rel.get("head", ""),
                "relation": rel.get("relation", ""),
                "object": rel.get("tail", ""),
            })
        return {"success": True, "triples": triples, **result}
    except Exception as e:
        logger.error(f"涓夊厓缁勬娊鍙栧け璐? {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


@router.post("/kg/rewrite-query")
async def kg_rewrite_query(request: RewriteQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        rewritten = await rag_service.rewrite_query(request.query)
        return {"success": True, "original": request.query, "rewritten": rewritten}
    except Exception as e:
        logger.error(f"鏌ヨ鏀瑰啓澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.post("/knowledge-graph/query")
async def kg_query(request: KGQueryRequest):
    if not request.query.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "鏌ヨ涓嶈兘涓虹┖")

    try:
        result = await kg_rag_service.query(request.query)
        return {"answer": result.get("answer", ""), **result}
    except Exception as e:
        logger.error(f"鐭ヨ瘑鍥捐氨鏌ヨ澶辫触: {e}")
        raise error_response(ErrorCode.QUERY_FAILED, str(e))


@router.post("/evaluate")
async def evaluate_rag(request: EvalRequest):
    if not request.question.strip() or not request.answer.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "知识库名称不能为空")

    try:
        result = await rag_evaluator.evaluate(
            question=request.question,
            answer=request.answer,
            contexts=request.contexts,
            ground_truth=request.ground_truth or None,
        )
        return {"success": True, "evaluation": result}
    except Exception as e:
        logger.error(f"RAG璇勪及澶辫触: {e}")
        raise error_response(ErrorCode.PROCESS_FAILED, str(e))


# ==================== P9: Smart Tags ====================

@router.post("/kb/{kb_id}/auto-tag")
async def auto_tag_documents(kb_id: str):
    """API route."""
    import re
    kb = kb_manager.get(kb_id)
    if not kb:
        raise error_response(ErrorCode.KB_NOT_FOUND, "鐭ヨ瘑搴撲笉瀛樺湪")
    kb_dir = os.path.join(settings.DATA_DIR, "knowledge_bases", kb_id, "documents")
    if not os.path.isdir(kb_dir):
        return {"success": True, "tags": {}}
    # 鍏抽敭璇?鏍囩鏄犲皠
    tag_rules = {
        "技术": ["python", "javascript", "api", "编程", "开发", "框架", "算法", "数据库", "服务器", "docker", "linux", "git"],
        "学术": ["论文", "研究", "实验", "分析", "模型", "理论", "引用", "参考文献", "abstract", "conclusion"],
        "产品": ["需求", "用户", "体验", "设计", "功能", "迭代", "版本", "roadmap", "prd"],
        "管理": ["项目", "计划", "进度", "团队", "会议", "总结", "汇报", "okr", "kpi"],
        "学习": ["笔记", "教程", "入门", "指南", "总结", "复习", "考试", "课程"],
        "生活": ["日记", "旅行", "美食", "健身", "读书", "电影", "音乐"],
    }
    doc_tags = {}
    for fname in os.listdir(kb_dir):
        fpath = os.path.join(kb_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(5000).lower()
        except Exception:
            continue
        tags = []
        for tag, keywords in tag_rules.items():
            if any(kw in content for kw in keywords):
                tags.append(tag)
        if tags:
            doc_tags[fname] = tags
    return {"success": True, "tags": doc_tags, "count": len(doc_tags)}


# ==================== P10: Backup ====================

@router.post("/backup/create")
async def create_backup():
    """API route."""
    import zipfile
    import tempfile
    data_dir = settings.DATA_DIR
    if not os.path.isdir(data_dir):
        raise error_response(ErrorCode.FILE_NOT_FOUND, "文件不存在")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    try:
        with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(data_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    arcname = os.path.relpath(fpath, data_dir)
                    zf.write(fpath, arcname)
    except Exception as e:
        os.unlink(tmp.name)
        raise error_response(ErrorCode.INTERNAL_ERROR, f"澶囦唤澶辫触: {e}")
    return FileResponse(tmp.name, media_type='application/zip', filename=f'knowledge-hub-backup-{int(time.time())}.zip')


@router.post("/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    """API route."""
    import zipfile
    import io
    data_dir = settings.DATA_DIR
    content = await file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            zf.extractall(data_dir)
    except zipfile.BadZipFile:
        raise error_response(ErrorCode.INVALID_PARAM, "无效的备份文件")
    return {"success": True, "message": "鏁版嵁鎭㈠鎴愬姛锛岃閲嶅惎鏈嶅姟"}


# ==================== P16: Encryption ====================

@router.post("/encrypt/text")
async def encrypt_text(request: dict):
    """API route."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise error_response(ErrorCode.INTERNAL_ERROR, "鍔犲瘑妯″潡鏈畨瑁咃紝璇峰畨瑁?cryptography")
    text = request.get("text", "")
    if not text:
        raise error_response(ErrorCode.INVALID_PARAM, "鏂囨湰涓嶈兘涓虹┖")
    key = settings.SECRET_KEY.encode()[:32]
    import base64
    fernet_key = base64.urlsafe_b64encode(key.ljust(32, b'0')[:32])
    f = Fernet(fernet_key)
    encrypted = f.encrypt(text.encode()).decode()
    return {"success": True, "encrypted": encrypted}


@router.post("/decrypt/text")
async def decrypt_text(request: dict):
    """API route."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise error_response(ErrorCode.INTERNAL_ERROR, "鍔犲瘑妯″潡鏈畨瑁咃紝璇峰畨瑁?cryptography")
    encrypted = request.get("encrypted", "")
    if not encrypted:
        raise error_response(ErrorCode.INVALID_PARAM, "鍔犲瘑鏂囨湰涓嶈兘涓虹┖")
    key = settings.SECRET_KEY.encode()[:32]
    import base64
    fernet_key = base64.urlsafe_b64encode(key.ljust(32, b'0')[:32])
    f = Fernet(fernet_key)
    try:
        decrypted = f.decrypt(encrypted.encode()).decode()
    except Exception:
        raise error_response(ErrorCode.INVALID_PARAM, "瑙ｅ瘑澶辫触锛屽瘑閽ヤ笉鍖归厤")
    return {"success": True, "decrypted": decrypted}


# ==================== Async Upload (O1) ====================

from app.core.task_tracker import task_tracker, TaskStatus


def _process_upload_task(task_id: str, content: bytes, filename: str, kb_id: str = "",
                         chunk_strategy: str = "", chunk_size: int = 0, chunk_overlap: int = 0):
    """API route."""



    try:
        task_tracker.update_task(task_id, status=TaskStatus.PROCESSING, progress=10,
                                 message="姝ｅ湪杞崲鏂囨。鏍煎紡...")
        file_hash = hashlib.sha256(content).hexdigest()
        if kb_id:
            duplicate_doc = kb_manager.find_duplicate_document(kb_id, content_hash=file_hash, filename=filename)
            if duplicate_doc:
                task_tracker.update_task(
                    task_id, status=TaskStatus.COMPLETED, progress=100,
                    message=f"文档 '{filename}' 已存在，已跳过重复上传",
                    result={
                        "duplicate": True,
                        "doc_id": duplicate_doc.get("doc_id", ""),
                        "filename": duplicate_doc.get("original_filename") or duplicate_doc.get("filename") or filename,
                        "kb_id": kb_id,
                    },
                )
                return

        strategy = chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
        size = chunk_size or settings.CHUNK_SIZE
        overlap = chunk_overlap or settings.CHUNK_OVERLAP
        use_layout_processor = _should_use_layout_processor(strategy, filename)
        processor = layout_doc_processor if use_layout_processor else doc_processor
        processing_strategy = strategy if use_layout_processor else _effective_text_strategy(strategy, filename)

        task_tracker.update_task(task_id, progress=30, message="姝ｅ湪鍒嗗潡澶勭悊...")
        doc = processor.process_upload(
            content, filename,
            chunk_strategy=processing_strategy,
            chunk_size=size,
            chunk_overlap=overlap,
            kb_id=kb_id,
        )

        task_tracker.update_task(task_id, progress=60, message="姝ｅ湪鍚戦噺鍖?..")
        chunks_data = [
            {
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in doc.chunks
        ]

        task_tracker.update_task(task_id, progress=80, message="姝ｅ湪鍏ュ簱...")
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(vector_store.add_documents_async(chunks_data))
        finally:
            loop.close()
        hybrid_search_engine.add_documents(chunks_data)

        if kb_id:
            kb_manager.update_counts(kb_id, doc_delta=1, chunk_delta=len(doc.chunks))
            chunk_meta = _detect_chunk_metadata(doc.chunks)
            kb_manager.save_document(
                kb_id, doc.doc_id, doc.content,
                title=doc.title,
                filename=filename,
                content_hash=file_hash,
                original_size=len(content),
                chunk_count=len(doc.chunks),
                detected_doc_type=chunk_meta["detected_doc_type"],
                chunk_strategy=chunk_meta["chunk_strategy"] or strategy,
            )

        task_tracker.update_task(
            task_id, status=TaskStatus.COMPLETED, progress=100,
            message=f"文档 '{filename}' 处理完成，生成 {len(doc.chunks)} 个分块",
            result={
                "doc_id": doc.doc_id,
                "title": doc.title,
                "chunk_count": len(doc.chunks),
                "kb_id": kb_id,
            }
        )
        logger.info(f"寮傛涓婁紶浠诲姟瀹屾垚: {task_id}, chunks={len(doc.chunks)}")
    except Exception as e:
        task_tracker.update_task(
            task_id, status=TaskStatus.FAILED, progress=0,
            message=f"澶勭悊澶辫触: {str(e)}", error=str(e)
        )
        logger.error(f"寮傛涓婁紶浠诲姟澶辫触: {task_id}, error={e}")


@router.post("/documents/upload-async")
async def upload_document_async(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    kb_id: str = Form(""),
    chunk_strategy: str = Form(""),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    """API route."""



    if not file.filename:
        raise error_response(ErrorCode.INVALID_PARAM, "鏈彁渚涙枃浠跺悕")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise error_response(ErrorCode.UNSUPPORTED_FORMAT, f"涓嶆敮鎸佺殑鏍煎紡: {ext}")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "鏂囦欢杩囧ぇ")

    task_id = str(uuid.uuid4())
    task_tracker.create_task(task_id, task_type="document_upload")
    task_tracker.update_task(task_id, message=f"鏂囦欢 '{file.filename}' 宸叉帴鏀讹紝绛夊緟澶勭悊...")

    background_tasks.add_task(
        _process_upload_task, task_id, content, file.filename,
        kb_id, chunk_strategy, chunk_size, chunk_overlap,
    )

    return {
        "success": True,
        "task_id": task_id,
        "message": f"文件 '{file.filename}' 已提交异步处理",
    }


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """API route."""
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "文件不存在")
    return task.to_dict()


@router.get("/tasks")
async def list_tasks(task_type: Optional[str] = None, status: Optional[str] = None):
    """API route."""
    from app.core.task_tracker import TaskStatus as TS
    status_enum = None
    if status:
        try:
            status_enum = TS(status)
        except ValueError:
            pass
    return {"tasks": task_tracker.list_tasks(task_type=task_type, status=status_enum)}


# ==================== Auth Endpoints (O4) ====================

from app.core.auth import (
    create_access_token, create_refresh_token, decode_token,
    generate_api_key, revoke_api_key, list_api_keys, AUTH_ENABLED,
)


class LoginRequest(BaseModel):
    user_id: str
    password: Optional[str] = ""


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class ApiKeyCreateRequest(BaseModel):
    name: str
    permissions: Optional[list] = ["read", "write"]


@router.post("/auth/login")
async def login(request: LoginRequest):
    """Create a full-access session from any non-empty credentials."""
    user_id = request.user_id.strip()
    if not user_id or not request.password:
        raise error_response(ErrorCode.INVALID_PARAM, "请输入账号和密码")
    claims = {"role": "owner", "permissions": ["read", "write"]}
    access_token = create_access_token(user_id, claims)
    refresh_token = create_refresh_token(user_id, claims)
    return {
        "success": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": user_id,
        **claims,
    }


@router.post("/rag/vectors/clear")
async def clear_vector_indexes():
    """Clear vector/keyword indexes while retaining source documents."""
    try:
        result = await asyncio.to_thread(vector_store.clear_all, recreate_milvus=True)
        keyword_removed = await asyncio.to_thread(hybrid_search_engine.clear)
        rag_service._cache.clear()
        return {
            "success": True,
            "message": "向量索引已清理，原始文档和知识库目录未删除。请重新执行批量入库。",
            "keyword_removed": keyword_removed,
            **result,
        }
    except Exception as exc:
        logger.exception("清理向量索引失败")
        raise error_response(ErrorCode.PROCESS_FAILED, f"清理向量索引失败：{str(exc)}")


AGENT_TASK_DIR = os.path.join(settings.DATA_DIR, "agent_tasks")
_agent_resume_tasks: set[asyncio.Task] = set()
_agent_resume_task_ids: set[str] = set()


def _agent_artifact_path(task_id: str, filename: str = "draft.md") -> str:
    date_dir = datetime.now().strftime("%Y-%m-%d")
    base = os.path.join(AGENT_TASK_DIR, date_dir, task_id)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, filename)


def _agent_markdown(payload: AgentTaskRequest, result: Dict[str, Any]) -> str:
    answer = result.get("answer") or "未生成有效内容。"
    sources = result.get("sources") or result.get("chunks") or []
    template_titles = {
        "hotspot": "热点汇总",
        "wechat": "微信公众号文章草稿",
        "xiaohongshu": "小红书笔记草稿",
        "weekly": "知识库周报",
        "research": "主题研究报告",
    }
    title = template_titles.get(payload.template or "research", "Agent RAG 内容草稿")
    lines = [f"# {title}", "", f"> 任务：{payload.goal}", ""]
    if payload.template == "hotspot":
        lines.extend(["## 本期摘要", "", answer.strip(), "", "## 热点分类", "", "> 请根据来源进一步编辑分类和优先级。"])
    elif payload.template == "wechat":
        lines.extend(["## 导语", "", answer.strip(), "", "## 正文", "", "（请补充或调整段落标题）"])
    elif payload.template == "xiaohongshu":
        lines.extend(["## 标题候选", "", "1. ", "2. ", "3. ", "", "## 正文", "", answer.strip(), "", "## 话题标签", "", "#AI #知识管理 #效率工具"])
    elif payload.template == "weekly":
        lines.extend(["## 本周结论", "", answer.strip(), "", "## 新增资料与行动项", ""])
    else:
        lines.extend(["## 分析结果", "", answer.strip()])
    lines.extend(["", "## 来源"])
    if sources:
        for idx, source in enumerate(sources, 1):
            meta = source.get("metadata") or {}
            name = meta.get("title") or meta.get("filename") or source.get("doc_id") or f"来源 {idx}"
            lines.append(f"- [{idx}] {name}（chunk: {source.get('chunk_id', '')}）")
    else:
        lines.append("- 本次没有检索到可引用来源，请补充资料后重试。")
    lines.extend(["", "---", "生成方式：Agent RAG（检索、整理、生成）", "状态：待人工审核"])
    return "\n".join(lines) + "\n"


async def _run_agent_task(task_id: str, payload: AgentTaskRequest) -> None:
    task_tracker.update_task(task_id, status=TaskStatus.PROCESSING, progress=5, message="Agent 正在启动")
    try:
        execution = await asyncio.wait_for(
            agent_orchestrator.run(task_id, payload),
            timeout=max(30, int(getattr(settings, "AGENT_TASK_TIMEOUT_SECONDS", 180) or 180)),
        )
        result = execution["rag_result"]
        # Agent 已经基于统一 evidence 生成并核验草稿，保留旧模板作为兼容回退。
        markdown = execution.get("draft") or _agent_markdown(payload, result)
        draft_path = _agent_artifact_path(task_id, "draft.md")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        source_path = _agent_artifact_path(task_id, "sources.json")
        with open(source_path, "w", encoding="utf-8") as f:
            json.dump(result.get("sources") or result.get("chunks") or [], f, ensure_ascii=False, indent=2)
        # Every completed Agent deliverable is also persisted in the user's
        # file-resource tree, so it can be browsed, searched and re-used by
        # later RAG tasks. The task artifact remains the audit copy.
        resource_dir = Path(FILE_RESOURCE_DIR) / "agent-results" / datetime.now().strftime("%Y-%m-%d")
        resource_dir.mkdir(parents=True, exist_ok=True)
        resource_path = resource_dir / f"{task_id}.md"
        resource_path.write_text(markdown, encoding="utf-8")
        # Agent mode participates in the same persistent conversation memory
        # as standard RAG.  This enables follow-up questions and rolling
        # compression after roughly twenty turns.
        if payload.session_id:
            from app.rag.conversation_memory import conversation_memory
            conversation_memory.create_session(payload.session_id)
            conversation_memory.add_message(
                payload.session_id, "user", payload.goal,
                metadata={"type": "agent_task", "task_id": task_id},
            )
            conversation_memory.add_message(
                payload.session_id, "assistant", markdown,
                metadata={"type": "agent_task", "task_id": task_id, "intent": (execution.get("perception") or {}).get("intent", ""), "images": execution.get("images", [])},
            )
            await agent_orchestrator.runtime.tools.call("memory_compress", {"session_id": payload.session_id})
        task_tracker.update_task(
            task_id, status=TaskStatus.COMPLETED, progress=100, message="Agent 草稿生成完成",
            result={"_payload": (payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()), "draft": markdown, "draft_path": draft_path, "resource_path": str(resource_path), "sources": result.get("sources", []), "evidence": execution.get("evidence", []), "images": result.get("images", execution.get("images", [])), **{k: execution[k] for k in ("perception", "plan", "tool_calls", "quality", "memory", "trace", "coverage") if k in execution}},
        )
        telemetry.record("artifact_saved", source="agent", task_id=task_id, session_id=payload.session_id or "",
                         properties={"artifact_type": "file_resource_markdown", "resource_path": str(resource_path)})
        telemetry.record("agent_task_completed", source="agent", task_id=task_id, session_id=payload.session_id or "",
                         properties={"intent": (execution.get("perception") or {}).get("intent", ""),
                                     "duration_ms": round((time.time() - task_tracker.get_task(task_id).created_at) * 1000, 1),
                                     "evidence_count": len(execution.get("evidence") or []),
                                     "artifact_type": payload.output_format or "markdown"})
    except Exception as exc:
        logger.exception("Agent RAG 任务失败: %s", exc)
        current = task_tracker.get_task(task_id)
        if not current or current.status != TaskStatus.CANCELLED:
            timed_out = isinstance(exc, asyncio.TimeoutError)
            task_tracker.update_task(
                task_id, status=TaskStatus.FAILED, progress=0,
                message="Agent 任务超时" if timed_out else "Agent 任务失败",
                error="agent_task_timeout" if timed_out else str(exc),
            )
            telemetry.record("agent_task_failed", source="agent", task_id=task_id, session_id=payload.session_id or "",
                             properties={"error_type": type(exc).__name__, "error_message": str(exc)[:300]})


async def resume_pending_agent_tasks() -> int:
    """Resume persisted pending/processing Agent tasks after a process restart.

    Tasks restart from their original goal with a fresh bounded loop. This is
    safer than replaying an unknown external side effect, while preserving the
    task ID, artifacts and telemetry chain.
    """
    resumed = 0
    for row in task_tracker.list_tasks(task_type="agent_rag"):
        task_id = str(row.get("task_id") or "")
        if not task_id or task_id in _agent_resume_task_ids:
            continue
        if row.get("status") not in {TaskStatus.PENDING.value, TaskStatus.PROCESSING.value}:
            continue
        payload_data = ((row.get("result") or {}).get("_payload") or {})
        if not payload_data.get("goal"):
            task_tracker.update_task(row["task_id"], status=TaskStatus.FAILED, progress=0,
                                     message="任务无法恢复：缺少原始任务参数", error="missing_persisted_payload")
            continue
        try:
            payload = AgentTaskRequest(**payload_data)
            task_tracker.update_task(row["task_id"], status=TaskStatus.PENDING, progress=0,
                                     message="服务已恢复，Agent 任务重新排队")
            _agent_resume_task_ids.add(task_id)
            try:
                task = asyncio.create_task(_run_agent_task(task_id, payload))
            except Exception:
                _agent_resume_task_ids.discard(task_id)
                raise
            _agent_resume_tasks.add(task)
            def _cleanup(done_task: asyncio.Task, resumed_id: str = task_id) -> None:
                _agent_resume_tasks.discard(done_task)
                _agent_resume_task_ids.discard(resumed_id)
            task.add_done_callback(_cleanup)
            resumed += 1
        except Exception as exc:
            task_tracker.update_task(row["task_id"], status=TaskStatus.FAILED, progress=0,
                                     message="任务恢复失败", error=str(exc)[:300])
    return resumed


@router.post("/agent/tasks")
async def create_agent_task(payload: AgentTaskRequest, background_tasks: BackgroundTasks):
    if not payload.goal.strip():
        raise error_response(ErrorCode.INVALID_PARAM, "任务目标不能为空")
    task_id = f"agent-{uuid.uuid4().hex}"
    task_tracker.create_task(task_id, task_type="agent_rag")
    payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    task_tracker.update_task(task_id, message="任务已创建，等待执行", result={"_payload": payload_data})
    telemetry.record("agent_task_created", source="agent", task_id=task_id, session_id=payload.session_id or "",
                     properties={"template": payload.template or "auto", "scope": payload.scope or [],
                                 "selected_path_count": len(payload.selected_paths or []), "goal_length": len(payload.goal)})
    background_tasks.add_task(_run_agent_task, task_id, payload)
    return {"task_id": task_id, "status": "pending", "message": "Agent 任务已创建"}


@router.get("/agent/runtime")
async def get_agent_runtime():
    """返回当前 Agent 能力清单，供前端展示，不暴露密钥。"""
    return agent_orchestrator.runtime.describe()


@router.get("/agent/tasks")
async def list_agent_tasks(status: Optional[str] = None, limit: int = 50):
    """列出 Agent 任务历史，供任务中心恢复和查看。"""
    from app.core.task_tracker import TaskStatus as TS
    status_enum = None
    if status:
        try:
            status_enum = TS(status)
        except ValueError:
            raise error_response(ErrorCode.INVALID_PARAM, "无效的任务状态")
    rows = task_tracker.list_tasks(task_type="agent_rag", status=status_enum)
    return {"tasks": rows[: max(1, min(int(limit), 200))]}


@router.get("/knowledge-review/daily")
async def get_daily_knowledge_review(limit: int = 3, include_handled: bool = False):
    """返回可追溯的每日复习题和未复用资料。"""
    return knowledge_review_service.daily(task_tracker.list_tasks(task_type="agent_rag"), limit=limit, include_handled=include_handled)


class KnowledgeReviewHandledRequest(BaseModel):
    source_path: str
    action: str = "reviewed"


@router.post("/knowledge-review/handled")
async def mark_knowledge_review_handled(payload: KnowledgeReviewHandledRequest):
    try:
        return {"success": True, "item": knowledge_review_service.mark_handled(payload.source_path, payload.action)}
    except ValueError as exc:
        raise error_response(ErrorCode.INVALID_PARAM, str(exc))


class KnowledgeReviewPreferencesRequest(BaseModel):
    enabled: Optional[bool] = None
    topics: Optional[List[str]] = None
    frequency: Optional[str] = None
    max_items: Optional[int] = None


@router.get("/knowledge-review/preferences")
async def get_knowledge_review_preferences():
    return {"success": True, "preferences": knowledge_review_service.get_preferences()}


@router.patch("/knowledge-review/preferences")
async def update_knowledge_review_preferences(payload: KnowledgeReviewPreferencesRequest):
    data = {key: value for key, value in payload.model_dump().items() if value is not None}
    try:
        return {"success": True, "preferences": knowledge_review_service.update_preferences(data)}
    except (TypeError, ValueError) as exc:
        raise error_response(ErrorCode.INVALID_PARAM, str(exc))


@router.get("/agent/mcp/{server_name}/tools")
async def discover_agent_mcp_tools(server_name: str):
    """Discover tools from a configured MCP server without exposing headers."""
    try:
        tools = await agent_orchestrator.runtime.mcp.discover(server_name)
        return {"server": server_name, "tools": tools}
    except Exception as exc:
        raise error_response(ErrorCode.QUERY_FAILED, f"MCP 工具发现失败：{str(exc)[:240]}")


@router.get("/agent/tasks/{task_id}")
async def get_agent_task(task_id: str):
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    return task.to_dict()


@router.get("/agent/tasks/{task_id}/artifact/{filename}")
async def get_agent_artifact(task_id: str, filename: str):
    if not re.fullmatch(r"agent-[0-9a-f]{32}", task_id):
        raise error_response(ErrorCode.INVALID_PARAM, "无效的 Agent 任务 ID")
    if filename not in {"draft.md", "final.md", "final.pdf", "sources.json", "publish.json"} and not re.fullmatch(r"draft\.v\d+\.md", filename):
        raise error_response(ErrorCode.INVALID_PARAM, "不支持的产物类型")
    path = _agent_artifact_path(task_id, filename)
    root = Path(AGENT_TASK_DIR).resolve()
    target = Path(path).resolve()
    if root not in target.parents or not target.exists():
        raise error_response(ErrorCode.FILE_NOT_FOUND, "产物不存在")
    return FileResponse(str(target), filename=filename)


@router.get("/agent/tasks/{task_id}/artifacts")
async def list_agent_artifacts(task_id: str):
    """列出任务产物及草稿版本，不返回文件内容。"""
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    base = Path(_agent_artifact_path(task_id, "draft.md")).parent
    artifacts = []
    if base.exists():
        for path in sorted(base.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file() or path.name not in {"draft.md", "final.md", "final.pdf", "sources.json", "publish.json"} and not re.fullmatch(r"draft\.v\d+\.md", path.name):
                continue
            artifacts.append({"filename": path.name, "size": path.stat().st_size,
                              "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()})
    return {"task_id": task_id, "artifacts": artifacts}


@router.get("/agent/tasks/{task_id}/trace")
async def get_agent_trace(task_id: str):
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    result = task.result or {}
    return {
        "task_id": task_id,
        "status": task.status.value,
        "progress": task.progress,
        "message": task.message,
        "perception": result.get("perception", {}),
        "plan": result.get("plan", []),
        "tool_calls": result.get("tool_calls", []),
        "trace": result.get("trace", []),
        "quality": result.get("quality", {}),
        "memory": result.get("memory", {}),
        "coverage": result.get("coverage", {}),
    }


@router.get("/agent/tasks/{task_id}/events")
async def get_agent_task_events(task_id: str, limit: int = 200):
    """返回单个 Agent 任务的可展示事件，不暴露隐藏推理或凭据。"""
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    return {"task_id": task_id, "events": telemetry.query_events(task_id=task_id, limit=max(1, min(int(limit), 500)))}


@router.post("/agent/tasks/{task_id}/feedback")
async def submit_agent_feedback(task_id: str, payload: AgentFeedbackRequest):
    """Persist a BadCase with task context for later evaluation."""
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    rating = str(payload.rating or "negative").lower()
    if rating not in {"positive", "negative", "neutral"}:
        raise error_response(ErrorCode.INVALID_PARAM, "rating 只能是 positive、negative 或 neutral")
    result = task.result if isinstance(task.result, dict) else {}
    record = {
        "feedback_id": uuid.uuid4().hex, "task_id": task_id, "rating": rating,
        "reason": str(payload.reason or "")[:200], "comment": str(payload.comment or "")[:2000],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "goal": str((result.get("_payload") or {}).get("goal") or "")[:2000],
        "perception": result.get("perception") or {}, "quality": result.get("quality") or {},
        "evidence": [{"source_path": item.get("source_path"), "title": item.get("title"), "score": item.get("score")} for item in (result.get("evidence") or [])[:50]],
        "draft": str(result.get("draft") or "")[:12000],
    }
    feedback_path = Path(settings.DATA_DIR, "agent_feedback.json")
    try:
        existing = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.exists() else []
        if not isinstance(existing, list): existing = []
    except Exception:
        existing = []
    existing.append(record)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = feedback_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(existing[-2000:], ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, feedback_path)
    telemetry.record("user_feedback", source="agent", task_id=task_id,
                     properties={"rating": rating, "reason": payload.reason or ""})
    return {"success": True, "feedback_id": record["feedback_id"], "message": "反馈已记录"}


@router.post("/agent/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: str):
    task = task_tracker.cancel_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    return task.to_dict()


@router.post("/agent/tasks/{task_id}/retry")
async def retry_agent_task(task_id: str, background_tasks: BackgroundTasks):
    """Retry a failed/cancelled Agent task with its persisted input snapshot."""
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    if task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING}:
        raise error_response(ErrorCode.INVALID_PARAM, "任务仍在执行中，无需重试")
    payload_data = (task.result or {}).get("_payload") if isinstance(task.result, dict) else None
    if not isinstance(payload_data, dict) or not str(payload_data.get("goal") or "").strip():
        raise error_response(ErrorCode.PROCESS_FAILED, "任务缺少可恢复的原始参数，请重新创建任务")
    try:
        payload = AgentTaskRequest(**payload_data)
    except Exception as exc:
        raise error_response(ErrorCode.INVALID_PARAM, f"任务参数已失效：{str(exc)[:160]}")
    retry_count = int((task.result or {}).get("retry_count") or 0) + 1
    task_tracker.update_task(task_id, status=TaskStatus.PENDING, progress=0,
                             message=f"任务已重新排队（第 {retry_count} 次）", error=None,
                             result={"_payload": payload_data, "retry_count": retry_count})
    telemetry.record("agent_task_retried", source="agent", task_id=task_id,
                     session_id=payload.session_id or "", properties={"retry_count": retry_count})
    background_tasks.add_task(_run_agent_task, task_id, payload)
    return {"task_id": task_id, "status": "pending", "retry_count": retry_count, "message": "Agent 任务已重新排队"}


@router.put("/agent/tasks/{task_id}/draft")
async def update_agent_draft(task_id: str, payload: AgentDraftUpdateRequest):
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    draft_path = _agent_artifact_path(task_id, "draft.md")
    # Preserve every previously saved draft before replacing it.  Versioned
    # files make manual editing auditable and allow a later compare/rollback
    # UI without changing the current artifact contract.
    if os.path.exists(draft_path):
        existing = sorted(Path(draft_path).parent.glob("draft.v*.md"),
                          key=lambda item: int(re.search(r"v(\d+)", item.name).group(1)) if re.search(r"v(\d+)", item.name) else 0)
        next_version = (int(re.search(r"v(\d+)", existing[-1].name).group(1)) if existing and re.search(r"v(\d+)", existing[-1].name) else 0) + 1
        shutil.copyfile(draft_path, Path(draft_path).parent / f"draft.v{next_version}.md")
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(payload.content)
    previous = task.result or {}
    versions = list(previous.get("draft_versions") or []) if isinstance(previous, dict) else []
    versions.append({"filename": os.path.basename(draft_path), "saved_at": datetime.now().isoformat(), "chars": len(payload.content)})
    task_tracker.update_task(task_id, message="草稿已保存（历史版本已保留）", result={**previous, "draft": payload.content, "draft_path": draft_path, "draft_versions": versions[-50:]})
    telemetry.record("artifact_saved", source="agent", task_id=task_id,
                     properties={"artifact_type": "draft", "version_count": len(versions), "chars": len(payload.content)})
    return {"success": True, "task_id": task_id, "draft": payload.content, "version_count": len(versions)}


@router.post("/agent/tasks/{task_id}/export")
async def export_agent_task(task_id: str, format: str = "markdown"):
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    content = task.result.get("draft", "")
    if format.lower() in {"md", "markdown"}:
        path = _agent_artifact_path(task_id, "final.md")
        with open(path, "w", encoding="utf-8") as f: f.write(content)
        telemetry.record("artifact_exported", source="agent", task_id=task_id,
                         properties={"format": "markdown", "chars": len(content)})
        return {"success": True, "format": "markdown", "path": path, "content": content}
    if format.lower() == "pdf":
        try:
            import fitz
            path = _agent_artifact_path(task_id, "final.pdf")
            doc = fitz.open()
            page = doc.new_page()
            y = 50
            for line in content.splitlines():
                if y > 780:
                    page = doc.new_page(); y = 50
                page.insert_text((50, y), line[:110], fontsize=10)
                y += 15
            doc.save(path); doc.close()
            telemetry.record("artifact_exported", source="agent", task_id=task_id,
                             properties={"format": "pdf", "chars": len(content)})
            return {"success": True, "format": "pdf", "path": path}
        except Exception as exc:
            raise error_response(ErrorCode.PROCESS_FAILED, f"PDF 导出失败: {exc}")
    raise error_response(ErrorCode.INVALID_PARAM, "仅支持 markdown 或 pdf")


@router.post("/agent/tasks/{task_id}/publish/prepare")
async def prepare_agent_publish(task_id: str, channel: str = "wechat"):
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    if channel not in {"wechat", "xiaohongshu"}:
        raise error_response(ErrorCode.INVALID_PARAM, "仅支持 wechat 或 xiaohongshu")
    content = task.result.get("draft", "")
    title = next((line.lstrip("# ").strip() for line in content.splitlines() if line.startswith("# ")), "Agent 内容草稿")
    materials = {
        "channel": channel,
        "status": "draft",
        "requires_user_confirmation": True,
        "title": title,
        "content": content,
        "hashtags": ["AI", "知识管理", "效率工具"] if channel == "xiaohongshu" else [],
        "note": "当前仅生成发布素材包，不自动登录或发布到第三方平台。",
    }
    path = _agent_artifact_path(task_id, "publish.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(materials, f, ensure_ascii=False, indent=2)
    return {"success": True, "task_id": task_id, "materials": materials, "path": path}


@router.get("/mcp/wechat/status")
async def get_wechat_mcp_status():
    """返回公众号 MCP 的脱敏配置状态。"""
    return wechat_mcp.status()


@router.post("/mcp/wechat/config")
async def configure_wechat_mcp(request: WeChatConfigRequest):
    """Save server-side WeChat configuration; secrets are never echoed."""
    if request.enabled and (not request.app_id.strip() or not request.app_secret.strip()):
        raise error_response(ErrorCode.INVALID_PARAM, "启用微信公众号 MCP 时必须填写 AppID 和 AppSecret")
    return {"success": True, "status": wechat_mcp.configure(request.enabled, request.app_id, request.app_secret, request.default_cover_media_id)}


@router.post("/mcp/wechat/upload-cover")
async def upload_wechat_cover(file: UploadFile = File(...)):
    """Upload a user-selected cover and return only its media_id."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise error_response(ErrorCode.INVALID_PARAM, "封面必须是图片文件")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise error_response(ErrorCode.FILE_TOO_LARGE, "封面图片不能超过 8MB")
    try:
        result = await wechat_mcp.upload_cover_image(data, file.filename or "cover.jpg", file.content_type)
    except Exception as exc:
        raise error_response(ErrorCode.PROCESS_FAILED, f"公众号封面上传失败: {exc}")
    return {"success": True, "media_id": result.get("media_id"), "status": wechat_mcp.status()}


@router.post("/mcp/wechat/test")
async def test_wechat_mcp():
    """主动测试公众号 token 获取；未配置时返回可操作的失败状态。"""
    try:
        status = await wechat_mcp.account_status()
        return {"success": True, "status": status}
    except Exception as exc:
        return {"success": False, "status": wechat_mcp.status(), "message": str(exc)}


def _wechat_article_from_task(task: Any, request: Optional[WeChatDraftPrepareRequest] = None) -> Dict[str, Any]:
    """Build a platform-neutral article preview from an Agent task."""
    result = task.result or {}
    draft = str(result.get("draft") or "")
    title = str((request.title if request else "") or "").strip()
    if not title:
        title = next((line.lstrip("# ").strip() for line in draft.splitlines() if line.startswith("# ")), "Agent 内容草稿")
    digest = str((request.digest if request else "") or "").strip()
    if not digest:
        plain = re.sub(r"[#>*`\[\]()]", " ", draft)
        digest = re.sub(r"\s+", " ", plain).strip()[:120]
    prepared = prepare_article(title, digest, draft)
    requested_cover = str((request.cover_media_id if request else "") or "").strip()
    cover_media_id = requested_cover or str(getattr(settings, "WECHAT_DEFAULT_COVER_MEDIA_ID", "") or "").strip()
    quality = result.get("quality") or {}
    prepared.update({
        "task_id": task.task_id,
        "draft_hash": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "cover_media_id": cover_media_id,
        "source_count": len(result.get("evidence") or result.get("sources") or []),
        "quality_passed": quality.get("passed") is not False,
        "source_refs": [
            {"source_id": item.get("source_id"), "source_path": item.get("source_path"), "title": item.get("title")}
            for item in (result.get("evidence") or result.get("sources") or [])
        ],
    })
    prepared["can_sync"] = bool(prepared["valid"] and prepared["quality_passed"] and prepared["cover_media_id"])
    if not prepared["cover_media_id"]:
        prepared["warnings"].append("缺少封面 media_id；请配置 WECHAT_DEFAULT_COVER_MEDIA_ID 或在请求中提供 cover_media_id")
    if not prepared["quality_passed"]:
        prepared["warnings"].append("引用或质量核验未通过，不能同步公众号")
    return prepared


async def _wechat_upload_and_replace_images(article: Dict[str, Any]) -> Dict[str, Any]:
    """Upload remote article images and replace URLs with WeChat URLs."""
    sources = list(article.get("image_sources") or [])[:8]
    if not sources:
        return article
    html_content = str(article.get("content_html") or "")
    uploaded = []
    warnings = list(article.get("warnings") or [])
    for source in sources:
        parsed = urllib.parse.urlsplit(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            warnings.append(f"图片地址不安全，已跳过：{source[:80]}")
            continue
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(source)
            response.raise_for_status()
            data = response.content
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("图片超过 8MB")
            mime = response.headers.get("content-type", "image/jpeg").split(";", 1)[0].lower()
            if not mime.startswith("image/"):
                raise ValueError("URL 返回的不是图片")
            filename = os.path.basename(parsed.path) or "article-image.jpg"
            uploaded_item = await wechat_mcp.upload_article_image(data, filename, mime)
            target = str(uploaded_item.get("url") or "")
            if target:
                html_content = html_content.replace(source, target)
                uploaded.append({"source_url": source, "wechat_url": target, "status": "uploaded"})
        except Exception as exc:
            warnings.append(f"图片上传失败：{source[:80]}（{str(exc)[:100]}）")
            uploaded.append({"source_url": source, "status": "failed"})
    article["content_html"] = html_content
    article["images"] = uploaded
    article["warnings"] = warnings
    article["can_sync"] = bool(article.get("can_sync") and not any(item.get("status") == "failed" for item in uploaded))
    return article


@router.post("/agent/tasks/{task_id}/wechat/prepare")
async def prepare_wechat_draft(task_id: str, request: WeChatDraftPrepareRequest):
    """Prepare and validate a WeChat article without external writes."""
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    article = _wechat_article_from_task(task, request)
    return {"success": True, "task_id": task_id, "preview": article}


@router.post("/agent/tasks/{task_id}/wechat/sync-draft")
async def sync_wechat_draft(task_id: str, request: WeChatDraftSyncRequest):
    """Create an idempotent WeChat draft after local validation."""
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    article = _wechat_article_from_task(task, request)
    if not article["can_sync"]:
        raise error_response(ErrorCode.INVALID_PARAM, "公众号草稿未达到同步条件：" + "；".join(article["warnings"]))
    article = await _wechat_upload_and_replace_images(article)
    if not article["can_sync"]:
        raise error_response(ErrorCode.PROCESS_FAILED, "正文图片未全部上传成功：" + "；".join(article["warnings"]))
    existing = (task.result.get("wechat") or {}) if task.result else {}
    idem = request.idempotency_key or f"{task_id}:{article['draft_hash']}"
    if existing.get("idempotency_key") == idem and existing.get("media_id"):
        return {"success": True, "task_id": task_id, **existing, "reused": True}
    try:
        result = await wechat_mcp.create_draft({
            "title": article["title"], "digest": article["digest"],
            "content_html": article["content_html"], "thumb_media_id": article["cover_media_id"],
        })
    except Exception as exc:
        raise error_response(ErrorCode.PROCESS_FAILED, f"公众号草稿创建失败: {exc}")
    sync = {
        "media_id": result.get("media_id"), "status": "draft",
        "idempotency_key": idem, "draft_hash": article["draft_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    updated = {**(task.result or {}), "wechat": sync}
    task_tracker.update_task(task_id, message="公众号草稿已创建，等待人工审核", result=updated)
    path = _agent_artifact_path(task_id, "publish.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sync, f, ensure_ascii=False, indent=2)
    return {"success": True, "task_id": task_id, **sync}


@router.post("/agent/tasks/{task_id}/wechat/update-draft")
async def update_wechat_draft(task_id: str, request: WeChatDraftSyncRequest):
    """Update an existing WeChat draft using the current Agent draft version."""
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    existing = (task.result.get("wechat") or {})
    media_id = str(existing.get("media_id") or "")
    if not media_id:
        raise error_response(ErrorCode.INVALID_PARAM, "该任务尚未同步过微信公众号草稿")
    article = _wechat_article_from_task(task, request)
    if not article["can_sync"]:
        raise error_response(ErrorCode.INVALID_PARAM, "公众号草稿未达到同步条件：" + "；".join(article["warnings"]))
    article = await _wechat_upload_and_replace_images(article)
    if not article["can_sync"]:
        raise error_response(ErrorCode.PROCESS_FAILED, "正文图片未全部上传成功：" + "；".join(article["warnings"]))
    try:
        await wechat_mcp.update_draft(media_id, {"title": article["title"], "digest": article["digest"], "content_html": article["content_html"], "thumb_media_id": article["cover_media_id"]})
    except Exception as exc:
        raise error_response(ErrorCode.PROCESS_FAILED, f"公众号草稿更新失败: {exc}")
    updated = {**existing, "status": "draft", "draft_hash": article["draft_hash"], "updated_at": datetime.now(timezone.utc).isoformat()}
    task_tracker.update_task(task_id, message="公众号草稿已更新，等待人工审核", result={**task.result, "wechat": updated})
    return {"success": True, "task_id": task_id, **updated}


@router.get("/agent/tasks/{task_id}/wechat/status")
async def get_wechat_task_status(task_id: str):
    task = task_tracker.get_task(task_id)
    if not task:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 任务不存在或已过期")
    return {"success": True, "task_id": task_id, "wechat": (task.result or {}).get("wechat") or {}}


@router.post("/agent/tasks/{task_id}/wechat/approval")
async def create_wechat_approval(task_id: str, request: WeChatApprovalRequest):
    """Issue a short-lived, one-time approval token for publishing."""
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    existing = task.result.get("wechat") or {}
    media_id = request.media_id or existing.get("media_id")
    if not media_id:
        raise error_response(ErrorCode.INVALID_PARAM, "请先同步微信公众号草稿")
    token = secrets.token_urlsafe(24)
    expires_at = time.time() + 10 * 60
    approval = {
        **existing, "media_id": media_id,
        "approval_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "approval_expires_at": expires_at,
        "approval_used": False,
        "status": "awaiting_user_approval",
    }
    task_tracker.update_task(task_id, message="已生成发布确认令牌，有效期 10 分钟", result={**task.result, "wechat": approval})
    # The token is returned once to the authenticated caller; it is not saved
    # in logs or exposed to the model.
    return {"success": True, "task_id": task_id, "media_id": media_id, "approval_token": token, "expires_at": expires_at}


@router.post("/agent/tasks/{task_id}/publish/wechat/draft")
async def create_wechat_draft(task_id: str):
    """兼容旧客户端，转发到新的 prepare/sync-draft 流程。"""
    return await sync_wechat_draft(task_id, WeChatDraftSyncRequest())


@router.post("/agent/tasks/{task_id}/publish/wechat/submit")
async def submit_wechat_publish(task_id: str, payload: WeChatPublishRequest):
    """提交公众号发布，必须显式确认且使用一次性审批令牌。"""
    if not payload.confirm:
        raise error_response(ErrorCode.INVALID_PARAM, "发布前必须确认 confirm=true")
    task = task_tracker.get_task(task_id)
    if not task or not task.result:
        raise error_response(ErrorCode.FILE_NOT_FOUND, "Agent 草稿尚未生成")
    media_id = payload.media_id or ((task.result.get("wechat") or {}).get("media_id") if task.result else "")
    if not media_id:
        raise error_response(ErrorCode.INVALID_PARAM, "请先创建公众号草稿并提供 media_id")
    wechat_state = task.result.get("wechat") or {}
    approval_hash = str(wechat_state.get("approval_hash") or "")
    approval_token = str(payload.approval_token or "")
    if not approval_hash or not approval_token:
        raise error_response(ErrorCode.INVALID_PARAM, "发布前必须先获取一次性审批令牌")
    if bool(wechat_state.get("approval_used")) or float(wechat_state.get("approval_expires_at") or 0) < time.time():
        raise error_response(ErrorCode.INVALID_PARAM, "发布审批令牌已过期或已使用，请重新确认")
    if not hmac.compare_digest(approval_hash, hashlib.sha256(approval_token.encode("utf-8")).hexdigest()):
        raise error_response(ErrorCode.INVALID_PARAM, "发布审批令牌无效")
    try:
        result = await agent_orchestrator.runtime.tools.call("wechat_submit_publish", {"media_id": media_id, "approved": True})
    except Exception as exc:
        raise error_response(ErrorCode.PROCESS_FAILED, f"公众号发布提交失败: {exc}")
    publish = {"media_id": media_id, "publish_id": result.get("publish_id"), "status": "submitted", "submitted_at": datetime.now(timezone.utc).isoformat()}
    task_tracker.update_task(task_id, message="公众号发布已提交", result={**(task.result or {}), "wechat": {**publish, "approval_used": True}})
    return {"success": True, "task_id": task_id, **publish}


@router.get("/mcp/wechat/publish/{publish_id}")
async def get_wechat_publish_status(publish_id: str):
    try:
        return {"success": True, "status": await wechat_mcp.publish_status(publish_id)}
    except Exception as exc:
        raise error_response(ErrorCode.PROCESS_FAILED, f"公众号发布状态查询失败: {exc}")


@router.post("/auth/guest")
async def guest_login():
    """Create a read-only guest session."""
    claims = {"role": "guest", "permissions": ["read"]}
    access_token = create_access_token("guest", claims)
    refresh_token = create_refresh_token("guest", claims)
    return {
        "success": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user_id": "游客",
        **claims,
    }


@router.post("/auth/refresh")
async def refresh_token(request: TokenRefreshRequest):
    """API route."""
    try:
        payload = decode_token(request.refresh_token)
        if not payload:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        if payload.get("type") != "refresh":
            raise error_response(ErrorCode.INVALID_PARAM, "闇€瑕?refresh token")
        claims = {
            "role": payload.get("role", "owner"),
            "permissions": payload.get("permissions", ["read", "write"]),
        }
        new_access = create_access_token(payload["sub"], claims)
        return {"success": True, "access_token": new_access, "token_type": "bearer", **claims}
    except HTTPException:
        raise
    except Exception as e:
        raise error_response(ErrorCode.INVALID_PARAM, str(e))


@router.get("/auth/me")
async def get_auth_session(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="登录凭证无效")
    return {
        "success": True,
        "user_id": payload.get("sub"),
        "role": payload.get("role", "owner"),
        "permissions": payload.get("permissions", ["read", "write"]),
    }


@router.post("/auth/api-keys")
async def create_api_key(request: ApiKeyCreateRequest, user: dict = Depends(get_current_user)):
    """API route."""
    key_info = generate_api_key(request.name, request.permissions)
    raw_key = key_info["key"]
    return {"success": True, "api_key": raw_key, "message": "请妥善保存，此 Key 仅显示一次"}


@router.get("/auth/api-keys")
async def get_api_keys(user: dict = Depends(get_current_user)):
    """API route."""
    return {"success": True, "keys": list_api_keys()}
