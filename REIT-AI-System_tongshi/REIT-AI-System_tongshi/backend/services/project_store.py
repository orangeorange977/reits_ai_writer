"""多项目数据隔离。

设计要点：
- **公用**的是 skill（各章 SKILL.md、planning.md、官方模板）——所有项目共享，不复制；
- **私有**的是每个项目自己保存/生成的 JSON（摘要表、各章 ch{n}.json、封面配置与图片），
  各存一份，互不覆盖。

存储结构：
- 登记表  skills/projects/index.json ：
    {"current": "<pid>", "projects": [{id,name,created_at,template_path,materials_path,model}, ...]}
- 每个项目一个目录  skills/projects/<pid>/ ：summary_saved.json、ch1..7.json、
  ch{n}_output.docx、cover_saved.json、cover_assets/。

首次运行（还没有 index.json）时，会把旧的“单项目”数据（散落在 SKILLS_DIR 各处：
summary_saved.json、reits-reading-ch{n}/ch{n}.json、cover_saved.json、cover_assets/、
project_meta.json、app_settings.json、model_setting.json）**复制**进一个默认项目里，
原文件保留作备份，绝不删除。
"""
import contextvars
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from backend.config import SKILLS_DIR

PROJECTS_ROOT = SKILLS_DIR / "projects"
INDEX_PATH = PROJECTS_ROOT / "index.json"

# index 读改写用可重入锁（多用户并发下保护登记表）
_lock = threading.RLock()

# 本次请求指定的项目 id（多人各改各的：每个浏览器在请求头/参数里带上自己的项目 id，
# 由路由依赖写进这个 contextvar；asyncio 的 to_thread / create_task 会自动复制上下文，
# 所以后台生成线程/任务里读到的仍是发起请求那个用户的项目——互不串）。
_ctx_pid = contextvars.ContextVar("reit_project_id", default=None)


def set_request_project(pid: str) -> None:
    """把“本请求所属项目”写进上下文（由路由依赖在每个请求开始时调用）。"""
    _ctx_pid.set((pid or "").strip() or None)

_FIELDS = ("name", "template_path", "materials_path", "model")

# —— 旧的“单项目”数据位置（仅首次迁移时读取，只复制不删除）——
_LEGACY_SUMMARY = SKILLS_DIR / "summary_saved.json"
_LEGACY_COVER_CFG = SKILLS_DIR / "cover_saved.json"
_LEGACY_COVER_ASSETS = SKILLS_DIR / "cover_assets"
_LEGACY_META = SKILLS_DIR / "project_meta.json"
_LEGACY_APP_SETTINGS = SKILLS_DIR / "app_settings.json"
_LEGACY_MODEL = SKILLS_DIR / "model_setting.json"


def _legacy_chapter_json(n: int) -> Path:
    return SKILLS_DIR / f"reits-reading-ch{n}" / f"ch{n}.json"


def _read_json(p: Path, default=None):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        pass
    return default


def _new_pid() -> str:
    return "p_" + uuid.uuid4().hex[:8]


def _write_index(idx: dict) -> None:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _migrate_legacy(idx: dict) -> dict:
    """把旧单项目数据复制进一个默认项目（不删原文件）。"""
    # 名称：优先旧 project_meta 的 display_name，其次旧摘要表的“项目名称”
    meta = _read_json(_LEGACY_META, {}) or {}
    name = str(meta.get("display_name", "")).strip()
    if not name:
        summ = _read_json(_LEGACY_SUMMARY, {}) or {}
        for r in summ.get("summary_table", []) or []:
            if str(r.get("label", "")).strip() == "项目名称":
                name = str(r.get("value", "")).strip()
                break
    if not name:
        name = "默认项目"

    app = _read_json(_LEGACY_APP_SETTINGS, {}) or {}
    ms = _read_json(_LEGACY_MODEL, {}) or {}

    pid = "p_default"
    d = PROJECTS_ROOT / pid
    d.mkdir(parents=True, exist_ok=True)

    # 复制数据文件（存在才复制）
    try:
        if _LEGACY_SUMMARY.exists():
            shutil.copy2(_LEGACY_SUMMARY, d / "summary_saved.json")
        if _LEGACY_COVER_CFG.exists():
            shutil.copy2(_LEGACY_COVER_CFG, d / "cover_saved.json")
        if _LEGACY_COVER_ASSETS.is_dir():
            shutil.copytree(_LEGACY_COVER_ASSETS, d / "cover_assets", dirs_exist_ok=True)
        for n in range(1, 8):
            src = _legacy_chapter_json(n)
            if src.exists():
                shutil.copy2(src, d / f"ch{n}.json")
    except Exception:
        # 迁移失败不应让系统起不来；至少留一个空的默认项目
        pass

    rec = {
        "id": pid,
        "name": name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "template_path": str(app.get("template_path", "") or ""),
        "materials_path": str(app.get("materials_path", "") or ""),
        "model": str(ms.get("model", "") or ""),
    }
    idx["projects"].append(rec)
    idx["current"] = pid
    return idx


