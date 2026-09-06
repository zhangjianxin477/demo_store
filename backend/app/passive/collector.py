from __future__ import annotations

import asyncio
import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup


USER_AGENT = "KnowledgeHubPassiveAgent/1.0 (+https://corenote.cloud)"


def _canonical_url(value: str) -> str:
    try:
        parsed = urlparse(value.strip())
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", parsed.query, ""))
    except Exception:
        return value.strip()


def _clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw or "", "html.parser")
    for node in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
        node.decompose()
    for selector in (".advertisement", ".advertise", ".ads", ".ad", ".share", ".comments", "[id*='advert']", "[class*='advert']"):
        for node in soup.select(selector):
            node.decompose()
    main = soup.find("article") or soup.find("main") or soup.find("body") or soup
    text = main.get_text(" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _published(value: str) -> str:
    return str(value or "").strip()[:80]


def _extract_published(soup: BeautifulSoup) -> str:
    """Extract a machine-readable publication date from common page markup."""
    for selector, attr in (
        ("meta[property='article:published_time']", "content"),
        ("meta[property='og:published_time']", "content"),
        ("meta[name='date']", "content"),
        ("meta[name='publish-date']", "content"),
        ("time[datetime]", "datetime"),
    ):
        node = soup.select_one(selector)
        if node and node.get(attr):
            return _published(node.get(attr))
    # Many company blogs render a visible ISO/English date in the article
    # header. Use only an explicit year so navigation numbers are not treated
    # as freshness metadata.
    text = soup.get_text(" ", strip=True)[:5000]
    match = re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b|\b([A-Z][a-z]{2,8}\s+\d{1,2},\s+20\d{2})\b", text)
    return _published(match.group(0) if match else "")


def _item(source: Dict[str, Any], title: str, url: str, content: str, published_at: str = "", extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    canonical = _canonical_url(url)
    normalized = re.sub(r"\s+", " ", content or "").strip()
    return {
        "item_id": hashlib.sha256((canonical or title).encode("utf-8")).hexdigest()[:20],
        "source_id": source.get("id") or source.get("url"),
        "source_name": source.get("name") or source.get("url"),
        "title": re.sub(r"\s+", " ", html.unescape(title or "未命名")).strip()[:300],
        "url": url,
        "canonical_url": canonical,
        "content": normalized[:12000],
        "published_at": _published(published_at),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"source_type": source.get("type", "web"), "date_missing": not bool(published_at), **(extra or {})},
    }


def _parse_rss(source: Dict[str, Any], raw: str) -> list[Dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    output = []
    for node in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        def text(*names: str) -> str:
            for name in names:
                found = node.find(name)
                if found is not None and found.text:
                    return found.text
            return ""
        link = text("link")
        if not link:
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            link = (link_node.attrib.get("href") if link_node is not None else "")
        title = text("title", "{http://www.w3.org/2005/Atom}title")
        summary = text("description", "summary", "{http://www.w3.org/2005/Atom}summary", "content")
        output.append(_item(source, title, link, _clean_html(summary), text("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}updated")))
    return output


def _parse_github(source: Dict[str, Any], data: Dict[str, Any]) -> list[Dict[str, Any]]:
    output = []
    for repo in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(repo, dict):
            continue
        content = " | ".join(filter(None, [repo.get("description", ""), f"语言：{repo.get('language') or '未知'}", f"Stars：{repo.get('stargazers_count', 0)}"]))
        output.append(_item(source, repo.get("full_name") or repo.get("name", ""), repo.get("html_url", ""), content, repo.get("updated_at", ""), {"stars": repo.get("stargazers_count"), "topics": repo.get("topics", [])}))
    return output


def _parse_trending(source: Dict[str, Any], raw: str) -> list[Dict[str, Any]]:
    soup = BeautifulSoup(raw or "", "html.parser")
    output = []
    for article in soup.select("article.Box-row")[:30]:
        link = article.select_one("h2 a")
        if not link:
            continue
        href = link.get("href", "")
        path_parts = [part for part in href.strip("/").split("/") if part]
        # Trending cards should point to /owner/repo. A changed GitHub DOM can
        # occasionally expose a user profile; those are not repositories and
        # must not become daily hotspots.
        if len(path_parts) != 2 or path_parts[0] in {"login", "sponsors", "features"}:
            continue
        url = urljoin("https://github.com", href)
        title = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        description = article.select_one("p")
        stars = re.search(r"([\d,]+)\s+stars? today", article.get_text(" ", strip=True), re.I)
        output.append(_item(source, title, url, description.get_text(" ", strip=True) if description else "", "", {"stars_today": stars.group(1) if stars else ""}))
    return output


async def fetch_source(source: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    url = str(source.get("url") or "").strip()
    if not url:
        return {"source": source, "items": [], "error": "URL 为空"}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.8"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if source.get("type") == "github_search" or "api.github.com/search" in url:
                items = _parse_github(source, response.json())
            elif source.get("type") == "github_trending":
                items = _parse_trending(source, response.text)
            elif source.get("type") == "rss" or "xml" in content_type or "rss" in url.lower() or "feed" in url.lower():
                items = _parse_rss(source, response.text)
            else:
                soup = BeautifulSoup(response.text, "html.parser")
                selector = ((source.get("extract") or {}).get("article_link_selector") if isinstance(source.get("extract"), dict) else None)
                links = []
                if selector:
                    links = [urljoin(url, node.get("href", "")) for node in soup.select(selector) if node.get("href")]
                else:
                    # 对博客/新闻列表页采用轻量启发式；若页面本身就是文章，
                    # 没有足够候选时仍回退为单页正文。
                    host = urlparse(url).netloc
                    for node in soup.select("article a[href], h2 a[href], h3 a[href]"):
                        candidate = urljoin(url, node.get("href", ""))
                        if urlparse(candidate).netloc == host and candidate not in links:
                            links.append(candidate)
                if links:
                    items = []
                    for link in list(dict.fromkeys(links))[:10]:
                        try:
                            detail = await client.get(link)
                            detail.raise_for_status()
                            detail_soup = BeautifulSoup(detail.text, "html.parser")
                            title_node = detail_soup.find("h1") or detail_soup.find("title")
                            title = title_node.get_text(" ", strip=True) if title_node else link
                            items.append(_item(source, title, link, _clean_html(detail.text), _extract_published(detail_soup)))
                        except Exception:
                            # 列表页仍保留链接，后续质量过滤会避免空内容进入日报。
                            items.append(_item(source, "", link, ""))
                else:
                    title = (soup.find("h1") or soup.find("title"))
                    items = [_item(source, title.get_text(" ", strip=True) if title else url, url, _clean_html(response.text))]
            return {"source": source, "items": items, "status_code": response.status_code}
    except Exception as exc:
        return {"source": source, "items": [], "error": str(exc)[:300]}


async def collect_sources(sources: Iterable[Dict[str, Any]], max_sources: int = 20, timeout: float = 15.0) -> Dict[str, Any]:
    selected = [item for item in sources if item.get("enabled", True)][:max_sources]
    results = await asyncio.gather(*(fetch_source(item, timeout) for item in selected), return_exceptions=False)
    items = []
    failures = []
    seen = set()
    for result in results:
        if result.get("error"):
            failures.append({"source": result.get("source"), "error": result["error"]})
        for item in result.get("items", []):
            content = str(item.get("content") or "").strip()
            if len(content) < 40 and not item.get("metadata", {}).get("stars") and not item.get("metadata", {}).get("stars_today"):
                continue
            key = item.get("canonical_url") or item.get("item_id")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return {"items": items, "failures": failures, "source_count": len(selected), "success_count": len(selected) - len(failures)}
