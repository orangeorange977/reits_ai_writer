"""
对比评测路由：标准答案上传 / 逐节对比 / AI 打分。
对应前端"对比评测"页，服务对象为 workspace/projects/<pid>/ 下的章节数据。
"""
import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from backend.services import eval_service
from backend.routers.skills import _assert_project_access, _current_user_id

router = APIRouter(tags=["对比评测"], prefix="/eval")
logger = logging.getLogger(__name__)

_MAX_UPLOAD = 20 * 1024 * 1024   # 标准答案 docx 上限 20MB


@router.get("/standards")
async def list_standards(http_req: Request, project_id: str = ""):
    """哪些章节已上传标准答案、哪些章节已生成内容。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    return {"chapters": eval_service.list_standards(project_id),
            "generated": eval_service.list_generated(project_id)}


@router.post("/standard/{n}/upload")
async def upload_standard(n: int, http_req: Request,
                          project_id: str = "", file: UploadFile = File(...)):
    """上传第 n 章标准答案 docx（可以是整本申报材料，自动定位到第 n 章）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    if not 1 <= n <= 99:
        raise HTTPException(status_code=400, detail="章节号不合法")
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式的标准答案")
    data = await file.read()
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(status_code=400, detail="文件过大（上限20MB）")
    try:
        info = await asyncio.to_thread(
            eval_service.save_standard, project_id, n, data, file.filename or "")
    except Exception as e:
        logger.warning(f"标准答案解析失败 ch{n}: {e}")
        raise HTTPException(status_code=400, detail=f"文档解析失败：{e}")
    return info


@router.delete("/standard/{n}")
async def delete_standard(n: int, http_req: Request, project_id: str = ""):
    await _assert_project_access(project_id, _current_user_id(http_req))

    def _do():
        eval_service.delete_standard(project_id, n)
    await asyncio.to_thread(_do)
    return {"ok": True}


@router.get("/compare/{n}")
async def compare(n: int, http_req: Request, project_id: str = "", force: int = 0):
    """逐节对齐 + 相似度 + 双栏文本（有缓存；force=1 强制重算）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    try:
        return await asyncio.to_thread(
            eval_service.compare_chapter, project_id, n, bool(force))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/score/{n}")
async def score(n: int, http_req: Request, project_id: str = ""):
    """AI 打分：后台任务启动即返回，前端轮询 /score_task 拿状态；切换页面不影响评分。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    if n not in eval_service.list_standards(project_id):
        raise HTTPException(status_code=404, detail=f"第{n}章尚未上传标准答案")
    if n not in eval_service.list_generated(project_id):
        raise HTTPException(status_code=400, detail=f"第{n}章内容尚未生成，无法打分")
    return {"task": eval_service.start_score_task(project_id, n)}


@router.get("/score_task/{n}")
async def score_task(n: int, http_req: Request, project_id: str = ""):
    """打分后台任务状态：running/done/failed；未启动为 null。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    return {"task": eval_service.get_score_task(project_id, n)}


@router.get("/scores/{n}")
async def scores(n: int, http_req: Request, project_id: str = ""):
    """打分历史（最新在最后）。"""
    await _assert_project_access(project_id, _current_user_id(http_req))
    return {"scores": eval_service.get_scores(project_id, n)}
