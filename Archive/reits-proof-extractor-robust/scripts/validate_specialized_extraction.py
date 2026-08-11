#!/usr/bin/env python3
"""校验三条专项提取通道：文件覆盖、第二章表格和第五章表15~20数据源。"""

import argparse
import datetime as dt
import json
import math
import os
import re


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip() or "待填写" in v
    return isinstance(v, (list, dict)) and not v


def number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def dig(data, path):
    cur = data
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if token.startswith("["):
            i = int(token[1:-1])
            if not isinstance(cur, list) or i >= len(cur):
                return None
            cur = cur[i]
        else:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(token)
    return cur


def all_paths(proofs):
    out = []
    for values in (proofs.get("material_index") or {}).values():
        if isinstance(values, list):
            out.extend(str(x) for x in values if x)
    return sorted(set(out))


def candidates(paths, kind):
    if kind == "legal":
        return [p for p in paths if "法律意见书" in p]
    if kind == "appraisal":
        return [p for p in paths
                if any(k in p for k in ("房地产估价报告", "资产评估报告", "估值报告"))
                and not re.search(r"节能.*评估报告|节能分析专项报告", p)]
    return [p for p in paths if re.search(r"审计报告|财务报表|年度报告|年报|营业执照|公司章程|企业信用报告", p)]


def doc_covered(path, coverage):
    core = coverage.get("core_pages") or {}
    for key, value in core.items():
        if key == path or os.path.basename(key) == os.path.basename(path):
            return not value.get("unread_count") and not value.get("render_gap_pages")
    # 非PDF专项文件不一定出现在 core_pages，回退文件级覆盖。
    for item in (coverage.get("items") or {}).values():
        for f in item.get("files") or []:
            if f.get("file") == path or os.path.basename(str(f.get("file"))) == os.path.basename(path):
                return bool(f.get("read"))
    return False


def pct_change(a, b):
    if a in (None, 0) or b is None:
        return None
    return (b - a) / abs(a) * 100.0


def financial_triggers(financials):
    annual = []
    for key, row in (financials or {}).items():
        if not isinstance(row, dict):
            continue
        m = re.fullmatch(r"(20\d{2})", str(key))
        if m:
            annual.append((int(m.group(1)), str(key), row))
    annual.sort()
    metrics = ("total_assets", "total_liabilities", "revenue", "net_profit", "operating_cash_flow")
    out = []
    for (_, k1, r1), (_, k2, r2) in zip(annual, annual[1:]):
        for metric in metrics:
            a, b = number(r1.get(metric)), number(r2.get(metric))
            chg = pct_change(a, b)
            sign_flip = a is not None and b is not None and ((a < 0 <= b) or (a >= 0 > b))
            negative = metric in ("net_profit", "operating_cash_flow") and b is not None and b < 0
            if (chg is not None and abs(chg) >= 20) or sign_flip or negative:
                out.append((metric, k1, k2))
        a1, l1 = number(r1.get("total_assets")), number(r1.get("total_liabilities"))
        a2, l2 = number(r2.get("total_assets")), number(r2.get("total_liabilities"))
        if a1 not in (None, 0) and a2 not in (None, 0) and l1 is not None and l2 is not None:
            if abs(l2 / a2 * 100 - l1 / a1 * 100) >= 5:
                out.append(("debt_ratio", k1, k2))
    return out


