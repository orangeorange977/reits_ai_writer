"""不涉及模块处理

处理"不涉及"模块的智能标记和内容替换：
根据配置规则自动检测、批量标记、以及在结构化数据中应用替换。
"""
import logging
from typing import List, Optional

from backend.database.db import get_project_metadata, save_project_metadata
from backend.mappings import load_metadata_config

logger = logging.getLogger(__name__)


class InapplicableSectionHandler:
    """处理"不涉及"模块的智能标记和内容替换。"""

    META_TYPE = "inapplicable"

    async def get_inapplicable_sections(self, project_id: int) -> list:
        """获取已标记为不涉及的模块ID列表。

        Args:
            project_id: 项目ID

        Returns:
            list: 已标记的模块ID列表，如 ["section_2-3", "section_5-1"]
        """
        try:
            data = await get_project_metadata(project_id, self.META_TYPE)
            if data is None:
                return []
            return data.get("sections", [])
        except Exception as e:
            logger.error(f"获取不涉及模块列表失败 (project_id={project_id}): {e}")
            return []

    async def mark_sections(self, project_id: int, section_ids: list, reason: str = "") -> dict:
        """批量标记模块为不涉及。

        Args:
            project_id: 项目ID
            section_ids: 要标记的模块ID列表
            reason: 标记原因说明

        Returns:
            dict: 操作结果 {"success": bool, "marked_count": int}
        """
        try:
            # 获取现有标记
            existing_data = await get_project_metadata(project_id, self.META_TYPE)
            if existing_data is None:
                existing_data = {"sections": [], "reasons": {}}

            existing_sections = set(existing_data.get("sections", []))
            reasons = existing_data.get("reasons", {})

            # 添加新标记
            for sid in section_ids:
                existing_sections.add(sid)
                if reason:
                    reasons[sid] = reason

            meta_data = {
                "sections": list(existing_sections),
                "reasons": reasons
            }
            await save_project_metadata(project_id, self.META_TYPE, meta_data)

            logger.info(f"标记不涉及模块成功 (project_id={project_id}, count={len(section_ids)})")
            return {"success": True, "marked_count": len(existing_sections)}
        except Exception as e:
            logger.error(f"标记不涉及模块失败 (project_id={project_id}): {e}")
            return {"success": False, "marked_count": 0, "error": str(e)}

    def auto_detect(self, chapter_data: dict, metadata_config: dict) -> list:
        """根据配置规则自动检测哪些模块应标记为不涉及。

        检查metadata_config中inapplicable_handling.rules的trigger_field是否为空。

        Args:
            chapter_data: 章节结构化数据
            metadata_config: 元数据配置（从load_metadata_config获取）

        Returns:
            list: 应标记为不涉及的模块ID列表
        """
        try:
            inapplicable_config = metadata_config.get("inapplicable_handling", {})
            rules = inapplicable_config.get("rules", [])

            if not rules:
                return []

            detected = []
            for rule in rules:
                trigger_field = rule.get("trigger_field", "")
                section_id = rule.get("section_id", "")

                if not trigger_field or not section_id:
                    continue

                # 检查trigger_field在chapter_data中是否为空
                field_value = self._get_nested_value(chapter_data, trigger_field)
                if field_value is None or field_value == "" or field_value == []:
                    detected.append(section_id)

            logger.info(f"自动检测到{len(detected)}个不涉及模块")
            return detected
        except Exception as e:
            logger.error(f"自动检测不涉及模块失败: {e}")
            return []

    def apply_to_structured_data(self, structured_data: dict, marked_sections: list) -> dict:
        """将标记模块的内容替换为"不涉及。"

        修改structured_data中对应章节的narrative_fields。

        Args:
            structured_data: 完整结构化数据
            marked_sections: 已标记的不涉及模块ID列表

        Returns:
            dict: 修改后的structured_data
        """
        try:
            if not marked_sections:
                return structured_data

            replacement_text = "不涉及。"

            for chapter_key, chapter_val in structured_data.items():
                if not isinstance(chapter_val, dict):
                    continue

                sections = chapter_val.get("sections", {})
                if not isinstance(sections, dict):
                    continue

                for section_key, section_val in sections.items():
                    if section_key in marked_sections:
                        # 替换narrative_fields中的内容
                        if isinstance(section_val, dict):
                            narrative = section_val.get("narrative_fields", {})
                            if isinstance(narrative, dict):
                                for field_key in narrative:
                                    narrative[field_key] = replacement_text
                                section_val["narrative_fields"] = narrative
                            # 同时标记该section为不涉及
                            section_val["is_inapplicable"] = True

            logger.info(f"已将{len(marked_sections)}个模块内容替换为'不涉及。'")
            return structured_data
        except Exception as e:
            logger.error(f"应用不涉及标记到结构化数据失败: {e}")
            return structured_data

    def _get_nested_value(self, data: dict, field_path: str):
        """根据点分路径获取嵌套字典中的值。

        Args:
            data: 数据字典
            field_path: 点分字段路径，如 "chapter1.sections.1-1.project_name"

        Returns:
            字段值，不存在返回None
        """
        try:
            keys = field_path.split(".")
            current = data
            for key in keys:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return None
                if current is None:
                    return None
            return current
        except Exception:
            return None
