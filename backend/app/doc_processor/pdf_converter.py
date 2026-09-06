import os
import re
import io
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict
# O8: 导入并行处理模块
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class PDFHeadingDetector:
    _CN_NUM_MAP = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
    }

    _H1_PATTERNS = [
        re.compile(r'^[一二三四五六七八九十]+[、．.]\s*[\u4e00-\u9fff]'),
        re.compile(r'^第[一二三四五六七八九十\d]+[章节篇部]\s'),
        re.compile(r'^第[一二三四五六七八九十\d]+[章节篇部]$'),
    ]

    _H2_PATTERNS = [
        re.compile(r'^[（(]\s*[一二三四五六七八九十\d]+\s*[）)]\s*[\u4e00-\u9fff]'),
        re.compile(r'^\d+[、．.]\s*[\u4e00-\u9fff]'),
    ]

    _H3_PATTERNS = [
        re.compile(r'^\d+\.\d+\s*[\u4e00-\u9fffA-Za-z]'),
        re.compile(r'^\d+\.\d+\.\d+\s*[\u4e00-\u9fffA-Za-z]'),
        re.compile(r'^\d+\.\d+\.\d+\.\d+\s*[\u4e00-\u9fffA-Za-z]'),
    ]

    _ACADEMIC_LABELS = [
        (re.compile(r'^摘\s*要[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^Abstract[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^目\s*录[\s：:]*', re.IGNORECASE), 1),
        (re.compile(r'^Contents[\s：:]*', re.IGNORECASE), 1),
        (re.compile(r'^前\s*言[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^引\s*言[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^Introduction[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^结\s*论[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^Conclusion[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^参\s*考\s*文\s*献[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^References[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^附\s*录[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^Appendix[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^致\s*谢[\s：:]*', re.IGNORECASE), 2),
        (re.compile(r'^Acknowledgement[\s：:]*', re.IGNORECASE), 2),
    ]

    @staticmethod
    def detect_heading_level(text: str, font_size: float = 0, is_bold: bool = False) -> int:
        stripped = text.strip()
        if not stripped:
            return 0

        for pattern in PDFHeadingDetector._H1_PATTERNS:
            if pattern.match(stripped):
                return 1

        for pattern in PDFHeadingDetector._H2_PATTERNS:
            if pattern.match(stripped):
                return 2

        for pattern in PDFHeadingDetector._H3_PATTERNS:
            if pattern.match(stripped):
                m = re.match(r'^(\d+\.\d+(?:\.\d+)?)', stripped)
                if m:
                    depth = m.group(1).count('.') + 1
                    return min(depth + 1, 5)
                return 3

        for pattern, level in PDFHeadingDetector._ACADEMIC_LABELS:
            if pattern.match(stripped):
                return level

        if font_size > 0:
            if font_size >= 18 and is_bold:
                return 1
            if font_size >= 15 and is_bold:
                return 2
            if font_size >= 13 and is_bold:
                return 3
            if is_bold and len(stripped) <= 40 and not stripped.endswith(('。', '，', '！', '？', '；', '.', ',', '!', '?', ';')):
                return 3

        if is_bold and len(stripped) <= 30 and not stripped.endswith(('。', '，', '！', '？', '；', '.', ',', '!', '?', ';')):
            if re.match(r'^[\u4e00-\u9fffA-Za-z]', stripped):
                return 3

        return 0

    @staticmethod
    def format_heading(text: str, level: int) -> str:
        if level <= 0 or level > 6:
            return text
        prefix = '#' * level
        stripped = text.strip()
        stripped = re.sub(r'^#+\s*', '', stripped)
        return f'{prefix} {stripped}'


class PDFRedundancyFilter:
    _PAGE_NUMBER_PATTERNS = [
        re.compile(r'^\s*\.{2,}\s*\d+\s*$'),
        re.compile(r'^\s*\d+\s*[、.．]\s*\d+\s*[、.．]\s*\d+'),
        re.compile(r'^\s*……\s*\d+'),
        re.compile(r'^\s*\d+\s*$'),
    ]

    _HEADER_FOOTER_PATTERNS = [
        re.compile(r'\d{8}\s+\d{2}:\d{2}:\d{2}\s*\|\|'),
        re.compile(r'No\.\s*\d{4,}'),
        re.compile(r'\d{4}\s*年.*(?:蓝皮书|白皮书|绿皮书|红皮书|报告|年鉴)'),
        re.compile(r'(?:蓝皮书|白皮书|绿皮书|红皮书|报告|年鉴)\s*\(\s*\d{4}\s*年\s*\)'),
    ]

    _ISOLATED_NUMBER_RE = re.compile(r'^#\s*\d[\d.]*\s*万?亿?[万亿]?\s*元?$')

    @staticmethod
    def is_redundant_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False

        for pattern in PDFRedundancyFilter._PAGE_NUMBER_PATTERNS:
            if pattern.match(stripped):
                return True

        for pattern in PDFRedundancyFilter._HEADER_FOOTER_PATTERNS:
            if pattern.search(stripped):
                return True

        if PDFRedundancyFilter._ISOLATED_NUMBER_RE.match(stripped):
            return True

        if re.match(r'^#{1,6}\s*\d+\s*$', stripped):
            return True

        if re.match(r'^\s*第\s*\d+\s*页', stripped):
            return True

        if re.match(r'^\s*-\s*\d+\s*-\s*$', stripped):
            return True

        return False

    @staticmethod
    def filter_text(text: str) -> str:
        lines = text.split('\n')
        filtered = []
        for line in lines:
            if PDFRedundancyFilter.is_redundant_line(line):
                continue
            filtered.append(line)
        return '\n'.join(filtered)


