import os
import asyncio
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.core.config import settings
from app.models.wiki import wiki_store
from app.services.doc_import import doc_import_service
from app.services.ai_doc import ai_doc_service
from app.services.ai_wiki_creator import ai_wiki_creator_service
from app.services.semantic_search import semantic_search_service
from app.services.knowledge_association import knowledge_association_service
from app.services.system_manage import system_manage_service
from app.services.version_diff import version_diff_service
from app.services.bookmark import bookmark_service
from app.services.fulltext_search import fulltext_search_service
from app.services.wiki_template import wiki_template_service
from app.services.page_embed import page_embed_service
from app.services.attachment import attachment_service
from app.services.page_export import page_export_service
from app.services.recycle_bin import recycle_bin_service
from app.services.audit_log import audit_log_service
from app.services.email_notification import email_notification_service
from app.services.api_key import api_key_service
from app.services.backup import backup_service
from app.telemetry.recorder import telemetry

logger = logging.getLogger(__name__)
wiki_router = APIRouter(prefix="/wiki", tags=["Wiki"])


# ==================== Request Models ====================

class PageCreateRequest(BaseModel):
    title: str
    content: Optional[str] = ""
    space_id: Optional[str] = "default"
    parent_id: Optional[str] = ""
    page_type: Optional[str] = "markdown"
    tags: Optional[List[str]] = None
    author: Optional[str] = "system"


class PageUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    author: Optional[str] = "system"
    change_summary: Optional[str] = ""


class PageMoveRequest(BaseModel):
    new_parent_id: str


class SpaceCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    space_type: Optional[str] = "project"
    icon: Optional[str] = ""
    owner: Optional[str] = "system"


class SpaceUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None


class TagCreateRequest(BaseModel):
    name: str
    color: Optional[str] = "#6b7280"
    space_id: Optional[str] = "default"


class TagUpdateRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class BatchTagRequest(BaseModel):
    page_ids: List[str]
    tag_names: List[str]


class PermissionSetRequest(BaseModel):
    space_id: str
    page_id: str
    user_id: str
    role: str


class ShareEnableRequest(BaseModel):
    password: Optional[str] = ""


class ShareAccessRequest(BaseModel):
    share_token: str
    password: Optional[str] = ""


class EncryptRequest(BaseModel):
    password: str


class DecryptRequest(BaseModel):
    password: str


class CommentCreateRequest(BaseModel):
    content: str
    author: Optional[str] = "system"
    parent_comment_id: Optional[str] = ""
    selection_start: Optional[int] = -1
    selection_end: Optional[int] = -1
    selection_text: Optional[str] = ""
    comment_type: Optional[str] = "comment"


class RollbackRequest(BaseModel):
    version_number: int
    author: Optional[str] = "system"


class WebpageImportRequest(BaseModel):
    url: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class NotionImportRequest(BaseModel):
    export_dir: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class ObsidianImportRequest(BaseModel):
    vault_dir: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class FolderImportRequest(BaseModel):
    folder_path: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class AICreatePageRequest(BaseModel):
    topic: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "ai"


class AIGenerateFromNotesRequest(BaseModel):
    note_ids: List[str]
    title: Optional[str] = ""
    space_id: Optional[str] = "default"


class AICompleteRequest(BaseModel):
    direction: Optional[str] = "expand"


class AICopilotRequest(BaseModel):
    page_id: Optional[str] = ""
    action: str = "related"
    selected_text: Optional[str] = ""
    instruction: Optional[str] = ""
    top_k: Optional[int] = 5
    space_id: Optional[str] = ""


class AICopilotSaveRequest(BaseModel):
    title: str
    content: str
    space_id: Optional[str] = "default"
    tags: Optional[List[str]] = None
    author: Optional[str] = "copilot"
    source_page_id: Optional[str] = ""


class AITemplateRequest(BaseModel):
    template_type: str
    topic: Optional[str] = ""
    space_id: Optional[str] = "default"


class AIQAToDocRequest(BaseModel):
    qa_content: str
    title: Optional[str] = ""
    space_id: Optional[str] = "default"


class AIArchiveConversationRequest(BaseModel):
    conversation_content: str
    title: Optional[str] = ""
    space_id: Optional[str] = "default"


class SemanticSearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 10
    kb_id: Optional[str] = ""
    space_id: Optional[str] = None


class FuzzySearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 10
    kb_id: Optional[str] = ""


class SimilarityMatchRequest(BaseModel):
    text1: str
    text2: str


class AliasAddRequest(BaseModel):
    standard_name: str
    alias: str


class AliasRemoveRequest(BaseModel):
    standard_name: str
    alias: str


class TermRegisterRequest(BaseModel):
    term: str
    definition: Optional[str] = ""
    category: Optional[str] = ""
    standard_name: Optional[str] = ""


class TermUpdateRequest(BaseModel):
    term: Optional[str] = None
    definition: Optional[str] = None
    category: Optional[str] = None
    standard_name: Optional[str] = None


class ModelConfigRequest(BaseModel):
    model_name: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class EmbeddingModelRequest(BaseModel):
    model_name: str
    dimension: Optional[int] = 2048
    base_url: Optional[str] = None


class ModelApiKeyCollectionRequest(BaseModel):
    collection: Dict[str, Dict[str, Any]]


class ModelConnectionTestRequest(BaseModel):
    slot: str
    config: Optional[Dict[str, Any]] = None


class PromptConfigRequest(BaseModel):
    kb_id: str
    system_prompt: str
    custom_instructions: Optional[str] = ""


class ChunkConfigRequest(BaseModel):
    kb_id: str
    chunk_size: Optional[int] = 512
    chunk_overlap: Optional[int] = 64
    strategy: Optional[str] = "general"


class VectorCleanupRequest(BaseModel):
    kb_id: Optional[str] = None
    doc_id: Optional[str] = None


# ==================== Page CRUD ====================

@wiki_router.post("/pages/create")
async def create_page(request: PageCreateRequest):
    if not request.title.strip():
        raise HTTPException(status_code=400, detail="页面标题不能为空")
    try:
        page = wiki_store.create_page(
            title=request.title,
            content=request.content,
            space_id=request.space_id,
            parent_id=request.parent_id,
            page_type=request.page_type,
            tags=request.tags or [],
            author=request.author,
        )
        fulltext_search_service.index_page(
            page_id=page.page_id, title=page.title, content=page.content,
            space_id=page.space_id, tags=page.tags, author=page.author,
        )
        return {"success": True, "page": page.to_dict()}
    except Exception as e:
        logger.error(f"创建页面失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# P2: 双链页面列表（用于自动补全） - 必须在 /pages/{page_id} 之前注册
@wiki_router.get("/pages/link-candidates")
async def get_link_candidates(q: str = ""):
    """获取可链接的页面列表，用于 [[ 自动补全。"""
    pages = wiki_store.list_pages()
    if q:
        pages = [p for p in pages if q.lower() in p.title.lower()]
    return {"success": True, "pages": [{"page_id": p.page_id, "title": p.title} for p in pages[:20]]}


@wiki_router.get("/pages/{page_id}")
async def get_page(page_id: str):
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True, "page": page.to_dict()}