def _ensure_index() -> dict:
    with _lock:
        idx = _read_json(INDEX_PATH)
        if isinstance(idx, dict) and idx.get("projects"):
            return idx
        idx = {"current": "", "projects": []}
        _migrate_legacy(idx)
        _write_index(idx)
        return idx


# ==================== 对外接口 ====================

def list_projects() -> list:
    """全部项目登记信息（不含各项目进度）。"""
    return [dict(p) for p in _ensure_index()["projects"]]


def current_project_id() -> str:
    idx = _ensure_index()
    ids = {p["id"] for p in idx["projects"]}
    # 1) 本请求显式指定的项目（多人各改各的，优先级最高）
    pid = _ctx_pid.get()
    if pid and pid in ids:
        return pid
    # 2) 服务器记录的默认当前项目（新浏览器首次进来的落地项目）
    cur = idx.get("current")
    if cur and cur in ids:
        return cur
    # 3) 兜底：第一个
    return idx["projects"][0]["id"] if idx["projects"] else ""


def get_project(pid: str = None) -> dict:
    idx = _ensure_index()
    pid = pid or current_project_id()
    for p in idx["projects"]:
        if p["id"] == pid:
            return dict(p)
    return {}


def project_dir(pid: str = None) -> Path:
    pid = pid or current_project_id() or "p_default"
    d = PROJECTS_ROOT / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_dir() -> Path:
    """当前项目的数据目录（摘要表/各章JSON/封面都存这里）。"""
    return project_dir(None)


def set_current(pid: str) -> str:
    with _lock:
        idx = _ensure_index()
        if not any(p["id"] == pid for p in idx["projects"]):
            raise ValueError(f"项目不存在：{pid}")
        idx["current"] = pid
        _write_index(idx)
        return pid


def create_project(name: str = "", template_path: str = "",
                   materials_path: str = "", model: str = "",
                   make_current: bool = True) -> dict:
    with _lock:
        idx = _ensure_index()
        pid = _new_pid()
        while any(p["id"] == pid for p in idx["projects"]):
            pid = _new_pid()
        (PROJECTS_ROOT / pid).mkdir(parents=True, exist_ok=True)
        rec = {
            "id": pid,
            "name": (name or "未命名项目").strip(),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "template_path": (template_path or "").strip(),
            "materials_path": (materials_path or "").strip(),
            "model": (model or "").strip(),
        }
        idx["projects"].append(rec)
        if make_current:
            idx["current"] = pid
        _write_index(idx)
        return dict(rec)


def update_project(pid: str = None, patch: dict = None) -> dict:
    with _lock:
        idx = _ensure_index()
        pid = pid or current_project_id()
        for p in idx["projects"]:
            if p["id"] == pid:
                for k, v in (patch or {}).items():
                    if k in _FIELDS and v is not None:
                        p[k] = v
                _write_index(idx)
                return dict(p)
        return {}


def delete_project(pid: str) -> bool:
    with _lock:
        idx = _ensure_index()
        remaining = [p for p in idx["projects"] if p["id"] != pid]
        if len(remaining) == len(idx["projects"]):
            return False  # 不存在
        if not remaining:
            raise ValueError("至少要保留一个项目，不能删除最后一个")
        idx["projects"] = remaining
        if idx.get("current") == pid:
            idx["current"] = remaining[0]["id"]
        _write_index(idx)
        try:
            shutil.rmtree(PROJECTS_ROOT / pid)
        except OSError:
            pass
        return True


# —— 当前项目的几个常用字段（供各服务读取）——

def current_name() -> str:
    return str(get_project().get("name", "")).strip()


def current_template_path() -> str:
    return str(get_project().get("template_path", "")).strip()


def current_materials_path() -> str:
    return str(get_project().get("materials_path", "")).strip()


def current_model() -> str:
    return str(get_project().get("model", "")).strip()
