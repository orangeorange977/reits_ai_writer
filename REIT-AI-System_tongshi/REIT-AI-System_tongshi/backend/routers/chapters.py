"""章节管理路由

提供章节状态查询、数据提取和手动编辑功能。
"""

import json
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database.db import get_db
from backend.generators import NDRCGenerator
from backend.mappings import load_ndrc_chapter_mapping, get_chapter_by_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["章节管理"])


# ===== 请求/响应模型 =====

class ChapterInfoResponse(BaseModel):
    """章节信息摘要"""
    id: Optional[int] = None
    chapter_id: str
    title: str
    status: str
    section_count: int = 0


class ChapterDetailResponse(BaseModel):
    """章节详情（含提取数据）"""
    chapter_id: str
    title: str
    status: str
    sections: List[Dict[str, Any]] = []
    extraction_summary: Optional[Dict[str, Any]] = None


class UpdateChapterDataRequest(BaseModel):
    """更新章节数据请求"""
    fields: Dict[str, Any]  # {field_id: value}


# ===== 路由 =====

@router.get("/projects/{project_id}/chapters", response_model=List[ChapterInfoResponse])
async def list_chapters(project_id: int):
    """获取项目所有章节状态列表"""
    db = await get_db()
    try:
        # 验证项目存在
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

        # 从映射配置加载章节定义
        mapping = load_ndrc_chapter_mapping()
        chapters_config = mapping.get("chapters", [])

        # 查询数据库中该项目的章节状态
        cursor = await db.execute(
            "SELECT id, chapter_id, title, status, data_json FROM chapters WHERE project_id = ?",
            (project_id,)
        )
        db_chapters = await cursor.fetchall()
        db_chapter_map = {}
        for ch in db_chapters:
            db_chapter_map[ch[1]] = {
                "id": ch[0],
                "status": ch[3],
                "data_json": ch[4],
            }

        # 合并配置与数据库状态
        result = []
        for chapter_conf in chapters_config:
            chapter_id = chapter_conf["id"]
            db_info = db_chapter_map.get(chapter_id)

            result.append(ChapterInfoResponse(
                id=db_info["id"] if db_info else None,
                chapter_id=chapter_id,
                title=chapter_conf["title"],
                status=db_info["status"] if db_info else "pending",
                section_count=len(chapter_conf.get("sections", [])),
            ))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取章节列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取章节列表失败: {e}")
    finally:
        await db.close()


