"""项目管理路由

提供项目的CRUD操作（新管线：项目↔模板包绑定，章节生成走 skills 路由）。
步骤 3.4：新增申报材料上传/列表/清空接口（上传模式替代本机路径）。
"""

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from backend.config import PROJECTS_DIR
from backend.database.db import get_db, get_project_owner_id, is_preset_project, get_project_pack_id
from backend.services import pack_service, materials_client, materials_catalog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["项目管理"])


def _current_user_id(http_req: Request) -> int:
    """从中间件解析的 token payload 里取当前用户 ID（步骤 3.5）。"""
    user = getattr(http_req.state, "user", None) or {}
    try:
        return int(user.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")


async def _assert_project_owned(project_id: int, user_id: int):
    """项目存在且归属当前用户，否则 404（不泄露他人项目的存在性，步骤 3.5）。"""
    owner = await get_project_owner_id(project_id)
    if owner is None or owner != user_id:
        raise HTTPException(status_code=404, detail=f"项目不存在: ID={project_id}")

# ===== 材料上传（步骤 3.4） =====

# 接受的材料文件类型（zip 会被解压，里面的文件不限后缀）
_MATERIAL_UPLOAD_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                        ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
                        ".tif", ".tiff", ".webp", ".msg", ".eml", ".wps", ".et", ".dps",
                        ".rtf", ".html", ".htm", ".zip"}
_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _is_junk_name(name: str) -> bool:
    """系统垃圾文件：永不保存（macOS ._*/.DS_Store、Windows Thumbs.db、Office 临时文件 ~$*）"""
    n = (name or "").rsplit("/", 1)[-1].lower()
    return n in _JUNK_NAMES or n.startswith("._") or n.startswith("~$")
_MAX_UNCOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024   # zip 解压后总大小上限 2GB（防 zip 炸弹）
_MAX_MATERIAL_FILES = 3000                          # 解压后文件数上限
_CHUNK = 1024 * 1024                                # 大文件分块读写 1MB


def _clip_name(name: str) -> str:
    """文件系统限制：单个路径段 UTF-8 不能超过 255 字节（中文目录名太长会 OSError 36）。
    超限按字节安全截断，保留扩展名并附 6 位哈希后缀防同名碰撞。"""
    if len(name.encode("utf-8")) <= 200:
        return name
    import hashlib
    suffix = "~" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext.encode("utf-8")) > 30:
        stem, ext = name, ""
    ext_part = ("." + ext) if ext else ""
    room = 200 - len(suffix.encode("utf-8")) - len(ext_part.encode("utf-8"))
    stem = stem.encode("utf-8")[:room].decode("utf-8", "ignore")
    return stem + suffix + ext_part


def _rel_parts(raw_name: str) -> list:
    """把上传文件名（可能带子目录，如浏览器文件夹上传的 webkitRelativePath）
    拆成安全的路径段：统一分隔符、丢弃空段与 '.'/'..'（防穿越，'..' 直接丢弃不回退），
    绝对路径剥掉盘符/前缀（如 'C:'）。"""
    parts = []
    for seg in str(raw_name or "").replace("\\", "/").split("/"):
        seg = seg.strip()
        if not seg or seg in (".", ".."):
            continue
        if ":" in seg:
            tail = seg.split(":", 1)[1].strip("/")
            if not tail:
                continue  # 纯盘符/前缀段（如 'C:'），丢弃
            seg = tail
        parts.append(_clip_name(seg))
    return parts


