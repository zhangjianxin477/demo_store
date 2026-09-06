"""Agent 上下文解析器。

把左侧文件资源库和已向量化知识库统一转换成可追溯的 evidence，供 Agent
后续的写作、核验和导出步骤使用。这里不通过 HTTP 自调用 API，避免后台任务
在小型服务器上产生额外连接和认证开销。
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from app.core.config import settings
from app.rag.media import extract_images, collect_evidence_images
from app.rag.service import rag_service

logger = logging.getLogger(__name__)


TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".html", ".htm", ".json", ".yaml", ".yml",
    ".xml", ".csv", ".rtf", ".log", ".ini", ".js", ".ts", ".css", ".py",
}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx"}


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _terms(text: str) -> List[str]:
    raw = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9][a-zA-Z0-9_-]{1,}", (text or "").lower())
    seen: Set[str] = set()
    result = []
    for item in raw:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _read_text(path: Path, max_chars: int = 18000) -> str:
    try:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            try:
                import fitz
                doc = fitz.open(str(path))
                text = "\n".join(page.get_text("text") for page in doc)
                doc.close()
                return text[:max_chars]
            except Exception as exc:
                logger.debug("读取 PDF 失败 %s: %s", path, exc)
                return ""
        if suffix == ".docx":
            try:
                from docx import Document
                doc = Document(str(path))
                return "\n".join(p.text for p in doc.paragraphs)[:max_chars]
            except Exception as exc:
                logger.debug("读取 DOCX 失败 %s: %s", path, exc)
                return ""
        data = path.read_bytes()[: max_chars * 4]
        for encoding in ("utf-8", "gb18030", "utf-16"):
            try:
                return data.decode(encoding, errors="ignore")[:max_chars]
            except Exception:
                continue
    except Exception as exc:
        logger.debug("读取文件失败 %s: %s", path, exc)
    return ""


def _clean_file_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s*(来源 URL|来源类型|采集时间|采集提示|采集模式)[:：].*$", " ", text, flags=re.M)
    return _compact(text)


def _safe_relative_path(base: Path, raw: str) -> Optional[Path]:
    try:
        candidate = (base / str(raw).replace("\\", "/").strip("/" )).resolve()
        if candidate == base or base not in candidate.parents:
            return None
        return candidate
    except Exception:
        return None


def _normalize_resource_ref(raw: str) -> str:
    """Normalize paths copied from Markdown/chat text.

    Markdown often escapes underscores as ``\\_``; that backslash is not a
    directory separator and must be removed before resolving the resource.
    """
    return str(raw or "").replace("\\_", "_").replace("\\", "/").strip("/ ")


def extract_resource_paths(query: str, base: Optional[Path] = None) -> List[str]:
    """Extract every explicit file-resource path from a natural-language query."""
    text = str(query or "").replace("\\_", "_").replace("\\", "/")
    matches = re.findall(
        r"((?:自动抓取|file_resources)/[^\s，。！？；;：:、)）\]】]+)",
        text,
        flags=re.I,
    )
    root = (base or Path(settings.DATA_DIR, "file_resources")).resolve()
    result: List[str] = []
    seen: Set[str] = set()
    for raw in matches:
        raw = _normalize_resource_ref(raw)
        candidates = [raw]
        trimmed = re.split(r"的(?:文章|文件|资料|内容)?$", raw, maxsplit=1)[0]
        if trimmed and trimmed != raw:
            candidates.insert(0, trimmed)
        for candidate in candidates:
            relative = candidate.split("/", 1)[1] if candidate.lower().startswith("file_resources/") else candidate
            target = _safe_relative_path(root, relative)
            if target and target.exists():
                value = target.relative_to(root).as_posix()
                if value not in seen:
                    seen.add(value)
                    result.append(value)
                break
    # Backward-compatible fallback for older browsers. Early versions of the
    # drag-and-drop UI inserted only ``[已选择 N 个资料：filename.md]`` into
    # the textarea and lost the hidden selected_paths state on submit. Resolve
    # those exact basenames against the resource library so cached clients do
    # not silently fall back to the currently opened (first) file.
    if text and root.exists():
        try:
            for target in root.rglob("*"):
                if not target.is_file() or target.suffix.lower() not in DOCUMENT_EXTENSIONS:
                    continue
                name = target.name
                if len(name) < 5 or name not in text:
                    continue
                value = target.relative_to(root).as_posix()
                if value not in seen:
                    seen.add(value)
                    result.append(value)
        except OSError:
            pass
    return result


def extract_resource_path(query: str, base: Optional[Path] = None) -> str:
    """Extract an explicit file-resource path from a natural-language query.

    Queries such as ``总结自动抓取/2026-08-30的文章`` previously fell through to
    full-corpus lexical search.  Only return a path that exists below the resource
    root, and trim common Chinese trailing phrases from the folder reference.
    """
    paths = extract_resource_paths(query, base)
    return paths[0] if paths else ""


class ContextResolver:
    """解析 Agent 当前任务可访问的文件、知识库和网页证据。"""

    def __init__(self) -> None:
        self.base = Path(settings.DATA_DIR, "file_resources").resolve()

    def _file_candidates(self, selected_paths: Optional[Iterable[str]] = None) -> List[Path]:
        self.base.mkdir(parents=True, exist_ok=True)
        selected = [_normalize_resource_ref(p) for p in (selected_paths or []) if str(p).strip()]
        if selected:
            paths: List[Path] = []
            for raw in selected:
                target = _safe_relative_path(self.base, raw)
                if not target or not target.exists():
                    continue
                paths.extend(p for p in (target.rglob("*") if target.is_dir() else [target]) if p.is_file())
        else:
            paths = [p for p in self.base.rglob("*") if p.is_file()]
        return [p for p in paths if p.suffix.lower() in DOCUMENT_EXTENSIONS]

    def _selected_file_paths(self, selected_paths: Optional[Iterable[str]] = None) -> List[str]:
        """Return selected paths that resolve to individual supported files."""
        result = []
        for raw in selected_paths or []:
            target = _safe_relative_path(self.base, _normalize_resource_ref(raw))
            if target and target.is_file() and target.suffix.lower() in DOCUMENT_EXTENSIONS:
                result.append(target.relative_to(self.base).as_posix())
        return result

    def _search_files(self, query: str, top_k: int, selected_paths: Optional[Iterable[str]], date_range: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected_paths = list(selected_paths or [])
        terms = _terms(query)
        selected_file_paths = self._selected_file_paths(selected_paths)
        # An explicit folder is a hard boundary too.  Folder-summary queries
        # must include every supported article in that folder, even when the
        # query words do not literally occur in each document.
        hard_resource_scope = bool(list(selected_paths or []))
        start = str((date_range or {}).get("start") or "")[:10]
        end = str((date_range or {}).get("end") or "")[:10]
        results: List[Dict[str, Any]] = []
        for path in self._file_candidates(selected_paths)[:400]:
            try:
                rel = path.relative_to(self.base).as_posix()
                mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
                if start and mtime < start:
                    continue
                if end and mtime > end:
                    continue
            except Exception:
                continue
            raw_content = _read_text(path)
            content = _clean_file_text(raw_content)
            if not content:
                continue
            haystack = f"{rel} {content}".lower()
            overlap = sum(1 for term in terms if term in haystack)
            title_bonus = sum(2 for term in terms if term in path.stem.lower())
            if terms and overlap == 0 and not hard_resource_scope:
                continue
            score = overlap / max(len(terms), 1) + title_bonus / max(len(terms), 1)
            digest = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
            results.append({
                "source_type": "file_resource",
                "source_id": f"file:{rel}",
                "source_path": rel,
                "title": path.stem,
                "content": content[:12000],
                "snippet": content[:800],
                "score": round(float(score), 4),
                "metadata": {"path": rel, "updated_at": mtime, "content_hash": digest},
                "images": extract_images(raw_content, source_path=rel, base=self.base),
            })
        if selected_file_paths:
            # Preserve the user's selection order for multi-document review;
            # numbering should not change because one title happens to score
            # higher lexically than another.
            order = {path: index for index, path in enumerate(selected_file_paths)}
            results.sort(key=lambda item: (order.get(item.get("source_path"), 10_000), -item["score"]))
        else:
            results.sort(key=lambda item: item["score"], reverse=True)
        if hard_resource_scope and not selected_file_paths:
            # For an explicitly selected folder, top_k controls per-document
            # retrieval elsewhere; it must not silently hide sibling files.
            return results[:50]
        if selected_file_paths:
            return results[: max(1, min(max(top_k, len(selected_file_paths)), 50))]
        return results[: max(1, min(top_k, 12))]

    @staticmethod
    def _kb_evidence(result: Dict[str, Any], kb_id: str = "") -> List[Dict[str, Any]]:
        sources = result.get("sources") or result.get("chunks") or []
        evidence = []
        for source in sources:
            meta = dict(source.get("metadata") or {})
            actual_kb = meta.get("kb_id") or source.get("kb_id") or kb_id
            chunk_id = source.get("chunk_id") or source.get("id") or ""
            doc_id = source.get("doc_id") or ""
            content = source.get("content") or source.get("text") or source.get("display_text") or ""
            if not content:
                continue
            title = meta.get("title") or meta.get("filename") or source.get("title") or doc_id or "知识库片段"
            evidence.append({
                "source_type": "knowledge_base",
                "source_id": f"kb:{actual_kb}:{chunk_id or doc_id}",
                "source_path": meta.get("source_path") or meta.get("path") or "",
                "title": title,
                "content": str(content)[:12000],
                "snippet": _compact(str(content))[:800],
                "score": float(source.get("display_score") or source.get("score") or 0),
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "kb_id": actual_kb,
                "metadata": meta,
                "images": extract_images(
                    str(content),
                    source_path=meta.get("source_path") or meta.get("path") or "",
                    base=Path(settings.DATA_DIR, "file_resources"),
                ),
            })
        return evidence

    @staticmethod
    def _deduplicate(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        seen: Set[str] = set()
        output = []
        for item in sorted(items, key=lambda value: value.get("score", 0), reverse=True):
            text = _compact(item.get("content", ""))
            key = item.get("source_id") or hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:20]
            if key in seen:
                continue
            seen.add(key)
            item["evidence_index"] = len(output) + 1
            output.append(item)
            if len(output) >= limit:
                break
        return output

    @staticmethod
    def _filter_relevant(items: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Discard obvious zero-overlap noise while retaining semantic hits."""
        terms = _terms(query)
        if not terms:
            return items
        filtered = []
        for item in items:
            haystack = f"{item.get('title', '')} {item.get('content', '')}".lower()
            overlap = sum(1 for term in terms if term in haystack)
            score = float(item.get("score") or 0)
            # High semantic scores can be valid paraphrases; low-score items
            # with no lexical signal are usually unrelated boilerplate.
            if overlap or score >= 0.45:
                filtered.append(item)
        return filtered

    async def resolve(self, query: str, *, kb_id: str = "", kb_ids: Optional[List[str]] = None,
                      scope: Optional[List[str]] = None, selected_paths: Optional[List[str]] = None,
                      top_k: int = 8, date_range: Optional[Dict[str, Any]] = None,
                      web_search_enabled: bool = False) -> Dict[str, Any]:
        scopes = set(scope or ["file_resources", "knowledge_bases"])
        all_evidence: List[Dict[str, Any]] = []
        file_results: List[Dict[str, Any]] = []
        kb_result: Dict[str, Any] = {}
        source_errors: List[Dict[str, str]] = []
        if "file_resources" in scopes:
            file_results = await __import__("asyncio").to_thread(
                self._search_files, query, top_k, selected_paths, date_range
            )
            all_evidence.extend(file_results)
        if "knowledge_bases" in scopes:
            effective_ids = list(dict.fromkeys((kb_ids or []) + ([kb_id] if kb_id else [])))
            try:
                # A remote embedding/Milvus outage must not block file-based
                # Agent work on the small server. The RAG service itself has
                # provider fallbacks; this outer deadline bounds the whole call.
                kb_result = await asyncio.wait_for(
                    rag_service.query(
                        query, mode="hybrid", kb_id=kb_id, kb_ids=effective_ids or None,
                        top_k=min(max(top_k, 5), 12), enable_web_search=False,
                    ),
                    timeout=25,
                )
                all_evidence.extend(self._kb_evidence(kb_result, kb_id))
            except asyncio.TimeoutError:
                source_errors.append({"source": "knowledge_bases", "message": "知识库检索超时，已保留文件资源证据"})
                logger.warning("Agent 知识库检索超时，query=%s", query[:80])
            except Exception as exc:
                source_errors.append({"source": "knowledge_bases", "message": "知识库暂时不可用，已保留文件资源证据"})
                logger.warning("Agent 知识库检索失败，降级到文件资源: %s", exc)
        if "web" in scopes and web_search_enabled:
            try:
                web_result = await asyncio.wait_for(
                    rag_service.query(query, mode="hybrid", kb_id="", top_k=min(top_k, 8), enable_web_search=True),
                    timeout=20,
                )
                for item in web_result.get("web_results") or []:
                    all_evidence.append({
                        "source_type": "web", "source_id": f"web:{item.get('url') or item.get('title')}",
                        "source_path": item.get("url") or "", "title": item.get("title") or "网页来源",
                        "content": item.get("snippet") or "", "snippet": item.get("snippet") or "",
                        "score": float(item.get("score") or 0), "metadata": item,
                    })
            except Exception as exc:
                source_errors.append({"source": "web", "message": "网页检索失败，未将未验证内容用于生成"})
                logger.warning("Agent 网页检索失败: %s", exc)
        selected_file_paths = self._selected_file_paths(selected_paths)
        folder_scope = bool(selected_paths) and not selected_file_paths
        if selected_file_paths:
            allowed = set(selected_file_paths)
            filtered_evidence = [
                item for item in all_evidence
                if item.get("source_type") == "file_resource"
                and item.get("source_path") in allowed
            ]
        elif folder_scope:
            filtered_evidence = [
                item for item in all_evidence
                if item.get("source_type") == "file_resource"
            ]
        else:
            filtered_evidence = self._filter_relevant(all_evidence, query)
        evidence_limit = 50 if folder_scope else (
            max(1, min(max(top_k, len(selected_file_paths)), 50))
            if selected_file_paths else max(1, min(top_k, 12))
        )
        evidence = self._deduplicate(filtered_evidence, evidence_limit)
        return {
            "evidence": evidence,
            "sources": evidence,
            "chunks": [item for item in evidence if item.get("source_type") == "knowledge_base"],
            "images": collect_evidence_images(evidence),
            "metadata": {
                "scope": sorted(scopes),
                "file_source_count": len(file_results),
                "knowledge_source_count": len(self._kb_evidence(kb_result, kb_id)),
                "evidence_count": len(evidence),
                "filtered_noise_count": max(0, len(all_evidence) - len(filtered_evidence)),
                "searched_paths": selected_paths or [],
                "selected_file_paths": selected_file_paths,
                "folder_scope": folder_scope,
                "single_document": len(selected_file_paths) == 1,
                "source_errors": source_errors,
            },
        }


context_resolver = ContextResolver()
