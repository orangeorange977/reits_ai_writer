# -*- coding: utf-8 -*-
"""申报材料缺件检测（借鉴 Archive reits-proof-extractor-robust 的 check_missing 机制）。

依据模板包内 catalog.json（2024年版格式文本附件1 的 25 项证明材料清单），
对项目已上传材料目录做关键词比对，输出缺件清单供前端提示。

设计约束：
- 纯函数 + 全量容错：任何异常都不得阻断材料列表展示，失败时返回 available=False；
- 匹配以"文件名/文件夹名含关键词"为准；能先定位到大类文件夹（folder_hints）就只在该子树里找，
  定位不到则全目录兜底找，避免用户目录命名不规范导致误报；
- optional（"如涉及"）项缺失只进提醒列表，不计入缺件数。
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def load_catalog(pack_id: str = None):
    """读模板包内 catalog.json；不存在或不可解析返回 None。"""
    try:
        from backend.services import pack_service
        p = pack_service.pack_path("catalog.json", pack_id)
        if not p.exists():
            return None
        import json
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data.get("items"), list) or not data["items"]:
            return None
        return data
    except Exception as e:
        logger.warning("catalog.json 读取失败：%s", e)
        return None


def _walk_names(mat_root: Path) -> list:
    """收集目录下所有文件/文件夹的相对路径名（小写归一前保留原文）。"""
    names = []
    for p in mat_root.rglob("*"):
        try:
            names.append(p.relative_to(mat_root).as_posix())
        except ValueError:
            continue
    return names


def _scope_by_category(all_names: list, folder_hints: dict, category: str) -> list:
    """若大类文件夹能定位（如"一、参与主体情况"），把搜索范围收窄到该子树；
    定位不到返回全量，宁可多匹配也不误报缺件。"""
    hints = folder_hints.get(category) or []
    if not hints:
        return all_names
    tops = []
    for n in all_names:
        head = n.split("/", 1)[0]
        if any(h in head for h in hints):
            tops.append(head)
    if not tops:
        return all_names
    tops = set(tops)
    return [n for n in all_names if n.split("/", 1)[0] in tops]


def _matched(scope_names: list, match: dict) -> bool:
    kws = [_norm(k) for k in (match or {}).get("keywords", []) if k]
    if not kws:
        return bool((match or {}).get("any_folder_accepts"))
    return any(any(k in _norm(n) for k in kws) for n in scope_names)


def check_materials(mat_root, pack_id: str = None) -> dict:
    """比对材料目录与 25 项清单，返回缺件体检结果。

    返回结构：
    {
      "available": bool,            # 清单/目录是否可用（任一不可用则前端不展示）
      "missing": [{"no","name","category"}...],          # 必交项缺失
      "optional_missing": [...],    # "如涉及"项缺失，仅提醒
      "found_count": int, "required_count": int,
      "message": str                # 业务语言汇总，如"缺少2项材料：……"
    }
    """
    result = {"available": False, "missing": [], "optional_missing": [],
              "found_count": 0, "required_count": 0, "message": ""}
    try:
        catalog = load_catalog(pack_id)
        if not catalog:
            return result
        root = Path(mat_root) if mat_root else None
        if not root or not root.is_dir():
            return result
        all_names = _walk_names(root)
        if not all_names:
            return result
        folder_hints = catalog.get("folder_hints") or {}
        missing, optional_missing = [], []
        found = 0
        required = 0
        for item in catalog["items"]:
            if not item.get("optional"):
                required += 1
            scope = _scope_by_category(all_names, folder_hints, item.get("category", ""))
            if _matched(scope, item.get("match") or {}):
                found += 1
                continue
            entry = {"no": item.get("no"), "name": item.get("name", ""),
                     "category": item.get("category", "")}
            if item.get("optional"):
                optional_missing.append(entry)
            else:
                missing.append(entry)
        result.update({
            "available": True,
            "missing": missing,
            "optional_missing": optional_missing,
            "found_count": found,
            "required_count": required,
        })
        if missing:
            names = "、".join(f"{m['name']}（第{m['no']}项）" for m in missing[:5])
            more = f"等{len(missing)}项" if len(missing) > 5 else ""
            result["message"] = f"缺少{len(missing)}项材料：{names}{more}，建议补充后再发起生成"
        else:
            result["message"] = ""
        return result
    except Exception as e:
        logger.warning("缺件检测失败（不影响材料列表）：%s", e)
        return result
