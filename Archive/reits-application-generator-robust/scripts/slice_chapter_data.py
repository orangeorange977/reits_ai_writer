# -*- coding: utf-8 -*-
"""按章切片：从 extracted_data.json + base_vars.json 抽出本章所需字段的扁平小文件。

为什么需要它（历史事故，2026-08 实测）：
- extracted_data.json 260~390KB，子agent 不敢整读 → 改用 jq 猜字段路径探路；
  jq 返回空时无法区分「路径写错」与「数据缺失」→ 换路径再猜 → 无限循环、零产出。
- sub_projects 是提取中间态而非干净数据源：同一子项目被每份证明材料各提取一次
  （实测 41 个条目，同一 A-18 出现 10+ 个同名/近名副本，还混入兄弟项目与园区总面积），
  jq sub_projects[0] 恰好命中空壳条目 → 加剧探路。
- base_vars.json 已由主agent（或初稿摘要表）完成「多副本收敛为唯一值」并带溯源，
  是 ch1 的权威取数口。

本脚本把「选定」动作从子agent手里收回脚本：
- base_vars 已有且非空 → 直接用（OK），source 带出 _sources/citations 对应条目
- base_vars 没有/为空 → 在 sub_projects 中按「当前子项目名」模糊匹配收集非空候选：
  唯一 → OK；多个不一致 → CONFLICT（全列出，子agent不得自行二选一）；零 → MISSING
- 字段存在但为空串 → EMPTY

子agent 纪律（写进切片文件的 _usage，同时由 ch1_guide.md 与 chapter_writer_prompt.md 约束）：
  看到 MISSING/EMPTY 立即写【注：……待核实】收工；CONFLICT 取 candidates 第一条并在
  回复中报告冲突；⛔ 禁止打开 extracted_data.json、禁止 jq/grep 探路。

用法：
  python slice_chapter_data.py --chapter 1 \
      --extracted <work_dir>/extracted_data.json \
      --base-vars <work_dir>/base_vars.json \
      --out <work_dir>/ch1_data.json
  python slice_chapter_data.py --chapter 2 ... --out <work_dir>/ch2_data.json

ch2 切片（--chapter 2）：供第二章剩余创作段（#7法律关系/#10业务论述/#11财务分析）的子agent取数：
- entities.originators/project_companies 是提取中间态（实测13/26条，同主体多副本+
  无关实体混入）→ 按 base_vars 权威名（originator/project_company）选取并合并副本；
- 副本收敛/选取逻辑与 gen_phase_fill_plan.py 的 auto_paragraphs 同源（直接 import），
  保证脚本直出段与子agent创作段看到的是同一拨主体。
"""
import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# ch1 字段清单：与 templates/chapter_guides/ch1_guide.md 的占位符一一对应。
# (base_vars 键, [sub_projects 候选键], 用途说明)
# base_vars 键为 None 表示该字段只能指望 sub_projects 归一。
# ---------------------------------------------------------------------------
CH1_FIELDS = {
    "项目名称":       ("project_name", [], "正文两段+表1"),
    "子项目名称":     ("sub_project_name", [], "正文两段+表1（评估报告原文、不带「栋」字）"),
    "行业领域":       ("industry", [], "表1+（二）四类判定"),
    "项目类型":       ("project_type", [], "正文第1段（所有权类/经营收益权类）"),
    "申报基准日":     ("evaluation_base_date", [], "正文两段+表1权属行"),
    "不动产评估值":   ("evaluation_value", [], "正文两段+表1"),
    "不动产评估净值": ("evaluation_net_value", [], "正文两段+表1"),
    "资产所在地":     ("location_full", ["location"], "表1（明确到县区级）"),
    "用地面积":       ("land_area", ["land_area"], "表1建设内容和规模"),
    "建筑面积":       ("building_area", ["building_area"], "正文两段+表1"),
    "计费机柜":       ("rack_count", ["rack_count", "billing_rack_count"], "表1建设内容和规模"),
    "机柜总功率":     (None, ["design_power", "total_rack_power"], "表1建设内容和规模"),
    "运营起始时间":   ("operation_start", ["operation_start"], "正文两段+表1（精确到年月/日）"),
    "竣工时间":       ("completion_year", ["completion_year", "construction_completion"], "表1开竣工时间"),
    "开工时间":       (None, ["construction_start"], "表1开竣工时间"),
    "权属到期日":     ("land_expiry", ["land_expiry", "property_right_end"], "表1权属行+剩余年限计算"),
    "权属证明":       ("property_cert", ["property_cert"], "正文（权属判定辅助）"),
    "资产范围":       (None, ["asset_scope"], "表1四至详述（不抄摘要表概述）"),
    "建设规模合计":   (None, ["construction_cost"], "表1建设内容和规模+决算总投资"),
}

