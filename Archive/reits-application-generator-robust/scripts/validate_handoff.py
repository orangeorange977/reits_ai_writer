#!/usr/bin/env python3
"""Validate the extractor→generator handoff. Standard-library only.

Exit codes: 0 READY, 1 BLOCKED (structural/accuracy failure), 2 READY_WITH_GAPS.
The JSON report is authoritative; console output is only a summary.
"""

import argparse
import datetime as dt
import json
import math
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_CONTRACT = os.path.join(SKILL_DIR, "templates", "handoff_contract.json")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip() in {"null", "N/A", "待填写", "【待填写】"}
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def dig(data, path):
    cur = data
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("["):
            idx = int(token[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or token not in cur:
                return None
            cur = cur[token]
    return cur


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace("，", "")
    text = text.replace("％", "%")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def merge_prov(inherited, node):
    out = dict(inherited or {})
    if isinstance(node, dict):
        for key in ("_source", "_attachment_no", "_doc_name", "_raw_text", "_page", "_section"):
            if key in node and not empty(node.get(key)):
                out[key] = node[key]
    return out


def provenance_stats(node, inherited=None, stats=None, path="$", critical=False):
    stats = stats or {"objects": 0, "complete": 0, "critical_objects": 0,
                      "critical_complete": 0, "missing_examples": []}
    prov = merge_prov(inherited, node)
    if isinstance(node, dict):
        scalars = [v for k, v in node.items() if not k.startswith("_") and not k.startswith("$")
                   and not isinstance(v, (dict, list)) and not empty(v)]
        if scalars:
            stats["objects"] += 1
            complete = all(not empty(prov.get(k)) for k in
                           ("_source", "_attachment_no", "_doc_name", "_raw_text"))
            complete = complete and (not empty(prov.get("_page")) or not empty(prov.get("_section")))
            if complete:
                stats["complete"] += 1
            elif len(stats["missing_examples"]) < 20:
                stats["missing_examples"].append(path)
            if critical:
                stats["critical_objects"] += 1
                if complete:
                    stats["critical_complete"] += 1
        child_inherited = {} if path == "$" else prov
        for key, value in node.items():
            if key.startswith("$"):
                continue
            child_critical = critical or path.startswith("$.operating_performance")
            provenance_stats(value, child_inherited, stats, "%s.%s" % (path, key), child_critical)
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            provenance_stats(value, prov, stats, "%s[%d]" % (path, idx), critical)
    return stats


def check_row_model(path, rows, expected_values, blockers, gaps):
    if empty(rows):
        return
    if not isinstance(rows, list):
        blockers.append({"code": "TABLE_NOT_LIST", "path": path,
                         "message": "表格数据源必须为数组"})
        return
    labels = []
    for idx, row in enumerate(rows):
        row_path = "%s[%d]" % (path, idx)
        if not isinstance(row, dict):
            blockers.append({"code": "ROW_NOT_OBJECT", "path": row_path,
                             "message": "表格行必须为对象"})
            continue
        label = str(row.get("label", "")).strip()
        if not label and path != "operating_performance.revenue_structure_rows":
            gaps.append({"code": "ROW_LABEL_EMPTY", "path": row_path,
                         "message": "表格行标签为空"})
        if label:
            labels.append(label)
        present = [k for k in expected_values if not empty(row.get(k))]
        if not present and label not in {"经营指标", "财务指标（万元）"}:
            gaps.append({"code": "ROW_VALUES_EMPTY", "path": row_path,
                         "message": "该行所有期间值均为空"})
    duplicates = sorted({x for x in labels if labels.count(x) > 1})
    if duplicates:
        blockers.append({"code": "DUPLICATE_ROW_LABEL", "path": path,
                         "message": "重复行标签：%s" % "、".join(duplicates[:10])})


def main():
    ap = argparse.ArgumentParser(description="REITs提取→生成交接硬校验")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--contract", default=DEFAULT_CONTRACT)
    ap.add_argument("--output")
    ap.add_argument("--strict", action="store_true", help="关键内容缺口按阻断处理")
    args = ap.parse_args()

    wd = os.path.abspath(args.work_dir)
    out_path = args.output or os.path.join(wd, "handoff_validation.json")
    blockers, gaps, warnings = [], [], []
    metrics = {}

    try:
        contract = load_json(args.contract)
    except Exception as exc:
        print("ERROR: 无法读取交接契约：%s" % exc, file=sys.stderr)
        return 1

    docs = {}
    for name in contract["required_artifacts"]:
        path = os.path.join(wd, name)
        if not os.path.exists(path):
            blockers.append({"code": "ARTIFACT_MISSING", "path": name, "message": "必需产物不存在"})
            continue
        try:
            docs[name] = load_json(path)
        except Exception as exc:
            blockers.append({"code": "INVALID_JSON", "path": name, "message": str(exc)})

    data = docs.get("extracted_data.json")
    proofs = docs.get("proofs_index.json")
    coverage = docs.get("extraction_coverage.json")
    specialized = docs.get("specialized_extraction_validation.json")

    if isinstance(proofs, dict):
        material_index = proofs.get("material_index") or []
        metrics["indexed_files"] = len(material_index)
        if not material_index:
            blockers.append({"code": "EMPTY_PROOFS_INDEX", "path": "proofs_index.material_index",
                             "message": "材料索引为空"})

    if isinstance(coverage, dict):
        metrics["file_coverage_pct"] = coverage.get("coverage_pct")
        if coverage.get("pass") is not True:
            blockers.append({"code": "COVERAGE_GATE_FAILED", "path": "extraction_coverage.pass",
                             "message": "文件/核心页覆盖门禁未通过"})
        threshold = number(coverage.get("threshold_pct"))
        if threshold is not None and threshold < contract["coverage"]["minimum_overall_pct"]:
            blockers.append({"code": "THRESHOLD_LOWERED", "path": "extraction_coverage.threshold_pct",
                             "message": "覆盖率阈值低于契约下限"})
        for key in ("critical_unread", "core_page_unread", "core_render_gaps"):
            if coverage.get(key):
                blockers.append({"code": key.upper(), "path": "extraction_coverage.%s" % key,
                                 "message": "存在未完成核心材料：%d项" % len(coverage[key])})

    if isinstance(specialized, dict):
        metrics["specialized_extraction_verdict"] = specialized.get("verdict")
        if specialized.get("verdict") != "READY":
            blockers.append({"code": "SPECIALIZED_GATE_FAILED",
                             "path": "specialized_extraction_validation.verdict",
                             "message": "法律意见书/评估报告/第二章专项提取未通过"})
        try:
            if os.path.getmtime(os.path.join(wd, "specialized_extraction_validation.json")) < \
                    os.path.getmtime(os.path.join(wd, "extracted_data.json")):
                blockers.append({"code": "SPECIALIZED_REPORT_STALE",
                                 "path": "specialized_extraction_validation.json",
                                 "message": "专项提取报告早于extracted_data，必须重跑"})
        except OSError:
            pass

    if isinstance(data, dict):
        meta = data.get("_contract") or data.get("$contract") or {}
        if meta.get("name") != "reits_handoff" or str(meta.get("version")) != "2.0":
            blockers.append({"code": "CONTRACT_VERSION_MISSING", "path": "_contract",
                             "message": "extracted_data必须声明reits_handoff/2.0"})
        if "operation" in data:
            blockers.append({"code": "LEGACY_MODEL_PRESENT", "path": "operation",
                             "message": "检测到旧版operation；新任务必须统一使用operating_performance"})
        for key in contract["required_top_level"]:
            if key not in data:
                blockers.append({"code": "TOP_LEVEL_MISSING", "path": key,
                                 "message": "缺少契约顶层字段"})
        required_status = []
        for spec in contract.get("required_fields", []):
            ok = not empty(dig(data, spec["path"]))
            required_status.append({"path": spec["path"], "used_by": spec.get("used_by"),
                                    "ready": ok})
            if not ok:
                issue = {"code": "REQUIRED_FIELD_EMPTY", "path": spec["path"],
                         "message": "关键字段为空，影响%s" % spec.get("used_by", "申报材料")}
                (blockers if args.strict else gaps).append(issue)
        metrics["required_fields"] = required_status
        metrics["required_fields_ready"] = sum(1 for x in required_status if x["ready"])
        metrics["required_fields_total"] = len(required_status)
        structure_status = []
        for spec in contract.get("required_structures", []):
            condition = spec.get("when") or {}
            if condition and dig(data, condition.get("path", "")) != condition.get("equals"):
                continue
            ok = not empty(dig(data, spec["path"]))
            structure_status.append({"path": spec["path"], "used_by": spec.get("used_by"),
                                     "ready": ok})
            if not ok:
                issue = {"code": "REQUIRED_STRUCTURE_EMPTY", "path": spec["path"],
                         "message": "结构化数据为空，影响%s" % spec.get("used_by", "申报材料")}
                (blockers if args.strict else gaps).append(issue)
        metrics["required_structures"] = structure_status
        metrics["required_structures_ready"] = sum(1 for x in structure_status if x["ready"])
        metrics["required_structures_total"] = len(structure_status)

        for chapter in ("chapter2", "chapter4", "chapter5"):
            table_status = []
            for spec in contract.get("%s_table_sources" % chapter, []):
                value = dig(data, spec["path"])
                ok = not empty(value)
                table_status.append({"table": spec["table"], "path": spec["path"],
                                     "required": spec["required"], "ready": ok})
                if not ok:
                    issue = {"code": "TABLE_SOURCE_EMPTY", "path": spec["path"],
                             "message": "表%s数据源为空" % spec["table"]}
                    if spec["required"] and args.strict:
                        blockers.append(issue)
                    elif spec["required"]:
                        gaps.append(issue)
                    else:
                        issue["code"] = "OPTIONAL_TABLE_EMPTY"
                        issue["message"] += "（可不涉及，但应在正文说明判断依据）"
                        warnings.append(issue)
            metrics["%s_tables" % chapter] = table_status
            metrics["%s_ready" % chapter] = sum(1 for x in table_status if x["ready"])
            metrics["%s_total" % chapter] = len(table_status)

        check_row_model("operating_performance.annual_rows",
                        dig(data, "operating_performance.annual_rows"),
                        ["v1", "v2", "v3", "v4"], blockers, gaps)
        check_row_model("operating_performance.forecast_rows",
                        dig(data, "operating_performance.forecast_rows"),
                        ["v1", "v2", "v3", "v4", "v5", "v6", "v7"], blockers, gaps)
        check_row_model("operating_performance.revenue_structure_rows",
                        dig(data, "operating_performance.revenue_structure_rows"),
                        ["v1", "v2", "v3", "v4"], blockers, gaps)

        ratio_rows = dig(data, "operating_performance.revenue_structure_rows") or []
        if isinstance(ratio_rows, list):
            ratio_rows = [r for r in ratio_rows if isinstance(r, dict) and r.get("kind") == "占比"]
            for col in ("v1", "v2", "v3", "v4"):
                vals = [number(r.get(col)) for r in ratio_rows]
                vals = [v for v in vals if v is not None]
                if vals and abs(sum(vals) - 100.0) > 0.5:
                    blockers.append({"code": "RATIO_SUM_MISMATCH", "path":
                                     "operating_performance.revenue_structure_rows.%s" % col,
                                     "message": "收入占比合计%.2f%%，应为100%%" % sum(vals)})

        test = dig(data, "operating_performance.seventy_percent_test") or {}
        if isinstance(test, dict):
            past = number(test.get("history_3y_avg"))
            future = number(test.get("forecast_3y_avg"))
            stated = number(test.get("ratio_pct"))
            if past is not None and future not in (None, 0) and stated is not None:
                expected = past / future * 100.0
                if abs(expected - stated) > 0.2:
                    blockers.append({"code": "SEVENTY_PERCENT_MISMATCH",
                                     "path": "operating_performance.seventy_percent_test.ratio_pct",
                                     "message": "声明值%.2f%%与重算值%.2f%%不一致" % (stated, expected)})

        prov = provenance_stats(data)
        prov["overall_pct"] = round(100.0 * prov["complete"] / max(1, prov["objects"]), 2)
        prov["critical_pct"] = round(100.0 * prov["critical_complete"] /
                                     max(1, prov["critical_objects"]), 2)
        metrics["provenance"] = prov
        pcfg = contract["provenance"]
        if prov["overall_pct"] < pcfg["minimum_overall_pct"]:
            issue = {"code": "PROVENANCE_LOW", "path": "$",
                     "message": "完整溯源率%.2f%%，低于%d%%" %
                                (prov["overall_pct"], pcfg["minimum_overall_pct"])}
            (blockers if args.strict else gaps).append(issue)
        if prov["critical_objects"] and prov["critical_pct"] < pcfg["critical_rows_pct"]:
            issue = {"code": "CRITICAL_PROVENANCE_LOW", "path": "operating_performance",
                     "message": "关键表格对象完整溯源率%.2f%%，应为100%%" % prov["critical_pct"]}
            (blockers if args.strict else gaps).append(issue)

        quality = data.get("_quality") or {}
        unresolved = quality.get("issues") or []
        conflicts = quality.get("conflicts") or []
        open_issues = [x for x in unresolved if isinstance(x, dict)
                       and str(x.get("status", "open")).lower() not in {"closed", "resolved"}]
        blocking_issues = [x for x in open_issues
                           if str(x.get("severity", "blocking")).lower() != "warning"]
        open_conflicts = [x for x in conflicts if isinstance(x, dict)
                          and str(x.get("status", "open")).lower() not in {"closed", "resolved"}]
        metrics["unresolved_issues"] = len(open_issues)
        metrics["conflicts"] = len(open_conflicts)
        if blocking_issues:
            issue = {"code": "UNRESOLVED_BLOCKING_ISSUES", "path": "_quality.issues",
                     "message": "存在%d项未关闭的阻断问题" % len(blocking_issues)}
            (blockers if args.strict else gaps).append(issue)
        if open_conflicts:
            issue = {"code": "UNRESOLVED_CONFLICTS", "path": "_quality.conflicts",
                     "message": "存在%d项未关闭的来源冲突" % len(open_conflicts)}
            (blockers if args.strict else gaps).append(issue)

    verdict = "BLOCKED" if blockers else ("READY_WITH_GAPS" if gaps else "READY")
    report = {
        "contract": contract["contract"],
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "work_dir": wd,
        "strict": args.strict,
        "verdict": verdict,
        "metrics": metrics,
        "blockers": blockers,
        "gaps": gaps,
        "warnings": warnings,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("%s: blockers=%d gaps=%d warnings=%d" %
          (verdict, len(blockers), len(gaps), len(warnings)))
    print("报告: %s" % out_path)
    if blockers:
        for item in blockers[:20]:
            print("  [BLOCK] %s — %s" % (item["path"], item["message"]))
        return 1
    if gaps:
        for item in gaps[:20]:
            print("  [GAP] %s — %s" % (item["path"], item["message"]))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
