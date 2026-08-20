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

from backend.config import PACKS_DIR, DATA_SOURCE_BASE

logger = logging.getLogger(__name__)

# ===== 用户修改覆盖层 =====
# 代码包内的 skill 文本是“默认值”；用户在网页上修改后的版本存到数据卷的
# skill_overrides/<pack_id>/<包内相对路径>，容器重建/重新发版不丢失，
# 也不污染代码默认值（可随时重置回默认）。仅文本类 skill 适用，
# web_render.py 等代码脚本不走覆盖层。
OVERRIDES_DIR = DATA_SOURCE_BASE / "skill_overrides"


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


def override_path(rel: str, pack_id: str = None) -> Path:
    """用户修改覆盖件的存储路径（可能尚不存在）；同样防目录穿越。"""
    pid = Path(pack_id or default_pack_id()).name
    base = (OVERRIDES_DIR / pid).resolve()
    p = (base / rel.replace("\\", "/")).resolve()
    if not p.is_relative_to(base):
        raise PackNotFoundError(f"非法覆盖路径：{rel}")
    return p


def skill_text_path(rel: str, pack_id: str = None) -> Path:
    """文本类 skill 的生效路径：有用户覆盖件时优先覆盖件，否则代码包默认件。
    读取链（生成/缓存失效判断）统一走这里，用户修改即时生效。"""
    ov = override_path(rel, pack_id)
    return ov if ov.exists() else pack_path(rel, pack_id)


def is_overridden(rel: str, pack_id: str = None) -> bool:
    return override_path(rel, pack_id).exists()


def planning_path(pack_id: str = None) -> Path:
    return skill_text_path("planning.md", pack_id)


def reading_path(n: int, pack_id: str = None) -> Path:
    ch = get_chapters(pack_id).get(int(n))
    if not ch:
        raise PackNotFoundError(f"模板包没有第 {n} 章")
    return skill_text_path(ch["reading"], pack_id)


def summary_reading_path(pack_id: str = None) -> Path:
    """卷首"摘要表和释义"写作要求 reading/summary.md 的生效路径（走覆盖层）。"""
    return skill_text_path("reading/summary.md", pack_id)


def writing_skill_path(pack_id: str = None) -> Path:
    """写作/排版要求 writing/SKILL.md 的生效路径（走覆盖层）。"""
    return skill_text_path("writing/SKILL.md", pack_id)


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