def _materials_dir(project_id: int) -> Path:
    """项目材料目录：workspace/projects/<id>/materials/（项目 ID 为 DB 自增整数，无穿越风险）。"""
    return PROJECTS_DIR / str(project_id) / "materials"


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """修正 zip 内中文文件名乱码：无 UTF-8 标志位时，zipfile 默认按 cp437 解码。
    Windows 打包的 zip 实际是 GBK；而 macOS zip 命令存 UTF-8 字节却不置标志位，
    因此先试 UTF-8（UTF-8 字节被 GBK 解会成乱码但“合法”，而 GBK 字节大多
    无法通过严格的 UTF-8 解码），失败再试 GBK，都失败保持原样。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def _zip_is_junk(name: str) -> bool:
    """zip 内垃圾成员：__MACOSX 目录、.DS_Store、._* 等系统附属文件"""
    segs = [s for s in name.replace("\\", "/").split("/") if s and s != "."]
    return bool(segs) and (segs[0] == "__MACOSX" or _is_junk_name(segs[-1]))


def _zip_clip_path(name: str) -> str:
    """逐段截断超长路径段（防 OSError 36）后重新拼回"""
    segs = [s for s in name.replace("\\", "/").split("/") if s and s != "."]
    return "/".join(_clip_name(s) for s in segs)


def _safe_extract_zip(file_obj, dest: Path) -> int:
    """安全解压 zip 到 dest：逐成员校验路径穿越/解压炸弹，分块写出。返回解压出的文件数。"""
    dest = dest.resolve()
    count = 0
    total_size = 0
    with zipfile.ZipFile(file_obj) as zf:
        # 先全量校验，全部通过再落盘，避免解到一半发现恶意成员
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _zip_member_name(info)
            if _zip_is_junk(name):
                continue
            name = _zip_clip_path(name)
            target = (dest / name).resolve()
            if not target.is_relative_to(dest):
                raise HTTPException(status_code=400, detail=f"zip 含非法路径（拒绝解压）：{name}")
            total_size += info.file_size
            count += 1
            if total_size > _MAX_UNCOMPRESSED_SIZE:
                raise HTTPException(status_code=400, detail="zip 解压后超过 2GB 上限，拒绝解压")
            if count > _MAX_MATERIAL_FILES:
                raise HTTPException(status_code=400, detail=f"zip 内文件数超过 {_MAX_MATERIAL_FILES} 上限")
        # 校验通过，逐个分块写出
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _zip_member_name(info)
            if _zip_is_junk(name):
                continue
            name = _zip_clip_path(name)
            target = (dest / name).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, _CHUNK)
    return count


# ===== 请求/响应模型 =====

class CreateProjectRequest(BaseModel):
    """创建项目请求"""
    name: str
    data_source_path: Optional[str] = ""  # 可选：网页版用户经上传接口传材料，无需指定服务器目录
    pack_id: Optional[str] = None  # 绑定的模板包；不传时绑默认包


class UpdateProjectRequest(BaseModel):
    """更新项目请求（当前支持改名）"""
    name: str


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
async def list_projects(http_req: Request):
    """获取项目列表（只返回当前用户自己的项目，步骤 3.5）"""
    user_id = _current_user_id(http_req)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status, created_at, updated_at, pack_id "
            "FROM projects WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        # 项目真实生成状态：从 generation_jobs 实时汇总（projects.status 字段创建后不再维护，
        # 概览页“生成中/已完成”统计必须看真实任务）。任一章节 running → generating；
        # 无 running 但有已完成章节 → generated。防僵尸：running 超 40 分钟未更新
        # 视为重启/崩溃遗留的死任务，不计入“生成中”（用户打开该章时会被既有逻辑自动修正为中断）。
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        jstat = {}  # pid(str) -> [有活跃running, 有已完成章节]
        try:
            cur2 = await db.execute("SELECT project_id, status, updated_at FROM generation_jobs")
            for pid, jst, upd in await cur2.fetchall():
                live_running = False
                if jst == "running":
                    try:
                        t = datetime.strptime(str(upd)[:19], "%Y-%m-%d %H:%M:%S")
                        live_running = (now - t) <= timedelta(minutes=40)
                    except Exception:
                        live_running = True
                prev = jstat.get(pid, [False, False])
                jstat[pid] = [prev[0] or live_running, prev[1] or (jst == "done")]
        except Exception:
            pass  # 任务表查不到不影响项目列表返回
        projects = []
        for row in rows:
            jr, jd = jstat.get(str(row[0]), [False, False])
            status = "generating" if jr else ("generated" if jd else row[3])
            projects.append(ProjectResponse(
                id=row[0],
                name=row[1],
                data_source_path=row[2],
                status=status,
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
async def create_project(request: CreateProjectRequest, http_req: Request):
    """创建新项目（归属当前登录用户，步骤 3.5）"""
    user_id = _current_user_id(http_req)
    # 数据源路径可选：填了才校验存在性（网页版用户通过上传接口传材料，无需指定）
    source_path_str = (request.data_source_path or "").strip()
    if source_path_str:
        source_path = Path(source_path_str)
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
            "INSERT INTO projects (name, data_source_path, status, pack_id, user_id) VALUES (?, ?, ?, ?, ?)",
            (request.name, source_path_str, "active", pack_id, user_id)
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
async def get_project(project_id: int, http_req: Request):
    """获取项目详情（仅归属当前用户的项目，步骤 3.5）"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
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


