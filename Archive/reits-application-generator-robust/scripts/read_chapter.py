#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
现读某一章的真实结构（标题层级、段落原文、表格尺寸与全部行）。

为什么不用预生成的切片文件（设计理由）：
  官方模版的表格编号、行列标签是「格式文本」的一部分，官方版本升级、或不同项目用的
  模版小版本不同，都会让手抄的静态切片过时。更要命的是**填充基底往往是业务初稿**——
  业务增删过表格与行，切片里的 table_index 与行数就全错（实测某初稿在摘要表后插了
  释义表，官方 idx=1~25 在初稿里全部 +1）。
  与其维护 7 份容易漂移的副本，不如每次直接从**实际填充基底**里现读：
  基底文件本身才是唯一可信来源，现读出来的编号与行数天然正确。

用法:
  # 读某一章（推荐：直接读填充基底，而不是官方模版）
  python read_chapter.py "<填充基底.docx>" "一、项目基本情况" "二、参与主体情况"

  # 省略结束标题则读到文末
  python read_chapter.py "<基底.docx>" "七、募集资金用途情况"

  # 只看表格结构（不打印段落），适合表格多的章
  python read_chapter.py "<基底.docx>" "四、项目基本条件" "五、项目合规情况" --tables-only

输出说明:
  【样式】标题        —— 该段的 Word 样式名，用于判断 Heading 层级
  段落原文            —— 生成 fill_plan 的 paragraphs.match 时从这里复制前 15~25 字
  >>> 表 idx=N        —— **本文档实际的 table_index**（可直接用），含尺寸与全部行
  上方标题            —— 表格上方最近的非空段落，可作 title_keyword / md 表标题
"""

import argparse
import sys

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CELL_MAX = 80


def row_cells(row):
    """逐个真实 w:tc 取文本。不能用 row.cells（会把合并展开成重复项），
    也不能用 lxml itertext（会把同一文本抽多次）。"""
    tbl = row._parent
    return [_Cell(tc, tbl).text.strip().replace("\n", " ")[:CELL_MAX]
            for tc in row._tr.findall(qn("w:tc"))]


def iter_body(doc):
    for el in doc.element.body:
        if el.tag.endswith("}p"):
            yield "p", Paragraph(el, doc)
        elif el.tag.endswith("}tbl"):
            yield "t", Table(el, doc)


def dump_chapter(path, start_heading, end_heading=None, tables_only=False):
    doc = Document(path)

    # 先按文档顺序建序列，并给每张表打上**全文实际 table_index**
    seq = []
    tbl_idx = -1
    last_para = ""
    for kind, obj in iter_body(doc):
        if kind == "p":
            t = obj.text.strip()
            seq.append(("p", obj, t, None))
            if t:
                last_para = t
        else:
            tbl_idx += 1
            seq.append(("t", obj, last_para, tbl_idx))

    def list_headings(from_i=0):
        """列出 from_i 之后文档实有的 Heading 标题，供命中失败时人工比对。"""
        for kind, obj, txt, _ in seq[from_i:]:
            if kind == "p" and txt and len(txt) < 40 and obj.style.name.startswith("Heading"):
                print("  " + txt, file=sys.stderr)

    def find_heading(prefix, from_i=0):
        """两遍匹配：优先命中 Heading 样式段（防命中目录行等同文前缀段），
        全文无 Heading 命中时才退回首个普通段并由调用方告警。
        返回 (索引 or None, 是否Heading样式命中)。"""
        fallback = None
        for i in range(from_i, len(seq)):
            kind, obj, txt, _ = seq[i]
            if kind != "p" or not txt or not txt.startswith(prefix):
                continue
            if obj.style.name.startswith("Heading"):
                return i, True
            if fallback is None:
                fallback = i
        return fallback, False

    # 定位起止
    start_i, start_is_h = find_heading(start_heading)
    if start_i is None:
        print("未找到起始标题: %r" % start_heading, file=sys.stderr)
        print("\n文档中的一级标题有：", file=sys.stderr)
        list_headings()
        sys.exit(1)
    if not start_is_h:
        print("WARN: 起始标题命中的段不是 Heading 样式（可能是目录行）: %r"
              % seq[start_i][2][:40], file=sys.stderr)

    end_i = len(seq)
    end_hit = None
    if end_heading:
        j, end_is_h = find_heading(end_heading, start_i + 1)
        if j is None:
            # 结束标题未命中时严禁静默读到文末——那会把后面所有章都当成“本章”
            print("ERROR: 未找到结束标题: %r（起始标题之后无此前缀段）" % end_heading, file=sys.stderr)
            print("拒绝读到文末。请从下列实有标题中复制正确的下一章标题重跑：", file=sys.stderr)
            list_headings(start_i + 1)
            sys.exit(1)
        if not end_is_h:
            print("WARN: 结束标题命中的段不是 Heading 样式（可能是目录行）: %r"
                  % seq[j][2][:40], file=sys.stderr)
        end_i = j
        end_hit = seq[j][2]

    n_p = sum(1 for k, _, t, _ in seq[start_i:end_i] if k == "p" and t)
    n_t = sum(1 for k, *_ in seq[start_i:end_i] if k == "t")
    # 首行回显**实际命中**的起止段，而不是传入参数——章界是否切对一眼可查
    print("=== 实际命中：%s  →  %s ==="
          % (seq[start_i][2][:40], (end_hit[:40] if end_hit else "文末（未给结束标题）")))
    print("本章：非空段落 %d 个，表格 %d 张" % (n_p, n_t))
    print("（若段数远超本章 guide 的篇幅量级，说明章界失效——立即停止并上报，不要继续撰写）")
    print("（table_index 是**本文档实际序号**，可直接用于 fill_plan）\n")

    for kind, obj, txt, ti in seq[start_i:end_i]:
        if kind == "p":
            if tables_only or not txt:
                continue
            style = obj.style.name
            if style.startswith("Heading"):
                print("\n【%s】%s" % (style, txt))
            else:
                print("  段落(%s): %s" % (style, txt))
        else:
            rows = obj.rows
            print("\n  >>> 表 idx=%d  %d行 x %d列   上方标题: %s"
                  % (ti, len(rows), len(obj.columns), (txt or "(无)")[:50]))
            for ri, r in enumerate(rows):
                print("      行%-2d | %s" % (ri, " | ".join(row_cells(r))))
            print()


def main():
    ap = argparse.ArgumentParser(description="现读某一章的真实结构（段落原文 + 表格全部行）")
    ap.add_argument("docx", help="填充基底 docx（推荐用实际基底，而非官方模版）")
    ap.add_argument("start", help="本章标题，如 一、项目基本情况")
    ap.add_argument("end", nargs="?", default=None, help="下一章标题；省略则读到文末")
    ap.add_argument("--tables-only", action="store_true", help="只打印表格结构，不打印段落")
    args = ap.parse_args()
    dump_chapter(args.docx, args.start, args.end, args.tables_only)


if __name__ == "__main__":
    main()
