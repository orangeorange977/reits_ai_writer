#!/usr/bin/env python3
"""Shared hard interlock for all generation scripts."""

import json
import os


class HandoffGateError(RuntimeError):
    pass


def _find_work_dir(hints):
    for hint in hints:
        if not hint:
            continue
        cur = os.path.dirname(os.path.abspath(hint)) if os.path.splitext(hint)[1] else os.path.abspath(hint)
        for _ in range(4):
            if os.path.exists(os.path.join(cur, "handoff_validation.json")):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None


def assert_handoff_ready(*hints):
    wd = _find_work_dir(hints)
    if not wd:
        raise HandoffGateError(
            "找不到handoff_validation.json。先运行validate_handoff.py --work-dir <work_dir> --strict")
    report_path = os.path.join(wd, "handoff_validation.json")
    try:
        with open(report_path, "r", encoding="utf-8-sig") as f:
            report = json.load(f)
    except Exception as exc:
        raise HandoffGateError("交接报告不可读：%s" % exc)
    if report.get("verdict") != "READY" or report.get("strict") is not True:
        raise HandoffGateError("交接门禁不是严格READY：verdict=%s strict=%s" %
                               (report.get("verdict"), report.get("strict")))
    report_mtime = os.path.getmtime(report_path)
    stale = []
    for name in ("extracted_data.json", "proofs_index.json", "extraction_coverage.json",
                 "specialized_extraction_validation.json"):
        path = os.path.join(wd, name)
        if not os.path.exists(path):
            stale.append(name + "(缺失)")
        elif os.path.getmtime(path) > report_mtime + 0.001:
            stale.append(name + "(晚于报告)")
    if stale:
        raise HandoffGateError("交接报告已过期：%s；请重新运行严格校验" % "、".join(stale))
    return wd, report
