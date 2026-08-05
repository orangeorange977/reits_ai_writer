"""Skill 执行路由 - 让 Kimi 按各章 SKILL.md 生成章节内容

因为 Kimi 生成一章可能要几分钟，这里做成异步任务：
POST /skills/ch1/run     立即返回，后台开始跑
GET  /skills/ch1/status  前端轮询，拿到 running / done(+data) / error
"""
import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import NDRC_OFFICIAL_TEMPLATE, DEFAULT_PROJECT_ID, PROJECTS_DIR
from backend.database.db import (get_project_pack_id, get_project_owner_id,
                                 upsert_generation_job, get_generation_job)
from backend.services import skill_runner, summary_service, materials_client, pack_service
from backend.services.kimi_client import chat

router = APIRouter(tags=["Skill执行"], prefix="/skills")
logger = logging.getLogger(__name__)


async def _project_pack_id(project_id: str) -> Optional[str]:
    """项目绑定的模板包；未绑包/项目不存在/默认项目时返回 None（用默认包）。"""
    pid = _norm_pid(project_id)
    if not pid or pid == DEFAULT_PROJECT_ID:
        return None
    return await get_project_pack_id(pid)


def _current_user_id(http_req: Request) -> int:
    """从中间件解析的 token payload 里取当前用户 ID（步骤 3.5）。"""
    user = getattr(http_req.state, "user", None) or {}
    try:
        return int(user.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")


async def _assert_project_access(project_id: str, user_id: int):
    """项目数据访问归属校验（步骤 3.5）：
    空/默认项目放行；其余必须在 DB 中存在且归属当前用户，否则 404（不泄露存在性）。"""
    pid = _norm_pid(project_id)
    if not pid or pid == DEFAULT_PROJECT_ID:
        return
    owner = await get_project_owner_id(pid)
    if owner is None or owner != user_id:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={pid}")


def _resolve_template_path(user_path: str = "", pack_id: str = None) -> str:
    """解析模板路径：用户提供的路径无效时，自动回退到项目所绑模板包的内置模板。

    回退顺序：
    1. 项目绑定模板包自带的 template.docx
    2. config 中的 NDRC_OFFICIAL_TEMPLATE（workspace 路径，部署场景）
    3. 项目源码目录 backend/templates/official/ndrc_2024.docx（开发场景）
    """
    tpl = (user_path or "").strip()
    if tpl and Path(tpl).exists():
        return tpl
    # 回退 1：绑定模板包自带模板
    try:
        pack_tpl = pack_service.template_docx(pack_id)
        if pack_tpl.exists():
            return str(pack_tpl)
    except Exception as e:
        logger.warning(f"读取模板包模板失败: {e}")
    # 回退 2：config 中配置的路径
    default = str(NDRC_OFFICIAL_TEMPLATE)
    if Path(default).exists():
        return default
    # 回退 3：项目源码目录下的模板（开发环境）
    project_template = Path(__file__).resolve().parents[1] / "templates" / "official" / "ndrc_2024.docx"
    if project_template.exists():
        return str(project_template)
    return ""


def _load_web_render(pack_id: str = None):
    """加载项目绑定模板包内的 web_render 渲染脚本（写作规则随包走，保留热重载机制）。"""
    return skill_runner.load_web_render(pack_id)


@router.get("/models")
async def list_models():
    """列出该 key 可用的 Kimi 模型 + 当前所选（供系统设置页下拉）。"""
    def _query():
        try:
            from backend.services.kimi_client import get_client
            client = get_client()
            return [m.id for m in client.models.list().data]
        except Exception as e:
            logger.warning(f"查询模型列表失败: {e}")
            return []
    models = await asyncio.to_thread(_query)
    return {"models": models, "current": skill_runner.get_selected_model()}


class ModelBody(BaseModel):
    model: str


@router.post("/model")
async def set_model(body: ModelBody):
    """保存所选 Kimi 模型（各章生成即时生效，无需重启）。"""
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="模型名不能为空")
    await asyncio.to_thread(skill_runner.set_selected_model, body.model.strip())
    return {"status": "ok", "model": body.model.strip()}


class AIEditBody(BaseModel):
    text: str = ""          # 选中的原文（可为空=让AI直接创作）
    instruction: str        # 用户下的指令（润色/改写/扩写/精简/自定义…）


