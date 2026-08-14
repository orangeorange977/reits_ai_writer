"""章节组装器 - 负责章节排序、编号和文档结构管理

将多个独立渲染的章节内容按正确顺序组装为完整文档，
并提供目录生成、交叉引用验证等辅助功能。
"""

import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 章节顺序定义
CHAPTER_ORDER = [
    "chapter1", "chapter2", "chapter3", "chapter4",
    "chapter5", "chapter6", "chapter7",
]

# 中文数字标题映射
CHINESE_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


class ChapterComposer:
    """将多个章节组装为完整文档结构

    职责：
    - 按章节ID排序组装
    - 生成文档标题页
    - 生成目录
    - 验证交叉引用
    """

    def __init__(self):
        """初始化组装器"""
        self._chapter_titles = {}

    def compose(self, chapters_content: Dict[str, str], project_name: str = "") -> str:
        """将各章节内容按顺序组装

        Args:
            chapters_content: {chapter_id: rendered_text}
            project_name: 项目名称（用于标题页）

        Returns:
            完整文档文本（按章节顺序排列）
        """
        parts = []

        # 1. 添加文档标题页
        if project_name:
            header = self.add_document_header(project_name)
            parts.append(header)

        # 2. 提取章节标题（用于目录生成）
        self._extract_chapter_titles(chapters_content)

        # 3. 生成目录
        toc = self.generate_toc(chapters_content)
        if toc:
            parts.append(toc)

        # 4. 按顺序添加各章节内容
        for chapter_id in CHAPTER_ORDER:
            content = chapters_content.get(chapter_id, "")
            if content:
                # 确保章节之间有分页标记
                parts.append(content.strip())

        # 5. 组装完整文档
        full_document = "\n\n".join(parts)

        logger.info(f"文档组装完成，共{len(CHAPTER_ORDER)}章，{len(full_document)}字符")
        return full_document

    def generate_toc(self, chapters_content: Dict[str, str]) -> str:
        """生成目录

        从渲染后的章节内容中提取标题，生成层级目录。

        Args:
            chapters_content: {chapter_id: rendered_text}

        Returns:
            目录文本
        """
        toc_lines = ["目  录", ""]

        for chapter_id in CHAPTER_ORDER:
            content = chapters_content.get(chapter_id, "")
            if not content:
                continue

            # 提取该章节的各级标题
            titles = self._extract_titles_from_content(content)
            for title_info in titles:
                level = title_info["level"]
                title = title_info["title"]

                # 根据层级添加缩进
                indent = "    " * (level - 1)
                toc_lines.append(f"{indent}{title}")

        if len(toc_lines) <= 2:
            return ""

        return "\n".join(toc_lines)

    def _extract_titles_from_content(self, content: str) -> List[Dict]:
        """从渲染后的文本内容中提取标题

        识别规则：
        - 一级标题：一、二、三、... 格式
        - 二级标题：（一）（二）... 格式
        - 三级标题：1. 2. 3. ... 格式（仅限独立行）
        """
        titles = []
        lines = content.split("\n")

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 一级标题
            if re.match(r'^[一二三四五六七八九十]+、', stripped):
                titles.append({"level": 1, "title": stripped})
            # 二级标题
            elif re.match(r'^（[一二三四五六七八九十]+）', stripped):
                titles.append({"level": 2, "title": stripped})
            # 三级标题（仅限短行，避免把正文误判）
            elif re.match(r'^\d+[\.\、]', stripped) and len(stripped) < 80:
                # 只提取章节级别的三级标题（排除纯内容行）
                if re.match(r'^\d+[\.\、]\s*\S+', stripped) and "：" not in stripped:
                    titles.append({"level": 3, "title": stripped})

        return titles

    def _extract_chapter_titles(self, chapters_content: Dict[str, str]) -> None:
        """提取各章节一级标题用于内部引用"""
        for chapter_id in CHAPTER_ORDER:
            content = chapters_content.get(chapter_id, "")
            if not content:
                continue
            lines = content.split("\n")
            for line in lines:
                stripped = line.strip()
                if re.match(r'^[一二三四五六七八九十]+、', stripped):
                    self._chapter_titles[chapter_id] = stripped
                    break

    def validate_cross_references(self, full_text: str) -> List[Dict]:
        """验证交叉引用（如"详见第X章"）

        检查文档中的交叉引用是否有效。

        Args:
            full_text: 完整文档文本

        Returns:
            交叉引用问题列表 [{location, reference, issue}]
        """
        issues = []

        # 匹配"详见第X章"、"参见第X节"等模式
        patterns = [
            (r'详见第([一二三四五六七八九十]+)章', "章"),
            (r'参见第([一二三四五六七八九十]+)章', "章"),
            (r'详见["\u201c]([^"\u201d]+)["\u201d]', "引用"),
            (r'如第([一二三四五六七八九十]+)章所述', "章"),
        ]

        for pattern, ref_type in patterns:
            for match in re.finditer(pattern, full_text):
                ref_text = match.group(0)
                ref_value = match.group(1)
                position = match.start()

                # 验证引用是否有效
                if ref_type == "章":
                    # 检查对应章节是否存在
                    try:
                        idx = CHINESE_NUMERALS.index(ref_value)
                        chapter_id = f"chapter{idx + 1}"
                        if chapter_id not in self._chapter_titles:
                            issues.append({
                                "location": position,
                                "reference": ref_text,
                                "issue": f"引用的第{ref_value}章内容为空或不存在",
                            })
                    except ValueError:
                        issues.append({
                            "location": position,
                            "reference": ref_text,
                            "issue": f"无法识别的章节编号: {ref_value}",
                        })

        if issues:
            logger.warning(f"发现{len(issues)}个交叉引用问题")
        else:
            logger.info("交叉引用验证通过")

        return issues

    def add_document_header(self, project_name: str) -> str:
        """生成文档标题页内容

        Args:
            project_name: 项目名称

        Returns:
            标题页文本内容
        """
        header_lines = [
            "",
            "",
            "",
            f"{project_name}",
            "",
            "基础设施领域不动产投资信托基金（REITs）",
            "项目申报材料",
            "",
            "",
            "",
        ]
        return "\n".join(header_lines)