_USAGE = (
    "本文件是第一章唯一取数口（主agent已从 extracted_data.json / base_vars.json 切片并选定）。"
    "status=OK 直接用 value；EMPTY/MISSING 写【注：……待核实】并向上游报缺；"
    "CONFLICT 取 candidates[0] 写入、并在完成回复里报告冲突项由主agent复核。"
    "⛔ 禁止打开 extracted_data.json、禁止用 jq/grep/python 探测任何 JSON 字段路径——"
    "你要的字段全部在本文件里，探路即违规。"
)

# 兄弟项目/汇总条目的排除词（名字含这些即判定为其他项目或多项目汇总，不参与归一）
_MULTI_ENTRY_MARK = "、"


def _norm_name(s):
    """名称归一：去空白、全角转半角、连字符统一、大写。"""
    if not s:
        return ""
    s = str(s)
    s = s.translate(str.maketrans("（）－—–", "()---"))
    s = re.sub(r"[\s　]+", "", s)
    return s.upper()


def _name_tokens(name):
    """从目标名提取项目代号 token（如 A-18 → A18）。"""
    m = re.findall(r"[A-Za-z]+\s*-\s*\d+|[A-Za-z]*\d+", _norm_name(name))
    return [t.replace("-", "") for t in m if any(c.isdigit() for c in t)]


def _entry_matches(entry_name, target_names, target_tokens):
    """判断 sub_projects 条目是否属于当前子项目。

    规则：归一化后条目名与任一目标名双向包含，或含全部目标 token；
    含「、」（多项目汇总条目，如 A-1、A-2、…A-18 列表）一律排除；
    含其他项目代号而token不完全覆盖的排除（由 token 全匹配兜底）。
    """
    en = _norm_name(entry_name)
    if not en:
        return False
    if _MULTI_ENTRY_MARK in str(entry_name):
        return False
    for tn in target_names:
        n = _norm_name(tn)
        if n and (n in en or en in n):
            return True
    if target_tokens and all(t in en for t in target_tokens):
        return True
    return False


def _num_eq(a, b):
    """数值宽松相等（去千分位逗号后按 float 比较，容差 0.01）。"""
    try:
        fa = float(str(a).replace(",", ""))
        fb = float(str(b).replace(",", ""))
        return abs(fa - fb) < 0.01
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _collect_entry_source(entry, field_keys):
    """宽松收集条目里与字段相关的溯源键值（_source_*/ *_source/ *_page/ _attachment_no 等）。"""
    src = {}
    for k, v in entry.items():
        if v in (None, ""):
            continue
        kl = k.lower()
        if any(fk.lower() in kl for fk in field_keys) and (
            "source" in kl or "page" in kl or "attachment" in kl or "doc_name" in kl
        ):
            src[k] = v
    # 条目级通用溯源也带出（子agent写括注的原料）
    for k in ("_attachment_no", "_doc_name", "_page", "_source"):
        if k in entry and entry[k] not in (None, ""):
            src.setdefault(k, entry[k])
    return src


def _base_var_source(base_vars, key):
    """从 base_vars 的 _sources（字符串摘录）或 citations（结构化对象）段取该字段溯源。"""
    for seg in ("citations", "_sources"):
        bag = base_vars.get(seg)
        if isinstance(bag, dict) and key in bag and bag[key] not in (None, ""):
            return bag[key]
    return None


