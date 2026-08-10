# -*- coding: utf-8 -*-
"""
共享工具：从官方《基础设施领域不动产投资信托基金（REITs）项目申报材料格式文本》
模板docx中，提取某一章节的完整结构（标题层级、表格位置、表格行列标签）。

用法：
    python read_chapter.py <模板docx路径> "<本章标题，如 四、项目基本条件>" "<下一章标题，如 五、项目合规情况>"

设计原因：
    模板的表格编号、行列标签是"官方格式文本"的一部分，未来官方版本升级、
    或者不同项目使用的模板小版本不同，都可能导致手抄一份"表12有哪几行"这种
    静态文档过时。与其把结构抄进每个chapter skill里维护7份容易漂移的副本，
    不如让Claude每次都直接从模板docx里现读——模板文件本身才是唯一可信来源。
"""
import sys
import docx
from docx.table import Table
from docx.oxml.ns import qn


def dump_chapter(template_path, start_heading, end_heading=None):
    d = docx.Document(template_path)
    body = d.element.body
    from docx.text.paragraph import Paragraph

    seq = []
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            seq.append(('p', Paragraph(child, d)))
        elif child.tag == qn('w:tbl'):
            seq.append(('t', Table(child, d)))

    # locate start/end indices by exact heading text
    start_idx = None
    end_idx = len(seq)
    for i, (kind, el) in enumerate(seq):
        if kind == 'p' and el.text.strip() == start_heading:
            start_idx = i
        elif start_idx is not None and end_heading and kind == 'p' and el.text.strip() == end_heading:
            end_idx = i
            break
    if start_idx is None:
        print(f'未找到起始标题: {start_heading!r}')
        return

    print(f'=== {start_heading} ({"到 " + end_heading if end_heading else "至文末"}) ===\n')
    for kind, el in seq[start_idx:end_idx]:
        if kind == 'p':
            style = el.style.name
            text = el.text.strip()
            if not text:
                continue
            if style.startswith('Heading'):
                print(f'\n【{style}】{text}')
            elif text.startswith('表') and len(text) < 60:
                print(f'  <表格标题> {text}')
            else:
                print(f'  段落({style}): {text[:120]}')
        else:  # table
            rows = el.rows
            print(f'    表格: {len(rows)}行 x {len(rows[0].cells) if rows else 0}列')
            for r_i, r in enumerate(rows):
                cells = [c.text.strip().replace(chr(10), ' ') for c in r.cells]
                print(f'      行{r_i}: {cells}')
    print()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    template_path = sys.argv[1]
    start_heading = sys.argv[2]
    end_heading = sys.argv[3] if len(sys.argv) > 3 else None
    dump_chapter(template_path, start_heading, end_heading)