@wiki_router.put("/pages/{page_id}")
async def update_page(page_id: str, request: PageUpdateRequest):
    page = wiki_store.update_page(
        page_id,
        title=request.title,
        content=request.content,
        tags=request.tags,
        author=request.author,
        change_summary=request.change_summary,
    )
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    fulltext_search_service.index_page(
        page_id=page.page_id, title=page.title, content=page.content,
        space_id=page.space_id, tags=page.tags, author=page.author,
    )
    return {"success": True, "page": page.to_dict()}


@wiki_router.delete("/pages/{page_id}")
async def delete_page(page_id: str):
    success = wiki_store.delete_page(page_id)
    if not success:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True}


@wiki_router.get("/pages")
async def list_pages(space_id: Optional[str] = None, parent_id: Optional[str] = None):
    pages = wiki_store.list_pages(space_id=space_id, parent_id=parent_id)
    return {"success": True, "pages": [p.to_dict() for p in pages]}


@wiki_router.get("/pages/search/{query}")
async def search_pages(query: str, space_id: Optional[str] = None):
    pages = wiki_store.search_pages(query, space_id=space_id)
    return {"success": True, "pages": [p.to_dict() for p in pages]}


# P2: 反向链接接口
@wiki_router.get("/pages/{page_id}/backlinks")
async def get_backlinks(page_id: str):
    """获取指向当前页面的所有反向链接。"""
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    page_title = page.title
    backlinks = []
    all_pages = wiki_store.list_pages(space_id=page.space_id)
    for p in all_pages:
        if p.page_id == page_id:
            continue
        # 检查内容中是否包含 [[页面标题]] 或 [[page_id]]
        if f"[[{page_title}]]" in p.content or f"[[{page_id}]]" in p.content:
            backlinks.append({
                "page_id": p.page_id,
                "title": p.title,
                "updated_at": p.updated_at,
            })
    return {"success": True, "backlinks": backlinks, "count": len(backlinks)}


# P14: 页面分享
import uuid
_share_tokens = {}  # 简单内存存储，生产环境应使用数据库

@wiki_router.post("/pages/{page_id}/share")
async def share_page(page_id: str):
    """生成页面分享链接。"""
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    token = str(uuid.uuid4())[:12]
    _share_tokens[token] = page_id
    return {"success": True, "share_token": token}


@wiki_router.get("/shared/wiki/{token}")
async def get_shared_page(token: str):
    """通过分享token获取页面。"""
    page_id = _share_tokens.get(token)
    if not page_id:
        raise HTTPException(status_code=404, detail="分享链接无效或已过期")
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True, "page": page.to_dict()}


@wiki_router.get("/pages/tree/{space_id}")
async def get_page_tree(space_id: str = "default"):
    tree = wiki_store.get_page_tree(space_id)
    return {"success": True, "tree": tree}


@wiki_router.post("/pages/{page_id}/move")
async def move_page(page_id: str, request: PageMoveRequest):
    success = wiki_store.move_page(page_id, request.new_parent_id)
    if not success:
        raise HTTPException(status_code=400, detail="移动页面失败")
    return {"success": True}


# ==================== Version History ====================

@wiki_router.get("/pages/{page_id}/versions")
async def get_page_versions(page_id: str):
    versions = wiki_store.get_page_versions(page_id)
    return {"success": True, "versions": [v.to_dict() for v in versions]}


@wiki_router.get("/pages/{page_id}/versions/{version_number}")
async def get_page_version(page_id: str, version_number: int):
    version = wiki_store.get_page_version(page_id, version_number)
    if not version:
        raise HTTPException(status_code=404, detail="版本不存在")
    return {"success": True, "version": version.to_dict()}


@wiki_router.post("/pages/{page_id}/rollback")
async def rollback_page(page_id: str, request: RollbackRequest):
    page = wiki_store.rollback_page(page_id, request.version_number, request.author)
    if not page:
        raise HTTPException(status_code=404, detail="回滚失败，版本不存在")
    return {"success": True, "page": page.to_dict()}


# ==================== Space CRUD ====================

@wiki_router.post("/spaces/create")
async def create_space(request: SpaceCreateRequest):
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="空间名称不能为空")
    try:
        space = wiki_store.create_space(
            name=request.name,
            description=request.description,
            space_type=request.space_type,
            icon=request.icon,
            owner=request.owner,
        )
        return {"success": True, "space": space.to_dict()}
    except Exception as e:
        logger.error(f"创建空间失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@wiki_router.get("/spaces")
async def list_spaces():
    spaces = wiki_store.list_spaces()
    return {"success": True, "spaces": [s.to_dict() for s in spaces]}


@wiki_router.get("/spaces/{space_id}")
async def get_space(space_id: str):
    space = wiki_store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    return {"success": True, "space": space.to_dict()}


@wiki_router.put("/spaces/{space_id}")
async def update_space(space_id: str, request: SpaceUpdateRequest):
    space = wiki_store.update_space(
        space_id,
        name=request.name,
        description=request.description,
        icon=request.icon,
    )
    if not space:
        raise HTTPException(status_code=404, detail="空间不存在")
    return {"success": True, "space": space.to_dict()}


@wiki_router.delete("/spaces/{space_id}")
async def delete_space(space_id: str):
    success = wiki_store.delete_space(space_id)
    if not success:
        raise HTTPException(status_code=400, detail="删除空间失败（默认空间不可删除）")
    return {"success": True}


# ==================== Tag CRUD ====================

@wiki_router.post("/tags/create")
async def create_tag(request: TagCreateRequest):
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="标签名称不能为空")
    tag = wiki_store.create_tag(name=request.name, color=request.color, space_id=request.space_id)
    return {"success": True, "tag": tag.to_dict()}


@wiki_router.get("/tags")
async def list_tags(space_id: Optional[str] = None):
    tags = wiki_store.list_tags(space_id=space_id)
    return {"success": True, "tags": [t.to_dict() for t in tags]}


@wiki_router.put("/tags/{tag_id}")
async def update_tag(tag_id: str, request: TagUpdateRequest):
    tag = wiki_store.update_tag(tag_id, name=request.name, color=request.color)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"success": True, "tag": tag.to_dict()}


@wiki_router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: str):
    success = wiki_store.delete_tag(tag_id)
    if not success:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"success": True}


@wiki_router.post("/tags/batch-apply")
async def batch_apply_tags(request: BatchTagRequest):
    count = wiki_store.batch_apply_tags(request.page_ids, request.tag_names)
    return {"success": True, "applied_count": count}


# ==================== Permission ====================

@wiki_router.post("/permissions/set")
async def set_permission(request: PermissionSetRequest):
    if request.role not in ["admin", "editor", "reader", "guest"]:
        raise HTTPException(status_code=400, detail="无效的角色")
    perm = wiki_store.set_permission(
        space_id=request.space_id,
        page_id=request.page_id,
        user_id=request.user_id,
        role=request.role,
    )
    return {"success": True, "permission": perm.to_dict()}


@wiki_router.get("/permissions")
async def list_permissions(space_id: Optional[str] = None, page_id: Optional[str] = None):
    perms = wiki_store.list_permissions(space_id=space_id, page_id=page_id)
    return {"success": True, "permissions": [p.to_dict() for p in perms]}


