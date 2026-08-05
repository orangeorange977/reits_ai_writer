"""表格自动编号引擎

文档内表格自动编号和交叉引用更新：
扫描DOCX文档表格、按章节建立编号映射、应用编号、更新正文引用。
"""
import re
import logging
from typing import Dict, Optional, List

from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)


class TableNumberingEngine:
    """文档内表格自动编号和交叉引用更新。"""

    # 匹配表格标题：如"表1-1"、"表2-3"、"表 1-1"等
    TABLE_TITLE_PATTERN = re.compile(r'表\s*(\d+)\s*[-—]\s*(\d+)')
    # 匹配正文中的表格引用
    TABLE_REF_PATTERN = re.compile(r'(?:详见|见|参见|如|参照)\s*表\s*(\d+)\s*[-—]\s*(\d+)')

    def build_numbering_map(self, doc) -> dict:
        """扫描DOCX文档所有表格，根据其所在章节建立编号映射。

        编号规则：表X-Y，X为章节号，Y为该章节内表格序号。

        Args:
            doc: python-docx Document对象

        Returns:
            dict: {table_index: "表X-Y"} 格式映射
        """
        try:
            numbering_map = {}
            current_chapter = 1
            chapter_table_count = {}

            # 获取文档body中所有元素的顺序
            body = doc.element.body
            table_index = 0

            for element in body:
                tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag

                if tag == 'p':
                    # 检查是否是章节标题（一级标题）
                    para_text = element.text or ''
                    # 尝试从段落的所有run中获取文本
                    if not para_text:
                        para_text = ''.join(
                            node.text or '' for node in element.iter()
                            if node.text
                        )
                    chapter_num = self._detect_chapter_number(para_text)
                    if chapter_num is not None:
                        current_chapter = chapter_num

                elif tag == 'tbl':
                    # 遇到表格，分配编号
                    if current_chapter not in chapter_table_count:
                        chapter_table_count[current_chapter] = 0
                    chapter_table_count[current_chapter] += 1

                    table_number = f"表{current_chapter}-{chapter_table_count[current_chapter]}"
                    numbering_map[table_index] = table_number
                    table_index += 1

            logger.info(f"表格编号映射建立完成，共{len(numbering_map)}个表格")
            return numbering_map
        except Exception as e:
            logger.error(f"建立表格编号映射失败: {e}")
            return {}

    def apply_numbering(self, doc, format_spec: dict = None):
        """按配置格式给所有表格的标题段落编号。

        查找表格前一个段落是否包含"表"字样，如有则更新编号。
        format_spec来自metadata_config的table_numbering配置。

        Args:
            doc: python-docx Document对象
            format_spec: 编号格式配置，如 {"prefix": "表", "separator": "-"}
        """
        try:
            if format_spec is None:
                format_spec = {"prefix": "表", "separator": "-"}

            prefix = format_spec.get("prefix", "表")
            separator = format_spec.get("separator", "-")

            # 先建立编号映射
            numbering_map = self.build_numbering_map(doc)
            if not numbering_map:
                logger.info("无表格需要编号")
                return

            # 遍历文档元素，查找表格前的标题段落
            body = doc.element.body
            elements = list(body)
            table_index = 0

            for i, element in enumerate(elements):
                tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag

                if tag == 'tbl' and table_index in numbering_map:
                    new_number = numbering_map[table_index]
                    # 查找此表格前一个段落
                    if i > 0:
                        prev_element = elements[i - 1]
                        prev_tag = prev_element.tag.split('}')[-1] if '}' in prev_element.tag else prev_element.tag
                        if prev_tag == 'p':
                            self._update_table_title_paragraph(prev_element, new_number)

                    table_index += 1

            logger.info(f"表格编号应用完成，共处理{table_index}个表格")
        except Exception as e:
            logger.error(f"应用表格编号失败: {e}")

    def update_cross_references(self, doc, numbering_map: dict):
        """更新正文中对表格的引用（如"详见表X-Y"）。

        使用正则查找并替换旧编号为新编号。

        Args:
            doc: python-docx Document对象
            numbering_map: 编号映射 {table_index: "表X-Y"}
        """
        try:
            if not numbering_map:
                return

            # 构建旧编号到新编号的映射
            # 先扫描文档中所有现有的表格引用
            ref_updates = {}
            old_numbers = []

            for para in doc.paragraphs:
                matches = self.TABLE_REF_PATTERN.finditer(para.text)
                for match in matches:
                    old_ref = f"表{match.group(1)}-{match.group(2)}"
                    if old_ref not in old_numbers:
                        old_numbers.append(old_ref)

            # 如果没有需要更新的引用，直接返回
            if not old_numbers:
                return

            # 对每个段落中的run进行替换
            update_count = 0
            for para in doc.paragraphs:
                for run in para.runs:
                    original_text = run.text
                    updated_text = original_text

                    for new_number in numbering_map.values():
                        # 使用正则替换
                        updated_text = self.TABLE_TITLE_PATTERN.sub(
                            lambda m: new_number if f"表{m.group(1)}-{m.group(2)}" in old_numbers else m.group(0),
                            updated_text
                        )

                    if updated_text != original_text:
                        run.text = updated_text
                        update_count += 1

            if update_count > 0:
                logger.info(f"更新了{update_count}处表格交叉引用")
        except Exception as e:
            logger.error(f"更新表格交叉引用失败: {e}")

    def _detect_chapter_number(self, text: str) -> Optional[int]:
        """从段落文本中检测章节号。

        支持格式：
        - "一、..." -> 1
        - "二、..." -> 2
        - "第一章" -> 1

        Args:
            text: 段落文本

        Returns:
            int: 章节号，未检测到返回None
        """
        text = text.strip()
        if not text:
            return None

        # 中文数字映射
        cn_numbers = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
        }

        # 匹配"X、"格式（如"一、项目基本情况"）
        match = re.match(r'^([一二三四五六七八九十]+)、', text)
        if match:
            cn_char = match.group(1)
            if cn_char in cn_numbers:
                return cn_numbers[cn_char]

        # 匹配"第X章"格式
        match = re.match(r'^第([一二三四五六七八九十]+)章', text)
        if match:
            cn_char = match.group(1)
            if cn_char in cn_numbers:
                return cn_numbers[cn_char]

        return None

    def _update_table_title_paragraph(self, para_element, new_number: str):
        """更新段落元素中的表格标题编号。

        Args:
            para_element: 段落XML元素
            new_number: 新编号如"表1-2"
        """
        try:
            # 获取段落中所有文本节点
            for node in para_element.iter():
                if node.text and '表' in node.text:
                    node.text = self.TABLE_TITLE_PATTERN.sub(new_number, node.text)
                if node.tail and '表' in node.tail:
                    node.tail = self.TABLE_TITLE_PATTERN.sub(new_number, node.tail)
        except Exception as e:
            logger.error(f"更新表格标题段落失败: {e}")
