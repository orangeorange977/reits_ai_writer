"""财务数据管理模块

管理标准化财务数据输入和格式化输出：
提供财务数据模板定义、保存/获取项目财务数据、转换为表格兼容格式。
"""
import logging
from typing import Dict, Optional, Any

from backend.database.db import get_project_metadata, save_project_metadata
from backend.mappings import load_financial_standards

logger = logging.getLogger(__name__)


class FinancialDataManager:
    """管理标准化财务数据输入和格式化输出。"""

    META_TYPE = "financial"

    def get_financial_template(self) -> dict:
        """获取财务数据模板定义（从配置文件）。

        Returns:
            dict: 财务数据模板，包含字段定义和验证规则
        """
        try:
            standards = load_financial_standards(cache=True)
            template = {
                "fields": standards.get("fields", []),
                "tables": standards.get("tables", []),
                "validation_rules": standards.get("validation_rules", []),
                "periods": standards.get("periods", ["最近一年", "最近两年", "最近三年"]),
            }
            return template
        except Exception as e:
            logger.error(f"获取财务数据模板失败: {e}")
            return {"fields": [], "tables": [], "validation_rules": [], "periods": []}

    async def save_financial_data(self, project_id: int, data: dict) -> dict:
        """保存项目财务数据到project_metadata。

        Args:
            project_id: 项目ID
            data: 财务数据字典，格式依据financial_template定义

        Returns:
            dict: 操作结果 {"success": bool, "saved_fields": int}
        """
        try:
            # 验证基本数据结构
            if not isinstance(data, dict):
                return {"success": False, "error": "数据格式无效，期望dict类型"}

            meta_data = {
                "financial_data": data,
                "field_count": len(data)
            }
            await save_project_metadata(project_id, self.META_TYPE, meta_data)

            logger.info(f"保存项目财务数据成功 (project_id={project_id}, fields={len(data)})")
            return {"success": True, "saved_fields": len(data)}
        except Exception as e:
            logger.error(f"保存项目财务数据失败 (project_id={project_id}): {e}")
            return {"success": False, "saved_fields": 0, "error": str(e)}

    async def get_financial_data(self, project_id: int) -> dict:
        """获取已保存的财务数据。

        Args:
            project_id: 项目ID

        Returns:
            dict: 财务数据字典，未找到返回空dict
        """
        try:
            data = await get_project_metadata(project_id, self.META_TYPE)
            if data is None:
                return {}
            return data.get("financial_data", {})
        except Exception as e:
            logger.error(f"获取项目财务数据失败 (project_id={project_id}): {e}")
            return {}

    def render_to_table_data(self, financial_data: dict, template_key: str) -> dict:
        """将保存的财务数据转为table_data格式，供template_docx_generator填充。

        输出格式与ndrc_chapter_mapping.json中table定义兼容。

        Args:
            financial_data: 已保存的财务数据
            template_key: 表格模板标识，如 "income_statement", "balance_sheet"

        Returns:
            dict: table_data格式数据，包含headers和rows
        """
        try:
            if not financial_data:
                logger.warning(f"财务数据为空，无法渲染表格 (template_key={template_key})")
                return {"headers": [], "rows": []}

            standards = load_financial_standards(cache=True)
            table_defs = standards.get("tables", [])

            # 查找对应的表格定义
            target_def = None
            for table_def in table_defs:
                if table_def.get("id") == template_key or table_def.get("key") == template_key:
                    target_def = table_def
                    break

            if target_def is None:
                logger.warning(f"未找到表格模板定义: {template_key}")
                return {"headers": [], "rows": []}

            # 根据表格定义构建输出
            headers = target_def.get("headers", [])
            row_defs = target_def.get("rows", [])
            periods = target_def.get("periods", [])

            rows = []
            for row_def in row_defs:
                field_id = row_def.get("field_id", "")
                row_label = row_def.get("label", "")

                row_data = {"label": row_label, "values": []}

                # 从financial_data中提取对应字段的各期数据
                field_data = financial_data.get(field_id, {})
                if isinstance(field_data, dict):
                    for period in periods:
                        value = field_data.get(period, "")
                        row_data["values"].append(self._format_number(value))
                elif isinstance(field_data, (int, float)):
                    row_data["values"].append(self._format_number(field_data))
                else:
                    row_data["values"].append(str(field_data) if field_data else "")

                rows.append(row_data)

            result = {
                "headers": headers,
                "rows": rows,
                "template_key": template_key,
                "title": target_def.get("title", "")
            }

            logger.info(f"财务数据渲染完成 (template_key={template_key}, rows={len(rows)})")
            return result
        except Exception as e:
            logger.error(f"渲染财务数据到表格格式失败 (template_key={template_key}): {e}")
            return {"headers": [], "rows": []}

    def _format_number(self, value) -> str:
        """格式化数字为财务展示格式。

        Args:
            value: 数字值

        Returns:
            str: 格式化后的字符串
        """
        try:
            if value is None or value == "":
                return ""
            if isinstance(value, str):
                # 尝试转换为数字
                try:
                    value = float(value.replace(",", ""))
                except ValueError:
                    return value

            if isinstance(value, (int, float)):
                # 保留两位小数，千分位分隔
                if value == int(value):
                    return f"{int(value):,}"
                return f"{value:,.2f}"
            return str(value)
        except Exception:
            return str(value) if value else ""
