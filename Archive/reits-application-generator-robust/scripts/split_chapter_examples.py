#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
章节切分脚本（构建期一次性工具）：把标准答案docx与官方模版docx按一级章节切分，
生成子agent章节撰写用的按章范例素材（模版切片默认不再产出，改用 read_chapter.py 现读）。

产出（写入 <skill_dir>/templates/chapter_examples/）:
  ch{N}_example.md   —— 标准答案该章全文（实体名脱敏，头部带"严禁照抄数据"警告）
  manifest.json      —— 章节清单（标题/段数/表数/文件名）

脱敏：公司名/项目名/人名等映射为【原始权益人】【项目公司1】等通用占位符（见 SANITIZE_MAP）。

用法:
  python split_chapter_examples.py --answer <标准答案.docx> --template <官方模版.docx> --out-dir <templates/chapter_examples>
"""

import argparse
import json
import os
import re
import sys

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHAPTER_PAT = re.compile(r"^[一二三四五六七八九十]+、")

# 脱敏映射：标准答案中的真实实体 → 通用占位符（按长度降序替换，防子串错替）。
# 2026-08-05 换源：范例源从润泽A-18换成《示例材料3》万国数据（昆山国金数据中心）——
# 用户实际跑的项目就是润泽，范例与运行项目同源时分不清「提数正确」还是「抄了范例」。
SANITIZE_MAP = [
    ("国金数据科技发展（昆山）有限公司", "【项目公司全称】"),
    ("北京国友大正资产评估有限公司", "【评估机构全称】"),
    ("普华永道咨询（深圳）有限公司", "【税务顾问全称】"),
    ("华泰联合证券有限责任公司", "【财务顾问全称】"),
    ("南方基金管理股份有限公司", "【基金管理人全称】"),
    ("毕马威华振会计师事务所", "【会计师事务所全称】"),
    ("昆山国诚数据科技有限公司", "【关联公司1全称】"),
    ("昆山国胜数据科技有限公司", "【关联公司2全称】"),
    ("上海信万企业管理有限公司", "【关联公司3全称】"),
    ("南方资本管理有限公司", "【ABS管理人全称】"),
    ("万国数据控股有限公司", "【原始权益人全称】"),
    ("万国数据服务有限公司", "【运营管理机构全称】"),
    ("招商银行股份有限公司", "【基金托管人全称】"),
    ("北京市金杜律师事务所", "【律师事务所全称】"),
    ("GDS Holdings Limited", "【原始权益人英文名】"),
    ("国金数据云计算数据中心", "【标的资产名称】"),
    ("国金数据中心", "【标的资产简称】"),
    ("国金数据", "【标的资产简称】"),
    # 组合简称必须排在单字「昆山」之前，否则先被拆成「【项目所在地】国X」残片
    ("昆山国金", "【项目公司简称】"),
    ("昆山国诚", "【关联公司1简称】"),
    ("昆山国胜", "【关联公司2简称】"),
    ("万国数据", "【原始权益人简称】"),
    ("万国", "【原始权益人简称】"),
    ("FEP HK", "【境外股东】"),
    ("FEP", "【境外股东】"),
    ("国友大正", "【评估机构简称】"),
    ("普华永道", "【税务顾问简称】"),
    ("华泰联合", "【财务顾问简称】"),
    ("南方基金", "【基金管理人简称】"),
    ("南方资本", "【ABS管理人简称】"),
    ("招商银行", "【基金托管人简称】"),
    ("毕马威", "【会计师事务所简称】"),
    ("金杜", "【律师事务所简称】"),
    ("GDS", "【原始权益人英文简称】"),
    ("昆山市", "【项目所在地】"),
    ("昆山", "【项目所在地】"),
    ("花桥镇", "【项目所在镇】"),
    ("花桥", "【项目所在镇】"),
    ("远创路558号", "【门牌地址】"),
    ("远创路", "【门牌地址】"),
    # 万国自有可扩募资产恰在廊坊（与用户实际项目润泽同城），必须遮掉防混淆
    ("廊坊市", "【其他城市】"),
    ("廊坊", "【其他城市】"),
    ("长三角", "【所在区域】"),
]

# 人工维护章：第三章（REITs设立方案）、第七章（募集资金用途情况）由业务人工维护，
# 不产出范例、不进 manifest（见 manifest.json 的 $manual_chapters）。
SKIP_CHAPTERS = {3, 7}

EXAMPLE_HEADER = """<!--
本范例切分自一份已通过发改委审核的真实申报材料（实体名已脱敏）。
用途：向撰写agent展示该章的【篇幅、结构、行文风格、数据颗粒度】。
⚠️ 严禁照抄其中任何数字/日期/机构名——所有数据必须来自本项目的 extracted_data.json；
   占位符（如【原始权益人全称】）对应 base_vars.json 里的本项目实体。
