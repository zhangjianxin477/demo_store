"""路径安全校验模块 - 防止文件路径遍历攻击。

所有文件操作前应调用 validate_path() 校验路径在允许范围内。
使用 os.path.realpath + 前缀检查，确保解析所有符号链接和相对路径。
"""

import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 危险文件扩展名黑名单
DANGEROUS_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.com', '.msi', '.scr',
    '.sh', '.bash', '.zsh', '.fish',
    '.py', '.pyc', '.pyo', '.rb', '.pl', '.php',
    '.dll', '.so', '.dylib',
    '.vbs', '.vbe', '.wsf', '.wsh',
    '.ps1', '.psm1',
}

# 允许的文件扩展名白名单（文档相关）
ALLOWED_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.txt', '.md', '.markdown', '.csv', '.tsv',
    '.html', '.htm', '.xml', '.json', '.yaml', '.yml',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg',
    '.rtf', '.odt', '.ods', '.odp',
    '.zip', '.tar', '.gz', '.bz2', '.7z',
}


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除路径分隔符和危险字符。

    Args:
        filename: 原始文件名

    Returns:
        清理后的安全文件名

    Raises:
        ValueError: 文件名为空或仅包含危险字符
    """
    if not filename:
        raise ValueError("文件名不能为空")

    # 取 basename，移除路径部分
    basename = os.path.basename(filename)

    # 替换危险字符为下划线
    basename = re.sub(r'[<>:"|?*\x00-\x1f]', '_', basename)

    # 移除前导点和空格（防止隐藏文件攻击）
    basename = basename.lstrip('. ')

    # 保留中文、字母、数字、下划线、连字符、点号
    basename = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', basename)

    if not basename or basename.startswith('.'):
        raise ValueError(f"无效的文件名: {filename}")

    # 限制文件名长度
    name, ext = os.path.splitext(basename)
    if len(name) > 200:
        name = name[:200]
    basename = name + ext

    return basename


def validate_path(base_dir: str, filename: str, check_extension: bool = True) -> str:
    """校验文件路径在允许范围内，防止路径遍历攻击。

    使用 os.path.realpath 解析所有符号链接和相对路径组件，
    然后检查结果路径是否在 base_dir 范围内。

    Args:
        base_dir: 允许的基础目录（绝对路径）
        filename: 要校验的文件名或相对路径
        check_extension: 是否检查文件扩展名白名单

    Returns:
        校验通过的绝对路径

    Raises:
        ValueError: 路径遍历攻击或非法文件名
    """
    if not base_dir:
        raise ValueError("基础目录不能为空")

    # `validate_path` accepts a filename, not a nested path. Reject traversal
    # syntax before basename sanitization so an attack is not silently turned
    # into a different, apparently valid filename.
    raw_name = str(filename or "")
    if "/" in raw_name or "\\" in raw_name or any(part == ".." for part in raw_name.split("/")):
        raise ValueError(f"非法的文件路径: {filename}")

    # 确保 base_dir 是绝对路径并解析
    base_real = os.path.realpath(base_dir)

    # 清理文件名
    safe_name = sanitize_filename(filename)

    # 构建完整路径
    full_path = os.path.join(base_real, safe_name)

    # 解析真实路径（处理 ..、符号链接等）
    real_path = os.path.realpath(full_path)

    # 前缀检查：确保真实路径在 base_dir 内
    if not real_path.startswith(base_real + os.sep) and real_path != base_real:
        logger.warning(f"路径遍历攻击检测: base={base_real}, path={real_path}, filename={filename}")
        raise ValueError(f"非法的文件路径: {filename}")

    # 扩展名白名单检查
    if check_extension:
        ext = os.path.splitext(safe_name)[1].lower()
        if ext in DANGEROUS_EXTENSIONS:
            raise ValueError(f"不允许的文件类型: {ext}")
        # 如果不在白名单中，给出警告但不阻止（兼容性考虑）
        if ext and ext not in ALLOWED_EXTENSIONS:
            logger.warning(f"文件扩展名不在白名单中: {ext} (file={safe_name})")

    return real_path


def validate_directory_path(base_dir: str, dir_path: str) -> str:
    """校验目录路径在允许范围内。

    Args:
        base_dir: 允许的基础目录
        dir_path: 要校验的目录路径

    Returns:
        校验通过的绝对路径

    Raises:
        ValueError: 路径遍历攻击
    """
    base_real = os.path.realpath(base_dir)

    # 清理目录名
    safe_dir = re.sub(r'[<>:"|?*\x00-\x1f]', '_', dir_path)
    safe_dir = safe_dir.lstrip('. ')

    full_path = os.path.join(base_real, safe_dir)
    real_path = os.path.realpath(full_path)

    if not real_path.startswith(base_real + os.sep) and real_path != base_real:
        logger.warning(f"目录路径遍历攻击检测: base={base_real}, path={real_path}")
        raise ValueError(f"非法的目录路径: {dir_path}")

    return real_path


def safe_join(*path_parts: str) -> Optional[str]:
    """安全地拼接路径，防止路径遍历。

    Args:
        *path_parts: 路径组件

    Returns:
        拼接后的安全路径，如果检测到遍历则返回 None
    """
    if not path_parts:
        return None

    base = path_parts[0]
    result = os.path.realpath(base)

    for part in path_parts[1:]:
        # 检查每个组件不包含遍历字符
        if '..' in part or part.startswith('/') or part.startswith('\\'):
            logger.warning(f"路径组件包含遍历字符: {part}")
            return None
        next_path = os.path.join(result, part)
        next_real = os.path.realpath(next_path)
        if not next_real.startswith(result + os.sep) and next_real != result:
            return None
        result = next_real

    return result
