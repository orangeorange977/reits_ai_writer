"""文档生成路由 - 触发生成、获取进度、下载文档"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import OUTPUT_DIR, USE_OFFICIAL_TEMPLATE, NDRC_OFFICIAL_TEMPLATE, ENABLE_ENHANCEMENTS
from backend.generators import NDRCGenerator, ChapterComposer, DocxExporter
from backend.database.db import get_db

router = APIRouter(tags=["文档生成"])
logger = logging.getLogger(__name__)

# 确保输出目录存在
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 全局字典存储各项目的生成器实例（用于查询进度）
_generators: dict = {}


# ===== 请求/响应模型 =====

class GenerateRequest(BaseModel):
    """文档生成请求"""
    chapter_ids: Optional[List[str]] = None  # None表示生成全部章节


# ===== 辅助函数 =====

async def _get_project_data(project_id: int) -> dict:
    """从数据库获取项目信息"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status FROM projects WHERE id = ?",
            (project_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "data_source_path": row[2],
            "status": row[3],
        }
    finally:
        await db.close()


async def _update_project_status(project_id: int, status: str) -> None:
    """更新项目状态"""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE projects SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, project_id)
        )
        await db.commit()
    finally:
        await db.close()


# ===== 后台生成任务 =====

async def run_generation(project_id: int, chapter_ids: Optional[List[str]], data_source_path: str):
    """后台生成任务（使用 asyncio.to_thread 避免阻塞事件循环）"""
    generator = NDRCGenerator(data_source_path)
    _generators[project_id] = generator
    try:
        # 更新项目状态为生成中
        await _update_project_status(project_id, "generating")

        # 1. 扫描数据源（同步操作，放到线程池执行）
        scan_result = await asyncio.to_thread(generator.scan_data_sources)
        logger.info(f"项目{project_id}扫描完成，找到{scan_result['total_files']}个文件")

        # 2. 提取各章数据
        all_chapter_data = {}
        chapters_to_generate = chapter_ids or [f"chapter{i}" for i in range(1, 8)]
        for ch_id in chapters_to_generate:
            data = await asyncio.to_thread(generator.extract_chapter_data, ch_id)
            all_chapter_data[ch_id] = data
            logger.info(f"项目{project_id}章节{ch_id}数据提取完成")

        # 3. 生成完整文档
        # 获取项目名称
        project_data_db = await _get_project_data(project_id)
        project_name = project_data_db["name"] if project_data_db else f"项目{project_id}"

        project_data = {
            "project_name": project_name,
            "chapters": all_chapter_data,
        }

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f"project_{project_id}_{timestamp}_发改委申报材料"

        if USE_OFFICIAL_TEMPLATE:
            # 新路径：结构化数据 → 官方模板填充
            from backend.generators.template_docx_generator import TemplateDocxGenerator
            from backend.mappings import load_table_schemas

            structured_data = await asyncio.to_thread(
                generator.generate_full_document, project_data, True
            )
            logger.info(f"项目{project_id}结构化数据生成完成")

            # === 可选增强功能（不影响核心管线） ===
            if ENABLE_ENHANCEMENTS:
                try:
                    from backend.managers import apply_all_enhancements
                    from backend.database.db import get_project_metadata
                    # 预先异步获取所有metadata
                    metadata = {}
                    for meta_type in ['glossary', 'financial', 'inapplicable', 'query_point', 'attachment_ref']:
                        meta = await get_project_metadata(project_id, meta_type)
                        if meta:
                            metadata[meta_type] = meta
                    # 同步执行文档增强（修改structured_data）
                    await asyncio.to_thread(
                        apply_all_enhancements, None, project_id, structured_data, metadata
                    )
                except Exception as e:
                    logger.warning(f"增强功能执行异常（不影响核心输出）: {e}")

            table_schemas = load_table_schemas()
            template_gen = TemplateDocxGenerator(str(NDRC_OFFICIAL_TEMPLATE), table_schemas)
            output_path = await asyncio.to_thread(
                template_gen.generate,
                structured_data,
                str(OUTPUT_DIR / f"{output_filename}.docx")
            )
        else:
            # 旧路径：Jinja2渲染 → 文本解析 → DOCX（保留作为回退）
            full_content = await asyncio.to_thread(
                generator.generate_full_document, project_data
            )
            logger.info(f"项目{project_id}文档内容生成完成，共{len(full_content)}字符")

            exporter = DocxExporter(str(OUTPUT_DIR))
            output_path = await asyncio.to_thread(
                exporter.export,
                full_content,
                output_filename,
                {"project_name": project_name, "title": f"{project_name}发改委申报材料"}
            )

        if output_path:
            logger.info(f"项目{project_id}DOCX导出成功: {output_path}")
            await _update_project_status(project_id, "generated")
        else:
            logger.error(f"项目{project_id}DOCX导出失败")
            await _update_project_status(project_id, "generation_failed")

    except Exception as e:
        logger.error(f"项目{project_id}生成失败: {e}", exc_info=True)
        try:
            await _update_project_status(project_id, "generation_failed")
        except Exception:
            pass
    finally:
        if project_id in _generators:
            del _generators[project_id]


