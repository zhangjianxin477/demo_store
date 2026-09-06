import os
import re
import uuid
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from app.core.config import settings
from app.doc_processor.converter import file_converter, SUPPORTED_FORMATS

logger = logging.getLogger(__name__)


class TextChunk:
    def __init__(self, chunk_id: str, content: str, doc_id: str, index: int,
                 metadata: Optional[Dict] = None, parent_chunk_id: Optional[str] = None):
        self.chunk_id = chunk_id
        self.content = content
        self.doc_id = doc_id
        self.index = index
        self.metadata = metadata or {}
        self.parent_chunk_id = parent_chunk_id  # 父子分块：小块指向大块
        self.embedding = None


class Document:
    def __init__(self, doc_id: str, title: str, content: str, source_path: str = "",
                 chunks: Optional[List[TextChunk]] = None, metadata: Optional[Dict[str, Any]] = None):
        self.doc_id = doc_id
        self.title = title
        self.content = content
        self.source_path = source_path
        self.chunks = chunks or []
        self.metadata = metadata or {}


class ChunkQualityChecker:
    """分块质量检查机制，验证分块结果是否符合规范"""

    # 句子结束标点
    _SENTENCE_END_CHARS = set('。！？；.!?;…')
    # 子句结束标点
    _CLAUSE_END_CHARS = set('，、,：:;')

    @classmethod
    def check_chunks(cls, chunks: List[TextChunk], parent_size: int = 1000,
                     child_size: int = 200, filename: str = "") -> Dict[str, Any]:
        """对分块结果进行全面质量检查，返回检查报告"""
        if not chunks:
            return {"passed": True, "total": 0, "issues": [], "warnings": []}

        issues = []
        warnings = []

        for i, chunk in enumerate(chunks):
            content = chunk.content
            is_child = chunk.metadata.get("is_child", False)
            is_table = chunk.metadata.get("type") == "table"

            # 1. 检查分块是否在句子中间截断
            sentence_issue = cls._check_sentence_boundary(content, i, is_table)
            if sentence_issue:
                issues.append(sentence_issue)

            # 2. 检查子块大小是否在合理范围内
            size_issue = cls._check_chunk_size(content, i, is_child, child_size)
            if size_issue:
                warnings.append(size_issue)

            # 3. 检查表格分块是否包含表头
            if is_table:
                table_issue = cls._check_table_header(content, i)
                if table_issue:
                    issues.append(table_issue)

            # 4. 检查分块内容是否为空或过短
            if len(content.strip()) < 10:
                warnings.append(f"分块#{i} 内容过短（{len(content.strip())}字符），可能丢失信息")

            # 5. 检查父子分块关联性
            if is_child and not chunk.parent_chunk_id:
                warnings.append(f"子块#{i} 缺少 parent_chunk_id 关联")

        passed = len(issues) == 0
        report = {
            "passed": passed,
            "total": len(chunks),
            "issues": issues,
            "warnings": warnings,
            "filename": filename,
        }

        if issues:
            logger.warning(f"分块质量检查发现问题: {len(issues)} 个问题, {len(warnings)} 个警告 (文件: {filename})")
        elif warnings:
            logger.info(f"分块质量检查通过，有 {len(warnings)} 个警告 (文件: {filename})")
        else:
            logger.info(f"分块质量检查完全通过 (文件: {filename})")

        return report

    @classmethod
    def _check_sentence_boundary(cls, content: str, idx: int, is_table: bool) -> Optional[str]:
        """检查分块是否在句子中间截断（表格除外）"""
        if is_table or not content.strip():
            return None

        stripped = content.rstrip()
        if not stripped:
            return None

        last_char = stripped[-1]

        # 允许的结尾字符：句子结束标点、子句标点、换行、引号/括号闭合
        allowed_endings = cls._SENTENCE_END_CHARS | cls._CLAUSE_END_CHARS | {'\n', '"', '"', '」', '』', '）', ')', '】', ']', '}', '—', '-', '…'}
        # 中文引号/书名号闭合
        allowed_endings |= {'"', '」', '》'}

        if last_char in allowed_endings:
            return None

        # 数字或字母结尾也可能是正常的（如编号、英文单词）
        if last_char.isalnum():
            # 检查是否是中文字符结尾（中文文本不以字母数字结尾是正常的）
            if any('\u4e00' <= c <= '\u9fff' for c in stripped[-5:]):
                return f"分块#{idx} 可能在句子中间截断（末尾字符: '{last_char}'，内容末尾: ...{stripped[-20:]}）"

        return None

    @classmethod
    def _check_chunk_size(cls, content: str, idx: int, is_child: bool, child_size: int) -> Optional[str]:
        """检查分块大小是否在合理范围内"""
        length = len(content)
        if is_child:
            if length > child_size * 2.2:
                return f"子块#{idx} 大小({length})超出子块上限({child_size * 2.2})"
        return None

    @classmethod
    def _check_table_header(cls, content: str, idx: int) -> Optional[str]:
        """检查表格分块是否包含表头信息"""
        lines = content.strip().split('\n')
        if len(lines) < 2:
            return f"表格分块#{idx} 行数过少（{len(lines)}行），可能缺少表头"

        # 检查是否有表头分隔行（|---|---|）
        has_separator = any(re.match(r'^\|[\s\-:|]+\|$', line.strip()) for line in lines)
        if not has_separator:
            return f"表格分块#{idx} 缺少表头分隔行，表头信息可能不完整"

        return None


SENTENCE_ENDINGS = re.compile(r'[。！？；\.\!\?;…]')
CLAUSE_ENDINGS = re.compile(r'[，、,；;：:]')
PARAGRAPH_SPLIT = re.compile(r'\n\s*\n')


