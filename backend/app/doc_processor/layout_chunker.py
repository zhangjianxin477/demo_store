import os
import re
import io
import uuid
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from app.core.config import settings
from app.doc_processor.layout_analyzer import (
    LayoutAnalyzer, LayoutBlock, LayoutBlockType, LayoutResult, layout_analyzer,
)
from app.doc_processor.table_extractor import table_extractor
from app.doc_processor.image_extractor import image_extractor
from app.doc_processor.processor import (
    TextChunk, Document, SENTENCE_ENDINGS, CLAUSE_ENDINGS,
    ChunkQualityChecker, TextCleaner, SmartChunker,
)

logger = logging.getLogger(__name__)


class LayoutAwareChunker:
    # 父块大小范围：700~1200字，默认1000
    PARENT_SIZE_MIN = 700
    PARENT_SIZE_MAX = 1200
    PARENT_SIZE_DEFAULT = 1000

    # 子块大小由配置控制，默认更适合中文知识库问答
    CHILD_SIZE_MIN = settings.CHILD_CHUNK_MIN_SIZE
    CHILD_SIZE_MAX = settings.CHILD_CHUNK_MAX_SIZE
    CHILD_SIZE_DEFAULT = settings.CHILD_CHUNK_SIZE

    def __init__(self, chunk_size: int = 0, chunk_overlap: int = 0):
        self.parent_size = max(self.PARENT_SIZE_MIN, min(self.PARENT_SIZE_MAX, chunk_size or self.PARENT_SIZE_DEFAULT))
        self.parent_overlap = max(0, min(chunk_overlap or int(self.parent_size * 0.1), self.parent_size // 2))
        self.child_size = max(self.CHILD_SIZE_MIN, min(self.CHILD_SIZE_MAX, self.CHILD_SIZE_DEFAULT))
        self.child_overlap = max(0, min(settings.CHILD_CHUNK_OVERLAP, self.child_size // 2))

    def chunk_from_layout(self, layout_result: LayoutResult, doc_id: str,
                          filename: str = "", kb_id: str = "") -> List[TextChunk]:
        """两阶段分块：先合并为父块，再切为子块（父子分块 Small-to-Big）"""
        # 第一阶段：合并相邻块为有语义的父块
        raw_sections = self._merge_layout_blocks(layout_result, doc_id, filename, kb_id)

        # 第一阶段补充：如果合并后的section超长，切分为多个父块（带overlap）
        parent_chunks = self._split_into_parent_chunks(raw_sections, doc_id)

        # 第二阶段：将父块切为子块，只对子块向量化
        child_chunks = self._build_child_chunks(parent_chunks, doc_id)

        logger.info(f"版面感知父子分块: {len(layout_result.blocks)} 个布局块 → {len(raw_sections)} 个原始section → {len(parent_chunks)} 个父块 → {len(child_chunks)} 个子块")
        return child_chunks

    def _merge_layout_blocks(self, layout_result: LayoutResult, doc_id: str,
                              filename: str = "", kb_id: str = "") -> List[Dict]:
        """
        合并相邻布局块为原始section（可能超长，后续再切分）
        - 同一标题下的段落合并
        - 表格、图片/OCR块独立成块，避免被普通正文稀释
        - 分栏文档：不同栏的内容不合并，避免跨栏内容被错误分割
        """
        sections = []
        current_section = ""
        current_heading = ""
        current_level = 0
        current_page = 0
        current_column = 0
        layout_profile = {
            "has_multi_column": bool(layout_result.metadata.get("has_multi_column")),
            "tables_count": layout_result.metadata.get("tables_count", len(layout_result.tables)),
            "images_count": layout_result.metadata.get("images_count", len(layout_result.images)),
        }

        def flush_current():
            nonlocal current_section
            if not current_section.strip():
                return
            sections.append({
                "content": current_section.strip(),
                "heading": current_heading,
                "heading_level": current_level,
                "page": current_page,
                "filename": filename,
                "kb_id": kb_id,
                "column": current_column,
                "block_type": "text",
                "atomic_chunk": False,
                "layout_profile": layout_profile,
            })
            current_section = ""

        def append_atomic_section(block: LayoutBlock, block_type: str, content: str):
            sections.append({
                "content": content.strip(),
                "heading": current_heading,
                "heading_level": current_level,
                "page": block.page,
                "filename": filename,
                "kb_id": kb_id,
                "column": block.metadata.get("column", 0),
                "block_type": block_type,
                "atomic_chunk": True,
                "bbox": block.bbox,
                "rows": block.metadata.get("rows", 0),
                "cols": block.metadata.get("cols", 0),
                "ocr_extracted": bool(block.metadata.get("ocr_extracted")),
                "layout_profile": layout_profile,
            })

        for block in layout_result.blocks:
            if block.block_type in (LayoutBlockType.HEADER, LayoutBlockType.FOOTER,
                                     LayoutBlockType.PAGE_NUMBER):
                continue

            block_column = block.metadata.get("column", 0)

            if block.block_type == LayoutBlockType.TABLE:
                flush_current()
                append_atomic_section(block, "table", block.content)
                continue

            if block.block_type in (LayoutBlockType.IMAGE, LayoutBlockType.FIGURE):
                flush_current()
                content = block.content.strip()
                if block.metadata.get("ocr_text"):
                    content = f"{content}\n\n[OCR识别内容]\n{block.metadata.get('ocr_text', '').strip()}"
                append_atomic_section(block, block.block_type.value, content)
                continue

            # 标题：保存当前section，开始新section
            if block.block_type in (LayoutBlockType.HEADING_H1, LayoutBlockType.HEADING_H2,
                                     LayoutBlockType.HEADING_H3, LayoutBlockType.HEADING_H4,
                                     LayoutBlockType.TITLE):
                flush_current()
                current_heading = block.content.strip()
                current_level = block.level
                current_page = block.page
                current_column = block_column
                current_section = block.content + "\n\n"
                continue

            # 分栏处理：多栏文档中只要栏号变化就切断，避免左右栏内容混在同一块
            if layout_profile["has_multi_column"] and current_section.strip() and block_column != current_column:
                flush_current()
                current_column = block_column

            # 各种块类型合并到当前section
            block_content = ""
            if block.block_type == LayoutBlockType.CODE_BLOCK:
                block_content = "```\n" + block.content.strip() + "\n```\n\n"
            elif block.content and block.content.strip():
                block_content = block.content + "\n\n"

            if block_content:
                if not current_page:
                    current_page = block.page
                if current_column == 0:
                    current_column = block_column
                # 如果加上这个块不超过父块最大值的2倍，合并
                if len(current_section) + len(block_content) <= self.PARENT_SIZE_MAX * 2:
                    current_section += block_content
                else:
                    # 先保存当前section
                    flush_current()
                    current_section = block_content
                    current_column = block_column

        # 保存最后一个section
        flush_current()

        return sections

    def _split_into_parent_chunks(self, sections: List[Dict], doc_id: str) -> List[TextChunk]:
        """
        将原始section切分为父块：
        - 父块大小 ~1000字（±10%），overlap 100字
        - 以句号或逗号结尾
        """
        parent_chunks = []
        idx = 0

        for section in sections:
            content = section["content"]
            section_type = section.get("block_type", "text")
            base_metadata = {
                "heading": section["heading"],
                "heading_level": section["heading_level"],
                "page": section["page"],
                "title": Path(section["filename"]).stem if section["filename"] else "",
                "kb_id": section["kb_id"],
                "chunk_strategy": "layout_aware",
                "layout_block_type": section_type,
                "type": section_type,
                "layout_column": section.get("column", 0),
                "bbox": section.get("bbox"),
                "atomic_chunk": bool(section.get("atomic_chunk")),
                "layout_profile": section.get("layout_profile", {}),
                "is_table_chunk": section_type == "table",
                "is_media_chunk": section_type in ("image", "figure"),
                "table_rows": section.get("rows", 0),
                "table_cols": section.get("cols", 0),
                "ocr_extracted": bool(section.get("ocr_extracted")),
            }

            # 如果section不超长，直接作为父块
            if len(content) <= self.PARENT_SIZE_MAX:
                parent_chunks.append(TextChunk(
                    chunk_id=str(uuid.uuid4()),
                    content=content,
                    doc_id=doc_id,
                    index=idx,
                    metadata={
                        **base_metadata,
                        "content_length": len(content),
                        "overlap": 0,
                    },
                ))
                idx += 1
                continue

            # 超长section需要切分为多个父块，带overlap
            # 检测是否为Markdown表格，如果是则按行切分并保留表头
            table_header = None
            is_markdown_table = content.strip().startswith('|') and '\n|' in content
            if is_markdown_table:
                lines = content.strip().split('\n')
                # 找到表头行和分隔行
                if len(lines) >= 2 and '---' in lines[1]:
                    table_header = lines[0] + '\n' + lines[1] + '\n'
                    data_lines = lines[2:]
                else:
                    table_header = lines[0] + '\n'
                    data_lines = lines[1:]

            if table_header and len(data_lines) > 0:
                # 按行切分表格，每个分块都包含表头
                current_rows = []
                current_len = len(table_header)
                for row in data_lines:
                    row_len = len(row) + 1  # +1 for newline
                    if current_len + row_len > self.parent_size and current_rows:
                        # 保存当前分块
                        chunk_text = table_header + '\n'.join(current_rows)
                        parent_chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                            metadata={
                                **base_metadata,
                                "content_length": len(chunk_text),
                                "overlap": 0,
                                "is_table_chunk": True,
                            },
                        ))
                        idx += 1
                        current_rows = []
                        current_len = len(table_header)
                    current_rows.append(row)
                    current_len += row_len
                # 保存最后一批行
                if current_rows:
                    chunk_text = table_header + '\n'.join(current_rows)
                    parent_chunks.append(TextChunk(
                        chunk_id=str(uuid.uuid4()),
                        content=chunk_text,
                        doc_id=doc_id,
                        index=idx,
                        metadata={
                            **base_metadata,
                            "content_length": len(chunk_text),
                            "overlap": 0,
                            "is_table_chunk": True,
                        },
                    ))
                    idx += 1
                continue

            pos = 0
            while pos < len(content):
                target_end = pos + self.parent_size

                if target_end >= len(content):
                    chunk_text = content[pos:].strip()
                    if chunk_text:
                        overlap_len = min(self.parent_overlap, pos) if pos > 0 else 0
                        parent_chunks.append(TextChunk(
                            chunk_id=str(uuid.uuid4()),
                            content=chunk_text,
                            doc_id=doc_id,
                            index=idx,
                            metadata={
                                **base_metadata,
                                "content_length": len(chunk_text),
                                "overlap": overlap_len,
                            },
                        ))
                        idx += 1
                    break

                # 在句号/逗号处截断
                end_pos = self._find_sentence_boundary(content, target_end)
                if end_pos <= pos:
                    end_pos = target_end

                chunk_text = content[pos:end_pos].strip()
                if chunk_text:
                    overlap_len = min(self.parent_overlap, pos) if pos > 0 else 0
                    parent_chunks.append(TextChunk(
                        chunk_id=str(uuid.uuid4()),
                        content=chunk_text,
                        doc_id=doc_id,
                        index=idx,
                        metadata={
                            **base_metadata,
                            "content_length": len(chunk_text),
                            "overlap": overlap_len,
                        },
                    ))
                    idx += 1

                # 前进到下一个父块起始位置（考虑 overlap）
                next_pos = SmartChunker._choose_overlap_start(content, pos, end_pos, self.parent_overlap)
                pos = next_pos
                continue
                if next_pos <= pos:
                    next_pos = end_pos
                # 在 overlap 区域找句子边界
                if self.parent_overlap > 0 and next_pos < end_pos:
                    overlap_region = content[next_pos:end_pos]
                    boundary = SENTENCE_ENDINGS.search(overlap_region)
                    if boundary:
                        actual_start = next_pos + boundary.end()
                        next_pos = actual_start if actual_start < end_pos else end_pos
                    else:
                        boundary = CLAUSE_ENDINGS.search(overlap_region)
                        if boundary:
                            actual_start = next_pos + boundary.end()
                            next_pos = actual_start if actual_start < end_pos else end_pos
                pos = next_pos

        return parent_chunks

    def _build_child_chunks(self, parent_chunks: List[TextChunk], doc_id: str) -> List[TextChunk]:
        """
        第二阶段：将父块切为子块（Small-to-Big 策略）
        - 子块大小和 overlap 由配置控制
        - 以句号或逗号结尾
        - 子块 metadata 记录 parent_chunk_id 和 parent_content
        - 只对子块向量化，检索时搜到子块，返回父块内容给大模型
        """
        parent_chunks = self._merge_tiny_parent_chunks(parent_chunks, self.CHILD_SIZE_MIN)
        SmartChunker._annotate_parent_overlap_metadata(parent_chunks, self.parent_overlap)
        child_chunks = []
        child_idx = 0

        for parent in parent_chunks:
            parent_content = parent.content
            parent_id = parent.chunk_id

            is_atomic_parent = bool(parent.metadata.get("atomic_chunk")) or parent.metadata.get("type") in ("table", "image", "figure")

            # 普通正文只有不超过子块上限时才不切；表格/图片/OCR 作为原子块保留完整。
            if is_atomic_parent or len(parent_content) <= self.CHILD_SIZE_MAX:
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
                        "parent_chunk_size": self.parent_size,
                        "parent_chunk_overlap": self.parent_overlap,
                        "child_chunk_size": self.child_size,
                        "child_chunk_overlap": self.child_overlap,
                    },
                    parent_chunk_id=parent_id,
                )
                if self._append_child_chunk(child_chunks, child, self.CHILD_SIZE_MIN):
                    child_idx += 1
                continue

            # 将父块切分为子块，在句子边界处截断
            pos = 0
            while pos < len(parent_content):
                target_end = pos + self.child_size

                if target_end >= len(parent_content):
                    chunk_text = parent_content[pos:].strip()
                    if chunk_text:
                        overlap_len = min(self.child_overlap, pos) if pos > 0 else 0
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
                                "parent_chunk_size": self.parent_size,
                                "parent_chunk_overlap": self.parent_overlap,
                                "child_chunk_size": self.child_size,
                                "child_chunk_overlap": self.child_overlap,
                            },
                            parent_chunk_id=parent_id,
                        )
                        if self._append_child_chunk(child_chunks, child, self.CHILD_SIZE_MIN):
                            child_idx += 1
                    break

                # 在句号/逗号处截断
                end_pos = self._find_sentence_boundary(parent_content, target_end)
                if end_pos <= pos:
                    end_pos = target_end

                chunk_text = parent_content[pos:end_pos].strip()
                if chunk_text:
                    overlap_len = min(self.child_overlap, pos) if pos > 0 else 0
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
                            "parent_chunk_size": self.parent_size,
                            "parent_chunk_overlap": self.parent_overlap,
                            "child_chunk_size": self.child_size,
                            "child_chunk_overlap": self.child_overlap,
                        },
                        parent_chunk_id=parent_id,
                    )
                    if self._append_child_chunk(child_chunks, child, self.CHILD_SIZE_MIN):
                        child_idx += 1

                # 前进到下一个子块起始位置（考虑 overlap）
                next_pos = SmartChunker._choose_overlap_start(parent_content, pos, end_pos, self.child_overlap)
                pos = next_pos
                continue
                if next_pos <= pos:
                    next_pos = end_pos
                # 在 overlap 区域找句子边界
                if self.child_overlap > 0 and next_pos < end_pos:
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

        return child_chunks

    def _merge_tiny_parent_chunks(self, parent_chunks: List[TextChunk], min_size: int) -> List[TextChunk]:
        merged: List[TextChunk] = []
        pending: Optional[TextChunk] = None

        def can_merge(a: TextChunk, b: TextChunk) -> bool:
            a_atomic = bool(a.metadata.get("atomic_chunk")) or a.metadata.get("type") in ("table", "image", "figure")
            b_atomic = bool(b.metadata.get("atomic_chunk")) or b.metadata.get("type") in ("table", "image", "figure")
            if a_atomic or b_atomic:
                return False
            a_profile = a.metadata.get("layout_profile") or {}
            b_profile = b.metadata.get("layout_profile") or {}
            if a_profile.get("has_multi_column") or b_profile.get("has_multi_column"):
                return a.metadata.get("layout_column", 0) == b.metadata.get("layout_column", 0)
            return True

        for chunk in parent_chunks:
            content = (chunk.content or "").strip()
            if not content:
                continue
            is_atomic = bool(chunk.metadata.get("atomic_chunk")) or chunk.metadata.get("type") in ("table", "image", "figure")

            if pending:
                if not can_merge(pending, chunk):
                    merged.append(pending)
                else:
                    chunk.content = f"{pending.content.rstrip()}\n{content}".strip()
                    chunk.metadata["content_length"] = len(chunk.content)
                    chunk.metadata["merged_tiny_parent"] = True
                pending = None

            if is_atomic:
                chunk.content = content
                chunk.metadata["content_length"] = len(content)
                merged.append(chunk)
                continue

            if len(chunk.content) < min_size:
                if merged:
                    prev = merged[-1]
                    if not can_merge(prev, chunk):
                        pending = chunk
                    else:
                        prev.content = f"{prev.content.rstrip()}\n{chunk.content.strip()}".strip()
                        prev.metadata["content_length"] = len(prev.content)
                        prev.metadata["merged_tiny_parent"] = True
                else:
                    pending = chunk
                continue

            merged.append(chunk)

        if pending:
            merged.append(pending)
        return merged

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
        """在目标位置附近寻找自然边界，优先保证分块以标点结束。"""
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