class PDFTableExtractor:
    @staticmethod
    def extract_tables_from_page(page) -> List[Dict[str, Any]]:
        tables_data = []
        try:
            tables = page.find_tables()
            for table in tables:
                table_md = PDFTableExtractor._convert_table_to_markdown(table)
                if table_md:
                    bbox = table.bbox if hasattr(table, 'bbox') else None
                    tables_data.append({
                        'markdown': table_md,
                        'bbox': bbox,
                        'row_count': len(table.rows) if hasattr(table, 'rows') else 0,
                    })
        except Exception as e:
            logger.debug(f"表格提取失败: {e}")
        return tables_data

    @staticmethod
    def _convert_table_to_markdown(table) -> Optional[str]:
        try:
            rows = table.extract()
            if not rows or len(rows) < 2:
                return None

            cleaned_rows = []
            for row in rows:
                cleaned_cells = []
                for cell in row:
                    cell_text = (cell or '').strip()
                    cell_text = re.sub(r'\n', ' ', cell_text)
                    cell_text = re.sub(r'\s+', ' ', cell_text)
                    cleaned_cells.append(cell_text)
                if any(c for c in cleaned_cells):
                    cleaned_rows.append(cleaned_cells)

            if len(cleaned_rows) < 2:
                return None

            max_cols = max(len(row) for row in cleaned_rows)
            for row in cleaned_rows:
                while len(row) < max_cols:
                    row.append('')

            md_lines = []
            header = cleaned_rows[0]
            md_lines.append('| ' + ' | '.join(header) + ' |')
            md_lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')

            for row in cleaned_rows[1:]:
                md_lines.append('| ' + ' | '.join(row) + ' |')

            return '\n'.join(md_lines)
        except Exception as e:
            logger.debug(f"表格转换Markdown失败: {e}")
            return None

    @staticmethod
    def get_table_bboxes(page) -> List[Tuple[float, float, float, float]]:
        bboxes = []
        try:
            tables = page.find_tables()
            for table in tables:
                if hasattr(table, 'bbox'):
                    bboxes.append(table.bbox)
        except Exception:
            pass
        return bboxes


class PDFImageExtractor:
    @staticmethod
    def extract_images_from_page(page, page_num: int, output_dir: str, doc_name: str) -> List[Dict[str, Any]]:
        images_info = []
        try:
            images = page.get_images(full=True)
            for img_idx, img in enumerate(images):
                xref = img[0]
                try:
                    base_image = page.parent.extract_image(xref)
                    if not base_image:
                        continue
                    image_bytes = base_image.get("image")
                    image_ext = base_image.get("ext", "png")
                    if not image_bytes or len(image_bytes) < 100:
                        continue

                    img_filename = f"{doc_name}_p{page_num + 1}_img{img_idx + 1}.{image_ext}"
                    img_path = os.path.join(output_dir, img_filename)
                    with open(img_path, "wb") as f:
                        f.write(image_bytes)

                    images_info.append({
                        'filename': img_filename,
                        'path': img_path,
                        'page': page_num + 1,
                        'index': img_idx + 1,
                        'size': len(image_bytes),
                    })
                except Exception as e:
                    logger.debug(f"提取图片{xref}失败: {e}")
        except Exception as e:
            logger.debug(f"页面{page_num}图片提取失败: {e}")
        return images_info

    @staticmethod
    def find_image_captions(text_blocks: List[Dict], images_info: List[Dict]) -> Dict[int, str]:
        captions = {}
        caption_re = re.compile(r'^图\s*(\d+)\s*[：:．.]\s*(.+)$')
        for block in text_blocks:
            text = block.get('text', '').strip()
            m = caption_re.match(text)
            if m:
                img_num = int(m.group(1))
                caption_text = m.group(2).strip()
                captions[img_num] = caption_text
        return captions

    @staticmethod
    def format_image_reference(img_info: Dict, caption: str = '') -> str:
        img_num = img_info.get('index', 1)
        alt_text = f"图{img_num}"
        if caption:
            alt_text += f" {caption}"
        filename = img_info.get('filename', '')
        return f'![{alt_text}](images/{filename})'


