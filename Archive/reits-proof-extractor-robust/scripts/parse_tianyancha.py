#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
天眼查「专业版企业信用报告」确定性解析器（阶段A 的 docx 支线）。

为什么单独一个脚本：
  这类报告的扩展名常是 `.doc`，但**实为 OOXML（PK 头）**，python-docx 直接可读；
  它有自定义样式（`报告一级标题`/`报告二级标题`/`报告三级标题`）与几十张规整表格，
  字段是标准 kv 或表头行 —— 属于**可确定性提取**的材料，不该走"拆图 + agent 读图"
  那条昂贵且有损的路径。而阶段A 的 `batch_render_pdfs.py` 只处理 `.pdf`，
  这类文件在页级队列里会显示「❌ 未找到 txt/页图」，形成"回去重跑阶段A 也补不出产物"
  的死结。本脚本同时解决两件事：①把字段确定性地提出来；②产出
  `<work_dir>/images/<主干>.txt`，让覆盖率的页级队列拿到正常的 txt 单元。

三条数据纪律（写死在实现里，不是建议）：
  1. **财务数据严格隔离**：天眼查财务是模糊值（只有 2 位有效数字，如「资产总计348亿」）
     且年份常不连续（缺 2023），一律只写 `tyc_report.financials_fuzzy` 并带 `_precision`，
     **绝不写 `entities.*.financials[YYYY]`**（那是表5 与 EBITDA/资产负债率公式的取数路径）。
  2. **风险计数是线索不是结论**：`动产抵押/对外担保/股权出质/司法冻结` 等非零项进
     `risk_nonzero` 并打 ⚠️，提示回第五章可转让性逐项核实，脚本不做任何合规判断。
  3. **股东比例合计必须=100%** 才写 `legal_relations.project_company_equity`，
     否则只进 warnings —— schema 硬要求，不得凑数。

溯源字段：`_source`（相对 proof_dir 的真实路径）、`_attachment_no`（取自**真实存在的**
  编号：文件名数字前缀 > 最深一级带编号祖先目录 > 二级材料目录编号）、`_doc_name`、
  `_section`（如 `2.1工商信息`）、`_raw_text`；**`_page` 一律 null**（docx 无页码概念，
  citation_rules 的 `[[第{page}页]]` 可选段会整段丢弃，不会留「第页」残渣）。绝不编造页码。

用法:
  # 先干跑，人工核对字段
  python parse_tianyancha.py "<报告1.doc>" "<报告2.doc>" --work-dir "<work_dir>" \
      --proof-dir "<proof_dir>" --extracted "<work_dir>/extracted_data.json" --dry-run
  # 正式跑（原子合并进 extracted_data.json，默认不覆盖已有非空值）
  python parse_tianyancha.py "<报告1.doc>" "<报告2.doc>" --work-dir "<work_dir>" \
      --proof-dir "<proof_dir>" --extracted "<work_dir>/extracted_data.json"