@wiki_router.delete("/permissions/{perm_id}")
async def delete_permission(perm_id: str):
    success = wiki_store.delete_permission(perm_id)
    if not success:
        raise HTTPException(status_code=404, detail="权限记录不存在")
    return {"success": True}


@wiki_router.get("/permissions/check")
async def check_permission(space_id: str, page_id: str, user_id: str, action: str):
    has_perm = wiki_store.check_permission(space_id, page_id, user_id, action)
    return {"success": True, "has_permission": has_perm}


# ==================== Share ====================

@wiki_router.post("/pages/{page_id}/share/enable")
async def enable_share(page_id: str, request: ShareEnableRequest):
    result = wiki_store.enable_share(page_id, request.password)
    if not result:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True, **result}


@wiki_router.post("/pages/{page_id}/share/disable")
async def disable_share(page_id: str):
    success = wiki_store.disable_share(page_id)
    if not success:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True}


@wiki_router.post("/share/access")
async def access_shared_page(request: ShareAccessRequest):
    page = wiki_store.get_shared_page(request.share_token, request.password)
    if not page:
        raise HTTPException(status_code=403, detail="访问失败，链接无效或密码错误")
    return {"success": True, "page": page.to_dict()}


# ==================== Encryption ====================

@wiki_router.post("/pages/{page_id}/encrypt")
async def encrypt_page(page_id: str, request: EncryptRequest):
    success = wiki_store.encrypt_page(page_id, request.password)
    if not success:
        raise HTTPException(status_code=404, detail="页面不存在")
    return {"success": True}


@wiki_router.post("/pages/{page_id}/decrypt")
async def decrypt_page(page_id: str, request: DecryptRequest):
    page = wiki_store.decrypt_page(page_id, request.password)
    if not page:
        raise HTTPException(status_code=403, detail="密码错误")
    return {"success": True, "page": page.to_dict()}


# ==================== Comment ====================

@wiki_router.post("/pages/{page_id}/comments/add")
async def add_comment(page_id: str, request: CommentCreateRequest):
    comment = wiki_store.add_comment(
        page_id=page_id,
        content=request.content,
        author=request.author,
        parent_comment_id=request.parent_comment_id,
        selection_start=request.selection_start,
        selection_end=request.selection_end,
        selection_text=request.selection_text,
        comment_type=request.comment_type,
    )
    return {"success": True, "comment": comment.to_dict()}


@wiki_router.get("/pages/{page_id}/comments")
async def get_comments(page_id: str):
    comments = wiki_store.get_comments(page_id)
    return {"success": True, "comments": [c.to_dict() for c in comments]}


@wiki_router.post("/comments/{comment_id}/resolve")
async def resolve_comment(comment_id: str, page_id: str):
    success = wiki_store.resolve_comment(comment_id, page_id)
    if not success:
        raise HTTPException(status_code=404, detail="评论不存在")
    return {"success": True}


@wiki_router.delete("/comments/{comment_id}")
async def delete_comment(comment_id: str, page_id: str):
    success = wiki_store.delete_comment(comment_id, page_id)
    if not success:
        raise HTTPException(status_code=404, detail="评论不存在")
    return {"success": True}


# ==================== Document Import ====================

@wiki_router.post("/import/webpage")
async def import_webpage(request: WebpageImportRequest):
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="URL不能为空")
    page = await doc_import_service.import_webpage(
        url=request.url, space_id=request.space_id, author=request.author,
    )
    if not page:
        raise HTTPException(status_code=500, detail="网页抓取失败")
    return {"success": True, "page": page.to_dict()}


@wiki_router.post("/import/notion")
async def import_notion(request: NotionImportRequest):
    pages = await doc_import_service.import_notion(
        export_dir=request.export_dir, space_id=request.space_id, author=request.author,
    )
    return {"success": True, "imported_count": len(pages), "pages": [{"page_id": p.page_id, "title": p.title} for p in pages]}


@wiki_router.post("/import/obsidian")
async def import_obsidian(request: ObsidianImportRequest):
    pages = await doc_import_service.import_obsidian(
        vault_dir=request.vault_dir, space_id=request.space_id, author=request.author,
    )
    return {"success": True, "imported_count": len(pages), "pages": [{"page_id": p.page_id, "title": p.title} for p in pages]}


@wiki_router.post("/import/folder")
async def import_folder(request: FolderImportRequest):
    result = await doc_import_service.import_folder(
        folder_path=request.folder_path, space_id=request.space_id, author=request.author,
    )
    return {"success": True, **result}


@wiki_router.post("/import/batch-upload")
async def batch_upload_files(
    files: List[UploadFile] = File(...),
    space_id: str = Form("default"),
    author: str = Form("system"),
):
    import tempfile
    file_paths = []
    temp_dir = tempfile.mkdtemp()
    try:
        for f in files:
            if not f.filename:
                continue
            content = await f.read()
            temp_path = os.path.join(temp_dir, f.filename)
            with open(temp_path, "wb") as out:
                out.write(content)
            file_paths.append(temp_path)
        result = await doc_import_service.batch_import_files(file_paths, space_id, author)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"批量上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==================== AI Document Processing ====================

@wiki_router.post("/ai/summarize/{page_id}")
async def ai_summarize_document(page_id: str):
    result = await ai_doc_service.summarize_document(page_id)
    return result


@wiki_router.post("/ai/split/{page_id}")
async def ai_split_document(page_id: str, rule: str = "chapter", chunk_size: int = 3000):
    result = await ai_doc_service.split_long_document(page_id, rule=rule, chunk_size=chunk_size)
    return result


@wiki_router.post("/ai/chapter-summary/{page_id}")
async def ai_chapter_summary(page_id: str):
    result = await ai_doc_service.summarize_chapters(page_id)
    return result


@wiki_router.post("/ai/mindmap/{page_id}")
async def ai_generate_mindmap(page_id: str):
    result = await ai_doc_service.generate_mindmap(page_id)
    return result


@wiki_router.post("/ai/rewrite/{page_id}")
async def ai_rewrite_text(page_id: str, style: str = "formal"):
    result = await ai_doc_service.rewrite_text(page_id, style=style)
    return result


@wiki_router.post("/ai/polish/{page_id}")
async def ai_polish_text(page_id: str):
    result = await ai_doc_service.polish_text(page_id)
    return result


