from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.doc_processor.processor import SmartChunker, TextCleaner  # noqa: E402


MARKER_RE = re.compile(r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}).{0,20}正文[:：]")
BOILERPLATE = ["分享让更多人看到", "责任编辑", "声明：本网站", "投资有风险"]


def parents(chunks):
    data = {}
    for chunk in chunks:
        pid = chunk.metadata.get("parent_chunk_id")
        data.setdefault(pid, chunk.metadata.get("parent_content", ""))
    return list(data.values())


def polluted(parent_texts):
    return sum(1 for text in parent_texts if len(MARKER_RE.findall(text)) > 1)


def boilerplate_count(parent_texts):
    return sum(1 for text in parent_texts if any(item in text for item in BOILERPLATE))


def terms(text):
    return set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2}", text.lower()))


def score(query, text):
    query_terms = terms(query)
    text_terms = terms(text)
    return len(query_terms & text_terms) / max(len(query_terms), 1)


def infer_article_index(raw_text, parent_content):
    if not parent_content:
        return None
    anchor = parent_content[:40]
    pos = raw_text.find(anchor)
    if pos < 0:
        return None
    return sum(1 for _ in MARKER_RE.finditer(raw_text[:pos])) + 1


def main():
    default_path = r"C:\Users\Administrator\Desktop\documents_dup_part_1_part_1.md"
    path = Path(sys.argv[1] if len(sys.argv) > 1 else default_path)
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    clean = TextCleaner.clean_markdown(raw)

    old_chunks = SmartChunker(900, 120, "semantic").chunk(raw, "old", path.name)
    new_chunks = SmartChunker(900, 120, "auto").chunk(clean, "new", path.name)

    print("file", str(path))
    print("raw_chars", len(raw), "clean_chars", len(clean))
    print(
        "old",
        "chunks", len(old_chunks),
        "parents", len(parents(old_chunks)),
        "cross_article_parents", polluted(parents(old_chunks)),
        "boilerplate_parents", boilerplate_count(parents(old_chunks)),
    )
    print(
        "new",
        "chunks", len(new_chunks),
        "parents", len(parents(new_chunks)),
        "cross_article_parents", polluted(parents(new_chunks)),
        "boilerplate_parents", boilerplate_count(parents(new_chunks)),
    )

    queries = [
        ("建军96周年招待会在哪里举行", 1),
        ("中国南非企业贸易对接会在哪里举行", 2),
        ("华硕无畏和哪个潮流品牌联名", 3),
        ("北翟路基地绿化屋顶光伏项目建设规模是多少", 9),
        ("赵有亮什么时候去世享年多少", 10),
    ]

    for label, chunks in [("old", old_chunks), ("new", new_chunks)]:
        print(f"--- {label} retrieval")
        for query, expected in queries:
            best = max(chunks, key=lambda chunk: score(query, chunk.content))
            article_index = best.metadata.get("article_index")
            if article_index is None:
                article_index = infer_article_index(raw, best.metadata.get("parent_content", ""))
            preview = best.content[:56].replace("\n", " ")
            print(
                query,
                "expected", expected,
                "hit", article_index,
                "score", round(score(query, best.content), 3),
                "preview", preview,
            )


if __name__ == "__main__":
    main()
