"""Skill 执行路由 - 让 Kimi 按各章 SKILL.md 生成章节内容

因为 Kimi 生成一章可能要几分钟，这里做成异步任务：
POST /skills/ch1/run     立即返回，后台开始跑
GET  /skills/ch1/status  前端轮询，拿到 running / done(+data) / error
"""
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import SKILLS_DIR, NDRC_OFFICIAL_TEMPLATE
from backend.services import skill_runner, summary_service, materials_client
from backend.services.kimi_client import chat

router = APIRouter(tags=["Skill执行"], prefix="/skills")
logger = logging.getLogger(__name__)


def _resolve_template_path(user_path: str = "") -> str:
    """解析模板路径：用户提供的路径无效时，自动回退到内置官方模板。

    回退顺序：
    1. config 中的 NDRC_OFFICIAL_TEMPLATE（workspace/app/... 路径，部署场景）
    2. 项目源码目录 backend/templates/official/ndrc_2024.docx（开发场景）
    """
    tpl = (user_path or "").strip()
    if tpl and Path(tpl).exists():
        return tpl
    # 回退 1：config 中配置的路径
    default = str(NDRC_OFFICIAL_TEMPLATE)
    if Path(default).exists():
        return default
    # 回退 2：项目源码目录下的模板（开发环境）
    project_template = Path(__file__).resolve().parents[1] / "templates" / "official" / "ndrc_2024.docx"
    if project_template.exists():
        return str(project_template)
    return ""


