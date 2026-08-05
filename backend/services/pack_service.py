"""模板包（templates-packs）服务 - 扫描、加载、解析模板包

模板包规范（每个包是 templates-packs/ 下的一个自包含目录）：
    manifest.json    {id, name, version, description, chapter_count}
    template.docx    官方格式 Word 模板
    chapters.json    章节结构（替代原先写死在代码里的 CHAPTERS）
    planning.md      全局总纲（跨章共性要求）
    reading/ch{n}.md 各章写作要求
    writing/         SKILL.md（排版要求）+ web_render.py（渲染脚本，支持热重载）
    diagrams/        drawio 画图模板

引擎本身不认识任何具体业务：换一个包即可生成另一套材料。
当前单包阶段由 DEFAULT_PACK_ID 选定唯一包；多包+项目绑定在步骤 2.5 引入。
"""
import json
import logging
import os
from pathlib import Path

from backend.config import PACKS_DIR

logger = logging.getLogger(__name__)


class PackNotFoundError(Exception):
    pass


def list_packs() -> list:
    """列出所有可用模板包的 manifest（供 /api/packs 与前端下拉）。"""
    packs = []
    if not PACKS_DIR.exists():
        return packs
    for d in sorted(PACKS_DIR.iterdir()):
        mf = d / "manifest.json"
        if not d.is_dir() or not mf.exists():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.warning(f"读取模板包 manifest 失败({d.name}): {e}")
            continue
        data.setdefault("id", d.name)
        packs.append(data)
    return packs


def default_pack_id() -> str:
    """当前生效的包：环境变量 PACK_ID 指定；未指定时取第一个可用包。"""
    pid = (os.environ.get("PACK_ID") or "").strip()
    if pid:
        return pid
    packs = list_packs()
    if packs:
        return packs[0]["id"]
    raise PackNotFoundError(f"templates-packs/ 下没有可用模板包：{PACKS_DIR}")


def get_pack(pack_id: str = None) -> dict:
    """返回 {'id', 'dir', 'manifest'}；pack_id 为空时用默认包。"""
    pid = (pack_id or default_pack_id()).strip()
    safe = Path(pid).name  # 防目录穿越
    d = PACKS_DIR / safe
    mf = d / "manifest.json"
    if not d.is_dir() or not mf.exists():
        raise PackNotFoundError(f"模板包不存在：{safe}")
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8-sig"))
    except Exception:
        manifest = {}
    manifest.setdefault("id", safe)
    return {"id": safe, "dir": d, "manifest": manifest}


def get_chapters(pack_id: str = None) -> dict:
    """章节结构 {n: {title, next, reading}}；reading 是包内相对路径。"""
    pack = get_pack(pack_id)
    cj = pack["dir"] / "chapters.json"
    if not cj.exists():
        raise PackNotFoundError(f"模板包缺少 chapters.json：{pack['id']}")
    data = json.loads(cj.read_text(encoding="utf-8-sig"))
    return {
        int(c["n"]): {"title": c["title"], "next": c.get("next"),
                      "reading": c.get("reading", f"reading/ch{int(c['n'])}.md")}
        for c in data.get("chapters", [])
    }


def pack_path(rel: str, pack_id: str = None) -> Path:
    """包内任意资源路径（相对路径）；解析后校验不得逃逸出包目录，防目录穿越。"""
    d = get_pack(pack_id)["dir"].resolve()
    p = (d / rel.replace("\\", "/")).resolve()
    if not p.is_relative_to(d):
        raise PackNotFoundError(f"非法包内资源路径：{rel}")
    return p


def planning_path(pack_id: str = None) -> Path:
    return pack_path("planning.md", pack_id)


def reading_path(n: int, pack_id: str = None) -> Path:
    ch = get_chapters(pack_id).get(int(n))
    if not ch:
        raise PackNotFoundError(f"模板包没有第 {n} 章")
    return pack_path(ch["reading"], pack_id)


def writing_script_dir(pack_id: str = None) -> Path:
    """writing/ 目录（web_render.py 所在处，热重载入口）。"""
    return pack_path("writing", pack_id)


def diagram_dir(pack_id: str = None) -> Path:
    return pack_path("diagrams", pack_id)


def template_docx(pack_id: str = None) -> Path:
    return pack_path("template.docx", pack_id)


def material_label(pack_id: str = None) -> str:
    """材料类型名称（manifest.material_label，供引擎拼提示词用，缺省为“申报材料”），
    避免在引擎代码里写死具体业务词。"""
    try:
        return get_pack(pack_id)["manifest"].get("material_label", "申报材料")
    except PackNotFoundError:
        return "申报材料"
