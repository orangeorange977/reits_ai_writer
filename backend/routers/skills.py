"""Skill 执行路由 - 让 Kimi 按各章 SKILL.md 生成章节内容

因为 Kimi 生成一章可能要几分钟，这里做成异步任务：
POST /skills/ch1/run     立即返回，后台开始跑
GET  /skills/ch1/status  前端轮询，拿到 running / done(+data) / error
"""
from __future__ import annotations

import asyncio
import json
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

from backend.config import NDRC_OFFICIAL_TEMPLATE, DEFAULT_PROJECT_ID, PROJECTS_DIR, safe_project_id
from backend.database.db import (get_project_pack_id, get_project_owner_id,
                                 upsert_generation_job, get_generation_job,
                                 touch_project_updated_at)
from backend.services import skill_runner, summary_service, materials_client, pack_service, json_gate, cover_service
from backend.services import data_foundation_service, manual_input_service
from backend.services import document_pipeline_service
from backend.services import report_audit_service
from backend.services import section_skill_service, section_recompile_service
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
    """列出可用的大模型（DeepSeek + Kimi 两厂商）+ 当前所选（供系统设置页下拉）。"""
    def _query():
        from backend.services.kimi_client import get_client
        from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        deepseek_models, kimi_models = [], []
        # DeepSeek：优先查接口，失败回退固定候选（deepseek-chat 为主力对话模型）
        if DEEPSEEK_API_KEY:
            try:
                client = get_client(DEEPSEEK_MODEL)
                deepseek_models = [m.id for m in client.models.list().data]
            except Exception as e:
                logger.warning(f"查询 DeepSeek 模型列表失败: {e}")
            # 确保默认模型（deepseek-chat 官方别名）在下拉里可选
            if DEEPSEEK_MODEL not in deepseek_models:
                deepseek_models.insert(0, DEEPSEEK_MODEL)
        # Kimi(Moonshot)
        try:
            client = get_client()
            kimi_models = [m.id for m in client.models.list().data]
        except Exception as e:
            logger.warning(f"查询 Kimi 模型列表失败: {e}")
        return deepseek_models + kimi_models
    models = await asyncio.to_thread(_query)
    return {"models": models, "current": skill_runner.get_selected_model()}


class ModelBody(BaseModel):
    model: str


@router.post("/model")
async def set_model(body: ModelBody):
    """保存所选模型（DeepSeek/Kimi，各章生成即时生效，无需重启）。"""
    if not body.model.strip():
        raise HTTPException(status_code=400, detail="模型名不能为空")
    await asyncio.to_thread(skill_runner.set_selected_model, body.model.strip())
    return {"status": "ok", "model": body.model.strip()}


# ===== Kimi 聊天入口（多轮对话，前端“Kimi 助手”抽屉）=====
_CHAT_SRC_CAP = 50000     # 每个素材最多取多少字（比一次性辅助宽松很多）
_CHAT_OCR_PAGES = 12      # 上传扫描件默认识别多少页（视觉侧已分批，安全放大）
_CHAT_CTX_CAP = 180000    # 历史+本轮合计字数上限，超了从最早的历史开始裁，防止超出模型窗口


def _chat_system_prompt() -> str:
    return (
        f"你是{pack_service.material_label()}的中文写作助手兼顾问。可以自由对话、回答问题、按用户要求撰写或修改"
        "申报材料相关内容。用户可能会附上【编辑区选中的文字】和【素材】（粘贴的文字/网页链接/上传文件的内容）作为参考，"
        "请结合它们作答。语言用中文、正式专业；当用户要“写一段/改一段”正文时，直接给出可用的正文本身，"
        "不要加“以下是”之类前后缀、不要用代码块包裹。素材仅作依据，甄别提炼、不要编造其中没有的关键数据。"
    )


def _trim_history(msgs: list, cap: int) -> list:
    """按字数上限裁剪：保留 system(第0条) 和最后一条用户消息，超了就从最早的历史往后丢。"""
    def size(ms):
        return sum(len(m.get("content", "")) for m in ms)
    if size(msgs) <= cap or len(msgs) <= 3:
        return msgs
    head, tail = msgs[:1], msgs[-1:]        # system + 本轮用户消息
    middle = msgs[1:-1]                      # 历史
    while middle and size(head + middle + tail) > cap:
        middle = middle[1:]                  # 丢最早的一条历史
    return head + middle + tail


@router.post("/ai-chat")
async def ai_chat(
    history: str = Form("[]"),          # 之前的对话 [{role, content}]（JSON 字符串）
    message: str = Form(""),            # 这轮用户输入
    selected_text: str = Form(""),      # 编辑区选中的文字（可空，作参考上下文）
    pasted_text: str = Form(""),
    urls: str = Form(""),
    files: list[UploadFile] = File(default=None),
):
    """Kimi 聊天入口：带着历史对话 + 本轮消息 + 本轮附件（粘贴/链接/上传文件）调 Kimi，返回回复。"""
    msg_text = (message or "").strip()
    has_attach = bool(pasted_text.strip() or urls.strip() or (files or []))
    if not msg_text and not has_attach:
        raise HTTPException(status_code=400, detail="请输入内容")
    try:
        hist = json.loads(history or "[]")
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
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
        mat = []
        if pasted_text.strip():
            mat.append("【素材：粘贴的文字】\n" + pasted_text.strip()[:_CHAT_SRC_CAP])
        for line in (urls or "").splitlines():
            u = line.strip()
            if u:
                mat.append(f"【素材：网页 {u}】\n" + materials_client.fetch_url_text(u, limit=_CHAT_SRC_CAP))
        for fname, data in uploaded:
            suffix = os.path.splitext(fname)[1] or ".bin"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            try:
                tmp.write(data)
                tmp.close()
                txt = materials_client.extract_file_text(
                    Path(tmp.name), query=msg_text,
                    max_chars=_CHAT_SRC_CAP, ocr_pages=_CHAT_OCR_PAGES)
                mat.append(f"【素材：文件 {fname}】\n{txt}")
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        user_content = msg_text or "（见下方素材，请按需处理）"
        if selected_text.strip():
            user_content += "\n\n【编辑区选中的文字】\n" + selected_text.strip()
        if mat:
            user_content += "\n\n" + "\n\n".join(mat)

        msgs = [{"role": "system", "content": _chat_system_prompt()}]
        for h in hist:
            role = h.get("role")
            if role in ("user", "assistant"):
                msgs.append({"role": role, "content": str(h.get("content", ""))})
        msgs.append({"role": "user", "content": user_content})
        msgs = _trim_history(msgs, _CHAT_CTX_CAP)
        return chat(msgs, model=skill_runner.get_selected_model(), temperature=1.0)

    try:
        reply = await asyncio.to_thread(_do)
        return {"status": "ok", "reply": (reply or "").strip()}
    except Exception as e:
        logger.error(f"Kimi 聊天失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Kimi 处理失败：{e}")


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
        await touch_project_updated_at(project_id)   # 刷新项目“更新时间”
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