@router.get("/projects/{project_id}/chapters/{chapter_id}", response_model=ChapterDetailResponse)
async def get_chapter_detail(project_id: int, chapter_id: str):
    """获取章节详情（含提取数据）"""
    db = await get_db()
    try:
        # 验证项目存在
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

        # 验证章节ID有效
        chapter_conf = get_chapter_by_id(chapter_id)
        if not chapter_conf:
            raise HTTPException(status_code=404, detail=f"章节不存在: {chapter_id}")

        # 查询数据库中的章节数据
        cursor = await db.execute(
            "SELECT id, chapter_id, title, status, data_json FROM chapters WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id)
        )
        row = await cursor.fetchone()

        if row and row[4]:
            # 有已提取的数据
            data = json.loads(row[4])
            return ChapterDetailResponse(
                chapter_id=chapter_id,
                title=chapter_conf["title"],
                status=row[3],
                sections=data.get("sections", []),
                extraction_summary=data.get("extraction_summary"),
            )
        else:
            # 尚未提取数据，返回章节配置结构
            sections = []
            for section in chapter_conf.get("sections", []):
                section_data = {
                    "section_id": section["id"],
                    "title": section["title"],
                }

                if section.get("has_subsections") and section.get("subsections"):
                    # 有子模块的section
                    section_data["has_subsections"] = True
                    subsections_data = []
                    for subsection in section["subsections"]:
                        sub_fields = []
                        for field_def in subsection.get("fields", []):
                            field_dict = {
                                "id": field_def["id"],
                                "label": field_def["label"],
                                "type": field_def.get("type", "text"),
                                "value": None,
                                "source": "",
                                "confidence": 0.0,
                            }
                            # 表格类型额外传递列定义和前置文本模板
                            if field_def.get("type") == "table":
                                field_dict["columns"] = field_def.get("columns", [])
                                if field_def.get("template_text"):
                                    field_dict["template_text"] = field_def["template_text"]
                            elif field_def.get("type") == "form_table":
                                field_dict["rows"] = field_def.get("rows", [])
                            if field_def.get("placeholder"):
                                field_dict["placeholder"] = field_def["placeholder"]
                            sub_fields.append(field_dict)
                        subsections_data.append({
                            "id": subsection["id"],
                            "title": subsection["title"],
                            "fields": sub_fields,
                        })
                    section_data["subsections"] = subsections_data
                else:
                    # 普通section，直接包含fields
                    fields = []
                    for field_def in section.get("fields", []):
                        field_dict = {
                            "id": field_def["id"],
                            "label": field_def["label"],
                            "type": field_def.get("type", "text"),
                            "value": None,
                            "source": "",
                            "confidence": 0.0,
                        }
                        # 表格类型额外传递列定义和前置文本模板
                        if field_def.get("type") == "table":
                            field_dict["columns"] = field_def.get("columns", [])
                            if field_def.get("template_text"):
                                field_dict["template_text"] = field_def["template_text"]
                        elif field_def.get("type") == "form_table":
                            field_dict["rows"] = field_def.get("rows", [])
                        if field_def.get("placeholder"):
                            field_dict["placeholder"] = field_def["placeholder"]
                        fields.append(field_dict)
                    section_data["fields"] = fields

                sections.append(section_data)

            return ChapterDetailResponse(
                chapter_id=chapter_id,
                title=chapter_conf["title"],
                status=row[3] if row else "pending",
                sections=sections,
                extraction_summary=None,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取章节详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取章节详情失败: {e}")
    finally:
        await db.close()


@router.put("/projects/{project_id}/chapters/{chapter_id}/data")
async def update_chapter_data(project_id: int, chapter_id: str, request: UpdateChapterDataRequest):
    """更新章节数据（用户手动编辑）"""
    db = await get_db()
    try:
        # 验证项目存在
        cursor = await db.execute("SELECT id FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

        # 验证章节ID有效
        chapter_conf = get_chapter_by_id(chapter_id)
        if not chapter_conf:
            raise HTTPException(status_code=404, detail=f"章节不存在: {chapter_id}")

        # 查询现有章节数据
        cursor = await db.execute(
            "SELECT id, data_json FROM chapters WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id)
        )
        row = await cursor.fetchone()

        if row:
            # 更新已有记录
            existing_data = json.loads(row[1]) if row[1] else {"sections": []}
            # 将fields中的值更新到对应位置
            existing_data = _apply_field_updates(existing_data, request.fields)
            await db.execute(
                "UPDATE chapters SET data_json = ?, status = 'extracted', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(existing_data, ensure_ascii=False), row[0])
            )
        else:
            # 创建新记录
            new_data = _build_chapter_data_from_fields(chapter_conf, request.fields)
            await db.execute(
                "INSERT INTO chapters (project_id, chapter_id, title, status, data_json) VALUES (?, ?, ?, ?, ?)",
                (project_id, chapter_id, chapter_conf["title"], "extracted",
                 json.dumps(new_data, ensure_ascii=False))
            )

        await db.commit()
        logger.info(f"章节数据已更新: project={project_id}, chapter={chapter_id}")
        return {"message": "章节数据已更新", "chapter_id": chapter_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新章节数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新章节数据失败: {e}")
    finally:
        await db.close()


@router.post("/projects/{project_id}/chapters/{chapter_id}/extract")
async def extract_chapter_data(project_id: int, chapter_id: str):
    """触发单章节数据提取"""
    db = await get_db()
    try:
        # 获取项目信息
        cursor = await db.execute(
            "SELECT id, data_source_path FROM projects WHERE id = ?",
            (project_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

        data_source_path = row[1]

        # 验证章节ID有效
        chapter_conf = get_chapter_by_id(chapter_id)
        if not chapter_conf:
            raise HTTPException(status_code=404, detail=f"章节不存在: {chapter_id}")

        # 更新章节状态为提取中
        cursor = await db.execute(
            "SELECT id FROM chapters WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id)
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE chapters SET status = 'extracting', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing[0],)
            )
        else:
            await db.execute(
                "INSERT INTO chapters (project_id, chapter_id, title, status) VALUES (?, ?, ?, ?)",
                (project_id, chapter_id, chapter_conf["title"], "extracting")
            )
        await db.commit()

        # 调用NDRCGenerator进行数据提取
        generator = NDRCGenerator(data_source_path)
        extract_result = generator.extract_chapter_data(chapter_id)

        # 保存提取结果
        data_json = json.dumps(extract_result, ensure_ascii=False)
        cursor = await db.execute(
            "SELECT id FROM chapters WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id)
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE chapters SET data_json = ?, status = 'extracted', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (data_json, existing[0])
            )
        await db.commit()

        logger.info(f"章节数据提取完成: project={project_id}, chapter={chapter_id}")
        return extract_result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"章节数据提取失败: {e}")
        raise HTTPException(status_code=500, detail=f"章节数据提取失败: {e}")
    finally:
        await db.close()


# ===== 辅助函数 =====

def _apply_field_updates(data: Dict, fields: Dict[str, Any]) -> Dict:
    """将字段更新应用到章节数据中"""
    sections = data.get("sections", [])
    for section in sections:
        for field in section.get("fields", []):
            if field.get("id") in fields:
                field["value"] = fields[field["id"]]
                field["source"] = "用户手动编辑"
                field["confidence"] = 1.0
    data["sections"] = sections
    return data


def _build_chapter_data_from_fields(chapter_conf: Dict, fields: Dict[str, Any]) -> Dict:
    """从字段数据构建章节数据结构"""
    sections = []
    for section in chapter_conf.get("sections", []):
        section_fields = []
        for field_def in section.get("fields", []):
            field_id = field_def["id"]
            section_fields.append({
                "id": field_id,
                "label": field_def["label"],
                "type": field_def.get("type", "text"),
                "value": fields.get(field_id),
                "source": "用户手动编辑" if field_id in fields else "",
                "confidence": 1.0 if field_id in fields else 0.0,
            })
        sections.append({
            "section_id": section["id"],
            "title": section["title"],
            "fields": section_fields,
        })

    return {
        "chapter_id": chapter_conf["id"],
        "chapter_title": chapter_conf["title"],
        "sections": sections,
        "extraction_summary": None,
    }