退出码: 0 成功 / 1 参数或解析失败 / 2 extracted_data.json 存在但损坏（一个字节都不写）
"""

import argparse
import copy
import json
import os
import re
import shutil
import sys
import tempfile

# Windows GBK 控制台/管道下打印 ✅⚠️ 等字符不崩溃
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

try:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
except ImportError:
    print("ERROR: python-docx not installed. Run: pip install python-docx", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 常量

# 天眼查报告的自定义标题样式名（用户实测）；样式缺失时回退到文本形态判定
HEADING_STYLES = {
    '报告一级标题': 1,
    '报告二级标题': 2,
    '报告三级标题': 3,
}

# 文本形态兜底：`一、企业基本信息` = 1 级；`2.1 工商信息` = 2 级；`2.1.1 xxx` = 3 级
RE_H1_TEXT = re.compile(r'^[一二三四五六七八九十]+[、.．]\s*\S')
RE_H2_TEXT = re.compile(r'^\d+\.\d+\s*\S')
RE_H3_TEXT = re.compile(r'^\d+\.\d+\.\d+\s*\S')

# 标题尾部的计数，如 `6.3 行政许可(82)`
RE_HEADING_COUNT = re.compile(r'[（(](\d+)[）)]\s*$')

# 计数网格单元格，如 `失信被执行人(0)`
RE_CELL_COUNT = re.compile(r'^(.*?)\s*[（(](\d+)[）)]\s*$')

# 路径组件/文件名的材料编号前缀（与 scan_proofs.extract_file_number 同一口径）
RE_NO_PREFIX = re.compile(r'^(\d+[-\d]*)')

# 注册资本：`59999万人民币` / `1.5亿人民币` / `5,000万元` / `100万美元`
RE_CAPITAL = re.compile(
    r'^\s*([\d,]+(?:\.\d+)?)\s*(亿|万)?\s*(?:元)?\s*'
    r'(人民币|美元|欧元|港元|港币|日元|英镑|新加坡元)?\s*(?:元)?\s*$')

RE_DATE = re.compile(r'(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?')
RE_YEAR = re.compile(r'(\d{4})')
RE_RATIO = re.compile(r'(\d+(?:\.\d+)?)\s*%')

CNY_ALIASES = ('人民币', '')

# 财务数据模糊值的强制标注（写死，不接受调用方覆盖）
FUZZY_NOTE = ('天眼查模糊值（仅2位有效数字，如「资产总计348亿」），仅供数量级交叉校验；'
              '正式财务数据一律以审计报告为准，严禁填入表5或用于任何公式取数')

# 非零即需回查可转让性的风险项（第五章（四）的核查线索）
WATCH_RISK_KEYS = ('动产抵押', '对外担保', '股权出质', '股权冻结', '司法冻结',
                   '土地抵押', '抵押', '质押', '查封', '限制高消费')

# 章节键 -> 解析器分派（键为归一化后的小节名前缀）
SECTION_BASIC = '2.1工商信息'
SECTION_SHAREHOLDER = '2.2股东信息'
SECTION_PERSONNEL = '2.3主要人员'
SECTION_INVESTMENT = '2.4对外投资'
SECTION_BRANCH = '2.6分支机构'
SECTION_FINANCE = '2.8财务数据'
SECTION_CONTROLLER = '2.10实际控制人'

# 工商信息 kv 键名 -> extracted_data 字段名
BASIC_FIELD_MAP = {
    '企业名称': 'name',
    '公司名称': 'name',
    '统一社会信用代码': 'credit_code',
    '法定代表人': 'legal_rep',
    '注册资本': 'registered_capital',
    '成立日期': 'established_date',
    '成立时间': 'established_date',
    '注册地址': 'registered_address',
    '企业地址': 'registered_address',
    '经营范围': 'business_scope',
    '企业类型': 'enterprise_type',
    '公司类型': 'enterprise_type',
    '登记状态': 'registration_status',
    '经营状态': 'registration_status',
    '营业期限': 'business_term',
    '经营期限': 'business_term',
}

# 财务三表的分类关键词
FIN_TABLE_KEYS = (
    ('balance_sheet', ('资产总计', '负债合计', '资产负债表', '所有者权益合计')),
    ('income_statement', ('营业总收入', '营业收入', '营业利润', '利润表', '净利润')),
    ('cash_flow', ('经营活动产生的现金流量', '现金流量表', '现金及现金等价物净增加额')),
)


# ---------------------------------------------------------------- 基础工具

def _t(s):
    """规范化单元格/段落文本：去首尾空白、压缩内部空白与全角空格"""
    if s is None:
        return ''
    return re.sub(r'[\s\u3000]+', ' ', str(s)).strip()


def _is_empty(v):
    """判定"空值"（合并时可被填充）：None / '' / [] / {} / 纯空白字符串"""
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ''
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _clip(s, n=480):
    s = _t(s)
    return s if len(s) <= n else s[:n] + '…'


def norm_section_key(text):
    """标题文本 -> 小节键：去掉尾部计数与全部空白。`6.3 行政许可(82)` -> `6.3行政许可`"""
    t = _t(text)
    t = RE_HEADING_COUNT.sub('', t).strip()
    return re.sub(r'[\s\u3000]+', '', t)


def heading_count(text):
    """标题尾部括号里的计数（`6.3 行政许可(82)` -> 82）；没有返回 None"""
    m = RE_HEADING_COUNT.search(_t(text))
    return int(m.group(1)) if m else None


def norm_date(text):
    """`2009-08-13` / `2009/8/13` / `2009年08月13日` -> `2009年8月13日`（schema 规定格式）"""
    t = _t(text)
    m = RE_DATE.search(t)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1900 <= y <= 2200 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return '%d年%d月%d日' % (y, mo, d)


def norm_business_term(text):
    """营业期限：`2009-08-13 至 2059-08-12` -> `2009年8月13日至2059年8月12日`；
    只有一个日期或含「无固定期限」时原样返回（不猜）。"""
    t = _t(text)
    if not t:
        return None
    dates = RE_DATE.findall(t)
    if len(dates) >= 2:
        a = '%d年%d月%d日' % (int(dates[0][0]), int(dates[0][1]), int(dates[0][2]))
        b = '%d年%d月%d日' % (int(dates[1][0]), int(dates[1][1]), int(dates[1][2]))
        return '%s至%s' % (a, b)
    return t


def norm_capital(text):
    """注册资本 -> 万元数值（float，保留2位小数）。

    返回 (value_or_None, warning_or_None)。非人民币币种**不换算**（置 None + 告警），
    避免把「100万美元」当成 100 万元人民币填进表3/表4。
    """
    t = _t(text)
    if not t or t in ('-', '--', '/', '无'):
        return None, None
    m = RE_CAPITAL.match(t)
    if not m:
        return None, '注册资本无法解析：%r（保持 null，请人工确认）' % t
    num, unit, cur = m.group(1), m.group(2), m.group(3)
    if cur and cur not in CNY_ALIASES:
        return None, '注册资本为非人民币币种（%s），不做换算，保持 null：%r' % (cur, t)
    try:
        val = float(num.replace(',', ''))
    except ValueError:
        return None, '注册资本数值无法解析：%r' % t
    if unit == '亿':
        val *= 10000.0
    elif unit == '万':
        pass
    else:
        # 无量词按「元」处理，换算为万元
        val /= 10000.0
    return round(val, 2), None


def norm_ratio(text):
    """持股比例：保留原文精度的字符串（schema 要求），如 `44.385%`。取不到返回 None"""
    t = _t(text)
    m = RE_RATIO.search(t)
    return (m.group(1) + '%') if m else None


def ratio_value(text):
    m = RE_RATIO.search(_t(text or ''))
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- docx 读取

def sniff_format(path):
    """探清真实格式。返回 'ooxml' / 'ole2' / 'unknown'。"""
    try:
        with open(path, 'rb') as f:
            magic = f.read(8)
    except OSError as e:
        raise RuntimeError('无法读取文件：%s' % e)
    if magic[:4] == b'PK\x03\x04':
        return 'ooxml'
    if magic[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return 'ole2'
    return 'unknown'


def iter_block_items(doc):
    """按**文档顺序**产出段落与表格。

    必须这么做：`doc.tables` 丢失了表格与段落的相对位置，无法把表挂到它所属的小节上，
    而本报告全靠「小节标题 + 紧随其后的表」定位字段。
    """
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, doc)
        elif child.tag == qn('w:tbl'):
            yield Table(child, doc)


def heading_level(par):
    """段落是否为标题、几级。优先看自定义样式名，样式对不上时回退文本形态。"""
    try:
        name = par.style.name if par.style is not None else ''
    except Exception:
        name = ''
    if name in HEADING_STYLES:
        return HEADING_STYLES[name]
    for sty, lv in HEADING_STYLES.items():
        if name and sty in name:
            return lv
    text = _t(par.text)
    if not text or len(text) > 40:
        return 0
    if RE_H3_TEXT.match(text):
        return 3
    if RE_H2_TEXT.match(text):
        return 2
    if RE_H1_TEXT.match(text):
        return 1
    return 0


def distinct_cells(row):
    """行内去重后的单元格列表。

    横向合并的单元格在 python-docx 里会被**重复返回**同一个 `_tc`，
    直接按下标取 (c0,c1)/(c2,c3) 会把「经营范围」这种整行合并的格解析成
    「键=经营范围, 值=经营范围」。按 `_tc` 身份去重可消除这一类假字段。
    """
    out, seen = [], set()
    for c in row.cells:
        key = id(c._tc)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def table_matrix(tbl):
    """表格 -> 二维文本矩阵（行内已按 _tc 去重）"""
    return [[_t(c.text) for c in distinct_cells(r)] for r in tbl.rows]


# ---------------------------------------------------------------- 表格解析器

def parse_kv_double_col(tbl):
    """「左右双列 kv」布局：一行藏两个字段 —— (c0,c1) 与 (c2,c3) 各成一对。

    行内去重后：>=4 格取两对；==2 格取一对（整行合并的长字段，如经营范围）；
    ==3 格取一对并忽略末格（表尾常见的空占位）。
    """
    kv = {}
    for row in table_matrix(tbl):
        cells = [c for c in row]
        if len(cells) >= 4:
            pairs = [(cells[0], cells[1]), (cells[2], cells[3])]
        elif len(cells) == 3:
            pairs = [(cells[0], cells[1])]
        elif len(cells) == 2:
            pairs = [(cells[0], cells[1])]
        else:
            continue
        for k, v in pairs:
            k = _t(k).rstrip('：:')
            v = _t(v)
            if not k or k == v:
                continue
            if k in kv and kv[k]:
                continue
            kv[k] = v
    return kv


def parse_header_rows(tbl, min_cols=2):
    """首行表头 -> 逐行成 dict。表头空列用 col{i} 兜底；全空行跳过。"""
    mat = table_matrix(tbl)
    if len(mat) < 2:
        return []
    header = [(_t(h) or ('col%d' % i)) for i, h in enumerate(mat[0])]
    if len(header) < min_cols:
        return []
    rows = []
    for r in mat[1:]:
        if not any(_t(x) for x in r):
            continue
        item = {}
        for i, val in enumerate(r):
            key = header[i] if i < len(header) else ('col%d' % i)
            item[key] = _t(val)
        rows.append(item)
    return rows


def parse_count_grid(tbl):
    """计数网格 -> {项名: 计数}。

    兼容两种排版：①单格内 `失信被执行人(0)`；②两列 `失信被执行人 | 0`。
    项名会剥掉前缀编号（`4.1 失信被执行人` -> `失信被执行人`）。
    """
    counts = {}

    def put(name, num):
        name = re.sub(r'^\d+(?:\.\d+)*\s*[、.．]?\s*', '', _t(name)).strip('：: ')
        if not name:
            return
        try:
            counts[name] = int(num)
        except (TypeError, ValueError):
            pass

    for row in table_matrix(tbl):
        cells = [c for c in row if _t(c)]
        # ② 两列：名 | 数字
        if len(cells) == 2 and re.fullmatch(r'\d+', _t(cells[1])):
            put(cells[0], cells[1])
            continue
        # ① 单格内含括号计数（一行可能并排多个）
        for c in cells:
            m = RE_CELL_COUNT.match(_t(c))
            if m:
                put(m.group(1), m.group(2))
    return counts


def classify_fin_table(mat):
    """按表内出现的科目关键词判断这是三表中的哪一张；判不出返回 None"""
    blob = '\n'.join('|'.join(r) for r in mat[:60])
    for key, kws in FIN_TABLE_KEYS:
        if any(kw in blob for kw in kws):
            return key
    return None


# ---------------------------------------------------------------- 小节切分

def split_sections(doc):
    """把文档切成 {小节键: {'title','level','paragraphs','tables','count'}}。

    **目录页去重**：目录条目与正文标题同名，且目录一定在正文之前 ——
    因此对同名标题一律**取最后一次出现**，天然跳过目录页。
    """
    blocks = list(iter_block_items(doc))

    headings = []          # (block_index, level, key, title)
    for i, b in enumerate(blocks):
        if isinstance(b, Paragraph):
            lv = heading_level(b)
            if lv:
                key = norm_section_key(b.text)
                if key:
                    headings.append((i, lv, key, _t(b.text)))

    # 同名标题取最后一次出现（去掉目录页的那一份）
    last_of = {}
    for i, lv, key, title in headings:
        last_of[key] = (i, lv, key, title)
    kept = sorted(last_of.values(), key=lambda x: x[0])

    sections = {}
    for n, (idx, lv, key, title) in enumerate(kept):
        end = kept[n + 1][0] if n + 1 < len(kept) else len(blocks)
        paras, tables = [], []
        for b in blocks[idx + 1:end]:
            if isinstance(b, Paragraph):
                t = _t(b.text)
                if t:
                    paras.append(t)
            else:
                tables.append(b)
        sections[key] = {
            'title': title,
            'level': lv,
            'block_index': idx,
            'paragraphs': paras,
            'tables': tables,
            'count': heading_count(title),
        }
    return sections, blocks


def find_section(sections, *keys):
    """按键精确查找，找不到再按「去掉编号后的名字」模糊查找（编号偶有版本差异）"""
    for k in keys:
        if k in sections:
            return k, sections[k]
    wanted = [re.sub(r'^\d+(?:\.\d+)*', '', k) for k in keys]
    for sk, sv in sections.items():
        bare = re.sub(r'^\d+(?:\.\d+)*', '', sk)
        if bare and bare in wanted:
            return sk, sv
    return None, None


def section_raw_text(sec, limit=480):
    """小节原文片段（供 _raw_text）：段落 + 首表前几行"""
    parts = list(sec.get('paragraphs') or [])
    for tbl in (sec.get('tables') or [])[:1]:
        for row in table_matrix(tbl)[:8]:
            cells = [c for c in row if c]
            if cells:
                parts.append(' | '.join(cells))
    return _clip('\n'.join(parts), limit)


# ---------------------------------------------------------------- 溯源信息

def build_provenance(report_path, proof_dir, doc_name_override=None):
    """算出 _source / _attachment_no / _doc_name。

    `_attachment_no` 只取**材料树里真实存在**的编号，优先级：
      ① 文件名的数字前缀（如 `28-4-1 xxx.doc` -> `28-4-1`）
      ② 最深一级带编号的祖先目录（如 `28-4 天眼查文件` -> `28-4`）
      ③ 最浅一级带编号的祖先目录（二级材料目录，如 `28 其他应当出具的证明材料` -> `28`）
    三者都取不到则为 None（正文标 pending，绝不编造）。
    """
    abspath = os.path.abspath(report_path)
    rel = None
    if proof_dir:
        pd = os.path.abspath(proof_dir)
        try:
            r = os.path.relpath(abspath, pd)
        except ValueError:
            r = None
        if r and not r.startswith('..'):
            rel = r.replace('\\', '/')
    if rel is None:
        rel = os.path.basename(abspath)

    comps = [c for c in rel.split('/') if c.strip()]
    fname = comps[-1] if comps else os.path.basename(abspath)
    stem = os.path.splitext(fname)[0]

    attachment_no = None
    m = RE_NO_PREFIX.match(fname.strip())
    if m:
        attachment_no = m.group(1).rstrip('-')
    else:
        dir_nos = []
        for comp in comps[:-1]:
            dm = RE_NO_PREFIX.match(comp.strip())
            if dm:
                dir_nos.append(dm.group(1).rstrip('-'))
        if dir_nos:
            attachment_no = dir_nos[-1]        # 最深一级
    doc_name = _t(doc_name_override) or stem
    return {
        '_source': rel,
        '_attachment_no': attachment_no,
        '_doc_name': doc_name,
        '_page': None,
    }


def stamp(obj, prov, section=None, raw_text=None):
    """给一个数据对象盖溯源字段（与数据字段同层，符合 schema 的存放层级约定）"""
    obj['_source'] = prov['_source']
    obj['_attachment_no'] = prov['_attachment_no']
    obj['_doc_name'] = prov['_doc_name']
    obj['_page'] = None
    if section:
        obj['_section'] = section
    if raw_text:
        obj['_raw_text'] = raw_text
    return obj


# ---------------------------------------------------------------- 字段构建

def build_basic(sections, prov, warnings):
    """2.1工商信息 -> 规范字段 dict（直接进 entities.originators[i] / project_companies[i]）"""
    key, sec = find_section(sections, SECTION_BASIC, '2.1基本信息', '工商信息')
    if not sec:
        warnings.append('未找到「2.1 工商信息」小节，工商字段全部跳过')
        return {}, None
    kv = {}
    for tbl in sec['tables']:
        for k, v in parse_kv_double_col(tbl).items():
            if k not in kv or not kv[k]:
                kv[k] = v
    if not kv:
        warnings.append('「%s」小节未解析出任何 kv 字段' % key)
        return {}, key

    out = {}
    for raw_key, val in kv.items():
        field = BASIC_FIELD_MAP.get(raw_key)
        if not field or not val or val in ('-', '--', '/'):
            continue
        if field == 'registered_capital':
            num, warn = norm_capital(val)
            if warn:
                warnings.append(warn)
            if num is not None:
                out[field] = num
        elif field == 'established_date':
            d = norm_date(val)
            if d:
                out[field] = d
            else:
                warnings.append('成立日期无法解析：%r' % val)
        elif field == 'business_term':
            out[field] = norm_business_term(val)
        elif field == 'credit_code':
            out[field] = re.sub(r'\s+', '', val)
        else:
            out[field] = val
    if out:
        raw = ' | '.join('%s：%s' % (k, _clip(v, 60)) for k, v in kv.items())
        stamp(out, prov, key, _clip(raw))
    return out, key


def build_shareholders(sections, prov, company_name, warnings):
    """2.2股东信息 -> legal_relations.project_company_equity 候选行 + 原始行"""
    key, sec = find_section(sections, SECTION_SHAREHOLDER, '股东信息')
    if not sec:
        warnings.append('未找到「2.2 股东信息」小节，股东结构跳过')
        return [], [], key
    rows = []
    for tbl in sec['tables']:
        rows.extend(parse_header_rows(tbl))
    if not rows:
        warnings.append('「%s」小节未解析出股东行' % key)
        return [], [], key

    raw_text = _clip(section_raw_text(sec))
    equity = []
    for r in rows:
        name = ''
        ratio = None
        for k, v in r.items():
            kk = _t(k)
            if not name and ('股东' in kk or '名称' in kk) and '比例' not in kk:
                name = _t(v)
            if ratio is None and ('比例' in kk or '持股' in kk or '出资比例' in kk):
                ratio = norm_ratio(v)
        if not name:
            continue
        if ratio is None:
            for v in r.values():
                ratio = norm_ratio(v)
                if ratio:
                    break
        item = {'project_company': company_name, 'shareholder': name, 'ratio': ratio}
        stamp(item, prov, key, raw_text)
        equity.append(item)

    total = sum(ratio_value(e['ratio']) or 0.0 for e in equity)
    if not equity:
        return [], rows, key
    if abs(total - 100.0) > 0.5:
        warnings.append(
            '股东持股比例合计 %.4f%% ≠ 100%%（共 %d 位股东）—— 按 schema 硬要求**不写入** '
            'legal_relations.project_company_equity，请人工核对章程/营业执照后补录'
            % (total, len(equity)))
        return [], rows, key
    return equity, rows, key


def build_controller(sections, prov, warnings):
    """2.10实际控制人 -> (actual_controller, control_path, 原始行)"""
    key, sec = find_section(sections, SECTION_CONTROLLER, '实际控制人', '2.9实际控制人')
    if not sec:
        return None, None, [], key
    rows = []
    for tbl in sec['tables']:
        rows.extend(parse_header_rows(tbl))

    best_name, best_val = None, -1.0
    paths = []
    for r in rows:
        name, ratio, path = '', None, ''
        for k, v in r.items():
            kk, vv = _t(k), _t(v)
            if not name and ('名称' in kk or '股东' in kk or '姓名' in kk
                             or '控制人' in kk) and '路径' not in kk:
                name = vv
            if ratio is None and ('比例' in kk or '股份' in kk or '受益' in kk):
                ratio = ratio_value(vv)
            if '路径' in kk or '关联' in kk:
                path = vv
        if path:
            paths.append(path)
        if name and ratio is not None and ratio > best_val:
            best_name, best_val = name, ratio

    if not paths:
        # 路径有时以正文段落而非表列出现
        paths = [p for p in (sec.get('paragraphs') or []) if '投资' in p or '->' in p or '→' in p]

    controller = None
    if best_name:
        # schema 明确：实际控制人为自然人只记姓名，不记身份证号等敏感信息
        clean = re.sub(r'[（(].*?[）)]', '', best_name).strip()
        clean = re.sub(r'\d{6,}', '', clean).strip()
        if clean:
            controller = clean
    else:
        warnings.append('「%s」小节未能判定实际控制人（缺姓名或比例列），保持 null'
                        % (key or '实际控制人'))

    control_path = _clip('\n'.join(paths), 900) if paths else None
    return controller, control_path, rows, key


def build_personnel(sections, prov, warnings):
    """2.3主要人员 -> [{name, title}]"""
    key, sec = find_section(sections, SECTION_PERSONNEL, '主要人员', '主要成员')
    if not sec:
        return [], key
    rows = []
    for tbl in sec['tables']:
        rows.extend(parse_header_rows(tbl))
    raw_text = _clip(section_raw_text(sec))
    out = []
    for r in rows:
        name, title = '', ''
        for k, v in r.items():
            kk, vv = _t(k), _t(v)
            if not name and ('姓名' in kk or '名称' in kk or '人员' in kk):
                name = vv
            if not title and ('职位' in kk or '职务' in kk):
                title = vv
        if not name:
            vals = [_t(v) for v in r.values() if _t(v)]
            if vals:
                name = vals[0]
                title = vals[1] if len(vals) > 1 else ''
        if not name:
            continue
        item = {'name': name, 'title': title or None}
        stamp(item, prov, key, raw_text)
        out.append(item)
    if not out:
        warnings.append('「%s」小节未解析出主要人员' % (key or '主要人员'))
    return out, key


def build_rows_section(sections, prov, keys, warnings, label):
    """通用「表头行」小节 -> 行列表（对外投资 / 分支机构 / 资质证书 / 上榜榜单）"""
    key, sec = find_section(sections, *keys)
    if not sec:
        return [], key
    rows = []
    for tbl in sec['tables']:
        rows.extend(parse_header_rows(tbl))
    if not rows:
        return [], key
    raw_text = _clip(section_raw_text(sec))
    for r in rows:
        stamp(r, prov, key, raw_text)
    return rows, key


def build_financials_fuzzy(sections, prov, warnings):
    """2.8财务数据 -> financials_fuzzy（**只做交叉校验用**，强制打 _precision）"""
    key, sec = find_section(sections, SECTION_FINANCE, '财务数据', '2.7财务数据')
    if not sec:
        return None, key
    out = {'_precision': FUZZY_NOTE}
    years = []
    idx = 0
    for tbl in sec['tables']:
        mat = table_matrix(tbl)
        if len(mat) < 2:
            continue
        rows = parse_header_rows(tbl)
        if not rows:
            continue
        cls = classify_fin_table(mat)
        if not cls:
            idx += 1
            cls = 'table_%d' % idx
        if cls in out and isinstance(out[cls], list):
            out[cls].extend(rows)
        else:
            out[cls] = rows
        for h in mat[0][1:]:
            hh = _t(h)
            if hh and hh not in years and RE_YEAR.search(hh):
                years.append(hh)

    if len(out) <= 1:
        return None, key

    out['_years_available'] = years
    ys = sorted({int(RE_YEAR.search(y).group(1)) for y in years if RE_YEAR.search(y)})
    missing = []
    if ys:
        missing = [str(y) for y in range(ys[0], ys[-1] + 1)
                   if not any(str(y) in v for v in years)]
    out['_years_missing'] = missing
    if missing:
        warnings.append('天眼查财务数据年份不连续，缺 %s —— 凑不齐「最近3年及一期」，'
                        '只能做数量级交叉校验' % '、'.join(missing))
    stamp(out, prov, key, _clip(section_raw_text(sec)))
    return out, key


def build_risk_summary(sections, prov, warnings):
    """四、法律诉讼 / 五、经营风险 / 六、经营信息 -> risk_summary + risk_nonzero + licenses

    计数有两个来源：①小节标题尾部的 `(82)`；②小节内的计数网格表。两者并集。
    """
    risk_summary = {}
    licenses = {}
    nonzero = []

    top_map = {}
    for skey, sec in sections.items():
        if sec['level'] != 1:
            continue
        bare = re.sub(r'^[一二三四五六七八九十]+[、.．]?', '', skey)
        top_map[bare] = skey

    def group_of(skey):
        """小节键所属的一级章节名（按块顺序找最近的上级一级标题）"""
        idx = sections[skey]['block_index']
        best, best_idx = None, -1
        for k2, s2 in sections.items():
            if s2['level'] == 1 and s2['block_index'] <= idx and s2['block_index'] > best_idx:
                best, best_idx = k2, s2['block_index']
        return best

    RISK_GROUPS = ('法律诉讼', '经营风险')
    INFO_GROUPS = ('经营信息',)

    for skey, sec in sections.items():
        grp = group_of(skey) or ''
        in_risk = any(g in grp for g in RISK_GROUPS)
        in_info = any(g in grp for g in INFO_GROUPS)
        if not (in_risk or in_info):
            continue

        bucket = risk_summary.setdefault(grp, {}) if in_risk else licenses

        # ① 标题自带计数
        if sec['count'] is not None and sec['level'] >= 2:
            item = re.sub(r'^\d+(?:\.\d+)*\s*', '', skey)
            if item:
                bucket[item] = sec['count']
        # ② 小节内的计数网格
        for tbl in sec['tables']:
            for item, num in parse_count_grid(tbl).items():
                if item and item not in bucket:
                    bucket[item] = num

    # 一级章节标题本身也可能带总计数
    for bare, skey in top_map.items():
        c = sections[skey]['count']
        if c is not None and any(g in bare for g in RISK_GROUPS):
            risk_summary.setdefault(skey, {})['_total'] = c

    for grp, items in risk_summary.items():
        for item, num in items.items():
            if item.startswith('_') or not num:
                continue
            hit = any(w in item for w in WATCH_RISK_KEYS)
            nonzero.append({
                'section': grp,
                'item': item,
                'count': num,
                'need_crosscheck': ('该项非零且属权利限制类，必须回第五章（四）可转让性逐项核实'
                                    '是否涉及标的资产，并登记到 '
                                    'compliance.transferability.{restrictions,encumbrances}'
                                    if hit else
                                    '该项非零，需人工判断是否影响第二章「近3年无重大违法违规」表述'),
                'watch': hit,
            })
    nonzero.sort(key=lambda e: (not e['watch'], -e['count']))

    if not risk_summary and not licenses:
        return None, None, []
    if risk_summary:
        stamp(risk_summary, prov, '四、法律诉讼 / 五、经营风险')
    if licenses:
        stamp(licenses, prov, '六、经营信息')
    return (risk_summary or None), (licenses or None), nonzero


def build_honors(sections, prov, warnings):
    """6.9资质证书 / 6.20上榜榜单 -> honors_raw；三要素齐全的才升级为 awards 候选。

    返回 (honors_or_None, awards, parsed_section_keys)
    """
    honors = {}
    awards = []
    keys_used = []
    for keys, label in ((('6.9资质证书', '资质证书'), '资质证书'),
                        (('6.20上榜榜单', '上榜榜单'), '上榜榜单')):
        rows, key = build_rows_section(sections, prov, keys, warnings, label)
        if not rows:
            continue
        honors[label] = rows
        if key:
            keys_used.append(key)
        for r in rows:
            year = name = grantor = None
            for k, v in r.items():
                if k.startswith('_'):
                    continue
                kk, vv = _t(k), _t(v)
                if year is None and ('年' in kk or '时间' in kk or '日期' in kk):
                    m = RE_YEAR.search(vv)
                    year = m.group(1) if m else None
                if name is None and ('名称' in kk or '榜单' in kk or '证书' in kk or '奖' in kk):
                    name = vv or None
                if grantor is None and ('授予' in kk or '发布' in kk or '机构' in kk
                                        or '颁发' in kk or '来源' in kk):
                    grantor = vv or None
            # 三要素齐全才进 awards，否则只留原始清单（不编造）
            if year and name and grantor:
                item = {'year': year, 'name': name,
                        'subject': _t(r.get('获奖主体') or '') or None, 'grantor': grantor}
                stamp(item, prov, key)
                awards.append(item)
    return (honors or None), awards, keys_used


def parse_report(path, proof_dir, doc_name_override=None):
    """解析一份报告，返回 (payload, warnings, sections, blocks)。payload 为中间结构。"""
    warnings = []
    fmt = sniff_format(path)
    if fmt == 'ole2':
        raise RuntimeError(
            '该文件是**真正的 OLE2 .doc**（非 docx 伪装），python-docx 读不了：%s\n'
            '   请先用 Word/WPS 另存为 .docx（或 LibreOffice: soffice --convert-to docx），'
            '再重跑本脚本。本脚本不调用任何外部转换服务（红线第2条）。' % path)
    if fmt != 'ooxml':
        raise RuntimeError('无法识别的文件格式（既非 OOXML 也非 OLE2）：%s' % path)

    try:
        doc = Document(path)
    except Exception as e:
        raise RuntimeError('python-docx 打开失败（%s）：%s' % (path, e))

    prov = build_provenance(path, proof_dir, doc_name_override)
    sections, blocks = split_sections(doc)
    if not sections:
        raise RuntimeError('未能按标题样式切出任何小节：%s（请确认是天眼查专业版信用报告）' % path)

    basic, k_basic = build_basic(sections, prov, warnings)
    company_name = basic.get('name')
    equity, sh_rows, k_sh = build_shareholders(sections, prov, company_name, warnings)
    controller, control_path, ctrl_rows, k_ctrl = build_controller(sections, prov, warnings)
    personnel, k_per = build_personnel(sections, prov, warnings)
    branches, k_br = build_rows_section(
        sections, prov, (SECTION_BRANCH, '分支机构'), warnings, '分支机构')
    investments, k_inv = build_rows_section(
        sections, prov, (SECTION_INVESTMENT, '对外投资'), warnings, '对外投资')
    fin, k_fin = build_financials_fuzzy(sections, prov, warnings)
    risk_summary, licenses, risk_nonzero = build_risk_summary(sections, prov, warnings)
    honors, awards, k_honors = build_honors(sections, prov, warnings)

    parsed_keys = [k for k in ([k_basic, k_sh, k_ctrl, k_per, k_br, k_inv, k_fin]
                               + list(k_honors)) if k]
    unparsed = sorted(k for k, v in sections.items()
                      if k not in parsed_keys and v['level'] >= 2 and v['tables'])

    tyc_report = {
        'sections_parsed': parsed_keys,
        'sections_unparsed': unparsed,
    }
    if risk_summary:
        tyc_report['risk_summary'] = risk_summary
    if risk_nonzero:
        tyc_report['risk_nonzero'] = risk_nonzero
    if fin:
        tyc_report['financials_fuzzy'] = fin
    if licenses:
        tyc_report['licenses'] = licenses
    if branches:
        tyc_report['branches'] = branches
    if investments:
        tyc_report['outbound_investments'] = investments
    if honors:
        tyc_report['honors_raw'] = honors
    if warnings:
        tyc_report['parse_warnings'] = list(warnings)
    stamp(tyc_report, prov, '天眼查专业版企业信用报告（全文）')

    payload = {
        'prov': prov,
        'basic': basic,
        'key_personnel': personnel,
        'equity': equity,
        'shareholder_rows': sh_rows,
        'controller': controller,
        'control_path': control_path,
        'controller_rows': ctrl_rows,
        'tyc_report': tyc_report,
        'awards': awards,
    }
    return payload, warnings, sections, blocks


# ---------------------------------------------------------------- 全文 txt 导出

def dump_full_text(blocks, out_path):
    """导出全文文字层到 <work_dir>/images/<主干>.txt。

    这一步不是"顺手做的"：`check_extraction_coverage.locate_artifacts` 正是按
    `images/<主干>.txt` 找阶段A产物 —— 没有它，这两份 .doc 在页级队列里永远显示
    「❌ 未找到 txt/页图」，而 SKILL 指示的补救动作（重跑 batch_render_pdfs）
    对 .doc 不生效，形成死循环。
    """
    lines = []
    for b in blocks:
        if isinstance(b, Paragraph):
            t = _t(b.text)
            if t:
                lv = heading_level(b)
                lines.append(('#' * lv + ' ' + t) if lv else t)
        else:
            lines.append('')
            for row in table_matrix(b):
                cells = [c for c in row]
                if any(cells):
                    lines.append(' | '.join(cells))
            lines.append('')
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    text = '\n'.join(lines)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return len(text)


# ---------------------------------------------------------------- 合并（写盘安全）

class MergeAborted(Exception):
    pass


def load_extracted(path):
    """读 extracted_data.json。

    **解析失败绝不重建**：文件存在但 JSON 损坏时抛异常，由调用方以 exit=2 中止且
    一个字节都不写 —— 与 check_extraction_coverage.py 的 --mark-batch 同一语义
    （历史事故：用空壳覆盖掉已提取的数据）。
    """
    if not os.path.exists(path):
        return {'_metadata': {'read_items': [], 'read_pages': []}}, True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        raise MergeAborted('%s 存在但无法解析为 JSON（%s）。已中止，未写入任何字节；'
                           '修好该文件后重跑即可。' % (path, e))
    if not isinstance(data, dict):
        raise MergeAborted('%s 顶层不是对象（实际为 %s）。已中止，未写入任何字节。'
                           % (path, type(data).__name__))
    return data, False


def _list_of_dicts(lst):
    return any(isinstance(x, dict) for x in lst if x is not None)


# 【累积型列表：必须 append 而不是按下标合并】
# 这些路径下的每一行都是**独立记录**，且多份报告各自贡献若干行（原始权益人报告给出它自己的
# 股东行、项目公司报告给出项目公司的股东行）。若按下标合并，第二份报告的第 0 行会被并进
# 第一份报告的第 0 行 —— 结果是「润泽科技的股东行里混进河北润禾的 project_company」这类
# 静默错数据（实测已复现）。因此对这些路径改为 append + 去重。
APPEND_LIST_PATHS = {
    'legal_relations.project_company_equity',
    'legal_relations.originator_relations',
    'awards',
}

# 各累积列表的去重键（按业务主键判重，避免同一份报告重跑产生重复行）
APPEND_DEDUP_KEYS = {
    'legal_relations.project_company_equity': ('project_company', 'shareholder'),
    'legal_relations.originator_relations': ('parent', 'subsidiary'),
    'awards': ('year', 'name', 'grantor'),
}


def _dedup_key(path, item):
    """取记录的业务主键；未定义主键时退化为「全部非下划线字段」的元组"""
    keys = APPEND_DEDUP_KEYS.get(path)
    if keys:
        return tuple(item.get(k) for k in keys)
    return tuple(sorted((k, json.dumps(v, ensure_ascii=False, sort_keys=True))
                        for k, v in item.items() if not k.startswith('_')))


def _append_list(dst_list, src_list, path, changes):
    """把 src_list 的记录 append 到 dst_list，按业务主键去重"""
    seen = set()
    for x in dst_list:
        if isinstance(x, dict):
            seen.add(_dedup_key(path, x))
    for item in src_list:
        if not isinstance(item, dict):
            continue
        k = _dedup_key(path, item)
        if k in seen:
            changes['same'].append('%s[+] %s（已存在，跳过）' % (path, _clip(repr(k), 60)))
            continue
        seen.add(k)
        dst_list.append(copy.deepcopy(item))
        changes['added'].append('%s[%d]' % (path, len(dst_list) - 1))


def deep_merge(dst, src, overwrite, changes, path=''):
    """深合并 src -> dst。

    默认**不覆盖已有非空值**（只填空位），冲突逐条记录供人工复核；`overwrite=True`
    才允许覆盖。列表分两类：
      - `APPEND_LIST_PATHS` 里的**累积型列表** → append + 按业务主键去重
      - 其余 list-of-dict → 按下标逐元素合并（元素为 None 表示"该下标不参与本次合并"，
        用于 entities.originators[N] 这种"写第 N 个主体"的场景）
    """
    for k, v in src.items():
        p = '%s.%s' % (path, k) if path else k
        if v is None:
            continue
        cur = dst.get(k)

        if isinstance(v, dict) and isinstance(cur, dict):
            deep_merge(cur, v, overwrite, changes, p)
            continue

        if isinstance(v, list) and p in APPEND_LIST_PATHS:
            if not isinstance(cur, list):
                if _is_empty(cur):
                    dst[k] = []
                    cur = dst[k]
                else:
                    changes['conflict'].append('%s（已有非列表值，跳过累积）' % p)
                    continue
            _append_list(cur, v, p, changes)
            continue

        if isinstance(v, list) and isinstance(cur, list) and _list_of_dicts(v):
            for i, item in enumerate(v):
                if item is None:
                    continue
                while len(cur) <= i:
                    cur.append({})
                if isinstance(item, dict) and isinstance(cur[i], dict):
                    deep_merge(cur[i], item, overwrite, changes, '%s[%d]' % (p, i))
                elif _is_empty(cur[i]) or overwrite:
                    cur[i] = copy.deepcopy(item)
                    changes['added'].append('%s[%d]' % (p, i))
            continue

        if _is_empty(cur):
            dst[k] = copy.deepcopy(v)
            changes['added'].append(p)
        elif cur == v:
            changes['same'].append(p)
        elif overwrite:
            changes['overwritten'].append('%s（旧=%s → 新=%s）'
                                          % (p, _clip(repr(cur), 60), _clip(repr(v), 60)))
            dst[k] = copy.deepcopy(v)
        else:
            changes['conflict'].append('%s（保留已有=%s，跳过天眼查=%s）'
                                       % (p, _clip(repr(cur), 60), _clip(repr(v), 60)))


def atomic_write_json(path, data):
    """原子写入 + 写前备份 .bak（大 JSON 半写坏比不写更糟）"""
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + '.bak')
        except OSError as e:
            print('WARNING: 备份 %s.bak 失败：%s' % (path, e), file=sys.stderr)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tyc_', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- 组装 fragment

SUBJECT_CHOICES = ('originator', 'project_company')
SUBJECT_ARRAY = {'originator': 'originators', 'project_company': 'project_companies'}
SUBJECT_LABEL = {'originator': '原始权益人', 'project_company': '项目公司'}


def guess_subject(path):
    """按文件名判定主体：含「原始权益人/发起人」→ originator；含「项目公司」→ project_company"""
    name = os.path.basename(str(path))
    if '项目公司' in name:
        return 'project_company'
    if '原始权益人' in name or '发起人' in name:
        return 'originator'
    return None


def build_fragment(payload, subject, index):
    """把解析结果拼成可 deep_merge 的 extracted_data.json 片段"""
    array_key = SUBJECT_ARRAY[subject]
    entity = {}
    entity.update(payload['basic'])
    if payload['key_personnel']:
        entity['key_personnel'] = payload['key_personnel']
    entity['tyc_report'] = payload['tyc_report']
    if subject == 'originator' and payload['controller']:
        entity['actual_controller'] = payload['controller']

    slots = [None] * index + [entity]
    frag = {'entities': {array_key: slots}}

    legal = {}
    if payload['equity']:
        legal['project_company_equity'] = payload['equity']
    if payload['controller']:
        legal['actual_controller'] = payload['controller']
    if payload['control_path']:
        legal['control_path'] = payload['control_path']
    if legal:
        frag['legal_relations'] = legal

    if payload['awards']:
        frag['awards'] = payload['awards']
    return frag


def print_field_tree(frag, title):
    print('\n--- %s ---' % title)
    print(json.dumps(frag, ensure_ascii=False, indent=2))


def summarize_payload(payload, subject, index, txt_path):
    prov = payload['prov']
    b = payload['basic']
    tr = payload['tyc_report']
    print('  主体            : %s（entities.%s[%d]）'
          % (SUBJECT_LABEL[subject], SUBJECT_ARRAY[subject], index))
    print('  _source         : %s' % prov['_source'])
    print('  _attachment_no  : %s' % (prov['_attachment_no'] or '（无，正文须标 pending）'))
    print('  _doc_name       : %s' % prov['_doc_name'])
    print('  _page           : null（docx 无页码，绝不编造）')
    print('  工商字段        : %d 个 %s'
          % (len([k for k in b if not k.startswith('_')]),
             sorted(k for k in b if not k.startswith('_'))))
    print('  股东行（已采纳）: %d' % len(payload['equity']))
    print('  股东行（原始）  : %d' % len(payload['shareholder_rows']))
    print('  实际控制人      : %s' % (payload['controller'] or '（未判定）'))
    print('  主要人员        : %d' % len(payload['key_personnel']))
    print('  财务模糊值      : %s'
          % ('有（%s）' % '、'.join(k for k in (tr.get('financials_fuzzy') or {})
                                    if not k.startswith('_')) if tr.get('financials_fuzzy')
             else '无'))
    print('  风险非零项      : %d' % len(tr.get('risk_nonzero') or []))
    print('  已解析小节      : %s' % '、'.join(tr.get('sections_parsed') or []))
    if tr.get('sections_unparsed'):
        print('  未解析小节      : %d 个 —— %s'
              % (len(tr['sections_unparsed']), '、'.join(tr['sections_unparsed'][:12])
                 + ('…' if len(tr['sections_unparsed']) > 12 else '')))
    if txt_path:
        print('  全文 txt        : %s' % txt_path)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description='天眼查专业版企业信用报告确定性解析 → 原子合并进 extracted_data.json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='注意：本脚本纯本地解析（python-docx），不联网、不调 OCR/LLM；'
               '天眼查财务只写 tyc_report.financials_fuzzy，绝不写 financials[YYYY]。')
    ap.add_argument('reports', nargs='+', help='报告文件路径（.doc 后缀实为 docx 也可）')
    ap.add_argument('--work-dir', '-w', required=True,
                    help='工作目录（全文 txt 写到 <work_dir>/images/）')
    ap.add_argument('--extracted', '-e', default=None,
                    help='extracted_data.json 路径（默认 <work_dir>/extracted_data.json）')
    ap.add_argument('--proof-dir', default=None,
                    help='证明材料根目录：用于把 _source 归一为相对路径并推导 _attachment_no')
    ap.add_argument('--subject', action='append', choices=SUBJECT_CHOICES, default=None,
                    help='与位置参数一一对应的主体类型；省略则按文件名「原始权益人/项目公司」自动判定')
    ap.add_argument('--index', action='append', type=int, default=None,
                    help='写入 entities.<array>[N] 的下标（与位置参数一一对应，默认全部 0）')
    ap.add_argument('--doc-name', action='append', default=None,
                    help='覆盖 _doc_name（与位置参数一一对应），用于让正文括注更自然')
    ap.add_argument('--overwrite', action='store_true',
                    help='允许覆盖 extracted_data.json 中已有的非空值（默认只填空位）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只打印将写入的字段树与汇总，不落盘（首次跑建议先用它）')
    ap.add_argument('--emit-txt', dest='emit_txt', action='store_true', default=True,
                    help='导出全文到 <work_dir>/images/<主干>.txt（默认开启，覆盖率页级队列依赖它）')
    ap.add_argument('--no-emit-txt', dest='emit_txt', action='store_false',
                    help='不导出全文 txt（不建议：会让页级队列报「未找到 txt/页图」）')
    args = ap.parse_args()

    if not os.path.isdir(args.work_dir):
        print('ERROR: work_dir 不存在：%s' % args.work_dir, file=sys.stderr)
        sys.exit(1)
    extracted_path = args.extracted or os.path.join(args.work_dir, 'extracted_data.json')

    n = len(args.reports)

    def pick(lst, i, default=None):
        """位置参数与可重复选项的对齐：给足 n 个则一一对应；只给 1 个则全体复用。"""
        if not lst:
            return default
        if len(lst) == 1:
            return lst[0]
        return lst[i] if i < len(lst) else default

    # ---- 逐份解析（任一份失败即整体中止，不做半量合并）----
    parsed = []
    all_warnings = []
    for i, rp in enumerate(args.reports):
        if not os.path.isfile(rp):
            print('ERROR: 文件不存在：%s' % rp, file=sys.stderr)
            sys.exit(1)
        subject = pick(args.subject, i) or guess_subject(rp)
        if not subject:
            print('ERROR: 无法判定 %s 的主体类型（文件名不含「原始权益人/发起人/项目公司」）。\n'
                  '       请显式指定：--subject originator 或 --subject project_company'
                  % os.path.basename(rp), file=sys.stderr)
            sys.exit(1)
        idx = pick(args.index, i, 0)
        idx = 0 if idx is None else int(idx)
        if idx < 0:
            print('ERROR: --index 不能为负：%s' % idx, file=sys.stderr)
            sys.exit(1)
        dn = pick(args.doc_name, i)

        print('\n=== 解析 [%d/%d] %s ===' % (i + 1, n, os.path.basename(rp)))
        try:
            payload, warns, sections, blocks = parse_report(rp, args.proof_dir, dn)
        except RuntimeError as e:
            print('❌ 解析失败：%s' % e, file=sys.stderr)
            sys.exit(1)

        txt_path = None
        if args.emit_txt:
            stem = os.path.splitext(os.path.basename(rp))[0]
            txt_path = os.path.join(args.work_dir, 'images', stem + '.txt')
            if args.dry_run:
                print('  （dry-run）将导出全文 txt: %s' % txt_path)
            else:
                size = dump_full_text(blocks, txt_path)
                print('  ✅ 全文 txt 已导出（%d 字符）: %s' % (size, txt_path))

        summarize_payload(payload, subject, idx, txt_path)
        frag = build_fragment(payload, subject, idx)
        parsed.append({'path': rp, 'subject': subject, 'index': idx,
                       'payload': payload, 'frag': frag})
        all_warnings.extend('[%s] %s' % (os.path.basename(rp), w) for w in warns)

    # ---- 风险线索：非零项必须显式喊出来（第五章可转让性的核查入口）----
    watch_hits = []
    for p in parsed:
        for e in (p['payload']['tyc_report'].get('risk_nonzero') or []):
            if e.get('watch'):
                watch_hits.append((os.path.basename(p['path']), e))
    if watch_hits:
        print('', file=sys.stderr)
        print('=' * 72, file=sys.stderr)
        print('⚠️ 检测到权利限制类风险项非零 —— 这些是**待核查线索，不是结论**：', file=sys.stderr)
        for fn, e in watch_hits:
            print('   - [%s] %s：%d（%s）' % (fn, e['item'], e['count'], e['section']),
                  file=sys.stderr)
        print('   ▶ 必须回第五章（四）可转让性逐项核实是否涉及标的资产，并登记到', file=sys.stderr)
        print('     compliance.transferability.{restrictions,encumbrances}；', file=sys.stderr)
        print('     脚本不做任何合规判断，也不会替你写「不影响转让」这类结论。', file=sys.stderr)
        print('=' * 72, file=sys.stderr)

    if args.dry_run:
        for p in parsed:
            print_field_tree(p['frag'], 'dry-run 将写入（%s / index=%d）'
                             % (os.path.basename(p['path']), p['index']))
        if all_warnings:
            print('\n--- warnings（%d 条）---' % len(all_warnings))
            for w in all_warnings:
                print('  ⚠️ %s' % w)
        print('\nℹ️ dry-run：未写入 %s，也未导出 txt。核对无误后去掉 --dry-run 重跑。'
              % extracted_path)
        return

    # ---- 合并落盘 ----
    try:
        data, created = load_extracted(extracted_path)
    except MergeAborted as e:
        print('❌ 合并已中止：%s' % e, file=sys.stderr)
        sys.exit(2)
    if created:
        print('\nℹ️ %s 不存在，已按空壳结构初始化' % extracted_path)

    changes = {'added': [], 'same': [], 'conflict': [], 'overwritten': []}
    for p in parsed:
        deep_merge(data, p['frag'], args.overwrite, changes)

    atomic_write_json(extracted_path, data)

    print('\n=== 合并结果（%s）===' % extracted_path)
    print('  新增字段  : %d' % len(changes['added']))
    for f in changes['added'][:40]:
        print('      + %s' % f)
    if len(changes['added']) > 40:
        print('      ... 及另外 %d 项' % (len(changes['added']) - 40))
    print('  值相同    : %d（跳过）' % len(changes['same']))
    if changes['overwritten']:
        print('  已覆盖    : %d（--overwrite）' % len(changes['overwritten']))
        for f in changes['overwritten']:
            print('      ! %s' % f)
    if changes['conflict']:
        print('  冲突保留  : %d（保留 extracted_data 已有值，天眼查值被跳过）'
              % len(changes['conflict']))
        for f in changes['conflict']:
            print('      = %s' % f)
        print('    → 逐条人工复核：确认应以天眼查为准时，加 --overwrite 重跑。')
    if all_warnings:
        print('  warnings  : %d' % len(all_warnings))
        for w in all_warnings:
            print('      ⚠️ %s' % w)

    print('\n✅ 已原子写入（备份 %s.bak）。' % extracted_path)
    print('   注：本脚本**不写** _metadata.read_items（那属"脚本代写进度"）；'
          '覆盖率靠写入的真实 _source 现算。')
    unparsed_all = sorted({s for p in parsed
                           for s in (p['payload']['tyc_report'].get('sections_unparsed') or [])})
    if unparsed_all:
        print('   未解析小节共 %d 个 —— 如需其中内容，读全文 txt 补提，不要重跑阶段A：'
              % len(unparsed_all))
        print('     %s' % ('、'.join(unparsed_all[:20])
                            + ('…' if len(unparsed_all) > 20 else '')))
    print('   下一步：跑 check_extraction_coverage.py 确认这两份材料不再报'
          '「未找到 txt/页图」且不在「仅自报可疑」清单。')


if __name__ == '__main__':
    main()