@router.put("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: int, request: UpdateProjectRequest, http_req: Request):
    """项目改名（仅归属当前用户的项目）"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="项目名称过长（最多 100 字）")
    db = await get_db()
    try:
        await db.execute(
            "UPDATE projects SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, project_id)
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id, name, data_source_path, status, created_at, updated_at, pack_id FROM projects WHERE id = ?",
            (project_id,)
        )
        row = await cursor.fetchone()
        logger.info(f"项目改名成功: ID={project_id} -> {name}")
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
        logger.error(f"项目改名失败: {e}")
        raise HTTPException(status_code=500, detail=f"项目改名失败: {e}")
    finally:
        await db.close()


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, http_req: Request):
    """删除项目（仅归属当前用户的项目，步骤 3.5）"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
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

        # 同步清理项目数据目录（章节 JSON、上传材料等，步骤 3.4）
        shutil.rmtree(PROJECTS_DIR / str(project_id), ignore_errors=True)

        logger.info(f"项目已删除: ID={project_id}")
        return {"message": f"项目已删除", "id": project_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除项目失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除项目失败: {e}")
    finally:
        await db.close()


# ===== 申报材料上传（步骤 3.4：上传模式替代本机路径） =====


@router.post("/projects/{project_id}/materials")
async def upload_materials(project_id: int, http_req: Request, files: List[UploadFile] = File(...)):
    """上传申报材料（多文件，支持 zip 自动解压）。

    落盘到 workspace/projects/<id>/materials/；zip 里的多级文件夹结构原样保留，
    生成时由 materials_client 递归扫描。同名文件直接覆盖（重传即替换）。
    """
    await _assert_project_owned(project_id, _current_user_id(http_req))
    if not files:
        raise HTTPException(status_code=400, detail="未选择任何文件")

    dest = _materials_dir(project_id)
    dest.mkdir(parents=True, exist_ok=True)

    added, extracted, skipped, existed = [], 0, [], []
    for f in files:
        if _is_junk_name(f.filename or ""):
            continue
        ext = Path(f.filename or "").suffix.lower()
        if ext not in _MATERIAL_UPLOAD_EXT:
            skipped.append(f.filename or "(无名文件)")
            continue
        if ext == ".zip":
            # UploadFile 的 spooled 对象不是完整文件对象（Python 3.9 缺 seekable），
            # 先落盘成临时文件再解压，也避免大 zip 占用内存
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            try:
                with os.fdopen(tmp_fd, "wb") as tmp_out:
                    shutil.copyfileobj(f.file, tmp_out, _CHUNK)
                with open(tmp_path, "rb") as zip_in:
                    extracted += await asyncio.to_thread(_safe_extract_zip, zip_in, dest)
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail=f"文件不是有效的 zip：{f.filename}")
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            # 文件夹上传时文件名携带相对路径（webkitRelativePath），按目录结构落盘
            parts = _rel_parts(f.filename) or [Path(f.filename or "").name or "未命名文件"]
            target = dest.joinpath(*parts)
            # 补传场景：同名同大小的文件已存在则跳过（不重传不覆盖），只补缺失的
            fsize = getattr(f, "size", None)
            if target.is_file() and fsize is not None and target.stat().st_size == fsize:
                existed.append(target.relative_to(dest).as_posix())
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(f.file, out, _CHUNK)
            added.append(target.relative_to(dest).as_posix())

    return {
        "uploaded": added,
        "extracted_from_zip": extracted,
        "skipped": skipped,
        "existed": existed,
        "materials_dir": str(dest),
    }


@router.get("/projects/{project_id}/materials")
async def list_materials(project_id: int, http_req: Request):
    """列出当前项目已上传的申报材料（递归，含多级子文件夹），附带 25 项清单缺件体检。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    root = _materials_dir(project_id)
    files, dirs = [], []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                dirs.append(p.relative_to(root).as_posix())
            elif p.is_file():
                files.append({
                    "path": p.relative_to(root).as_posix(),
                    "size": p.stat().st_size,
                })
    # 缺件体检：失败/无清单时 available=False，前端不展示，不影响列表
    try:
        pack_id = await get_project_pack_id(project_id)
        catalog_check = await asyncio.to_thread(
            materials_catalog.check_materials, root, pack_id)
    except Exception:
        catalog_check = {"available": False}
    return {
        "project_id": project_id,
        "total_files": len(files),
        "total_size": sum(f["size"] for f in files),
        "files": files,
        "dirs": dirs,
        "catalog_check": catalog_check,
    }


@router.get("/projects/{project_id}/materials/preview")
async def preview_material(project_id: int, http_req: Request, path: str = ""):
    """解析上传材料的原文（供“依据”标注点击后核对出处用，非下载）。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    # planning.md（写作总纲）不是上传材料、属于模板包：依据点击时同样返回原文供核对
    if (path or "").strip() == "planning.md":
        from backend.database.db import get_project_pack_id
        pp = pack_service.planning_path(await get_project_pack_id(project_id))
        if not pp.exists():
            raise HTTPException(status_code=404, detail="当前项目未绑定写作总纲 planning.md")
        return {"filename": "写作总纲 planning.md", "path": path,
                "text": pp.read_text(encoding="utf-8", errors="replace")[:120000]}
    root = _materials_dir(project_id)
    parts = [seg for seg in (path or "").replace("\\", "/").split("/") if seg and seg != "."]
    if not parts or any(seg == ".." for seg in parts):
        raise HTTPException(status_code=400, detail="无效的文件路径")
    fp = root
    for seg in parts:
        fp = fp / seg
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已被删除")
    try:
        text = await asyncio.to_thread(materials_client.extract_file_text, fp, "")
    except Exception as e:
        logger.error(f"解析材料失败 {fp}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析失败：{e}")
    return {"filename": fp.name, "path": path, "text": (text or "")[:120000]}


@router.get("/projects/{project_id}/materials/preview-pages")
async def preview_material_pages(project_id: int, http_req: Request, path: str = "",
                                 start: int = 1, count: int = 3, quote: str = "",
                                 hl_page: int = 0, hl_box: str = ""):
    """PDF 按页渲染成图片（仿 Word/WPS 原版观感），分页懒加载。
    quote 非空且 PDF 有文字层时，顺带返回摘录所在页 hit_page（忽略空白匹配）。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    root = _materials_dir(project_id)
    parts = [seg for seg in (path or "").replace("\\", "/").split("/") if seg and seg != "."]
    if not parts or any(seg == ".." for seg in parts):
        raise HTTPException(status_code=400, detail="无效的文件路径")
    fp = root
    for seg in parts:
        fp = fp / seg
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已被删除")
    if fp.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="仅 PDF 支持按页原版预览")

    def _render():
        import base64
        import io
        import re as _re
        import fitz
        from PIL import Image
        doc = fitz.open(str(fp))
        n = doc.page_count
        s = max(1, min(start, n))
        e = min(n, s + count - 1)
        box = None
        if hl_page and hl_box:
            try:
                box = [float(v) for v in hl_box.split(",")][:4]
            except Exception:
                box = None
        pages = []
        for i in range(s - 1, e):
            pix = doc[i].get_pixmap(dpi=120)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            if box and i + 1 == hl_page:
                # hl_box 统一为 120dpi 坐标，直接画半透明红框
                from PIL import ImageDraw
                x0, y0, x1, y1 = box
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                dr = ImageDraw.Draw(overlay)
                dr.rectangle([x0, y0, x1, y1], fill=(255, 90, 90, 40), outline=(225, 55, 55, 255), width=5)
                img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=82)
            pages.append({"page": i + 1, "img": base64.b64encode(buf.getvalue()).decode()})
        # 摘录定位页：先逐字/片段投票；不中再模糊（关键词重叠度）框大致位置；扫描件不在此搜（避免整篇 OCR）
        has_text = any(doc[i].get_text().strip() for i in range(min(n, 6)))
        fuzzy = False
        weak = False
        ftoks = None
        wtoks = None
        hit = _quote_page_hit(doc, n, quote) if (quote or "").strip() and has_text else None
        if not hit and (quote or "").strip() and has_text:
            fr = materials_client.fuzzy_quote_page_hit(doc, n, quote)
            if fr:
                hit, ftoks = fr
                fuzzy = True
        if not hit and (quote or "").strip() and has_text:
            # 概括性摘录兜底：AI 改写概括无逐字原文时，按主题词重叠取最相关页，
            # 并记住命中的主题词，供下面画框（弱命中也尽量框出最相关段落）
            wt = materials_client.weak_topic_tokens(quote)
            best_w = None
            for i in range(n):
                m = materials_client.weak_topic_match(materials_client.norm_q(doc[i].get_text()), wt)
                if m and (best_w is None or len(m) > len(best_w[1])):
                    best_w = (i + 1, m)
            if best_w:
                hit, fuzzy, weak = best_w[0], True, True
                wtoks = best_w[1]
        if hit and weak:
            hit_box = _text_highlight_box(doc, hit - 1, quote, wtoks)
        elif hit:
            hit_box = _text_highlight_box(doc, hit - 1, quote, ftoks)
        else:
            hit_box = None
        doc.close()
        return {"total": n, "pages": pages, "hit_page": hit, "has_text": has_text,
                "hit_box": hit_box, "fuzzy": fuzzy, "weak": weak}

    try:
        return await asyncio.to_thread(_render)
    except Exception as ex:
        logger.error(f"PDF 按页渲染失败 {fp}: {ex}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"渲染失败：{ex}")


# ===== 扫描件摘录搜页：后台线程用免费本地 OCR 逐页识别搜索（带磁盘缓存），前端轮询结果 =====
_quote_search_tasks = {}  # key -> {"status": "running"|"done", "hit": int|None, "scanned": int}


def _norm_q(s: str) -> str:
    return materials_client.norm_q(s)


def _quote_tokens(quote: str):
    return materials_client.quote_tokens(quote)


def _quote_page_hit(doc, n: int, quote: str):
    return materials_client.quote_page_hit(doc, n, quote)


def _text_highlight_box(doc, page_idx: int, quote: str, toks: list = None):
    """文字层高亮框：优先行级整行 bbox（框住整句更可读），不中再退 search_for 精确坐标。返回 120dpi 坐标。
    toks：模糊定位命中的特征词（逐字不中时由 fuzzy_quote_page_hit 给出），并入候选一起框。"""
    nums, frags = _quote_tokens(quote)
    page = doc[page_idx]
    cands = [f for f in frags if len(f) >= 6] + nums
    cands.append((quote or "").strip()[:16])
    cands += [t for t in (toks or []) if len(t) >= 3]
    nset = {_norm_q(f) for f in cands if len(f) >= 4}
    # 滑窗兜底：fuzzy 词经滑窗命中页面时，行级匹配也用同一套滑窗，保证能框出大致位置
    winset = {w for nf in nset for w in materials_client._tok_windows(nf)} if toks else set()
    hb = []
    lns = []
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            nt = _norm_q(txt)
            if len(nt) >= 6:
                lns.append((ln["bbox"], nt))
    for b, nt in lns:
        if any(nf in nt or nt in nf for nf in nset) or (winset and any(w in nt for w in winset)):
            hb.append(b)
    if not hb:
        # 特征词被 PDF 行切断：单行永远不中、页级却能中→相邻 2~3 行合并再匹配，框出大致位置
        for i in range(len(lns)):
            for j in (1, 2):
                if i + j >= len(lns):
                    break
                nt = "".join(t for _, t in lns[i:i + j + 1])
                if any(nf in nt for nf in nset) or (winset and any(w in nt for w in winset)):
                    hb += [lns[k][0] for k in range(i, i + j + 1)]
                    break
    hb = hb[:8]
    if hb:
        k = 120 / 72
        x0 = min(b[0] for b in hb) * k
        y0 = min(b[1] for b in hb) * k
        x1 = max(b[2] for b in hb) * k
        y1 = max(b[3] for b in hb) * k
        return [max(0, x0 - 14), max(0, y0 - 30), x1 + 14, y1 + 36]
    rects = []
    for tok in cands:
        try:
            rects += list(page.search_for(tok))
        except Exception:
            pass
    if not rects:
        return None
    k = 120 / 72
    x0 = min(r.x0 for r in rects) * k
    y0 = min(r.y0 for r in rects) * k
    x1 = max(r.x1 for r in rects) * k
    y1 = max(r.y1 for r in rects) * k
    return [max(0, x0 - 14), max(0, y0 - 30), x1 + 14, y1 + 36]


def _resolve_material_fp(project_id: int, path: str) -> Path:
    root = _materials_dir(project_id)
    parts = [seg for seg in (path or "").replace("\\", "/").split("/") if seg and seg != "."]
    if not parts or any(seg == ".." for seg in parts):
        raise HTTPException(status_code=400, detail="无效的文件路径")
    fp = root
    for seg in parts:
        fp = fp / seg
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="文件不存在或已被删除")
    return fp


def _run_quote_search(key: str, fp: Path, quote: str):
    """后台逐页搜索摘录：先严格（数字/片段/前12字）命中即停；
    全文扫完无严格命中时，按模糊关键词重叠度取最优页框大致位置（fuzzy=True）。"""
    import re as _re
    try:
        nums, frags = _quote_tokens(quote)
        ftoks = materials_client.fuzzy_quote_tokens(quote)
        need = materials_client.fuzzy_hit_threshold(ftoks)
        qn = _re.sub(r"\s+", "", quote)
        head = qn[:12]
        n = materials_client.pdf_page_count(fp)
        hit = None
        box = None
        fuzzy = False
        weak = False
        best_fuzzy = None  # (页索引, 命中词列表)
        page_texts = []  # 各页归一化 OCR 文本（弱命中兜底复用，避免重读缓存）
        for i in range(n):
            t = _norm_q(materials_client.ocr_page_text(fp, i))
            page_texts.append(t)
            if not t:
                continue
            if (nums and any(num in t for num in nums)) or (frags and any(_norm_q(f) in t for f in frags)) or (head and head in t):
                hit = i + 1
                b = materials_client.ocr_page_highlight_box(fp, i, (nums or []) + (frags or []) or ftoks)
                box = [v * 1.2 for v in b] if b else None  # 100dpi→120dpi，与文字层约定一致
                break
            m = materials_client.fuzzy_match_tokens(t, ftoks)
            if m and (best_fuzzy is None or len(m) > len(best_fuzzy[1])):
                best_fuzzy = (i, m)
            _quote_search_tasks[key] = {"status": "running", "hit": None, "scanned": i + 1}
        # 宽松兜底（与 fuzzy_quote_page_hit 同规则）：达阈值、或重叠≥2、或≤6页小文档重叠≥1
        if hit is None and best_fuzzy and (len(best_fuzzy[1]) >= need or len(best_fuzzy[1]) >= 2
                                           or (n <= 6 and len(best_fuzzy[1]) >= 1)):
            hit = best_fuzzy[0] + 1
            fuzzy = True
            b = materials_client.ocr_page_highlight_box(fp, best_fuzzy[0], best_fuzzy[1])
            box = [v * 1.2 for v in b] if b else None
        if hit is None:
            # 概括性摘录兜底：AI 改写概括无逐字原文时，按主题词重叠取最相关页，
            # 并用命中的主题词框出最相关段落（OCR 词级坐标）
            wt = materials_client.weak_topic_tokens(quote)
            best_w = None
            for i, t in enumerate(page_texts):
                m = materials_client.weak_topic_match(t, wt)
                if m and (best_w is None or len(m) > len(best_w[1])):
                    best_w = (i, m)
            if best_w:
                hit = best_w[0] + 1
                fuzzy, weak = True, True
                b = materials_client.ocr_page_highlight_box(fp, best_w[0], best_w[1])
                box = [v * 1.2 for v in b] if b else None  # 100dpi→120dpi，与文字层约定一致
        _quote_search_tasks[key] = {"status": "done", "hit": hit, "scanned": n,
                                    "box": box,
                                    "fuzzy": fuzzy if hit else False,
                                    "weak": weak if hit else False}
    except Exception as e:
        logger.error(f"摘录搜页失败 {fp}: {e}", exc_info=True)
        _quote_search_tasks[key] = {"status": "done", "hit": None, "scanned": -1}


@router.get("/projects/{project_id}/materials/quote-search")
async def quote_search(project_id: int, http_req: Request, path: str = "", quote: str = ""):
    """启动（或复用）扫描件摘录搜页任务，返回 task key 供轮询。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    fp = _resolve_material_fp(project_id, path)
    if fp.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="仅 PDF 支持摘录搜页")
    import hashlib
    key = hashlib.md5(f"{fp}|{fp.stat().st_size}|{(quote or '').strip()}".encode()).hexdigest()
    task = _quote_search_tasks.get(key)
    if not task or task.get("status") == "done" and task.get("scanned") == -1:
        _quote_search_tasks[key] = {"status": "running", "hit": None, "scanned": 0}
        threading.Thread(target=_run_quote_search, args=(key, fp, (quote or "").strip()), daemon=True).start()
    return {"task": key, **_quote_search_tasks[key]}