@router.post("/ai-edit")
async def ai_edit(body: AIEditBody):
    """AI 辅助写作：给一段选中文字 + 一条指令，返回处理后的文字（用当前所选 Kimi 模型）。"""
    instruction = (body.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="请填写指令")
    text = body.text or ""

    def _do():
        system = (
            f"你是{pack_service.material_label()}的中文写作助手。用户会给你一条【指令】和一段【原文】，"
            "请严格按指令处理这段原文，只返回处理后的正文本身：不要解释、不要加引号、"
            "不要加“以下是”之类前后缀、不要用代码块包裹。保持正式的申报材料书面文体。"
            "若【原文】为空，则按指令直接写出一段合适的正文。"
        )
        user = f"【指令】\n{instruction}\n\n【原文】\n{text}"
        return chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=skill_runner.get_selected_model(), temperature=1.0,
        )

    try:
        result = await asyncio.to_thread(_do)
        return {"status": "ok", "result": (result or "").strip()}
    except Exception as e:
        logger.error(f"AI辅助写作失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI处理失败：{e}")


# 每个素材最多取多少字 / 所有素材合计上限（防止撑爆上下文）
_AI_SRC_CAP = 6000
_AI_TOTAL_CAP = 26000


@router.post("/ai-compose")
async def ai_compose(
    instruction: str = Form(...),
    selected_text: str = Form(""),
    pasted_text: str = Form(""),
    urls: str = Form(""),                       # 每行一个链接
    files: list[UploadFile] = File(default=None),
):
    """AI 辅助写作（增强版）：综合用户的【指令】+【选中原文】+ 多种素材
    （粘贴文字 / 网页链接 / 上传文件：Word/PPT/Excel/PDF/图片）写出想要的正文。"""
    instruction = (instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="请填写指令")
    # 在异步上下文里先把上传文件读成字节（UploadFile 不能带进线程后再读）
    uploaded = []
    for f in (files or []):
        try:
            uploaded.append((f.filename or "上传文件", await f.read()))
        finally:
            await f.close()

    def _do():
        import os
        import tempfile
        blocks, total = [], 0

        def add(label: str, content: str):
            nonlocal total
            content = (content or "").strip()
            if not content or total >= _AI_TOTAL_CAP:
                return
            snippet = content[:_AI_SRC_CAP]
            blocks.append(f"【素材：{label}】\n{snippet}")
            total += len(snippet)

        if pasted_text.strip():
            add("粘贴的文字", pasted_text)
        for line in (urls or "").splitlines():
            u = line.strip()
            if u:
                add(f"网页 {u}", materials_client.fetch_url_text(u, limit=_AI_SRC_CAP))
        for fname, data in uploaded:
            if total >= _AI_TOTAL_CAP:
                break
            suffix = os.path.splitext(fname)[1] or ".bin"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            try:
                tmp.write(data)
                tmp.close()
                # 图片/扫描件按“指令”作为识别侧重点
                txt = materials_client.extract_file_text(Path(tmp.name), query=instruction)
                add(f"文件 {fname}", txt)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        material = "\n\n".join(blocks)
        system = (
            f"你是{pack_service.material_label()}的中文写作助手。用户会给你一条【指令】，可能还有一段【选中的原文】"
            "以及若干【素材】（用户粘贴的文字、网页链接内容、上传文件的内容）。请理解并综合这些素材，"
            "严格按【指令】写出用户想要的正文。只返回正文本身：不要解释、不要加引号、不要“以下是”之类前后缀、"
            "不要用代码块包裹，保持正式的申报材料书面文体。素材仅作依据，请甄别提炼、按需引用，"
            "不要照抄无关内容，也不要编造素材里没有的关键数据；素材相互矛盾时以更权威、更新的为准并可注明。"
        )
        parts = [f"【指令】\n{instruction}"]
        if selected_text.strip():
            parts.append(f"【选中的原文】\n{selected_text.strip()}")
        if material:
            parts.append(material)
        return chat(
            [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}],
            model=skill_runner.get_selected_model(), temperature=1.0,
        )

    try:
        result = await asyncio.to_thread(_do)
        return {"status": "ok", "result": (result or "").strip()}
    except Exception as e:
        logger.error(f"AI综合写作失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI处理失败：{e}")