class PDFColumnCorrector:
    @staticmethod
    def detect_and_reorder_columns(page, words: list) -> str:
        if not words:
            return ""

        columns = PDFColumnCorrector._detect_column_boundary(page, words)
        if not columns:
            return ""

        boundary = columns[1]
        page_height = page.height

        line_groups = PDFColumnCorrector._group_words_to_lines(words)

        segments = PDFColumnCorrector._split_into_segments(line_groups, boundary, page_height)

        ordered_text_parts = []
        for seg in segments:
            if seg.get('full'):
                ordered_text_parts.append('\n'.join(seg['full']))
            if seg.get('left'):
                ordered_text_parts.append('\n'.join(seg['left']))
            if seg.get('right'):
                ordered_text_parts.append('\n'.join(seg['right']))

        return '\n\n'.join(ordered_text_parts)

    @staticmethod
    def _detect_column_boundary(page, words: list) -> List[float]:
        page_width = page.width
        if page_width < 100:
            return []

        all_x0 = [w["x0"] for w in words if w.get("text", "").strip()]
        all_x1 = [w["x1"] for w in words if w.get("text", "").strip()]
        if not all_x0:
            return []

        min_x = min(all_x0)
        max_x = max(all_x1)
        content_width = max_x - min_x
        if content_width < 100:
            return []

        num_bins = 40
        bin_width = content_width / num_bins
        bins = [0] * num_bins
        for w in words:
            if not w.get("text", "").strip():
                continue
            word_mid = (w["x0"] + w["x1"]) / 2
            bin_idx = int((word_mid - min_x) / bin_width)
            if 0 <= bin_idx < num_bins:
                bins[bin_idx] += 1

        max_bin = max(bins) if bins else 0
        if max_bin == 0:
            return []

        threshold = max_bin * 0.12
        gap_bins = [i for i, count in enumerate(bins) if count < threshold]
        if not gap_bins:
            return []

        groups = []
        current_group = [gap_bins[0]]
        for i in range(1, len(gap_bins)):
            if gap_bins[i] - gap_bins[i - 1] <= 2:
                current_group.append(gap_bins[i])
            else:
                groups.append(current_group)
                current_group = [gap_bins[i]]
        groups.append(current_group)

        center_bin = num_bins // 2
        best_group = None
        best_dist = num_bins
        for group in groups:
            if len(group) < 2:
                continue
            group_mid = sum(group) / len(group)
            dist = abs(group_mid - center_bin)
            if dist < best_dist:
                best_dist = dist
                best_group = group

        if best_group is None or len(best_group) < 2:
            return []

        gap_start_bin = best_group[0]
        gap_end_bin = best_group[-1]
        boundary = min_x + (gap_start_bin + gap_end_bin + 1) / 2 * bin_width

        left_words = [w for w in words if w["x1"] < boundary]
        right_words = [w for w in words if w["x0"] >= boundary]
        if len(left_words) < 5 or len(right_words) < 5:
            return []

        return [min_x, boundary, max_x]

    @staticmethod
    def _group_words_to_lines(words: list) -> List[Dict]:
        if not words:
            return []
        sorted_words = sorted(words, key=lambda w: (round(w["top"], 0), w["x0"]))
        lines = []
        current_line_words = [sorted_words[0]]
        current_top = round(sorted_words[0]["top"], 0)
        for word in sorted_words[1:]:
            word_top = round(word["top"], 0)
            if abs(word_top - current_top) <= 3:
                current_line_words.append(word)
            else:
                line_text = PDFColumnCorrector._merge_line_words(current_line_words)
                line_x0 = min(w["x0"] for w in current_line_words)
                line_x1 = max(w["x1"] for w in current_line_words)
                lines.append({
                    'text': line_text,
                    'top': current_top,
                    'x0': line_x0,
                    'x1': line_x1,
                })
                current_line_words = [word]
                current_top = word_top
        if current_line_words:
            line_text = PDFColumnCorrector._merge_line_words(current_line_words)
            line_x0 = min(w["x0"] for w in current_line_words)
            line_x1 = max(w["x1"] for w in current_line_words)
            lines.append({
                'text': line_text,
                'top': current_top,
                'x0': line_x0,
                'x1': line_x1,
            })
        return lines

    @staticmethod
    def _merge_line_words(words: list) -> str:
        if not words:
            return ""
        sorted_words = sorted(words, key=lambda w: w["x0"])
        result = sorted_words[0].get("text", "")
        for i in range(1, len(sorted_words)):
            prev = sorted_words[i - 1]
            curr = sorted_words[i]
            gap = curr["x0"] - prev["x1"]
            prev_text = prev.get("text", "")
            curr_text = curr.get("text", "")
            prev_is_cjk = bool(re.search(r"[\u4e00-\u9fff]", prev_text))
            curr_is_cjk = bool(re.search(r"[\u4e00-\u9fff]", curr_text))
            if prev_is_cjk and curr_is_cjk and gap < 15:
                result += curr_text
            elif gap > 20:
                result += " " + curr_text
            else:
                result += curr_text
        return result

    @staticmethod
    def _split_into_segments(lines: List[Dict], boundary: float, page_height: float) -> List[Dict]:
        if not lines:
            return []

        sorted_lines = sorted(lines, key=lambda l: l['top'])
        segment_gap_threshold = page_height * 0.08

        segments = []
        current_segment = {'left': [], 'right': [], 'full': [], 'top_start': sorted_lines[0]['top']}

        for line in sorted_lines:
            line_mid = (line['x0'] + line['x1']) / 2
            line_width = line['x1'] - line['x0']

            is_left = line['x1'] < boundary + 5
            is_right = line['x0'] > boundary - 5
            is_full = line_width > (boundary - line.get('x0', 0)) * 0.8 if line.get('x0', 0) < boundary else line_width > 100

            if is_left and not is_right:
                col = 'left'
            elif is_right and not is_left:
                col = 'right'
            else:
                col = 'full'

            gap = line['top'] - current_segment.get('top_end', current_segment['top_start'])
            if gap > segment_gap_threshold and (current_segment['left'] or current_segment['right'] or current_segment['full']):
                segments.append(current_segment)
                current_segment = {'left': [], 'right': [], 'full': [], 'top_start': line['top']}

            current_segment[col].append(line['text'])
            current_segment['top_end'] = line['top']

        if current_segment['left'] or current_segment['right'] or current_segment['full']:
            segments.append(current_segment)

        return segments


