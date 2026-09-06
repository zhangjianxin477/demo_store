"""微信公众号文章内容转换与安全校验。

该模块只做确定性的 Markdown/HTML 处理，不调用模型，也不负责发布。
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List


ALLOWED_TAGS = {"p", "br", "strong", "em", "h1", "h2", "h3", "ul", "ol", "li", "blockquote", "pre", "code", "a", "img", "table", "thead", "tbody", "tr", "th", "td"}


def extract_image_sources(content: str) -> List[str]:
    sources: List[str] = []
    for match in re.finditer(r"<img\b[^>]*(?:src|data-src)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", str(content or ""), re.I):
        value = match.group(1).strip()
        if value and value not in sources:
            sources.append(value)
    for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", str(content or "")):
        value = match.group(1).strip("<>\"'")
        if value and value not in sources:
            sources.append(value)
    return sources


def markdown_to_html(markdown: str) -> str:
    text = str(markdown or "").replace("\r\n", "\n")
    text = re.sub(r"^---[\s\S]*?---\s*", "", text, count=1) if text.lstrip().startswith("---") else text
    lines = text.split("\n")
    out: List[str] = []
    paragraph: List[str] = []
    in_code = False
    code_lines: List[str] = []
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            value = " ".join(x.strip() for x in paragraph if x.strip())
            if value:
                def image_tag(match: re.Match) -> str:
                    src = match.group(2).strip().strip("<>\"'")
                    return f'<img src="{html.escape(src, quote=True)}" />'
                value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_tag, value)
                value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>', value)
                value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
                value = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", value)
                value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)
                out.append(f"<p>{value}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.strip()
        if line.startswith("```"):
            flush_paragraph(); close_list()
            if in_code:
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []; in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(raw); continue
        if not line:
            flush_paragraph(); close_list(); continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph(); close_list()
            level = len(heading.group(1)); out.append(f"<h{level}>{html.escape(heading.group(2).strip())}</h{level}>")
            continue
        if re.match(r"^[-*+]\s+", line):
            flush_paragraph()
            if not in_list: out.append("<ul>"); in_list = True
            item_text = re.sub(r"^[-*+]\s+", "", line)
            out.append(f"<li>{html.escape(item_text)}</li>")
            continue
        if line.startswith(">"):
            flush_paragraph(); close_list()
            quote_text = line.lstrip("> ")
            out.append(f"<blockquote>{html.escape(quote_text)}</blockquote>"); continue
        if line.startswith("|") and line.endswith("|"):
            # Keep simple Markdown tables readable; complex tables are left to
            # the editor rather than silently dropping columns.
            flush_paragraph(); close_list()
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and not all(re.match(r"^:?-{3,}:?$", c) for c in cells):
                out.append("<table><tbody><tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr></tbody></table>")
            continue
        paragraph.append(line)
    if in_code:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph(); close_list()
    return sanitize_html("\n".join(out))


def sanitize_html(content: str) -> str:
    text = str(content or "")
    def clean(match: re.Match) -> str:
        closing, name, attrs = match.group(1), match.group(2).lower(), match.group(3) or ""
        if name not in ALLOWED_TAGS:
            return ""
        if closing:
            return f"</{name}>"
        if name == "img":
            src = re.search(r"(?:src|data-src)\s*=\s*[\"']([^\"']+)", attrs, re.I)
            return f'<img src="{html.escape(src.group(1), quote=True)}" />' if src and re.match(r"^https?://", src.group(1), re.I) else ""
        if name == "a":
            href = re.search(r"href\s*=\s*[\"'](https?://[^\"']+)", attrs, re.I)
            return f'<a href="{html.escape(href.group(1), quote=True)}">' if href else "<a>"
        return f"<{name}>"
    text = re.sub(r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>", clean, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:90000]


def prepare_article(title: str, digest: str, content: str, content_is_html: bool = False) -> Dict[str, Any]:
    html_content = sanitize_html(content) if content_is_html else markdown_to_html(content)
    warnings: List[str] = []
    if not str(title or "").strip(): warnings.append("标题不能为空")
    if len(str(title or "")) > 64: warnings.append("标题超过 64 字符，将被截断")
    if len(str(digest or "")) > 120: warnings.append("摘要超过 120 字符，将被截断")
    if not html_content: warnings.append("正文为空")
    return {
        "title": str(title or "未命名文章").strip()[:64],
        "digest": str(digest or "").strip()[:120],
        "content_html": html_content,
        "image_sources": extract_image_sources(html_content),
        "warnings": warnings,
        "valid": not any(w in {"标题不能为空", "正文为空"} for w in warnings),
    }
