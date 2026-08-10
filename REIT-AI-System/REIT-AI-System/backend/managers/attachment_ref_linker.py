"""附件引用管理模块

管理附件编号分配和正文中附件引用的自动更新：
维护附件清单、自动分配编号、替换文档中的附件引用标记。
"""
import re
import logging
from typing import List, Dict, Optional

from backend.database.db import get_project_metadata, save_project_metadata

logger = logging.getLogger(__name__)


class AttachmentReferenceLinker:
    """管理附件编号分配和正文中附件引用的自动更新。"""

    META_TYPE = "attachment_ref"

    # 匹配附件引用标记：如"附件1"、"附件一"、"附件:xxx"、"详见附件xxx"
    ATTACHMENT_REF_PATTERN = re.compile(
        r'(?:详见|见|参见)?\s*附件\s*[:：]?\s*([^\s，。、；\n]+)'
    )
    # 匹配编号格式：附件1、附件2等
    ATTACHMENT_NUM_PATTERN = re.compile(r'附件\s*(\d+)')

    async def get_attachment_list(self, project_id: int) -> list:
        """获取项目附件编号清单。

        Args:
            project_id: 项目ID

        Returns:
            list: 附件清单，每项格式为 {"id": str, "number": int, "title": str, "filename": str}
        """
        try:
            data = await get_project_metadata(project_id, self.META_TYPE)
            if data is None:
                return []
            return data.get("attachments", [])
        except Exception as e:
            logger.error(f"获取附件清单失败 (project_id={project_id}): {e}")
            return []

    async def save_attachment_list(self, project_id: int, attachments: list) -> dict:
        """保存/更新附件清单。

        Args:
            project_id: 项目ID
            attachments: 附件列表，每项格式为 {"id": str, "title": str, "filename": str}

        Returns:
            dict: 操作结果 {"success": bool, "count": int}
        """
        try:
            # 自动分配编号
            numbered_attachments = self.auto_assign_numbers(attachments)

            meta_data = {
                "attachments": numbered_attachments,
                "count": len(numbered_attachments)
            }
            await save_project_metadata(project_id, self.META_TYPE, meta_data)

            logger.info(f"保存附件清单成功 (project_id={project_id}, count={len(numbered_attachments)})")
            return {"success": True, "count": len(numbered_attachments)}
        except Exception as e:
            logger.error(f"保存附件清单失败 (project_id={project_id}): {e}")
            return {"success": False, "count": 0, "error": str(e)}

    def auto_assign_numbers(self, attachments: list) -> list:
        """按附件出现顺序自动分配编号。

        Args:
            attachments: 附件列表

        Returns:
            list: 带编号的附件列表
        """
        try:
            numbered = []
            for i, attachment in enumerate(attachments, start=1):
                item = attachment.copy() if isinstance(attachment, dict) else {"title": str(attachment)}
                item["number"] = i
                item["ref_label"] = f"附件{i}"
                numbered.append(item)

            logger.info(f"附件自动编号完成，共{len(numbered)}个附件")
            return numbered
        except Exception as e:
            logger.error(f"附件自动编号失败: {e}")
            return attachments

    def update_references_in_doc(self, doc, attachment_map: dict):
        """替换DOCX正文中的附件引用标记。

        查找格式如 附件:xxx 或"详见附件"相关文本，替换为正确编号。

        Args:
            doc: python-docx Document对象
            attachment_map: 附件标题到编号的映射，格式为 {"附件标题": "附件1", ...}
        """
        try:
            if not attachment_map:
                logger.info("附件映射为空，跳过引用更新")
                return

            update_count = 0

            for para in doc.paragraphs:
                for run in para.runs:
                    original_text = run.text
                    if not original_text:
                        continue

                    updated_text = self._replace_attachment_refs(original_text, attachment_map)
                    if updated_text != original_text:
                        run.text = updated_text
                        update_count += 1

            # 同时处理表格中的引用
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                original_text = run.text
                                if not original_text:
                                    continue

                                updated_text = self._replace_attachment_refs(
                                    original_text, attachment_map
                                )
                                if updated_text != original_text:
                                    run.text = updated_text
                                    update_count += 1

            if update_count > 0:
                logger.info(f"更新了{update_count}处附件引用")
            else:
                logger.info("未发现需要更新的附件引用")
        except Exception as e:
            logger.error(f"更新附件引用失败: {e}")

    def build_attachment_map(self, attachments: list) -> dict:
        """从附件清单构建标题到编号标签的映射。

        Args:
            attachments: 带编号的附件清单

        Returns:
            dict: {"附件标题关键词": "附件N", ...}
        """
        try:
            mapping = {}
            for att in attachments:
                if not isinstance(att, dict):
                    continue
                title = att.get("title", "")
                ref_label = att.get("ref_label", "")
                att_id = att.get("id", "")

                if title and ref_label:
                    mapping[title] = ref_label
                if att_id and ref_label:
                    mapping[att_id] = ref_label

            return mapping
        except Exception as e:
            logger.error(f"构建附件映射失败: {e}")
            return {}

    def _replace_attachment_refs(self, text: str, attachment_map: dict) -> str:
        """在文本中替换附件引用。

        Args:
            text: 原始文本
            attachment_map: 附件映射

        Returns:
            str: 替换后的文本
        """
        try:
            result = text

            # 策略1: 精确匹配 "附件:标题" 或 "附件：标题" 格式
            for title, ref_label in attachment_map.items():
                # 替换 "附件:标题" → "附件N（标题）"
                patterns = [
                    f"附件:{title}",
                    f"附件：{title}",
                    f"附件: {title}",
                    f"附件： {title}",
                ]
                for pattern in patterns:
                    if pattern in result:
                        result = result.replace(pattern, f"{ref_label}（{title}）")

            # 策略2: 查找包含附件标题关键词的引用并补充编号
            for title, ref_label in attachment_map.items():
                if title in result and ref_label not in result:
                    # 在标题前面查找"附件"字样，如果有则补充编号
                    old_pattern = f"附件{title}"
                    if old_pattern in result:
                        result = result.replace(old_pattern, f"{ref_label}（{title}）")

            return result
        except Exception as e:
            logger.error(f"替换附件引用文本失败: {e}")
            return text