def slice_ch1(extracted, base_vars):
    fields = {}
    sub_projects = extracted.get("sub_projects") or []
    target_names = [
        base_vars.get("sub_project_name"),
        base_vars.get("asset_name"),
        base_vars.get("project_name"),
    ]
    target_names = [t for t in target_names if t]
    target_tokens = _name_tokens(base_vars.get("sub_project_name") or base_vars.get("asset_name") or "")

    for out_name, (bv_key, sp_keys, note) in CH1_FIELDS.items():
        # 1) base_vars 优先
        val = base_vars.get(bv_key) if bv_key else None
        if val not in (None, ""):
            fields[out_name] = {
                "value": val,
                "status": "OK",
                "source": _base_var_source(base_vars, bv_key),
                "via": "base_vars",
                "note": note,
            }
            continue

        # 2) sub_projects 归一：当前子项目的匹配条目里收集该字段非空候选
        candidates = []
        if sp_keys:
            for entry in sub_projects:
                if not isinstance(entry, dict):
                    continue
                if not _entry_matches(entry.get("name"), target_names, target_tokens):
                    continue
                for sk in sp_keys:
                    v = entry.get(sk)
                    if v in (None, ""):
                        continue
                    if not any(_num_eq(v, c["value"]) for c in candidates):
                        candidates.append({
                            "value": v,
                            "entry_name": entry.get("name"),
                            "source": _collect_entry_source(entry, sp_keys),
                        })
                    break  # 一个条目取第一个命中的候选键即可

        if len(candidates) == 1:
            fields[out_name] = {
                "value": candidates[0]["value"],
                "status": "OK",
                "source": candidates[0]["source"] or None,
                "via": "sub_projects(归一)",
                "note": note,
            }
        elif len(candidates) > 1:
            fields[out_name] = {
                "value": None,
                "status": "CONFLICT",
                "candidates": candidates,
                "note": note + "；多个提取副本值不一致，取 candidates[0] 并在回复中报告冲突",
            }
        else:
            # 区分 MISSING（键不存在）与 EMPTY（键存在但全空）
            key_exists = any(
                isinstance(e, dict) and any(sk in e for sk in sp_keys)
                for e in sub_projects
            ) if sp_keys else False
            fields[out_name] = {
                "value": None,
                "status": "EMPTY" if key_exists else "MISSING",
                "note": note + "；写【注：……待核实】并向上游提取方报缺",
            }

    return {
        "chapter": "一",
        "_usage": _USAGE,
        "issuance_type": base_vars.get("issuance_type"),
        "industry": base_vars.get("industry"),
        "fields": fields,
        # （三）表2 唯一数据源；None/空 → 整表占位并标【注：待业务提供可扩募资产清单】
        "expandable_assets": extracted.get("expandable_assets"),
    }


# ---------------------------------------------------------------------------
# ch2 切片：第二章剩余创作段（#7法律关系/#10业务论述/#11财务分析/#13运营机构）取数口。
# 违法违规/信用/执业资格等模板段已由 gen_phase_fill_plan.py 的 auto_paragraphs 直出，
# 不在本切片职责内。
# ---------------------------------------------------------------------------
_USAGE_CH2 = (
    "本文件是第二章创作段（法律关系/业务论述/财务分析）的唯一取数口，"
    "主体已按 base_vars 权威名选定并合并多副本。"
    "originators/project_companies 里就是本项目的真主体（已滤除无关实体）；"
    "字段缺失（null/缺键）写【注：……待核实】并在完成回复里报缺；"
    "legal_relations.status 为 MISSING 时，（一）只写主体罗列段+图占位，不得编造股权比例。"
    "⛔ 禁止打开 extracted_data.json、禁止用 jq/grep/python 探测字段路径——探路即违规。"
    "另：违法违规/信用/执业资格/中介机构小节已由脚本直出，不归你写，不要重复生成。"
)


def _strip_raw(entity):
    """去掉 _raw_text* 大字段（切片要小），保留数据字段与溯源键（_source/_page 等）"""
    if not isinstance(entity, dict):
        return entity
    return {k: v for k, v in entity.items() if not k.startswith("_raw_text")}


def _pick_by_authority(arr, authority_names):
    """从提取中间态数组里按权威名选主体（副本合并，名单名未命中也造空实体不丢主体）"""
    from gen_phase_fill_plan import merge_entity_copies, is_blank as _blank
    ents = merge_entity_copies(arr or [])
    out = []
    for w in authority_names:
        if not w or str(w).strip() in ("", "-"):
            continue
        w = str(w).strip()
        wn = re.sub(r"[\s　]+", "", w)
        hit = {}
        for e in ents:
            en = re.sub(r"[\s　]+", "", str(e.get("name") or ""))
            if en and (en in wn or wn in en):
                for fk, fv in e.items():
                    if (fk not in hit or _blank(hit.get(fk))) and not _blank(fv):
                        hit[fk] = fv
        hit["name"] = w
        out.append(_strip_raw(hit))
    return out


def _single_entity(extracted_entities, key, bv_name):
    """取单主体（fund_manager/中介等）：dict 直接用，list 按权威名选；都没有则只给名字"""
    v = (extracted_entities or {}).get(key)
    if isinstance(v, list):
        picked = _pick_by_authority(v, [bv_name]) if bv_name else []
        return picked[0] if picked else ({"name": bv_name} if bv_name else None)
    if isinstance(v, dict):
        ent = _strip_raw(v)
        if bv_name and not ent.get("name"):
            ent["name"] = bv_name
        return ent
    return {"name": bv_name} if bv_name else None