class LayoutDocumentProcessor:
    def __init__(self):
        self.chunk_size = settings.CHUNK_SIZE
        self.chunk_overlap = settings.CHUNK_OVERLAP
        self._layout_analyzer = layout_analyzer
        self._table_extractor = table_extractor
        self._image_extractor = image_extractor
        self._layout_chunker = LayoutAwareChunker(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        self._paddle_ocr_instance = None

    def _get_paddle_ocr(self):
        """获取或初始化 PaddleOCR 实例（单例缓存），用于图片内容OCR识别"""
        if not settings.local_ocr_enabled:
            return None
        if self._paddle_ocr_instance is not None:
            return self._paddle_ocr_instance
        try:
            from paddleocr import PaddleOCR
            self._paddle_ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                show_log=False,
                det_limit_side_len=1920,
                det_db_thresh=0.3,
            )
            logger.info("LayoutDocumentProcessor: PaddleOCR 引擎初始化成功")
            return self._paddle_ocr_instance
        except ImportError:
            logger.info("PaddleOCR 未安装，图片内容将无法进行OCR文字识别")
            return None
        except Exception as e:
            logger.warning(f"PaddleOCR 初始化失败: {e}")
            return None

    def _ocr_image_content(self, file_content: bytes, filename: str, layout_result: LayoutResult):
        """对PDF中的图片区域进行OCR文字识别，将识别结果补充到布局块中。
        确保图片中的文字内容可被提取和检索。"""
        if not settings.local_ocr_enabled:
            return
        ext = os.path.splitext(filename)[1].lower()
        if ext != '.pdf':
            return

        # 检查是否有需要OCR的图片块
        image_blocks = [b for b in layout_result.blocks
                        if b.block_type in (LayoutBlockType.IMAGE, LayoutBlockType.FIGURE)]
        if not image_blocks:
            return

        # 检查图片块是否已有实际文字内容（非占位符）
        needs_ocr = []
        for block in image_blocks:
            content = block.content.strip()
            # 仅含占位符标记（如 [图片: 第X页] 或 ![...](...) ）的块需要OCR
            is_placeholder = (
                content.startswith('[图片') or
                (content.startswith('![') and not any(c.isalpha() or '\u4e00' <= c <= '\u9fff' for c in content.split(']')[0][2:]))
            )
            if is_placeholder:
                needs_ocr.append(block)

        if not needs_ocr:
            return

        # 初始化OCR引擎
        paddle_ocr = self._get_paddle_ocr()
        has_tesseract = False
        if paddle_ocr is None:
            try:
                import pytesseract
                has_tesseract = True
            except ImportError:
                logger.warning("PaddleOCR 和 Tesseract 均不可用，跳过图片OCR识别")
                return

        # 打开PDF并逐页OCR图片区域
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        try:
            import fitz
            doc = fitz.open(tmp_path)

            for block in needs_ocr:
                page_idx = block.page - 1 if block.page > 0 else 0
                if page_idx >= len(doc):
                    continue

                page = doc[page_idx]
                ocr_text = ""

                # 如果有bbox，裁剪图片区域进行OCR
                if block.bbox:
                    x1, y1, x2, y2 = block.bbox
                    # bbox可能是像素坐标（YOLO）或PDF点坐标，需要根据页面尺寸判断并转换
                    page_width = page.rect.width
                    page_height = page.rect.height
                    # 如果坐标值大于页面尺寸，说明是像素坐标，需要转换
                    if x2 > page_width or y2 > page_height:
                        # 获取渲染DPI来计算缩放比
                        zoom = 300 / 72  # 默认DPI=300, PDF默认72点/英寸
                        x1_pdf = x1 / zoom
                        y1_pdf = y1 / zoom
                        x2_pdf = x2 / zoom
                        y2_pdf = y2 / zoom
                        rect = fitz.Rect(x1_pdf, y1_pdf, x2_pdf, y2_pdf)
                    else:
                        rect = fitz.Rect(x1, y1, x2, y2)
                    # 确保rect在页面范围内
                    rect = rect & page.rect
                    pix = page.get_pixmap(dpi=300, clip=rect)
                else:
                    # 整页OCR
                    pix = page.get_pixmap(dpi=300)

                img_data = pix.tobytes("png")

                # 优先使用PaddleOCR
                if paddle_ocr is not None:
                    try:
                        import numpy as np
                        from PIL import Image
                        img = Image.open(io.BytesIO(img_data))
                        img_array = np.array(img)
                        result = paddle_ocr.ocr(img_array, cls=True)
                        if result and result[0]:
                            lines = []
                            for line in result[0]:
                                if line and len(line) >= 2 and isinstance(line[1], (list, tuple)):
                                    lines.append(str(line[1][0]))
                            ocr_text = "\n".join(lines)
                    except Exception as e:
                        logger.warning(f"图片OCR识别失败(PaddleOCR, page={block.page}): {e}")

                # 降级Tesseract
                if not ocr_text.strip() and has_tesseract:
                    try:
                        import pytesseract
                        from PIL import Image
                        img = Image.open(io.BytesIO(img_data))
                        ocr_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                    except Exception as e:
                        logger.warning(f"图片OCR识别失败(Tesseract, page={block.page}): {e}")

                # 将OCR结果更新到布局块
                if ocr_text.strip():
                    original_content = block.content
                    block.content = f"{original_content}\n[OCR识别内容]\n{ocr_text.strip()}"
                    block.metadata["ocr_extracted"] = True
                    block.metadata["ocr_text"] = ocr_text.strip()
                    logger.info(f"图片OCR识别成功: 第{block.page}页, 识别出{len(ocr_text.strip())}字符")

            doc.close()
        except ImportError:
            logger.info("fitz 不可用，跳过PDF图片OCR识别")
        except Exception as e:
            logger.warning(f"PDF图片OCR处理失败: {e}")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def process_upload(self, file_content: bytes, filename: str,
                       chunk_strategy: str = "layout_aware",
                       chunk_size: int = 0,
                       chunk_overlap: int = 0,
                       kb_id: str = "") -> Document:
        from app.doc_processor.converter import file_converter

        result = file_converter.convert(file_content, filename)
        if not result.success:
            raise ValueError(f"文件处理失败: {result.error}")
        cleaned_markdown = TextCleaner.clean_markdown(result.markdown_content)

        doc_id = str(uuid.uuid4())
        effective_size = chunk_size or self.chunk_size
        effective_overlap = chunk_overlap or self.chunk_overlap
        effective_strategy = chunk_strategy or "layout_aware"
        detected_doc_type = SmartChunker.detect_strategy(cleaned_markdown, filename) if effective_strategy == "auto" else effective_strategy

        layout_result = self._layout_analyzer.analyze_content(file_content, filename)
        for block in layout_result.blocks:
            block.content = TextCleaner.clean_markdown(block.content)

        ext = os.path.splitext(filename)[1].lower()
        extracted_tables = []
        extracted_images = []

        if ext == '.pdf':
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(file_content)
                tmp_path = tmp.name
            try:
                extracted_tables = self._table_extractor.extract_from_pdf(tmp_path)
                extracted_images = self._image_extractor.extract_from_pdf(tmp_path)
            finally:
                os.unlink(tmp_path)
        elif ext in ('.docx', '.doc'):
            extracted_tables = self._table_extractor.extract_from_docx(file_content)
            extracted_images = self._image_extractor.extract_from_docx(file_content)
        elif ext in ('.xlsx', '.xls'):
            extracted_tables = self._table_extractor.extract_from_xlsx(file_content)
        elif ext == '.csv':
            extracted_tables = self._table_extractor.extract_from_csv(file_content)
        elif ext in ('.html', '.htm'):
            extracted_tables = self._table_extractor.extract_from_html(file_content)
            extracted_images = self._image_extractor.extract_from_html(file_content)

        for table_info in extracted_tables:
            if table_info.get("markdown"):
                found = False
                for block in layout_result.blocks:
                    if block.block_type == LayoutBlockType.TABLE and block.content.strip() == table_info["markdown"].strip():
                        found = True
                        break
                if not found:
                    layout_result.blocks.append(LayoutBlock(
                        block_type=LayoutBlockType.TABLE,
                        content=table_info["markdown"],
                        page=table_info.get("page", 1),
                        metadata={
                            "rows": table_info.get("rows", 0),
                            "cols": table_info.get("cols", 0),
                            "has_header": table_info.get("has_header", True),
                        },
                    ))

        if extracted_images:
            for img_info in extracted_images:
                if img_info.get("path"):
                    img_md = f"![{img_info.get('caption', '图片')}]({img_info['path']})"
                    layout_result.blocks.append(LayoutBlock(
                        block_type=LayoutBlockType.IMAGE,
                        content=img_md,
                        page=img_info.get("page", 1),
                        metadata={
                            "image_path": img_info["path"],
                            "width": img_info.get("width", 0),
                            "height": img_info.get("height", 0),
                            "format": img_info.get("format", ""),
                        },
                    ))

        layout_result.blocks.sort(key=lambda b: (b.page, -b.level if b.block_type.value.startswith("heading") else 0))

        # 对PDF中的图片区域进行OCR文字识别，确保图片内容可被提取
        self._ocr_image_content(file_content, filename, layout_result)

        chunker = LayoutAwareChunker(
            chunk_size=effective_size,
            chunk_overlap=effective_overlap,
        )
        chunks = chunker.chunk_from_layout(layout_result, doc_id, filename=filename, kb_id=kb_id)

        for chunk in chunks:
            if "title" not in chunk.metadata:
                chunk.metadata["title"] = Path(filename).stem
            if "kb_id" not in chunk.metadata:
                chunk.metadata["kb_id"] = kb_id
            chunk.metadata["chunk_strategy"] = "layout_aware"
            chunk.metadata["detected_doc_type"] = detected_doc_type
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
            metadata={"conversion": result.metadata or {}, "layout_aware": True},
        )

        # 分块质量检查
        ChunkQualityChecker.check_chunks(
            chunks,
            parent_size=effective_size,
            child_size=settings.CHILD_CHUNK_SIZE,
            filename=filename,
        )

        logger.info(
            f"版面感知文档处理完成: {filename}, "
            f"版面块={len(layout_result.blocks)}, "
            f"表格={len(extracted_tables)}, "
            f"图片={len(extracted_images)}, "
            f"分块={len(chunks)}"
        )
        return doc

    def process_file(self, file_path: str,
                     chunk_strategy: str = "layout_aware",
                     chunk_size: int = 0,
                     chunk_overlap: int = 0,
                     kb_id: str = "") -> Document:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            content = f.read()
        return self.process_upload(
            content, filename,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            kb_id=kb_id,
        )


layout_doc_processor = LayoutDocumentProcessor()