@router.get("/projects/{project_id}/materials/quote-search-result")
async def quote_search_result(project_id: int, http_req: Request, task: str = ""):
    """轮询搜页任务结果。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    return _quote_search_tasks.get(task) or {"status": "running", "hit": None, "scanned": 0}


@router.post("/projects/{project_id}/chapters/{n}/verify-refs")
async def verify_chapter_refs(project_id: int, n: int, http_req: Request):
    """对已生成章节的依据跑一遍自检纠偏（路径归一+摘录换原文），用于修复存量章节。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    from backend.services import skill_runner
    data = skill_runner._load_json(n, str(project_id))
    if not data.get("sections"):
        raise HTTPException(status_code=404, detail=f"第{n}章还没有生成内容")
    mat_root = _materials_dir(project_id)

    def _run():
        st = skill_runner.verify_fix_refs(data["sections"], mat_root)
        skill_runner._save_json(n, data, str(project_id))
        return st

    st = await asyncio.to_thread(_run)
    return {"message": f"依据自检完成：共{st['total']}条，路径修正{st['fixed_path']}，"
                       f"摘录改原文{st['replaced']}，摘录换正确文件{st['rehomed']}，补摘录{st['added']}，"
                       f"不涉及去依据{st['removed_inapplicable']}，删无源依据{st['removed_untraceable']}", **st}