# ===== 路由 =====

@router.post("/projects/{project_id}/generate")
async def trigger_generation(
    project_id: int,
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
):
    """触发文档生成

    在后台执行文档生成任务，可通过进度接口查询状态。
    """
    # 检查项目是否存在
    project = await _get_project_data(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

    # 检查是否正在生成
    if project_id in _generators:
        raise HTTPException(status_code=409, detail="该项目正在生成中，请等待完成")

    # 验证数据源路径
    data_source_path = project["data_source_path"]
    if not Path(data_source_path).exists():
        raise HTTPException(
            status_code=400,
            detail=f"数据源路径不存在: {data_source_path}"
        )

    # 启动后台生成任务
    background_tasks.add_task(
        run_generation,
        project_id,
        request.chapter_ids,
        data_source_path,
    )

    logger.info(f"项目{project_id}文档生成已启动，章节: {request.chapter_ids or '全部'}")
    return {
        "status": "started",
        "message": "文档生成已启动",
        "project_id": project_id,
        "chapters": request.chapter_ids or [f"chapter{i}" for i in range(1, 8)],
    }


@router.get("/projects/{project_id}/generate/status")
async def get_generation_status(project_id: int):
    """获取文档生成进度

    返回当前生成任务的状态、进度百分比和已完成章节数。
    """
    # 检查项目是否存在
    project = await _get_project_data(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

    # 如果有活跃的生成器实例，返回实时进度
    if project_id in _generators:
        generator = _generators[project_id]
        progress = generator.get_generation_progress()
        return progress

    # 没有活跃的生成器，根据项目状态返回
    status_map = {
        "generated": {
            "status": "completed",
            "current_step": "文档生成完成",
            "progress_percent": 100,
            "chapters_completed": 7,
            "total_chapters": 7,
            "message": "文档已生成完毕",
        },
        "generation_failed": {
            "status": "error",
            "current_step": "生成失败",
            "progress_percent": 0,
            "chapters_completed": 0,
            "total_chapters": 7,
            "message": "文档生成失败，请重试",
        },
    }

    return status_map.get(project["status"], {
        "status": "idle",
        "current_step": "",
        "progress_percent": 0,
        "chapters_completed": 0,
        "total_chapters": 7,
        "message": "就绪，尚未开始生成",
    })


@router.get("/projects/{project_id}/download")
async def download_latest_document(project_id: int):
    """下载最新生成的文档

    查找OUTPUT_DIR下该项目最新生成的DOCX文件并返回。
    """
    # 检查项目是否存在
    project = await _get_project_data(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

    # 查找该项目的DOCX文件（按修改时间排序，取最新）
    pattern = f"project_{project_id}_*"
    docx_files = sorted(
        OUTPUT_DIR.glob(f"{pattern}.docx"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not docx_files:
        raise HTTPException(
            status_code=404,
            detail=f"未找到项目{project_id}的已生成文档，请先触发生成"
        )

    latest_file = docx_files[0]

    # 构造下载文件名（中文名需要URL编码）
    download_name = latest_file.name
    encoded_name = quote(download_name)

    return FileResponse(
        path=str(latest_file),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
        },
    )


@router.get("/projects/{project_id}/documents")
async def list_project_documents(project_id: int):
    """获取已生成文档列表

    扫描OUTPUT_DIR下属于该项目的所有DOCX文件。
    """
    # 检查项目是否存在
    project = await _get_project_data(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

    # 扫描该项目的文件
    pattern = f"project_{project_id}_*"
    docx_files = sorted(
        OUTPUT_DIR.glob(f"{pattern}.docx"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    documents = []
    for file_path in docx_files:
        try:
            stat = file_path.stat()
            from backend.parsers.utils import format_file_size
            documents.append({
                "filename": file_path.name,
                "path": str(file_path),
                "size": stat.st_size,
                "size_formatted": format_file_size(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
            })
        except (OSError, PermissionError) as e:
            logger.warning(f"无法获取文件信息: {file_path}, 错误: {e}")

    return {
        "project_id": project_id,
        "documents": documents,
        "total": len(documents),
    }
