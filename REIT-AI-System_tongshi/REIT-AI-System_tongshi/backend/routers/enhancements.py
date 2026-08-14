"""增强功能API路由 - 释义表、承诺函、财务数据、不涉及模块、基准日、附件引用"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.managers.glossary_manager import GlossaryManager
from backend.managers.inapplicable_handler import InapplicableSectionHandler
from backend.managers.financial_data_manager import FinancialDataManager
from backend.managers.attachment_ref_linker import AttachmentReferenceLinker
from backend.mappings import load_commitment_templates, load_metadata_config
from backend.database.db import get_project_metadata, save_project_metadata

router = APIRouter(tags=["enhancements"])
logger = logging.getLogger(__name__)


# ===== Pydantic请求/响应模型 =====

class GlossaryEntry(BaseModel):
    """释义条目"""
    term: str
    definition: str


class GlossaryUpdateRequest(BaseModel):
    """释义表更新请求"""
    entries: List[GlossaryEntry]


class CommitmentFillRequest(BaseModel):
    """承诺函填充请求"""
    template_id: str
    variables: dict


class FinancialDataUpdateRequest(BaseModel):
    """财务数据更新请求"""
    data: dict


class InapplicableUpdateRequest(BaseModel):
    """不涉及模块更新请求"""
    sections: List[str]
    reason: Optional[str] = ""


class QueryDatesUpdateRequest(BaseModel):
    """基准日配置更新请求"""
    base_date: str
    query_point: Optional[str] = ""
    extra: Optional[dict] = None


class AttachmentItem(BaseModel):
    """附件条目"""
    id: Optional[str] = None
    title: str
    filename: Optional[str] = ""


class AttachmentUpdateRequest(BaseModel):
    """附件引用更新请求"""
    attachments: List[AttachmentItem]


# ===== 统一响应辅助 =====

def _success_response(data=None):
    """统一成功响应"""
    return {"success": True, "data": data}


def _error_response(error: str, status_code: int = 400):
    """统一错误响应（通过HTTPException抛出）"""
    raise HTTPException(status_code=status_code, detail={"success": False, "error": error})


# ===== 释义表端点 =====

@router.get("/projects/{project_id}/glossary")
async def get_glossary(project_id: int):
    """获取项目释义表"""
    try:
        manager = GlossaryManager()
        result = await manager.get_project_glossary(project_id)
        return _success_response(result)
    except Exception as e:
        logger.error(f"获取释义表失败 (project_id={project_id}): {e}")
        _error_response(f"获取释义表失败: {str(e)}", 500)


@router.put("/projects/{project_id}/glossary")
async def update_glossary(project_id: int, request: GlossaryUpdateRequest):
    """更新项目释义表"""
    try:
        manager = GlossaryManager()
        entries = [entry.model_dump() for entry in request.entries]
        result = await manager.update_glossary(project_id, entries)
        if result.get("success"):
            return _success_response(result)
        else:
            _error_response(result.get("error", "更新失败"), 500)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新释义表失败 (project_id={project_id}): {e}")
        _error_response(f"更新释义表失败: {str(e)}", 500)


# ===== 承诺函端点 =====

@router.get("/projects/{project_id}/commitments")
async def get_commitment_templates(project_id: int):
    """获取可用承诺函模板列表"""
    try:
        templates = load_commitment_templates(cache=True)
        template_list = templates.get("templates", [])
        return _success_response({
            "templates": template_list,
            "total": len(template_list)
        })
    except Exception as e:
        logger.error(f"获取承诺函模板失败: {e}")
        _error_response(f"获取承诺函模板失败: {str(e)}", 500)


@router.post("/projects/{project_id}/commitments/fill")
async def fill_commitment(project_id: int, request: CommitmentFillRequest):
    """填充承诺函（替换变量）"""
    try:
        templates = load_commitment_templates(cache=True)
        template_list = templates.get("templates", [])

        # 查找指定模板
        target_template = None
        for tpl in template_list:
            if tpl.get("id") == request.template_id:
                target_template = tpl
                break

        if target_template is None:
            _error_response(f"未找到模板: {request.template_id}", 404)

        # 替换模板中的变量占位符
        content = target_template.get("content", "")
        filled_content = content
        for key, value in request.variables.items():
            placeholder = "{{" + key + "}}"
            filled_content = filled_content.replace(placeholder, str(value))

        return _success_response({
            "template_id": request.template_id,
            "title": target_template.get("title", ""),
            "filled_content": filled_content,
            "variables_applied": list(request.variables.keys())
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"填充承诺函失败 (project_id={project_id}): {e}")
        _error_response(f"填充承诺函失败: {str(e)}", 500)


# ===== 财务数据端点 =====

@router.get("/projects/{project_id}/financial-data")
async def get_financial_data(project_id: int):
    """获取项目财务数据"""
    try:
        manager = FinancialDataManager()
        data = await manager.get_financial_data(project_id)
        template = manager.get_financial_template()
        return _success_response({
            "financial_data": data,
            "template": template
        })
    except Exception as e:
        logger.error(f"获取财务数据失败 (project_id={project_id}): {e}")
        _error_response(f"获取财务数据失败: {str(e)}", 500)


@router.put("/projects/{project_id}/financial-data")
async def save_financial_data(project_id: int, request: FinancialDataUpdateRequest):
    """保存项目财务数据"""
    try:
        manager = FinancialDataManager()
        result = await manager.save_financial_data(project_id, request.data)
        if result.get("success"):
            return _success_response(result)
        else:
            _error_response(result.get("error", "保存失败"), 400)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存财务数据失败 (project_id={project_id}): {e}")
        _error_response(f"保存财务数据失败: {str(e)}", 500)


# ===== 不涉及模块端点 =====

@router.get("/projects/{project_id}/inapplicable")
async def get_inapplicable(project_id: int):
    """获取已标记的不涉及模块"""
    try:
        handler = InapplicableSectionHandler()
        sections = await handler.get_inapplicable_sections(project_id)
        return _success_response({
            "sections": sections,
            "total": len(sections)
        })
    except Exception as e:
        logger.error(f"获取不涉及模块失败 (project_id={project_id}): {e}")
        _error_response(f"获取不涉及模块失败: {str(e)}", 500)


@router.put("/projects/{project_id}/inapplicable")
async def update_inapplicable(project_id: int, request: InapplicableUpdateRequest):
    """更新不涉及标记"""
    try:
        handler = InapplicableSectionHandler()
        result = await handler.mark_sections(project_id, request.sections, request.reason)
        if result.get("success"):
            return _success_response(result)
        else:
            _error_response(result.get("error", "更新失败"), 500)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新不涉及模块失败 (project_id={project_id}): {e}")
        _error_response(f"更新不涉及模块失败: {str(e)}", 500)


# ===== 基准日/查询时点端点 =====

@router.get("/projects/{project_id}/query-dates")
async def get_query_dates(project_id: int):
    """获取基准日配置"""
    try:
        data = await get_project_metadata(project_id, "query_point")
        if data is None:
            # 返回默认配置
            metadata_config = load_metadata_config(cache=True)
            default_dates = metadata_config.get("query_dates", {})
            return _success_response({
                "base_date": default_dates.get("base_date", ""),
                "query_point": default_dates.get("query_point", ""),
                "extra": default_dates.get("extra", {}),
                "source": "default"
            })
        return _success_response({
            "base_date": data.get("base_date", ""),
            "query_point": data.get("query_point", ""),
            "extra": data.get("extra", {}),
            "source": "custom"
        })
    except Exception as e:
        logger.error(f"获取基准日配置失败 (project_id={project_id}): {e}")
        _error_response(f"获取基准日配置失败: {str(e)}", 500)


@router.put("/projects/{project_id}/query-dates")
async def update_query_dates(project_id: int, request: QueryDatesUpdateRequest):
    """更新基准日配置"""
    try:
        meta_data = {
            "base_date": request.base_date,
            "query_point": request.query_point or "",
            "extra": request.extra or {}
        }
        await save_project_metadata(project_id, "query_point", meta_data)
        return _success_response({
            "base_date": request.base_date,
            "query_point": request.query_point,
            "updated": True
        })
    except Exception as e:
        logger.error(f"更新基准日配置失败 (project_id={project_id}): {e}")
        _error_response(f"更新基准日配置失败: {str(e)}", 500)


# ===== 附件引用端点 =====

@router.get("/projects/{project_id}/attachments")
async def get_attachments(project_id: int):
    """获取附件编号清单"""
    try:
        linker = AttachmentReferenceLinker()
        attachments = await linker.get_attachment_list(project_id)
        return _success_response({
            "attachments": attachments,
            "total": len(attachments)
        })
    except Exception as e:
        logger.error(f"获取附件清单失败 (project_id={project_id}): {e}")
        _error_response(f"获取附件清单失败: {str(e)}", 500)


@router.put("/projects/{project_id}/attachments")
async def update_attachments(project_id: int, request: AttachmentUpdateRequest):
    """更新附件引用"""
    try:
        linker = AttachmentReferenceLinker()
        attachments = [item.model_dump() for item in request.attachments]
        result = await linker.save_attachment_list(project_id, attachments)
        if result.get("success"):
            return _success_response(result)
        else:
            _error_response(result.get("error", "更新失败"), 500)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新附件引用失败 (project_id={project_id}): {e}")
        _error_response(f"更新附件引用失败: {str(e)}", 500)