# 天眼查企业名→官网 URL 缓存（避免重复消耗 MCP 查询）
_TYC_URL_CACHE: dict = {}


@router.get("/tianyancha/company-url")
async def tianyancha_company_url(name: str = ""):
    """天眼查依据→可点击的官网网址：调 MCP search_companies，
    企业名与候选表精确匹配则给公司详情页 tianyancha.com/company/{id}；
    匹配不到/未配置/查询失败则给搜索页（搜索页首条即目标企业）。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少企业名称")
    if name in _TYC_URL_CACHE:
        return _TYC_URL_CACHE[name]

    def _resolve():
        from backend.services import tianyancha_client
        if not tianyancha_client.is_enabled():
            return None
        out = tianyancha_client.call("search_companies", {"query": name})
        header = None
        for ln in (out or "").splitlines():
            s = ln.strip()
            if s.startswith("|") and "企业名称" in s and "企业ID" in s:
                header = [c.strip() for c in s.strip("|").split("|")]
                continue
            if header and s.startswith("|") and not set(s) <= set("|-: "):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) == len(header):
                    row = dict(zip(header, cells))
                    cid = (row.get("企业ID") or "").strip()
                    # 只认精确同名候选，避免跳错公司
                    if row.get("企业名称") == name and cid.isdigit():
                        return f"https://www.tianyancha.com/company/{cid}"
        return None

    url = await asyncio.to_thread(_resolve)
    if not url:
        url = f"https://www.tianyancha.com/search?key={quote(name)}"
    resp = {"url": url, "company": name}
    _TYC_URL_CACHE[name] = resp
    return resp


@router.delete("/projects/{project_id}/materials")
async def clear_materials(project_id: int, http_req: Request):
    """清空当前项目的全部申报材料。"""
    await _assert_project_owned(project_id, _current_user_id(http_req))
    shutil.rmtree(_materials_dir(project_id), ignore_errors=True)
    return {"message": "材料已清空", "project_id": project_id}