@wiki_router.post("/ai/copilot")
async def ai_copilot(request: AICopilotRequest):
    """Editor Copilot: retrieve related notes first, then generate a preview.

    The endpoint never mutates the current page. Generated text is returned as
    a preview with evidence so the UI can offer an explicit apply/save action.
    """
    action = str(request.action or "related").strip().lower()
    started = __import__("time").monotonic()
    allowed = {"related", "outline", "expand", "gaps", "rewrite"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的 Copilot 操作：{action}")
    page = wiki_store.get_page(request.page_id) if request.page_id else None
    if request.page_id and not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    selected = (request.selected_text or "").strip()
    base_text = selected or (page.content if page else "")
    if not base_text and action != "related":
        raise HTTPException(status_code=400, detail="当前没有可处理的文本")

    query = selected or (page.title if page else request.instruction) or "相关知识"
    from app.services.semantic_search import semantic_search_service
    search = await semantic_search_service.natural_language_search(
        query[:1000], top_k=max(3, min(int(request.top_k or 5), 10)),
        space_id=request.space_id or (page.space_id if page else None),
    )
    recommendations = search.get("results") or []
    telemetry.record("copilot_retrieval_completed", source="copilot",
                     properties={"action": action, "evidence_count": len(recommendations),
                                 "page_id": request.page_id or ""})
    evidence = [{"title": item.get("title", ""), "source": item.get("source", ""),
                 "page_id": item.get("page_id", ""), "snippet": item.get("snippet", ""),
                 "score": item.get("score", 0)} for item in recommendations]
    if action == "related":
        telemetry.record("copilot_completed", source="copilot", properties={"action": action, "degraded": False, "duration_ms": round((__import__("time").monotonic() - started) * 1000, 1)})
        return {"success": True, "action": action, "preview": "", "evidence": evidence,
                "degraded": False, "message": "已找到相关笔记" if evidence else "暂无相关笔记"}

    evidence_text = "\n".join(f"[{i + 1}] {item['title']}\n{item['snippet']}" for i, item in enumerate(evidence))
    action_desc = {
        "outline": "为当前内容生成层级清晰的 Markdown 大纲",
        "expand": "围绕当前内容补充有依据的段落",
        "gaps": "识别当前内容的证据缺口、未回答问题和需要补充的资料",
        "rewrite": "在保持事实和原意的前提下改写当前内容",
    }[action]
    prompt = (f"任务：{action_desc}。\n只能使用当前文本和相关笔记证据，不得补充外部事实。"
              "如果证据不足，明确标注‘资料不足’，不要猜测。输出可直接预览的 Markdown，不要隐藏推理。\n\n"
              f"当前文本：\n{base_text[:10000]}\n\n相关笔记证据：\n{evidence_text[:8000]}\n\n"
              f"用户补充要求：{(request.instruction or '')[:1000]}")
    generated = await ai_doc_service._call_llm(
        "你是编辑器 Copilot。严格基于输入资料工作，不虚构引用。只输出最终 Markdown。",
        prompt, max_tokens=3000, temperature=0.2,
    )
    degraded = not bool(generated.strip())
    if degraded:
        if action == "outline":
            generated = "\n".join(f"- {line.lstrip('#').strip()}" for line in base_text.splitlines() if line.lstrip().startswith("#")) or "资料不足，无法生成大纲。"
        elif action == "gaps":
            generated = "资料不足，当前未能调用模型完成证据缺口分析。"
        else:
            generated = "资料不足，当前模型不可用，未生成无依据内容。"
    telemetry.record("copilot_completed", source="copilot", properties={"action": action, "degraded": degraded, "duration_ms": round((__import__("time").monotonic() - started) * 1000, 1), "evidence_count": len(evidence)})
    return {"success": True, "action": action, "preview": generated.strip(), "evidence": evidence,
            "degraded": degraded, "message": "已生成预览" if not degraded else "模型不可用，返回安全降级结果"}


@wiki_router.post("/ai/copilot/save")
async def save_copilot_note(request: AICopilotSaveRequest):
    """Persist an explicitly approved Copilot preview as a new wiki page."""
    title = request.title.strip()
    content = request.content.strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")
    tags = list(dict.fromkeys([str(tag).strip() for tag in (request.tags or []) if str(tag).strip()]))
    page = wiki_store.create_page(title=title[:200], content=content[:200000],
                                  space_id=request.space_id or "default", tags=tags,
                                  author=request.author or "copilot")
    telemetry.record("copilot_note_saved", source="copilot", properties={"page_id": page.page_id, "source_page_id": request.source_page_id or "", "tag_count": len(tags)})
    return {"success": True, "page": page.to_dict(), "message": "已保存为新笔记"}


@wiki_router.post("/ai/translate/{page_id}")
async def ai_translate_text(page_id: str, target_lang: str = "en"):
    result = await ai_doc_service.translate_text(page_id, target_lang=target_lang)
    return result


@wiki_router.post("/ai/explain-terms/{page_id}")
async def ai_explain_terms(page_id: str):
    result = await ai_doc_service.explain_terms(page_id)
    return result


# ==================== AI Wiki Creation ====================

@wiki_router.post("/ai/create-page")
async def ai_create_wiki_page(request: AICreatePageRequest):
    result = await ai_wiki_creator_service.create_wiki_page(
        topic=request.topic, space_id=request.space_id, author=request.author,
    )
    return result


@wiki_router.post("/ai/generate-from-notes")
async def ai_generate_from_notes(request: AIGenerateFromNotesRequest):
    result = await ai_wiki_creator_service.generate_from_notes(
        note_ids=request.note_ids, title=request.title, space_id=request.space_id,
    )
    return result


@wiki_router.post("/ai/complete/{page_id}")
async def ai_complete_content(page_id: str, request: AICompleteRequest):
    result = await ai_wiki_creator_service.complete_content(
        page_id=page_id, direction=request.direction,
    )
    return result


@wiki_router.post("/ai/reorganize/{page_id}")
async def ai_reorganize_logic(page_id: str):
    result = await ai_wiki_creator_service.reorganize_logic(page_id)
    return result


@wiki_router.post("/ai/generate-template")
async def ai_generate_template(request: AITemplateRequest):
    result = await ai_wiki_creator_service.generate_template(
        template_type=request.template_type, topic=request.topic, space_id=request.space_id,
    )
    return result


@wiki_router.post("/ai/qa-to-doc")
async def ai_qa_to_document(request: AIQAToDocRequest):
    result = await ai_wiki_creator_service.qa_to_document(
        qa_content=request.qa_content, title=request.title, space_id=request.space_id,
    )
    return result


@wiki_router.post("/ai/archive-conversation")
async def ai_archive_conversation(request: AIArchiveConversationRequest):
    result = await ai_wiki_creator_service.archive_conversation(
        conversation_content=request.conversation_content, title=request.title, space_id=request.space_id,
    )
    return result


# ==================== Semantic Search ====================

@wiki_router.post("/search/semantic")
async def semantic_search(request: SemanticSearchRequest):
    result = await semantic_search_service.natural_language_search(
        query=request.query, top_k=request.top_k, kb_id=request.kb_id, space_id=request.space_id,
    )
    return result


@wiki_router.post("/search/similarity")
async def semantic_similarity_match(request: SimilarityMatchRequest):
    result = await semantic_search_service.semantic_similarity_match(
        text1=request.text1, text2=request.text2,
    )
    return result


@wiki_router.post("/search/fuzzy")
async def fuzzy_knowledge_search(request: FuzzySearchRequest):
    result = await semantic_search_service.fuzzy_knowledge_search(
        query=request.query, top_k=request.top_k, kb_id=request.kb_id,
    )
    return result


@wiki_router.get("/search/recommend/{page_id}")
async def recommend_related(page_id: str, top_k: int = 5):
    result = await semantic_search_service.recommend_related(page_id, top_k=top_k)
    return result


# ==================== Knowledge Association ====================

@wiki_router.get("/association/similar-pages/{node_label}")
async def find_similar_pages(node_label: str, limit: int = 10):
    results = knowledge_association_service.find_similar_pages(node_label, limit=limit)
    return {"success": True, "results": results}


@wiki_router.get("/association/upstream-downstream/{node_id}")
async def find_upstream_downstream(node_id: str, depth: int = 3):
    result = knowledge_association_service.find_upstream_downstream(node_id, depth=depth)
    return {"success": True, **result}


@wiki_router.post("/association/alias/add")
async def add_alias(request: AliasAddRequest):
    result = knowledge_association_service.add_alias(request.standard_name, request.alias)
    return {"success": True, "data": result}


@wiki_router.post("/association/alias/remove")
async def remove_alias(request: AliasRemoveRequest):
    success = knowledge_association_service.remove_alias(request.standard_name, request.alias)
    return {"success": success}


@wiki_router.get("/association/aliases")
async def list_aliases():
    aliases = knowledge_association_service.list_aliases()
    return {"success": True, "aliases": aliases}


@wiki_router.get("/association/normalize/{entity_name}")
async def normalize_entity(entity_name: str):
    normalized = knowledge_association_service.normalize_entity(entity_name)
    return {"success": True, "original": entity_name, "normalized": normalized}


@wiki_router.post("/association/auto-detect-aliases")
async def auto_detect_aliases():
    result = await knowledge_association_service.auto_detect_aliases()
    return result


@wiki_router.post("/association/terms/register")
async def register_term(request: TermRegisterRequest):
    result = knowledge_association_service.register_term(
        term=request.term, definition=request.definition,
        category=request.category, standard_name=request.standard_name,
    )
    return {"success": True, "term": result}


@wiki_router.get("/association/terms")
async def list_terms(category: Optional[str] = None):
    terms = knowledge_association_service.list_terms(category=category)
    return {"success": True, "terms": terms}


@wiki_router.put("/association/terms/{term_id}")
async def update_term(term_id: str, request: TermUpdateRequest):
    result = knowledge_association_service.update_term(
        term_id, term=request.term, definition=request.definition,
        category=request.category, standard_name=request.standard_name,
    )
    if not result:
        raise HTTPException(status_code=404, detail="术语不存在")
    return {"success": True, "term": result}


@wiki_router.delete("/association/terms/{term_id}")
async def delete_term(term_id: str):
    success = knowledge_association_service.delete_term(term_id)
    if not success:
        raise HTTPException(status_code=404, detail="术语不存在")
    return {"success": True}


@wiki_router.get("/kg/visualization")
async def kg_visualization(doc_key: Optional[str] = None,
                           center_node_id: Optional[str] = None,
                           depth: int = 2):
    result = knowledge_association_service.get_graph_visualization_data(
        doc_key=doc_key, center_node_id=center_node_id, depth=depth,
    )
    return {"success": True, **result}


# ==================== System Management ====================

@wiki_router.get("/system/info")
async def get_system_info():
    result = system_manage_service.get_system_info()
    return {"success": True, **result}


@wiki_router.get("/system/model-config")
async def get_model_config():
    result = system_manage_service.get_model_config()
    return {"success": True, **result}


@wiki_router.post("/system/model/llm")
async def set_llm_model(request: ModelConfigRequest):
    result = system_manage_service.set_llm_model(
        model_name=request.model_name, base_url=request.base_url, api_key=request.api_key,
    )
    return result


@wiki_router.post("/system/model/embedding")
async def set_embedding_model(request: EmbeddingModelRequest):
    result = system_manage_service.set_embedding_model(
        model_name=request.model_name, dimension=request.dimension, base_url=request.base_url,
    )
    return result


@wiki_router.get("/system/available-models")
async def list_available_models():
    result = system_manage_service.list_available_models()
    return {"success": True, **result}


@wiki_router.get("/system/model-api-keys")
async def get_model_api_keys():
    result = system_manage_service.get_model_api_keys(reveal=False)
    return {"success": True, **result}


@wiki_router.post("/system/model-api-keys")
async def set_model_api_keys(request: ModelApiKeyCollectionRequest):
    return system_manage_service.set_model_api_keys(request.collection)


@wiki_router.post("/system/model-api-keys/test")
async def test_model_api_key(request: ModelConnectionTestRequest):
    return await asyncio.to_thread(
        system_manage_service.test_model_connection, request.slot, request.config or {}
    )


@wiki_router.get("/system/prompt-config")
async def get_prompt_config(kb_id: str = ""):
    result = system_manage_service.get_prompt_config(kb_id=kb_id)
    return {"success": True, **result}


@wiki_router.post("/system/prompt-config")
async def set_prompt_config(request: PromptConfigRequest):
    result = system_manage_service.set_prompt_config(
        kb_id=request.kb_id, system_prompt=request.system_prompt,
        custom_instructions=request.custom_instructions,
    )
    return {"success": True, "data": result}


@wiki_router.get("/system/prompt-configs")
async def list_prompt_configs():
    result = system_manage_service.list_prompt_configs()
    return {"success": True, "configs": result}


@wiki_router.delete("/system/prompt-config/{kb_id}")
async def delete_prompt_config(kb_id: str):
    success = system_manage_service.delete_prompt_config(kb_id)
    return {"success": success}


@wiki_router.get("/system/chunk-config")
async def get_chunk_config(kb_id: str = ""):
    result = system_manage_service.get_chunk_config(kb_id=kb_id)
    return {"success": True, **result}


@wiki_router.post("/system/chunk-config")
async def set_chunk_config(request: ChunkConfigRequest):
    result = system_manage_service.set_chunk_config(
        kb_id=request.kb_id, chunk_size=request.chunk_size,
        chunk_overlap=request.chunk_overlap, strategy=request.strategy,
    )
    return {"success": True, "data": result}


@wiki_router.get("/system/vector-store/stats")
async def get_vector_store_stats():
    result = system_manage_service.get_vector_store_stats()
    return {"success": True, **result}


@wiki_router.post("/system/vector-store/cleanup")
async def cleanup_vectors(request: VectorCleanupRequest):
    result = system_manage_service.cleanup_vectors(kb_id=request.kb_id, doc_id=request.doc_id)
    return result


@wiki_router.post("/system/vector-store/revectorize")
async def revectorize(kb_id: Optional[str] = None):
    result = system_manage_service.revectorize(kb_id=kb_id)
    return result


# ==================== Batch 2: Enhanced Features ====================

class NotionAPIImportRequest(BaseModel):
    database_id: str
    notion_token: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class EnhancedOCRRequest(BaseModel):
    file_path: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"
    preprocess: Optional[bool] = True


class BatchOCRRequest(BaseModel):
    file_paths: List[str]
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"
    preprocess: Optional[bool] = True


class EnhancedExcelImportRequest(BaseModel):
    file_path: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"
    include_stats: Optional[bool] = True
    max_rows_per_sheet: Optional[int] = 1000


class StructuredExcelImportRequest(BaseModel):
    file_path: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"


class VersionDiffRequest(BaseModel):
    page_id: str
    version1: int
    version2: int


class TextDiffRequest(BaseModel):
    old_text: str
    new_text: str
    context_lines: Optional[int] = 3


class BookmarkQuickSaveRequest(BaseModel):
    url: str
    title: Optional[str] = ""
    selection: Optional[str] = ""
    content: Optional[str] = ""
    description: Optional[str] = ""
    author_name: Optional[str] = ""
    published_at: Optional[str] = ""
    source_type: Optional[str] = "web"
    captured_at: Optional[str] = ""
    tags: Optional[List[str]] = None
    space_id: Optional[str] = "default"
    author: Optional[str] = "bookmarklet"


class AnnotationCreateRequest(BaseModel):
    page_id: str
    content: str
    author: Optional[str] = "system"
    selection_start: int
    selection_end: int
    selection_text: str
    annotation_type: Optional[str] = "highlight"
    color: Optional[str] = "#fef08a"


class AnnotationUpdateRequest(BaseModel):
    content: Optional[str] = None
    annotation_type: Optional[str] = None
    color: Optional[str] = None
    is_resolved: Optional[bool] = None


# ==================== Enhanced Import APIs ====================

@wiki_router.post("/import/notion-api")
async def import_notion_api(request: NotionAPIImportRequest):
    result = await doc_import_service.import_notion_api(
        database_id=request.database_id,
        notion_token=request.notion_token,
        space_id=request.space_id,
        author=request.author,
    )
    return {"success": True, **result}


@wiki_router.post("/import/ocr-enhanced")
async def import_ocr_enhanced(request: EnhancedOCRRequest):
    page = await doc_import_service.import_image_ocr_enhanced(
        file_path=request.file_path,
        space_id=request.space_id,
        author=request.author,
        preprocess=request.preprocess,
    )
    if not page:
        raise HTTPException(status_code=500, detail="OCR识别失败")
    return {"success": True, "page": page.to_dict()}


@wiki_router.post("/import/ocr-batch")
async def batch_ocr(request: BatchOCRRequest):
    result = await doc_import_service.batch_ocr(
        file_paths=request.file_paths,
        space_id=request.space_id,
        author=request.author,
        preprocess=request.preprocess,
    )
    return {"success": True, **result}


@wiki_router.post("/import/excel-enhanced")
async def import_excel_enhanced(request: EnhancedExcelImportRequest):
    page = await doc_import_service.import_excel_enhanced(
        file_path=request.file_path,
        space_id=request.space_id,
        author=request.author,
        include_stats=request.include_stats,
        max_rows_per_sheet=request.max_rows_per_sheet,
    )
    if not page:
        raise HTTPException(status_code=500, detail="Excel导入失败")
    return {"success": True, "page": page.to_dict()}


@wiki_router.post("/import/excel-structured")
async def import_excel_structured(request: StructuredExcelImportRequest):
    result = await doc_import_service.import_excel_to_structured(
        file_path=request.file_path,
        space_id=request.space_id,
        author=request.author,
    )
    return result


# ==================== Version Diff APIs ====================

@wiki_router.post("/versions/diff")
async def compute_version_diff(request: VersionDiffRequest):
    result = version_diff_service.compute_version_diff(
        page_id=request.page_id,
        version1=request.version1,
        version2=request.version2,
    )
    return result


@wiki_router.post("/versions/text-diff")
async def compute_text_diff(request: TextDiffRequest):
    result = version_diff_service.compute_diff(
        old_text=request.old_text,
        new_text=request.new_text,
        context_lines=request.context_lines,
    )
    return result


@wiki_router.get("/pages/{page_id}/history-diff")
async def get_page_history_diff(page_id: str):
    result = version_diff_service.compute_page_history_diff(page_id)
    return result


# ==================== Bookmark APIs ====================

@wiki_router.post("/bookmarks/quick-save")
async def bookmark_quick_save(request: BookmarkQuickSaveRequest):
    result = await bookmark_service.quick_save(
        url=request.url,
        title=request.title,
        selection=request.selection,
        content=request.content,
        description=request.description,
        author_name=request.author_name,
        published_at=request.published_at,
        source_type=request.source_type,
        captured_at=request.captured_at,
        tags=request.tags,
        space_id=request.space_id,
        author=request.author,
    )
    return result


@wiki_router.get("/bookmarklet/save")
async def bookmarklet_save(url: str, title: str = "", selection: str = ""):
    result = await bookmark_service.quick_save(
        url=url, title=title, selection=selection,
    )
    if result.get("success"):
        return HTMLResponse(content=f"""
        <html><head><title>保存成功</title></head>
        <body style="font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">
            <div style="text-align:center">
                <h2 style="color:#22c55e">✅ 保存成功！</h2>
                <p>页面 <strong>{title or url}</strong> 已保存到Wiki</p>
                <p><a href="javascript:window.close()">关闭此窗口</a></p>
            </div>
        </body></html>
        """)
    else:
        return HTMLResponse(content=f"""
        <html><head><title>保存失败</title></head>
        <body style="font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">
            <div style="text-align:center">
                <h2 style="color:#ef4444">❌ 保存失败</h2>
                <p>{result.get('error', '未知错误')}</p>
            </div>
        </body></html>
        """)


@wiki_router.get("/bookmarks")
async def list_bookmarks(space_id: Optional[str] = None, tag: Optional[str] = None):
    result = bookmark_service.list_bookmarks(space_id=space_id, tag=tag)
    return result


@wiki_router.delete("/bookmarks/{bookmark_id}")
async def delete_bookmark(bookmark_id: str):
    result = bookmark_service.delete_bookmark(bookmark_id)
    return result


@wiki_router.get("/bookmarklet/script")
async def get_bookmarklet_script():
    script = bookmark_service.get_bookmarklet_script()
    return {"success": True, "script": script}


# ==================== Annotation APIs ====================

@wiki_router.post("/annotations/create")
async def create_annotation(request: AnnotationCreateRequest):
    comment = wiki_store.add_comment(
        page_id=request.page_id,
        content=request.content,
        author=request.author,
        selection_start=request.selection_start,
        selection_end=request.selection_end,
        selection_text=request.selection_text,
        comment_type="annotation",
    )
    return {"success": True, "annotation": comment.to_dict()}


@wiki_router.get("/annotations/{page_id}")
async def get_annotations(page_id: str):
    comments = wiki_store.get_comments(page_id)
    annotations = [c for c in comments if c.comment_type == "annotation"]
    return {"success": True, "annotations": [a.to_dict() for a in annotations]}


@wiki_router.put("/annotations/{annotation_id}")
async def update_annotation(annotation_id: str, page_id: str, request: AnnotationUpdateRequest):
    comments = wiki_store._load_comments(page_id)
    for c in comments:
        if c.comment_id == annotation_id:
            if request.content is not None:
                c.content = request.content
            if request.is_resolved is not None:
                c.is_resolved = request.is_resolved
            wiki_store._save_comments(page_id)
            return {"success": True, "annotation": c.to_dict()}
    raise HTTPException(status_code=404, detail="批注不存在")


# ==================== Batch 3: Full Text Search ====================

class FullTextSearchRequest(BaseModel):
    query: str
    space_id: Optional[str] = None
    tags: Optional[List[str]] = None
    author: Optional[str] = None
    page: Optional[int] = 1
    page_size: Optional[int] = 20
    fuzzy: Optional[bool] = True
    highlight: Optional[bool] = True


@wiki_router.post("/search/fulltext")
async def fulltext_search(request: FullTextSearchRequest):
    result = fulltext_search_service.search(
        query=request.query,
        space_id=request.space_id,
        tags=request.tags,
        author=request.author,
        page=request.page,
        page_size=request.page_size,
        fuzzy=request.fuzzy,
        highlight=request.highlight,
    )
    return result


@wiki_router.post("/search/rebuild-index")
async def rebuild_search_index():
    result = fulltext_search_service.rebuild_index()
    return result


@wiki_router.get("/search/stats")
async def get_search_stats():
    return fulltext_search_service.get_search_stats()


# ==================== Batch 3: Wiki Templates ====================

class TemplateCreateRequest(BaseModel):
    name: str
    content: str
    description: Optional[str] = ""
    category: Optional[str] = "自定义"


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None


class TemplateApplyRequest(BaseModel):
    template_id: str
    title: str
    space_id: Optional[str] = "default"
    author: Optional[str] = "system"
    variables: Optional[dict] = None


@wiki_router.get("/templates")
async def list_templates(category: Optional[str] = None):
    templates = wiki_template_service.list_templates(category=category)
    return {"success": True, "templates": templates}


@wiki_router.get("/templates/categories")
async def list_template_categories():
    categories = wiki_template_service.list_categories()
    return {"success": True, "categories": categories}


@wiki_router.post("/templates/apply")
async def apply_template(request: TemplateApplyRequest):
    result = wiki_template_service.create_page_from_template(
        template_id=request.template_id, title=request.title,
        space_id=request.space_id, author=request.author,
        variables=request.variables,
    )
    if not result:
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


@wiki_router.get("/templates/{template_id}")
async def get_template(template_id: str):
    template = wiki_template_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"success": True, "template": template}