class TextCleaner:
    """Normalize extracted text before chunking and embedding."""

    _MOJIBAKE_HINT_RE = re.compile(r'[ÃÂâåæçèéðï][\x80-\xBF\u0080-\u00BF]?|�')
    _IMAGE_RE = re.compile(r'!\[([^\]]*)\]\([^)]+\)')
    _PAGE_NO_RE = re.compile(r'^\s*(?:第\s*\d+\s*页|page\s*\d+|\d+\s*/\s*\d+)\s*$', re.IGNORECASE)
    _NEWS_MARKER_RE = re.compile(
        r'(?:\[\s*)?(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?|\d{1,2}[-/]\d{1,2})'
        r'(?:\s+\d{1,2}[：:]\d{2}(?::\d{2})?)?(?:\s*\])?\s*[，,]?\s*正文[:：]'
    )
    _BOILERPLATE_PATTERNS = [
        re.compile(r'分享让更多人看到'),
        re.compile(r'责任编辑[:：]\s*[\u4e00-\u9fffA-Za-z0-9_ -]{1,24}'),
        re.compile(r'责编[:：]\s*[\u4e00-\u9fffA-Za-z0-9_ -]{1,24}'),
        re.compile(r'设计[:：]\s*[\u4e00-\u9fff、A-Za-z0-9_ -]{1,40}'),
        re.compile(r'声明[:：]本网站所提供的信息仅供参考之用[^。\n]*(?:。|$)'),
        re.compile(r'您若对该稿件内容有任何疑问或质疑[^。\n]*(?:。|$)'),
        re.compile(r'联系方式[:：][A-Za-z0-9_.+\-]+@[A-Za-z0-9_.\-]+'),
        re.compile(r'以上内容由[^\n]{0,360}?投资有风险，请谨慎决策。'),
        re.compile(r'本文为数据整理，不对您构成任何投资建议。?'),
    ]

    @classmethod
    def clean_markdown(cls, text: str) -> str:
        if not text:
            return ""

        text = text.replace("\ufeff", "").replace("\u00a0", " ")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = cls._repair_mojibake(text)
        text = cls._IMAGE_RE.sub(lambda m: f"\n[图片: {m.group(1).strip() or '未命名'}]\n", text)
        text = cls._normalize_news_boundaries(text)
        text = cls._remove_boilerplate(text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        text = cls._drop_repeated_noise_lines(text)
        return text.strip()

    @classmethod
    def _repair_mojibake(cls, text: str) -> str:
        try:
            import ftfy  # type: ignore
            fixed = ftfy.fix_text(text)
            if cls._quality_score(fixed) > cls._quality_score(text):
                return fixed
        except Exception:
            pass

        lines = []
        for line in text.split("\n"):
            if not cls._MOJIBAKE_HINT_RE.search(line):
                lines.append(line)
                continue
            try:
                candidate = line.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            except Exception:
                lines.append(line)
                continue
            if candidate and cls._quality_score(candidate) > cls._quality_score(line):
                lines.append(candidate)
            else:
                lines.append(line)
        return "\n".join(lines)

    @classmethod
    def _normalize_news_boundaries(cls, text: str) -> str:
        def repl(match: re.Match) -> str:
            marker = match.group(0)
            prefix = "" if match.start() == 0 or text[match.start() - 1] == "\n" else "\n\n"
            return prefix + marker

        return cls._NEWS_MARKER_RE.sub(repl, text)

    @classmethod
    def _remove_boilerplate(cls, text: str) -> str:
        cleaned = text
        for pattern in cls._BOILERPLATE_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        return cleaned

    @classmethod
    def _quality_score(cls, text: str) -> int:
        chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
        ascii_words = len(re.findall(r'[A-Za-z]{2,}', text))
        mojibake = len(cls._MOJIBAKE_HINT_RE.findall(text))
        replacement = text.count("�")
        return chinese * 3 + ascii_words - mojibake * 8 - replacement * 12

    @classmethod
    def _drop_repeated_noise_lines(cls, text: str) -> str:
        raw_lines = [line.rstrip() for line in text.split("\n")]
        counts: Dict[str, int] = {}
        for line in raw_lines:
            key = line.strip()
            if key:
                counts[key] = counts.get(key, 0) + 1

        cleaned = []
        for line in raw_lines:
            stripped = line.strip()
            if cls._PAGE_NO_RE.match(stripped):
                continue
            if len(stripped) <= 80 and counts.get(stripped, 0) >= 4 and not stripped.startswith(("#", "|", "- ")):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)


