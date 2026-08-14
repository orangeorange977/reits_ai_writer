"""文件解析器公共工具函数

提供文件类型检测、文件夹扫描、大小格式化等通用功能。
"""

import os
import logging
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    '.docx': 'docx',
    '.doc': 'doc',
    '.pdf': 'pdf',
    '.xlsx': 'xlsx',
    '.xls': 'xls',
}

# 默认扫描的文件格式
DEFAULT_SCAN_EXTENSIONS = ['.docx', '.pdf', '.xlsx']


def detect_file_type(file_path: str) -> str:
    """根据扩展名判断文件类型

    Args:
        file_path: 文件路径

    Returns:
        文件类型字符串（如 'docx', 'pdf', 'xlsx'），
        未知类型返回 'unknown'
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    return SUPPORTED_EXTENSIONS.get(ext, 'unknown')


def scan_folder(folder_path: str, extensions: List[str] = None) -> List[Dict]:
    """递归扫描文件夹，返回符合条件的文件列表

    Args:
        folder_path: 要扫描的文件夹路径
        extensions: 要筛选的文件扩展名列表（如['.docx', '.pdf']），
                   None则使用默认列表

    Returns:
        文件信息列表 [{name, path, extension, size, modified_time, file_type}]
    """
    folder = Path(folder_path)

    if not folder.exists():
        logger.error(f"文件夹不存在: {folder_path}")
        return []

    if not folder.is_dir():
        logger.error(f"路径不是文件夹: {folder_path}")
        return []

    if extensions is None:
        extensions = DEFAULT_SCAN_EXTENSIONS

    # 标准化扩展名（确保以.开头，小写）
    normalized_exts = set()
    for ext in extensions:
        if not ext.startswith('.'):
            ext = '.' + ext
        normalized_exts.add(ext.lower())

    files: List[Dict] = []

    try:
        for file_path in folder.rglob('*'):
            if not file_path.is_file():
                continue

            ext = file_path.suffix.lower()
            if ext not in normalized_exts:
                continue

            try:
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "path": str(file_path),
                    "extension": ext,
                    "size": stat.st_size,
                    "size_formatted": format_file_size(stat.st_size),
                    "modified_time": datetime.fromtimestamp(
                        stat.st_mtime
                    ).strftime('%Y-%m-%d %H:%M:%S'),
                    "file_type": detect_file_type(str(file_path)),
                    "relative_path": str(file_path.relative_to(folder)),
                })
            except (OSError, PermissionError) as e:
                logger.warning(f"无法访问文件 {file_path}: {e}")

        # 按文件名排序
        files.sort(key=lambda f: f["name"])

        logger.info(f"扫描完成: {folder_path}, 找到{len(files)}个文件")
        return files

    except PermissionError as e:
        logger.error(f"无权限访问文件夹: {folder_path}, 错误: {e}")
        return []
    except Exception as e:
        logger.error(f"扫描文件夹时出错: {folder_path}, 错误: {e}")
        return []


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小为可读字符串

    Args:
        size_bytes: 文件大小（字节）

    Returns:
        格式化的大小字符串，如 "1.5 MB", "256 KB"
    """
    if size_bytes < 0:
        return "0 B"

    if size_bytes == 0:
        return "0 B"

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} B"
    else:
        return f"{size:.1f} {units[unit_index]}"


def get_file_info(file_path: str) -> Optional[Dict]:
    """获取单个文件的基本信息

    Args:
        file_path: 文件路径

    Returns:
        文件信息字典，文件不存在返回None
    """
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"文件不存在: {file_path}")
        return None

    try:
        stat = path.stat()
        return {
            "name": path.name,
            "path": str(path),
            "extension": path.suffix.lower(),
            "size": stat.st_size,
            "size_formatted": format_file_size(stat.st_size),
            "modified_time": datetime.fromtimestamp(
                stat.st_mtime
            ).strftime('%Y-%m-%d %H:%M:%S'),
            "file_type": detect_file_type(str(path)),
            "exists": True,
        }
    except Exception as e:
        logger.error(f"获取文件信息时出错: {e}")
        return None


def is_supported_file(file_path: str) -> bool:
    """判断文件是否为支持的格式

    Args:
        file_path: 文件路径

    Returns:
        True=支持的格式
    """
    return detect_file_type(file_path) != 'unknown'
