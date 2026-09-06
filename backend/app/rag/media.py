"""Resolve image references embedded in retrieved documents.

Images are presentation resources, not retrieval inputs.  This module only
returns images that already live in the local file-resource directory and are
served by the existing safe raw-resource endpoint.
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote, urlsplit

from app.core.config import settings


IMAGE_EXTENSIONS = {
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif",
    ".tiff", ".webp",
}
RAW_PREFIX = "/api/v1/file-resources/raw/"


def _resource_root(base: Optional[Path] = None) -> Path:
    return (base or Path(settings.DATA_DIR, "file_resources")).resolve()


def _safe_resource_path(root: Path, relative: str) -> Optional[Path]:
    try:
        candidate = (root / unquote(relative).replace("\\", "/").lstrip("/")).resolve()
        if candidate == root or root not in candidate.parents:
            return None
        return candidate
    except (OSError, ValueError):
        return None


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _extract_references(content: str) -> List[Tuple[str, str]]:
    text = html.unescape(str(content or ""))
    references: List[Tuple[str, str]] = []

    markdown_pattern = re.compile(
        r"!\[([^\]]*)\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\)",
        flags=re.I,
    )
    for match in markdown_pattern.finditer(text):
        references.append((match.group(2) or match.group(3) or "", match.group(1) or ""))

    html_pattern = re.compile(
        r"<img\b[^>]*?\bsrc\s*=\s*(['\"])(.*?)\1[^>]*>", flags=re.I | re.S
    )
    for match in html_pattern.finditer(text):
        tag = match.group(0)
        alt_match = re.search(r"\balt\s*=\s*(['\"])(.*?)\1", tag, flags=re.I | re.S)
        references.append((match.group(2), alt_match.group(2) if alt_match else ""))
    return references


def _resolve_reference(reference: str, source_path: str, root: Path) -> Optional[Tuple[Path, str]]:
    raw = str(reference or "").strip()
    if not raw or raw.startswith(("#", "data:", "blob:", "javascript:")):
        return None

    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        return None
    value = unquote(parsed.path or raw).replace("\\", "/")

    if value.startswith(RAW_PREFIX):
        relative = value[len(RAW_PREFIX):].lstrip("/")
    elif value.startswith("/api/v1/file-resources/raw/"):
        relative = value.split("/raw/", 1)[1].lstrip("/")
    elif value.lower().startswith("file_resources/"):
        relative = value.split("/", 1)[1]
    elif value.startswith("/"):
        return None
    else:
        source = str(source_path or "").replace("\\", "/")
        source_file = _safe_resource_path(root, source)
        if source_file and source_file.is_file():
            try:
                relative = (source_file.parent / value).resolve().relative_to(root).as_posix()
            except ValueError:
                return None
        else:
            relative = value

    target = _safe_resource_path(root, relative)
    if not target or not target.is_file() or not _is_image(target):
        return None
    normalized = target.relative_to(root).as_posix()
    return target, f"{RAW_PREFIX}{normalized}"


def extract_images(
    content: str,
    source_path: str = "",
    base: Optional[Path] = None,
    max_images: int = 8,
) -> List[Dict[str, Any]]:
    """Return safe, deduplicated image descriptors from document content."""
    root = _resource_root(base)
    images: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for order, (reference, caption) in enumerate(_extract_references(content), 1):
        resolved = _resolve_reference(reference, source_path, root)
        if not resolved:
            continue
        target, url = resolved
        if url in seen:
            continue
        seen.add(url)
        media_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        images.append({
            "media_id": media_id,
            "url": url,
            "thumbnail_url": url,
            "caption": str(caption or target.stem).strip()[:240],
            "source_path": target.relative_to(root).as_posix(),
            "order": order,
        })
        if len(images) >= max(1, int(max_images or 8)):
            break
    return images


def collect_evidence_images(evidence: Iterable[Dict[str, Any]], max_images: int = 12) -> List[Dict[str, Any]]:
    """Collect image descriptors already attached to Agent evidence."""
    output: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in evidence or []:
        attached = item.get("images") if isinstance(item, dict) else []
        for image in attached or []:
            if not isinstance(image, dict) or not image.get("url"):
                continue
            key = str(image["url"])
            if key in seen:
                continue
            seen.add(key)
            output.append(dict(image))
            if len(output) >= max(1, int(max_images or 12)):
                return output
    return output
