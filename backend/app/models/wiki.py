import os
import json
import uuid
import logging
import shutil
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.core.config import settings

logger = logging.getLogger(__name__)


class WikiPage:
    def __init__(
        self,
        page_id: str = "",
        title: str = "",
        content: str = "",
        space_id: str = "default",
        parent_id: str = "",
        page_type: str = "markdown",
        tags: Optional[List[str]] = None,
        author: str = "system",
        sort_order: int = 0,
        is_encrypted: bool = False,
        encrypt_password_hash: str = "",
        share_token: str = "",
        share_password: str = "",
        share_enabled: bool = False,
        is_public: bool = False,
    ):
        self.page_id = page_id or str(uuid.uuid4())
        self.title = title
        self.content = content
        self.space_id = space_id
        self.parent_id = parent_id
        self.page_type = page_type
        self.tags = tags or []
        self.author = author
        self.sort_order = sort_order
        self.is_encrypted = is_encrypted
        self.encrypt_password_hash = encrypt_password_hash
        self.share_token = share_token
        self.share_password = share_password
        self.share_enabled = share_enabled
        self.is_public = is_public
        self.created_at = ""
        self.updated_at = ""
        self.version = 1

    def to_dict(self) -> Dict:
        return {
            "page_id": self.page_id,
            "title": self.title,
            "content": self.content,
            "space_id": self.space_id,
            "parent_id": self.parent_id,
            "page_type": self.page_type,
            "tags": self.tags,
            "author": self.author,
            "sort_order": self.sort_order,
            "is_encrypted": self.is_encrypted,
            "encrypt_password_hash": self.encrypt_password_hash,
            "share_token": self.share_token,
            "share_password": self.share_password,
            "share_enabled": self.share_enabled,
            "is_public": self.is_public,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "WikiPage":
        page = cls(
            page_id=data.get("page_id", ""),
            title=data.get("title", ""),
            content=data.get("content", ""),
            space_id=data.get("space_id", "default"),
            parent_id=data.get("parent_id", ""),
            page_type=data.get("page_type", "markdown"),
            tags=data.get("tags", []),
            author=data.get("author", "system"),
            sort_order=data.get("sort_order", 0),
            is_encrypted=data.get("is_encrypted", False),
            encrypt_password_hash=data.get("encrypt_password_hash", ""),
            share_token=data.get("share_token", ""),
            share_password=data.get("share_password", ""),
            share_enabled=data.get("share_enabled", False),
            is_public=data.get("is_public", False),
        )
        page.created_at = data.get("created_at", "")
        page.updated_at = data.get("updated_at", "")
        page.version = data.get("version", 1)
        return page


class PageVersion:
    def __init__(
        self,
        version_id: str = "",
        page_id: str = "",
        version_number: int = 1,
        title: str = "",
        content: str = "",
        author: str = "system",
        change_summary: str = "",
        diff_summary: str = "",
    ):
        self.version_id = version_id or str(uuid.uuid4())
        self.page_id = page_id
        self.version_number = version_number
        self.title = title
        self.content = content
        self.author = author
        self.change_summary = change_summary
        self.diff_summary = diff_summary
        self.created_at = ""

    def to_dict(self) -> Dict:
        return {
            "version_id": self.version_id,
            "page_id": self.page_id,
            "version_number": self.version_number,
            "title": self.title,
            "content": self.content,
            "author": self.author,
            "change_summary": self.change_summary,
            "diff_summary": self.diff_summary,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PageVersion":
        v = cls(
            version_id=data.get("version_id", ""),
            page_id=data.get("page_id", ""),
            version_number=data.get("version_number", 1),
            title=data.get("title", ""),
            content=data.get("content", ""),
            author=data.get("author", "system"),
            change_summary=data.get("change_summary", ""),
            diff_summary=data.get("diff_summary", ""),
        )
        v.created_at = data.get("created_at", "")
        return v


class WikiSpace:
    def __init__(
        self,
        space_id: str = "",
        name: str = "",
        description: str = "",
        space_type: str = "project",
        icon: str = "",
        owner: str = "system",
    ):
        self.space_id = space_id or str(uuid.uuid4())
        self.name = name
        self.description = description
        self.space_type = space_type
        self.icon = icon
        self.owner = owner
        self.created_at = ""
        self.updated_at = ""

    def to_dict(self) -> Dict:
        return {
            "space_id": self.space_id,
            "name": self.name,
            "description": self.description,
            "space_type": self.space_type,
            "icon": self.icon,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "WikiSpace":
        s = cls(
            space_id=data.get("space_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            space_type=data.get("space_type", "project"),
            icon=data.get("icon", ""),
            owner=data.get("owner", "system"),
        )
        s.created_at = data.get("created_at", "")
        s.updated_at = data.get("updated_at", "")
        return s


class Tag:
    def __init__(self, tag_id: str = "", name: str = "", color: str = "#6b7280", space_id: str = "default"):
        self.tag_id = tag_id or str(uuid.uuid4())
        self.name = name
        self.color = color
        self.space_id = space_id
        self.created_at = ""

    def to_dict(self) -> Dict:
        return {
            "tag_id": self.tag_id,
            "name": self.name,
            "color": self.color,
            "space_id": self.space_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Tag":
        t = cls(
            tag_id=data.get("tag_id", ""),
            name=data.get("name", ""),
            color=data.get("color", "#6b7280"),
            space_id=data.get("space_id", "default"),
        )
        t.created_at = data.get("created_at", "")
        return t


class RolePermission:
    ROLE_ADMIN = "admin"
    ROLE_EDITOR = "editor"
    ROLE_READER = "reader"
    ROLE_GUEST = "guest"

    PERMISSIONS = {
        ROLE_ADMIN: ["read", "write", "delete", "manage", "share", "admin"],
        ROLE_EDITOR: ["read", "write", "share"],
        ROLE_READER: ["read"],
        ROLE_GUEST: ["read"],
    }

    def __init__(
        self,
        perm_id: str = "",
        space_id: str = "default",
        page_id: str = "",
        user_id: str = "",
        role: str = "reader",
    ):
        self.perm_id = perm_id or str(uuid.uuid4())
        self.space_id = space_id
        self.page_id = page_id
        self.user_id = user_id
        self.role = role
        self.created_at = ""

    def to_dict(self) -> Dict:
        return {
            "perm_id": self.perm_id,
            "space_id": self.space_id,
            "page_id": self.page_id,
            "user_id": self.user_id,
            "role": self.role,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "RolePermission":
        p = cls(
            perm_id=data.get("perm_id", ""),
            space_id=data.get("space_id", "default"),
            page_id=data.get("page_id", ""),
            user_id=data.get("user_id", ""),
            role=data.get("role", "reader"),
        )
        p.created_at = data.get("created_at", "")
        return p

    def has_permission(self, action: str) -> bool:
        return action in self.PERMISSIONS.get(self.role, [])


class Comment:
    def __init__(
        self,
        comment_id: str = "",
        page_id: str = "",
        content: str = "",
        author: str = "system",
        parent_comment_id: str = "",
        selection_start: int = -1,
        selection_end: int = -1,
        selection_text: str = "",
        comment_type: str = "comment",
    ):
        self.comment_id = comment_id or str(uuid.uuid4())
        self.page_id = page_id
        self.content = content
        self.author = author
        self.parent_comment_id = parent_comment_id
        self.selection_start = selection_start
        self.selection_end = selection_end
        self.selection_text = selection_text
        self.comment_type = comment_type
        self.is_resolved = False
        self.created_at = ""
        self.updated_at = ""

    def to_dict(self) -> Dict:
        return {
            "comment_id": self.comment_id,
            "page_id": self.page_id,
            "content": self.content,
            "author": self.author,
            "parent_comment_id": self.parent_comment_id,
            "selection_start": self.selection_start,
            "selection_end": self.selection_end,
            "selection_text": self.selection_text,
            "comment_type": self.comment_type,
            "is_resolved": self.is_resolved,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Comment":
        c = cls(
            comment_id=data.get("comment_id", ""),
            page_id=data.get("page_id", ""),
            content=data.get("content", ""),
            author=data.get("author", "system"),
            parent_comment_id=data.get("parent_comment_id", ""),
            selection_start=data.get("selection_start", -1),
            selection_end=data.get("selection_end", -1),
            selection_text=data.get("selection_text", ""),
            comment_type=data.get("comment_type", "comment"),
        )
        c.is_resolved = data.get("is_resolved", False)
        c.created_at = data.get("created_at", "")
        c.updated_at = data.get("updated_at", "")
        return c


class WikiStore:
    def __init__(self):
        self._wiki_dir = os.path.join(settings.DATA_DIR, "wiki")
        self._pages_dir = os.path.join(self._wiki_dir, "pages")
        self._versions_dir = os.path.join(self._wiki_dir, "versions")
        self._spaces_dir = os.path.join(self._wiki_dir, "spaces")
        self._tags_dir = os.path.join(self._wiki_dir, "tags")
        self._permissions_dir = os.path.join(self._wiki_dir, "permissions")
        self._comments_dir = os.path.join(self._wiki_dir, "comments")
        self._registry_file = os.path.join(self._wiki_dir, "registry.json")

        self._pages: Dict[str, WikiPage] = {}
        self._versions: Dict[str, List[PageVersion]] = {}
        self._spaces: Dict[str, WikiSpace] = {}
        self._tags: Dict[str, Tag] = {}
        self._permissions: Dict[str, RolePermission] = {}
        self._comments: Dict[str, List[Comment]] = {}

        for d in [self._wiki_dir, self._pages_dir, self._versions_dir,
                  self._spaces_dir, self._tags_dir, self._permissions_dir,
                  self._comments_dir]:
            os.makedirs(d, exist_ok=True)

        self._load_all()

    def _load_all(self):
        self._load_registry()
        self._load_spaces()
        self._load_tags()
        self._load_permissions()
        logger.info(f"Wiki存储加载完成: {len(self._pages)} 页面, {len(self._spaces)} 空间, {len(self._tags)} 标签")

    def _load_registry(self):
        if not os.path.exists(self._registry_file):
            self._ensure_default_space()
            return
        try:
            with open(self._registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for page_data in data.get("pages", []):
                page = WikiPage.from_dict(page_data)
                self._pages[page.page_id] = page
            self._load_versions_for_pages()
        except Exception as e:
            logger.error(f"Wiki注册表加载失败: {e}")
            self._ensure_default_space()

    def _load_versions_for_pages(self):
        for page_id in self._pages:
            version_file = os.path.join(self._versions_dir, f"{page_id}.json")
            if os.path.exists(version_file):
                try:
                    with open(version_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._versions[page_id] = [PageVersion.from_dict(v) for v in data.get("versions", [])]
                except Exception as e:
                    logger.error(f"页面 {page_id} 版本历史加载失败: {e}")
                    self._versions[page_id] = []

    def _load_spaces(self):
        spaces_file = os.path.join(self._spaces_dir, "spaces.json")
        if os.path.exists(spaces_file):
            try:
                with open(spaces_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for s in data.get("spaces", []):
                    space = WikiSpace.from_dict(s)
                    self._spaces[space.space_id] = space
            except Exception as e:
                logger.error(f"空间数据加载失败: {e}")

    def _load_tags(self):
        tags_file = os.path.join(self._tags_dir, "tags.json")
        if os.path.exists(tags_file):
            try:
                with open(tags_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for t in data.get("tags", []):
                    tag = Tag.from_dict(t)
                    self._tags[tag.tag_id] = tag
            except Exception as e:
                logger.error(f"标签数据加载失败: {e}")

    def _load_permissions(self):
        perms_file = os.path.join(self._permissions_dir, "permissions.json")
        if os.path.exists(perms_file):
            try:
                with open(perms_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for p in data.get("permissions", []):
                    perm = RolePermission.from_dict(p)
                    self._permissions[perm.perm_id] = perm
            except Exception as e:
                logger.error(f"权限数据加载失败: {e}")

    def _ensure_default_space(self):
        if "default" not in self._spaces:
            space = WikiSpace(space_id="default", name="默认空间", description="系统默认知识空间", space_type="project")
            space.created_at = datetime.now().isoformat()
            space.updated_at = space.created_at
            self._spaces["default"] = space
            self._save_spaces()

    def _save_registry(self):
        data = {"pages": [p.to_dict() for p in self._pages.values()]}
        with open(self._registry_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_spaces(self):
        data = {"spaces": [s.to_dict() for s in self._spaces.values()]}
        with open(os.path.join(self._spaces_dir, "spaces.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_tags(self):
        data = {"tags": [t.to_dict() for t in self._tags.values()]}
        with open(os.path.join(self._tags_dir, "tags.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_permissions(self):
        data = {"permissions": [p.to_dict() for p in self._permissions.values()]}
        with open(os.path.join(self._permissions_dir, "permissions.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_versions(self, page_id: str):
        versions = self._versions.get(page_id, [])
        version_file = os.path.join(self._versions_dir, f"{page_id}.json")
        data = {"versions": [v.to_dict() for v in versions]}
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_comments(self, page_id: str):
        comments = self._comments.get(page_id, [])
        comments_file = os.path.join(self._comments_dir, f"{page_id}.json")
        data = {"comments": [c.to_dict() for c in comments]}
        with open(comments_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_comments(self, page_id: str) -> List[Comment]:
        if page_id in self._comments:
            return self._comments[page_id]
        comments_file = os.path.join(self._comments_dir, f"{page_id}.json")
        if os.path.exists(comments_file):
            try:
                with open(comments_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._comments[page_id] = [Comment.from_dict(c) for c in data.get("comments", [])]
                return self._comments[page_id]
            except Exception as e:
                logger.error(f"评论数据加载失败: {e}")
        self._comments[page_id] = []
        return self._comments[page_id]

    # ==================== Page CRUD ====================

    def create_page(self, title: str, content: str = "", space_id: str = "default",
                    parent_id: str = "", page_type: str = "markdown",
                    tags: Optional[List[str]] = None, author: str = "system") -> WikiPage:
        page = WikiPage(
            title=title,
            content=content,
            space_id=space_id,
            parent_id=parent_id,
            page_type=page_type,
            tags=tags or [],
            author=author,
        )
        now = datetime.now().isoformat()
        page.created_at = now
        page.updated_at = now
        self._pages[page.page_id] = page

        version = PageVersion(
            page_id=page.page_id,
            version_number=1,
            title=title,
            content=content,
            author=author,
            change_summary="创建页面",
        )
        version.created_at = now
        self._versions[page.page_id] = [version]

        self._save_registry()
        self._save_versions(page.page_id)
        logger.info(f"Wiki页面创建: {title} ({page.page_id})")
        return page

    def get_page(self, page_id: str) -> Optional[WikiPage]:
        return self._pages.get(page_id)

    def update_page(self, page_id: str, title: Optional[str] = None,
                    content: Optional[str] = None, tags: Optional[List[str]] = None,
                    author: str = "system", change_summary: str = "") -> Optional[WikiPage]:
        page = self._pages.get(page_id)
        if not page:
            return None

        old_content = page.content
        old_title = page.title

        if title is not None:
            page.title = title
        if content is not None:
            page.content = content
        if tags is not None:
            page.tags = tags

        page.version += 1
        page.updated_at = datetime.now().isoformat()

        version = PageVersion(
            page_id=page_id,
            version_number=page.version,
            title=page.title,
            content=page.content,
            author=author,
            change_summary=change_summary or f"更新至版本 {page.version}",
            diff_summary=self._compute_diff_summary(old_content, page.content),
        )
        version.created_at = page.updated_at
        self._versions.setdefault(page_id, []).append(version)

        self._save_registry()
        self._save_versions(page_id)
        logger.info(f"Wiki页面更新: {page.title} v{page.version}")
        return page

    def delete_page(self, page_id: str) -> bool:
        if page_id not in self._pages:
            return False

        for child in list(self._pages.values()):
            if child.parent_id == page_id:
                child.parent_id = self._pages[page_id].parent_id

        del self._pages[page_id]
        self._versions.pop(page_id, None)
        self._comments.pop(page_id, None)

        version_file = os.path.join(self._versions_dir, f"{page_id}.json")
        if os.path.exists(version_file):
            os.remove(version_file)
        comments_file = os.path.join(self._comments_dir, f"{page_id}.json")
        if os.path.exists(comments_file):
            os.remove(comments_file)

        self._save_registry()
        logger.info(f"Wiki页面删除: {page_id}")
        return True

    def list_pages(self, space_id: Optional[str] = None, parent_id: Optional[str] = None) -> List[WikiPage]:
        pages = list(self._pages.values())
        if space_id:
            pages = [p for p in pages if p.space_id == space_id]
        if parent_id is not None:
            pages = [p for p in pages if p.parent_id == parent_id]
        pages.sort(key=lambda p: (p.sort_order, p.title))
        return pages

    def search_pages(self, query: str, space_id: Optional[str] = None) -> List[WikiPage]:
        query_lower = query.lower()
        results = []
        for page in self._pages.values():
            if space_id and page.space_id != space_id:
                continue
            if query_lower in page.title.lower() or query_lower in page.content.lower():
                results.append(page)
            elif any(query_lower in tag.lower() for tag in page.tags):
                results.append(page)
        return results

    def get_page_tree(self, space_id: str = "default") -> Dict:
        pages = [p for p in self._pages.values() if p.space_id == space_id]
        return self._build_tree(pages, parent_id="")

    def _build_tree(self, pages: List[WikiPage], parent_id: str) -> Dict:
        children = [p for p in pages if p.parent_id == parent_id]
        children.sort(key=lambda p: (p.sort_order, p.title))
        result = []
        for child in children:
            node = child.to_dict()
            node["children"] = self._build_tree(pages, child.page_id)
            result.append(node)
        return result

    def move_page(self, page_id: str, new_parent_id: str) -> bool:
        page = self._pages.get(page_id)
        if not page:
            return False
        if new_parent_id and new_parent_id not in self._pages:
            return False
        if new_parent_id == page_id:
            return False
        page.parent_id = new_parent_id
        page.updated_at = datetime.now().isoformat()
        self._save_registry()
        return True

    # ==================== Version History ====================

    def get_page_versions(self, page_id: str) -> List[PageVersion]:
        return self._versions.get(page_id, [])

    def get_page_version(self, page_id: str, version_number: int) -> Optional[PageVersion]:
        versions = self._versions.get(page_id, [])
        for v in versions:
            if v.version_number == version_number:
                return v
        return None

    def rollback_page(self, page_id: str, version_number: int, author: str = "system") -> Optional[WikiPage]:
        version = self.get_page_version(page_id, version_number)
        if not version:
            return None
        return self.update_page(
            page_id,
            title=version.title,
            content=version.content,
            author=author,
            change_summary=f"回滚至版本 {version_number}",
        )

    def _compute_diff_summary(self, old_content: str, new_content: str) -> str:
        if not old_content:
            return f"新增 {len(new_content)} 字"
        if not new_content:
            return "删除全部内容"
        added = max(0, len(new_content) - len(old_content))
        removed = max(0, len(old_content) - len(new_content))
        parts = []
        if added > 0:
            parts.append(f"+{added}字")
        if removed > 0:
            parts.append(f"-{removed}字")
        return " ".join(parts) if parts else "无变化"

    # ==================== Space CRUD ====================

    def create_space(self, name: str, description: str = "", space_type: str = "project",
                     icon: str = "", owner: str = "system") -> WikiSpace:
        space = WikiSpace(name=name, description=description, space_type=space_type, icon=icon, owner=owner)
        now = datetime.now().isoformat()
        space.created_at = now
        space.updated_at = now
        self._spaces[space.space_id] = space
        self._save_spaces()
        logger.info(f"Wiki空间创建: {name} ({space.space_id})")
        return space

    def get_space(self, space_id: str) -> Optional[WikiSpace]:
        return self._spaces.get(space_id)

    def list_spaces(self) -> List[WikiSpace]:
        return list(self._spaces.values())

    def update_space(self, space_id: str, name: Optional[str] = None,
                     description: Optional[str] = None, icon: Optional[str] = None) -> Optional[WikiSpace]:
        space = self._spaces.get(space_id)
        if not space:
            return None
        if name is not None:
            space.name = name
        if description is not None:
            space.description = description
        if icon is not None:
            space.icon = icon
        space.updated_at = datetime.now().isoformat()
        self._save_spaces()
        return space

    def delete_space(self, space_id: str) -> bool:
        if space_id == "default":
            return False
        if space_id not in self._spaces:
            return False
        for page in list(self._pages.values()):
            if page.space_id == space_id:
                page.space_id = "default"
        del self._spaces[space_id]
        self._save_registry()
        self._save_spaces()
        return True

    # ==================== Tag CRUD ====================

    def create_tag(self, name: str, color: str = "#6b7280", space_id: str = "default") -> Tag:
        for t in self._tags.values():
            if t.name == name and t.space_id == space_id:
                return t
        tag = Tag(name=name, color=color, space_id=space_id)
        tag.created_at = datetime.now().isoformat()
        self._tags[tag.tag_id] = tag
        self._save_tags()
        return tag

    def list_tags(self, space_id: Optional[str] = None) -> List[Tag]:
        tags = list(self._tags.values())
        if space_id:
            tags = [t for t in tags if t.space_id == space_id]
        return tags

    def update_tag(self, tag_id: str, name: Optional[str] = None, color: Optional[str] = None) -> Optional[Tag]:
        tag = self._tags.get(tag_id)
        if not tag:
            return None
        if name is not None:
            tag.name = name
        if color is not None:
            tag.color = color
        self._save_tags()
        return tag

    def delete_tag(self, tag_id: str) -> bool:
        if tag_id not in self._tags:
            return False
        tag_name = self._tags[tag_id].name
        for page in self._pages.values():
            if tag_name in page.tags:
                page.tags.remove(tag_name)
        del self._tags[tag_id]
        self._save_tags()
        self._save_registry()
        return True

    def batch_apply_tags(self, page_ids: List[str], tag_names: List[str]) -> int:
        count = 0
        for pid in page_ids:
            page = self._pages.get(pid)
            if not page:
                continue
            for tn in tag_names:
                if tn not in page.tags:
                    self.create_tag(tn, space_id=page.space_id)
                    page.tags.append(tn)
                    count += 1
        if count > 0:
            self._save_registry()
        return count

    # ==================== Permission ====================

    def set_permission(self, space_id: str, page_id: str, user_id: str, role: str) -> RolePermission:
        for p in self._permissions.values():
            if p.space_id == space_id and p.page_id == page_id and p.user_id == user_id:
                p.role = role
                self._save_permissions()
                return p

        perm = RolePermission(space_id=space_id, page_id=page_id, user_id=user_id, role=role)
        perm.created_at = datetime.now().isoformat()
        self._permissions[perm.perm_id] = perm
        self._save_permissions()
        return perm

    def get_user_role(self, space_id: str, page_id: str, user_id: str) -> str:
        for p in self._permissions.values():
            if p.space_id == space_id and p.page_id == page_id and p.user_id == user_id:
                return p.role
        for p in self._permissions.values():
            if p.space_id == space_id and p.page_id == "" and p.user_id == user_id:
                return p.role
        return RolePermission.ROLE_READER

    def check_permission(self, space_id: str, page_id: str, user_id: str, action: str) -> bool:
        role = self.get_user_role(space_id, page_id, user_id)
        return action in RolePermission.PERMISSIONS.get(role, [])

    def list_permissions(self, space_id: Optional[str] = None, page_id: Optional[str] = None) -> List[RolePermission]:
        perms = list(self._permissions.values())
        if space_id:
            perms = [p for p in perms if p.space_id == space_id]
        if page_id:
            perms = [p for p in perms if p.page_id == page_id]
        return perms

    def delete_permission(self, perm_id: str) -> bool:
        if perm_id not in self._permissions:
            return False
        del self._permissions[perm_id]
        self._save_permissions()
        return True

    # ==================== Share ====================

    def enable_share(self, page_id: str, password: str = "") -> Optional[Dict]:
        page = self._pages.get(page_id)
        if not page:
            return None
        page.share_enabled = True
        page.share_token = page.share_token or str(uuid.uuid4())[:8]
        page.share_password = password
        page.is_public = not bool(password)
        self._save_registry()
        return {
            "share_token": page.share_token,
            "share_password": page.share_password,
            "is_public": page.is_public,
        }

    def disable_share(self, page_id: str) -> bool:
        page = self._pages.get(page_id)
        if not page:
            return False
        page.share_enabled = False
        page.is_public = False
        self._save_registry()
        return True

    def get_shared_page(self, share_token: str, password: str = "") -> Optional[WikiPage]:
        for page in self._pages.values():
            if page.share_token == share_token and page.share_enabled:
                if page.share_password and page.share_password != password:
                    return None
                return page
        return None

    # ==================== Encryption ====================

    def encrypt_page(self, page_id: str, password: str) -> bool:
        page = self._pages.get(page_id)
        if not page:
            return False
        import hashlib
        page.is_encrypted = True
        page.encrypt_password_hash = hashlib.sha256(password.encode()).hexdigest()
        self._save_registry()
        return True

    def decrypt_page(self, page_id: str, password: str) -> Optional[WikiPage]:
        page = self._pages.get(page_id)
        if not page or not page.is_encrypted:
            return page
        import hashlib
        if hashlib.sha256(password.encode()).hexdigest() == page.encrypt_password_hash:
            return page
        return None

    # ==================== Comment ====================

    def add_comment(self, page_id: str, content: str, author: str = "system",
                    parent_comment_id: str = "", selection_start: int = -1,
                    selection_end: int = -1, selection_text: str = "",
                    comment_type: str = "comment") -> Comment:
        self._load_comments(page_id)
        comment = Comment(
            page_id=page_id,
            content=content,
            author=author,
            parent_comment_id=parent_comment_id,
            selection_start=selection_start,
            selection_end=selection_end,
            selection_text=selection_text,
            comment_type=comment_type,
        )
        now = datetime.now().isoformat()
        comment.created_at = now
        comment.updated_at = now
        self._comments.setdefault(page_id, []).append(comment)
        self._save_comments(page_id)
        return comment

    def get_comments(self, page_id: str) -> List[Comment]:
        return self._load_comments(page_id)

    def resolve_comment(self, comment_id: str, page_id: str) -> bool:
        comments = self._load_comments(page_id)
        for c in comments:
            if c.comment_id == comment_id:
                c.is_resolved = True
                c.updated_at = datetime.now().isoformat()
                self._save_comments(page_id)
                return True
        return False

    def delete_comment(self, comment_id: str, page_id: str) -> bool:
        comments = self._load_comments(page_id)
        new_comments = [c for c in comments if c.comment_id != comment_id]
        if len(new_comments) == len(comments):
            return False
        self._comments[page_id] = new_comments
        self._save_comments(page_id)
        return True

    # ==================== Statistics ====================

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def space_count(self) -> int:
        return len(self._spaces)


wiki_store = WikiStore()