def main():
    ap = argparse.ArgumentParser(description="REITs专项提取校验")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--output")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    wd = os.path.abspath(args.work_dir)
    out_path = args.output or os.path.join(wd, "specialized_extraction_validation.json")
    blockers, gaps, warnings = [], [], []
    try:
        proofs = load_json(os.path.join(wd, "proofs_index.json"))
        data = load_json(os.path.join(wd, "extracted_data.json"))
        coverage = load_json(os.path.join(wd, "extraction_coverage.json"))
    except Exception as exc:
        print("ERROR: %s" % exc)
        return 1

    paths = all_paths(proofs)
    docs = {k: candidates(paths, k) for k in ("legal", "appraisal", "chapter2")}
    for kind, values in docs.items():
        if not values:
            warnings.append({"code": "NO_CANDIDATE", "path": kind,
                             "message": "未按文件名识别到候选材料，需人工核对命名"})
        for path in values:
            if not doc_covered(path, coverage):
                blockers.append({"code": "SPECIALIZED_DOCUMENT_UNREAD", "path": path,
                                 "message": "%s通道文件未证明全量完成" % kind})

    required_paths = {
        "legal": [
            "compliance.legal_opinions", "compliance.investment_procedures",
            "compliance.industry_procedures", "compliance.land_use",
            "compliance.building_ownership", "compliance.land_procedure_summary",
            "compliance.transferability.summary",
        ],
        "appraisal": [
            "evaluation.total_value", "evaluation.base_date", "evaluation.method",
            "operating_performance.forecast_rows",
            "operating_performance.valuation_params.occupancy_rows",
            "operating_performance.valuation_params.opex_param_rows",
            "operating_performance.valuation_params.capex_equipment_rows",
            "operating_performance.valuation_params.capex_forecast_rows",
        ],
    }
    for kind, specs in required_paths.items():
        if not docs[kind]:
            continue
        for path in specs:
            if empty(dig(data, path)):
                issue = {"code": "SPECIALIZED_FIELD_EMPTY", "path": path,
                         "message": "%s通道必交字段为空" % kind}
                (blockers if args.strict else gaps).append(issue)

    project_companies = dig(data, "entities.project_companies") or []
    originators = dig(data, "entities.originators") or []
    pc_fields = ("name", "legal_rep", "established_date", "registered_capital",
                 "registered_address", "business_scope")
    org_fields = ("name", "legal_rep", "actual_controller", "established_date",
                  "registered_capital", "registered_address", "main_business",
                  "reits_issued", "reits_withdrawn_12m")
    legal_fields = ("legal_relations.project_company_equity",
                    "legal_relations.controlling_shareholder",
                    "legal_relations.actual_controller",
                    "legal_relations.has_foreign_investment")
    for path in legal_fields:
        if empty(dig(data, path)):
            (blockers if args.strict else gaps).append({
                "code": "CH2_LEGAL_RELATION_EMPTY", "path": path,
                "message": "第二章（一）法律关系必备字段为空"})
    if not project_companies:
        blockers.append({"code": "CH2_PROJECT_COMPANY_EMPTY", "path": "entities.project_companies",
                         "message": "表3无项目公司行"})
    if not originators:
        blockers.append({"code": "CH2_ORIGINATOR_EMPTY", "path": "entities.originators",
                         "message": "表4/5无发起人行"})
    for i, entity in enumerate(project_companies):
        for field in pc_fields:
            if empty(entity.get(field)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_BASIC_FIELD_EMPTY", "path": "entities.project_companies[%d].%s" % (i, field),
                    "message": "表3应填格为空"})
        for field in ("violation_query_date", "violation_official_websites",
                      "credit_query_date", "credit_websites", "credit_attachment_no",
                      "commitment.doc_name", "commitment.issue_date",
                      "commitment.attachment_no"):
            path = "entities.project_companies[%d].compliance_credit.%s" % (i, field)
            if empty(dig(data, path)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_PROJECT_COMPANY_COMPLIANCE_EMPTY", "path": path,
                    "message": "第二章项目公司违法违规/信用段字段为空"})
    fin_metrics = ("total_assets", "total_liabilities", "revenue", "net_profit", "operating_cash_flow")
    for i, entity in enumerate(originators):
        for field in org_fields:
            if empty(entity.get(field)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_BASIC_FIELD_EMPTY", "path": "entities.originators[%d].%s" % (i, field),
                    "message": "表4应填格为空"})
        for field in ("credit_query_date", "credit_websites", "credit_attachment_no",
                      "commitment.doc_name", "commitment.issue_date",
                      "commitment.attachment_no"):
            path = "entities.originators[%d].compliance_credit.%s" % (i, field)
            if empty(dig(data, path)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_ORIGINATOR_COMPLIANCE_EMPTY", "path": path,
                    "message": "第二章发起人违法违规/信用段字段为空"})
        fins = entity.get("financials") or {}
        annual = [k for k in fins if re.fullmatch(r"20\d{2}", str(k))]
        interim = [k for k in fins if k not in annual]
        if len(annual) < 3 or not interim:
            (blockers if args.strict else gaps).append({
                "code": "CH2_PERIODS_INCOMPLETE", "path": "entities.originators[%d].financials" % i,
                "message": "表5需最近3个会计年度及一期，当前年度%d/一期%d" % (len(annual), len(interim))})
        for period, row in fins.items():
            if not isinstance(row, dict):
                continue
            for metric in fin_metrics:
                if empty(row.get(metric)):
                    (blockers if args.strict else gaps).append({
                        "code": "CH2_FINANCIAL_CELL_EMPTY",
                        "path": "entities.originators[%d].financials.%s.%s" % (i, period, metric),
                        "message": "表5财务单元格为空"})
        reasons = entity.get("financial_analysis", {}).get("change_reasons", [])
        covered = {(str(x.get("metric")), str(x.get("from_period")), str(x.get("to_period")))
                   for x in reasons if isinstance(x, dict)
                   and x.get("status") in ("explained", "unexplained")}
        for trigger in financial_triggers(fins):
            if trigger not in covered:
                (blockers if args.strict else gaps).append({
                    "code": "CH2_CHANGE_REASON_UNREGISTERED",
                    "path": "entities.originators[%d].financial_analysis.change_reasons" % i,
                    "message": "变动项%s %s→%s未标记explained/unexplained" % trigger})

    for field in ("name", "legal_rep", "actual_controller", "established_date",
                  "registered_capital", "registered_address", "main_business",
                  "reits_operated"):
        path = "entities.operation_manager.%s" % field
        if empty(dig(data, path)):
            (blockers if args.strict else gaps).append({
                "code": "CH2_OPERATION_MANAGER_EMPTY", "path": path,
                "message": "表6/运营管理机构字段为空；与发起人相同时也须按交叉复用规则写入"})

    for path in ("entities.fund_manager.name", "entities.abs_manager.name"):
        if empty(dig(data, path)):
            (blockers if args.strict else gaps).append({
                "code": "CH2_MANAGER_EMPTY", "path": path,
                "message": "表9管理人名称为空"})

    for role in ("financial_advisor", "law_firm", "accounting_firm", "valuation_agency"):
        base = "entities.%s" % role
        for field in ("name", "contact_address"):
            path = "%s.%s" % (base, field)
            if empty(dig(data, path)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_INTERMEDIARY_BASIC_EMPTY", "path": path,
                    "message": "表10中介机构名称/地址为空"})
        for field in ("credit_query_date", "credit_websites", "credit_attachment_no",
                      "qualification_commitment.doc_name",
                      "qualification_commitment.issue_date",
                      "qualification_commitment.attachment_no"):
            path = "%s.intermediary_compliance.%s" % (base, field)
            if empty(dig(data, path)):
                (blockers if args.strict else gaps).append({
                    "code": "CH2_INTERMEDIARY_COMPLIANCE_EMPTY", "path": path,
                    "message": "第二章中介机构信用/执业资格字段为空"})

    verdict = "BLOCKED" if blockers else ("READY_WITH_GAPS" if gaps else "READY")
    report = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
              "strict": bool(args.strict), "verdict": verdict,
              "candidate_documents": docs,
              "blockers": blockers, "gaps": gaps, "warnings": warnings}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("%s: blockers=%d gaps=%d warnings=%d" %
          (verdict, len(blockers), len(gaps), len(warnings)))
    print("报告: %s" % out_path)
    return 1 if blockers else (2 if gaps else 0)


if __name__ == "__main__":
    raise SystemExit(main())
