# 文件解析器模块
"""
REITs基金法律文件解析器包

提供DOCX、PDF、XLSX三种格式的文件解析能力，
以及文件夹扫描和文件类型检测等工具函数。
"""

# DOCX解析器
from .docx_parser import (
    parse_docx,
    ParsedDocument,
    Section,
    TableData,
    extract_chapter_titles,
    extract_tables,
    get_section_content,
)

# PDF解析器
from .pdf_parser import (
    parse_pdf,
    extract_text_by_pages,
    is_scanned_pdf,
    get_pdf_metadata,
)

# Excel解析器
from .xlsx_parser import (
    parse_xlsx,
    extract_sheet_data,
    list_sheets,
)

# 工具函数
from .utils import (
    detect_file_type,
    scan_folder,
    format_file_size,
    get_file_info,
    is_supported_file,
)

__all__ = [
    # 数据类
    'ParsedDocument',
    'Section',
    'TableData',
    # DOCX
    'parse_docx',
    'extract_chapter_titles',
    'extract_tables',
    'get_section_content',
    # PDF
    'parse_pdf',
    'extract_text_by_pages',
    'is_scanned_pdf',
    'get_pdf_metadata',
    # Excel
    'parse_xlsx',
    'extract_sheet_data',
    'list_sheets',
    # 工具
    'detect_file_type',
    'scan_folder',
    'format_file_size',
    'get_file_info',
    'is_supported_file',
]