-->

"""

# manifest.json 中由本脚本负责生成的键；此集合之外的键都是手工维护的，重跑时必须保留
SCRIPT_KEYS = {
    "chapter", "title", "example", "example_paras", "example_tables",
    "template", "template_title", "template_paras", "template_tables",
}

TEMPLATE_HEADER = """<!--
本切片来自官方模版《申报材料格式文本（2024年版）》的对应章节，含全部指导文字原文。
用途：①指导文字告诉你该写什么；②生成 fill_plan 的 paragraphs.match 时，
   直接从本文件复制段落原文的前15~25字作为 match（子串包含匹配，逐字一致才能命中）。
[表格] 行标注了该章各表格的 table_index（基于完整模版的全局序号）与首行，供 locate 使用。
-->

"""


def sanitize(text):
    for src, dst in SANITIZE_MAP:
        text = text.replace(src, dst)
    return text


def iter_body(doc):
    """按文档顺序产出 ('p', Paragraph) / ('tbl', Table)"""
    for element in doc.element.body:
        if element.tag.endswith("}p"):
            yield "p", Paragraph(element, doc)
        elif element.tag.endswith("}tbl"):
            yield "tbl", Table(element, doc)


CELL_MAX = 80  # 单元格文本上限；30 太短，经营范围/资产范围这类长字段会被截断到无法参照


def table_to_md(table, tbl_idx):
    """表格转 md：全局 table_index + 尺寸 + **全部行**。

    为什么不截断行（历史事故）：原先只给前 6 行 + 「其余 N 行略」，子agent照着
    6 行样例推算行号，行数一多就越界——ch4「表12行号越界」的次生根因就是这个。
    行是子agent唯一能参照的结构信息，必须给全。
    """
    n_rows = len(table.rows)
    n_cols = len(table.columns)
    lines = [f"[表格 table_index={tbl_idx}] {n_rows}行 x {n_cols}列"]
    for row in table.rows:
        cells = [c.text.strip().replace("\n", " ")[:CELL_MAX] for c in row.cells]
        lines.append("  | " + " | ".join(cells))
    return "\n".join(lines)


def split_doc(docx_path, do_sanitize):
    """按一级章节切分，返回 [{title, blocks:[md片段], n_paras, n_tables}]；含前置部分(chapter 0)"""
    doc = Document(docx_path)
    chapters = [{"title": "（前置：封面/摘要表/释义）", "blocks": [], "n_paras": 0, "n_tables": 0}]
    tbl_idx = -1
    for kind, obj in iter_body(doc):
        if kind == "p":
            t = obj.text.strip()
            if CHAPTER_PAT.match(t) and len(t) < 40 and obj.style.name.startswith("Heading"):
                chapters.append({"title": t, "blocks": [], "n_paras": 0, "n_tables": 0})
                continue
            if t:
                chapters[-1]["blocks"].append(sanitize(t) if do_sanitize else t)
                chapters[-1]["n_paras"] += 1
        else:
            tbl_idx += 1
            md = table_to_md(obj, tbl_idx)
            chapters[-1]["blocks"].append(sanitize(md) if do_sanitize else md)
            chapters[-1]["n_tables"] += 1
    return chapters


def main():
    ap = argparse.ArgumentParser(description="按章节切分标准答案与官方模版")
    ap.add_argument("--answer", required=True, help="标准答案docx（将脱敏）")
    ap.add_argument("--emit-template-slices", action="store_true",
                    help="额外产出 ch{i}_template.md（默认不产出：改用 read_chapter.py 现读实际基底）")
    ap.add_argument("--template", required=True, help="官方模版docx（保留原文）")
    ap.add_argument("--out-dir", required=True, help="输出目录")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = {"chapters": []}

    # 读已有 manifest：本脚本只管 SCRIPT_KEYS，其余键（anchor_map / anchor_styles /
    # $ambiguous_matches / min_paras 等手工维护的元数据）必须原样保留。
    # 历史事故：重跑切片把 16.4KB 的 manifest 冲成 3.3KB，ch5 的 30 个锚点全丢。
    prev_by_ch = {}
    prev_top = {}
    man_path = os.path.join(args.out_dir, "manifest.json")
    if os.path.exists(man_path):
        try:
            old = json.load(open(man_path, encoding="utf-8"))
            prev_top = {k: v for k, v in old.items() if k != "chapters"}
            for c in old.get("chapters", []):
                prev_by_ch[c.get("chapter")] = c
        except Exception as e:
            print(f"WARN 读取已有 manifest 失败，手工字段将丢失：{e}", file=sys.stderr)

    ans_chapters = split_doc(args.answer, do_sanitize=True)
    tpl_chapters = split_doc(args.template, do_sanitize=False)

    n = max(len(ans_chapters), len(tpl_chapters))
    for i in range(n):
        if i in SKIP_CHAPTERS:
            continue
        entry = {"chapter": i}
        if i < len(ans_chapters):
            ch = ans_chapters[i]
            fname = f"ch{i}_example.md"
            with open(os.path.join(args.out_dir, fname), "w", encoding="utf-8") as f:
                f.write(EXAMPLE_HEADER + f"# {ch['title']}（标准范例·脱敏）\n\n" + "\n\n".join(ch["blocks"]))
            entry.update({"title": ch["title"], "example": fname,
                          "example_paras": ch["n_paras"], "example_tables": ch["n_tables"]})
        if i < len(tpl_chapters):
            ch = tpl_chapters[i]
            # 模版切片文件**默认不再产出**：它的 table_index 是官方模版序号，用初稿作
            # 填充基底时会整体偏移（实测某初稿插了释义表 → 官方 idx=1~25 全部 +1）。
            # 改由 scripts/read_chapter.py 从实际基底现读。这里只保留 manifest 计数
            # （ch5 的「条目数 ≤ 可替换段数」等硬约束仍依赖 template_paras）。
            entry.update({"template_title": ch["title"],
                          "template_paras": ch["n_paras"], "template_tables": ch["n_tables"]})
            if args.emit_template_slices:
                fname = f"ch{i}_template.md"
                with open(os.path.join(args.out_dir, fname), "w", encoding="utf-8") as f:
                    f.write(TEMPLATE_HEADER + f"# {ch['title']}（官方模版切片）\n\n"
                            + "\n\n".join(ch["blocks"]))
                entry["template"] = fname
        prev = prev_by_ch.get(i, {})
        kept = {k: v for k, v in prev.items() if k not in SCRIPT_KEYS}
        if kept:
            entry.update(kept)
        manifest["chapters"].append(entry)

    manifest.update(prev_top)  # 顶层手工键也保留
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("=== 章节切分完成 → %s ===" % args.out_dir)
    for e in manifest["chapters"]:
        print("  ch%d %-22s 范例:%s段/%s表  模版:%s段/%s表" % (
            e["chapter"], (e.get("title") or e.get("template_title", "?"))[:20],
            e.get("example_paras", "-"), e.get("example_tables", "-"),
            e.get("template_paras", "-"), e.get("template_tables", "-")))


if __name__ == "__main__":
    main()
