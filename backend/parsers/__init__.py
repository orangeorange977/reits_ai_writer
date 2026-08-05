# 文件解析器模块
"""
文件解析工具包

旧管线的 DOCX/PDF/XLSX 结构化解析器已随步骤 2.6 删除；
现仅保留文件夹扫描和文件类型检测等工具函数（folders 路由使用）。
"""

# 工具函数
from .utils import (
    detect_file_type,
    scan_folder,
    format_file_size,
    get_file_info,
    is_supported_file,
)

__all__ = [
    'detect_file_type',
    'scan_folder',
    'format_file_size',
    'get_file_info',
    'is_supported_file',
]