class SmartChunker:
    _NUMERIC_HEADING_RE = re.compile(
        r'^(\d+(?:\.\d+)*)(?:\s*[、.．]\s*|\s+)([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s：:——\-]{0,40})'
    )
    _CHAPTER_HEADING_RE = re.compile(
        r'第[一二三四五六七八九十百千\d]+[章回节编部篇]'
    )
    _CHINESE_NUM_HEADING_RE = re.compile(
        r'^(?:一|二|三|四|五|六|七|八|九|十)+[、.．\s]'
    )
    _MARKDOWN_HEADING_RE = re.compile(r'^#{1,6}\s')

    _LAW_ARTICLE_RE = re.compile(r'第[一二三四五六七八九十百千\d]+[条章节款项]')
    _QA_PATTERN_RE = re.compile(r'(?:^|\n)(?:Q[:：]|问题[:：]|\d+[\.、）\)])', re.MULTILINE)
    _DIALOGUE_RE = re.compile(r'[「"「『].*?[」"」』]', re.DOTALL)
    _CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```')
    _TABLE_ROW_RE = re.compile(r'^\|.+\|$', re.MULTILINE)
    _LIST_ITEM_RE = re.compile(r'^[\s]*[-*+•]\s|^\d+[\.、）)]\s', re.MULTILINE)
    _NEWS_ARTICLE_MARKER_RE = re.compile(
        r'(?m)^\s*(?:\[\s*)?'
        r'(?P<date>(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}(?:日)?|\d{1,2}[-/]\d{1,2})'
        r'(?:\s+\d{1,2}[：:]\d{2}(?::\d{2})?)?)'
        r'(?:\s*\])?\s*[，,]?\s*正文[:：]\s*'
    )

    _TRANSITION_WORDS = [
        "因此", "所以", "综上", "总之", "总的来说", "由此可见",
        "然而", "但是", "不过", "另一方面", "与此同时", "相反",
        "首先", "其次", "再次", "最后", "第一", "第二", "第三",
        "此外", "另外", "除此之外", "同时", "而且", "并且",
        "例如", "比如", "具体来说", "换句话说", "也就是说",
        "值得注意的是", "需要指出的是", "重要的是", "关键在于",
        "Furthermore", "Moreover", "However", "Therefore", "Consequently",
        "In addition", "In contrast", "On the other hand", "In summary",
        "For example", "For instance", "In other words", "As a result",
    ]

    _TOPIC_SHIFT_PHRASES = [
        "接下来", "下面", "关于", "至于", "回到", "话说",
        "让我们", "现在", "那么", "言归正传",
    ]

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 100, strategy: str = "general"):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.strategy = strategy

    def chunk(self, text: str, doc_id: str, filename: str = "") -> List[TextChunk]:
        if not text or not text.strip():
            return []

        effective_strategy = self.strategy
        if self.strategy == "auto":
            effective_strategy = self.detect_strategy(text, filename)
            logger.info(f"自动检测切分策略: {effective_strategy} (文件: {filename})")

        result_chunks = []
        if effective_strategy == "novel":
            result_chunks = self._chunk_novel(text, doc_id)
        elif effective_strategy == "law":
            result_chunks = self._chunk_law(text, doc_id)
        elif effective_strategy == "qa":
            result_chunks = self._chunk_qa(text, doc_id)
        elif effective_strategy == "book":
            result_chunks = self._chunk_book(text, doc_id)
        elif effective_strategy == "academic":
            result_chunks = self._chunk_academic(text, doc_id)
        elif effective_strategy == "semantic":
            result_chunks = self._chunk_semantic(text, doc_id)
        elif effective_strategy == "code":
            result_chunks = self._chunk_code(text, doc_id)
        elif effective_strategy == "table":
            result_chunks = self._chunk_table(text, doc_id)
        elif effective_strategy == "news":
            result_chunks = self._chunk_news_articles(text, doc_id)
        else:
            result_chunks = self._chunk_general(text, doc_id)

        if self.strategy == "auto":
            for chunk in result_chunks:
                chunk.metadata["chunk_strategy"] = effective_strategy

        # 父子分块 (Parent-Child / Small-to-Big)
        # 将大块作为父块，进一步切分为小块（子块），只对子块向量化
        # 检索时搜到子块，返回父块内容给大模型
        result_chunks = self._build_parent_child_chunks(result_chunks, doc_id)

        return result_chunks

    @staticmethod
    def detect_strategy(text: str, filename: str = "") -> str:
        ext = Path(filename).suffix.lower() if filename else ""
        lines = text.split('\n')
        total_lines = len(lines)
        if total_lines == 0:
            return "general"

        text_sample = text[:5000]

        law_count = len(SmartChunker._LAW_ARTICLE_RE.findall(text_sample))
        if law_count >= 3:
            return "law"

        qa_count = len(SmartChunker._QA_PATTERN_RE.findall(text_sample))
        if qa_count >= 3:
            return "qa"

        news_count = len(SmartChunker._NEWS_ARTICLE_MARKER_RE.findall(text[:20000]))
        if news_count >= 3:
            return "news"

        heading_count = 0
        chapter_count = 0
        for line in lines[:min(200, total_lines)]:
            stripped = line.strip()
            if SmartChunker._MARKDOWN_HEADING_RE.match(stripped):
                heading_count += 1
            if SmartChunker._CHAPTER_HEADING_RE.search(stripped):
                chapter_count += 1
            if SmartChunker._NUMERIC_HEADING_RE.match(stripped):
                heading_count += 1

        if chapter_count >= 2:
            return "book"

        if heading_count >= 4:
            numeric_headings = sum(1 for l in lines[:min(200, total_lines)]
                                  if SmartChunker._NUMERIC_HEADING_RE.match(l.strip()))
            if numeric_headings >= 3:
                return "academic"

        dialogue_count = len(SmartChunker._DIALOGUE_RE.findall(text_sample))
        avg_para_len = 0
        paragraphs = PARAGRAPH_SPLIT.split(text_sample)
        non_empty = [p.strip() for p in paragraphs if p.strip()]
        if non_empty:
            avg_para_len = sum(len(p) for p in non_empty) / len(non_empty)

        if dialogue_count >= 5 and avg_para_len < 200:
            return "novel"

        if ext in ('.csv', '.xlsx', '.xls'):
            return "table"

        if ext in ('.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs', '.rb', '.php', '.sh', '.bat'):
            return "code"

        if heading_count >= 2:
            return "semantic"

        return "semantic"

    @staticmethod
    def _detect_heading_level(line: str) -> int:
        stripped = line.strip()
        if not stripped:
            return 0
        m = SmartChunker._MARKDOWN_HEADING_RE.match(stripped)
        if m:
            level = len(m.group(0).strip())
            return min(level, 6)
        m = SmartChunker._NUMERIC_HEADING_RE.match(stripped)
        if m:
            num_part = m.group(1)
            depth = num_part.count('.') + 1
            return min(depth + 1, 6)
        if SmartChunker._CHAPTER_HEADING_RE.match(stripped):
            return 2
        if SmartChunker._CHINESE_NUM_HEADING_RE.match(stripped):
            return 2
        return 0

    @staticmethod
    def _split_by_headings(text: str) -> List[Tuple[str, str]]:
        lines = text.split('\n')
        sections = []
        current_heading = ""
        current_lines = []

        for line in lines:
            level = SmartChunker._detect_heading_level(line)
            if level > 0:
                if current_lines:
                    sections.append((current_heading, '\n'.join(current_lines)))
                current_heading = line.strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            sections.append((current_heading, '\n'.join(current_lines)))

        return sections

    @staticmethod
    def _common_suffix_prefix_len(left: str, right: str, max_len: int) -> int:
        left = (left or "").strip()
        right = (right or "").strip()
        max_len = max(0, min(max_len, len(left), len(right)))
        for size in range(max_len, 0, -1):
            if left[-size:] == right[:size]:
                return size
        return 0

    @staticmethod
    def _choose_overlap_start(text: str, start_pos: int, end_pos: int, overlap_size: int) -> int:
        if not text or overlap_size <= 0 or end_pos <= start_pos:
            return end_pos

        overlap_start = max(start_pos, end_pos - overlap_size)
        overlap_region = text[overlap_start:end_pos]
        min_overlap = min(20, max(1, overlap_size // 5))

        for pattern in (SENTENCE_ENDINGS, CLAUSE_ENDINGS):
            matches = list(pattern.finditer(overlap_region))
            for match in matches:
                candidate = overlap_start + match.end()
                if candidate < end_pos and len(text[candidate:end_pos].strip()) >= min_overlap:
                    return candidate

        return overlap_start

    @staticmethod
    def _is_hard_parent_boundary(prev: TextChunk, current: TextChunk) -> Tuple[bool, str]:
        prev_meta = prev.metadata or {}
        curr_meta = current.metadata or {}
        prev_type = prev_meta.get("type")
        curr_type = curr_meta.get("type")
        prev_atomic = bool(prev_meta.get("atomic_chunk")) or prev_type in ("table", "image", "figure")
        curr_atomic = bool(curr_meta.get("atomic_chunk")) or curr_type in ("table", "image", "figure")

        if prev_atomic or curr_atomic:
            return True, "atomic_boundary"

        prev_article = prev_meta.get("article_index")
        curr_article = curr_meta.get("article_index")
        if prev_article is not None or curr_article is not None:
            if prev_article != curr_article:
                return True, "article_start"

        prev_heading = prev_meta.get("heading")
        curr_heading = curr_meta.get("heading")
        if prev_heading and curr_heading and prev_heading != curr_heading:
            return True, "section_start"

        return False, "continuous"

    @classmethod
    def _annotate_parent_overlap_metadata(cls, parent_chunks: List[TextChunk], configured_overlap: int) -> None:
        prev: Optional[TextChunk] = None
        max_scan = max(configured_overlap * 2, configured_overlap, 1)

        for chunk in parent_chunks:
            meta = chunk.metadata
            meta["parent_chunk_overlap"] = configured_overlap
            meta["parent_overlap_config"] = configured_overlap

            if prev is None:
                meta["parent_overlap_actual"] = 0
                meta["parent_overlap_expected"] = False
                meta["parent_overlap_boundary"] = "document_start"
                prev = chunk
                continue

            hard_boundary, boundary_type = cls._is_hard_parent_boundary(prev, chunk)
            actual = 0 if hard_boundary else cls._common_suffix_prefix_len(prev.content, chunk.content, max_scan)
            expected = (
                not hard_boundary
                and configured_overlap > 0
                and len((prev.content or "").strip()) > configured_overlap
                and len((chunk.content or "").strip()) > configured_overlap
            )

            meta["parent_overlap_actual"] = actual
            meta["parent_overlap_expected"] = expected
            meta["parent_overlap_boundary"] = "continuous" if actual > 0 else boundary_type
            if actual > 0:
                meta["parent_overlap_preview"] = (chunk.content or "").strip()[:actual][-160:]
            elif expected:
                meta["parent_overlap_missing"] = True

            prev = chunk

    def _build_parent_child_chunks(self, parent_chunks: List[TextChunk], doc_id: str) -> List[TextChunk]:
        """
        父子分块 (Parent-Child / Small-to-Big) 策略：
        - 原始分块作为父块（大块 ~1000字），保留完整上下文
        - 每个父块进一步切分为子块，语义聚焦，便于精准检索
        - 父块 overlap 10%，子块 overlap 10%
        - 以句号或逗号结尾
        - 只对子块进行向量化，检索时搜到子块后返回父块内容给大模型
        """
        child_size = settings.CHILD_CHUNK_SIZE
        child_size_max = settings.CHILD_CHUNK_MAX_SIZE
        child_size_min = settings.CHILD_CHUNK_MIN_SIZE
        child_overlap = settings.CHILD_CHUNK_OVERLAP

        parent_chunks = self._merge_tiny_parent_chunks(parent_chunks, child_size_min)
        self._annotate_parent_overlap_metadata(parent_chunks, self.chunk_overlap)
        child_chunks = []
        child_idx = 0

        for parent in parent_chunks:
            parent_content = parent.content
            parent_id = parent.chunk_id

            is_atomic_parent = bool(parent.metadata.get("atomic_chunk")) or parent.metadata.get("type") in ("table", "image", "figure")

            # 普通正文只有不超过子块上限时才不切；表格/图片/OCR 作为原子块保留完整。
            if is_atomic_parent or len(parent_content) <= child_size_max:
                # 父块本身作为子块，parent_chunk_id 指向自己
                child = TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=parent_content,
                    doc_id=doc_id,
                    index=child_idx,
                    metadata={
                        **parent.metadata,
                        "parent_chunk_id": parent_id,
                        "parent_content": parent_content,
                        "is_child": True,
                        "content_length": len(parent_content),
                        "overlap": 0,
                        "parent_chunk_size": self.chunk_size,
                        "parent_chunk_overlap": self.chunk_overlap,
                        "child_chunk_size": child_size,
                        "child_chunk_overlap": child_overlap,
                    },
                    parent_chunk_id=parent_id,
                )
                if self._append_child_chunk(child_chunks, child, child_size_min):
                    child_idx += 1
                continue

            # 将父块切分为子块，在句子边界处截断
            pos = 0
            while pos < len(parent_content):
                target_end = pos + child_size
                if target_end >= len(parent_content):
                    chunk_text = parent_content[pos:].strip()
                    if chunk_text:
                        overlap_len = min(child_overlap, pos) if pos > 0 else 0
                        child = TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=child_idx,
                            metadata={
                                **parent.metadata,
                                "parent_chunk_id": parent_id,
                                "parent_content": parent_content,
                                "is_child": True,
                                "content_length": len(chunk_text),
                                "overlap": overlap_len,
                                "parent_chunk_size": self.chunk_size,
                                "parent_chunk_overlap": self.chunk_overlap,
                                "child_chunk_size": child_size,
                                "child_chunk_overlap": child_overlap,
                            },
                            parent_chunk_id=parent_id,
                        )
                        if self._append_child_chunk(child_chunks, child, child_size_min):
                            child_idx += 1
                    break

                # 在句号/逗号处截断
                end_pos = self._find_sentence_boundary(parent_content, target_end)
                if end_pos <= pos:
                    end_pos = target_end

                chunk_text = parent_content[pos:end_pos].strip()
                if chunk_text:
                    overlap_len = min(child_overlap, pos) if pos > 0 else 0
                    child = TextChunk(
                        chunk_id=str(uuid.uuid4()),
                        content=chunk_text,
                        doc_id=doc_id,
                        index=child_idx,
                        metadata={
                            **parent.metadata,
                            "parent_chunk_id": parent_id,
                            "parent_content": parent_content,
                            "is_child": True,
                            "content_length": len(chunk_text),
                            "overlap": overlap_len,
                            "parent_chunk_size": self.chunk_size,
                            "parent_chunk_overlap": self.chunk_overlap,
                            "child_chunk_size": child_size,
                            "child_chunk_overlap": child_overlap,
                        },
                        parent_chunk_id=parent_id,
                    )
                    if self._append_child_chunk(child_chunks, child, child_size_min):
                        child_idx += 1

                # 前进到下一个子块起始位置（考虑 overlap）
                next_pos = self._choose_overlap_start(parent_content, pos, end_pos, child_overlap)
                pos = next_pos
                continue
                if next_pos <= pos:
                    next_pos = end_pos
                # 在 overlap 区域找句子边界
                if child_overlap > 0 and next_pos < end_pos:
                    overlap_region = parent_content[next_pos:end_pos]
                    boundary = SENTENCE_ENDINGS.search(overlap_region)
                    if boundary:
                        actual_start = next_pos + boundary.end()
                        if actual_start < end_pos:
                            next_pos = actual_start
                        else:
                            next_pos = end_pos
                    else:
                        boundary = CLAUSE_ENDINGS.search(overlap_region)
                        if boundary:
                            actual_start = next_pos + boundary.end()
                            if actual_start < end_pos:
                                next_pos = actual_start
                            else:
                                next_pos = end_pos
                pos = next_pos

        logger.info(f"父子分块: {len(parent_chunks)} 个父块 → {len(child_chunks)} 个子块")
        return child_chunks

    def _merge_tiny_parent_chunks(self, parent_chunks: List[TextChunk], min_size: int) -> List[TextChunk]:
        merged: List[TextChunk] = []
        pending: Optional[TextChunk] = None

        for chunk in parent_chunks:
            content = (chunk.content or "").strip()
            if not content:
                continue

            if pending:
                if self._can_merge_parent_chunks(pending, chunk):
                    chunk.content = f"{pending.content.rstrip()}\n{content}".strip()
                    chunk.metadata["content_length"] = len(chunk.content)
                    chunk.metadata["merged_tiny_parent"] = True
                    pending = None
                else:
                    merged.append(pending)
                    pending = None

            if len(chunk.content) < min_size:
                if merged:
                    prev = merged[-1]
                    if self._can_merge_parent_chunks(prev, chunk):
                        prev.content = f"{prev.content.rstrip()}\n{chunk.content.strip()}".strip()
                        prev.metadata["content_length"] = len(prev.content)
                        prev.metadata["merged_tiny_parent"] = True
                    else:
                        pending = chunk
                else:
                    pending = chunk
                continue

            merged.append(chunk)

        if pending:
            merged.append(pending)
        return merged

    def _can_merge_parent_chunks(self, left: TextChunk, right: TextChunk) -> bool:
        left_article = left.metadata.get("article_index")
        right_article = right.metadata.get("article_index")
        if left_article is not None or right_article is not None:
            return left_article == right_article
        if left.metadata.get("atomic_chunk") or right.metadata.get("atomic_chunk"):
            return False
        return True

    def _append_child_chunk(self, child_chunks: List[TextChunk], child: TextChunk, min_size: int) -> bool:
        content = (child.content or "").strip()
        if not content:
            return False

        parent_id = child.metadata.get("parent_chunk_id", "")
        if len(content) < min_size and child_chunks:
            prev = child_chunks[-1]
            if prev.metadata.get("parent_chunk_id", "") == parent_id:
                prev.content = f"{prev.content.rstrip()}\n{content}".strip()
                prev.metadata["content_length"] = len(prev.content)
                prev.metadata["merged_tiny_child"] = True
                return False

        child.content = content
        child.metadata["content_length"] = len(content)
        child_chunks.append(child)
        return True

    def _find_sentence_boundary(self, text: str, target_pos: int) -> int:
        if not text:
            return target_pos

        target_pos = max(0, min(target_pos, len(text)))
        left_start = max(0, target_pos - 180)
        right_end = min(len(text), target_pos + 120)

        def last_match(pattern: re.Pattern, start: int, end: int) -> int:
            pos = -1
            for match in pattern.finditer(text, start, end):
                pos = match.end()
            return pos

        def first_match(pattern: re.Pattern, start: int, end: int) -> int:
            match = pattern.search(text, start, end)
            return match.end() if match else -1

        for pattern in (SENTENCE_ENDINGS, CLAUSE_ENDINGS):
            pos = last_match(pattern, left_start, target_pos)
            if pos > left_start:
                return pos
            pos = first_match(pattern, target_pos, right_end)
            if pos != -1:
                return pos

        newline_left = text.rfind('\n', left_start, target_pos)
        if newline_left != -1:
            return newline_left + 1
        newline_right = text.find('\n', target_pos, right_end)
        if newline_right != -1:
            return newline_right + 1

        return target_pos

    def _chunk_semantic(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        sections = self._split_by_headings(text)

        if not sections:
            sections = [("", text)]

        idx = 0
        for heading, section_text in sections:
            section_text = section_text.strip()
            if not section_text:
                continue

            paragraphs = PARAGRAPH_SPLIT.split(section_text)
            paragraphs = [p.strip() for p in paragraphs if p.strip()]

            if not paragraphs:
                continue

            current = ""
            current_sentences = []

            for para in paragraphs:
                sentences = self._split_to_sentences(para)

                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue

                    if len(current) + len(sent) + 1 <= self.chunk_size:
                        current = (current + " " + sent) if current else sent
                        current_sentences.append(sent)
                    else:
                        if current and self._is_semantic_boundary(current_sentences):
                            if current.strip():
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=current.strip(),
                                    doc_id=doc_id,
                                    index=idx,
                                    metadata={"heading": heading} if heading else {},
                                ))
                                idx += 1
                            overlap_text = self._get_semantic_overlap(current, self.chunk_overlap)
                            current = (overlap_text + " " + sent) if overlap_text else sent
                            current_sentences = [sent]
                        else:
                            if current.strip():
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=current.strip(),
                                    doc_id=doc_id,
                                    index=idx,
                                    metadata={"heading": heading} if heading else {},
                                ))
                                idx += 1
                            overlap_text = self._get_semantic_overlap(current.strip(), self.chunk_overlap)
                            current = (overlap_text + " " + sent) if overlap_text else sent
                            current_sentences = [sent]

                if len(current) > self.chunk_size * 1.5:
                    sub_chunks = self._split_long_text(current, doc_id, idx)
                    for sc in sub_chunks:
                        if heading:
                            sc.metadata["heading"] = heading
                    chunks.extend(sub_chunks)
                    idx += len(sub_chunks)
                    current = ""
                    current_sentences = []

            if current.strip():
                chunks.append(TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=current.strip(),
                    doc_id=doc_id,
                    index=idx,
                    metadata={"heading": heading} if heading else {},
                ))
                idx += 1

        return chunks

    def _split_to_sentences(self, text: str) -> List[str]:
        sentences = []
        current = ""
        in_code = False
        for char in text:
            current += char
            if char == '`' and current.count('`') >= 3:
                in_code = not in_code
                continue
            if in_code:
                continue
            if char in '。！？；\n.!?;':
                if current.strip():
                    sentences.append(current.strip())
                current = ""
        if current.strip():
            sentences.append(current.strip())
        return sentences

    def _is_semantic_boundary(self, sentences: List[str]) -> bool:
        if len(sentences) < 2:
            return True

        last_sent = sentences[-1]
        for tw in self._TRANSITION_WORDS:
            if last_sent.startswith(tw):
                return True

        for phrase in self._TOPIC_SHIFT_PHRASES:
            if last_sent.startswith(phrase):
                return True

        if len(sentences) >= 2:
            curr_sent = sentences[-1]
            if self._detect_heading_level(curr_sent) > 0:
                return True
            if curr_sent.startswith(('#', '第', '一、', '二、', '三、')):
                return True

        if len(sentences) >= 2:
            prev_sent = sentences[-2]
            if prev_sent.endswith(('：', ':', '——', '如下', '包括', '如下：')):
                return False

        if len(last_sent) < 15:
            return False

        return True

    def _get_semantic_overlap(self, text: str, overlap_size: int) -> str:
        if not text or overlap_size <= 0:
            return ""
        start = self._choose_overlap_start(text, 0, len(text), overlap_size)
        overlap = text[start:].strip()
        return overlap or text[-overlap_size:].strip()

    def _chunk_news_articles(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks: List[TextChunk] = []
        markers = list(self._NEWS_ARTICLE_MARKER_RE.finditer(text))
        if not markers:
            return self._chunk_semantic(text, doc_id)

        preface = text[:markers[0].start()].strip()
        idx = 0
        if preface:
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=preface,
                doc_id=doc_id,
                index=idx,
                metadata={"type": "preface", "chunk_strategy": "news"},
            ))
            idx += 1

        for article_idx, marker in enumerate(markers):
            start = marker.start()
            end = markers[article_idx + 1].start() if article_idx + 1 < len(markers) else len(text)
            article_text = text[start:end].strip()
            if not article_text:
                continue

            article_date = marker.group("date").strip()
            article_title = self._infer_article_title(article_text)
            base_meta = {
                "type": "news_article",
                "chunk_strategy": "news",
                "article_index": article_idx + 1,
                "article_date": article_date,
            }
            if article_title:
                base_meta["article_title"] = article_title
                base_meta["heading"] = article_title

            if len(article_text) <= self.chunk_size:
                chunks.append(TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=article_text,
                    doc_id=doc_id,
                    index=idx,
                    metadata={**base_meta, "article_part": 1},
                ))
                idx += 1
                continue

            sub_chunks = self._split_long_text(article_text, doc_id, idx)
            for part_idx, sub_chunk in enumerate(sub_chunks, start=1):
                sub_chunk.metadata.update({
                    **base_meta,
                    "article_part": part_idx,
                    "article_part_count": len(sub_chunks),
                })
            chunks.extend(sub_chunks)
            idx += len(sub_chunks)

        return chunks

    def _infer_article_title(self, article_text: str) -> str:
        body = self._NEWS_ARTICLE_MARKER_RE.sub("", article_text, count=1).strip()
        first_sentence = self._split_to_sentences(body[:260])
        if not first_sentence:
            return ""
        title = first_sentence[0].strip()
        title = re.sub(r'^(?:[\u4e00-\u9fff]{2,12}(?:网|报|社|电|讯)[^，。]{0,30}[，,]\s*)', '', title)
        return title[:60]

    def _chunk_general(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        paragraphs = PARAGRAPH_SPLIT.split(text)
        current = ""
        idx = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 2 <= self.chunk_size:
                current = (current + "\n\n" + para) if current else para
            else:
                if current:
                    # 在句子边界处截断，确保chunk以完整句子结尾
                    cut_pos = self._find_sentence_boundary(current, len(current))
                    if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                        chunk_text = current[:cut_pos].strip()
                        remaining = current[cut_pos:].strip()
                    else:
                        chunk_text = current
                        remaining = ""

                    if chunk_text:
                        chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                        ))
                        idx += 1

                    # overlap: 从上一个chunk末尾取overlap长度的内容，从句子边界开始
                    overlap_text = self._get_semantic_overlap(chunk_text, self.chunk_overlap)
                    current = (overlap_text + "\n" + remaining + "\n\n" + para) if overlap_text and remaining else \
                              (overlap_text + "\n\n" + para) if overlap_text else \
                              (remaining + "\n\n" + para) if remaining else para
                else:
                    current = para

                # 如果合并后仍然超长，需要拆分
                if len(current) > self.chunk_size:
                    if len(para) <= self.chunk_size:
                        # current包含overlap+remaining+para，超长时先保存current再处理para
                        sub_chunks = self._split_long_text(current, doc_id, idx)
                        chunks.extend(sub_chunks)
                        idx += len(sub_chunks)
                        current = ""
                    else:
                        sub_chunks = self._split_long_text(para, doc_id, idx)
                        chunks.extend(sub_chunks)
                        idx += len(sub_chunks)
                        current = ""

        if current.strip():
            # 最后一个chunk也在句子边界截断
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _split_long_text(self, text: str, doc_id: str, start_idx: int) -> List[TextChunk]:
        chunks = []
        pos = 0
        idx = start_idx

        while pos < len(text):
            target_end = pos + self.chunk_size
            if target_end >= len(text):
                chunk_text = text[pos:].strip()
                if chunk_text:
                    chunks.append(TextChunk(
                        chunk_id=str(uuid.uuid4()),
                        content=chunk_text,
                        doc_id=doc_id,
                        index=idx,
                    ))
                    idx += 1
                break

            end_pos = self._find_sentence_boundary(text, target_end)
            if end_pos <= pos:
                end_pos = target_end

            chunk_text = text[pos:end_pos].strip()
            if chunk_text:
                chunks.append(TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=chunk_text,
                    doc_id=doc_id,
                    index=idx,
                ))
                idx += 1

            # overlap: 从句子边界开始取overlap内容
            pos = self._choose_overlap_start(text, pos, end_pos, self.chunk_overlap)
            continue
            if self.chunk_overlap > 0 and end_pos - overlap_start > 0:
                overlap_region = text[overlap_start:end_pos]
                # 在overlap区域内找句子边界，从完整句子开始
                boundary = SENTENCE_ENDINGS.search(overlap_region)
                if boundary:
                    actual_start = overlap_start + boundary.end()
                    pos = actual_start if actual_start < end_pos else end_pos
                else:
                    boundary = CLAUSE_ENDINGS.search(overlap_region)
                    if boundary:
                        actual_start = overlap_start + boundary.end()
                        pos = actual_start if actual_start < end_pos else end_pos
                    else:
                        pos = overlap_start
            else:
                pos = end_pos

        return chunks

    def _chunk_novel(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        dialogue_pattern = re.compile(r'[「"「『].*?[」"」』]', re.DOTALL)
        paragraphs = re.split(r'\n+', text)
        current = ""
        idx = 0
        prev_chunk_text = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 1 <= self.chunk_size:
                current = (current + "\n" + para) if current else para
            else:
                if current:
                    end_pos = self._find_sentence_boundary(current, len(current))
                    chunk_text = current[:end_pos].strip() if end_pos < len(current) else current
                    remaining = current[end_pos:] if end_pos < len(current) else ""
                    if chunk_text:
                        chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                        ))
                        idx += 1
                        prev_chunk_text = chunk_text
                    overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap)
                    current = (overlap_text + "\n" + remaining + "\n" + para) if overlap_text and remaining.strip() else \
                              (overlap_text + "\n" + para) if overlap_text else \
                              (remaining + "\n" + para) if remaining.strip() else para
                else:
                    sub_chunks = self._split_long_text(para, doc_id, idx)
                    chunks.extend(sub_chunks)
                    idx += len(sub_chunks)
                    if sub_chunks:
                        prev_chunk_text = sub_chunks[-1].content
                    current = ""

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _chunk_law(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        article_pattern = re.compile(r'(第[一二三四五六七八九十百千\d]+[条章节款项编部])')
        parts = article_pattern.split(text)

        current = ""
        idx = 0

        i = 0
        while i < len(parts):
            part = parts[i]
            if article_pattern.match(part) and i + 1 < len(parts):
                section_header = part
                section_body = parts[i + 1] if i + 1 < len(parts) else ""
                section = section_header + section_body

                if len(current) + len(section) <= self.chunk_size:
                    current = (current + "\n\n" + section) if current else section
                else:
                    if current:
                        # 句子边界截断
                        cut_pos = self._find_sentence_boundary(current, len(current))
                        if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                            chunk_text = current[:cut_pos].strip()
                            remaining = current[cut_pos:].strip()
                        else:
                            chunk_text = current
                            remaining = ""
                        if chunk_text:
                            chunks.append(TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=chunk_text,
                                doc_id=doc_id,
                                index=idx,
                            ))
                            idx += 1
                        overlap_text = self._get_semantic_overlap(chunk_text, self.chunk_overlap)
                        current = (overlap_text + "\n" + remaining + "\n\n" + section) if overlap_text and remaining else \
                                  (overlap_text + "\n\n" + section) if overlap_text else \
                                  (remaining + "\n\n" + section) if remaining else section
                    else:
                        current = section
                i += 2
            else:
                part = part.strip()
                if part:
                    if len(current) + len(part) + 2 <= self.chunk_size:
                        current = (current + "\n\n" + part) if current else part
                    else:
                        if current:
                            cut_pos = self._find_sentence_boundary(current, len(current))
                            if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                                chunk_text = current[:cut_pos].strip()
                                remaining = current[cut_pos:].strip()
                            else:
                                chunk_text = current
                                remaining = ""
                            if chunk_text:
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=chunk_text,
                                    doc_id=doc_id,
                                    index=idx,
                                ))
                                idx += 1
                            overlap_text = self._get_semantic_overlap(chunk_text, self.chunk_overlap)
                            current = (overlap_text + "\n" + remaining + "\n\n" + part) if overlap_text and remaining else \
                                      (overlap_text + "\n\n" + part) if overlap_text else \
                                      (remaining + "\n\n" + part) if remaining else part
                        else:
                            current = part
                i += 1

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _chunk_qa(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        qa_pattern = re.compile(r'(?:^|\n)(?:Q[:：]|问题[:：]|\d+[\.、）\)])', re.MULTILINE)
        parts = qa_pattern.split(text)

        current = ""
        idx = 0
        prev_chunk_text = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if len(part) <= self.chunk_size:
                if len(current) + len(part) + 2 <= self.chunk_size:
                    current = (current + "\n\n" + part) if current else part
                else:
                    if current:
                        # 句子边界截断
                        cut_pos = self._find_sentence_boundary(current, len(current))
                        if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                            chunk_text = current[:cut_pos].strip()
                            remaining = current[cut_pos:].strip()
                        else:
                            chunk_text = current
                            remaining = ""
                        if chunk_text:
                            chunks.append(TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=chunk_text,
                                doc_id=doc_id,
                                index=idx,
                            ))
                            idx += 1
                            prev_chunk_text = chunk_text
                        overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap)
                        current = (overlap_text + "\n" + remaining + "\n\n" + part) if overlap_text and remaining else \
                                  (overlap_text + "\n\n" + part) if overlap_text else \
                                  (remaining + "\n\n" + part) if remaining else part
                    else:
                        current = part
            else:
                if current:
                    cut_pos = self._find_sentence_boundary(current, len(current))
                    if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                        chunk_text = current[:cut_pos].strip()
                    else:
                        chunk_text = current
                    if chunk_text:
                        chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                        ))
                        idx += 1
                        prev_chunk_text = chunk_text
                sub_chunks = self._split_long_text(part, doc_id, idx)
                chunks.extend(sub_chunks)
                idx += len(sub_chunks)
                if sub_chunks:
                    prev_chunk_text = sub_chunks[-1].content
                current = ""

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _chunk_book(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        chapter_pattern = re.compile(
            r'(?:^|\n)(#{1,6}\s.+|第[一二三四五六七八九十百千\d]+[章回节].*|\d+(?:\.\d+)*\s+[、.．\s]?\s*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s：:——\-]{0,40}|(?:一|二|三|四|五|六|七|八|九|十)+[、.．\s]+[\u4e00-\u9fff]{2,15})',
            re.MULTILINE
        )
        parts = chapter_pattern.split(text)

        current = ""
        idx = 0
        prev_chunk_text = ""

        i = 0
        while i < len(parts):
            part = parts[i].strip() if parts[i] else ""
            if chapter_pattern.match(part) and i + 1 < len(parts):
                chapter_header = part
                chapter_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                chapter = chapter_header + "\n" + chapter_body

                if len(current) + len(chapter) <= self.chunk_size:
                    current = (current + "\n\n" + chapter) if current else chapter
                else:
                    if current:
                        cut_pos = self._find_sentence_boundary(current, len(current))
                        if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                            chunk_text = current[:cut_pos].strip()
                            remaining = current[cut_pos:].strip()
                        else:
                            chunk_text = current
                            remaining = ""
                        if chunk_text:
                            chunks.append(TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=chunk_text,
                                doc_id=doc_id,
                                index=idx,
                            ))
                            idx += 1
                            prev_chunk_text = chunk_text
                        overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap)
                        current = (overlap_text + "\n" + remaining + "\n\n" + chapter) if overlap_text and remaining else \
                                  (overlap_text + "\n\n" + chapter) if overlap_text else \
                                  (remaining + "\n\n" + chapter) if remaining else chapter
                    else:
                        current = chapter
                i += 2
            else:
                if part:
                    if len(current) + len(part) + 2 <= self.chunk_size:
                        current = (current + "\n\n" + part) if current else part
                    else:
                        if current:
                            cut_pos = self._find_sentence_boundary(current, len(current))
                            if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                                chunk_text = current[:cut_pos].strip()
                            else:
                                chunk_text = current
                            if chunk_text:
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=chunk_text,
                                    doc_id=doc_id,
                                    index=idx,
                                ))
                                idx += 1
                                prev_chunk_text = chunk_text
                        overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap) if prev_chunk_text else ""
                        current_with_overlap = (overlap_text + "\n\n" + part) if overlap_text else part
                        sub_chunks = self._split_long_text(current_with_overlap, doc_id, idx)
                        chunks.extend(sub_chunks)
                        idx += len(sub_chunks)
                        if sub_chunks:
                            prev_chunk_text = sub_chunks[-1].content
                        current = ""
                i += 1

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _chunk_academic(self, text: str, doc_id: str) -> List[TextChunk]:
        sections = self._split_by_headings(text)
        if not sections:
            return self._chunk_general(text, doc_id)

        chunks = []
        idx = 0
        current = ""
        current_heading_path = []
        prev_chunk_text = ""

        for heading, section_text in sections:
            section_text = section_text.strip()
            if not section_text:
                continue

            level = self._detect_heading_level(heading) if heading else 0
            if level > 0:
                current_heading_path = current_heading_path[:level - 1] + [heading]

            section_with_context = section_text
            if len(section_text) <= self.chunk_size:
                if len(current) + len(section_text) + 2 <= self.chunk_size:
                    current = (current + "\n\n" + section_text) if current else section_text
                else:
                    if current:
                        # 句子边界截断
                        cut_pos = self._find_sentence_boundary(current, len(current))
                        if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                            chunk_text = current[:cut_pos].strip()
                            remaining = current[cut_pos:].strip()
                        else:
                            chunk_text = current
                            remaining = ""
                        if chunk_text:
                            chunk = TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=chunk_text,
                                doc_id=doc_id,
                                index=idx,
                            )
                            if current_heading_path:
                                chunk.metadata["heading_path"] = " > ".join(current_heading_path)
                            chunks.append(chunk)
                            idx += 1
                            prev_chunk_text = chunk_text
                        overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap)
                        current = (overlap_text + "\n" + remaining + "\n\n" + section_text) if overlap_text and remaining else \
                                  (overlap_text + "\n\n" + section_text) if overlap_text else \
                                  (remaining + "\n\n" + section_text) if remaining else section_text
                    else:
                        current = section_text
            else:
                if current:
                    cut_pos = self._find_sentence_boundary(current, len(current))
                    if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                        chunk_text = current[:cut_pos].strip()
                    else:
                        chunk_text = current
                    if chunk_text:
                        chunk = TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                        )
                        if current_heading_path:
                            chunk.metadata["heading_path"] = " > ".join(current_heading_path)
                        chunks.append(chunk)
                        idx += 1
                        prev_chunk_text = chunk_text
                    current = ""

                sub_chunks = self._split_academic_section(section_text, doc_id, idx, heading)
                for sc in sub_chunks:
                    if current_heading_path:
                        sc.metadata["heading_path"] = " > ".join(current_heading_path)
                chunks.extend(sub_chunks)
                idx += len(sub_chunks)

        if current.strip():
            chunk = TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            )
            if current_heading_path:
                chunk.metadata["heading_path"] = " > ".join(current_heading_path)
            chunks.append(chunk)

        return chunks

    def _split_academic_section(self, text: str, doc_id: str, start_idx: int, heading: str = "") -> List[TextChunk]:
        paragraphs = PARAGRAPH_SPLIT.split(text)
        chunks = []
        current = ""
        idx = start_idx
        prev_chunk_text = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 2 <= self.chunk_size:
                current = (current + "\n\n" + para) if current else para
            else:
                if current:
                    # 句子边界截断
                    cut_pos = self._find_sentence_boundary(current, len(current))
                    if cut_pos < len(current) and cut_pos > len(current) * 0.5:
                        chunk_text = current[:cut_pos].strip()
                        remaining = current[cut_pos:].strip()
                    else:
                        chunk_text = current
                        remaining = ""
                    if chunk_text:
                        chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                        ))
                        idx += 1
                        prev_chunk_text = chunk_text
                    overlap_text = self._get_semantic_overlap(prev_chunk_text, self.chunk_overlap)
                    current = (overlap_text + "\n" + remaining + "\n\n" + para) if overlap_text and remaining else \
                              (overlap_text + "\n\n" + para) if overlap_text else \
                              (remaining + "\n\n" + para) if remaining else para
                else:
                    current = para

                if len(current) > self.chunk_size:
                    sub_chunks = self._split_long_text(current, doc_id, idx)
                    chunks.extend(sub_chunks)
                    idx += len(sub_chunks)
                    if sub_chunks:
                        prev_chunk_text = sub_chunks[-1].content
                    current = ""

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
            ))

        return chunks

    def _chunk_code(self, text: str, doc_id: str) -> List[TextChunk]:
        chunks = []
        code_block_re = re.compile(r'(```[\s\S]*?```)', re.MULTILINE)
        parts = code_block_re.split(text)

        current = ""
        idx = 0

        i = 0
        while i < len(parts):
            part = parts[i]
            is_code_block = part.startswith('```') and part.endswith('```')

            if is_code_block:
                lines = part.split('\n')
                lang_line = lines[0] if lines else ""
                code_body = '\n'.join(lines[1:-1]) if len(lines) > 2 else ""

                func_pattern = re.compile(r'^(?:def |class |function |public |private |protected |static |async |func |fn |pub fn )', re.MULTILINE)
                func_splits = func_pattern.split(code_body)
                func_headers = func_pattern.findall(code_body)

                if func_headers:
                    code_parts = []
                    for j, header in enumerate(func_headers):
                        body = func_splits[j + 1] if j + 1 < len(func_splits) else ""
                        code_parts.append(header + body)
                    remaining = func_splits[0]
                    if remaining.strip():
                        code_parts.insert(0, remaining)

                    for cp in code_parts:
                        cp_text = lang_line + '\n' + cp.strip() + '\n```'
                        if len(cp_text) <= self.chunk_size:
                            if len(current) + len(cp_text) + 2 <= self.chunk_size:
                                current = (current + "\n\n" + cp_text) if current else cp_text
                            else:
                                if current:
                                    chunks.append(TextChunk(
                                        chunk_id=str(uuid.uuid4()),
                                        content=current,
                                        doc_id=doc_id,
                                        index=idx,
                                        metadata={"type": "code"},
                                    ))
                                    idx += 1
                                current = cp_text
                        else:
                            if current:
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=current,
                                    doc_id=doc_id,
                                    index=idx,
                                    metadata={"type": "code"},
                                ))
                                idx += 1
                            sub_lines = cp_text.split('\n')
                            sub_current = ""
                            for sl in sub_lines:
                                if len(sub_current) + len(sl) + 1 <= self.chunk_size:
                                    sub_current = (sub_current + '\n' + sl) if sub_current else sl
                                else:
                                    if sub_current:
                                        chunks.append(TextChunk(
                                            chunk_id=str(uuid.uuid4()),
                                            content=sub_current,
                                            doc_id=doc_id,
                                            index=idx,
                                            metadata={"type": "code"},
                                        ))
                                        idx += 1
                                    sub_current = sl
                            if sub_current:
                                current = sub_current
                else:
                    if len(part) <= self.chunk_size:
                        if len(current) + len(part) + 2 <= self.chunk_size:
                            current = (current + "\n\n" + part) if current else part
                        else:
                            if current:
                                chunks.append(TextChunk(
                                    chunk_id=str(uuid.uuid4()),
                                    content=current,
                                    doc_id=doc_id,
                                    index=idx,
                                    metadata={"type": "code"},
                                ))
                                idx += 1
                            current = part
                    else:
                        if current:
                            chunks.append(TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=current,
                                doc_id=doc_id,
                                index=idx,
                                metadata={"type": "code"},
                            ))
                            idx += 1
                        sub_lines = part.split('\n')
                        sub_current = ""
                        for sl in sub_lines:
                            if len(sub_current) + len(sl) + 1 <= self.chunk_size:
                                sub_current = (sub_current + '\n' + sl) if sub_current else sl
                            else:
                                if sub_current:
                                    chunks.append(TextChunk(
                                        chunk_id=str(uuid.uuid4()),
                                        content=sub_current,
                                        doc_id=doc_id,
                                        index=idx,
                                        metadata={"type": "code"},
                                    ))
                                    idx += 1
                                sub_current = sl
                        if sub_current:
                            current = sub_current
                i += 1
            else:
                part = part.strip()
                if part:
                    if len(current) + len(part) + 2 <= self.chunk_size:
                        current = (current + "\n\n" + part) if current else part
                    else:
                        if current:
                            chunks.append(TextChunk(
                                chunk_id=str(uuid.uuid4()),
                                content=current,
                                doc_id=doc_id,
                                index=idx,
                                metadata={"type": "code"},
                            ))
                            idx += 1
                        current = part
                i += 1

        if current.strip():
            chunks.append(TextChunk(
                chunk_id=str(uuid.uuid4()),
                content=current,
                doc_id=doc_id,
                index=idx,
                metadata={"type": "code"},
            ))

        return chunks

    def _chunk_table(self, text: str, doc_id: str) -> List[TextChunk]:
        """表格分块策略：
        - 每个数据块必须包含完整表头信息，确保字段与表头的关联性
        - 按数据逻辑单元进行分块，避免破坏数据关联性
        - 表格与散文内容分开处理，不混合
        """
        chunks = []
        lines = text.split('\n')
        table_groups = []
        current_table = []
        current_prose = []

        for line in lines:
            if self._TABLE_ROW_RE.match(line.strip()):
                if current_prose:
                    table_groups.append(("prose", '\n'.join(current_prose)))
                    current_prose = []
                current_table.append(line)
            else:
                if current_table:
                    table_groups.append(("table", '\n'.join(current_table)))
                    current_table = []
                if line.strip():
                    current_prose.append(line)

        if current_table:
            table_groups.append(("table", '\n'.join(current_table)))
        if current_prose:
            table_groups.append(("prose", '\n'.join(current_prose)))

        idx = 0

        for group_type, group_text in table_groups:
            if group_type == "table":
                table_lines = group_text.split('\n')
                header_line = table_lines[0] if table_lines else ""
                separator_idx = -1
                for j, tl in enumerate(table_lines):
                    if re.match(r'^\|[\s\-:|]+\|$', tl.strip()):
                        separator_idx = j
                        break

                # 提取表头（含分隔行），确保每个分块都包含完整表头
                table_header = ""
                if separator_idx >= 0:
                    table_header = '\n'.join(table_lines[:separator_idx + 1])

                data_lines = table_lines[separator_idx + 1:] if separator_idx >= 0 else table_lines[1:]

                # 按数据逻辑单元分块：每个分块必须包含表头
                row_batch = []
                for dl in data_lines:
                    row_batch.append(dl)
                    batch_text = table_header + '\n' + '\n'.join(row_batch) if table_header else '\n'.join(row_batch)

                    if len(batch_text) > self.chunk_size and len(row_batch) > 1:
                        # 当前批次超长，保存前一批次（含表头）
                        chunk_text = table_header + '\n' + '\n'.join(row_batch[:-1]) if table_header else '\n'.join(row_batch[:-1])
                        chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                            metadata={"type": "table", "has_header": bool(table_header)},
                        ))
                        idx += 1
                        # 新批次从当前行开始，重新带上表头
                        row_batch = [dl]

                # 处理最后一批数据行（含表头）
                if row_batch:
                    batch_text = table_header + '\n' + '\n'.join(row_batch) if table_header else '\n'.join(row_batch)
                    chunks.append(TextChunk(
                        chunk_id=str(uuid.uuid4()),
                        content=batch_text,
                        doc_id=doc_id,
                        index=idx,
                        metadata={"type": "table", "has_header": bool(table_header)},
                    ))
                    idx += 1
            else:
                # 散文内容独立分块，不与表格混合
                chunks.append(TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=group_text,
                    doc_id=doc_id,
                    index=idx,
                ))
                idx += 1

        return chunks