def _load_web_render():
    """加载 reits-writing skill 里的 web_render 脚本（写入逻辑归属该 skill）。

    每次都 reload：skill 脚本在 SKILLS_DIR 下，不在后端自动重载范围内，
    reload 后你改 skill 才能即时生效，无需重启服务。
    """
    import importlib
    scripts_dir = str(SKILLS_DIR / "reits-writing" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import web_render
    importlib.reload(web_render)
    return web_render


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
            "你是REITs发改委申报材料的中文写作助手。用户会给你一条【指令】和一段【原文】，"
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
            "你是REITs发改委申报材料的中文写作助手。用户会给你一条【指令】，可能还有一段【选中的原文】"
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


_DIAGRAM_TPL_DIR = SKILLS_DIR / "reits-diagrams" / "templates"


@router.get("/diagram-templates")
async def diagram_templates():
    """列出可选的画图模板（reits-diagrams/templates 下的 .drawio 文件）。"""
    def _list():
        if not _DIAGRAM_TPL_DIR.exists():
            return []
        items = []
        for p in sorted(_DIAGRAM_TPL_DIR.glob("*.drawio")):
            items.append({"name": p.stem, "label": p.stem})
        return items
    return {"templates": await asyncio.to_thread(_list)}


@router.get("/diagram-template")
async def diagram_template(name: str):
    """返回某个模板的 draw.io XML。"""
    # 防目录穿越：只用文件名
    safe = Path(name).name
    path = _DIAGRAM_TPL_DIR / f"{safe}.drawio"
    if not path.exists():
        raise HTTPException(status_code=404, detail="模板不存在")
    xml = await asyncio.to_thread(path.read_text, "utf-8")
    return {"name": safe, "xml": xml}


@router.get("/summary")
async def get_summary():
    """摘要表 / 释义（来自申报定稿 docx）+ 其他基本信息（来自 planning.md）。"""
    try:
        data = await asyncio.to_thread(summary_service.get_summary_data)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error(f"获取摘要表/释义失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取摘要表/释义失败：{e}")


class SummaryData(BaseModel):
    summary_table: list = []
    glossary: list = []
    other_info: list = []


@router.post("/summary/save")
async def save_summary(data: SummaryData):
    """保存网页上编辑好的摘要表/释义/其他基本信息（写入 JSON，之后各章生成都以此为准）。"""
    try:
        await asyncio.to_thread(summary_service.save_summary_data, data.model_dump())
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

# 各章生成任务状态（内存，本地单机单用户够用），按章节号存
_jobs = {}    # n -> {"status","data","error"}
# 后台 task 的强引用：asyncio 只对 task 持弱引用，不保存会被 GC 掉，导致状态卡在 running
_tasks = {}   # n -> asyncio.Task


def _job(n: int) -> dict:
    return _jobs.get(n, {"status": "idle", "data": None, "error": None})


def _valid_chapter(n: int):
    if n not in skill_runner.CHAPTERS:
        raise HTTPException(status_code=404, detail=f"章节 {n} 不存在")


async def _run_chapter_job(n: int):
    try:
        result = await asyncio.to_thread(skill_runner.run_chapter, n)
        _jobs[n] = {"status": "done", "data": result, "error": None}
        logger.info(f"第{n}章 skill 执行完成")
    except Exception as e:
        logger.error(f"第{n}章 skill 执行失败: {e}", exc_info=True)
        _jobs[n] = {"status": "error", "data": None, "error": str(e)}


async def _run_chapter_job_with_subs(n: int, subtitles: list, materials_path: str = ""):
    try:
        result = await asyncio.to_thread(skill_runner.run_chapter, n, subtitles, materials_path)
        _jobs[n] = {"status": "done", "data": result, "error": None}
        logger.info(f"第{n}章 skill 执行完成")
    except Exception as e:
        logger.error(f"第{n}章 skill 执行失败: {e}", exc_info=True)
        _jobs[n] = {"status": "error", "data": None, "error": str(e)}


@router.post("/chapter/{n}/run")
async def chapter_run(n: int, template_path: str = "", materials_path: str = ""):
    """启动第 n 章生成（后台异步），立即返回。template_path 有效时强制用模板小标题；
    materials_path 有效时把"读取申报材料"的工具挂给 Kimi。"""
    _valid_chapter(n)
    if _job(n)["status"] == "running":
        raise HTTPException(status_code=409, detail=f"第{n}章正在生成中，请稍候")
    subs = []
    tpl = _resolve_template_path(template_path)
    if tpl:
        subs = await asyncio.to_thread(skill_runner.chapter_subtitles, n, tpl)
    _jobs[n] = {"status": "running", "data": None, "error": None}
    _tasks[n] = asyncio.create_task(_run_chapter_job_with_subs(n, subs, materials_path.strip()))
    return {"status": "started", "message": f"第{n}章生成已启动，请稍候（Kimi 处理约需数分钟）"}


@router.get("/chapter/{n}/status")
async def chapter_status(n: int):
    """查询第 n 章生成进度/结果。"""
    _valid_chapter(n)
    return _job(n)


@router.get("/chapter/{n}/content")
async def chapter_content(n: int, template_path: str = ""):
    """第 n 章可编辑内容：以官方模板的本章小标题为骨架，合并已保存/已生成的内容。

    template_path（来自系统设置）有效时，即使还没生成，也能看到该章的小标题结构。
    若前端未传或路径无效，自动回退到内置官方模板。
    """
    _valid_chapter(n)

    def _do():
        subs = []
        tables = {}
        table_start = 1
        tpl = _resolve_template_path(template_path)
        if tpl:
            subs = skill_runner.chapter_subtitles(n, tpl)
            tables = skill_runner.chapter_tables(n, tpl)
            table_start = skill_runner.chapter_table_start(n, tpl)
        return skill_runner.get_chapter_content(n, subs, tables, table_start)

    return await asyncio.to_thread(_do)


class ChapterSaveBody(BaseModel):
    sections: list = []


@router.post("/chapter/{n}/save")
async def chapter_save(n: int, body: ChapterSaveBody):
    """保存用户编辑后的第 n 章内容（回传给 reading skill 的最终版）。"""
    _valid_chapter(n)
    try:
        await asyncio.to_thread(skill_runner.save_chapter_content, n, body.sections)
        return {"status": "ok", "message": "已保存"}
    except Exception as e:
        logger.error(f"保存第{n}章内容失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


# 预览渲染缓存：{n: (signature, result)}。签名只取“会影响成稿的东西”——本章已保存内容(JSON)、
# 模板文件、排版配置的修改时间。改 skill/planning 不会改变这三样，故不会触发重渲染；
# 只有在编辑区保存(内容变)或重新生成时 JSON 变了，签名才变、才重跑。进程内单例、够用。
_PREVIEW_CACHE: dict = {}


def _preview_signature(n: int, template_path: str) -> str:
    tpl = (template_path or "").strip()
    parts = [tpl]
    srcs = [skill_runner.chapter_json_path(n), skill_runner.WRITE_CONFIG_PATH]
    if tpl:
        srcs.append(Path(tpl))
    for p in srcs:
        try:
            parts.append(str(p.stat().st_mtime))
        except OSError:
            parts.append("0")
    return "|".join(parts)


@router.get("/chapter/{n}/preview")
async def chapter_preview(n: int, template_path: str = ""):
    """reits-writing skill 读第 n 章 JSON -> 写入官方模板对应章节 + 返回预览 HTML。

    template_path 由网页"系统设置"里的模板文件路径传入；有效则写入模板、预览取自填好的模板，
    无效/未提供则自动回退到内置官方模板；仍无效则回退到独立生成一份 Word。

    预览只对"编辑区内容"负责：不调用大模型、不因 skill/planning 改动而重跑；
    只有本章已保存内容(JSON)变化时才真正重新渲染，否则直接复用上次结果。
    """
    _valid_chapter(n)
    cfg = skill_runner.CHAPTERS[n]
    docx_path = str(skill_runner.chapter_docx_path(n))

    tpl_resolved = _resolve_template_path(template_path)
    sig = _preview_signature(n, tpl_resolved)
    cached = _PREVIEW_CACHE.get(n)
    if cached and cached[0] == sig:
        return {"status": "ok", "cached": True, **cached[1]}

    def _do():
        sections = skill_runner.get_chapter_structured(n)
        if not sections:
            return {"has_content": False, "html": "", "used_template": False}
        wr = _load_web_render()
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
            _PREVIEW_CACHE[n] = (sig, result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"生成第{n}章预览失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成预览失败：{e}")


@router.get("/chapter/{n}/download")
async def chapter_download(n: int):
    """下载 reits-writing skill 生成的第 n 章 Word 文件。"""
    _valid_chapter(n)
    path = skill_runner.chapter_docx_path(n)
    if not path.exists():
        raise HTTPException(status_code=404, detail="尚未生成 Word，请先在预览处生成")
    title = skill_runner.CHAPTERS[n]["title"]
    name_part = title.split("、", 1)[-1] if "、" in title else title
    filename = f"第{n}章_{name_part}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