# ===== 字段级数据底座 + 审核层（业务方法论 1.1 / 2.3） =====

def _foundation_required_paths(data: dict | None) -> list[str]:
    return [
        item.get("path", "")
        for item in (data or {}).get("sources", [])
        if item.get("required") and item.get("status") == "located" and item.get("path")
    ]


def _section_extraction_scope(data: dict | None, section_id: str) -> tuple[set[str], set[str]]:
    """field_ids + source paths a single small section actually reads, derived from
    each field's ``used_in_sections`` (see _fields_used_in_sections) so a Know-how edit
    can be re-extracted without re-running every other section's fields."""
    fields = (data or {}).get("fields", [])
    field_ids = {f.get("id") for f in fields if section_id in (f.get("used_in_sections") or [])}
    roles = {(f.get("rule") or {}).get("source_role") for f in fields if f.get("id") in field_ids}
    sources_by_role = {s.get("role"): s for s in (data or {}).get("sources", [])}
    target_paths = {
        sources_by_role[role]["path"] for role in roles
        if role in sources_by_role and sources_by_role[role].get("status") == "located"
        and sources_by_role[role].get("path")
    }
    return field_ids, target_paths


class DocumentBuildBody(BaseModel):
    paths: list[str] = []
    required_only: bool = False
    full_ocr: bool = False
    force: bool = False


class DocumentRefineBody(BaseModel):
    path: str
    pages: list[int]
    instruction: str = ""


@router.get("/manual-inputs")
async def get_manual_inputs(http_req: Request, project_id: str = ""):
    """读取两份业务手填 Word；该数据与 AI 抽取中间层分开保存。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        data = await asyncio.to_thread(
            manual_input_service.load_manual_inputs, project_id or None, True)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error("读取人工输入失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取人工输入失败：{e}")


@router.post("/manual-inputs/refresh")
async def refresh_manual_inputs(http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        data = await asyncio.to_thread(
            manual_input_service.build_manual_inputs, project_id or None)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error("刷新人工输入失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"刷新人工输入失败：{e}")


@router.get("/document-library")
async def list_document_library(http_req: Request, project_id: str = ""):
    """底稿知识库：一份源文件对应一个业务可读 Markdown。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    foundation = await asyncio.to_thread(
        data_foundation_service.load_foundation, project_id or None, pack_id)
    rows = await asyncio.to_thread(
        document_pipeline_service.list_documents,
        project_id or None,
        _foundation_required_paths(foundation),
    )
    return {"status": "ok", "documents": rows}


