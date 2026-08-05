"""模板包管理路由

列出可用模板包（供新建项目时选"材料模板"）与单包详情（manifest + 章节结构）。
"""

import logging

from fastapi import APIRouter, HTTPException

from backend.services import pack_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/packs", tags=["模板包"])


@router.get("")
async def list_packs():
    """可用模板包列表（id/名称/版本等 manifest 字段）+ 当前默认包。"""
    try:
        return {
            "packs": pack_service.list_packs(),
            "default_id": pack_service.default_pack_id(),
        }
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取模板包列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取模板包列表失败：{e}")


@router.get("/{pack_id}")
async def get_pack_detail(pack_id: str):
    """单包详情：manifest + 章节结构（前端步骤条标题按此渲染）。"""
    try:
        pack = pack_service.get_pack(pack_id)
        chapters = pack_service.get_chapters(pack_id)
        chapter_list = sorted(
            ({"n": n, "title": c["title"]} for n, c in chapters.items()),
            key=lambda x: x["n"],
        )
        return {"pack": pack["manifest"], "chapters": chapter_list}
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取模板包详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取模板包详情失败：{e}")
