"""PDF文档解析器 - 提取PDF文本内容

使用PyMuPDF(fitz)库解析PDF文件，提取文本内容和元数据。
支持判断是否为扫描件，以及按页提取文本。
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path

# 复用docx_parser中的数据类
from .docx_parser import ParsedDocument, Section, TableData, TITLE_PATTERNS

logger = logging.getLogger(__name__)


# ============================================================
# 辅助函数
# ============================================================

def _detect_title_level(text: str) -> Optional[int]:
    """通过正则模式检测文本是否为标题，返回标题层级或None"""
    text = text.strip()
    if not text:
        return None
    for level, pattern in TITLE_PATTERNS:
        if re.match(pattern, text):
            return level
    return None


def _is_likely_heading(line: str) -> bool:
    """判断一行文本是否可能是标题（启发式）"""
    line = line.strip()
    if not line:
        return False
    # 长度限制：标题通常不超过80个字符
    if len(line) > 80:
        return False
    # 不以标点结尾（排除普通句子）
    if line.endswith(('。', '；', '，', '、', '：')):
        return False
    return _detect_title_level(line) is not None


# ============================================================
# 主要解析函数
# ============================================================

def parse_pdf(file_path: str) -> ParsedDocument:
    """主解析函数 - 解析PDF文件并返回结构化文档

    Args:
        file_path: PDF文件路径

    Returns:
        ParsedDocument: 解析后的文档对象
    """
    path = Path(file_path)

    # 基本验证
    if not path.exists():
        logger.error(f"文件不存在: {file_path}")
        return ParsedDocument(
            filename=path.name,
            file_path=str(path),
            file_size=0,
            total_pages=0,
            metadata={"error": "文件不存在"}
        )

    if path.suffix.lower() != '.pdf':
        logger.error(f"不支持的文件格式: {path.suffix}")
        return ParsedDocument(
            filename=path.name,
            file_path=str(path),
            file_size=int(path.stat().st_size),
            total_pages=0,
            metadata={"error": f"不支持的文件格式: {path.suffix}"}
        )

    try:
        import fitz  # PyMuPDF

        file_size = path.stat().st_size
        doc = fitz.open(str(path))

        logger.info(f"开始解析PDF文件: {path.name} ({file_size} bytes, {doc.page_count}页)")

        total_pages = doc.page_count
        all_text_parts: List[str] = []
        flat_sections: List[Section] = []
        current_section: Optional[Section] = None
        current_content_parts: List[str] = []

        for page_num in range(total_pages):
            page = doc[page_num]
            page_text = page.get_text("text")
            all_text_parts.append(page_text)

            # 逐行处理，识别标题
            lines = page_text.split('\n')
            for line in lines:
                line_stripped = line.strip()
                if not line_stripped:
                    continue

                heading_level = None
                if _is_likely_heading(line_stripped):
                    heading_level = _detect_title_level(line_stripped)

                if heading_level is not None:
                    # 保存之前的section
                    if current_section is not None:
                        current_section.content = '\n'.join(current_content_parts).strip()
                        flat_sections.append(current_section)

                    # 创建新section
                    current_section = Section(
                        title=line_stripped,
                        level=heading_level,
                        content="",
                        source_file=path.name,
                        page_range=f"第{page_num + 1}页",
                    )
                    current_content_parts = []
                else:
                    current_content_parts.append(line_stripped)

        # 处理最后一个section
        if current_section is not None:
            current_section.content = '\n'.join(current_content_parts).strip()
            flat_sections.append(current_section)
        elif current_content_parts:
            flat_sections.append(Section(
                title="(无标题)",
                level=0,
                content='\n'.join(current_content_parts).strip(),
                source_file=path.name,
            ))

        # 构建层级树
        sections_tree = _build_section_tree(flat_sections)

        # 提取元数据
        raw_text = '\n'.join(all_text_parts)
        metadata = get_pdf_metadata(file_path)

        doc.close()

        result = ParsedDocument(
            filename=path.name,
            file_path=str(path),
            file_size=file_size,
            total_pages=total_pages,
            sections=sections_tree,
            metadata=metadata,
            raw_text=raw_text,
        )

        logger.info(f"PDF解析完成: {path.name}, 共{total_pages}页, {len(flat_sections)}个章节")
        return result

    except ImportError:
        logger.error("PyMuPDF库未安装，请运行: pip install PyMuPDF")
        return ParsedDocument(
            filename=path.name,
            file_path=str(path),
            file_size=int(path.stat().st_size),
            total_pages=0,
            metadata={"error": "PyMuPDF库未安装"}
        )
    except Exception as e:
        logger.error(f"解析PDF文件时出错: {file_path}, 错误: {e}")
        return ParsedDocument(
            filename=path.name,
            file_path=str(path),
            file_size=int(path.stat().st_size) if path.exists() else 0,
            total_pages=0,
            metadata={"error": str(e)}
        )


def _build_section_tree(flat_sections: List[Section]) -> List[Section]:
    """将扁平化的sections列表构建为层级树结构"""
    if not flat_sections:
        return []

    root_sections: List[Section] = []
    stack: List[Section] = []

    for section in flat_sections:
        while stack and stack[-1].level >= section.level:
            stack.pop()

        if stack:
            stack[-1].children.append(section)
        else:
            root_sections.append(section)

        stack.append(section)

    return root_sections


def extract_text_by_pages(file_path: str) -> List[Dict]:
    """按页提取PDF文本

    Args:
        file_path: PDF文件路径

    Returns:
        页面数据列表 [{page_num, text, has_images}]
    """
    path = Path(file_path)

    if not path.exists():
        logger.error(f"文件不存在: {file_path}")
        return []

    try:
        import fitz

        doc = fitz.open(str(path))
        pages: List[Dict] = []

        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text("text").strip()
            image_list = page.get_images(full=True)

            pages.append({
                "page_num": page_num + 1,
                "text": text,
                "has_images": len(image_list) > 0,
                "image_count": len(image_list),
                "char_count": len(text),
            })

        doc.close()
        logger.info(f"按页提取完成: {path.name}, 共{len(pages)}页")
        return pages

    except ImportError:
        logger.error("PyMuPDF库未安装")
        return []
    except Exception as e:
        logger.error(f"按页提取PDF时出错: {e}")
        return []


def is_scanned_pdf(file_path: str) -> bool:
    """判断PDF是否为扫描件

    通过文本密度判断：如果大部分页面文字极少但有图片，
    则判定为扫描件。

    Args:
        file_path: PDF文件路径

    Returns:
        True=扫描件, False=非扫描件
    """
    path = Path(file_path)

    if not path.exists():
        logger.error(f"文件不存在: {file_path}")
        return False

    try:
        import fitz

        doc = fitz.open(str(path))
        total_pages = doc.page_count

        if total_pages == 0:
            doc.close()
            return False

        low_text_pages = 0
        TEXT_THRESHOLD = 50  # 每页少于50个字符视为低文本密度

        for page_num in range(min(total_pages, 10)):  # 检查前10页
            page = doc[page_num]
            text = page.get_text("text").strip()
            image_list = page.get_images(full=True)

            if len(text) < TEXT_THRESHOLD and len(image_list) > 0:
                low_text_pages += 1

        doc.close()

        checked_pages = min(total_pages, 10)
        # 如果超过60%的页面是低文本+有图片，判定为扫描件
        is_scanned = (low_text_pages / checked_pages) > 0.6

        if is_scanned:
            logger.info(f"检测到扫描件: {path.name}")
        else:
            logger.info(f"非扫描件: {path.name}")

        return is_scanned

    except ImportError:
        logger.error("PyMuPDF库未安装")
        return False
    except Exception as e:
        logger.error(f"判断扫描件时出错: {e}")
        return False


def get_pdf_metadata(file_path: str) -> Dict:
    """获取PDF元数据

    Args:
        file_path: PDF文件路径

    Returns:
        元数据字典 {pages, author, created, file_size, ...}
    """
    path = Path(file_path)

    if not path.exists():
        logger.error(f"文件不存在: {file_path}")
        return {"error": "文件不存在"}

    try:
        import fitz

        file_size = path.stat().st_size
        doc = fitz.open(str(path))

        metadata = {
            "pages": doc.page_count,
            "file_size": file_size,
            "author": doc.metadata.get("author", "") if doc.metadata else "",
            "title": doc.metadata.get("title", "") if doc.metadata else "",
            "subject": doc.metadata.get("subject", "") if doc.metadata else "",
            "creator": doc.metadata.get("creator", "") if doc.metadata else "",
            "producer": doc.metadata.get("producer", "") if doc.metadata else "",
            "created": doc.metadata.get("creationDate", "") if doc.metadata else "",
            "modified": doc.metadata.get("modDate", "") if doc.metadata else "",
            "format": doc.metadata.get("format", "") if doc.metadata else "",
        }

        doc.close()
        return metadata

    except ImportError:
        logger.error("PyMuPDF库未安装")
        return {"error": "PyMuPDF库未安装", "file_size": int(path.stat().st_size)}
    except Exception as e:
        logger.error(f"获取PDF元数据时出错: {e}")
        return {"error": str(e)}