@router.post("/document-library/build")
async def build_document_library(body: DocumentBuildBody, http_req: Request,
                                 project_id: str = ""):
    """构建底稿 Markdown；默认覆盖全目录，可选仅处理 Know-how 命中材料。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    paths = [str(p).strip() for p in body.paths if str(p).strip()]
    if body.required_only and not paths:
        foundation = await asyncio.to_thread(
            data_foundation_service.load_foundation, project_id or None, pack_id)
        if not foundation:
            foundation = await asyncio.to_thread(
                data_foundation_service.build_foundation, project_id or None, pack_id)
        paths = _foundation_required_paths(foundation)
    if body.required_only and not paths:
        raise HTTPException(status_code=400, detail="尚未定位到 Know-how 要求的底稿，请先上传材料并刷新规则")
    try:
        result = await asyncio.to_thread(
            document_pipeline_service.build_project,
            project_id or None,
            paths or None,
            body.full_ocr,
            body.force,
        )
        await touch_project_updated_at(project_id)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error("构建底稿知识库失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"构建底稿知识库失败：{e}")


@router.get("/document-library/document")
async def get_document_markdown(http_req: Request, path: str, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        data = await asyncio.to_thread(
            document_pipeline_service.get_document, project_id or None, path)
        return {"status": "ok", **data}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取底稿 Markdown 失败：{e}")


@router.post("/document-library/refine")
async def refine_document_pages(body: DocumentRefineBody, http_req: Request,
                                project_id: str = ""):
    """Know-how 指定页视觉精读；无视觉模型配置时自动回退本地 OCR。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    if not body.pages:
        raise HTTPException(status_code=400, detail="请至少选择一页")
    try:
        data = await asyncio.to_thread(
            document_pipeline_service.refine_pdf_pages,
            project_id or None,
            body.path,
            body.pages,
            body.instruction,
        )
        await touch_project_updated_at(project_id)
        return {"status": "ok", **data}
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("底稿指定页精读失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"底稿指定页精读失败：{e}")


@router.get("/document-library/page-image")
async def get_document_page_image(http_req: Request, path: str, page: int,
                                  project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        image = await asyncio.to_thread(
            document_pipeline_service.ensure_page_image,
            project_id or None,
            path,
            page,
        )
        return FileResponse(str(image), media_type="image/png", filename=image.name)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


class FoundationUpdateBody(BaseModel):
    updates: list = []


class FoundationRuleUpdateBody(BaseModel):
    updates: list = []


class FoundationExtractBody(BaseModel):
    force: bool = False
    section_id: str = ""


class FoundationFileReextractBody(BaseModel):
    update: dict


class ReportAuditBody(BaseModel):
    scope: str = "report"
    chapter_n: int = 0
    section_title: str = ""
    use_ai: bool = True


@router.get("/report-audit")
async def get_report_audit(http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    return {"status": "ok", "data": await asyncio.to_thread(
        report_audit_service.load_audit, project_id or None)}


@router.post("/report-audit/run")
async def run_report_audit(body: ReportAuditBody, http_req: Request,
                           project_id: str = ""):
    """审核生成报告；发现问题只提示，不阻止保存或 Word 导出。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        if body.scope == "chapter" or body.section_title:
            if body.chapter_n < 1:
                raise HTTPException(status_code=400, detail="请选择要审核的章节")
            data = await asyncio.to_thread(
                report_audit_service.audit_chapter,
                project_id or None, body.chapter_n, body.use_ai, body.section_title, pack_id)
        else:
            data = await asyncio.to_thread(
                report_audit_service.audit_report, project_id or None, body.use_ai, pack_id)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data, "blocking": False}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("报告审核失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"报告审核失败：{e}")


@router.post("/report-audit/whole-report")
async def run_whole_report_audit(http_req: Request, project_id: str = ""):
    """跨小节一致性校验：全部小节写完后运行一次，只找跨节矛盾，由 report-audit/SKILL.md
    的 Know-how 驱动。与小节审核并列，同样只提示不阻断导出。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            report_audit_service.audit_whole_report, project_id or None, pack_id)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data, "blocking": False}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("全文一致性校验失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"全文一致性校验失败：{e}")


@router.get("/data-foundation")
async def get_data_foundation(http_req: Request, project_id: str = ""):
    """读取当前项目的数据底座。首次未构建时仅返回规则版本，不静默触发耗时抽取。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            data_foundation_service.load_foundation, project_id or None, pack_id)
        if data:
            return {"status": "ok", "exists": True, "data": data}
        rules = await asyncio.to_thread(data_foundation_service.load_rules, pack_id, project_id or None)
        return {
            "status": "ok",
            "exists": False,
            "data": None,
            "rule_version": rules.get("rule_version", ""),
            "methodology_sources": rules.get("methodology_sources", []),
        }
    except Exception as e:
        logger.error("读取数据底座失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"读取数据底座失败：{e}")


@router.get("/data-foundation/rules")
async def get_data_foundation_rules(http_req: Request, project_id: str = ""):
    """Return project-effective editable extraction rules."""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    rules = await asyncio.to_thread(
        data_foundation_service.load_rules, pack_id, project_id or None)
    return {"status": "ok", "data": rules}


@router.put("/data-foundation/rules")
async def update_data_foundation_rules(body: FoundationRuleUpdateBody, http_req: Request,
                                       project_id: str = ""):
    """Save reusable Know-how rules; project-only disabled flags remain project scoped."""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    project_updates = [item for item in body.updates
                       if isinstance(item, dict) and set(item).issubset({"id", "disabled"})]
    shared_updates = [item for item in body.updates if item not in project_updates]
    if project_updates:
        await asyncio.to_thread(
            data_foundation_service.save_rule_overrides,
            project_id or None, project_updates, pack_id)
    rules = await asyncio.to_thread(
        data_foundation_service.save_shared_rule_updates,
        shared_updates, pack_id, "business_rule_edit") if shared_updates else await asyncio.to_thread(
            data_foundation_service.load_rules, pack_id, project_id or None)
    await touch_project_updated_at(project_id)
    return {"status": "ok", "data": rules,
            "message": "通用 Know-how 抽取规则已保存；对同模板的其他项目同样生效"}


_foundation_file_reextracting: set[str] = set()


@router.post("/data-foundation/rules/reextract-file")
async def update_rule_and_reextract_file(body: FoundationFileReextractBody, http_req: Request,
                                         project_id: str = ""):
    """Persist one reusable rule revision, rediscover its runtime file, then re-extract its file scope."""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pid = _norm_pid(project_id)
    field_id = str((body.update or {}).get("id", "")).strip()
    if not field_id:
        raise HTTPException(status_code=400, detail="缺少要修改的字段 ID")
    if (_foundation_extract_jobs.get(pid, {}).get("status") == "running"
            or pid in _foundation_file_reextracting):
        raise HTTPException(status_code=409, detail="当前项目正在执行数据提取，请完成后再试")
    pack_id = await _project_pack_id(project_id)
    _foundation_file_reextracting.add(pid)
    try:
        await asyncio.to_thread(
            data_foundation_service.save_shared_rule_updates,
            [body.update], pack_id, "save_and_reextract_project_scope")
        result = await asyncio.to_thread(
            data_foundation_service.reextract_file_for_field,
            pid or None, field_id, pack_id)
        await touch_project_updated_at(project_id)
        run = result.get("run") or {}
        target = run.get("target_path") or run.get("target_role") or "当前来源"
        return {
            "status": "ok", "data": result.get("data"), "run": run,
            "message": f"规则修订已记录；已重新提取 {target} 关联的 {len(run.get('affected_field_ids') or [])} 个字段",
        }
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"规则已保存，但无法按文件重提取：{exc}")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"规则已保存，但按文件重提取失败：{exc}")
    finally:
        _foundation_file_reextracting.discard(pid)


@router.post("/data-foundation/build")
async def build_data_foundation(http_req: Request, project_id: str = ""):
    """按业务规则刷新字段快照；保留人工修订，来源值改变时自动把审核状态退回待审。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            data_foundation_service.build_foundation, project_id or None, pack_id)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("构建数据底座失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"构建数据底座失败：{e}")


@router.post("/data-foundation/deep-extract")
async def deep_extract_data_foundation(http_req: Request, project_id: str = ""):
    """专项读取营业执照、运营承诺函和信用报告；不对百页财报做整份 OCR。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            data_foundation_service.deep_extract_foundation, project_id or None, pack_id)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error("专项提取数据底座失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"专项提取失败：{e}")


_foundation_extract_jobs: dict[str, dict] = {}
_foundation_extract_tasks: dict[str, asyncio.Task] = {}


async def _run_foundation_extraction(project_id: str, pack_id: str | None, force: bool,
                                     section_id: str = ""):
    pid = _norm_pid(project_id)
    scoped = bool(section_id)

    def progress(stage: str, percent: int, message: str):
        _foundation_extract_jobs[pid] = {
            "status": "running", "stage": stage, "percent": percent,
            "message": message, "error": None,
        }

    try:
        progress("manual_inputs", 5, "读取两份业务人工输入")
        await asyncio.to_thread(manual_input_service.build_manual_inputs, pid or None)
        progress("rules", 12, "按当前可编辑规则定位底稿")
        foundation = await asyncio.to_thread(
            data_foundation_service.build_foundation, pid or None, pack_id)

        field_ids: set[str] | None = None
        target_paths: set[str] | None = None
        if scoped:
            field_ids, target_paths = _section_extraction_scope(foundation, section_id)
            if not field_ids:
                raise ValueError(f"小节 {section_id} 没有引用任何数据中间层字段，无需单独提取")
            required = sorted(target_paths)
        else:
            required = _foundation_required_paths(foundation)

        progress("documents", 20, f"构建 {len(required)} 份目标底稿 Markdown")
        if required:
            await asyncio.to_thread(
                document_pipeline_service.build_project,
                pid or None, required, False, force)
        progress("documents", 35, "从营业执照、承诺函、信用报告和财报关键页提取事实")
        await asyncio.to_thread(
            data_foundation_service.deep_extract_foundation, pid or None, pack_id, force,
            target_paths, field_ids)
        progress("rules", 76, "按可编辑规则合并执行其余底稿字段提取")
        await asyncio.to_thread(
            data_foundation_service.extract_rule_driven_fields, pid or None, pack_id, force,
            field_ids, target_paths)
        progress("external", 86, "调用天眼查并联网搜索公开信息")
        data = await asyncio.to_thread(
            data_foundation_service.extract_external_foundation, pid or None, pack_id, force,
            field_ids)
        if not scoped:
            progress("documents", 92, "为项目目录其余材料建立一文件一 Markdown 底稿")
            await asyncio.to_thread(
                document_pipeline_service.build_project,
                pid or None, None, False, force)
        _foundation_extract_jobs[pid] = {
            "status": "done", "stage": "completed", "percent": 100,
            "message": (f"小节 {section_id} 相关字段提取完成（共 {len(field_ids)} 个），未涉及其余小节"
                       if scoped else "数据提取完成，可检查规则、来源和字段后按章批量生成或按小节精修"),
            "error": None, "data": data,
        }
        await touch_project_updated_at(pid)
    except Exception as exc:
        logger.error("项目数据提取失败: %s", exc, exc_info=True)
        _foundation_extract_jobs[pid] = {
            "status": "error", "stage": "failed", "percent": 0,
            "message": "数据提取失败", "error": str(exc),
        }


@router.post("/data-foundation/extract")
async def start_data_foundation_extraction(body: FoundationExtractBody, http_req: Request,
                                           project_id: str = ""):
    """User-triggered extraction. Uploading files alone never starts AI/OCR/external calls.

    ``section_id`` scopes the run to just the fields one small section reads (see
    ``_section_extraction_scope``) so editing one Know-how does not force a full
    project re-extraction; omitted, it runs the full pipeline as before.
    """
    await _assert_project_access(project_id, _current_user_id(http_req))
    pid = _norm_pid(project_id)
    current = _foundation_extract_jobs.get(pid, {})
    if current.get("status") == "running":
        raise HTTPException(status_code=409, detail="当前项目正在提取数据")
    pack_id = await _project_pack_id(project_id)
    section_id = (body.section_id or "").strip()
    _foundation_extract_jobs[pid] = {
        "status": "running", "stage": "queued", "percent": 1,
        "message": f"已进入提取队列（{section_id}）" if section_id else "已进入提取队列", "error": None,
    }
    _foundation_extract_tasks[pid] = asyncio.create_task(
        _run_foundation_extraction(pid, pack_id, body.force, section_id))
    return {"status": "started", "message": "数据提取已启动"}


@router.get("/data-foundation/extract-status")
async def data_foundation_extraction_status(http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    return _foundation_extract_jobs.get(
        _norm_pid(project_id),
        {"status": "idle", "stage": "idle", "percent": 0,
         "message": "上传文件后点击“提取数据”开始", "error": None},
    )


@router.put("/data-foundation")
async def update_data_foundation(body: FoundationUpdateBody, http_req: Request,
                                 project_id: str = ""):
    """批量保存字段修订和逐字段审核结论。值、来源快照、审核记录分开保存。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            data_foundation_service.update_foundation,
            project_id or None, body.updates, pack_id)
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data}
    except Exception as e:
        logger.error("保存数据底座审核结果失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


@router.post("/data-foundation/apply-drafts")
async def apply_data_foundation_drafts(http_req: Request, project_id: str = ""):
    """把 1.1 和 2.3 的确定性草稿写入对应章节，其余二级小节不受影响。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            data_foundation_service.load_foundation, project_id or None, pack_id)
        if not data:
            data = await asyncio.to_thread(
                data_foundation_service.build_foundation, project_id or None, pack_id)
        drafts = data.get("drafts") or {}
        mapping = {"1.1": 1, "2.3": 2}
        applied = []
        for section_id, chapter_n in mapping.items():
            section = drafts.get(section_id)
            if not section:
                continue
            await asyncio.to_thread(
                skill_runner.upsert_structured_section,
                chapter_n, section, project_id or None,
                ["字段级数据底座", f"业务方法论规则 {section_id}"], pack_id)
            applied.append(section_id)
        if applied:
            audit_key = (str(project_id or DEFAULT_PROJECT_ID), "drafts")
            _audit_tasks[audit_key] = asyncio.create_task(
                _auto_audit_drafts(project_id or None, drafts, applied, pack_id))
        await touch_project_updated_at(project_id)
        return {"status": "ok", "applied": applied, "audit_status": "running" if applied else "idle",
                "message": "两节草稿已写入第一章和第二章，报告审核已在后台启动"}
    except Exception as e:
        logger.error("写入数据底座草稿失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"写入草稿失败：{e}")


# ===== 小节级 Skill 生产线（新前端唯一生成入口） =====

@router.get("/sections")
async def list_skill_sections(http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    rows = await asyncio.to_thread(
        section_skill_service.list_sections, project_id or None, pack_id)
    return {"status": "ok", "sections": rows}


@router.get("/sections/all")
async def list_all_skill_sections(http_req: Request, project_id: str = ""):
    """全部官方二级小节（含尚无 Know-how 的），供申报材料页/Know-how 页展示项目全貌。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    rows = await asyncio.to_thread(
        section_skill_service.list_all_official_sections, project_id or None, pack_id)
    return {"status": "ok", "sections": rows}


@router.post("/sections/chapter/{chapter_n}/generate")
async def generate_skill_chapter(chapter_n: int, http_req: Request,
                                 project_id: str = ""):
    """一次生成本章所有已配置的小节 Skill；未配置小节只报告跳过。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            section_skill_service.generate_chapter_sections,
            project_id or None, chapter_n, pack_id)
        if data["generated_total"]:
            audit_key = (_norm_pid(project_id), f"section-chapter-{chapter_n}")
            _audit_tasks[audit_key] = asyncio.create_task(
                _auto_audit_chapter(project_id or None, chapter_n, pack_id))
            await touch_project_updated_at(project_id)
        message = (
            f"第{chapter_n}章已生成 {data['generated_total']} 个小节"
            f"，失败 {data['failed_total']} 个"
            f"，跳过 {data['skipped_total']} 个未配置小节"
        )
        return {"status": "ok" if not data["failed_total"] else "partial",
                "data": data, "audit_status": "running" if data["generated_total"] else "idle",
                "message": message}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/sections/{section_id}/content")
async def get_skill_section_content(section_id: str, http_req: Request,
                                    project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            section_skill_service.get_section_content,
            project_id or None, section_id, pack_id)
        return {"status": "ok", "data": data}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _require_admin_role(http_req: Request):
    user = getattr(http_req.state, "user", None) or {}
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可编译 Know-how 规则")


class RecompileBody(BaseModel):
    know_how_text: str = ""


class RecompileApplyBody(BaseModel):
    payload: dict


@router.post("/sections/{section_id}/recompile")
async def recompile_section(section_id: str, body: RecompileBody, http_req: Request,
                            project_id: str = ""):
    """AI 把该小节的 Know-how 原文编译为抽取规则/生成模板/审核清单预览；只预览，不写入任何文件。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    _require_admin_role(http_req)
    pack_id = await _project_pack_id(project_id)
    know_how_text = body.know_how_text
    if not know_how_text.strip():
        skill_dir = f"section-skills/reits-section-{section_id.replace('.', '-')}/SKILL.md"
        try:
            know_how_text = await asyncio.to_thread(
                lambda: pack_service.skill_text_path(skill_dir, pack_id).read_text(encoding="utf-8"))
        except Exception:
            know_how_text = ""
    try:
        result = await asyncio.to_thread(
            section_recompile_service.recompile, section_id, know_how_text, pack_id)
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        selected_model = skill_runner.get_selected_model() or "当前模型"
        if status_code == 402 or "Insufficient Balance" in str(exc):
            provider = "DeepSeek" if str(selected_model).lower().startswith("deepseek") else selected_model
            raise HTTPException(
                status_code=402,
                detail=f"{provider} API 余额不足，无法编译 Know-how。请充值或在系统设置中切换到可用模型后重试。",
            ) from exc
        if status_code in (401, 403):
            raise HTTPException(
                status_code=502,
                detail=f"{selected_model} 的 API 凭证无效或无权访问，请检查系统配置。",
            ) from exc
        if isinstance(exc, RuntimeError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(
            status_code=502,
            detail=f"AI 编译服务调用失败（{selected_model}）：{exc}",
        ) from exc
    return {"status": "ok", **result}


@router.post("/sections/{section_id}/recompile/apply")
async def apply_recompiled_section(section_id: str, body: RecompileApplyBody, http_req: Request,
                                   project_id: str = ""):
    """业务确认预览无误后应用：只替换该小节自身的规则，其余小节不受影响。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    _require_admin_role(http_req)
    pack_id = await _project_pack_id(project_id)
    try:
        rules = await asyncio.to_thread(
            section_recompile_service.apply_compiled, section_id, body.payload, pack_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "rule_version": rules.get("rule_version", "")}


@router.post("/sections/{section_id}/generate")
async def generate_skill_section(section_id: str, http_req: Request,
                                 project_id: str = ""):
    """Generate exactly one business section from its Skill and current foundation."""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    try:
        data = await asyncio.to_thread(
            section_skill_service.generate_section,
            project_id or None, section_id, pack_id)
        config = data["config"]
        audit_key = (_norm_pid(project_id), f"section-{section_id}")
        _audit_tasks[audit_key] = asyncio.create_task(asyncio.to_thread(
            report_audit_service.audit_chapter,
            project_id or None, config["chapter_n"], True, config["title"], pack_id))
        await touch_project_updated_at(project_id)
        return {"status": "ok", "data": data,
                "message": f"{config['title']}已生成，其他小节未改动"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

# 各章生成任务状态：内存字典供本进程实时轮询，同时落 DB generation_jobs 表
# （步骤 3.5：重启后已完成任务仍可见，多 worker 部署的状态共享基础），按 (project_id, 章节号) 存
_jobs = {}    # (pid, n) -> {"status","data","error"}
# 后台 task 的强引用：asyncio 只对 task 持弱引用，不保存会被 GC 掉，导致状态卡在 running
_tasks = {}   # (pid, n) -> asyncio.Task
_audit_tasks = {}  # (pid, scope) -> asyncio.Task；审核不阻塞生成/导出


async def _auto_audit_drafts(project_id: str | None, drafts: dict, section_ids: list[str],
                             pack_id: str | None = None):
    mapping = {"1.1": 1, "2.3": 2}
    try:
        for section_id in section_ids:
            section = drafts.get(section_id) or {}
            await asyncio.to_thread(
                report_audit_service.audit_chapter,
                project_id, mapping[section_id], True, section.get("title", ""), pack_id)
    except Exception as exc:
        logger.warning("自动小节审核失败（不影响生成/导出）：%s", exc)


async def _auto_audit_chapter(project_id: str | None, chapter_n: int, pack_id: str | None = None):
    try:
        await asyncio.to_thread(
            report_audit_service.audit_chapter, project_id, chapter_n, True, "", pack_id)
    except Exception as exc:
        logger.warning("第%s章自动审核失败（不影响生成/导出）：%s", chapter_n, exc)


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
        if state.get("status") == "done":
            await touch_project_updated_at(pid)   # 生成完成也算项目内容更新
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
        audit_key = (key[0], f"chapter-{n}")
        _audit_tasks[audit_key] = asyncio.create_task(
            _auto_audit_chapter(project_id or None, n, pack_id))
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
    return {"status": "started", "message": f"第{n}章生成已启动，请稍候（AI 处理约需数分钟）"}


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
    if stored and stored.get("status") == "running":
        # 服务重启后原任务已不存在：DB 里遗留的 running 是僵尸状态，
        # 标记为中断并落库，让用户可以重新生成（否则永远卡在“生成中”）
        stored = {"status": "error", "data": None,
                  "error": "生成因系统重启被中断，请点“重新生成”继续"}
        _jobs[key] = stored
        await _save_job_to_db(key, stored)
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
        foreign = []
        tpl = _resolve_template_path(template_path, pack_id)
        if tpl:
            subs = skill_runner.chapter_subtitles(n, tpl, pack_id)
            tables = skill_runner.chapter_tables(n, tpl, pack_id)
            table_start = skill_runner.chapter_table_start(n, tpl, pack_id)
            # 他章模板小标题集合：已存数据里命中它的 section 是串章误存，剔除修复目录
            all_subs = skill_runner.all_chapters_subtitles(tpl, pack_id)
            foreign = [t for m, ss in all_subs.items() if m != n for t in (ss or [])]
        return skill_runner.get_chapter_content(n, subs, tables, table_start, pid, foreign)

    return await asyncio.to_thread(_do)


class ChapterSaveBody(BaseModel):
    sections: list = []


@router.post("/chapter/{n}/save")
async def chapter_save(n: int, body: ChapterSaveBody, http_req: Request, project_id: str = ""):
    """保存用户编辑后的第 n 章内容到该项目（回传给最终版 JSON）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    # 串章护栏：本章模板小标题——提交的小标题与之毫无交集时视为跨章误存、拒写
    tpl = _resolve_template_path("", pack_id)
    subs = await asyncio.to_thread(skill_runner.chapter_subtitles, n, tpl, pack_id) if tpl else []
    try:
        await asyncio.to_thread(
            skill_runner.save_chapter_content, n, body.sections, project_id or None, pack_id, subs)
        await touch_project_updated_at(project_id)   # 刷新项目“更新时间”
        return {"status": "ok", "message": "已保存"}
    except Exception as e:
        logger.error(f"保存第{n}章内容失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


# 预览渲染缓存：{(pid, n): (signature, result)}。签名只取“会影响成稿的东西”——本章已保存内容(JSON)、
# 模板文件、排版配置的修改时间。改 skill/planning 不会改变这三样，故不会触发重渲染；
# 只有在编辑区保存(内容变)或重新生成时 JSON 变了，签名才变、才重跑。进程内单例、够用。
_PREVIEW_CACHE: dict = {}


def _preview_signature(n: int, template_path: str, project_id: str = "", pack_id=None) -> str:
    tpl = (template_path or "").strip()
    parts = [tpl]
    srcs = [skill_runner.chapter_json_path(n, project_id or None), skill_runner.WRITE_CONFIG_PATH]
    if tpl:
        srcs.append(Path(tpl))
    try:
        srcs.append(Path(str(pack_service.writing_script_dir(pack_id))) / "web_render.py")
    except Exception:
        pass
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
    sig = (pack_id or "") + "|" + _preview_signature(n, tpl_resolved, project_id, pack_id)
    cached = _PREVIEW_CACHE.get(key)
    if cached and cached[0] == sig:
        return {"status": "ok", "cached": True, **cached[1]}

    def _do():
        sections = skill_runner.get_chapter_structured(n, pid)
        if not sections:
            return {"has_content": False, "html": "", "used_template": False}
        # 串章修复：与编辑区同一剔除逻辑，他章小标题不进 Word
        sections = _strip_foreign_sections(n, sections, pack_id)
        # JSON 健康门禁：写 Word 前清理畸形块，坏数据不再把整章预览打崩；
        # 门禁提示不缓存（内容修复后要立即消失）
        sections, gate_warnings = json_gate.check_and_clean(sections)
        if not sections:
            return {"has_content": False, "html": "", "used_template": False,
                    "gate_warnings": gate_warnings}
        wr = _load_web_render(pack_id)
        if tpl_resolved:
            wr.render_into_template(sections, tpl_resolved, docx_path, cfg["title"], cfg["next"],
                                    chapter_n=n)
            _install_cover_front(docx_path, pid)   # 规则：导出 Word 第一页=编辑好的封面（预览也同步，固化版本一致）
            html = wr.docx_to_preview_html(docx_path, cfg["title"], cfg["next"])
            # 内容变化重新渲染后固化为新的正式文档版本（项目名_日期_第n章_vN，历史保留；失败不阻断）
            skill_runner.snapshot_docx(n, pid)
            return {"has_content": True, "html": html, "used_template": True,
                    "gate_warnings": gate_warnings}
        # 回退：没有有效模板路径时，独立生成一份
        wr.render_docx(sections, docx_path)
        html = wr.render_preview_html(sections)
        skill_runner.snapshot_docx(n, pid)
        return {"has_content": True, "html": html, "used_template": False,
                "gate_warnings": gate_warnings}

    try:
        result = await asyncio.to_thread(_do)
        # 缓存含 gate_warnings：签名含本章 JSON 的 mtime，内容修复后签名变、缓存失效，
        # 提醒不会过时
        if result.get("has_content"):
            _PREVIEW_CACHE[key] = (sig, result)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"生成第{n}章预览失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成预览失败：{e}")


_DOC_H1_STYLE = 'font-family:"方正黑体_GBK", SimHei, 黑体, sans-serif;font-size:15pt'
_DOC_H2_STYLE = 'font-family:"方正楷体_GBK", KaiTi, 楷体, STKaiti, serif;font-size:15pt'
_DOC_P_STYLE = 'text-align:justify;font-family:"方正仿宋_GBK", FangSong, 仿宋, STFangsong, serif;font-size:15pt'


def _render_section_standalone_html(chapter_title: str, section: dict) -> str:
    """只这一节的 Word 排版预览，不经过官方整章模板——那条流水线是"往一份含全部小标题
    的模板里填内容"，喂给它一个小节时，同章其它还没写的小标题会带着官方模板本身的填写
    说明原样留在输出里，看起来像"缺了很多"，其实是模板机制决定的，不是这个小节的问题。
    这里直接从小节自己的 blocks 拼 HTML，样式对齐模板预览用的字体/字号，但只有这一节。"""
    from html import escape as _e

    parts = [
        '<div class="doc-page">',
        f'<h2 class="doc-h1" style="{_DOC_H1_STYLE}">{_e(chapter_title)}</h2>',
        f'<h3 class="doc-h2" style="{_DOC_H2_STYLE}">{_e(section.get("title", ""))}</h3>',
    ]
    for block in section.get("blocks", []) or []:
        kind = block.get("type")
        if kind == "p":
            text = _e(str(block.get("text", ""))).replace("\n", "<br>")
            parts.append(f'<p class="doc-prev-p" style="{_DOC_P_STYLE}">{text}</p>')
        elif kind == "kv":
            if block.get("caption"):
                parts.append(f'<p class="doc-prev-p" style="{_DOC_P_STYLE}"><b>{_e(block["caption"])}</b></p>')
            rows_html = "".join(
                f'<tr><td>{_e(str(r.get("label", "")))}</td><td>{_e(str(r.get("value", "")))}</td></tr>'
                for r in block.get("rows", []) or []
            )
            parts.append(f'<table class="doc-prev-table"><tbody>{rows_html}</tbody></table>')
        elif kind == "grid":
            if block.get("caption"):
                parts.append(f'<p class="doc-prev-p" style="{_DOC_P_STYLE}"><b>{_e(block["caption"])}</b></p>')
            headers = "".join(f'<th>{_e(str(h))}</th>' for h in block.get("headers", []) or [])
            rows_html = "".join(
                "<tr>" + "".join(f'<td>{_e(str(c))}</td>' for c in row) + "</tr>"
                for row in block.get("rows", []) or []
            )
            parts.append(f'<table class="doc-prev-table"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table>')
    parts.append("</div>")
    return "".join(parts)


@router.get("/sections/{section_id}/preview")
async def section_word_preview(section_id: str, http_req: Request, project_id: str = ""):
    """小节自己的 Word 排版预览——只这一节，不混进同章其它（可能还没配置/没生成的）小节，
    也不经过整章官方模板填写流水线（原因见 _render_section_standalone_html）。整章合并下载
    是另一件事，见"下载本章 Word" / /chapter/{n}/download。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    pid = project_id or None
    try:
        config = section_skill_service.get_section(section_id, pack_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    n = config["chapter_n"]
    cfg = skill_runner.chapters_for(pack_id)[n]

    def _do():
        sections = skill_runner.get_chapter_structured(n, pid)
        matched = [s for s in sections if str(s.get("title", "")).strip() == config["title"].strip()]
        if not matched:
            return {"has_content": False, "html": ""}
        cleaned, gate_warnings = json_gate.check_and_clean(matched)
        if not cleaned:
            return {"has_content": False, "html": "", "gate_warnings": gate_warnings}
        html = _render_section_standalone_html(cfg["title"], cleaned[0])
        return {"has_content": True, "html": html, "gate_warnings": gate_warnings}

    try:
        result = await asyncio.to_thread(_do)
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"生成小节 {section_id} 预览失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成预览失败：{e}")


def _strip_foreign_sections(n: int, sections: list, pack_id) -> list:
    """剔除已存数据里属于其他章节模板小标题的 section（串章误存修复）；
    编辑区/预览/导出共用，保证各视图目录一致。"""
    if not sections:
        return sections
    tpl = _resolve_template_path("", pack_id)
    if not tpl:
        return sections
    all_subs = skill_runner.all_chapters_subtitles(tpl, pack_id)
    ft = {(t or "").strip() for m, ss in all_subs.items() if m != n for t in (ss or [])}
    if not ft:
        return sections
    return [s for s in sections if (s.get("title") or "").strip() not in ft]


def _install_cover_front(docx_path, pid) -> None:
    """封面置顶规则：把官方模板首页（格式文本页）替换为编辑好的封面。
    失败不阻断导出（降级为原首页），仅记录日志。"""
    try:
        cover_service.install_cover_front_page(docx_path, pid)
    except Exception as e:
        logger.warning(f"封面置顶失败（保留官方首页）: {e}")


def _render_chapter_docx(n: int, pid, pack_id) -> bool:
    """把本章当前保存内容渲染进 Word 工作文件（与预览同一管线，只写文件不返回 HTML）；
    无有效内容返回 False。失败抛异常由调用方处理。"""
    sections = skill_runner.get_chapter_structured(n, pid)
    if not sections:
        return False
    sections = _strip_foreign_sections(n, sections, pack_id)
    sections, _warns = json_gate.check_and_clean(sections)
    if not sections:
        return False
    cfg = skill_runner.chapters_for(pack_id)[n]
    docx_path = str(skill_runner.chapter_docx_path(n, pid))
    wr = _load_web_render(pack_id)
    tpl_resolved = _resolve_template_path("", pack_id)
    if tpl_resolved:
        wr.render_into_template(sections, tpl_resolved, docx_path, cfg["title"], cfg["next"],
                                chapter_n=n)
        _install_cover_front(docx_path, pid)   # 规则：导出 Word 第一页=编辑好的封面
    else:
        wr.render_docx(sections, docx_path)
    return True


@router.get("/chapter/{n}/download")
async def chapter_download(n: int, http_req: Request, project_id: str = "", version: int = 0):
    """下载该项目第 n 章的 Word 文件：缺省=一次导出事件——先按最新保存内容渲染、
    强制固化一个新版本（v+1，历史保留）再下载；传 version 则下载指定历史版本（不产生新版本）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    pid = project_id or None
    path = None
    if version > 0:
        for f in skill_runner.versioned_docx_files(n, pid):
            m = re.search(r"_v(\d+)\.docx$", f.name)
            if m and int(m.group(1)) == version:
                path = f
                break
        if not path or not path.exists():
            raise HTTPException(status_code=404, detail=f"未找到第{n}章 v{version} 版本")
    else:
        # 主动下载=导出：先渲染当前保存内容，再强制出新版本（内容没变也出，代表一次导出）
        if _render_chapter_docx(n, pid, pack_id):
            skill_runner.snapshot_docx(n, pid, force=True)
        path = skill_runner.ensure_versioned(n, pid) or skill_runner.chapter_docx_path(n, pid)
        if not path.exists():
            raise HTTPException(status_code=404, detail="尚未生成 Word，请先在预览处生成")
    filename = path.name
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/chapter/{n}/document")
async def generate_document(n: int, http_req: Request, project_id: str = ""):
    """把第 n 章当前保存内容渲染成 Word 并固化一个新版本（不触发下载）；
    与“下载Word”同一套渲染管线，只是不返回文件流。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    pid = project_id or None
    if not _render_chapter_docx(n, pid, pack_id):
        raise HTTPException(status_code=400, detail="本章还没有已保存的内容，请先生成或编辑内容")
    path = skill_runner.snapshot_docx(n, pid, force=True)
    if not path:
        raise HTTPException(status_code=500, detail="生成版本失败")
    return {"status": "ok", "filename": path.name}


@router.delete("/chapter/{n}/document")
async def delete_document(n: int, http_req: Request, project_id: str = "", version: int = 0):
    """删除第 n 章指定版本的正式文档（必须指明版本号；只删版本文件，
    不碰渲染工作文件，删除后其余版本不受影响）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    _valid_chapter(n, pack_id)
    if version <= 0:
        raise HTTPException(status_code=400, detail="请指定要删除的版本号")
    target = None
    for f in skill_runner.versioned_docx_files(n, project_id or None):
        m = re.search(r"_v(\d+)\.docx$", f.name)
        if m and int(m.group(1)) == version:
            target = f
            break
    if not target or not target.exists():
        raise HTTPException(status_code=404, detail=f"未找到第{n}章 v{version} 版本")
    try:
        target.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败：{e}")
    # 版本删光了→落墓碑，防止列表接口的老数据迁移把工作文件复活成 v1
    if not skill_runner.versioned_docx_files(n, project_id or None):
        skill_runner.mark_doc_deleted(n, project_id or None)
    return {"status": "ok", "deleted": target.name}


@router.get("/documents")
async def list_documents(http_req: Request, project_id: str = ""):
    """列出该项目已生成的各章 Word 文档（文档管理页数据源）：
    正式文档统一为 项目名_日期_第n章_vN 命名，历史版本全部列出。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    pack_id = await _project_pack_id(project_id)
    chapters = skill_runner.chapters_for(pack_id)
    pid = project_id or None
    docs = []
    for n in sorted(chapters.keys()):
        # 老数据迁移：只有工作文件时自动固化为 v1，历史文档不会丢
        if not skill_runner.ensure_versioned(n, pid):
            continue
        for f in skill_runner.versioned_docx_files(n, pid):
            m = re.match(r".+_(\d{8})_第\d+章_v(\d+)\.docx$", f.name)
            if not m:
                continue
            st = f.stat()
            docs.append({
                "chapter": n,
                "title": chapters.get(n, {}).get("title", f"第{n}章"),
                "filename": f.name,
                "version": int(m.group(2)),
                "version_date": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}",
                "size": st.st_size,
                "size_formatted": f"{st.st_size / 1024:.1f} KB" if st.st_size < 1024 * 1024 else f"{st.st_size / 1024 / 1024:.1f} MB",
                # 统一约定：后端返回 UTC 字符串，前端 _fmtTime 换算北京时间（容器本地是 CST，不能用 fromtimestamp）
                "updated_at": datetime.utcfromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    # 章序在前、同章新版本在前
    docs.sort(key=lambda d: (d["chapter"], -d["version"]))
    return {"documents": docs}


# ===== 封面（封面编辑：日期 + 四角色 logo；标题/原始权益人自动取自摘要表） =====

class CoverDate(BaseModel):
    date_text: str = ""


@router.get("/cover")
async def cover_get(http_req: Request, project_id: str = ""):
    """封面编辑页所需状态：标题(自动)、原始权益人(自动)、日期(已存)、各 logo 是否已上传。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        return {"status": "ok", "data": await asyncio.to_thread(cover_service.get_state, project_id or None)}
    except Exception as e:
        logger.error(f"获取封面状态失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取封面状态失败：{e}")


@router.post("/cover/save")
async def cover_save(data: CoverDate, http_req: Request, project_id: str = ""):
    """保存用户填写的日期（标题/原始权益人分别来自摘要表与上传，不在此保存）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        await asyncio.to_thread(cover_service.save_date, data.date_text, project_id or None)
        return {"status": "ok", "message": "已保存"}
    except Exception as e:
        logger.error(f"保存封面日期失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"保存失败：{e}")


@router.post("/cover/logo/{role}")
async def cover_upload_logo(role: str, http_req: Request, file: UploadFile = File(...), project_id: str = ""):
    """上传某个角色的 logo（issuer/fund_manager/plan_manager/advisor）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    if role not in cover_service.LOGO_ROLES:
        raise HTTPException(status_code=400, detail=f"未知的 logo 角色：{role}")
    data = await file.read()
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    try:
        await asyncio.to_thread(cover_service.save_logo, role, file.filename or "logo.png", data, project_id or None)
        return {"status": "ok", "message": "已上传"}
    except Exception as e:
        logger.error(f"上传 logo 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上传失败：{e}")


@router.delete("/cover/logo/{role}")
async def cover_delete_logo(role: str, http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))
    if role not in cover_service.LOGO_ROLES:
        raise HTTPException(status_code=400, detail=f"未知的 logo 角色：{role}")
    await asyncio.to_thread(cover_service.delete_logo, role, project_id or None)
    return {"status": "ok", "message": "已删除"}


@router.get("/cover/logo/{role}")
async def cover_get_logo(role: str, http_req: Request, project_id: str = ""):
    """读取某角色 logo 图片（供编辑页预览）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    path = await asyncio.to_thread(cover_service.logo_path, role, project_id or None)
    if path is None:
        raise HTTPException(status_code=404, detail="尚未上传")
    ext = path.suffix.lower()
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
    return FileResponse(str(path), media_type=media)


@router.get("/cover/download")
async def cover_download(http_req: Request, project_id: str = ""):
    """下载“只有封面”的 Word。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    out = PROJECTS_DIR / safe_project_id(project_id) / "_cover_preview.docx"
    try:
        state = await asyncio.to_thread(cover_service.get_state, project_id or None)
        await asyncio.to_thread(cover_service.build_cover_docx, out, project_id or None)
    except Exception as e:
        logger.error(f"生成封面 Word 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成封面失败：{e}")
    fname = f"{(state.get('project_name') or '项目').strip()}_封面.docx"
    return FileResponse(
        str(out),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