@wiki_router.post("/templates/create")
async def create_template(request: TemplateCreateRequest):
    template = wiki_template_service.create_template(
        name=request.name, content=request.content,
        description=request.description, category=request.category,
    )
    return {"success": True, "template": template}


@wiki_router.put("/templates/{template_id}")
async def update_template(template_id: str, request: TemplateUpdateRequest):
    result = wiki_template_service.update_template(
        template_id=template_id, name=request.name, content=request.content,
        description=request.description, category=request.category,
    )
    if not result:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"success": True, "template": result}


@wiki_router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    success = wiki_template_service.delete_template(template_id)
    if not success:
        raise HTTPException(status_code=404, detail="模板不存在或为内置模板")
    return {"success": True}


# ==================== Batch 3: Page Embed ====================

class PageEmbedResolveRequest(BaseModel):
    content: str
    max_depth: Optional[int] = 5


@wiki_router.post("/embeds/resolve")
async def resolve_embeds(request: PageEmbedResolveRequest):
    resolved = page_embed_service.resolve_embeds(request.content, max_depth=request.max_depth)
    return {"success": True, "resolved_content": resolved}


@wiki_router.get("/embeds/references/{page_id}")
async def get_embed_references(page_id: str):
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    refs = page_embed_service.find_embed_references(page.content)
    return {"success": True, "references": refs}