class PDFLineMerger:
    _SENTENCE_ENDINGS = ('。', '！', '？', '；', '：', '.', '!', '?', ';', ':', '…', '—', '】', '）', ')', ']', '`', '"', "'", '》')
    _HEADING_PREFIXES = re.compile(r'^#{1,6}\s')
    _LIST_PREFIXES = re.compile(r'^[\-\*\+]\s|^\d+\.\s')
    _CN_NUM_HEADING = re.compile(r'^[一二三四五六七八九十]+[、．.]\s*[\u4e00-\u9fff]')
    _PAREN_NUM_HEADING = re.compile(r'^[（(]\s*[一二三四五六七八九十\d]+\s*[）)]\s*[\u4e00-\u9fff]')
    _NUM_HEADING = re.compile(r'^\d+(?:\.\d+)*[、．.]\s*[\u4e00-\u9fff]')
    _ACADEMIC_LABEL = re.compile(r'^(?:摘要|关键词|关键字|Abstract|Key\s*words|目录|前言|引言|结论|参考文献|附录|致谢)[\s：:]', re.IGNORECASE)

    @staticmethod
    def merge_broken_lines(text: str) -> str:
        lines = text.split('\n')
        merged = []
        i = 0

        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()

            if not stripped:
                merged.append('')
                i += 1
                continue

            if PDFLineMerger._is_unmergeable(stripped):
                merged.append(line)
                i += 1
                continue

            current_text = stripped

            while i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                if not next_line:
                    break

                if PDFLineMerger._is_unmergeable(next_line):
                    break

                if not PDFLineMerger._should_merge(current_text, next_line):
                    break

                joiner = PDFLineMerger._get_joiner(current_text, next_line)
                current_text = current_text + joiner + next_line
                i += 1

            merged.append(current_text)
            i += 1

        return '\n'.join(merged)

    @staticmethod
    def _is_unmergeable(stripped: str) -> bool:
        if PDFLineMerger._HEADING_PREFIXES.match(stripped):
            return True
        if PDFLineMerger._CN_NUM_HEADING.match(stripped):
            return True
        if PDFLineMerger._PAREN_NUM_HEADING.match(stripped):
            return True
        if PDFLineMerger._NUM_HEADING.match(stripped):
            return True
        if PDFLineMerger._ACADEMIC_LABEL.match(stripped):
            return True
        if PDFLineMerger._LIST_PREFIXES.match(stripped):
            return True
        if stripped.startswith('```'):
            return True
        if stripped.startswith('|') and '|' in stripped[1:]:
            return True
        if stripped.startswith('> '):
            return True
        if re.match(r'^!\[', stripped):
            return True
        return False

    @staticmethod
    def _should_merge(current: str, next_line: str) -> bool:
        if not current or not next_line:
            return False

        if current.endswith(('—', '…', '-')):
            return True

        if next_line.startswith(("#", "第", "|", "-", "*", ">", "```", "![", "表")):
            return False
        if PDFLineMerger._NUM_HEADING.match(next_line):
            return False
        if PDFLineMerger._CN_NUM_HEADING.match(next_line):
            return False
        if PDFLineMerger._PAREN_NUM_HEADING.match(next_line):
            return False
        if PDFLineMerger._ACADEMIC_LABEL.match(next_line):
            return False

        if current.endswith(PDFLineMerger._SENTENCE_ENDINGS):
            if re.search(r'^[\u4e00-\u9fff]', next_line) and len(next_line) > 15:
                return False
            if re.search(r'^[A-Z]', next_line) and len(next_line) > 20:
                return False

        current_last_cjk = re.search(r'[\u4e00-\u9fff\d]$', current)
        next_first_cjk = re.search(r'^[\u4e00-\u9fff]', next_line)

        if current_last_cjk and next_first_cjk:
            return True

        if re.search(r'[，、；：）】』」]$', current) and re.search(r'^[\u4e00-\u9fff\d]', next_line):
            return True

        if current_last_cjk and re.search(r'^\d', next_line):
            return True

        if re.search(r'[A-Za-z\d]$', current) and re.search(r'^[A-Za-z\d]', next_line):
            return True

        if re.search(r'[。！？；：.!?;:]$', current) and next_first_cjk and len(next_line) <= 15:
            return True

        return False

    @staticmethod
    def _get_joiner(current: str, next_line: str) -> str:
        if not current:
            return ''
        last_char = current[-1]
        first_char = next_line[0] if next_line else ''

        if re.match(r'[\u4e00-\u9fff]', last_char) and re.match(r'[\u4e00-\u9fff]', first_char):
            return ''
        if last_char in ('，', '、', '。', '！', '？', '；', '：'):
            if re.match(r'[\u4e00-\u9fff]', first_char):
                return ''
            return ' '
        if re.match(r'[A-Za-z0-9]', last_char) and re.match(r'[A-Za-z0-9]', first_char):
            if PDFLineMerger._is_broken_word(current, next_line):
                return ''
            return ' '
        return ''

    @staticmethod
    def _is_broken_word(current: str, next_line: str) -> bool:
        if not current or not next_line:
            return False
        last_char = current[-1]
        first_char = next_line[0]
        if not (last_char.isalpha() and first_char.isalpha()):
            return False

        tail_match = re.search(r'([a-zA-Z]{2,})$', current)
        head_match = re.search(r'^([a-zA-Z]{2,})', next_line)
        if not tail_match or not head_match:
            return False

        tail = tail_match.group(1).lower()
        head = head_match.group(1).lower()

        if len(tail) <= 2 and len(head) <= 2:
            return False

        common_words = {
            'autonomous', 'intelligence', 'artificial', 'comprehensive', 'understanding',
            'significant', 'environment', 'particularly', 'construction', 'application',
            'development', 'implementation', 'representation', 'classification',
            'optimization', 'experiment', 'performance', 'evaluation', 'technology',
            'information', 'knowledge', 'extraction', 'generation', 'processing',
            'architecture', 'framework', 'methodology', 'contribution', 'investigation',
            'perspective', 'collaboration', 'communication', 'demonstration',
            'documentation', 'establishment', 'functional', 'historical', 'individual',
            'professional', 'traditional', 'transform', 'university', 'published',
            'springer', 'journal', 'article', 'review', 'abstract', 'keywords',
            'introduction', 'conclusion', 'references', 'acknowledgement', 'appendix',
            'flexible', 'explainable', 'previous', 'isolated', 'environments',
            'diverges', 'significantly', 'replicating', 'unconstrained', 'acquisition',
            'alongside', 'substantial', 'parameters', 'employing', 'controllers',
            'facilitates', 'addition', 'interfaces', 'promising', 'overview',
            'comprehensively', 'intuitively', 'capabilities', 'challenges',
            'directions', 'strategies', 'commonly', 'diverse', 'applications',
            'encompasses', 'proposing', 'holistic', 'studies', 'delivering',
            'systematic', 'discuss', 'construction', 'present', 'several',
            'based', 'recently', 'leading', 'surge', 'research', 'survey',
            'these', 'those', 'which', 'where', 'their', 'there', 'about',
            'human', 'level', 'decision', 'making', 'process', 'memory',
            'planning', 'learning', 'model', 'models', 'agent', 'agents',
            'language', 'large', 'world', 'internal', 'knowledge', 'domain',
            'specific', 'data', 'training', 'natural', 'interact', 'humans',
            'provide', 'obtain', 'achieve', 'usually', 'focus', 'limited',
            'within', 'because', 'gaps', 'highly', 'complex', 'mind',
            'often', 'far', 'from', 'like', 'make', 'them', 'behave',
            'benefit', 'inspire', 'future', 'field', 'work', 'much',
            'also', 'first', 'then', 'finally', 'since', 'however',
            'therefore', 'moreover', 'furthermore', 'addition',
            'autonomous', 'nomous', 'intellig', 'gence', 'arti', 'ficial',
            'comp', 'rehensive', 'pre', 'vious', 'signi', 'ficant',
            'flex', 'ible', 'expla', 'nable', 'iso', 'lated',
            'envi', 'ronment', 'diver', 'ges', 'repli', 'cating',
            'uncon', 'strained', 'acqui', 'sition', 'along', 'side',
            'subst', 'antial', 'param', 'eters', 'employ', 'ing',
            'control', 'lers', 'facili', 'tates', 'addi', 'tion',
            'inter', 'faces', 'promi', 'sing', 'over', 'view',
            'compre', 'hensively', 'intui', 'tively', 'capa', 'bilities',
            'chal', 'lenges', 'direc', 'tions', 'strate', 'gies',
            'encom', 'passes', 'propo', 'sing', 'holi', 'stic',
            'deli', 'vering', 'syste', 'matic', 'const', 'ruction',
        }

        merged = tail + head
        if merged in common_words:
            return True

        for word in common_words:
            if len(word) >= 6 and word.startswith(merged):
                return True
            if len(word) >= 6 and word.endswith(merged):
                return True

        if len(tail) >= 3 and len(head) >= 3:
            for word in common_words:
                if word.startswith(tail) and len(word) > len(tail) + 1:
                    remaining = word[len(tail):]
                    if remaining.startswith(head) or head.startswith(remaining[:len(head)]):
                        return True

        return False


