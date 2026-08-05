"""项目管理路由

提供项目的CRUD操作（新管线：项目↔模板包绑定，章节生成走 skills 路由）。
"""

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.database.db import get_db, is_preset_project
from backend.services import pack_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["项目管理"])


# ===== 请求/响应模型 =====

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str
    data_source_path: str
    pack_id: Optional[str] = None  # 绑定的模板包；不传时绑默认包


class ProjectResponse(BaseModel):
    """项目响应"""
    id: int
    name: str
    data_source_path: str
    status: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_demo: bool = False
    pack_id: Optional[str] = None


# ===== 路由 =====

@router.get("/projects", response_model=List[ProjectResponse])
async def list_projects():
    """获取项目列表"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status, created_at, updated_at, pack_id "
            "FROM projects ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        projects = []
        for row in rows:
            projects.append(ProjectResponse(
                id=row[0],
                name=row[1],
                data_source_path=row[2],
                status=row[3],
                created_at=row[4],
                updated_at=row[5],
                is_demo=is_preset_project(row[2]),
                pack_id=row[6],
            ))
        return projects
    except Exception as e:
        logger.error(f"获取项目列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取项目列表失败: {e}")
    finally:
        await db.close()


@router.post("/projects", response_model=ProjectResponse)
async def create_project(request: CreateProjectRequest):
    """创建新项目"""
    # 验证路径是否存在
    source_path = Path(request.data_source_path)
    if not source_path.exists():
        raise HTTPException(status_code=400, detail=f"数据源路径不存在: {request.data_source_path}")
    if not source_path.is_dir():
        raise HTTPException(status_code=400, detail=f"数据源路径不是文件夹: {request.data_source_path}")

    # 校验/解析要绑定的模板包：不传时绑默认包，传了不存在的包则报错
    try:
        if request.pack_id:
            pack_service.get_pack(request.pack_id)
            pack_id = request.pack_id
        else:
            pack_id = pack_service.default_pack_id()
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO projects (name, data_source_path, status, pack_id) VALUES (?, ?, ?, ?)",
            (request.name, request.data_source_path, "active", pack_id)
        )
        await db.commit()
        project_id = cursor.lastrowid

        # 查询刚创建的项目
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status, created_at, updated_at, pack_id FROM projects WHERE id = ?",
            (project_id,)
        )
        row = await cursor.fetchone()
        logger.info(f"项目创建成功: {request.name} (ID={project_id}, 模板包={pack_id})")
        return ProjectResponse(
            id=row[0],
            name=row[1],
            data_source_path=row[2],
            status=row[3],
            created_at=row[4],
            updated_at=row[5],
            is_demo=is_preset_project(row[2]),
            pack_id=row[6],
        )
    except Exception as e:
        logger.error(f"创建项目失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建项目失败: {e}")
    finally:
        await db.close()


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: int):
    """获取项目详情"""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status, created_at, updated_at, pack_id FROM projects WHERE id = ?",
            (project_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")
        return ProjectResponse(
            id=row[0],
            name=row[1],
            data_source_path=row[2],
            status=row[3],
            created_at=row[4],
            updated_at=row[5],
            is_demo=is_preset_project(row[2]),
            pack_id=row[6],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取项目详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取项目详情失败: {e}")
    finally:
        await db.close()


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int):
    """删除项目"""
    db = await get_db()
    try:
        # 检查项目是否存在
        cursor = await db.execute("SELECT id, data_source_path FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

        # 保护预置示范项目不被删除
        if is_preset_project(row[1]):
            raise HTTPException(status_code=403, detail="示范项目不可删除")

        # 删除关联的章节数据
        await db.execute("DELETE FROM chapters WHERE project_id = ?", (project_id,))
        # 删除项目
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()

        logger.info(f"项目已删除: ID={project_id}")
        return {"message": f"项目已删除", "id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除项目失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除项目失败: {e}")
    finally:
        await db.close()

