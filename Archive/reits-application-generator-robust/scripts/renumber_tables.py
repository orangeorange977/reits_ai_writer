#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表号全篇统一重排：按表格出现顺序把所有表标题段的编号改写为 表1、表2、表3…（跨章连续）。

交付前最后一步执行（在所有章节填充、rebuild_tables 完成之后）：
  python renumber_tables.py --input <已生成.docx> --output <重排后.docx>
  python renumber_tables.py --input <已生成.docx> --dry-run          # 只看映射不落盘

设计要点（与 rebuild_tables 配套的「表#」纪律）：
  - rebuild_tables 的 caption 一律写「表#」占位，本脚本统一赋号——生成侧从此不关心全局表号；
  - 官方模版自带的固定表号（表1~表24）同样被重排覆盖，业务已确认「表#连续编号」是标准做法；
  - 正文引用一律写「下表」「如下表所示」，禁止硬编码表号（validate_output.py 负责检查），
    因此重排不需要同步改正文引用。

标题段判定（移植 AI test web_render.py 的 _para_is_caption_before_table）：
  文字以「表#/表＃/表12/表A」开头（_CAP_LEAD_RE），且该段之后（跳过空段）紧跟一张表格。
  「表格」「表决」这类词不会命中（表 后面不是 #/字母/数字）；正文里"详见表5"的句子
  因为后面不紧跟表格实体，同样不会被误改。

改写方式（移植 _set_caption_number）：只改 runs[0] 的文字、清空其余 run 的编号残留，
  段落样式/字体完全保留。幂等：重复执行结果不变。
"""
import argparse
import json
import re
import sys

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

# 表标题里的编号占位：表# / 表＃ / 表3 / 表10 / 表A / 表4-1（复合式整体吞掉，
# 否则「表4-1」只改「表4」部分，重排后残留成「表3-1」）
_CAP_LEAD_RE = re.compile(r"^表(?:[#＃]|[0-9]+(?:[-—－.][0-9]+)?|[A-Za-z])")


def iter_block_items(doc):
    """按文档序产出 body 顶层的 Paragraph / Table 对象。"""
    for el in doc.element.body:
        if el.tag.endswith('}p'):
            yield Paragraph(el, doc)
        elif el.tag.endswith('}tbl'):
            yield Table(el, doc)


def para_is_caption_before_table(items, i):
    """items[i] 是不是一张表的标题段：文字像「表X …」且其后（跳过空段）紧跟一张表格。"""
    b = items[i]
    if not isinstance(b, Paragraph) or not _CAP_LEAD_RE.match(b.text.strip()):
        return False
    j = i + 1
    while j < len(items) and isinstance(items[j], Paragraph) and not items[j].text.strip():
        j += 1
    return j < len(items) and isinstance(items[j], Table)


def set_caption_number(para, n):
    """把标题段开头的「表X」改成「表{n}」，保留该段原有字体/格式。"""
    if not para.runs:
        return False
    full = ''.join(r.text for r in para.runs)
    stripped = full.lstrip()
    m = _CAP_LEAD_RE.match(stripped)
    if not m:
        return False
    lead_ws = full[:len(full) - len(stripped)]
    para.runs[0].text = lead_ws + f'表{n}' + stripped[m.end():]
    for r in para.runs[1:]:
        r.text = ''
    return True


def renumber_all_table_captions(doc):
    """全篇按表格出现顺序重排表标题编号。返回 [(旧编号文字, 新标题文字)] 映射。"""
    items = list(iter_block_items(doc))
    mapping = []
    counter = 0
    for i in range(len(items)):
        if para_is_caption_before_table(items, i):
            counter += 1
            old_text = items[i].text.strip()
            if set_caption_number(items[i], counter):
                mapping.append({'old': old_text, 'new': items[i].text.strip()})
            else:
                # runs 结构异常（如整段无 run）：跳过但记录，不吞错
                mapping.append({'old': old_text, 'new': old_text,
                                'skipped': 'runs结构异常，未改写'})
    return mapping


def main():
    parser = argparse.ArgumentParser(description='表号全篇统一重排（交付前最后一步）')
    parser.add_argument('--input', required=True, help='已生成的docx路径')
    parser.add_argument('--output', default=None, help='重排后输出路径（--dry-run 时可省）')
    parser.add_argument('--dry-run', action='store_true', help='只输出映射，不落盘')
    args = parser.parse_args()

    if not args.dry_run and not args.output:
        print('ERROR: 非 --dry-run 模式必须提供 --output', file=sys.stderr)
        sys.exit(2)

    doc = Document(args.input)
    mapping = renumber_all_table_captions(doc)
    skipped = [m for m in mapping if m.get('skipped')]

    if not args.dry_run:
        doc.save(args.output)

    print(json.dumps({
        'mode': 'dry-run' if args.dry_run else 'renumber',
        'input': args.input,
        'output': None if args.dry_run else args.output,
        'captions': len(mapping),
        'renamed': sum(1 for m in mapping if m['old'] != m['new']),
        'skipped': len(skipped),
        'mapping': mapping,
    }, ensure_ascii=False, indent=2))
    # 有跳过项说明存在无法改写的标题段（罕见），交人工核对；不判失败但退出码提示
    sys.exit(1 if skipped else 0)


if __name__ == '__main__':
    main()