@router.get("/diagram-templates")
async def diagram_templates():
    """列出可选的画图模板（默认模板包 diagrams/ 下的 .drawio 文件）。"""
    def _list():
        try:
            tpl_dir = pack_service.diagram_dir()
        except Exception:
            return []
        if not tpl_dir.exists():
            return []
        items = []
        for p in sorted(tpl_dir.glob("*.drawio")):
            items.append({"name": p.stem, "label": p.stem})
        return items
    return {"templates": await asyncio.to_thread(_list)}


@router.get("/diagram-template")
async def diagram_template(name: str):
    """返回某个模板的 draw.io XML。"""
    # 防目录穿越：只用文件名
    safe = Path(name).name
    path = pack_service.diagram_dir() / f"{safe}.drawio"
    if not path.exists():
        raise HTTPException(status_code=404, detail="模板不存在")
    xml = await asyncio.to_thread(path.read_text, "utf-8")
    return {"name": safe, "xml": xml}


@router.get("/summary")
async def get_summary(http_req: Request, project_id: str = ""):
    """摘要表 / 释义（来自申报定稿 docx）+ 其他基本信息，按项目隔离。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        data = await asyncio.to_thread(summary_service.get_summary_data, project_id or None)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error(f"获取摘要表/释义失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取摘要表/释义失败：{e}")


class SummaryData(BaseModel):
    summary_table: list = []
    glossary: list = []
    other_info: list = []


@router.post("/summary/save")
async def save_summary(data: SummaryData, http_req: Request, project_id: str = ""):
    """保存网页上编辑好的摘要表/释义/其他基本信息到该项目（写入 JSON，之后该项目各章生成都以此为准）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        await asyncio.to_thread(summary_service.save_summary_data, data.model_dump(), project_id or None)
        return {"status": "ok", "message": "已保存"}
    except Exception as e:
        logger.error(f"保存摘要表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


@router.post("/summary/import-excel")
async def import_summary_excel(file: UploadFile = File(...)):
    """导入用户上传的 Excel（三个 sheet：摘要表/释义/其他基本信息），返回解析后的键值数据。"""
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 或 .xls 文件")
    try:
        content = await file.read()
        data = await asyncio.to_thread(summary_service.parse_import_excel, content)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error(f"解析上传的 Excel 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析 Excel 失败：{e}")

# 各章生成任务状态：内存字典供本进程实时轮询，同时落 DB generation_jobs 表
# （步骤 3.5：重启后已完成任务仍可见，多 worker 部署的状态共享基础），按 (project_id, 章节号) 存
_jobs = {}    # (pid, n) -> {"status","data","error"}
# 后台 task 的强引用：asyncio 只对 task 持弱引用，不保存会被 GC 掉，导致状态卡在 running
_tasks = {}   # (pid, n) -> asyncio.Task


def _norm_pid(project_id: str = "") -> str:
    return str(project_id or "").strip()


def _job(key):
    return _jobs.get(key, {"status": "idle", "data": None, "error": None})


async def _save_job_to_db(key, state: dict):
    """任务状态落 DB（失败不阻断生成本身）。"""
    import json as _json
    pid, n = key
    try:
        data_json = _json.dumps(state.get("data"), ensure_ascii=False) if state.get("data") is not None else None
        await upsert_generation_job(pid, n, state.get("status", "idle"), data_json, state.get("error"))
    except Exception as e:
        logger.warning(f"生成任务状态落库失败（不影响生成）：{e}")


def _valid_chapter(n: int, pack_id: str = None):
    if n not in skill_runner.chapters_for(pack_id):
        raise HTTPException(status_code=404, detail=f"章节 {n} 不存在")


async def _run_chapter_job_with_subs(key, n: int, subtitles: list,
                                     materials_path: str = "", project_id: str = "",
                                     pack_id: str = None):
    try:
        result = await asyncio.to_thread(
            skill_runner.run_chapter, n, subtitles, materials_path,
            project_id or None, pack_id)
        _jobs[key] = {"status": "done", "data": result, "error": None}
        await _save_job_to_db(key, _jobs[key])
        logger.info(f"第{n}章 skill 执行完成（项目 {project_id or '默认'}，模板包 {pack_id or '默认'}）")
    except Exception as e:
        logger.error(f"第{n}章 skill 执行失败: {e}", exc_info=True)
        _jobs[key] = {"status": "error", "data": None, "error": str(e)}
        await _save_job_to_db(key, _jobs[key])


@router.post("/chapter/{n}/run")
async def chapter_run(n: int, http_req: Request, template_path: str = "",
                      materials_path: str = "", project_id: str = ""):
    """启动第 n 章生成（后台异步），立即返回。template_path 有效时强制用模板小标题；
    材料目录有效时把"读取申报材料"的工具挂给 Kimi；project_id 决定数据落在哪个项目目录，
    并按项目绑定的模板包执行。

    步骤 3.4：materials_path 不再需要前端传——留空时自动解析到项目上传的材料目录
    workspace/projects/<id>/materials/（参数保留仅供向后兼容）。
    """
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    pid = _norm_pid(project_id)
    key = (pid, n)
    if _job(key)["status"] == "running":
        raise HTTPException(status_code=409, detail=f"第{n}章正在生成中，请稍候")
    subs = []
    tpl = _resolve_template_path(template_path, pack_id)
    if tpl:
        subs = await asyncio.to_thread(skill_runner.chapter_subtitles, n, tpl, pack_id)
    # 材料目录：优先用显式传入的，否则落回项目上传的材料目录（非空才挂工具）
    mat = materials_path.strip()
    if not mat:
        candidate = PROJECTS_DIR / pid / "materials"
        if candidate.is_dir() and any(candidate.iterdir()):
            mat = str(candidate)
    _jobs[key] = {"status": "running", "data": None, "error": None}
    await _save_job_to_db(key, _jobs[key])
    _tasks[key] = asyncio.create_task(
        _run_chapter_job_with_subs(key, n, subs, mat, pid, pack_id))
    return {"status": "started", "message": f"第{n}章生成已启动，请稍候（Kimi 处理约需数分钟）"}


@router.get("/chapter/{n}/status")
async def chapter_status(n: int, http_req: Request, project_id: str = ""):
    """查询第 n 章生成进度/结果（按项目隔离）；内存无记录时落回 DB（重启后仍可见）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    key = (_norm_pid(project_id), n)
    if key in _jobs:
        return _jobs[key]
    stored = await get_generation_job(key[0], n)
    return stored or {"status": "idle", "data": None, "error": None}


@router.get("/chapter/{n}/content")
async def chapter_content(n: int, http_req: Request, template_path: str = "", project_id: str = ""):
    """第 n 章可编辑内容：以官方模板的本章小标题为骨架，合并该项目已保存/已生成的内容。

    template_path（来自系统设置）有效时，即使还没生成，也能看到该章的小标题结构。
    若前端未传或路径无效，自动回退到项目绑定模板包的内置模板。
    """
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    pid = project_id or None

    def _do():
        subs = []
        tables = {}
        table_start = 1
        tpl = _resolve_template_path(template_path, pack_id)
        if tpl:
            subs = skill_runner.chapter_subtitles(n, tpl, pack_id)
            tables = skill_runner.chapter_tables(n, tpl, pack_id)
            table_start = skill_runner.chapter_table_start(n, tpl, pack_id)
        return skill_runner.get_chapter_content(n, subs, tables, table_start, pid)

    return await asyncio.to_thread(_do)


class ChapterSaveBody(BaseModel):
    sections: list = []


@router.post("/chapter/{n}/save")
async def chapter_save(n: int, body: ChapterSaveBody, http_req: Request, project_id: str = ""):
    """保存用户编辑后的第 n 章内容到该项目（回传给最终版 JSON）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    try:
        await asyncio.to_thread(
            skill_runner.save_chapter_content, n, body.sections, project_id or None, pack_id)
        return {"status": "ok", "message": "已保存"}
    except Exception as e:
        logger.error(f"保存第{n}章内容失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


# 预览渲染缓存：{(pid, n): (signature, result)}。签名只取“会影响成稿的东西”——本章已保存内容(JSON)、
# 模板文件、排版配置的修改时间。改 skill/planning 不会改变这三样，故不会触发重渲染；
# 只有在编辑区保存(内容变)或重新生成时 JSON 变了，签名才变、才重跑。进程内单例、够用。
_PREVIEW_CACHE: dict = {}


def _preview_signature(n: int, template_path: str, project_id: str = "") -> str:
    tpl = (template_path or "").strip()
    parts = [tpl]
    srcs = [skill_runner.chapter_json_path(n, project_id or None), skill_runner.WRITE_CONFIG_PATH]
    if tpl:
        srcs.append(Path(tpl))
    for p in srcs:
        try:
            parts.append(str(p.stat().st_mtime))
        except OSError:
            parts.append("0")
    return "|".join(parts)


@router.get("/chapter/{n}/preview")
async def chapter_preview(n: int, http_req: Request, template_path: str = "", project_id: str = ""):
    """读该项目第 n 章 JSON -> 写入官方模板对应章节 + 返回预览 HTML。

    template_path 由网页"系统设置"里的模板文件路径传入；有效则写入模板、预览取自填好的模板，
    无效/未提供则自动回退到项目绑定模板包的内置模板；仍无效则回退到独立生成一份 Word。

    预览只对"编辑区内容"负责：不调用大模型、不因 skill/planning 改动而重跑；
    只有本章已保存内容(JSON)变化时才真正重新渲染，否则直接复用上次结果。
    """
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    pid = project_id or None
    key = (_norm_pid(project_id), n)
    cfg = skill_runner.chapters_for(pack_id)[n]
    docx_path = str(skill_runner.chapter_docx_path(n, pid))

    tpl_resolved = _resolve_template_path(template_path, pack_id)
    # 签名含模板包：同一项目换绑包后预览要重算
    sig = (pack_id or "") + "|" + _preview_signature(n, tpl_resolved, project_id)
    cached = _PREVIEW_CACHE.get(key)
    if cached and cached[0] == sig:
        return {"status": "ok", "cached": True, **cached[1]}

    def _do():
        sections = skill_runner.get_chapter_structured(n, pid)
        if not sections:
            return {"has_content": False, "html": "", "used_template": False}
        wr = _load_web_render(pack_id)
        if tpl_resolved:
            wr.render_into_template(sections, tpl_resolved, docx_path, cfg["title"], cfg["next"])
            html = wr.docx_to_preview_html(docx_path, cfg["title"], cfg["next"])
            return {"has_content": True, "html": html, "used_template": True}
        # 回退：没有有效模板路径时，独立生成一份
        wr.render_docx(sections, docx_path)
        html = wr.render_preview_html(sections)
        return {"has_content": True, "html": html, "used_template": False}

    try:
        result = await asyncio.to_thread(_do)
        if result.get("has_content"):
            _PREVIEW_CACHE[key] = (sig, result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"生成第{n}章预览失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成预览失败：{e}")


@router.get("/chapter/{n}/download")
async def chapter_download(n: int, http_req: Request, project_id: str = ""):
    """下载该项目第 n 章生成的 Word 文件。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    path = skill_runner.chapter_docx_path(n, project_id or None)
    if not path.exists():
        raise HTTPException(status_code=404, detail="尚未生成 Word，请先在预览处生成")
    title = skill_runner.chapters_for(pack_id)[n]["title"]
    name_part = title.split("、", 1)[-1] if "、" in title else title
    filename = f"第{n}章_{name_part}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/documents")
async def list_documents(http_req: Request, project_id: str = ""):
    """列出该项目已生成的各章 Word 文档（文档管理页数据源，替代旧管线 /projects/{id}/documents）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    chapters = skill_runner.chapters_for(pack_id)
    out_dir = skill_runner.chapter_docx_path(1, project_id or None).parent
    docs = []
    for f in sorted(out_dir.glob("ch*_output.docx")):
        m = re.match(r"ch(\d+)_output\.docx$", f.name)
        if not m:
            continue
        n = int(m.group(1))
        st = f.stat()
        docs.append({
            "chapter": n,
            "title": chapters.get(n, {}).get("title", f"第{n}章"),
            "filename": f.name,
            "size": st.st_size,
            "size_formatted": f"{st.st_size / 1024:.1f} KB" if st.st_size < 1024 * 1024 else f"{st.st_size / 1024 / 1024:.1f} MB",
            "updated_at": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return {"documents": docs}