class DocumentProcessor:
    def __init__(self):
        self.chunk_size = settings.CHUNK_SIZE
        self.chunk_overlap = settings.CHUNK_OVERLAP

    def process_upload(self, file_content: bytes, filename: str,
                       chunk_strategy: str = "auto",
                       chunk_size: int = 0,
                       chunk_overlap: int = 0,
                       kb_id: str = "") -> Document:
        result = file_converter.convert(file_content, filename)
        if not result.success:
            raise ValueError(f"文件处理失败: {result.error}")

        doc_id = str(uuid.uuid4())
        effective_size = chunk_size or self.chunk_size
        effective_overlap = chunk_overlap or self.chunk_overlap
        effective_strategy = chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
        if effective_strategy == "layout_aware":
            effective_strategy = "auto"
        cleaned_markdown = TextCleaner.clean_markdown(result.markdown_content)

        chunker = SmartChunker(
            chunk_size=effective_size,
            chunk_overlap=effective_overlap,
            strategy=effective_strategy,
        )
        chunks = chunker.chunk(cleaned_markdown, doc_id, filename=filename)

        detected_strategy = effective_strategy
        if effective_strategy == "auto" and chunks:
            detected_strategy = chunks[0].metadata.get("chunk_strategy", effective_strategy)

        for i, chunk in enumerate(chunks):
            chunk.metadata["title"] = Path(filename).stem
            chunk.metadata["kb_id"] = kb_id
            chunk.metadata["chunk_strategy"] = detected_strategy
            chunk.metadata["detected_doc_type"] = detected_strategy
            chunk.metadata["document_title"] = Path(filename).stem
            chunk.metadata["original_filename"] = filename

        safe_filename = f"{doc_id}_{filename}"
        upload_path = os.path.join(settings.UPLOAD_DIR, safe_filename)
        with open(upload_path, "wb") as f:
            f.write(file_content)

        md_path = os.path.join(settings.KNOWLEDGE_BASE_DIR, f"{doc_id}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(cleaned_markdown)

        doc = Document(
            doc_id=doc_id,
            title=Path(filename).stem,
            content=cleaned_markdown,
            source_path=upload_path,
            chunks=chunks,
            metadata={"conversion": result.metadata or {}},
        )

        # 分块质量检查
        ChunkQualityChecker.check_chunks(
            chunks,
            parent_size=effective_size,
            child_size=settings.CHILD_CHUNK_SIZE,
            filename=filename,
        )

        logger.info(f"上传文档处理完成: {filename}, 策略={detected_strategy}, {len(chunks)} 个分块")
        return doc

    def process_file(self, file_path: str,
                     chunk_strategy: str = "auto",
                     chunk_size: int = 0,
                     chunk_overlap: int = 0,
                     kb_id: str = "") -> Document:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            content = f.read()

        result = file_converter.convert(content, filename)
        if not result.success:
            raise ValueError(f"文件处理失败: {result.error}")

        doc_id = str(uuid.uuid4())
        effective_size = chunk_size or self.chunk_size
        effective_overlap = chunk_overlap or self.chunk_overlap
        effective_strategy = chunk_strategy or settings.CHUNK_STRATEGY_DEFAULT
        cleaned_markdown = TextCleaner.clean_markdown(result.markdown_content)

        chunker = SmartChunker(
            chunk_size=effective_size,
            chunk_overlap=effective_overlap,
            strategy=effective_strategy,
        )
        chunks = chunker.chunk(cleaned_markdown, doc_id, filename=filename)

        detected_strategy = effective_strategy
        if effective_strategy == "auto" and chunks:
            detected_strategy = chunks[0].metadata.get("chunk_strategy", effective_strategy)

        for i, chunk in enumerate(chunks):
            chunk.metadata["title"] = Path(filename).stem
            chunk.metadata["kb_id"] = kb_id
            chunk.metadata["chunk_strategy"] = detected_strategy
            chunk.metadata["detected_doc_type"] = detected_strategy
            chunk.metadata["document_title"] = Path(filename).stem
            chunk.metadata["original_filename"] = filename

        md_path = os.path.join(settings.KNOWLEDGE_BASE_DIR, f"{doc_id}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(cleaned_markdown)

        doc = Document(
            doc_id=doc_id,
            title=Path(filename).stem,
            content=cleaned_markdown,
            source_path=file_path,
            chunks=chunks,
            metadata={"conversion": result.metadata or {}},
        )

        logger.info(f"文档处理完成: {filename}, 策略={detected_strategy}, {len(chunks)} 个分块")
        return doc


doc_processor = DocumentProcessor()
