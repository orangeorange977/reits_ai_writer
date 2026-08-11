#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
table_index 对齐器：把「官方模版的表格序号」映射到「实际填充基底的表格序号」。

为什么必须有它（真实事故）：
  `phase*_blueprints.json` 里的 table_index 是按**官方模版（26 张表）**标注的
  （章节模版切片已废弃，改为 `read_chapter.py` 现读）。但实际填充基底往往是业务给的初稿，初稿可能
  自行增删过表格 —— 实测一份初稿在「摘要表」后插入了「释义表」，于是官方 idx=1~25
  的表在初稿里全部变成 idx=2~26（整体 +1）：
      官方 idx=1 项目概况(19x2)  →  初稿 idx=2 (17x2)
      官方 idx=2 可扩募  (4x9)   →  初稿 idx=3
  若直接拿切片的 table_index 去填初稿，**全篇每张表都会错填到相邻的另一张表**。

匹配方式：
  以 (列数, 首格文字前14字) 作指纹按顺序贪心匹配 —— 列数是表格最稳定的结构特征，
  首格文字是官方模版的固定表头。行数**不参与匹配**，因为初稿常有增删行
  （如项目概况 19 行被业务删成 17 行、中介机构表从 9 行扩到 35 行）。

用法:
  python align_table_index.py --template "<填充基底.docx>" --out "<work_dir>/table_index_map.json"
  # 官方模版路径默认取 skill 内置 assets/，也可显式指定：
  python align_table_index.py --template "<初稿.docx>" --official "<官方模版.docx>" --out "<out.json>"

输出 table_index_map.json:
  {
    "offset_summary": "官方 idx 1~25 → 实际 +1",
    "map": {"0": 0, "1": 2, "2": 3, ...},       # 官方idx(字符串) → 实际idx
    "unmatched": [ ... ],                        # 官方有、基底找不到的表
    "extra_in_template": [ ... ],                # 基底多出来的表（官方没有）
    "tables": [ {idx, rows, cols, first_cell} ]  # 基底所有表的实测结构，便于人工核对
  }
"""

import argparse
import json
import os
import sys

from docx import Document

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FINGERPRINT_LEN = 14  # 首格文字取前 N 字做指纹


def table_sigs(path):
    d = Document(path)
    out = []
    for i, t in enumerate(d.tables):
        rows = len(t.rows)
        cols = len(t.columns)
        first = ""
        if rows and len(t.rows[0].cells):
            first = t.rows[0].cells[0].text.strip().replace("\n", " ")
        out.append({"idx": i, "rows": rows, "cols": cols, "first_cell": first})
    return out


def align(official, actual):
    """顺序贪心匹配：列数相同 + 首格前N字相同。行数不参与（初稿常增删行）。"""
    mapping = {}
    unmatched = []
    used = set()
    for o in official:
        hit = None
        for a in actual:
            if a["idx"] in used:
                continue
            if a["cols"] != o["cols"]:
                continue
            if a["first_cell"][:FINGERPRINT_LEN] != o["first_cell"][:FINGERPRINT_LEN]:
                continue
            hit = a["idx"]
            used.add(hit)
            break
        if hit is None:
            unmatched.append({"official_idx": o["idx"], "cols": o["cols"],
                              "first_cell": o["first_cell"][:30],
                              "reason": "基底中未找到列数与首行文字都吻合的表"})
        else:
            mapping[str(o["idx"])] = hit
    extra = [a for a in actual if a["idx"] not in used]
    return mapping, unmatched, extra


def summarize(mapping):
    """把映射压成人话，如「官方 idx 1~25 → 实际 +1」"""
    if not mapping:
        return "无任何匹配"
    deltas = {}
    for k, v in mapping.items():
        d = v - int(k)
        deltas.setdefault(d, []).append(int(k))
    parts = []
    for d in sorted(deltas):
        ks = sorted(deltas[d])
        rng = "%d~%d" % (ks[0], ks[-1]) if len(ks) > 1 else str(ks[0])
        parts.append("官方 idx %s → 实际 %s%d" % (rng, "+" if d >= 0 else "", d))
    return "；".join(parts)


def main():
    ap = argparse.ArgumentParser(description="对齐官方模版与实际填充基底的 table_index")
    ap.add_argument("--template", required=True, help="实际填充基底 docx（初稿或官方模版）")
    ap.add_argument("--official", default=None,
                    help="官方模版 docx；默认取 <skill_dir>/assets/ 下的内置模版")
    ap.add_argument("--out", required=True, help="输出 table_index_map.json")
    args = ap.parse_args()

    official_path = args.official
    if not official_path:
        assets = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        cand = [os.path.join(assets, f) for f in os.listdir(assets)
                if f.endswith(".docx") and not f.startswith("~$")] if os.path.isdir(assets) else []
        if not cand:
            print("ERROR: 未找到内置官方模版，请用 --official 指定", file=sys.stderr)
            sys.exit(1)
        official_path = cand[0]

    off = table_sigs(official_path)
    act = table_sigs(args.template)
    mapping, unmatched, extra = align(off, act)
    summary = summarize(mapping)

    result = {
        "official_template": os.path.basename(official_path),
        "actual_template": os.path.basename(args.template),
        "official_table_count": len(off),
        "actual_table_count": len(act),
        "offset_summary": summary,
        "$usage": "凡使用来自 chapter_examples 切片 / phase*_blueprints.json 的 table_index，"
                  "必须先经本 map 换算成实际索引；map 里没有的官方 idx 说明基底缺这张表，"
                  "需改用 insert_tables 新建或跳过。",
        "map": mapping,
        "unmatched": unmatched,
        "extra_in_template": extra,
        "tables": act,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("官方模版 %d 张表 | 基底 %d 张表" % (len(off), len(act)))
    print("偏移情况: %s" % summary)
    if unmatched:
        print("⚠️ 官方有但基底找不到的表 %d 张:" % len(unmatched))
        for u in unmatched:
            print("    官方idx=%-2d %d列 %s" % (u["official_idx"], u["cols"], u["first_cell"]))
    if extra:
        print("ℹ️ 基底多出的表 %d 张（官方模版没有，通常是业务自行新增）:" % len(extra))
        for e in extra:
            print("    实际idx=%-2d %d行x%d列 %s" % (e["idx"], e["rows"], e["cols"], e["first_cell"][:30]))
    print("→ %s" % args.out)


if __name__ == "__main__":
    main()