# ==================== Batch 3: Attachments ====================

@wiki_router.post("/attachments/upload/{page_id}")
async def upload_attachment(page_id: str, file: UploadFile = File(...),
                            author: str = Form("system"),
                            description: str = Form("")):
    page = wiki_store.get_page(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="页面不存在")
    file_data = await file.read()
    if len(file_data) > settings.MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件大小超过限制")
    att = attachment_service.upload(
        page_id=page_id, file_data=file_data,
        original_name=file.filename or "unknown",
        author=author, description=description,
    )
    audit_log_service.log("upload_attachment", "attachment", att.attach_id, author)
    return {"success": True, "attachment": att.to_dict()}


@wiki_router.get("/attachments/stats")
async def get_attachment_stats():
    return attachment_service.get_stats()


@wiki_router.get("/attachments/download/{attach_id}")
async def download_attachment(attach_id: str):
    att = attachment_service.get(attach_id)
    if not att:
        raise HTTPException(status_code=404, detail="附件不存在")
    file_path = attachment_service.get_file_path(attach_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="文件不存在")
    from fastapi.responses import FileResponse
    return FileResponse(file_path, filename=att.original_name, media_type=att.mime_type)


@wiki_router.delete("/attachments/delete/{attach_id}")
async def delete_attachment(attach_id: str):
    success = attachment_service.delete(attach_id)
    if not success:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"success": True}