def slice_ch2(extracted, base_vars):
    entities = extracted.get("entities") or {}

    originators = _pick_by_authority(
        entities.get("originators"),
        [base_vars.get("originator"), base_vars.get("originator_2")])
    project_companies = _pick_by_authority(
        entities.get("project_companies"),
        [base_vars.get("project_company"), base_vars.get("project_company_2")])

    lr = extracted.get("legal_relations")
    legal_relations = {
        "status": "OK" if isinstance(lr, dict) and any(
            v not in (None, "", [], {}) for k, v in lr.items() if not k.startswith("$")
        ) else "MISSING",
        "data": lr or None,
        "note": "MISSING 时（一）只写主体罗列+图占位，股权比例/实控人写【注：待核实】，严禁编造",
    }

    intermediaries = {}
    for key, bv_key in (("law_firm", "law_firm"), ("accounting_firm", "accounting_firm"),
                        ("valuation_agency", "valuation_agency"), ("tax_advisor", "tax_advisor"),
                        ("financial_advisor", "financial_advisor")):
        ent = _single_entity(entities, key, base_vars.get(bv_key))
        if ent:
            intermediaries[key] = {"name": ent.get("name")}

    return {
        "chapter": "二",
        "_usage": _USAGE_CH2,
        "issuance_type": base_vars.get("issuance_type"),
        "project_name": base_vars.get("project_name"),
        "fund_name": base_vars.get("fund_name"),
        "sub_project_name": base_vars.get("sub_project_name"),
        "legal_relations": legal_relations,
        "originators": originators,
        "project_companies": project_companies,
        "operation_manager": _single_entity(entities, "operation_manager",
                                             base_vars.get("operation_manager")),
        "fund_manager": {"name": base_vars.get("fund_manager")},
        "abs_manager": {"name": base_vars.get("abs_manager")},
        "intermediaries": intermediaries,
    }


def print_ch2_summary(out):
    """ch2 体检摘要：主体选取结果 + 关键块缺口，供主agent归入数据缺口清单"""
    orig = out["originators"]
    pcs = out["project_companies"]
    print(f"[slice ch2] 原始权益人={len(orig)}个({'、'.join(e['name'] for e in orig) or '无'})  "
          f"项目公司={len(pcs)}个({'、'.join(e['name'] for e in pcs) or '无'})")
    gaps = []
    if out["legal_relations"]["status"] != "OK":
        gaps.append("legal_relations（法律关系/股权结构）")
    for e in orig:
        fin = e.get("financials")
        if not isinstance(fin, dict) or not fin:
            gaps.append(f"{e['name']}.financials（财务分析段无数可用）")
        if not e.get("main_business"):
            gaps.append(f"{e['name']}.main_business（业务论述段口径）")
    if gaps:
        print("  缺口（子agent写【注：待核实】+向上游报缺）:", "；".join(gaps))
    else:
        print("  关键块齐全")


def main():
    ap = argparse.ArgumentParser(description="按章切片：产出子agent的扁平取数小文件")
    ap.add_argument("--chapter", required=True, help="章号（目前支持 1/2）")
    ap.add_argument("--extracted", required=True, help="extracted_data.json 路径")
    ap.add_argument("--base-vars", required=True, help="base_vars.json 路径")
    ap.add_argument("--out", required=True, help="输出路径，如 <work_dir>/ch1_data.json")
    args = ap.parse_args()

    with open(args.extracted, encoding="utf-8") as f:
        extracted = json.load(f)
    with open(args.base_vars, encoding="utf-8") as f:
        base_vars = json.load(f)

    ch = str(args.chapter)
    if ch == "1":
        out = slice_ch1(extracted, base_vars)
    elif ch == "2":
        out = slice_ch2(extracted, base_vars)
    else:
        print(f"暂仅支持 --chapter 1/2（收到 {args.chapter}）", file=sys.stderr)
        sys.exit(2)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    if ch == "2":
        print_ch2_summary(out)
        return

    # 打印体检摘要：让主agent一眼看到缺口与冲突，归入数据缺口清单
    n_ok = sum(1 for v in out["fields"].values() if v["status"] == "OK")
    n_conflict = [k for k, v in out["fields"].items() if v["status"] == "CONFLICT"]
    n_missing = [k for k, v in out["fields"].items() if v["status"] in ("MISSING", "EMPTY")]
    print(f"[slice ch1] OK={n_ok}  CONFLICT={len(n_conflict)}  MISSING/EMPTY={len(n_missing)}")
    if n_conflict:
        print("  冲突字段（取 candidates[0]，需复核）:", "、".join(n_conflict))
    if n_missing:
        print("  缺口字段（占位符+向上游报缺）:", "、".join(n_missing))
    ea = out["expandable_assets"]
    ea_n = len((ea or {}).get("assets") or []) if isinstance(ea, dict) else 0
    print(f"  expandable_assets.assets: {ea_n} 条" + ("（空→表2整表占位）" if ea_n == 0 else ""))


if __name__ == "__main__":
    main()
