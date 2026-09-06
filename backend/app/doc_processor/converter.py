import os
import re
import io
import csv
import json
import logging
import tempfile
import hashlib
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


def _has_severe_cid(text: str) -> bool:
    """检测文本中是否存在严重的 CID 编码乱码。"""
    import re
    cid_matches = re.findall(r'\(cid:\d+\)', text)
    if not cid_matches:
        return False
    cid_len = len(''.join(cid_matches))
    ratio = cid_len / max(len(text), 1)
    return ratio > 0.1  # CID 占比超过 10% 视为严重乱码


SUPPORTED_FORMATS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xml": "application/xml",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".epub": "application/epub+zip",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# .doc 是旧版二进制格式，python-docx 不支持，需要 LibreOffice 转换
LEGACY_FORMATS = {".doc", ".xls", ".ppt"}


class ConversionResult:
    def __init__(
        self,
        success: bool,
        original_name: str,
        markdown_content: str = "",
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.success = success
        self.original_name = original_name
        self.markdown_content = markdown_content
        self.error = error
        self.metadata = metadata or {}
        self.content_hash = hashlib.sha256(markdown_content.encode("utf-8")).hexdigest() if markdown_content else ""


class TextCleaner:
    _TYPO_MAP = {
        "即产生": "既产生",
        "导理文献": "梳理文献",
        "导理": "梳理",
        "即然": "既然",
        "即使": "即使",
        "做为": "作为",
        "按装": "安装",
        "甘败下风": "甘拜下风",
        "自暴自起": "自暴自弃",
        "一愁莫展": "一筹莫展",
        "穿流不息": "川流不息",
        "一诺千斤": "一诺千金",
        "默守成规": "墨守成规",
        "金壁辉煌": "金碧辉煌",
        "按步就班": "按部就班",
        "记忆尤新": "记忆犹新",
        "既往不究": "既往不咎",
        "草管人命": "草菅人命",
        "人情事故": "人情世故",
        "有持无恐": "有恃无恐",
        "竭泽而鱼": "竭泽而渔",
        "滥芋充数": "滥竽充数",
        "世外桃园": "世外桃源",
        "再接再励": "再接再厉",
    }

    _COMMON_ENGLISH_WORDS = {
        "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
        "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
        "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
        "or", "an", "will", "my", "one", "all", "would", "there", "their",
        "what", "so", "up", "out", "if", "about", "who", "get", "which", "go",
        "me", "when", "make", "can", "like", "time", "no", "just", "him",
        "know", "take", "people", "into", "year", "your", "good", "some",
        "could", "them", "see", "other", "than", "then", "now", "look",
        "only", "come", "its", "over", "think", "also", "back", "after",
        "use", "two", "how", "our", "work", "first", "well", "way", "even",
        "new", "want", "because", "any", "these", "give", "day", "most",
        "us", "is", "are", "was", "were", "been", "being", "has", "had",
        "did", "does", "doing", "shall", "should", "may", "might", "must",
        "can", "could", "will", "would", "need", "dare", "ought", "used",
        "review", "research", "spatial", "evolution", "urban", "development",
        "analysis", "study", "model", "method", "approach", "framework",
        "system", "process", "structure", "function", "result", "data",
        "based", "using", "through", "between", "within", "however",
        "therefore", "moreover", "furthermore", "addition", "conclusion",
        "introduction", "abstract", "summary", "background", "related",
        "proposed", "present", "paper", "article", "journal", "science",
        "network", "graph", "node", "edge", "cluster", "algorithm",
        "learning", "deep", "neural", "training", "feature", "layer",
        "performance", "accuracy", "evaluation", "experiment", "dataset",
        "simulation", "optimization", "prediction", "classification",
        "regression", "generation", "detection", "recognition", "segmentation",
        "representation", "embedding", "attention", "transformer",
        "information", "knowledge", "extraction", "retrieval", "generation",
        "language", "natural", "processing", "understanding", "reasoning",
        "semantic", "syntactic", "lexical", "morphological", "phonological",
        "corpus", "annotation", "tokenization", "normalization",
        "application", "implementation", "comparison", "improvement",
        "significant", "effective", "efficient", "proposed", "existing",
        "previous", "current", "future", "recent", "novel",
        "environmental", "economic", "social", "cultural", "political",
        "technology", "innovation", "sustainability", "policy", "management",
        "global", "regional", "local", "national", "international",
        "population", "growth", "change", "impact", "factor", "effect",
        "relationship", "correlation", "influence", "contribution",
        "figure", "table", "section", "chapter", "appendix", "reference",
        "equation", "formula", "parameter", "variable", "constant",
        "input", "output", "source", "target", "domain", "task",
    }

    _ENGLISH_SUFFIXES = (
        'tion', 'sion', 'ment', 'ness', 'able', 'ible', 'ful', 'less',
        'ous', 'ive', 'ing', 'ed', 'er', 'ly', 'al', 'ity', 'ical',
        'ize', 'ise', 'fy', 'ate', 'ence', 'ance', 'dom', 'ship',
        'ward', 'wise', 'like', 'proof', 'hood', 'some',
    )

    _ENGLISH_PREFIXES = (
        'un', 're', 'pre', 'dis', 'mis', 'over', 'out', 'sub', 'inter',
        'trans', 'anti', 'semi', 'multi', 'auto', 'co', 'counter',
    )

    _NUMERIC_HEADING_RE = re.compile(
        r'^(\d+\.\d+(?:\.\d+)*\s*[A-Za-z]?)(?:\s*[、.．，,]\s*|\s+|(?=[\u4e00-\u9fff]))([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z\s：:——\-、，,.\d²³%。；！？]{1,300})'
    )

    @staticmethod
    def clean_pdf_text(text: str) -> str:
        # 先检测 CID 乱码，如果大面积乱码则标记需要 OCR
        text = TextCleaner._clean_cid_artifacts(text)
        text = TextCleaner._normalize_whitespace(text)
        text = TextCleaner._remove_page_artifacts(text)
        text = TextCleaner._handle_special_formatting(text)
        # 对表格行和非表格行分别处理，避免破坏表格结构
        text = TextCleaner._process_preserving_tables(text)
        text = TextCleaner._fix_punctuation(text)
        text = TextCleaner._fix_typos(text)
        text = TextCleaner._remove_duplicates(text)
        text = TextCleaner._fix_structure(text)
        text = TextCleaner._remove_repeated_blank_lines(text)
        text = TextCleaner.filter_low_quality_paragraphs(text, min_score=0.3)
        return text.strip()

    @staticmethod
    def _process_preserving_tables(text: str) -> str:
        """对表格行和非表格行分别处理，避免破坏 Markdown 表格结构"""
        lines = text.split('\n')
        processed = []
        in_table = False

        for line in lines:
            stripped = line.strip()
            is_table_line = stripped.startswith('|') and '|' in stripped[1:]

            if is_table_line:
                # 表格行：只做最小限度的处理
                # 修复 OCR 标点但不破坏 | 分隔符
                line = re.sub(r'(\d)\s*一\s*(\d)', r'\1—\2', line)
                line = re.sub(r'm\s*[2²]', 'm²', line)
                line = re.sub(r'm\s*[3³]', 'm³', line)
                processed.append(line)
                in_table = True
            else:
                if in_table and not stripped:
                    in_table = False
                # 非表格行：做完整处理
                line = TextCleaner._separate_chinese_english(line)
                line = TextCleaner._fix_chinese_spacing(line)
                processed.append(line)

        text = '\n'.join(processed)
        text = TextCleaner._merge_broken_lines(text)
        return text

    @staticmethod
    def clean_general_text(text: str) -> str:
        text = TextCleaner._normalize_whitespace(text)
        text = TextCleaner._remove_page_artifacts(text)
        text = TextCleaner._handle_special_formatting(text)
        text = TextCleaner._separate_chinese_english(text)
        text = TextCleaner._fix_chinese_spacing(text)
        text = TextCleaner._fix_punctuation(text)
        text = TextCleaner._fix_typos(text)
        text = TextCleaner._remove_duplicates(text)
        text = TextCleaner._fix_structure(text)
        text = TextCleaner._remove_repeated_blank_lines(text)
        return text.strip()

    @staticmethod
    def clean_markdown_document(text: str) -> str:
        """清洗正文，同时完整保护代码围栏和 Markdown 表格。"""
        if not text:
            return ""

        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        result: List[str] = []
        prose: List[str] = []
        protected: List[str] = []
        in_code = False
        in_table = False

        def flush_prose():
            if not prose:
                return
            cleaned = TextCleaner.clean_general_text("\n".join(prose))
            if cleaned:
                result.extend(cleaned.split("\n"))
            prose.clear()

        def flush_protected():
            if not protected:
                return
            result.extend(line.rstrip() for line in protected)
            protected.clear()

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                flush_prose()
                protected.append(line.rstrip())
                in_code = not in_code
                if not in_code:
                    flush_protected()
                continue
            if in_code:
                protected.append(line.rstrip())
                continue

            is_table_line = stripped.startswith("|") and stripped.count("|") >= 2
            if is_table_line:
                flush_prose()
                in_table = True
                protected.append(line.rstrip())
                continue
            if in_table:
                flush_protected()
                in_table = False
            prose.append(line)

        flush_prose()
        flush_protected()
        cleaned = "\n".join(result)
        cleaned = re.sub(r'\n{4,}', '\n\n\n', cleaned)
        return cleaned.strip()

    @staticmethod
    def _handle_special_formatting(text: str) -> str:
        text = TextCleaner._clean_ocr_artifacts(text)
        text = TextCleaner._clean_footnotes(text)
        text = TextCleaner._clean_table_artifacts(text)
        text = TextCleaner._clean_special_chars(text)
        return text

    @staticmethod
    def _clean_cid_artifacts(text: str) -> str:
        """清理 PDF CID 编码乱码，如 (cid:1)(cid:15) 等。

        当 PDF 使用内嵌子集字体且缺少 ToUnicode CMap 时，
        文本提取会产生 (cid:XX) 形式的乱码。
        """
        # 先保护 m² 和 m³，避免被后续清理误删
        text = re.sub(r'm\s*²', 'm²', text)
        text = re.sub(r'm\s*³', 'm³', text)
        text = text.replace('m²', 'M_SQUARED')
        text = text.replace('m³', 'M_CUBED')

        # 统计 CID 乱码占比
        cid_pattern = re.compile(r'\(cid:\d+\)')
        cid_matches = cid_pattern.findall(text)
        total_len = max(len(text), 1)
        cid_ratio = len(''.join(cid_matches)) / total_len

        if cid_ratio > 0.3:
            logger.warning(f"PDF 文本 CID 乱码占比 {cid_ratio:.1%}，文本质量极差，建议使用 OCR 模式")
            text = cid_pattern.sub('', text)
            text = re.sub(r'[¢ß¶•˙˜‹›¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿]', '', text)
        elif cid_matches:
            text = cid_pattern.sub('', text)

        # 清理 CID 旁边的孤立特殊字符
        text = re.sub(r'\s*[¢ß¶•˙˜‹›¤¥¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿]+\s*', ' ', text)

        # 恢复 m² 和 m³
        text = text.replace('M_SQUARED', 'm²')
        text = text.replace('M_CUBED', 'm³')

        return text

    @staticmethod
    def _clean_ocr_artifacts(text: str) -> str:
        text = re.sub(r'[〇○◯]', '0', text)
        text = re.sub(r'[\u2580-\u259F]+', '', text)
        text = re.sub(r'[\u2500-\u257F]+', '', text)
        text = re.sub(r'[\u25A0-\u25FF]+', '', text)
        text = re.sub(r'〔〔〕〕', '', text)
        text = re.sub(r'[﹍﹎﹏﹋﹌]+', '', text)
        text = re.sub(r'\s*[┆┇┊┋╎╏]\s*', '\n', text)
        text = re.sub(r'[═╪╫╬╭╮╯╰]+', '', text)
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append('')
                continue
            # 保护 Markdown 表格分隔符行（如 | --- | --- |）
            if re.match(r'^\|[\s\-:|]+\|$', stripped):
                cleaned.append(line)
                continue
            non_cjk = len(re.sub(r'[\u4e00-\u9fffA-Za-z0-9，。！？；：、\u201c\u201d\u2018\u2019\uff08\uff09\u3010\u3011\u300a\u300b\s]', '', stripped))
            total = max(len(stripped), 1)
            if non_cjk / total > 0.6 and len(stripped) < 30:
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    @staticmethod
    def _clean_footnotes(text: str) -> str:
        text = re.sub(r'[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]', '', text)
        text = re.sub(r'[⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑]', '', text)
        text = re.sub(r'〔\d+〕', '', text)
        text = re.sub(r'［\d+］', '', text)
        text = re.sub(r'\(\d{1,3}\)', '', text)
        text = re.sub(r'^\s*\d{1,3}\s*$', '', text, flags=re.MULTILINE)
        return text

    @staticmethod
    def _clean_table_artifacts(text: str) -> str:
        text = re.sub(r'^\s*[┌┐└┘├┤┬┴┼─│┏┓┗┛┣┫┳┻╋━┃]+\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\+[-+]+\+\s*$', '', text, flags=re.MULTILINE)
        return text

    @staticmethod
    def _clean_special_chars(text: str) -> str:
        text = re.sub(r'\u3000+', ' ', text)
        text = re.sub(r' +', ' ', text)
        text = re.sub(r'[\ufeff\u200b\u200c\u200d\ufeff]', '', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'·{3,}', '……', text)
        text = re.sub(r'\.{4,}', '……', text)
        # 保护 Markdown 表格分隔符行，避免 --- 被转为中文破折号
        table_sep_marker = 'TABLESEPARATORLINE'
        text = re.sub(r'^\|[\s\-:|]+$', lambda m: m.group().replace('---', table_sep_marker), text, flags=re.MULTILINE)
        text = re.sub(r'-{3,}', '——', text)
        text = text.replace(table_sep_marker, '---')
        return text

    @staticmethod
    def _separate_chinese_english(text: str) -> str:
        text = re.sub(r'([\u4e00-\u9fff])([A-Za-z])', r'\1 \2', text)
        text = re.sub(r'([A-Za-z])([\u4e00-\u9fff])', r'\1 \2', text)
        text = re.sub(r'([\u4e00-\u9fff])(\d)', r'\1 \2', text)
        text = re.sub(r'(\d)([\u4e00-\u9fff])', r'\1 \2', text)
        text = re.sub(r'([A-Za-z])(\d)', r'\1 \2', text)
        text = re.sub(r'(\d)([A-Za-z])', r'\1 \2', text)
        return text

    @staticmethod
    def _fix_broken_english(text: str) -> str:
        def _try_split_long_word(word):
            if len(word) < 6:
                return word
            lower = word.lower()

            for known_word in sorted(TextCleaner._COMMON_ENGLISH_WORDS, key=len, reverse=True):
                if len(known_word) < 3:
                    continue
                idx = lower.find(known_word)
                if idx >= 0:
                    parts = []
                    if idx > 0:
                        prefix = word[:idx]
                        if prefix.lower() in TextCleaner._COMMON_ENGLISH_WORDS or len(prefix) >= 2:
                            parts.append(prefix)
                        else:
                            continue
                    parts.append(word[idx:idx + len(known_word)])
                    suffix_start = idx + len(known_word)
                    if suffix_start < len(word):
                        suffix = word[suffix_start:]
                        if suffix.lower() in TextCleaner._COMMON_ENGLISH_WORDS or any(suffix.endswith(s) for s in TextCleaner._ENGLISH_SUFFIXES):
                            parts.append(suffix)
                        elif len(suffix) >= 2:
                            parts.append(suffix)
                        else:
                            parts[-1] += suffix
                    if len(parts) > 1:
                        return ' '.join(parts)

            for prefix in sorted(TextCleaner._ENGLISH_PREFIXES, key=len, reverse=True):
                if lower.startswith(prefix) and len(word) > len(prefix) + 2:
                    rest = word[len(prefix):]
                    if rest.lower() in TextCleaner._COMMON_ENGLISH_WORDS or any(rest.endswith(s) for s in TextCleaner._ENGLISH_SUFFIXES):
                        return f'{prefix} {rest}'

            for suffix in sorted(TextCleaner._ENGLISH_SUFFIXES, key=len, reverse=True):
                if lower.endswith(suffix) and len(word) > len(suffix) + 2:
                    stem = word[:len(word) - len(suffix)]
                    if stem.lower() in TextCleaner._COMMON_ENGLISH_WORDS or len(stem) >= 3:
                        return f'{stem} {suffix}'

            return word

        def _fix_word_match(m):
            word = m.group(0)
            return _try_split_long_word(word)

        text = re.sub(r'[A-Za-z]{8,}', _fix_word_match, text)
        return text

    @staticmethod
    def _detect_and_mark_truncation(text: str) -> str:
        def _check_line(line):
            stripped = line.rstrip()
            if not stripped:
                return line
            if stripped.endswith(('。', '！', '？', '；', '：', '.', '!', '?', ';', ':', '…', '—')):
                return line
            if re.match(r'^#{1,6}\s', stripped):
                return line
            if re.match(r'^[\d一二三四五六七八九十]+[、.．]\s', stripped):
                return line
            if stripped.endswith(('|', '-', '*', '>')):
                return line
            english_tail = re.search(r'[A-Za-z]{2,}$', stripped)
            if english_tail:
                tail_word = english_tail.group(0).lower()
                if tail_word not in TextCleaner._COMMON_ENGLISH_WORDS and len(tail_word) >= 4:
                    if not any(tail_word.endswith(s) for s in ['tion', 'ment', 'ness', 'able', 'ible', 'ful', 'less', 'ous', 'ive', 'ing', 'ed', 'er', 'ly', 'al']):
                        return stripped + ' [截断]'
            if stripped.endswith(('的', '了', '在', '是', '和', '与', '或', '及', '等', '为', '被')):
                return line
            chinese_tail = re.search(r'[\u4e00-\u9fff]$', stripped)
            if chinese_tail:
                prev_char = stripped[-1]
                if prev_char in ('的', '了', '在', '是', '和', '与', '或', '及', '等', '为', '被', '将', '把', '从', '向', '对'):
                    return line
            return line

        lines = text.split('\n')
        result = []
        for line in lines:
            if line.strip() and len(line.strip()) > 20:
                result.append(_check_line(line))
            else:
                result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _fix_typos(text: str) -> str:
        for wrong, correct in TextCleaner._TYPO_MAP.items():
            text = text.replace(wrong, correct)
        return text

    @staticmethod
    def _remove_duplicates(text: str) -> str:
        lines = text.split('\n')
        seen = {}
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.append('')
                continue
            normalized = re.sub(r'\s+', '', stripped.lower())
            if normalized in seen:
                if len(stripped) > len(seen[normalized]):
                    idx = result.index(seen[normalized]) if seen[normalized] in result else -1
                    if idx != -1:
                        result[idx] = line
                continue
            seen[normalized] = line
            result.append(line)

        def _is_heading(line: str) -> bool:
            s = line.strip()
            if re.match(r'^#{1,6}\s', s):
                return True
            if TextCleaner._NUMERIC_HEADING_RE.match(s):
                return True
            if re.match(r'^(?:一|二|三|四|五|六|七|八|九|十)+[、.．\s]', s):
                return True
            if re.match(r'^[\d一二三四五六七八九十]+[、.．]\s', s):
                return True
            if re.match(r'^(?:摘要|关键词|关键字|Abstract|Key\s*words)(?:[\s：:]|(?=[\u4e00-\u9fffA-Za-z]))', s, re.IGNORECASE):
                return True
            return False

        paragraphs = []
        current_para = []
        for line in result:
            stripped = line.strip()
            if not stripped:
                if current_para:
                    para_text = '\n'.join(current_para)
                    para_normalized = re.sub(r'\s+', '', para_text.lower())
                    is_dup = False
                    for existing in paragraphs:
                        existing_normalized = re.sub(r'\s+', '', existing.lower())
                        if para_normalized == existing_normalized:
                            is_dup = True
                            break
                        overlap = 0
                        min_len = min(len(para_normalized), len(existing_normalized))
                        if min_len > 20:
                            for i in range(0, min_len - 10, 5):
                                chunk = para_normalized[i:i + 10]
                                if chunk in existing_normalized:
                                    overlap += 10
                            if overlap > min_len * 0.7:
                                is_dup = True
                                break
                    if not is_dup:
                        paragraphs.append(para_text)
                    current_para = []
                else:
                    if paragraphs and paragraphs[-1] != '':
                        paragraphs.append('')
            elif _is_heading(line):
                if current_para:
                    para_text = '\n'.join(current_para)
                    para_normalized = re.sub(r'\s+', '', para_text.lower())
                    is_dup = False
                    for existing in paragraphs:
                        existing_normalized = re.sub(r'\s+', '', existing.lower())
                        if para_normalized == existing_normalized:
                            is_dup = True
                            break
                        overlap = 0
                        min_len = min(len(para_normalized), len(existing_normalized))
                        if min_len > 20:
                            for i in range(0, min_len - 10, 5):
                                chunk = para_normalized[i:i + 10]
                                if chunk in existing_normalized:
                                    overlap += 10
                            if overlap > min_len * 0.7:
                                is_dup = True
                                break
                    if not is_dup:
                        paragraphs.append(para_text)
                    current_para = []
                current_para.append(line)
            else:
                current_para.append(line)

        if current_para:
            para_text = '\n'.join(current_para)
            paragraphs.append(para_text)

        return '\n\n'.join(paragraphs)

    @staticmethod
    def _fix_structure(text: str) -> str:
        lines = text.split('\n')
        processed = []
        for line in lines:
            stripped = line.strip()
            if re.match(r'^\*\*关键词\*\*', stripped) or re.match(r'^\*\*Keywords\*\*', stripped, re.IGNORECASE):
                processed.append(line)
            elif re.match(r'^(?:关键词|关键字)[\s：:]', stripped) or re.match(r'^(?:Abstract|Key\s*words)[\s：:]', stripped, re.IGNORECASE):
                processed.append(line)
            elif stripped.startswith('```'):
                processed.append(line)
            elif stripped.startswith('|'):
                processed.append(line)
            elif re.match(r'^#{1,6}\s', stripped):
                processed.append(line)
            else:
                line = re.sub(r'([。！？；])\s*(第[一二三四五六七八九十\d]+[章节编])', r'\1\n\n\2', line)
                line = re.sub(r'([。！？；])\s*(\d+[、.．]\s*[\u4e00-\u9fff])', r'\1\n\n\2', line)
                line = re.sub(r'([\u4e00-\u9fff])\s*([，、])\s*([\u4e00-\u9fff])', r'\1\2\3', line)
                line = re.sub(r'([\u4e00-\u9fff])\s*([。！？；：])', r'\1\2', line)
                line = re.sub(r'([，。！？；：、])\s*([，。！？；：、])', r'\1', line)
                processed.append(line)
        text = '\n'.join(processed)

        text = TextCleaner._convert_numeric_headings(text)
        # 先将条款编号转为列表项，再处理中文数字标题
        # 这样中文数字条款编号（如"一、xxx"）会被转为列表项而非标题
        text = TextCleaner._convert_list_items(text)
        # _convert_chinese_numeric_headings 仅处理未被列表项转换的中文数字行
        text = TextCleaner._convert_chinese_numeric_headings(text)
        text = TextCleaner._convert_academic_labels(text)

        text = TextCleaner._normalize_heading_levels(text)
        text = TextCleaner._ensure_heading_spacing(text)

        # 全局面积上下文修复：OCR文本块分割导致面积关键词和数字不在同一块，
        # 逐块修复会遗漏，此处对完整文本再做一次修复
        text = TextCleaner._fix_area_units(text)

        # 全局清理孤立页码行（如单独一行的 "3"、"·4·"）
        text = TextCleaner._clean_page_numbers(text)

        return text

    @staticmethod
    def _convert_list_items(text: str) -> str:
        """将条款编号（纯数字或中文数字开头）转为有序列表项，而非标题。
        规范文档中的条款编号如'1 xxx'、'二、xxx'应为列表项，
        而条文号如'5.1.1 xxx'已由 _convert_numeric_headings 处理为标题。"""
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # 跳过已有标记的行，但如果标题行包含冒号+分号分隔的列表项，需要拆分
            if stripped.startswith('#') or stripped.startswith('|') or stripped.startswith('- ') or stripped.startswith('* '):
                # 标题行中包含冒号+分号分隔的列表项（如"### 5.5.13 ...封闭楼梯间： 医疗建筑...； 设置歌舞...； 商店..."）
                if stripped.startswith('#') and '；' in stripped and re.search(r'[：:]\s*[\u4e00-\u9fff]', stripped):
                    # 找到冒号位置
                    colon_match = re.search(r'[：:]', stripped)
                    if colon_match:
                        colon_pos = colon_match.end()
                        prefix = stripped[:colon_pos]
                        items_text = stripped[colon_pos:].strip()
                        parts = [p.strip() for p in re.split(r'[；;]', items_text) if p.strip()]
                        if len(parts) >= 2:
                            result.append(prefix)
                            for idx, part in enumerate(parts, 1):
                                result.append(f'{idx}. {part}')
                            continue
                result.append(line)
                continue
            # 拆分行内多个条款编号（如 "1设置在一... 2设置在三级..."）
            # 匹配 "数字+中文" 模式，在行内用分号/空格分隔的位置拆分
            parts = re.split(r'([；;]\s*)(\d{1,2}\s*[\u4e00-\u9fff])', stripped)
            if len(parts) > 1:
                # 重新组装：将分隔符附加到前一部分，新条款编号开始新行
                rebuilt_lines = []
                current = parts[0]
                for i in range(1, len(parts), 3):
                    sep = parts[i]  # 分号
                    next_num = parts[i + 1]  # 下一个条款编号的开头
                    rest = parts[i + 2] if i + 2 < len(parts) else ''
                    rebuilt_lines.append(current + sep.rstrip())
                    current = next_num + rest
                rebuilt_lines.append(current)
                # 递归处理每个拆分后的行
                sub_result = []
                for sub_line in rebuilt_lines:
                    sub_stripped = sub_line.strip()
                    m = re.match(r'^(\d{1,2})\s*([\u4e00-\u9fff].+)$', sub_stripped)
                    if m and not re.match(r'^\d+\.\d+', sub_stripped) and int(m.group(1)) <= 20:
                        sub_result.append(f'{m.group(1)}. {m.group(2)}')
                    else:
                        sub_result.append(sub_line)
                result.extend(sub_result)
                continue
            # 纯数字 + 可选空格 + 中文（如 "1设置在一" 或 "2 除医疗建筑"）→ 有序列表
            # 但排除条文号（如 "5.1.1"），条文号包含点号
            # 限制数字范围 1-20，避免大数字（如页码46）被误识别
            m = re.match(r'^(\d{1,2})\s*([\u4e00-\u9fff].+)$', stripped)
            if m and not re.match(r'^\d+\.\d+', stripped) and int(m.group(1)) <= 20:
                result.append(f'{m.group(1)}. {m.group(2)}')
                continue
            # 修复 OCR 粘连：列表编号+数字+中文（如 "46层及以上" → "4. 6层及以上"）
            # 检测模式：行首数字(1-9)紧跟数字+中文单位词（层/m/h等）+ 可选后续内容
            m = re.match(r'^([1-9])(\d+\s*(?:层|m²|m³|m\b|h\b|lx|人|座|个|间|部|条|种|类|项|款|级|等).*)$', stripped)
            if m and not re.match(r'^\d+\.\d+', stripped):
                result.append(f'{m.group(1)}. {m.group(2)}')
                continue
            # 中文数字 + 顿号 + 内容（如 "一、xxx"）→ 有序列表
            m = re.match(r'^((?:一|二|三|四|五|六|七|八|九|十)+)[、.．]\s*([\u4e00-\u9fff].+)$', stripped)
            if m:
                cn_num_map = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
                              '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
                num = cn_num_map.get(m.group(1), m.group(1))
                result.append(f'{num}. {m.group(2)}')
                continue
            # 检测以冒号结尾后跟分号分隔的列表项（OCR丢失序号的情况）
            # 模式："...应符合下列规定： 条款1； 条款2； 条款3；" → 拆分为带序号的列表
            # 匹配：冒号 + 空格 + 中文内容 + 分号 + 中文内容 + 分号 + ...
            if '；' in stripped and re.match(r'^.*[：:]\s*[\u4e00-\u9fff]', stripped):
                # 找到冒号位置
                colon_pos = -1
                for cp in ['：', ':']:
                    pos = stripped.find(cp)
                    if pos >= 0:
                        colon_pos = pos + 1
                        break
                if colon_pos > 0:
                    prefix = stripped[:colon_pos]
                    items_text = stripped[colon_pos:].strip()
                    # 按中文分号拆分
                    parts = [p.strip() for p in re.split(r'[；;]', items_text) if p.strip()]
                    if len(parts) >= 2:
                        result.append(prefix)
                        for idx, part in enumerate(parts, 1):
                            result.append(f'{idx}. {part}')
                        continue
            result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _normalize_heading_levels(text: str) -> str:
        lines = text.split('\n')
        heading_levels = []
        for line in lines:
            m = re.match(r'^(#{1,6})\s', line)
            if m:
                heading_levels.append(len(m.group(1)))

        if not heading_levels:
            return text

        unique_levels = sorted(set(heading_levels))
        level_map = {}
        for i, lvl in enumerate(unique_levels):
            level_map[lvl] = min(i + 1, 6)

        result = []
        for line in lines:
            m = re.match(r'^(#{1,6})\s', line)
            if m:
                old_level = len(m.group(1))
                new_level = level_map[old_level]
                rest = line[old_level:]
                result.append('#' * new_level + rest)
            else:
                result.append(line)

        return '\n'.join(result)

    @staticmethod
    def _ensure_heading_spacing(text: str) -> str:
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

    @staticmethod
    def _convert_numeric_headings(text: str) -> str:
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            m = TextCleaner._NUMERIC_HEADING_RE.match(stripped)
            if m:
                num_part = m.group(1).strip()
                title_part = m.group(2).strip()
                # 截断标题：取第一个句号/分号之前的内容作为标题，剩余作为正文
                # 但如果句号在末尾附近（最后5个字符内），说明整个标题就是一条完整条文，不截断
                truncate_pos = len(title_part)
                for punct in ['。', '；', '！', '？']:
                    pos = title_part.find(punct)
                    if pos > 0 and pos < truncate_pos:
                        truncate_pos = pos
                # 如果截断点在末尾5个字符内，说明这是条文的结尾而非标题/正文的分界，不截断
                if truncate_pos >= len(title_part) - 5:
                    truncate_pos = len(title_part)
                if truncate_pos < len(title_part):
                    heading_text = title_part[:truncate_pos]
                    remaining_text = title_part[truncate_pos:]
                    depth = num_part.replace(' ', '').count('.') + 1
                    level = min(depth + 1, 6)
                    # 将 remaining_text 追加到标题行后面，而非单独成行
                    # 避免产生以"。；"开头的孤立行
                    result.append(f'{"#" * level} {num_part} {heading_text}{remaining_text.strip()}')
                else:
                    depth = num_part.replace(' ', '').count('.') + 1
                    level = min(depth + 1, 6)
                    result.append(f'{"#" * level} {num_part} {title_part}')
            else:
                result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _clean_page_numbers(text: str) -> str:
        """清理孤立的页码行（如单独一行的 "3"、"·4·"）。
        注意：只清理独立成行的页码，不清理列表编号（如 "1. xxx"）。"""
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # 清理 "·3·"、"- 3 -" 格式的页码
            if re.match(r'^[·\-—]\s*\d+\s*[·\-—]$', stripped):
                continue
            # 清理孤立数字行（页码），但排除列表编号行（如 "1."、"2. xxx"）
            if re.match(r'^\d{1,3}$', stripped):
                continue
            result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _fix_area_units(text: str) -> str:
        """全局修复面积单位：在面积关键词上下文中，将 '数字 m' 修复为 '数字 m²'。
        OCR逐块处理时，面积关键词和数字可能不在同一文本块，导致遗漏。"""
        area_keywords = r'(?:面积|占地|场地|净面积|总建筑面积|建筑面|使用面|营业面|基底面)'
        # 距离/宽度/高度关键词：这些上下文中的 "数字 m" 是距离而非面积
        distance_keywords = r'(?:宽度|距离|高度|直径|半径|长度|厚度|深度|净宽|短边)'
        # 匹配：面积关键词 + 中间文字 + 数字 + m（非m²/m³）
        def _replace_area(m):
            full = m.group(0)
            num = m.group(2)
            # 检查 "数字 m" 前面的上下文是否包含距离关键词
            # 取 "数字 m" 前面最近的关键词
            before_num = full[:full.rfind(num)]
            if re.search(distance_keywords, before_num):
                return full  # 不转换，这是距离/宽度/高度
            return full.replace(f'{num} m', f'{num} m²', 1)
        text = re.sub(
            rf'({area_keywords}[^。；？！\n]*?(\d+\.?\d*)\s+m(?![a-zA-Z\d²³]))',
            _replace_area,
            text
        )
        # 体积上下文修复：在体积关键词上下文中，将 '数字 m' 修复为 '数字 m³'
        volume_keywords = r'(?:体积|容积|库容)'
        def _replace_volume(m):
            full = m.group(0)
            num = m.group(2)
            return full.replace(f'{num} m', f'{num} m³', 1)
        text = re.sub(
            rf'({volume_keywords}[^。；？！\n]*?(\d+\.?\d*)\s+m(?![a-zA-Z\d²³]))',
            _replace_volume,
            text
        )
        return text

    @staticmethod
    def _convert_chinese_numeric_headings(text: str) -> str:
        cn_num_map = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
                      '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            # 跳过已有标记的行（标题、列表项、表格）
            if stripped.startswith('#') or stripped.startswith('|') or re.match(r'^\d+\.\s', stripped):
                result.append(line)
                continue
            m = re.match(r'^((?:一|二|三|四|五|六|七|八|九|十)+)[、.．\s]+([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z\s：:——\-]{1,40})', stripped)
            if m:
                cn_num = m.group(1)
                title_part = m.group(2).strip()
                result.append(f'## {cn_num}、{title_part}')
            else:
                result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _convert_academic_labels(text: str) -> str:
        academic_label_patterns = [
            (r'^[\s]*摘[\s]*要[\s：:]*([\u4e00-\u9fffA-Za-z].+)$', r'## 摘要\n\n\1'),
            (r'^[\s]*(?:关键词|关键字)[\s：:]*([\u4e00-\u9fffA-Za-z].+)$', r'**关键词**：\1'),
            (r'^[\s]*Abstract[\s：:]*([A-Za-z].+)$', r'## Abstract\n\n\1'),
            (r'^[\s]*Key[\s]*words[\s：:]*([A-Za-z].+)$', r'**Keywords**: \1'),
        ]
        for pattern, replacement in academic_label_patterns:
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        return text

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return text

    @staticmethod
    def _fix_chinese_spacing(text: str) -> str:
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.strip()
            if re.match(r'^(?:关键词|关键字)[\s：:]', stripped, re.IGNORECASE):
                colon_pos = -1
                for i, c in enumerate(stripped):
                    if c in '：:':
                        colon_pos = i
                        break
                if colon_pos >= 0:
                    prefix = stripped[:colon_pos + 1]
                    rest = stripped[colon_pos + 1:]
                else:
                    m = re.match(r'^(关键词|关键字)', stripped)
                    prefix = m.group(1) + '：'
                    rest = stripped[m.end():]
                rest = re.sub(r'[\s,，;；]+', '；', rest.strip())
                line = prefix + rest
            else:
                prev = None
                while prev != line:
                    prev = line
                    line = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", line)
                    line = re.sub(r"([\u4e00-\u9fff])\s+([，。！？；：、）】」』])", r"\1\2", line)
                    line = re.sub(r"([（【「『])\s+([\u4e00-\u9fff])", r"\1\2", line)
            result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _merge_broken_lines(text: str) -> str:
        lines = text.split("\n")
        merged = []
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            if not line:
                merged.append("")
                i += 1
                continue

            stripped = line.strip()

            if re.match(r"^#{1,6}\s", stripped):
                merged.append(line)
                i += 1
                continue

            if TextCleaner._NUMERIC_HEADING_RE.match(stripped):
                # 条文号行：如果不以句号结尾，且下一行不是新条文号，则合并
                if not stripped.endswith(('。', '；', '！', '？', '.', ';', '!', '?', '：', ':')):
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        # 下一行不是新条文号、标题、列表项等，则合并
                        if next_line and not re.match(r'^\d+\.\d+', next_line) and not next_line.startswith('#') and not re.match(r'^[\d一二三四五六七八九十]+[、.．]\s', next_line) and not re.match(r'^表\s*\d', next_line):
                            merged.append(line + next_line)
                            i += 2
                            continue
                merged.append(line)
                i += 1
                continue

            if re.match(r"^[\d一二三四五六七八九十]+[、.．]\s", stripped):
                merged.append(line)
                i += 1
                continue

            # 纯数字+空格+中文（条款编号，如"1 设置在一"），不合并
            if re.match(r'^\d{1,2}\s*[\u4e00-\u9fff]', stripped) and not re.match(r'^\d+\.\d+', stripped):
                merged.append(line)
                i += 1
                continue

            if re.match(r'^(?:一|二|三|四|五|六|七|八|九|十)+[、.．\s]', stripped):
                merged.append(line)
                i += 1
                continue

            if re.match(r'^(?:摘要|关键词|关键字|Abstract|Key\s*words)(?:[\s：:]|(?=[\u4e00-\u9fffA-Za-z]))', stripped, re.IGNORECASE):
                merged.append(line)
                i += 1
                continue

            if stripped.startswith('```'):
                code_lines = [line]
                i += 1
                while i < len(lines):
                    code_lines.append(lines[i])
                    if lines[i].strip().endswith('```') and len(code_lines) > 1:
                        i += 1
                        break
                    i += 1
                merged.append('\n'.join(code_lines))
                continue

            if stripped.startswith('|') and '|' in stripped[1:]:
                table_lines = [line]
                i += 1
                while i < len(lines) and lines[i].strip().startswith('|'):
                    table_lines.append(lines[i])
                    i += 1
                merged.append('\n'.join(table_lines))
                continue

            if stripped.startswith(('- ', '* ', '+ ', '> ')) or re.match(r'^\d+\.\s', stripped):
                merged.append(line)
                i += 1
                continue

            if stripped.endswith(('。', '！', '？', '；', '：', '.', '!', '?', ';', ':', '…', '—', '】', '）', ')', ']', '`', '"', "'")):
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if not next_line:
                        merged.append(line)
                        i += 1
                        continue
                    if next_line.startswith(("#", "第", "|", "-", "*", ">", "```", "表")):
                        merged.append(line)
                        i += 1
                        continue
                    if re.match(r"^[\d一二三四五六七八九十]+[、.．]\s", next_line):
                        merged.append(line)
                        i += 1
                        continue
                    if TextCleaner._NUMERIC_HEADING_RE.match(next_line):
                        merged.append(line)
                        i += 1
                        continue
                    if re.search(r'^[\u4e00-\u9fff]', next_line) and len(next_line) > 15:
                        merged.append(line)
                        i += 1
                        continue
                    if re.search(r'^[A-Z]', next_line) and len(next_line) > 20:
                        merged.append(line)
                        i += 1
                        continue
                else:
                    merged.append(line)
                    i += 1
                    continue

            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if not next_line:
                    merged.append(line)
                    i += 1
                    continue

                if next_line and not next_line.startswith(("#", "第", "|", "-", "*", ">", "```")):
                    if not re.match(r"^[\d一二三四五六七八九十]+[、.．]\s", next_line):
                        if not TextCleaner._NUMERIC_HEADING_RE.match(next_line):
                            if not re.match(r'^(?:摘要|关键词|关键字|Abstract|Key\s*words)(?:[\s：:]|(?=[\u4e00-\u9fffA-Za-z]))', next_line, re.IGNORECASE):
                                if not line.endswith(("—", "…", "-")):
                                    merged.append(line + next_line)
                                    i += 2
                                    continue

            merged.append(line)
            i += 1

        # 二次合并：将不完整的条文标题与后续段落合并（跳过中间的表格和空行）
        # 例如：5.5.13标题以"直接"结尾，中间有表格5.5.8，续行"相连的楼梯间外"
        final = []
        j = 0
        while j < len(merged):
            line = merged[j]
            stripped = line.strip()
            # 检测条文号行且不以句号结尾
            if (re.match(r'^#{1,6}\s\d+\.\d+', stripped) or TextCleaner._NUMERIC_HEADING_RE.match(stripped)):
                if not stripped.endswith(('。', '；', '！', '？', '.', ';', '!', '?', '：', ':')):
                    # 向后搜索续行（跳过表格、空行）
                    k = j + 1
                    while k < len(merged):
                        next_stripped = merged[k].strip()
                        if not next_stripped:
                            k += 1
                            continue
                        if next_stripped.startswith('|'):
                            k += 1
                            continue
                        if re.match(r'^表\s*\d', next_stripped):
                            k += 1
                            continue
                        # 找到续行
                        if not re.match(r'^\d+\.\d+', next_stripped) and not next_stripped.startswith('#'):
                            merged[j] = line + next_stripped
                            # 标记续行为空（避免重复输出）
                            merged[k] = ''
                        break
            final.append(merged[j])
            j += 1

        return "\n".join(final)

    @staticmethod
    def _remove_page_artifacts(text: str) -> str:
        text = re.sub(r'^#{1,6}\s*第\s*\d+\s*页\s*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^#{1,6}\s*第\s*[\d一二三四五六七八九十]+\s*页\s*[/／]\s*共?\s*[\d一二三四五六七八九十]+\s*页\s*$', '', text, flags=re.MULTILINE)

        text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*第\s*\d+\s*页\s*(?:共\s*\d+\s*页)?\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*-\s*\d+\s*-\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[Pp]age\s*\d+\s*(?:of\s*\d+)?\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*[Pp]\s*\d+\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*第\s*[\d一二三四五六七八九十]+\s*页\s*[/／]\s*共?\s*[\d一二三四五六七八九十]+\s*页\s*$", "", text, flags=re.MULTILINE)

        header_footer_patterns = [
            r"^[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*卷[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*期[\s]*[\u4e00-\u9fff\d\s]{0,20}[\s]*$",
            r"^[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*卷[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*期[\s]*$",
            r"^[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*卷[\s]*$",
            r"^[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*期[\s]*$",
            r"^[\s]*[\u4e00-\u9fff\d\s]{2,10}[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*卷[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*期[\s]*$",
            r"^[\s]*[\u4e00-\u9fff\d\s]{2,10}(?:学报|期刊|杂志|大学|学院|研究院)[\u4e00-\u9fff\d\s]{0,15}(?:社会科学版|自然科学版|哲学版|综合版|人文版)?[\s]*$",
            r"^[\s]*[\u4e00-\u9fff\d\s]{2,10}(?:学报|期刊|杂志|大学|学院)[\u4e00-\u9fff\d\s]{0,15}(?:社会科学版|自然科学版|哲学版|综合版|人文版)?[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*卷[\s]*第[\s]*[\d一二三四五六七八九十百千]+[\s]*期[\s]*$",
            r"^[\s]*Vol[\s]*\.?\s*\d+[\s]*,?\s*No[\s]*\.?\s*\d+[\s]*$",
            r"^[\s]*[\d\s]{4}[\s]*年[\s]*$",
            r"^[\s]*[\d\s]{4}[\s]*年[\s]*[\d一二三四五六七八九十]+[\s]*月[\s]*$",
            r"^[\s]*作者简介[\s：:]*.*$",
            r"^[\s]*收稿日期[\s：:]*.*$",
            r"^[\s]*基金项目[\s：:]*.*$",
            r"^[\s]*中图分类号[\s：:]*.*$",
            r"^[\s]*文献标识码[\s：:]*.*$",
            r"^[\s]*文章编号[\s：:]*.*$",
            r"^[\s]*DOI[\s：:]*.*$",
            r"^[\s]*doi[\s：:]*.*$",
            r"^[\s]*\*[\s]*$",
            r"^[\s]*\*{1,3}[\s]*[\u4e00-\u9fff]{2,4}[\s]*$",
        ]
        for pattern in header_footer_patterns:
            text = re.sub(pattern, "", text, flags=re.MULTILINE)

        text = re.sub(r'^\s*第\s*\d+\s*页\s*$', '', text, flags=re.MULTILINE)

        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                cleaned.append('')
                continue
            if re.match(r'^#{1,6}\s*第\s*[\d一二三四五六七八九十]+\s*页', stripped):
                continue
            if re.match(r'^[\u4e00-\u9fff]{1,4}\s*第\s*\d+\s*期\s*$', stripped):
                continue
            if re.match(r'^[\u4e00-\u9fff]{1,4}\s*$', stripped) and len(stripped) <= 4:
                continue
            cleaned.append(line)
        text = '\n'.join(cleaned)

        return text

    @staticmethod
    def _fix_punctuation(text: str) -> str:
        text = text.replace(",,", "，")
        text = text.replace("..", "。")
        text = text.replace("。。", "。")
        text = re.sub(r"\.\.", "。", text)
        text = re.sub(r"([。！？])\1{2,}", r"\1\1", text)
        text = re.sub(r'([\u4e00-\u9fff]),(?=[\u4e00-\u9fff])', r'\1，', text)
        text = re.sub(r'([\u4e00-\u9fff])\.(?=[\u4e00-\u9fff\s]|$)', r'\1。', text)
        text = re.sub(r'([\u4e00-\u9fff]);(?=[\u4e00-\u9fff])', r'\1；', text)
        text = re.sub(r'([\u4e00-\u9fff]):(?=[\u4e00-\u9fff])', r'\1：', text)
        text = re.sub(r'([\u4e00-\u9fff])!(?=[\u4e00-\u9fff\s]|$)', r'\1！', text)
        text = re.sub(r'([\u4e00-\u9fff])\?(?=[\u4e00-\u9fff\s]|$)', r'\1？', text)
        text = re.sub(r'([\u4e00-\u9fff])\((?=[\u4e00-\u9fff])', r'\1（', text)
        text = re.sub(r'([\u4e00-\u9fff])\)(?=[\u4e00-\u9fff])', r'\1）', text)
        return text

    @staticmethod
    def _remove_repeated_blank_lines(text: str) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    @staticmethod
    def assess_text_quality(text: str) -> Dict[str, Any]:
        if not text or not text.strip():
            return {"score": 0.0, "issues": ["empty_text"], "is_valid": False}

        issues = []
        score = 1.0

        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        digit_chars = len(re.findall(r'\d', text))
        total_chars = len(text.replace(' ', '').replace('\n', ''))

        if total_chars == 0:
            return {"score": 0.0, "issues": ["no_content"], "is_valid": False}

        chinese_ratio = chinese_chars / max(total_chars, 1)
        digit_ratio = digit_chars / max(total_chars, 1)

        if chinese_ratio < 0.1 and english_chars < 10:
            issues.append("low_content_density")
            score -= 0.3

        if digit_ratio > 0.6:
            issues.append("excessive_digits")
            score -= 0.3

        special_chars = len(re.findall(r'[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef.,;:!?，。；：！？、""''（）【】《》—…·-]', text))
        special_ratio = special_chars / max(total_chars, 1)
        if special_ratio > 0.3:
            issues.append("high_special_chars")
            score -= 0.2

        sentences = re.split(r'[。！？\n]', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if sentences:
            avg_len = sum(len(s) for s in sentences) / len(sentences)
            if avg_len < 5:
                issues.append("fragmented_sentences")
                score -= 0.2

        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        if len(paragraphs) >= 2:
            seen = set()
            for p in paragraphs:
                p_norm = re.sub(r'\s+', '', p)
                if p_norm in seen:
                    issues.append("duplicate_paragraphs")
                    score -= 0.2
                    break
                seen.add(p_norm)

        lines = text.split('\n')
        short_lines = sum(1 for l in lines if len(l.strip()) < 3 and l.strip())
        if len(lines) > 5 and short_lines / len(lines) > 0.5:
            issues.append("too_many_short_lines")
            score -= 0.15

        garbage_patterns = [
            r'''[^\u4e00-\u9fff\w\s.,;:!?，。；：！？、"'（）【】《》—…·/\\|@#$%^&*+=\[\]{}~`\-]{5,}''',
            r'(.)\1{4,}',
            r'[a-zA-Z]{20,}',
        ]
        for gp in garbage_patterns:
            if re.search(gp, text):
                issues.append("garbage_content")
                score -= 0.3
                break

        score = max(0.0, min(1.0, score))
        is_valid = score >= 0.4

        return {
            "score": round(score, 3),
            "issues": issues,
            "is_valid": is_valid,
            "stats": {
                "total_chars": total_chars,
                "chinese_ratio": round(chinese_ratio, 3),
                "digit_ratio": round(digit_ratio, 3),
                "special_ratio": round(special_ratio, 3),
                "paragraph_count": len(paragraphs),
                "sentence_count": len(sentences),
            },
        }

    @staticmethod
    def filter_low_quality_paragraphs(text: str, min_score: float = 0.4) -> str:
        paragraphs = text.split('\n\n')
        filtered = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            quality = TextCleaner.assess_text_quality(para)
            if quality["is_valid"]:
                filtered.append(para)
            else:
                logger.info(f"过滤低质量段落(得分={quality['score']}, 问题={quality['issues']}): {para[:80]}...")

        return '\n\n'.join(filtered)


class FileConverter:
    def __init__(self):
        self._check_dependencies()
        self._paddle_ocr_instance = None  # PaddleOCR 实例缓存

    def _ocr_disabled_result(self, filename: str) -> ConversionResult:
        return ConversionResult(
            success=False,
            original_name=filename,
            error=(
                "OCR is disabled (OCR_MODE=off). Text-based PDFs can be uploaded normally; "
                "scanned PDFs and images need OCR before upload, or set OCR_MODE=local "
                "and build with the full OCR dependencies."
            ),
        )

    def _check_dependencies(self):
        self._has_docx = self._try_import("docx")
        self._has_pymupdf = self._try_import("fitz")
        self._has_pdfplumber = self._try_import("pdfplumber")
        self._has_pdf = self._has_pymupdf or self._has_pdfplumber
        self._has_openpyxl = self._try_import("openpyxl")
        self._has_pptx = self._try_import("pptx")
        self._has_pil = self._try_import("PIL")
        self._has_tesseract = settings.local_ocr_enabled and self._try_import("pytesseract")
        self._has_paddleocr = settings.local_ocr_enabled and self._try_import("paddleocr")
        self._has_bs4 = self._try_import("bs4")
        self._has_markdownify = self._try_import("markdownify")
        self._has_chardet = self._try_import("chardet")

    def _try_import(self, module_name: str) -> bool:
        try:
            __import__(module_name)
            return True
        except ImportError:
            return False

    def _get_paddle_ocr(self):
        """获取或初始化 PaddleOCR 实例（单例缓存）"""
        if not settings.local_ocr_enabled:
            return None
        if self._paddle_ocr_instance is not None:
            return self._paddle_ocr_instance
        if not self._has_paddleocr:
            return None
        try:
            from paddleocr import PaddleOCR
            # 使用 server 模型提高识别精度，det_limit_side_len 增大检测分辨率
            self._paddle_ocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                show_log=False,
                det_model_dir=None,  # 使用默认 server 检测模型
                rec_model_dir=None,  # 使用默认 server 识别模型
                det_limit_side_len=1920,  # 增大检测边长限制，提高小字识别率
                det_db_thresh=0.3,  # 降低检测阈值，捕获更多文字区域
            )
            logger.info("PaddleOCR 引擎初始化成功")
            return self._paddle_ocr_instance
        except Exception as e:
            logger.warning(f"PaddleOCR 初始化失败: {e}")
            return None

    def convert(self, file_content: bytes, filename: str) -> ConversionResult:
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_FORMATS:
            # 对旧版 Office 格式给出明确提示
            if ext in LEGACY_FORMATS:
                new_ext = ext + 'x'  # .doc -> .docx, .xls -> .xlsx, .ppt -> .pptx
                return ConversionResult(
                    success=False,
                    original_name=filename,
                    error=f"不支持旧版 {ext} 格式，请将文件另存为 {new_ext} 格式后重新上传。可在 Office 中打开后选择'另存为'转换为 {new_ext} 格式。",
                )
            return ConversionResult(
                success=False,
                original_name=filename,
                error=f"不支持的文件格式: {ext}。支持: {', '.join(SUPPORTED_FORMATS.keys())}",
            )

        if len(file_content) > settings.MAX_FILE_SIZE:
            return ConversionResult(
                success=False,
                original_name=filename,
                error=f"文件过大: {len(file_content)} 字节 (最大 {settings.MAX_FILE_SIZE})",
            )

        converters = {
            ".txt": self._convert_txt,
            ".md": self._convert_md,
            ".markdown": self._convert_md,
            ".csv": self._convert_csv,
            ".yaml": self._convert_yaml,
            ".yml": self._convert_yaml,
            ".xml": self._convert_xml,
            ".rtf": self._convert_rtf,
            ".odt": self._convert_odt,
            ".epub": self._convert_epub,
            ".doc": self._convert_legacy_office,
            ".docx": self._convert_docx,
            ".xls": self._convert_legacy_office,
            ".xlsx": self._convert_xlsx,
            ".ppt": self._convert_legacy_office,
            ".pptx": self._convert_pptx,
            ".pdf": self._convert_pdf,
            ".json": self._convert_json,
            ".html": self._convert_html,
            ".htm": self._convert_html,
            ".png": self._convert_image,
            ".jpg": self._convert_image,
            ".jpeg": self._convert_image,
            ".bmp": self._convert_image,
            ".tiff": self._convert_image,
            ".tif": self._convert_image,
            ".webp": self._convert_image,
            ".gif": self._convert_image,
            ".svg": self._convert_svg,
        }

        converter = converters.get(ext)
        if not converter:
            return ConversionResult(
                success=False,
                original_name=filename,
                error=f"无可用的转换器: {ext}",
            )

        try:
            result = converter(file_content, filename)
            if result.success and result.markdown_content:
                original_chars = len(result.markdown_content)
                result.markdown_content = TextCleaner.clean_markdown_document(result.markdown_content)
                cleaned_chars = len(result.markdown_content)
                result.metadata = {
                    **(result.metadata or {}),
                    "cleaning": {
                        "pipeline": "structure_preserving_v2",
                        "original_chars": original_chars,
                        "cleaned_chars": cleaned_chars,
                        "removed_chars": max(0, original_chars - cleaned_chars),
                        "protected_blocks": ["markdown_table", "fenced_code"],
                    },
                }
                result.content_hash = hashlib.sha256(result.markdown_content.encode("utf-8")).hexdigest()
            return result
        except Exception as e:
            logger.error(f"文件转换失败 {filename}: {e}", exc_info=True)
            return ConversionResult(
                success=False,
                original_name=filename,
                error=f"转换错误: {str(e)}",
            )

    def convert_pdf_ocr(self, file_content: bytes, filename: str) -> ConversionResult:
        """强制使用 OCR 模式转换 PDF（适用于扫描件/CID乱码PDF）"""
        if not settings.local_ocr_enabled:
            return self._ocr_disabled_result(filename)
        if self._has_pil and (self._has_paddleocr or self._has_tesseract):
            return self._convert_pdf_ocr(file_content, filename)
        return ConversionResult(
            success=False,
            original_name=filename,
            error="OCR 不可用，请安装 PaddleOCR (pip install paddlepaddle paddleocr) 或 Tesseract",
        )

    def _convert_txt(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        title = Path(filename).stem
        md = f"# {title}\n\n{text}"
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=md,
            metadata={"source_format": "txt", "char_count": len(text)},
        )

    def _convert_md(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=text,
            metadata={"source_format": "md", "char_count": len(text)},
        )

    def _convert_csv(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        title = Path(filename).stem
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        if not rows:
            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content=f"# {title}\n\n*空CSV文件*",
                metadata={"source_format": "csv", "row_count": 0},
            )

        headers = [str(cell).replace("|", "\\|").replace("\n", " ").strip() for cell in rows[0]]
        data_rows = rows[1:]
        md_lines = [f"# {title}", ""]
        md_lines.append(f"| {' | '.join(headers)} |")
        md_lines.append(f"| {' | '.join(['---'] * len(headers))} |")
        for row in data_rows:
            padded = [str(cell).replace("|", "\\|").replace("\n", " ").strip() for cell in row]
            padded += [""] * (len(headers) - len(padded))
            md_lines.append(f"| {' | '.join(padded[:len(headers)])} |")

        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content="\n".join(md_lines),
            metadata={"source_format": "csv", "row_count": len(data_rows), "column_count": len(headers)},
        )

    def _convert_yaml(self, content: bytes, filename: str) -> ConversionResult:
        try:
            import yaml
        except ImportError:
            return ConversionResult(success=False, original_name=filename, error="PyYAML 未安装，请运行 pip install PyYAML")

        text = self._decode_text(content)
        try:
            data = yaml.safe_load(text)
        except Exception as exc:
            return ConversionResult(success=False, original_name=filename, error=f"无效 YAML: {exc}")

        json_result = self._convert_json(
            json.dumps(data, ensure_ascii=False).encode("utf-8"),
            filename,
        )
        json_result.original_name = filename
        json_result.metadata = {
            **(json_result.metadata or {}),
            "source_format": Path(filename).suffix.lower().lstrip("."),
        }
        return json_result

    def _convert_xml(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return ConversionResult(success=False, original_name=filename, error=f"无效 XML: {exc}")

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def element_to_value(element):
            children = list(element)
            text_value = (element.text or "").strip()
            if not children and not element.attrib:
                return text_value
            value: Dict[str, Any] = {}
            if element.attrib:
                value["@attributes"] = dict(element.attrib)
            if text_value:
                value["#text"] = text_value
            grouped: Dict[str, List[Any]] = {}
            for child in children:
                grouped.setdefault(local_name(child.tag), []).append(element_to_value(child))
            for key, values in grouped.items():
                value[key] = values[0] if len(values) == 1 else values
            return value

        data = {local_name(root.tag): element_to_value(root)}
        result = self._convert_json(json.dumps(data, ensure_ascii=False).encode("utf-8"), filename)
        result.metadata = {
            **(result.metadata or {}),
            "source_format": "xml",
            "root_element": local_name(root.tag),
        }
        return result

    def _convert_rtf(self, content: bytes, filename: str) -> ConversionResult:
        raw = content.decode("latin-1", errors="replace")

        def replace_unicode(match: re.Match) -> str:
            value = int(match.group(1))
            if value < 0:
                value += 65536
            try:
                return chr(value)
            except ValueError:
                return ""

        text = re.sub(r'\\u(-?\d+)\??', replace_unicode, raw)
        text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="ignore"), text)
        text = re.sub(r'{\\\*[^{}]*(?:{[^{}]*}[^{}]*)*}', '', text)
        text = re.sub(r'\\(?:par|line)\b\s*', '\n', text)
        text = re.sub(r'\\tab\b\s*', '\t', text)
        text = re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', text)
        text = text.replace('\\{', '{').replace('\\}', '}').replace('\\\\', '\\')
        text = re.sub(r'[{}]', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        title = Path(filename).stem
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=f"# {title}\n\n{text}",
            metadata={"source_format": "rtf", "char_count": len(text)},
        )

    def _convert_odt(self, content: bytes, filename: str) -> ConversionResult:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                info = archive.getinfo("content.xml")
                if info.file_size > 64 * 1024 * 1024:
                    raise ValueError("ODT 内容过大")
                root = ET.fromstring(archive.read(info))
        except Exception as exc:
            return ConversionResult(success=False, original_name=filename, error=f"ODT 解析失败: {exc}")

        def name(element) -> str:
            return element.tag.rsplit("}", 1)[-1]

        def text_of(element) -> str:
            return "".join(element.itertext()).strip()

        table_descendants = set()
        table_blocks: Dict[int, str] = {}
        for table in [node for node in root.iter() if name(node) == "table"]:
            rows = []
            for row in [node for node in table.iter() if name(node) == "table-row"]:
                cells = []
                for cell in [node for node in list(row) if name(node) in {"table-cell", "covered-table-cell"}]:
                    cells.append(text_of(cell).replace("|", "\\|").replace("\n", " "))
                if cells:
                    rows.append(cells)
            if rows:
                width = max(len(row) for row in rows)
                rows = [row + [""] * (width - len(row)) for row in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
                lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
                table_blocks[id(table)] = "\n".join(lines)
            table_descendants.update(id(node) for node in table.iter() if node is not table)

        title = Path(filename).stem
        parts = [f"# {title}", ""]
        for element in root.iter():
            if id(element) in table_descendants:
                continue
            kind = name(element)
            value = text_of(element)
            if kind == "table" and id(element) in table_blocks:
                parts.extend([table_blocks[id(element)], ""])
            elif kind == "h" and value:
                level_attr = next((v for k, v in element.attrib.items() if k.endswith("outline-level")), "1")
                level = max(2, min(6, int(level_attr) + 1 if str(level_attr).isdigit() else 2))
                parts.extend([f"{'#' * level} {value}", ""])
            elif kind == "p" and value:
                parts.extend([value, ""])

        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content="\n".join(parts),
            metadata={"source_format": "odt", "table_count": len(table_blocks)},
        )

    def _convert_epub(self, content: bytes, filename: str) -> ConversionResult:
        title = Path(filename).stem
        parts = [f"# {title}", ""]
        chapter_count = 0
        total_uncompressed = 0
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = [
                    info for info in archive.infolist()
                    if info.filename.lower().endswith((".xhtml", ".html", ".htm"))
                    and not any(token in Path(info.filename).name.lower() for token in ("nav", "toc"))
                ]
                for info in sorted(members, key=lambda item: item.filename):
                    total_uncompressed += info.file_size
                    if total_uncompressed > 96 * 1024 * 1024:
                        raise ValueError("EPUB 解压内容过大")
                    chapter_name = Path(info.filename).stem
                    chapter = self._convert_html(archive.read(info), f"{chapter_name}.html")
                    if not chapter.success or not chapter.markdown_content.strip():
                        continue
                    chapter_text = re.sub(r'^#\s+[^\n]+\n+', '', chapter.markdown_content.strip())
                    parts.extend([f"## {chapter_name}", "", chapter_text, ""])
                    chapter_count += 1
        except Exception as exc:
            return ConversionResult(success=False, original_name=filename, error=f"EPUB 解析失败: {exc}")

        if not chapter_count:
            return ConversionResult(success=False, original_name=filename, error="EPUB 中没有可解析的正文")
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content="\n".join(parts),
            metadata={"source_format": "epub", "chapter_count": chapter_count},
        )

    def _convert_legacy_office(self, content: bytes, filename: str) -> ConversionResult:
        office_binary = shutil.which("libreoffice") or shutil.which("soffice")
        ext = Path(filename).suffix.lower()
        target_ext = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}.get(ext)
        if not office_binary or not target_ext:
            return ConversionResult(
                success=False,
                original_name=filename,
                error=f"旧版 {ext} 文件需要服务器安装 LibreOffice；也可以先另存为 {target_ext or '新版 Office 格式'} 后上传",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / Path(filename).name
            source_path.write_bytes(content)
            target_format = target_ext.lstrip(".")
            process = subprocess.run(
                [office_binary, "--headless", "--convert-to", target_format, "--outdir", temp_dir, str(source_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            converted_path = source_path.with_suffix(target_ext)
            if process.returncode != 0 or not converted_path.exists():
                message = (process.stderr or process.stdout or "LibreOffice 转换失败").strip()
                return ConversionResult(success=False, original_name=filename, error=message[:500])
            target_converter = {
                ".docx": self._convert_docx,
                ".xlsx": self._convert_xlsx,
                ".pptx": self._convert_pptx,
            }[target_ext]
            result = target_converter(converted_path.read_bytes(), converted_path.name)
            result.original_name = filename
            result.metadata = {
                **(result.metadata or {}),
                "source_format": ext.lstrip("."),
                "normalized_format": target_ext.lstrip("."),
                "conversion_engine": "libreoffice",
            }
            return result

    def _convert_docx(self, content: bytes, filename: str) -> ConversionResult:
        if not self._has_docx:
            return ConversionResult(success=False, original_name=filename, error="python-docx 未安装，请运行 pip install python-docx")

        import docx
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            doc = docx.Document(tmp_path)
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]

            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    md_lines.append("")
                    continue
                if para.style and para.style.name and para.style.name.startswith("Heading"):
                    level_str = para.style.name.split()[-1] if para.style.name.split() else "1"
                    level = int(level_str) if level_str.isdigit() else 1
                    level = min(level + 1, 6)
                    md_lines.append(f"{'#' * level} {text}")
                else:
                    runs_text = ""
                    for run in para.runs:
                        if run.bold and run.italic:
                            runs_text += f"***{run.text}***"
                        elif run.bold:
                            runs_text += f"**{run.text}**"
                        elif run.italic:
                            runs_text += f"*{run.text}*"
                        else:
                            runs_text += run.text
                    if runs_text.strip():
                        md_lines.append(runs_text.strip())
                    else:
                        md_lines.append(text)

            for table in doc.tables:
                md_lines.append("")
                for i, row in enumerate(table.rows):
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    md_lines.append(f"| {' | '.join(cells)} |")
                    if i == 0:
                        md_lines.append(f"| {' | '.join(['---'] * len(cells))} |")
                md_lines.append("")

            md_content = "\n".join(md_lines)

            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content=md_content,
                metadata={"source_format": "docx", "paragraph_count": len(doc.paragraphs), "table_count": len(doc.tables)},
            )
        finally:
            os.unlink(tmp_path)

    def _convert_xlsx(self, content: bytes, filename: str) -> ConversionResult:
        if not self._has_openpyxl:
            return ConversionResult(success=False, original_name=filename, error="openpyxl 未安装，请运行 pip install openpyxl")

        import openpyxl
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                md_lines.append(f"## {sheet_name}")
                md_lines.append("")

                rows_data = []
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    if any(c for c in cells):
                        rows_data.append(cells)

                if not rows_data:
                    md_lines.append("*空工作表*")
                    md_lines.append("")
                    continue

                max_cols = max(len(r) for r in rows_data)
                for r in rows_data:
                    while len(r) < max_cols:
                        r.append("")

                for i, cells in enumerate(rows_data):
                    escaped = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
                    md_lines.append("| " + " | ".join(escaped) + " |")
                    if i == 0:
                        md_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

                md_lines.append("")

            sheet_names = wb.sheetnames
            wb.close()
            md_content = "\n".join(md_lines)
            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content=md_content,
                metadata={"source_format": "xlsx", "sheet_count": len(sheet_names)},
            )
        finally:
            os.unlink(tmp_path)

    def _convert_pptx(self, content: bytes, filename: str) -> ConversionResult:
        if not self._has_pptx:
            return ConversionResult(success=False, original_name=filename, error="python-pptx 未安装，请运行 pip install python-pptx")

        from pptx import Presentation
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            prs = Presentation(tmp_path)
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]

            for i, slide in enumerate(prs.slides):
                md_lines.append(f"## 幻灯片 {i + 1}")
                md_lines.append("")
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        md_lines.append(shape.text.strip())
                        md_lines.append("")
                    if shape.has_table:
                        table = shape.table
                        for row_idx, row in enumerate(table.rows):
                            cells = [cell.text.strip() for cell in row.cells]
                            md_lines.append(f"| {' | '.join(cells)} |")
                            if row_idx == 0:
                                md_lines.append(f"| {' | '.join(['---'] * len(cells))} |")
                        md_lines.append("")

            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content="\n".join(md_lines),
                metadata={"source_format": "pptx", "slide_count": len(prs.slides)},
            )
        finally:
            os.unlink(tmp_path)

    def _convert_pdf(self, content: bytes, filename: str) -> ConversionResult:
        try:
            from app.doc_processor.pdf_converter import advanced_pdf_converter
            result = advanced_pdf_converter.convert(content, filename)
            if result.get('success'):
                md = result.get('markdown_content', '')
                meta = result.get('metadata', {})
                total_chars = meta.get('total_chars', 0)
                images_count = meta.get('images_count', 0)

                # 扫描件 PDF 检测：有图片但无文字内容，自动降级 OCR
                if total_chars == 0 and images_count > 0:
                    logger.info(f"检测到扫描件 PDF（{images_count} 页图片，0 字符），自动降级 OCR")
                elif len(md.strip()) > 100 and not _has_severe_cid(md):
                    return ConversionResult(
                        success=True,
                        original_name=filename,
                        markdown_content=md,
                        metadata=meta,
                    )
                elif _has_severe_cid(md):
                    logger.warning("高级PDF转换结果含 CID 乱码，降级 OCR")
                # else: 文本过短，继续尝试其他引擎
        except Exception as e:
            logger.warning(f"高级PDF转换失败，回退到基础模式: {e}")

        result = None

        # PyMuPDF 优先：对 CID 编码和中文 PDF 处理更好
        if self._has_pymupdf:
            result = self._convert_pdf_pymupdf(content, filename)
            if result.success and len(result.markdown_content.strip()) > 100:
                if _has_severe_cid(result.markdown_content):
                    logger.warning("PyMuPDF 结果含 CID 乱码，尝试其他引擎")
                else:
                    return result

        if self._has_pdfplumber:
            result = self._convert_pdf_plumber(content, filename)
            if result.success and len(result.markdown_content.strip()) > 100:
                if _has_severe_cid(result.markdown_content):
                    logger.warning("pdfplumber 结果含 CID 乱码，尝试 OCR")
                else:
                    return result

        # OCR 降级：文本过少、CID 乱码、或扫描件 PDF
        if settings.local_ocr_enabled and self._has_pil and (self._has_paddleocr or self._has_tesseract):
            logger.info("常规PDF提取文字过少或质量差，降级到 OCR 模式")
            return self._convert_pdf_ocr(content, filename)

        if result:
            metadata = result.metadata or {}
            total_chars = metadata.get("total_chars")
            if total_chars == 0 or _has_severe_cid(result.markdown_content):
                return self._ocr_disabled_result(filename)
            return result

        return ConversionResult(
            success=False,
            original_name=filename,
            error="无PDF库可用，请安装 PyMuPDF (pip install PyMuPDF) 或 pdfplumber (pip install pdfplumber)",
        )

    def _convert_pdf_plumber(self, content: bytes, filename: str) -> ConversionResult:
        import pdfplumber
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]
            total_chars = 0

            with pdfplumber.open(tmp_path) as pdf:
                page_count = len(pdf.pages)

                for page_num, page in enumerate(pdf.pages):
                    page_text = self._extract_pdf_page_plumber(page)
                    if page_text.strip():
                        total_chars += len(page_text)
                        if page_count > 1:
                            md_lines.append(f"## 第 {page_num + 1} 页")
                            md_lines.append("")
                        md_lines.append(page_text.strip())
                        md_lines.append("")

                md_content = "\n".join(md_lines)
                md_content = TextCleaner.clean_pdf_text(md_content)

                return ConversionResult(
                    success=True,
                    original_name=filename,
                    markdown_content=md_content,
                    metadata={
                        "source_format": "pdf",
                        "page_count": page_count,
                        "engine": "pdfplumber",
                        "total_chars": total_chars,
                    },
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _extract_pdf_page_plumber(self, page) -> str:
        try:
            words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
            if not words:
                return page.extract_text() or ""

            columns = self._detect_columns(page, words)
            if columns and len(columns) >= 2:
                return self._extract_multi_column(words, columns)
            else:
                lines = self._group_words_to_lines(words)
                return "\n".join(lines)
        except Exception:
            return page.extract_text() or ""

    def _group_words_to_lines(self, words: list) -> List[str]:
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
                lines.append(self._merge_line_words(current_line_words))
                current_line_words = [word]
                current_top = word_top
        if current_line_words:
            lines.append(self._merge_line_words(current_line_words))
        return lines

    def _detect_columns(self, page, words: list) -> List[float]:
        if not words:
            return []

        page_width = page.width
        if page_width < 100:
            return []

        all_x0 = [w["x0"] for w in words if w["text"].strip()]
        all_x1 = [w["x1"] for w in words if w["text"].strip()]
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
            if not w["text"].strip():
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

    def _extract_multi_column(self, words: list, columns: List[float]) -> str:
        boundary = columns[1]
        full_width_lines = []
        col_left_lines = []
        col_right_lines = []

        sorted_words = sorted(words, key=lambda w: (round(w["top"], 0), w["x0"]))
        if not sorted_words:
            return ""

        lines = []
        current_line_words = [sorted_words[0]]
        current_top = round(sorted_words[0]["top"], 0)

        for word in sorted_words[1:]:
            word_top = round(word["top"], 0)
            if abs(word_top - current_top) <= 3:
                current_line_words.append(word)
            else:
                lines.append(current_line_words)
                current_line_words = [word]
                current_top = word_top

        if current_line_words:
            lines.append(current_line_words)

        for line_words in lines:
            has_left = any(w["x0"] < boundary and w["x1"] <= boundary + 5 for w in line_words)
            has_right = any(w["x0"] >= boundary - 5 for w in line_words)
            line_text = self._merge_line_words(line_words)

            if has_left and has_right:
                left_words = [w for w in line_words if w["x1"] <= boundary + 5]
                right_words = [w for w in line_words if w["x0"] >= boundary - 5]

                if left_words and right_words:
                    left_text = self._merge_line_words(left_words)
                    right_text = self._merge_line_words(right_words)
                    if left_text.strip():
                        col_left_lines.append(left_text)
                    if right_text.strip():
                        col_right_lines.append(right_text)
                else:
                    full_width_lines.append(line_text)
            elif has_right:
                col_right_lines.append(line_text)
            elif has_left:
                col_left_lines.append(line_text)
            else:
                full_width_lines.append(line_text)

        result_parts = []
        if full_width_lines:
            result_parts.append("\n".join(full_width_lines))
        if col_left_lines:
            result_parts.append("\n".join(col_left_lines))
        if col_right_lines:
            result_parts.append("\n".join(col_right_lines))

        return "\n\n".join(result_parts)

    def _merge_line_words(self, words: list) -> str:
        if not words:
            return ""
        sorted_words = sorted(words, key=lambda w: w["x0"])
        result = sorted_words[0]["text"]
        for i in range(1, len(sorted_words)):
            prev = sorted_words[i - 1]
            curr = sorted_words[i]
            gap = curr["x0"] - prev["x1"]
            prev_text = prev["text"]
            curr_text = curr["text"]
            prev_is_cjk = bool(re.search(r"[\u4e00-\u9fff]", prev_text))
            curr_is_cjk = bool(re.search(r"[\u4e00-\u9fff]", curr_text))

            if prev_is_cjk and curr_is_cjk and gap < 15:
                result += curr_text
            elif gap > 20:
                result += " " + curr_text
            else:
                result += curr_text

        return result

    def _convert_pdf_pymupdf(self, content: bytes, filename: str) -> ConversionResult:
        import fitz
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        doc = None
        try:
            doc = fitz.open(tmp_path)
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]
            total_chars = 0

            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text("text")
                if text.strip():
                    total_chars += len(text)
                    if len(doc) > 1:
                        md_lines.append(f"## 第 {page_num + 1} 页")
                        md_lines.append("")
                    md_lines.append(text.strip())
                    md_lines.append("")

            md_content = "\n".join(md_lines)
            md_content = TextCleaner.clean_pdf_text(md_content)

            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content=md_content,
                metadata={
                    "source_format": "pdf",
                    "page_count": len(doc),
                    "engine": "pymupdf",
                    "total_chars": total_chars,
                },
            )
        finally:
            if doc is not None:
                doc.close()
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _convert_pdf_ocr(self, content: bytes, filename: str) -> ConversionResult:
        """PDF OCR 转换，优先使用 PaddleOCR（中文效果更好），降级 Tesseract。
        利用 PaddleOCR 返回的坐标信息实现表格识别和多列文本分离。"""
        if not settings.local_ocr_enabled:
            return self._ocr_disabled_result(filename)
        import fitz
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        doc = None
        try:
            doc = fitz.open(tmp_path)
            title = Path(filename).stem
            md_lines = [f"# {title}", ""]
            total_chars = 0
            ocr_engine = "none"

            # 初始化 OCR 引擎（优先 PaddleOCR，使用缓存实例）
            paddle_ocr = self._get_paddle_ocr()
            if paddle_ocr is not None:
                ocr_engine = "paddleocr"
                logger.info("使用 PaddleOCR 引擎进行 PDF OCR")
            elif self._has_tesseract:
                ocr_engine = "tesseract"
                logger.info("使用 Tesseract 引擎进行 PDF OCR")

            # 逐页 OCR，收集每页的结构化文本
            page_texts = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                # 使用 300 DPI 获取高质量图像
                pix = page.get_pixmap(dpi=300)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))

                # 图像预处理：灰度化 + 对比度增强，提升扫描件识别率
                try:
                    from PIL import ImageEnhance, ImageFilter
                    if img.mode != 'L':
                        gray = img.convert('L')
                    else:
                        gray = img
                    enhancer = ImageEnhance.Contrast(gray)
                    gray = enhancer.enhance(1.5)
                    gray = gray.filter(ImageFilter.SHARPEN)
                    img = gray.convert('RGB')
                except Exception:
                    pass

                ocr_text = ""

                # 优先 PaddleOCR（利用坐标信息进行结构化处理）
                if paddle_ocr is not None:
                    try:
                        import numpy as np
                        img_array = np.array(img)
                        result = paddle_ocr.ocr(img_array, cls=True)
                        if result and result[0]:
                            ocr_text = self._structure_ocr_result(result[0], img_array.shape[1])
                    except Exception as e:
                        logger.warning(f"PaddleOCR 页 {page_num + 1} 识别失败: {e}")
                        ocr_text = ""

                # 降级 Tesseract
                if not ocr_text.strip() and self._has_tesseract:
                    try:
                        import pytesseract
                        ocr_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                        if not ocr_engine.startswith("tesseract"):
                            ocr_engine = "paddleocr+tesseract_fallback"
                    except Exception:
                        try:
                            import pytesseract
                            ocr_text = pytesseract.image_to_string(img, lang="eng")
                        except Exception as e:
                            ocr_text = f"[OCR失败: {str(e)}]"

                page_texts.append(ocr_text.strip())
                if ocr_text.strip():
                    total_chars += len(ocr_text)

            # 跨页拼接：如果上一页末尾不是句号/分号等结束符，且下一页开头不是新条目号，则合并
            merged_texts = self._merge_cross_page_texts(page_texts)

            for page_num, text in enumerate(merged_texts):
                if text.strip():
                    md_lines.append(text.strip())
                    md_lines.append("")
                else:
                    md_lines.append("")

            md_content = "\n".join(md_lines)
            md_content = TextCleaner.clean_pdf_text(md_content)

            return ConversionResult(
                success=True,
                original_name=filename,
                markdown_content=md_content,
                metadata={
                    "source_format": "pdf",
                    "page_count": len(doc),
                    "engine": f"ocr_{ocr_engine}",
                    "total_chars": total_chars,
                },
            )
        finally:
            if doc is not None:
                doc.close()
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _merge_cross_page_texts(self, page_texts: list) -> list:
        """跨页拼接：如果上一页末尾不是句号等结束符，且下一页开头不是新条目号，则合并两段文本。"""
        if not page_texts:
            return page_texts

        result = [page_texts[0]]
        for i in range(1, len(page_texts)):
            prev = result[-1].rstrip()
            curr = page_texts[i].lstrip()

            if not prev or not curr:
                result.append(page_texts[i])
                continue

            # 判断上一页末尾是否是句子结束
            prev_ends_sentence = prev[-1] in ('。', '！', '？', '；', '.', '!', '?', ';', '…', '—', '】', '）')
            # 判断下一页开头是否是新条目/标题
            curr_starts_new = bool(re.match(r'^#{1,6}\s', curr) or
                                   re.match(r'^\d+\.\d+', curr) or
                                   re.match(r'^表\s*\d', curr) or
                                   re.match(r'^\|', curr) or
                                   re.match(r'^第[一二三四五六七八九十\d]+[章节编]', curr))

            if not prev_ends_sentence and not curr_starts_new:
                # 拼接：上一页末尾 + 下一页开头（去掉页码标记后）
                # 清理下一页开头的页码标记
                curr_cleaned = re.sub(r'^\s*·\s*\d+\s*·\s*', '', curr)
                curr_cleaned = re.sub(r'^\s*-\s*\d+\s*-\s*', '', curr_cleaned)
                result[-1] = prev + curr_cleaned
            else:
                result.append(page_texts[i])

        return result

    def _structure_ocr_result(self, ocr_lines: list, img_width: int) -> str:
        """利用 PaddleOCR 返回的坐标信息，重建文档结构（表格、多列文本）。

        核心思路：
        1. 根据 y 坐标将文本行分组为"行"
        2. 根据 x 坐标分布检测表格区域（同一行有多个独立文本块）
        3. 表格区域用 Markdown 表格格式输出
        4. 非表格区域按阅读顺序拼接
        """
        if not ocr_lines:
            return ""

        # 解析 OCR 结果为结构化数据
        items = []
        for line in ocr_lines:
            if not line or len(line) < 2:
                continue
            bbox = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            text_info = line[1]  # [text, confidence]
            if isinstance(text_info, (list, tuple)) and len(text_info) >= 1:
                text = str(text_info[0])
                conf = text_info[1] if len(text_info) >= 2 else 1.0
            else:
                text = str(text_info)
                conf = 1.0

            x1, y1 = bbox[0]
            x2, y2 = bbox[2]
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            # 清理 OCR 常见误识别
            text = self._clean_ocr_text(text)

            if text.strip():
                items.append({
                    'text': text.strip(),
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'cx': cx, 'cy': cy, 'conf': conf,
                })

        if not items:
            return ""

        # 按 y 坐标排序，将 y 坐标接近的项归为同一行
        items.sort(key=lambda it: (it['cy'], it['cx']))
        rows = self._group_into_rows(items, y_tolerance=20)

        # 检测表格区域：连续多行都有多个列
        table_regions = self._detect_table_regions(rows, img_width)

        # 合并相邻的表格区域（间隔不超过2行非表格行的视为同一表格）
        table_regions = self._merge_nearby_tables(table_regions, rows)

        # 生成 Markdown
        md_parts = []
        row_idx = 0
        current_paragraph = []  # 收集连续的普通文本行，合并为段落

        while row_idx < len(rows):
            # 检查当前行是否在表格区域内
            in_table = None
            for tr in table_regions:
                if tr['start'] <= row_idx <= tr['end']:
                    in_table = tr
                    break

            if in_table and row_idx == in_table['start']:
                # 先输出已收集的段落
                if current_paragraph:
                    md_parts.append("".join(current_paragraph))
                    current_paragraph = []
                # 输出整个表格
                table_md = self._render_table(rows, in_table['start'], in_table['end'])
                md_parts.append(table_md)
                row_idx = in_table['end'] + 1
            else:
                # 普通文本行
                row = rows[row_idx]
                row_text = " ".join(it['text'] for it in row)

                # 判断是否需要开始新段落
                start_new_paragraph = False

                if current_paragraph:
                    prev_row = rows[row_idx - 1] if row_idx > 0 else None
                    # 如果当前行与上一行 y 间距较大，视为新段落
                    if prev_row and len(prev_row) > 0 and len(row) > 0:
                        prev_y = max(it['y2'] for it in prev_row)
                        curr_y = min(it['y1'] for it in row)
                        line_height = max(it['y2'] - it['y1'] for it in row) if row else 20
                        if curr_y - prev_y > line_height * 1.5:
                            start_new_paragraph = True

                    # 如果当前行以条款编号开头（如"1设置在"、"1 医疗"、"5.4.4"），视为新段落
                    if re.match(r'^\d{1,2}\s*[\u4e00-\u9fff]', row_text) or re.match(r'^\d+\.\d+', row_text):
                        start_new_paragraph = True

                # 如果当前行以"表"开头（如"表 5.5.8"），视为新段落
                if re.match(r'^表\s*\d', row_text):
                    start_new_paragraph = True

                if start_new_paragraph:
                    md_parts.append("".join(current_paragraph))
                    current_paragraph = []

                # 如果行首是条文号（如"5.1.1 民用建筑..."），将条文号+正文作为段落开头
                # 后续行会自然拼接到 current_paragraph 中
                heading_match = re.match(r'^(\d+\.\d+(?:\.\d+)*\s*[A-Za-z]?)\s+(.+)$', row_text)
                if heading_match and not in_table:
                    current_paragraph.append(row_text)
                    row_idx += 1
                    continue

                # 同一段落内的行：智能拼接
                if current_paragraph:
                    last_text = current_paragraph[-1]
                    if not last_text:
                        current_paragraph.append(row_text)
                    elif last_text[-1] in ('。', '；', '：', '！', '？', '.', ';', ':', '!', '?'):
                        # 上一行以句号等结尾 → 加空格
                        current_paragraph.append(' ' + row_text)
                    elif last_text[-1] in ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9') and row_text and '\u4e00' <= row_text[0] <= '\u9fff':
                        # 上一行以数字结尾，下一行以中文开头 → 加空格（如 "5.1.1" + "民用建筑"）
                        current_paragraph.append(' ' + row_text)
                    else:
                        # 同一句内换行，直接拼接
                        current_paragraph.append(row_text)
                else:
                    current_paragraph.append(row_text)
                row_idx += 1

        # 输出最后一个段落
        if current_paragraph:
            md_parts.append("".join(current_paragraph))

        return "\n\n".join(md_parts)

    def _clean_ocr_text(self, text: str) -> str:
        """清理 OCR 常见误识别字符"""
        # 清理页码标记：·4·、- 4 -、·12·、.6· 等
        text = re.sub(r'^[·.\-—\s]*\d{1,3}[·.\-—\s]*$', '', text.strip()).strip()
        # 归一化条文号中的空格：5. 1. 1 → 5.1.1（数字+点+空格+数字 → 去空格）
        text = re.sub(r'(\d)\.\s+(?=\d)', r'\1.', text)
        # 归一化条文号字母后缀的空格：5.3.1 A → 5.3.1A（条文号+空格+单个字母）
        text = re.sub(r'(\d+\.\d+(?:\.\d+)*)\s+([A-Za-z])\s*(?=[\u4e00-\u9fff，。；：])', r'\1\2', text)
        # 修复 OCR 对特殊标记的误识别（√c、−√i 等乱码组合）
        text = re.sub(r'[−–—]\s*[√✓✔]\s*[cCgGiIaA]', '', text)
        text = re.sub(r'[√✓✔]\s*[cCgGiIaA]', '', text)
        text = re.sub(r'[√✓✔]{2,}', '', text)
        text = re.sub(r'\(\s*[−–—]\s*[:：]\s*\)', '', text)  # (−:) 乱码
        # 清理孤立的 √ 和 ✓ 符号（OCR 误识别的标记符号）
        text = re.sub(r'\s*[√✓✔]\s*', ' ', text)
        # 清理 OCR 识别的表格横线分隔符
        if re.match(r'^[—\-–_=\s]+$', text):
            return ''
        # 修复 OCR 标点误识别
        text = re.sub(r'(\d)\s*一\s*(\d)', r'\1—\2', text)
        # 修复常见 OCR 标点错误
        text = text.replace('，，', '，')
        text = text.replace('。。', '。')
        text = re.sub(r'\s+([，。；：！？、）】」』])', r'\1', text)
        text = re.sub(r'([（【「『])\s+', r'\1', text)
        # 修复面积/体积单位：m" → m², m2 → m², m3 → m³
        text = re.sub(r'm\s*["\u201d\u2019`]', 'm²', text)
        text = re.sub(r'm\s*[2²]', 'm²', text)
        text = re.sub(r'm\s*[3³]', 'm³', text)
        # 修复 OCR 误识别的 ㎡（U+33A1）→ m²
        text = text.replace('㎡', 'm²')
        # 修复 "2㎡" 这种条款编号+单位误识别：数字+㎡ 且后面没有更多内容 → 仅保留数字
        text = re.sub(r'^(\d{1,2})m²$', r'\1', text)
        # 统一数字与单位之间有空格：50m → 50 m, 24m → 24 m
        # 注意：Python3 中 \b 对中文字符无效（中文也是 \w），所以用负向前瞻替代 m\b
        text = re.sub(r'(\d)\s*(m²|m³|m(?![a-zA-Z\d²³])|lx(?![a-zA-Z\d])|h(?![a-zA-Z\d])|L/s)', r'\1 \2', text)
        # 面积上下文修复：在面积相关关键词附近，"数字 m" 应为 "数字 m²"
        # 匹配：面积/占地/场地/净面积 + 任意中间文字 + 数字 + m（非m²/m³）
        area_keywords = r'(?:面积|占地|场地|净面积|总建筑面积|建筑面|使用面|营业面|基底面)'
        _distance_kw = r'(?:宽度|距离|高度|直径|半径|长度|厚度|深度|净宽|短边)'
        def _replace_area_local(m):
            full = m.group(0)
            num = m.group(2)
            before_num = full[:full.rfind(num)]
            if re.search(_distance_kw, before_num):
                return full
            return full.replace(f'{num} m', f'{num} m²', 1)
        text = re.sub(
            rf'({area_keywords}[^。；？！]*?(\d+\.?\d*)\s+m(?![a-zA-Z\d²³]))',
            _replace_area_local,
            text
        )
        # 修复照度单位：Ix → lx (勒克斯), "1 x" → "lx"
        text = re.sub(r'(\d+\.?\d*)\s*Ix\b', r'\1 lx', text)
        text = re.sub(r'(\d+\.?\d*)\s*1\s*x\b', r'\1 lx', text)
        # 修复 "mo" → "m"（OCR 误识别）
        text = re.sub(r'(\d+\.?\d*)\s*mo\b', r'\1 m', text)
        # 修复挤压在一起的数字（如 "0.751.00" → "0.75 1.00"）
        text = re.sub(r'(\d+\.\d+)(\d+\.\d+)', r'\1 \2', text)
        # 修复 OCR 常见汉字误识别
        text = text.replace('厂播', '广播')
        text = text.replace('厂泛', '广泛')
        text = text.replace('未用', '采用')
        # 统一术语：老年人设施 → 老年人照料设施
        text = re.sub(r'老年人(?!照料)(设施|建筑)', r'老年人照料\1', text)
        # 清理残留的特殊符号
        text = re.sub(r'[□■◇◆○●△▲▽▼☆★]', '', text)
        # 清理 OCR 误识别的页码标记（如 "·3·"、"- 3 -"）
        text = re.sub(r'^\s*[·\-—]\s*\d+\s*[·\-—]\s*$', '', text)
        # 注意：不再清理孤立的页码数字（如 "3" 单独一行），
        # 因为这会误杀列表编号（1、2、3等），页码在全局处理中清理
        # 清理表格单元格中多余的 | 符号（OCR 误识别的表格线）
        text = text.replace('|', '')
        return text.strip()

    def _group_into_rows(self, items: list, y_tolerance: int = 15) -> list:
        """将 OCR 项按 y 坐标分组为行"""
        if not items:
            return []

        rows = []
        current_row = [items[0]]
        current_y = items[0]['cy']

        for item in items[1:]:
            if abs(item['cy'] - current_y) <= y_tolerance:
                current_row.append(item)
            else:
                # 行内按 x 排序
                current_row.sort(key=lambda it: it['x1'])
                rows.append(current_row)
                current_row = [item]
                current_y = item['cy']

        if current_row:
            current_row.sort(key=lambda it: it['x1'])
            rows.append(current_row)

        return rows

    def _detect_table_regions(self, rows: list, img_width: int) -> list:
        """检测表格区域：基于列间距和表标记两种策略"""
        if len(rows) < 2:
            return []

        table_regions = []

        # 策略1：基于列间距检测（原有逻辑，降低阈值）
        min_col_gap = img_width * 0.015  # 进一步降低列间最小间隔
        row_col_counts = []
        for row in rows:
            if len(row) <= 1:
                row_col_counts.append(1)
                continue
            # 如果行首是条文号（如"5.1.1"），则视为单列（条文号+正文）
            first_text = row[0]['text']
            if re.match(r'^\d+\.\d+', first_text) and len(row) == 2:
                row_col_counts.append(1)
                continue
            # 如果行首是纯数字1-20（列表编号），则视为单列（编号+内容）
            if re.match(r'^\d{1,2}$', first_text) and len(row) == 2:
                row_col_counts.append(1)
                continue
            cols = 1
            for i in range(1, len(row)):
                gap = row[i]['x1'] - row[i-1]['x2']
                if gap > min_col_gap:
                    cols += 1
            row_col_counts.append(cols)

        # 找到连续多列的行（>=2列视为表格候选）
        i = 0
        while i < len(row_col_counts):
            if row_col_counts[i] >= 2:
                start = i
                # 检查是否有"表"字标记在前面几行
                has_table_marker = False
                for lookback in range(max(0, start - 3), start):
                    for item in rows[lookback]:
                        if re.match(r'^表\s*\d', item['text']):
                            has_table_marker = True
                            break
                    if has_table_marker:
                        start = lookback
                        break

                while i < len(row_col_counts) and row_col_counts[i] >= 2:
                    i += 1
                # 允许表格末尾有1行单列（如注释行）
                if i < len(row_col_counts) and row_col_counts[i] == 1:
                    row_text = ''.join(it['text'] for it in rows[i])
                    if len(row_text) < 30:
                        i += 1
                end = i - 1
                if end - start >= 1:
                    table_regions.append({'start': start, 'end': end})
            else:
                i += 1

        # 策略2：基于"表 X.X.X"标记检测表格区域
        # 扫描所有行，找到"表"标记行，将其后连续的短行视为表格
        for row_idx, row in enumerate(rows):
            row_text = ''.join(it['text'] for it in row)
            if not re.match(r'^表\s*\d', row_text):
                continue
            # 检查该标记是否已被策略1覆盖
            already_covered = any(tr['start'] <= row_idx <= tr['end'] for tr in table_regions)
            if already_covered:
                continue
            # 从标记行开始，向后扫描，找到表格结束位置
            table_start = row_idx
            table_end = row_idx
            for j in range(row_idx + 1, min(row_idx + 50, len(rows))):
                j_row = rows[j]
                j_text = ''.join(it['text'] for it in j_row).strip()
                # 续表标记，继续
                if re.match(r'^续表', j_text):
                    table_end = j
                    continue
                # 空行或纯分隔线，跳过但继续
                if not j_text or re.match(r'^[—\-–_=]+$', j_text):
                    continue
                # 注释行（以"注"开头），纳入表格但可能是最后一行
                if re.match(r'^注[：:]', j_text):
                    table_end = j
                    break
                # 遇到新条目号（如 5.1.2）或新表标记，表格结束
                if re.match(r'^\d+\.\d+', j_text) or re.match(r'^表\s*\d', j_text):
                    break
                # 遇到长段落文本（超过80字且无分号/句号分隔的短句），视为正文，表格结束
                if len(j_text) > 80 and '；' not in j_text and '。' not in j_text:
                    break
                # 遇到标题标记（##），表格结束
                if j_text.startswith('#'):
                    break
                # 否则视为表格行
                table_end = j
            if table_end > table_start:
                table_regions.append({'start': table_start, 'end': table_end})

        # 按起始行排序
        table_regions.sort(key=lambda tr: tr['start'])
        return table_regions

    def _merge_nearby_tables(self, table_regions: list, rows: list) -> list:
        """合并相邻的表格区域（间隔不超过2行非表格行的视为同一表格）。
        同时合并续表：如果间隔行包含"续表"标记，则合并。"""
        if len(table_regions) <= 1:
            return table_regions

        merged = [table_regions[0]]
        for tr in table_regions[1:]:
            prev = merged[-1]
            gap = tr['start'] - prev['end'] - 1
            # 检查间隔行中是否有"续表"标记
            has_continuation = False
            for row_idx in range(prev['end'] + 1, tr['start']):
                if row_idx < len(rows):
                    row_text = ''.join(it['text'] for it in rows[row_idx])
                    if re.match(r'^续表', row_text):
                        has_continuation = True
                        break
            if gap <= 2 or has_continuation:
                prev['end'] = tr['end']
            else:
                merged.append(tr)
        return merged

    def _render_table(self, rows: list, start_idx: int, end_idx: int) -> str:
        """将表格区域渲染为 Markdown 表格"""
        table_rows = rows[start_idx:end_idx + 1]

        # 预处理：识别并提取表标题行（如"表 5.1.1 xxx"），跳过续表标记行
        # 将条文号行（如"5.1.1 民用建筑..."）从表格中移出
        table_title = ""
        pre_table_lines = []  # 表格前的正文行
        filtered_rows = []
        for row in table_rows:
            row_text = ''.join(it['text'] for it in row)
            # 表标题行（如"表 5.1.1 建筑物的耐火等级"）
            if re.match(r'^表\s*\d', row_text) and len(row) <= 2:
                table_title = row_text.strip()
                continue
            # 续表标记行（如"续表 5.1.1"）
            if re.match(r'^续表', row_text):
                continue
            # 条文号行（如"5.1.1 民用建筑根据..."），移出表格
            if re.match(r'^\d+\.\d+', row_text):
                pre_table_lines.append(row_text.strip())
                continue
            filtered_rows.append(row)

        table_rows = filtered_rows
        if not table_rows:
            return ""

        # 收集所有列的 x 坐标边界，确定列数
        all_x_positions = set()
        for row in table_rows:
            for item in row:
                all_x_positions.add(int(item['x1']))

        if len(all_x_positions) < 2:
            lines = []
            if table_title:
                lines.append(table_title)
            for row in table_rows:
                lines.append(" ".join(it['text'] for it in row))
            return "\n".join(lines)

        # 根据 x 坐标聚类确定列位置
        sorted_x = sorted(all_x_positions)
        # 动态计算 gap_threshold：基于 x 坐标分布
        if len(sorted_x) > 1:
            gaps = [sorted_x[i+1] - sorted_x[i] for i in range(len(sorted_x)-1)]
            median_gap = sorted(gaps)[len(gaps)//2]
            gap_threshold = max(50, min(median_gap * 0.6, 150))
        else:
            gap_threshold = 80

        col_boundaries = self._cluster_x_positions(sorted_x, gap_threshold=gap_threshold)

        if len(col_boundaries) < 2:
            lines = []
            if table_title:
                lines.append(table_title)
            for row in table_rows:
                lines.append(" ".join(it['text'] for it in row))
            return "\n".join(lines)

        num_cols = len(col_boundaries)

        # 将每个文本项分配到对应的列
        md_table = []
        for row_idx, row in enumerate(table_rows):
            cells = [''] * num_cols
            for item in row:
                col_idx = self._find_nearest_col(item['cx'], col_boundaries)
                if cells[col_idx]:
                    cells[col_idx] += ' ' + item['text']
                else:
                    cells[col_idx] = item['text']
            cells = [c.strip() for c in cells]
            md_table.append(cells)

        # 移除纯横线行（OCR 识别的表格分隔线）
        md_table = [row for row in md_table if not all(
            re.match(r'^[—\-–_=]*$', cell) for cell in row
        )]

        if not md_table:
            return ""

        # 合并空列：移除所有行中都为空的列，只保留有内容的列
        col_has_content = [any(row[i].strip() for row in md_table) for i in range(num_cols)]
        keep_cols = [i for i, has in enumerate(col_has_content) if has]

        # 如果所有列都有内容或所有列都为空，不做合并
        if 0 < len(keep_cols) < num_cols:
            new_table = []
            for row in md_table:
                new_row = [row[i].strip() for i in keep_cols]
                new_table.append(new_row)
            md_table = new_table
            num_cols = len(keep_cols)

        # 表格质量检查：如果空列占比过高，降级为结构化文本
        total_cells = len(md_table) * num_cols
        empty_cells = sum(1 for row in md_table for cell in row if not cell.strip())
        empty_ratio = empty_cells / total_cells if total_cells > 0 else 0

        # 如果空列占比超过50%，说明列对齐严重错乱，降级为结构化文本
        if empty_ratio > 0.5 and num_cols > 3:
            lines = []
            for pre_line in pre_table_lines:
                lines.append(pre_line)
            if table_title:
                lines.append(table_title)
            for row in md_table:
                # 每行用 | 分隔，但不使用 Markdown 表格语法
                row_text = ' | '.join(cell.strip() for cell in row if cell.strip())
                if row_text:
                    lines.append(row_text)
            return '\n'.join(lines)

        # 生成 Markdown 表格
        lines = []
        # 先输出条文号行（表格前的正文）
        for pre_line in pre_table_lines:
            lines.append(pre_line)
        if table_title:
            lines.append(table_title)
        if md_table:
            header = '| ' + ' | '.join(md_table[0]) + ' |'
            separator = '| ' + ' | '.join(['---'] * num_cols) + ' |'
            lines.append(header)
            lines.append(separator)
            for cells in md_table[1:]:
                line = '| ' + ' | '.join(cells) + ' |'
                lines.append(line)

        return '\n'.join(lines)

    def _cluster_x_positions(self, sorted_x: list, gap_threshold: int = 80) -> list:
        """将 x 坐标聚类为列边界"""
        if not sorted_x:
            return []

        clusters = [sorted_x[0]]
        for x in sorted_x[1:]:
            if x - clusters[-1] > gap_threshold:
                clusters.append(x)
        return clusters

    def _find_nearest_col(self, cx: float, col_boundaries: list) -> int:
        """找到中心 x 坐标最近的列索引"""
        min_dist = float('inf')
        nearest = 0
        for i, bx in enumerate(col_boundaries):
            dist = abs(cx - bx)
            if dist < min_dist:
                min_dist = dist
                nearest = i
        return nearest

    def _convert_json(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return ConversionResult(success=False, original_name=filename, error=f"无效JSON: {str(e)}")

        title = Path(filename).stem
        parts = [f"# {title}\n"]

        if isinstance(data, list) and data and isinstance(data[0], dict):
            all_keys = []
            for item in data:
                for k in item.keys():
                    if k not in all_keys:
                        all_keys.append(k)

            parts.append(f"共 {len(data)} 条记录\n")
            parts.append("| " + " | ".join(all_keys) + " |")
            parts.append("| " + " | ".join(["---"] * len(all_keys)) + " |")
            for item in data:
                cells = []
                for k in all_keys:
                    val = item.get(k, "")
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val, ensure_ascii=False)
                    cells.append(str(val).replace("\n", " ").replace("|", "\\|"))
                parts.append("| " + " | ".join(cells) + " |")
            parts.append("")
            parts.append("## 原始数据\n")
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
            if len(formatted) > 5000:
                formatted = formatted[:5000] + "\n... (数据已截断)"
            parts.append("```json\n" + formatted + "\n```")

        elif isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    all_keys = []
                    for item in value:
                        for k in item.keys():
                            if k not in all_keys:
                                all_keys.append(k)
                    parts.append(f"## {key}\n")
                    parts.append(f"共 {len(value)} 条记录\n")
                    parts.append("| " + " | ".join(all_keys) + " |")
                    parts.append("| " + " | ".join(["---"] * len(all_keys)) + " |")
                    for item in value:
                        cells = []
                        for k in all_keys:
                            val = item.get(k, "")
                            if isinstance(val, (dict, list)):
                                val = json.dumps(val, ensure_ascii=False)
                            cells.append(str(val).replace("\n", " ").replace("|", "\\|"))
                        parts.append("| " + " | ".join(cells) + " |")
                    parts.append("")
                else:
                    parts.append(f"## {key}\n")
                    if isinstance(value, (dict, list)):
                        formatted = json.dumps(value, indent=2, ensure_ascii=False)
                        if len(formatted) > 3000:
                            formatted = formatted[:3000] + "\n... (数据已截断)"
                        parts.append("```json\n" + formatted + "\n```")
                    else:
                        parts.append(str(value))
                    parts.append("")

        else:
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
            parts.append("```json\n" + formatted + "\n```")

        md_text = "\n".join(parts)
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=md_text,
            metadata={"source_format": "json", "top_level_keys": list(data.keys()) if isinstance(data, dict) else []},
        )

    def _convert_html(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        title = Path(filename).stem

        if self._has_markdownify and self._has_bs4:
            try:
                from bs4 import BeautifulSoup
                from markdownify import markdownify as md, MarkdownConverter

                soup = BeautifulSoup(text, "html.parser")

                for tag in soup(["script", "style", "nav", "footer", "header", "noscript",
                                  "iframe", "svg", "aside", "form", "button", "input",
                                  "select", "textarea"]):
                    tag.decompose()

                for tag in soup.find_all(class_=re.compile(
                    r'(sidebar|ad|advertisement|banner|cookie|popup|modal|overlay|'
                    r'navigation|breadcrumb|pagination|comment|social|share|footer|'
                    r'widget|promo|newsletter|subscribe|related|recommend|tooltip)',
                    re.I
                )):
                    tag.decompose()

                for tag in soup.find_all(id=re.compile(
                    r'(sidebar|ad|advertisement|banner|cookie|popup|modal|overlay|'
                    r'navigation|breadcrumb|pagination|comment|social|share|footer|'
                    r'widget|promo|newsletter|subscribe|related|recommend|tooltip)',
                    re.I
                )):
                    tag.decompose()

                for tag in soup.find_all(['div', 'span', 'section', 'article']):
                    if tag.get_text(strip=True) == '' and not tag.find(['img', 'table', 'iframe']):
                        tag.decompose()

                body = soup.find("body")
                html_content = str(body) if body else str(soup)

                class CleanMarkdownConverter(MarkdownConverter):
                    def convert_table(self, el, text, convert_as_inline):
                        rows = el.find_all('tr')
                        if not rows:
                            return ''
                        result_lines = []
                        header_row = rows[0]
                        headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
                        if not headers:
                            return text
                        result_lines.append('| ' + ' | '.join(headers) + ' |')
                        result_lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')
                        for row in rows[1:]:
                            cells = [td.get_text(strip=True).replace('\n', ' ') for td in row.find_all(['th', 'td'])]
                            while len(cells) < len(headers):
                                cells.append('')
                            result_lines.append('| ' + ' | '.join(cells[:len(headers)]) + ' |')
                        return '\n' + '\n'.join(result_lines) + '\n'

                    def convert_img(self, el, text, convert_as_inline):
                        alt = el.get('alt', '') or ''
                        src = el.get('src', '') or ''
                        if not src:
                            return ''
                        return f'![{alt}]({src})'

                    def convert_a(self, el, text, convert_as_inline):
                        href = el.get('href', '') or ''
                        if not href or href.startswith('#') or href.startswith('javascript:'):
                            return text
                        return f'[{text}]({href})'

                md_text = md(html_content, heading_style="ATX", bullets="-",
                             convert=['table', 'img', 'a'],
                             convert_table=CleanMarkdownConverter.convert_table)

                md_text = re.sub(r'\n{3,}', '\n\n', md_text)
                md_text = re.sub(r'^\s+|\s+$', '', md_text, flags=re.MULTILINE)
                md_text = md_text.strip()

                if not md_text.startswith('#'):
                    md_text = f"# {title}\n\n{md_text}"

                return ConversionResult(
                    success=True,
                    original_name=filename,
                    markdown_content=md_text,
                    metadata={"source_format": "html", "engine": "markdownify", "char_count": len(md_text)},
                )
            except Exception as e:
                logger.warning(f"markdownify转换失败，回退到纯文本: {e}")

        if self._has_bs4:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript",
                              "iframe", "svg", "aside", "form", "button"]):
                tag.decompose()

            for h in soup.find_all(re.compile(r"^h[1-6]$")):
                level = int(h.name[1])
                h.replace_with(f"{'#' * level} {h.get_text(strip=True)}")

            for li in soup.find_all("li"):
                li.replace_with(f"- {li.get_text(strip=True)}")

            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                if not rows:
                    continue
                table_md_lines = []
                for i, row in enumerate(rows):
                    cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
                    table_md_lines.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        table_md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                table.replace_with("\n".join(table_md_lines))

            for a in soup.find_all("a"):
                href = a.get("href", "")
                link_text = a.get_text(strip=True)
                if href and not href.startswith("#") and not href.startswith("javascript:"):
                    a.replace_with(f"[{link_text}]({href})")

            body = soup.find("body")
            plain = body.get_text("\n", strip=True) if body else soup.get_text("\n", strip=True)
            plain = re.sub(r'\n{3,}', '\n\n', plain)
        else:
            plain = re.sub(r"<[^>]+>", "", text)
            plain = re.sub(r"\n\s*\n", "\n\n", plain)

        md = f"# {title}\n\n{plain}"
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=md,
            metadata={"source_format": "html", "engine": "basic", "char_count": len(plain)},
        )

    def _convert_svg(self, content: bytes, filename: str) -> ConversionResult:
        text = self._decode_text(content)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return ConversionResult(success=False, original_name=filename, error=f"SVG 解析失败: {exc}")

        extracted = []
        for element in root.iter():
            kind = element.tag.rsplit("}", 1)[-1]
            value = " ".join(part.strip() for part in element.itertext() if part.strip()).strip()
            if value and kind in {"title", "desc", "text", "tspan"} and value not in extracted:
                extracted.append(value)

        title = Path(filename).stem
        body = "\n\n".join(extracted) if extracted else f"*[SVG 图片: {filename}，未检测到文本内容]*"
        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=f"# {title}\n\n{body}",
            metadata={"source_format": "svg", "text_block_count": len(extracted)},
        )

    def _convert_image(self, content: bytes, filename: str) -> ConversionResult:
        """图片 OCR 转换，优先 PaddleOCR，降级 Tesseract"""
        if not settings.local_ocr_enabled:
            return self._ocr_disabled_result(filename)
        title = Path(filename).stem
        ext = Path(filename).suffix.lower()

        ocr_text = ""
        ocr_engine = "none"

        if self._has_pil:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(content))

                # 优先 PaddleOCR（使用缓存实例）
                paddle_ocr = self._get_paddle_ocr()
                if paddle_ocr is not None:
                    try:
                        import numpy as np
                        img_array = np.array(img)
                        result = paddle_ocr.ocr(img_array, cls=True)
                        if result and result[0]:
                            lines = []
                            for line in result[0]:
                                if line and len(line) >= 2:
                                    text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                                    lines.append(text)
                            ocr_text = "\n".join(lines)
                            ocr_engine = "paddleocr"
                    except Exception as e:
                        logger.warning(f"PaddleOCR 图片识别失败: {e}")

                # 降级 Tesseract
                if not ocr_text.strip() and self._has_tesseract:
                    try:
                        import pytesseract
                        try:
                            ocr_text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                            ocr_engine = "tesseract_chi_sim+eng"
                        except Exception:
                            ocr_text = pytesseract.image_to_string(img, lang="eng")
                            ocr_engine = "tesseract_eng"
                    except Exception as e:
                        ocr_text = f"[OCR失败: {str(e)}]"
                        ocr_engine = "tesseract_failed"
            except Exception as e:
                ocr_text = f"[图片处理失败: {str(e)}]"

        if ocr_text.strip():
            md = f"# {title}\n\n{ocr_text.strip()}"
        else:
            md = f"# {title}\n\n*[图片文件: {filename}，无可用的OCR引擎]*"

        return ConversionResult(
            success=True,
            original_name=filename,
            markdown_content=md,
            metadata={"source_format": "image", "image_type": ext, "ocr_engine": ocr_engine},
        )

    def _decode_text(self, content: bytes) -> str:
        if self._has_chardet:
            try:
                import chardet
                result = chardet.detect(content)
                encoding = result.get("encoding")
                if encoding:
                    try:
                        return content.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        pass
            except Exception:
                pass

        for encoding in ["utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1"]:
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("latin-1", errors="replace")


file_converter = FileConverter()