@wiki_router.get("/attachments/preview/{attach_id}")
async def preview_attachment(attach_id: str):
    if not attachment_service.can_preview(attach_id):
        raise HTTPException(status_code=400, detail="该文件类型不支持预览")
    file_path = attachment_service.get_file_path(attach_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="文件不存在")
    att = attachment_service.get(attach_id)
    from fastapi.responses import FileResponse
    return FileResponse(file_path, media_type=att.mime_type)


@wiki_router.get("/attachments/{page_id}")
async def list_attachments(page_id: str):
    attachments = attachment_service.list_by_page(page_id)
    return {"success": True, "attachments": [a.to_dict() for a in attachments]}


# ==================== Batch 3: Page Export ====================

class PageExportRequest(BaseModel):
    page_id: str
    format: str = "markdown"
    resolve_embeds: Optional[bool] = True


@wiki_router.post("/export")
async def export_page(request: PageExportRequest):
    exporters = {
        "markdown": page_export_service.export_markdown,
        "html": page_export_service.export_html,
        "pdf": page_export_service.export_pdf,
        "word": page_export_service.export_word,
    }
    exporter = exporters.get(request.format)
    if not exporter:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式: {request.format}")
    result = exporter(request.page_id, resolve_embeds=request.resolve_embeds)
    if not result:
        raise HTTPException(status_code=404, detail="页面不存在")
    if result.get("success") and result.get("filepath"):
        from fastapi.responses import FileResponse
        return FileResponse(
            result["filepath"],
            filename=result.get("filename", "export"),
            media_type="application/octet-stream",
        )
    return result


@wiki_router.get("/export/download/{filename}")
async def download_export(filename: str):
    export_dir = os.path.join(settings.DATA_DIR, "exports")
    filepath = os.path.join(export_dir, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="导出文件不存在")
    from fastapi.responses import FileResponse
    return FileResponse(filepath, filename=filename)


# ==================== Batch 3: Recycle Bin ====================

@wiki_router.post("/recycle-bin/soft-delete/{page_id}")
async def soft_delete_page(page_id: str, deleted_by: str = "system"):
    success = recycle_bin_service.soft_delete(page_id, deleted_by=deleted_by)
    if not success:
        raise HTTPException(status_code=404, detail="页面不存在")
    audit_log_service.log("soft_delete_page", "page", page_id, deleted_by)
    return {"success": True}


@wiki_router.post("/recycle-bin/restore/{page_id}")
async def restore_page(page_id: str):
    result = recycle_bin_service.restore(page_id)
    if not result:
        raise HTTPException(status_code=404, detail="回收站中不存在该页面")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "恢复失败"))
    audit_log_service.log("restore_page", "page", page_id)
    return result


@wiki_router.delete("/recycle-bin/permanent/{page_id}")
async def permanent_delete_page(page_id: str):
    success = recycle_bin_service.permanent_delete(page_id)
    if not success:
        raise HTTPException(status_code=404, detail="回收站中不存在该页面")
    audit_log_service.log("permanent_delete", "page", page_id)
    return {"success": True}


