# -*- coding: utf-8 -*-
"""材料阅读台账（借鉴 Archive reits-proof-extractor-robust 的 read_items 覆盖率机制）。

章节生成期间，AI 每读取一份申报材料（read_document 工具）就登记一条：
哪个文件、哪些页、第几章读的、读了几次。落盘在项目目录 read_ledger.json，
供"本章/本项目到底读了哪些材料"的量化核对（对账 server 日志、后续覆盖率校验都用它）。

设计约束：
- 全量容错：任何异常只告警不抛出，绝不阻断章节生成；
- 原子写入：写临时文件再替换，避免生成中断留下半截 JSON；
- 重跑覆盖：章节开跑前调 reset_chapter 清掉该章旧条目，台账始终反映最新一次生成；
- 页码归一：'5' / '1-3' / '2,4' 统一展开成整数列表去重；空 pages 表示整篇读取，记 "整篇"。
"""
import datetime
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def ledger_path(project_id) -> Path:
    from backend.config import PROJECTS_DIR, safe_project_id
    pid = safe_project_id(project_id)
    return PROJECTS_DIR / pid / "read_ledger.json"


def _parse_pages(pages: str):
    """'1-3' '5' '2,4' → [1,2,3,5,2,4]→去重排序；空 → '整篇'。"""
    s = str(pages or "").strip()
    if not s:
        return "整篇"
    out = set()
    for seg in s.replace("，", ",").split(","):
        seg = seg.strip()
        if not seg:
            continue
        if "-" in seg:
            try:
                a, b = seg.split("-", 1)
                a, b = int(a), int(b)
                if a > b:
                    a, b = b, a
                out.update(range(a, min(b, a + 500) + 1))  # 上限保护，防异常区间撑爆
            except ValueError:
                continue
        else:
            try:
                out.add(int(seg))
            except ValueError:
                continue
    return sorted(out) if out else "整篇"


def _load(path: Path) -> list:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _dump(path: Path, entries: list) -> None:
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    tmp.replace(path)


def reset_chapter(project_id, chapter: int) -> None:
    """章节开跑前调：清掉该章旧台账（重跑覆盖）。失败静默不阻断。"""
    if not project_id:
        return
    try:
        with _LOCK:
            lp = ledger_path(project_id)
            if not lp.exists():
                return
            entries = [e for e in _load(lp) if e.get("chapter") != int(chapter)]
            _dump(lp, entries)
    except Exception as e:
        logger.warning("阅读台账清理失败（不影响生成）：%s", e)


def record_read(project_id, chapter: int, path: str, pages: str = "") -> None:
    """登记一次材料读取。同章同文件合并（页码并集、次数+1）。失败静默不阻断。"""
    path = str(path or "").strip()
    if not path or not project_id:
        return
    try:
        with _LOCK:
            lp = ledger_path(project_id)
            lp.parent.mkdir(parents=True, exist_ok=True)
            entries = _load(lp)
            hit = next((e for e in entries
                        if e.get("chapter") == int(chapter) and e.get("path") == path), None)
            pg = _parse_pages(pages)
            now = datetime.datetime.now().isoformat(timespec="seconds")
            if hit:
                hit["reads"] = int(hit.get("reads", 1)) + 1
                hit["last_at"] = now
                if pg == "整篇" or hit.get("pages") == "整篇":
                    hit["pages"] = "整篇"
                else:
                    hit["pages"] = sorted(set(hit.get("pages") or []) | set(pg))
            else:
                entries.append({"chapter": int(chapter), "path": path,
                                "pages": pg, "reads": 1, "last_at": now})
            _dump(lp, entries)
    except Exception as e:
        logger.warning("阅读台账登记失败（不影响生成）：%s", e)


def chapter_stats(project_id, chapter: int) -> dict:
    """某章的阅读统计（业务语言展示用）。"""
    empty = {"files": 0, "pages": 0, "message": ""}
    try:
        entries = [e for e in _load(ledger_path(project_id))
                   if e.get("chapter") == int(chapter)]
        if not entries:
            return empty
        pages = 0
        for e in entries:
            if e.get("pages") == "整篇":
                pages += 1  # 整篇按 1 计，仅用于数量级参考
            else:
                pages += len(e.get("pages") or [])
        return {"files": len(entries), "pages": pages,
                "message": f"本章生成时共查阅材料 {len(entries)} 份"}
    except Exception:
        return empty


def get_ledger(project_id) -> list:
    """全量台账（只读副本）。"""
    try:
        return _load(ledger_path(project_id))
    except Exception:
        return []
