"""业务逻辑管理模块包

提供REITs法律文件生成的业务增强功能：
- GlossaryManager: 释义表管理
- InapplicableSectionHandler: 不涉及模块处理
- TableNumberingEngine: 表格自动编号
- FinancialDataManager: 财务数据管理
- AttachmentReferenceLinker: 附件引用管理
"""
import logging

from backend.managers.glossary_manager import GlossaryManager
from backend.managers.inapplicable_handler import InapplicableSectionHandler
from backend.managers.table_numbering_engine import TableNumberingEngine
from backend.managers.financial_data_manager import FinancialDataManager
from backend.managers.attachment_ref_linker import AttachmentReferenceLinker

logger = logging.getLogger(__name__)

__all__ = [
    "GlossaryManager",
    "InapplicableSectionHandler",
    "TableNumberingEngine",
    "FinancialDataManager",
    "AttachmentReferenceLinker",
    "apply_all_enhancements",
]


def apply_all_enhancements(doc, project_id: int, structured_data: dict, metadata: dict = None):
    """在DOCX生成后统一应用所有增强功能（同步函数）。

    在generate.py中通过asyncio.to_thread调用，接收预先异步获取的metadata数据。

    按顺序执行：
    1. 不涉及模块处理 — 修改structured_data中标记模块的内容
    2. 释义表填充 — 在DOCX文档中插入术语表
    3. 表格自动编号 — 给所有表格编号并更新交叉引用
    4. 附件引用更新 — 替换正文中的附件引用标记

    每个步骤独立try/except，单个功能失败不影响其他功能。

    Args:
        doc: python-docx Document对象
        project_id: 项目ID
        structured_data: 完整结构化数据（各章节内容）
        metadata: 预先异步获取的所有project_metadata数据，格式为 {meta_type: meta_data}。
                  如果为None或某个key不存在，则对应功能跳过。

    Returns:
        dict: 各步骤执行结果汇总
    """
    results = {
        "inapplicable": {"status": "skipped"},
        "glossary": {"status": "skipped"},
        "table_numbering": {"status": "skipped"},
        "attachment_ref": {"status": "skipped"},
    }

    if metadata is None:
        metadata = {}

    # ===== 步骤1: 不涉及模块处理 =====
    try:
        inapplicable_data = metadata.get("inapplicable")
        if inapplicable_data is not None:
            handler = InapplicableSectionHandler()
            marked_sections = inapplicable_data.get("sections", [])

            if marked_sections:
                handler.apply_to_structured_data(structured_data, marked_sections)
                results["inapplicable"] = {
                    "status": "success",
                    "sections_processed": len(marked_sections)
                }
                logger.info(f"不涉及模块处理完成，处理{len(marked_sections)}个模块")
            else:
                results["inapplicable"] = {"status": "skipped", "reason": "无标记模块"}
        else:
            results["inapplicable"] = {"status": "skipped", "reason": "无metadata"}
    except Exception as e:
        logger.error(f"不涉及模块处理失败: {e}")
        results["inapplicable"] = {"status": "error", "error": str(e)}

    # ===== 步骤2: 释义表填充 =====
    try:
        glossary_data = metadata.get("glossary")
        if glossary_data is not None:
            manager = GlossaryManager()
            manager.inject_glossary_to_docx(doc, glossary_data)
            results["glossary"] = {
                "status": "success",
                "entries_count": len(glossary_data.get("entries", []))
            }
            logger.info("释义表填充完成")
        else:
            # 尝试使用默认术语表
            try:
                from backend.mappings import load_glossary
                default_glossary = load_glossary(cache=True)
                if default_glossary and default_glossary.get("entries"):
                    manager = GlossaryManager()
                    manager.inject_glossary_to_docx(doc, default_glossary)
                    results["glossary"] = {
                        "status": "success",
                        "entries_count": len(default_glossary.get("entries", [])),
                        "source": "default"
                    }
                else:
                    results["glossary"] = {"status": "skipped", "reason": "无释义数据"}
            except Exception:
                results["glossary"] = {"status": "skipped", "reason": "无metadata且默认加载失败"}
    except Exception as e:
        logger.error(f"释义表填充失败: {e}")
        results["glossary"] = {"status": "error", "error": str(e)}

    # ===== 步骤3: 表格自动编号 =====
    try:
        engine = TableNumberingEngine()

        # 获取编号格式配置
        metadata_config = metadata.get("metadata_config", {})
        format_spec = None
        if metadata_config:
            format_spec = metadata_config.get("table_numbering", None)

        # 应用编号
        engine.apply_numbering(doc, format_spec)

        # 更新交叉引用
        numbering_map = engine.build_numbering_map(doc)
        if numbering_map:
            engine.update_cross_references(doc, numbering_map)

        results["table_numbering"] = {
            "status": "success",
            "tables_numbered": len(numbering_map)
        }
        logger.info(f"表格自动编号完成，共{len(numbering_map)}个表格")
    except Exception as e:
        logger.error(f"表格自动编号失败: {e}")
        results["table_numbering"] = {"status": "error", "error": str(e)}

    # ===== 步骤4: 附件引用更新 =====
    try:
        attachment_data = metadata.get("attachment_ref")
        if attachment_data is not None:
            linker = AttachmentReferenceLinker()
            attachments = attachment_data.get("attachments", [])

            if attachments:
                # 构建映射并更新引用
                attachment_map = linker.build_attachment_map(attachments)
                linker.update_references_in_doc(doc, attachment_map)
                results["attachment_ref"] = {
                    "status": "success",
                    "attachments_count": len(attachments)
                }
                logger.info(f"附件引用更新完成，共{len(attachments)}个附件")
            else:
                results["attachment_ref"] = {"status": "skipped", "reason": "附件清单为空"}
        else:
            results["attachment_ref"] = {"status": "skipped", "reason": "无metadata"}
    except Exception as e:
        logger.error(f"附件引用更新失败: {e}")
        results["attachment_ref"] = {"status": "error", "error": str(e)}

    logger.info(f"所有增强功能执行完毕 (project_id={project_id}): {results}")
    return results
