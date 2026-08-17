"""模板包管理路由

列出可用模板包（供新建项目时选"材料模板"）与单包详情（manifest + 章节结构）。
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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


# ===== Skill 文本管理（预览/修改/重置） =====

def _editable_skills(pack_id: str) -> list:
    """可编辑的文本 skill 清单：各章写作要求 + 全局总纲 + 写作/排版要求。"""
    chapters = pack_service.get_chapters(pack_id)
    files = []
    for n, c in sorted(chapters.items()):
        files.append({"rel": c["reading"], "kind": "chapter", "n": n,
                      "label": f"第{n}章 · {c['title']}"})
    files.append({"rel": "planning.md", "kind": "global", "label": "全局总纲 planning.md"})
    files.append({"rel": "writing/SKILL.md", "kind": "global", "label": "写作/排版要求 writing/SKILL.md"})
    return files


def _assert_editable(rel: str, pack_id: str) -> dict:
    for f in _editable_skills(pack_id):
        if f["rel"] == rel:
            return f
    raise HTTPException(status_code=400, detail=f"该文件不支持在线编辑：{rel}")


def _require_admin(http_req: Request):
    user = getattr(http_req.state, "user", None) or {}
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改 Skill")


@router.get("/{pack_id}/skills")
async def list_skills(pack_id: str):
    """可编辑 skill 清单 + 是否已被用户修改。"""
    try:
        out = []
        for f in _editable_skills(pack_id):
            p = pack_service.skill_text_path(f["rel"], pack_id)
            out.append({
                **f,
                "overridden": pack_service.is_overridden(f["rel"], pack_id),
                "chars": p.stat().st_size if p.exists() else 0,
            })
        return {"skills": out}
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{pack_id}/skill")
async def get_skill(pack_id: str, rel: str):
    """单个 skill 内容：生效内容 + 代码默认内容（供对照/重置）。"""
    meta = _assert_editable(rel, pack_id)
    try:
        eff = pack_service.skill_text_path(rel, pack_id)
        default = pack_service.pack_path(rel, pack_id)
        return {
            **meta,
            "content": eff.read_text(encoding="utf-8") if eff.exists() else "",
            "default_content": default.read_text(encoding="utf-8") if default.exists() else "",
            "overridden": pack_service.is_overridden(rel, pack_id),
        }
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 Skill 失败：{e}")


class SkillSaveBody(BaseModel):
    rel: str
    content: str


@router.post("/{pack_id}/skill/save")
async def save_skill(pack_id: str, body: SkillSaveBody, http_req: Request):
    """保存用户修改：写入数据卷覆盖层，生成链路即时生效。"""
    _require_admin(http_req)
    _assert_editable(body.rel, pack_id)
    try:
        ov = pack_service.override_path(body.rel, pack_id)
        ov.parent.mkdir(parents=True, exist_ok=True)
        ov.write_text(body.content, encoding="utf-8")
        return {"ok": True, "overridden": True}
    except pack_service.PackNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存 Skill 失败：{e}")


@router.post("/{pack_id}/skill/reset")
async def reset_skill(pack_id: str, body: SkillSaveBody, http_req: Request):
    """重置为代码默认：删除覆盖件。"""
    _require_admin(http_req)
    _assert_editable(body.rel, pack_id)
    try:
        pack_service.override_path(body.rel, pack_id).unlink(missing_ok=True)
        return {"ok": True, "overridden": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重置 Skill 失败：{e}")