class PDFTextFormatter:
    _TECH_TERMS = {
        'DeepSeek': 'DeepSeek',
        'deepseek': 'DeepSeek',
        'Deep Seek': 'DeepSeek',
        'deep seek': 'DeepSeek',
        'AIIaaS': 'AI IaaS',
        'AIiaaS': 'AI IaaS',
        'AIPaaS': 'AI PaaS',
        'AIpaas': 'AI PaaS',
        'MaaS': 'MaaS',
        'maas': 'MaaS',
        'AISaaS': 'AI SaaS',
        'AIsaas': 'AI SaaS',
        'AIMSP': 'AI MSP',
        'Aimsp': 'AI MSP',
        'IaaS': 'IaaS',
        'PaaS': 'PaaS',
        'SaaS': 'SaaS',
    }

    @staticmethod
    def normalize_text(text: str) -> str:
        text = PDFTextFormatter._fix_hyphenated_words(text)
        text = PDFTextFormatter._fix_broken_english_words(text)
        text = PDFTextFormatter._normalize_tech_terms(text)
        text = PDFTextFormatter._fix_cjk_english_spacing(text)
        text = PDFTextFormatter._normalize_punctuation(text)
        text = PDFTextFormatter._fix_broken_sentences(text)
        return text

    @staticmethod
    def _fix_hyphenated_words(text: str) -> str:
        text = re.sub(r'([a-zA-Z])-\s*\n\s*([a-zA-Z])', r'\1\2', text)
        text = re.sub(r'([a-zA-Z])-\s+([a-zA-Z])', r'\1\2', text)
        return text

    @staticmethod
    def _fix_broken_english_words(text: str) -> str:
        lines = text.split('\n')
        result = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue
            if re.match(r'^#{1,6}\s', stripped):
                result.append(line)
                continue
            if stripped.startswith('|'):
                result.append(line)
                continue

            prev = None
            while prev != stripped:
                prev = stripped
                stripped = re.sub(
                    r'\b([a-zA-Z]{2,})\s+([a-zA-Z]{2,})\b',
                    lambda m: PDFTextFormatter._try_merge_two_words(m),
                    stripped
                )
            result.append(stripped)

        return '\n'.join(result)

    @staticmethod
    def _try_merge_two_words(m) -> str:
        word1 = m.group(1)
        word2 = m.group(2)
        merged = word1 + word2
        merged_lower = merged.lower()

        common_words = {
            'autonomous', 'intelligence', 'artificial', 'comprehensive', 'understanding',
            'significant', 'environment', 'particularly', 'construction', 'application',
            'development', 'implementation', 'representation', 'classification',
            'optimization', 'experiment', 'performance', 'evaluation', 'technology',
            'information', 'knowledge', 'extraction', 'generation', 'processing',
            'architecture', 'framework', 'methodology', 'contribution', 'investigation',
            'perspective', 'collaboration', 'communication', 'demonstration',
            'documentation', 'establishment', 'functional', 'historical', 'individual',
            'professional', 'traditional', 'transform', 'university', 'published',
            'springer', 'journal', 'article', 'review', 'abstract', 'keywords',
            'introduction', 'conclusion', 'references', 'acknowledgement', 'appendix',
            'flexible', 'explainable', 'previous', 'isolated', 'environments',
            'diverges', 'significantly', 'replicating', 'unconstrained', 'acquisition',
            'alongside', 'substantial', 'parameters', 'employing', 'controllers',
            'facilitates', 'addition', 'interfaces', 'promising', 'overview',
            'comprehensively', 'intuitively', 'capabilities', 'challenges',
            'directions', 'strategies', 'commonly', 'diverse', 'applications',
            'encompasses', 'proposing', 'holistic', 'studies', 'delivering',
            'systematic', 'discuss', 'construction', 'present', 'several',
            'based', 'recently', 'leading', 'surge', 'research', 'survey',
            'these', 'those', 'which', 'where', 'their', 'there', 'about',
            'human', 'level', 'decision', 'making', 'process', 'memory',
            'planning', 'learning', 'model', 'models', 'agent', 'agents',
            'language', 'large', 'world', 'internal', 'knowledge', 'domain',
            'specific', 'data', 'training', 'natural', 'interact', 'humans',
            'provide', 'obtain', 'achieve', 'usually', 'focus', 'limited',
            'within', 'because', 'gaps', 'highly', 'complex', 'mind',
            'often', 'far', 'from', 'like', 'make', 'them', 'behave',
            'benefit', 'inspire', 'future', 'field', 'work', 'much',
            'also', 'first', 'then', 'finally', 'since', 'however',
            'therefore', 'moreover', 'furthermore', 'addition',
            'humancapabilities', 'informed', 'constrained', 'replicating',
            'flexible', 'explainable', 'previous', 'isolated', 'environments',
            'diverges', 'significantly', 'unconstrained', 'acquisition',
            'alongside', 'substantial', 'parameters', 'comprehensively',
            'intuitively', 'capabilities', 'challenges', 'directions',
            'strategies', 'encompasses', 'proposing', 'delivering',
            'systematic', 'construction', 'published', 'springer',
            'acknowledgement', 'representation', 'classification',
            'optimization', 'experiment', 'performance', 'evaluation',
            'architecture', 'framework', 'methodology', 'contribution',
            'investigation', 'perspective', 'collaboration', 'communication',
            'demonstration', 'documentation', 'establishment', 'functional',
            'historical', 'individual', 'professional', 'traditional',
            'transform', 'university', 'implementation', 'extraction',
            'generation', 'processing', 'technology', 'information',
            'understanding', 'significant', 'particularly', 'application',
            'development', 'intelligence', 'artificial', 'comprehensive',
            'autonomous', 'environment', 'construction',
        }

        if merged_lower in common_words:
            return merged

        for word in common_words:
            if len(word) >= 8 and word.startswith(merged_lower):
                return merged

        return m.group(0)

    @staticmethod
    def _normalize_tech_terms(text: str) -> str:
        for wrong, correct in PDFTextFormatter._TECH_TERMS.items():
            text = re.sub(re.escape(wrong), correct, text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _fix_cjk_english_spacing(text: str) -> str:
        text = re.sub(r'([\u4e00-\u9fff])([A-Za-z])', r'\1 \2', text)
        text = re.sub(r'([A-Za-z])([\u4e00-\u9fff])', r'\1 \2', text)
        text = re.sub(r'([\u4e00-\u9fff])(\d)', r'\1 \2', text)
        text = re.sub(r'(\d)([\u4e00-\u9fff])', r'\1 \2', text)
        text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
        return text

    @staticmethod
    def _normalize_punctuation(text: str) -> str:
        text = re.sub(r'→', '→', text)
        text = re.sub(r'·{2,}', '……', text)
        text = re.sub(r'\.{4,}', '……', text)
        text = re.sub(r'-{3,}', '——', text)
        text = re.sub(r'([\u4e00-\u9fff]),(?=[\u4e00-\u9fff])', r'\1，', text)
        text = re.sub(r'([\u4e00-\u9fff]);(?=[\u4e00-\u9fff])', r'\1；', text)
        text = re.sub(r'([\u4e00-\u9fff]):(?=[\u4e00-\u9fff])', r'\1：', text)
        text = re.sub(r'([\u4e00-\u9fff])\((?=[\u4e00-\u9fff])', r'\1（', text)
        text = re.sub(r'([\u4e00-\u9fff])\)(?=[\u4e00-\u9fff])', r'\1）', text)
        return text

    @staticmethod
    def _fix_broken_sentences(text: str) -> str:
        text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue
            if re.match(r'^#{1,6}\s', stripped):
                result.append(line)
                continue
            if stripped.startswith('|'):
                result.append(line)
                continue
            if stripped.startswith('```'):
                result.append(line)
                continue
            if stripped.startswith('!['):
                result.append(line)
                continue
            prev = None
            while prev != stripped:
                prev = stripped
                stripped = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', stripped)
            stripped = re.sub(r'([\u4e00-\u9fff])\s+([，。！？；：、）】」』])', r'\1\2', stripped)
            stripped = re.sub(r'([（【「『])\s+([\u4e00-\u9fff])', r'\1\2', stripped)
            stripped = re.sub(r'([\u4e00-\u9fff])\s+(\d)', r'\1\2', stripped)
            stripped = re.sub(r'(\d)\s+([\u4e00-\u9fff])', r'\1\2', stripped)
            result.append(stripped)
        return '\n'.join(result)


class AdvancedPDFConverter:
    def __init__(self):
        self._has_pdfplumber = self._try_import("pdfplumber")
        self._has_pymupdf = self._try_import("fitz")

    def _try_import(self, module_name: str) -> bool:
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False

    @staticmethod
    def _cid_ratio(text: str) -> float:
        """计算文本中 CID 乱码的占比。"""
        import re
        cid_matches = re.findall(r'\(cid:\d+\)', text)
        if not cid_matches:
            return 0.0
        cid_len = len(''.join(cid_matches))
        return cid_len / max(len(text), 1)

    def convert(self, content: bytes, filename: str, output_dir: str = None) -> Dict[str, Any]:
        # PyMuPDF 优先：对 CID 编码和中文 PDF 处理更好
        if self._has_pymupdf:
            result = self._convert_with_pymupdf(content, filename, output_dir)
            if result.get('success') and len(result.get('markdown_content', '').strip()) > 100:
                md = result.get('markdown_content', '')
                cid_ratio = self._cid_ratio(md)
                if cid_ratio < 0.1:
                    return result
                logger.warning(f"PyMuPDF 结果 CID 乱码占比 {cid_ratio:.1%}，尝试 pdfplumber")

        if self._has_pdfplumber:
            result = self._convert_with_pdfplumber(content, filename, output_dir)
            if result.get('success') and len(result.get('markdown_content', '').strip()) > 100:
                return result

        return {
            'success': False,
            'error': '无PDF库可用',
            'markdown_content': '',
        }

    def _convert_with_pdfplumber(self, content: bytes, filename: str, output_dir: str = None) -> Dict[str, Any]:
        """使用 pdfplumber 转换 PDF，O8 优化：ThreadPoolExecutor 并行处理页面。"""
        import pdfplumber

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            title = Path(filename).stem
            if output_dir is None:
                output_dir = tempfile.mkdtemp(prefix="pdf_images_")

            images_dir = os.path.join(output_dir, "images")
            os.makedirs(images_dir, exist_ok=True)

            all_page_texts = []
            all_images = []
            total_chars = 0

            with pdfplumber.open(tmp_path) as pdf:
                page_count = len(pdf.pages)

                # O8: 并行处理页面
                max_workers = min(4, os.cpu_count() or 1)
                if page_count <= 3:
                    # 少量页面直接串行处理
                    for page_num, page in enumerate(pdf.pages):
                        page_result = self._process_page_pdfplumber(
                            page, page_num, page_count, images_dir, title
                        )
                        all_page_texts.append(page_result['text'])
                        all_images.extend(page_result.get('images', []))
                        total_chars += page_result.get('char_count', 0)
                else:
                    # 多页面并行处理
                    page_results = [None] * page_count
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_idx = {}
                        for page_num, page in enumerate(pdf.pages):
                            future = executor.submit(
                                self._process_page_pdfplumber,
                                page, page_num, page_count, images_dir, title
                            )
                            future_to_idx[future] = page_num

                        for future in as_completed(future_to_idx):
                            idx = future_to_idx[future]
                            try:
                                page_results[idx] = future.result()
                            except Exception as e:
                                logger.warning(f"页面 {idx + 1} 并行处理失败: {e}")
                                page_results[idx] = {'text': '', 'images': [], 'char_count': 0}

                    for pr in page_results:
                        if pr:
                            all_page_texts.append(pr['text'])
                            all_images.extend(pr.get('images', []))
                            total_chars += pr.get('char_count', 0)

                md_content = '\n\n'.join(t for t in all_page_texts if t.strip())

            md_content = self._post_process(md_content, title)

            return {
                'success': True,
                'markdown_content': md_content,
                'metadata': {
                    'source_format': 'pdf',
                    'page_count': page_count,
                    'engine': 'pdfplumber_advanced',
                    'total_chars': total_chars,
                    'images_count': len(all_images),
                },
            }
        except Exception as e:
            logger.error(f"高级PDF转换失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'markdown_content': '',
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _process_page_pdfplumber(self, page, page_num: int, page_count: int, images_dir: str, doc_name: str) -> Dict[str, Any]:
        result_text = ''
        images = []

        try:
            tables_data = PDFTableExtractor.extract_tables_from_page(page)
            table_bboxes = [t['bbox'] for t in tables_data if t.get('bbox')]

            words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)

            if words:
                columns = PDFColumnCorrector._detect_column_boundary(page, words)

                if columns and len(columns) >= 2:
                    page_text = PDFColumnCorrector.detect_and_reorder_columns(page, words)
                else:
                    line_groups = PDFColumnCorrector._group_words_to_lines(words)
                    page_text = '\n'.join(lg['text'] for lg in line_groups)

                lines = page_text.split('\n')
                filtered_lines = []
                for line in lines:
                    if not PDFRedundancyFilter.is_redundant_line(line):
                        filtered_lines.append(line)
                page_text = '\n'.join(filtered_lines)

                page_text = self._insert_tables_into_text(page_text, tables_data, page)
            else:
                page_text = page.extract_text() or ''

            result_text = page_text
        except Exception as e:
            logger.debug(f"页面{page_num + 1}处理失败: {e}")
            result_text = page.extract_text() or ''

        return {
            'text': result_text,
            'images': images,
            'char_count': len(result_text),
        }

    def _insert_tables_into_text(self, page_text: str, tables_data: List[Dict], page) -> str:
        if not tables_data:
            return page_text

        caption_patterns = [
            re.compile(r'表\s*(\d+)\s*[：:．.]\s*(.+?)(?:\n|$)'),
            re.compile(r'表\s*(\d+)\s+(.+?)(?:\n|$)'),
        ]

        lines = page_text.split('\n')
        result_lines = []
        inserted_tables = set()

        for line in lines:
            result_lines.append(line)

            for caption_pat in caption_patterns:
                m = caption_pat.match(line.strip())
                if m:
                    table_num = int(m.group(1))
                    if table_num not in inserted_tables and table_num <= len(tables_data):
                        table_md = tables_data[table_num - 1].get('markdown', '')
                        if table_md:
                            result_lines.append('')
                            result_lines.append(table_md)
                            result_lines.append('')
                            inserted_tables.add(table_num)

        for idx, table_info in enumerate(tables_data):
            table_num = idx + 1
            if table_num not in inserted_tables:
                table_md = table_info.get('markdown', '')
                if table_md:
                    result_lines.append('')
                    result_lines.append(table_md)
                    result_lines.append('')

        return '\n'.join(result_lines)

    def _convert_with_pymupdf(self, content: bytes, filename: str, output_dir: str = None) -> Dict[str, Any]:
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            doc = fitz.open(tmp_path)
            title = Path(filename).stem
            if output_dir is None:
                output_dir = tempfile.mkdtemp(prefix="pdf_images_")

            images_dir = os.path.join(output_dir, "images")
            os.makedirs(images_dir, exist_ok=True)

            all_page_texts = []
            all_images = []
            total_chars = 0

            for page_num in range(len(doc)):
                page = doc[page_num]

                images_info = PDFImageExtractor.extract_images_from_page(
                    page, page_num, images_dir, title
                )
                all_images.extend(images_info)

                text = page.get_text("text")
                if text.strip():
                    lines = text.split('\n')
                    filtered_lines = [l for l in lines if not PDFRedundancyFilter.is_redundant_line(l)]
                    text = '\n'.join(filtered_lines)
                    total_chars += len(text)
                    all_page_texts.append(text)

                for img_info in images_info:
                    img_ref = PDFImageExtractor.format_image_reference(img_info)
                    all_page_texts.append(img_ref)

            md_content = '\n\n'.join(t for t in all_page_texts if t.strip())
            md_content = self._post_process(md_content, title)

            return {
                'success': True,
                'markdown_content': md_content,
                'metadata': {
                    'source_format': 'pdf',
                    'page_count': len(doc),
                    'engine': 'pymupdf_advanced',
                    'total_chars': total_chars,
                    'images_count': len(all_images),
                },
            }
        except Exception as e:
            logger.error(f"PyMuPDF高级转换失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'markdown_content': '',
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _post_process(self, text: str, title: str) -> str:
        text = PDFLineMerger.merge_broken_lines(text)
        text = PDFRedundancyFilter.filter_text(text)
        text = self._apply_heading_detection(text)
        text = PDFTextFormatter.normalize_text(text)
        text = self._remove_page_markers(text)
        text = self._add_document_title(text, title)
        text = self._ensure_heading_spacing(text)
        text = self._clean_cjk_internal_spaces(text)
        text = self._fix_cross_column_contamination(text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _clean_cjk_internal_spaces(self, text: str) -> str:
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue
            if re.match(r'^#{1,6}\s', stripped):
                result.append(line)
                continue
            if stripped.startswith('|'):
                result.append(line)
                continue
            if stripped.startswith('```'):
                result.append(line)
                continue
            if stripped.startswith('!['):
                result.append(line)
                continue
            prev = None
            while prev != stripped:
                prev = stripped
                stripped = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', stripped)
                stripped = re.sub(r'([，、；：])\s+(\d)', r'\1\2', stripped)
            result.append(stripped)
        return '\n'.join(result)

    def _apply_heading_detection(self, text: str) -> str:
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue

            if re.match(r'^#{1,6}\s', stripped):
                result.append(line)
                continue

            level = PDFHeadingDetector.detect_heading_level(stripped)
            if level > 0:
                result.append(PDFHeadingDetector.format_heading(stripped, level))
            else:
                result.append(line)
        return '\n'.join(result)

    def _remove_page_markers(self, text: str) -> str:
        text = re.sub(r'^#{1,6}\s*第\s*\d+\s*页\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*第\s*\d+\s*页\s*$', '', text, flags=re.MULTILINE)
        return text

    def _add_document_title(self, text: str, title: str) -> str:
        if not text.strip().startswith('#'):
            return f'# {title}\n\n{text}'
        first_line = text.strip().split('\n')[0]
        if not re.match(r'^#\s', first_line):
            return f'# {title}\n\n{text}'
        return text

    def _ensure_heading_spacing(self, text: str) -> str:
        lines = text.split('\n')
        result = []
        for i, line in enumerate(lines):
            if re.match(r'^#{1,6}\s', line.strip()):
                if i > 0 and result and result[-1].strip() != '':
                    result.append('')
                result.append(line)
                if i + 1 < len(lines) and lines[i + 1].strip() != '':
                    result.append('')
            else:
                if result and result[-1] == '' and line.strip() == '':
                    continue
                result.append(line)
        return '\n'.join(result)

    def _fix_cross_column_contamination(self, text: str) -> str:
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue
            if re.match(r'^#{1,6}\s', stripped):
                result.append(line)
                continue
            if stripped.startswith('|'):
                result.append(line)
                continue
            if stripped.startswith('```'):
                result.append(line)
                continue
            if stripped.startswith('!['):
                result.append(line)
                continue

            fixed = self._fix_joined_sentences(stripped)
            result.append(fixed)
        return '\n'.join(result)

    @staticmethod
    def _fix_joined_sentences(text: str) -> str:
        sentence_endings = ('。', '！', '？', '；', '.', '!', '?', ';')
        sentence_start_indicators = [
            re.compile(r'[\u4e00-\u9fff]{2,}'),
        ]

        parts = []
        current = ''
        i = 0
        while i < len(text):
            ch = text[i]
            current += ch
            if ch in sentence_endings and i + 1 < len(text):
                next_ch = text[i + 1]
                if re.match(r'[\u4e00-\u9fff]', next_ch):
                    rest = text[i + 1:]
                    min_rest_len = 8
                    if len(rest) >= min_rest_len:
                        has_ending_in_rest = False
                        for j, rc in enumerate(rest):
                            if rc in sentence_endings and j > min_rest_len:
                                has_ending_in_rest = True
                                break
                            if rc in ('，', '、', '；', '：', ',', '、'):
                                if j > 3:
                                    has_ending_in_rest = True
                                    break
                        if has_ending_in_rest:
                            parts.append(current)
                            current = ''
            i += 1

        if current:
            parts.append(current)

        if len(parts) <= 1:
            return text

        return '\n'.join(parts)


advanced_pdf_converter = AdvancedPDFConverter()