@wiki_router.get("/recycle-bin/list")
async def list_recycle_bin(space_id: Optional[str] = None):
    items = recycle_bin_service.list_deleted(space_id=space_id)
    return {"success": True, "items": items, "total": len(items)}


@wiki_router.get("/recycle-bin/detail/{page_id}")
async def get_recycle_bin_detail(page_id: str):
    detail = recycle_bin_service.get_deleted_page(page_id)
    if not detail:
        raise HTTPException(status_code=404, detail="回收站中不存在该页面")
    return {"success": True, **detail}


@wiki_router.post("/recycle-bin/empty")
async def empty_recycle_bin():
    count = recycle_bin_service.empty_bin()
    audit_log_service.log("empty_recycle_bin", "recycle_bin", "", "system", {"count": count})
    return {"success": True, "deleted_count": count}


# ==================== Batch 3: Audit Log ====================

class AuditLogQueryRequest(BaseModel):
    action: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    user_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    page: Optional[int] = 1
    page_size: Optional[int] = 50


@wiki_router.post("/audit-logs/query")
async def query_audit_logs(request: AuditLogQueryRequest):
    result = audit_log_service.query(
        action=request.action,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        user_id=request.user_id,
        start_time=request.start_time,
        end_time=request.end_time,
        page=request.page,
        page_size=request.page_size,
    )
    return result


@wiki_router.get("/audit-logs/stats")
async def get_audit_stats():
    return audit_log_service.get_stats()


# ==================== Batch 3: Email Notification ====================

class EmailConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    from_address: Optional[str] = None
    notify_on_page_update: Optional[bool] = None
    notify_on_comment: Optional[bool] = None
    notify_on_annotation: Optional[bool] = None


class EmailSubscriberRequest(BaseModel):
    email: str
    notify_types: Optional[List[str]] = None


class EmailNotifyRequest(BaseModel):
    event_type: str
    title: str
    content: str
    page_id: Optional[str] = ""
    author: Optional[str] = "system"


class EmailTestSendRequest(BaseModel):
    recipient: str


@wiki_router.get("/email/config")
async def get_email_config():
    return {"success": True, "config": email_notification_service.get_config()}


@wiki_router.post("/email/config")
async def update_email_config(request: EmailConfigUpdateRequest):
    kwargs = {k: v for k, v in request.model_dump().items() if v is not None}
    result = email_notification_service.update_config(**kwargs)
    return result


@wiki_router.post("/email/test")
async def test_email_connection():
    return email_notification_service.test_connection()


@wiki_router.post("/email/test-send")
async def test_email_send(request: EmailTestSendRequest):
    """Send a real test message; this is intentionally separate from a TCP/login test."""
    return email_notification_service.test_send(request.recipient)


@wiki_router.post("/email/subscribers")
async def add_email_subscriber(request: EmailSubscriberRequest):
    result = email_notification_service.add_subscriber(request.email, request.notify_types)
    return result


@wiki_router.delete("/email/subscribers/{email:path}")
async def remove_email_subscriber(email: str):
    success = email_notification_service.remove_subscriber(email)
    if not success:
        raise HTTPException(status_code=404, detail="订阅者不存在")
    return {"success": True}


@wiki_router.post("/email/notify")
async def send_email_notification(request: EmailNotifyRequest):
    result = email_notification_service.notify(
        event_type=request.event_type, title=request.title,
        content=request.content, page_id=request.page_id, author=request.author,
    )
    return result


@wiki_router.get("/email/queue")
async def get_email_queue(status: Optional[str] = None):
    queue = email_notification_service.get_queue(status=status)
    return {"success": True, "queue": queue}


@wiki_router.post("/email/retry-failed")
async def retry_failed_emails():
    result = email_notification_service.retry_failed()
    return result


# ==================== Batch 3: API Key Management ====================

class ApiKeyCreateRequest(BaseModel):
    name: str
    permissions: Optional[List[str]] = None
    expires_days: Optional[int] = 0
    created_by: Optional[str] = "admin"


class ApiKeyValidateRequest(BaseModel):
    api_key: str
    required_permission: Optional[str] = "read"


@wiki_router.post("/api-keys/create")
async def create_api_key(request: ApiKeyCreateRequest):
    result = api_key_service.create_key(
        name=request.name, permissions=request.permissions,
        expires_days=request.expires_days, created_by=request.created_by,
    )
    return result


@wiki_router.get("/api-keys/list")
async def list_api_keys():
    keys = api_key_service.list_keys()
    return {"success": True, "keys": keys}


@wiki_router.post("/api-keys/validate")
async def validate_api_key(request: ApiKeyValidateRequest):
    result = api_key_service.validate_key(request.api_key, request.required_permission)
    return result


@wiki_router.post("/api-keys/revoke/{key_id}")
async def revoke_api_key(key_id: str):
    success = api_key_service.revoke_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API密钥不存在")
    return {"success": True}


@wiki_router.delete("/api-keys/delete/{key_id}")
async def delete_api_key(key_id: str):
    success = api_key_service.delete_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="API密钥不存在")
    return {"success": True}


@wiki_router.get("/api-keys/stats")
async def get_api_key_stats():
    return {"success": True, **api_key_service.get_stats()}


# ==================== Batch 3: Backup & Restore ====================

class BackupCreateRequest(BaseModel):
    description: Optional[str] = ""
    backup_type: Optional[str] = "full"
    created_by: Optional[str] = "admin"


class BackupRestoreRequest(BaseModel):
    backup_id: str
    restore_type: Optional[str] = "replace"
    created_by: Optional[str] = "admin"


@wiki_router.post("/backups/create")
async def create_backup(request: BackupCreateRequest):
    result = backup_service.create_backup(
        description=request.description,
        backup_type=request.backup_type,
        created_by=request.created_by,
    )
    audit_log_service.log("create_backup", "backup", result.get("backup_id", ""), request.created_by)
    return result


@wiki_router.get("/backups/list")
async def list_backups():
    backups = backup_service.list_backups()
    return {"success": True, "backups": backups}


@wiki_router.post("/backups/restore")
async def restore_backup(request: BackupRestoreRequest):
    result = backup_service.restore_backup(
        backup_id=request.backup_id,
        restore_type=request.restore_type,
        created_by=request.created_by,
    )
    audit_log_service.log("restore_backup", "backup", request.backup_id, request.created_by)
    return result


@wiki_router.delete("/backups/delete/{backup_id}")
async def delete_backup(backup_id: str):
    success = backup_service.delete_backup(backup_id)
    if not success:
        raise HTTPException(status_code=404, detail="备份不存在")
    return {"success": True}


@wiki_router.get("/backups/download/{backup_id}")
async def download_backup(backup_id: str):
    filepath = backup_service.download_backup(backup_id)
    if not filepath:
        raise HTTPException(status_code=404, detail="备份文件不存在")
    from fastapi.responses import FileResponse
    return FileResponse(filepath, filename=os.path.basename(filepath),
                        media_type="application/zip")


@wiki_router.get("/backups/stats")
async def get_backup_stats():
    return {"success": True, **backup_service.get_stats()}
