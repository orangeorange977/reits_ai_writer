#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
填充流水线状态自检 / 恢复检查点（抗上下文压缩的核心组件）。

本 SKILL 只负责**内容填充**（第一步交接校验 → 第二步生成填充计划并写入文档 →
第三步章节完备性检查与交付）。证明材料的解压、扫描、读图提取**由上游完成**，
本脚本不重新计算阶段B覆盖率，但生成端必须通过 `handoff_validation.json` 的严格
READY 门禁；`gen_phase_fill_plan.py` 与 `fill_docx.py` 也会复核报告新鲜度并硬阻断。

用法（只读诊断，exit 恒为 0）：

    python pipeline_state.py --work-dir <work_dir>

它扫描 work_dir 里**实际存在的产物**，推导「当前在第几步 / 输入数据体检结论 /
各 fill_plan 是否已生成 / **越界产物体检** / 下一条该跑的命令」，并写入 <work_dir>/checkpoint.json。
会话被压缩、中断、换人接手后跑这一条即可恢复进度认知，不依赖上下文里的记忆。

设计要点（为什么不做成"agent 手写状态文件"）：
  checkpoint.json 是**推导产物，不是真相**，随时可由本脚本重建，**禁止手工编辑**。
  输入体检（关键字段非空、溯源字段覆盖、八处结构化数据源、**第四章 15 张表的逐表数据源**）
  一律现算自 extracted_data.json，不采信任何自报状态。
  ⚠️ 体检结论**不阻断**流程：它的用途是让你在第一步就把数据缺口告知用户/上游提取方，
  而不是等到第三步才发现"表格大面积为空、占位符泛滥"。
  **越界产物体检**同样只诊断不阻断：子agent的合法交付物只有 fill_plan_ch{N}.json +
  ch{N}_tables.md（N∈1/2/4/5/6），它报出别章 fill_plan 与疑似子agent落盘的 docx，
  并给出「基底(HEAD)参考」——防的是主agent（尤其上下文被压缩后接手时）把子agent
  独立写出的 step_ch4_v3.docx 之类误当填充基底。
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys

from handoff_gate import HandoffGateError, assert_handoff_ready

# Windows GBK 控制台/管道下打印 ✅❌ 等字符不崩溃
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

CHECKPOINT_NAME = 'checkpoint.json'

# work_dir 识别标志文件（任一存在即认为是本 SKILL 的工作目录）
MARKERS = ('extracted_data.json', 'proofs_index.json', 'base_vars.json', CHECKPOINT_NAME)

# 第二步产物清单
TABLE_PLANS = ['fill_plan_phase0.json', 'fill_plan_phase1.json',
               'fill_plan_phase2.json', 'fill_plan_phase4.json',
               'fill_plan_phase5.json']
# 三、七章由业务人工维护，本 SKILL 不生成 → 不列入待办
LEGIT_CHAPTERS = (1, 2, 4, 5, 6)          # 唯一允许委派子agent撰写的章
MANUAL_CHAPTERS = (3, 7)                  # 三、七章由业务人工维护，不委派、不生成
APPLY_ORDER_CH = (1, 2, 4, 5, 6)          # 逐批应用顺序里的章序（ch1→ch2→ch4→ch5→ch6）
CHAPTER_PLANS = ['fill_plan_ch%d.json' % n for n in LEGIT_CHAPTERS]   # 一/二/四/五/六章正文
CHAPTER_TABLE_MDS = ['ch%d_tables.md' % n for n in LEGIT_CHAPTERS]    # 子agent产出的 md 表格
LAST_PLANS = ['fill_plan_annex.json', 'fill_plan_phase5.phase6.json',
              'fill_plan_phase4.phase7.json']     # 必须最后应用：annex → phase6(-1) → phase4.phase7(+1，最后)

# 输入交接校验：关键字段（缺了对应章节只能占位或写不成）
KEY_FIELDS = [
    'project_info.project_name',
    'evaluation.total_value',
    'evaluation.total_net_value',
    'entities.originators[0].name',
    'entities.project_companies[0].name',
    'entities.fund_manager.name',
    'entities.abs_manager.name',
    'entities.operation_manager.name',
    'entities.law_firm.name',
]

# 输入交接校验：结构化数据源（缺则对应章节只能整节占位，详见 SKILL.md 第一步）
# ⚠️ `operating_performance` 曾漏列（SKILL.md 写「七处」而本清单只有六处）→ 第四章
# 15 张表的数据源为空时，第一步体检**一句话都不报**，一路走到交付才发现「正文写了
# 表4-4~表4-15，文档里却只有 3 张表」（实测事故）。它是第四章唯一结构化数据源，必须在列。
STRUCT_SOURCES = [
    ('sub_projects[0].building_area', '第一章表1（建设内容和规模等复合行）'),
    ('expandable_assets.assets', '第一章（三）可扩募资产表2（首发项目必需）'),
    ('legal_relations.project_company_equity', '第二章（一）法律关系'),
    ('entities.originators[0].compliance_credit', '第二章违法违规和信用情况'),
    ('operating_performance.annual_rows', '第四章表4-1 与（三）2 历史经营收益'),
    ('operating_performance.valuation_params',
     '第四章（四）资产估值情况正文 + 表4-7~表4-14（评估报告详细参数，非摘要页）'),
    ('operation_management.fee_structure', '第六章（二）激励约束机制'),
    ('compliance.procedures', '第五章表15~表19 投资管理手续'),
]

# ---- 第四章 15 张表的数据源逐表体检（本章表格事故的根因项）----
# 为什么单列一项：第四章的 15 张表由 phase4 蓝图从 operating_performance 确定性生成，
# **某张表的数据源为空 → 该表整表不生成**（gen_phase_fill_plan 写 table_new_skipped），
# 而 ch4 子agent按 guide 已经在正文写出了 15 个表标题段 → 交付稿里出现「有表标题、
# 没有表格实体」的悬空引用。实测根因：评估报告（数十页）只提取了前 3 页摘要，
# evaluation 段只有报告编号/评估值等基础字段，valuation_params 下的现金流预测、
# 运营费用参数、资本性支出明细、可比实例、客户财务数据全部缺失 → 表4-7~表4-14 全空。
# 因此这里逐表报缺，第一步就把「哪几张表建不出来、要向上游补哪个字段」讲清楚。
CH4_TABLE_SOURCES = [
    ('表4-1 经营收益情况', 'operating_performance.annual_rows', False, '备考财务报表'),
    ('表4-2 近3年与未来3年收益对比', 'operating_performance.forecast_rows', False,
     '备考财务报表 + 评估报告现金流预测表'),
    ('表4-3 收入结构', 'operating_performance.revenue_structure_rows', False, '备考财务报表-收入明细'),
    ('表4-4 占比超10%的现金流提供方', 'operating_performance.cash_flow_providers', False,
     '评估报告/客户合同台账'),
    ('表4-5 主要终端客户财务情况', 'operating_performance.end_customer_financial_rows', False,
     '终端客户公开财报'),
    ('表4-6 政府补贴情况', 'operating_performance.subsidies.subsidy_rows', True,
     '备考财务报表（无补贴则本表不涉及）'),
    ('表4-7 上架/出租情况', 'operating_performance.valuation_params.occupancy_rows', False, '评估报告'),
    ('表4-8 市场可比实例', 'operating_performance.valuation_params.comparable_case_rows', False, '评估报告'),
    ('表4-9 CPI', 'operating_performance.valuation_params.cpi_rows', False, '评估报告'),
    ('表4-10 可比项目服务费', 'operating_performance.valuation_params.comparable_price_rows', False,
     '评估报告'),
    ('表4-11 运营费用参数', 'operating_performance.valuation_params.opex_param_rows', False, '评估报告'),
    ('表4-12 非运营支出税费', 'operating_performance.valuation_params.tax_param_rows', False, '评估报告'),
    ('表4-13 前20设备支出', 'operating_performance.valuation_params.capex_equipment_rows', False,
     '评估报告设备生命周期表'),
    ('表4-14 预测期资本性支出', 'operating_performance.valuation_params.capex_forecast_rows', False,
     '评估报告'),
    ('表4-15 终端客户自建', 'operating_performance.self_built_rows', True,
     '评估报告/行业资料（不涉及则本表不建）'),
]

# 溯源字段（正文括注的唯一原料，缺了该内容无法标注来源）
PROV_KEYS = ('_attachment_no', '_doc_name')


# ---------------------------------------------------------------- 基础工具

def _load_json(path):
    """读 JSON。返回 (data, error)：任何异常（不存在/损坏/编码）都不抛出。
    用 utf-8-sig 解码：兼容带 BOM 的 JSON（Windows 侧上游导出常见），
    不带 BOM 时行为与 utf-8 一致——否则会把可用的输入误判成"不可用"并把流程判成 blocked。"""
    if not os.path.exists(path):
        return None, 'not_found'
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)


def _stat(path):
    try:
        st = os.stat(path)
        return {'exists': True, 'size': st.st_size,
                'mtime': datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')}
    except Exception:
        return {'exists': False}


def _q(p):
    return '"%s"' % p


def infer_work_dir(hint_paths=()):
    """从若干路径（--extracted / --fill-plan / --proofs-index / docx 等）推断 work_dir。
    优先返回**含标志文件**的目录；否则返回第一个有效路径的所在目录。"""
    cands = []
    for p in hint_paths:
        if not p:
            continue
        d = os.path.dirname(os.path.abspath(str(p)))
        if d and d not in cands:
            cands.append(d)
    for d in cands:
        if any(os.path.exists(os.path.join(d, m)) for m in MARKERS):
            return d
    # 再往上一层找（如 fill_plan 放在 work_dir/plans/ 的情况）
    for d in cands:
        parent = os.path.dirname(d)
        if parent and parent != d and any(os.path.exists(os.path.join(parent, m)) for m in MARKERS):
            return parent
    return cands[0] if cands else os.getcwd()


def dig(obj, path):
    """按 'a.b[0].c' 取嵌套值；任一层缺失返回 None。"""
    cur = obj
    for token in path.replace('[', '.[').split('.'):
        if not token:
            continue
        if token.startswith('['):
            try:
                i = int(token[1:-1])
            except ValueError:
                return None
            if not isinstance(cur, list) or i >= len(cur):
                return None
            cur = cur[i]
        else:
            if not isinstance(cur, dict) or token not in cur:
                return None
            cur = cur[token]
        if cur is None:
            return None
    return cur


def _is_empty(v):
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip() or v.strip() in ('null', 'N/A', '待填写')
    if isinstance(v, (list, dict)):
        return len(v) == 0
    return False


def _count_prov(node, stats):
    """递归统计"带值的数据对象"中有多少带齐了溯源字段（正文括注原料）。"""
    if isinstance(node, dict):
        has_data = any(not k.startswith('_') and not isinstance(v, (dict, list))
                       and not _is_empty(v) for k, v in node.items())
        if has_data:
            stats['data_objects'] += 1
            if all(not _is_empty(node.get(k)) for k in PROV_KEYS):
                stats['with_prov'] += 1
            elif not _is_empty(node.get('_source')):
                stats['source_only'] += 1
        for v in node.values():
            _count_prov(v, stats)
    elif isinstance(node, list):
        for v in node:
            _count_prov(v, stats)


# ---------------------------------------------------------------- 输入体检

def check_inputs(work_dir):
    """第一步交接校验的现算实现（只读、不阻断）。

    返回 dict：extracted_data 是否可用、关键字段非空情况、溯源字段覆盖率、
    结构化数据源是否有值、**第四章 15 张表逐表数据源是否有值**、proofs_index 是否就位、
    以及给用户的 gap 清单。
    """
    wd = os.path.abspath(work_dir or '.')
    ed_path = os.path.join(wd, 'extracted_data.json')
    pi_path = os.path.join(wd, 'proofs_index.json')
    data, err = _load_json(ed_path)
    info = {
        'work_dir': wd,
        'extracted_data_path': ed_path,
        'extracted_data_ok': data is not None,
        'extracted_data_error': err,
        'extracted_data_kb': round(_stat(ed_path).get('size', 0) / 1024.0, 1),
        'proofs_index_ok': os.path.exists(pi_path),
        'key_fields_filled': [], 'key_fields_missing': [],
        'struct_sources_ok': [], 'struct_sources_missing': [],
        'ch4_tables_ok': [], 'ch4_tables_missing': [],
        'prov_stats': {'data_objects': 0, 'with_prov': 0, 'source_only': 0},
        'prov_pct': 0.0,
        'gaps': [],
    }
    if data is None:
        info['gaps'].append(
            'extracted_data.json 不可用（%s）——本 SKILL 以它为唯一数据源，'
            '请上游提取方提供（结构见 templates/extracted_data_schema.json）' % err)
        return info
    if not isinstance(data, dict):
        info['extracted_data_ok'] = False
        info['gaps'].append('extracted_data.json 顶层不是对象，结构不符合 extracted_data_schema.json')
        return info

    for f in KEY_FIELDS:
        (info['key_fields_missing'] if _is_empty(dig(data, f))
         else info['key_fields_filled']).append(f)
    for path, why in STRUCT_SOURCES:
        (info['struct_sources_ok'] if not _is_empty(dig(data, path))
         else info['struct_sources_missing']).append({'field': path, 'used_by': why})
    for name, path, optional, src in CH4_TABLE_SOURCES:
        item = {'table': name, 'field': path, 'optional': optional, 'from_material': src}
        (info['ch4_tables_ok'] if not _is_empty(dig(data, path))
         else info['ch4_tables_missing']).append(item)

    _count_prov(data, info['prov_stats'])
    n = info['prov_stats']['data_objects'] or 1
    info['prov_pct'] = round(100.0 * info['prov_stats']['with_prov'] / n, 1)

    if not info['proofs_index_ok']:
        info['gaps'].append(
            'proofs_index.json 缺失——第三步「附件编号真实性」校验依赖它，'
            '请上游提取方一并提供')
    if info['key_fields_missing']:
        info['gaps'].append('关键字段为空 %d 个：%s（为空处只能写占位符，需向用户说明）'
                            % (len(info['key_fields_missing']),
                               '、'.join(info['key_fields_missing'])))
    if info['struct_sources_missing']:
        info['gaps'].append('结构化数据源缺 %d 处：%s'
                            % (len(info['struct_sources_missing']),
                               '；'.join('%s → %s' % (x['field'], x['used_by'])
                                        for x in info['struct_sources_missing'])))
    must = [x for x in info['ch4_tables_missing'] if not x['optional']]
    if must:
        info['gaps'].append(
            '第四章 %d/%d 张表的数据源为空（%s）—— 这些表**整表不会生成**，而 ch4 子agent '
            '按 guide 会在正文写出全部 15 个表标题段 → 交付稿必然出现「有表标题、没有表格实体」'
            '的悬空引用。若缺的是 valuation_params.* ，典型根因是**评估报告只提取了前几页摘要**'
            '（现金流预测表/运营费用参数/资本性支出明细/可比实例/客户财务数据都在后面几十页）——'
            '请上游把评估报告读全后补录；补不了则必须在正文删掉对应表标题与「下表列示…」引用'
            % (len(must), len(CH4_TABLE_SOURCES),
               '；'.join('%s ← %s（%s）' % (x['table'], x['field'], x['from_material'])
                        for x in must)))
    if info['prov_pct'] < 50:
        info['gaps'].append(
            '溯源字段覆盖率仅 %.1f%%（%d/%d 个数据对象带齐 _attachment_no+_doc_name）——'
            '第三步「来源标注覆盖率」门槛为 50%%，低于此值交付校验会 FAIL，'
            '请上游补溯源字段（取不到的写 pending，不得编造）'
            % (info['prov_pct'], info['prov_stats']['with_prov'],
               info['prov_stats']['data_objects']))
    return info


# ---------------------------------------------------------------- 越界产物体检

# 文件名里的章号标记（要求 ch 前面不是字母，避免 "arch4" 之类误判）
_CH_IN_NAME = re.compile(r'(?<![a-zA-Z])ch(\d)', re.I)


def _ch_of(name):
    """从文件名里取章号；取不到返回 None。"""
    m = _CH_IN_NAME.search(os.path.splitext(os.path.basename(name))[0])
    return int(m.group(1)) if m else None


def check_stray(work_dir, docx_names):
    """扫描 work_dir 里的**越界产物**（只读、不阻断、exit 不受影响）。

    子agent的合法交付物只有 `fill_plan_ch{N}.json` + 可选 `ch{N}_tables.md`（N∈1/2/4/5/6）。
    实测事故：ch4 子agent自行跑 fill_docx.py 落盘 step_ch4/_v2/_v3.docx 并跑 validate_output.py
    自校验（并行期跑出的结论必然失真），还顺手产出了 fill_plan_ch7.json；主agent若把这些 docx
    当填充基底，会把与其它章互斥的写入一路带到交付。本体检把"人肉识别再丢弃"变成脚本自动报出。

    返回 dict：
      stray_files   —— **明确**越界（别章的 fill_plan / 表格 md / docx），一律丢弃
      suspect_docx  —— **疑似**子agent落盘的 docx（带判据，需主agent确认，可能误报）
      docx_timeline —— work_dir 内全部 docx 按 mtime 旧→新排序
      head_candidate—— 最新的**非可疑** docx（基底参考，不是结论）
    """
    wd = os.path.abspath(work_dir or '.')
    res = {'stray_files': [], 'suspect_docx': [], 'docx_timeline': [],
           'newest_docx': None, 'head_candidate': None, 'warnings': []}

    # ---- ① 明确越界：不在撰写范围的章的 fill_plan / 表格 md ----
    for pattern, kind in (('fill_plan_ch*.json', 'fill_plan'), ('ch*_tables.md', '表格 md')):
        for path in sorted(glob.glob(os.path.join(wd, pattern))):
            name = os.path.basename(path)
            n = _ch_of(name)
            if n is None or n in LEGIT_CHAPTERS:
                continue
            why = ('第%d章由业务人工维护、不委派子agent' % n) if n in MANUAL_CHAPTERS \
                else ('第%d章不在撰写范围（只撰写一/二/四/五/六章）' % n)
            res['stray_files'].append({
                'file': name, 'kind': kind, 'why': why,
                'action': '丢弃不用，⛔ 不得进应用链；交付后随中间产物一并清理'})

    # ---- ② docx 时间线 ----
    infos = []
    for name in docx_names:
        st = _stat(os.path.join(wd, name))
        infos.append({'file': name, 'mtime': st.get('mtime'),
                      'size': st.get('size', 0), 'chapter': _ch_of(name)})
    infos.sort(key=lambda x: (x['mtime'] or ''))
    res['docx_timeline'] = infos

    suspect = {}

    def _mark(fname, reason):
        suspect.setdefault(fname, [])
        if reason not in suspect[fname]:
            suspect[fname].append(reason)

    by_ch = {}
    for it in infos:
        if it['chapter'] is not None:
            by_ch.setdefault(it['chapter'], []).append(it['file'])

    # ②-a 不在撰写范围的章的 docx：明确越界
    for n in sorted(by_ch):
        if n in LEGIT_CHAPTERS:
            continue
        for f in by_ch[n]:
            res['stray_files'].append({
                'file': f, 'kind': 'docx',
                'why': '第%d章不在撰写范围，本 SKILL 不应产出该章 docx' % n,
                'action': '丢弃不用，⛔ 绝不可当填充基底'})
            _mark(f, '第%d章不在撰写范围' % n)

    # ②-b 同章多版本：主agent逐批应用每章只产一个输出
    for n in sorted(by_ch):
        if n in LEGIT_CHAPTERS and len(by_ch[n]) >= 2:
            for f in by_ch[n]:
                _mark(f, '第%d章出现 %d 个 docx 版本（%s）——主agent逐批应用每章只产一个输出，'
                         '多版本是子agent「落盘→自校验→重写」循环的典型特征；'
                         '若确系主agent重跑该章后重新应用所产，可忽略本项'
                      % (n, len(by_ch[n]), '、'.join(by_ch[n])))

    # ②-c 跳号：逐批应用链条不可能跳过应用顺序在前的章
    for n in [c for c in APPLY_ORDER_CH if c in by_ch]:
        missing_before = [m for m in APPLY_ORDER_CH[:APPLY_ORDER_CH.index(n)] if m not in by_ch]
        if missing_before:
            for f in by_ch[n]:
                _mark(f, '存在第%d章 docx，但缺应用顺序在它之前的第%s章 docx——'
                         '主agent逐批应用（ch1→ch2→ch4→ch5→ch6）不可能跳号；'
                         '若你的各批次输出是同名覆盖，本项可忽略'
                      % (n, '、'.join(str(m) for m in missing_before)))

    res['suspect_docx'] = [{'file': f, 'reasons': suspect[f]} for f in sorted(suspect)]

    # ---- ③ 基底(HEAD)参考：最新的非可疑 docx ----
    if infos:
        res['newest_docx'] = infos[-1]['file']
        clean = [it for it in infos if it['file'] not in suspect]
        res['head_candidate'] = clean[-1]['file'] if clean else None

    # ---- ④ 结论汇总 ----
    if res['stray_files']:
        res['warnings'].append(
            '明确越界产物 %d 个：%s —— 一律丢弃、⛔ 不得进应用链。子agent的合法交付物只有 '
            'fill_plan_ch{1,2,4,5,6}.json + 对应 ch{N}_tables.md（见 SKILL.md 红线第 4 条）'
            % (len(res['stray_files']), '、'.join(x['file'] for x in res['stray_files'])))
    if suspect:
        res['warnings'].append(
            '疑似子agent越界落盘的 docx %d 个：%s —— 核对判据后**不得作为填充基底**'
            '（子agent不知道当前 HEAD 与应用顺序，其独立写入与其它章互斥、无法合并）'
            % (len(suspect), '、'.join(sorted(suspect))))
    if res['newest_docx'] and res['newest_docx'] in suspect:
        res['warnings'].append(
            '⛔ work_dir 里**最新的 docx 恰是疑似越界产物**「%s」——它的文件名/时间看起来最"新"，'
            '正是挑错基底的高发点（上下文被压缩后接手时尤甚）。基底建议取「%s」，'
            '并按逐批应用链条复核'
            % (res['newest_docx'],
               res['head_candidate'] or '（无干净候选：需回到初稿或上一批确认输出）'))
    return res


# ---------------------------------------------------------------- 状态推导

def scan_state(work_dir):
    """扫描 work_dir 现有产物，推导当前步骤、状态与下一条命令。"""
    wd = os.path.abspath(work_dir)
    inputs = check_inputs(wd)

    def ex(name):
        return os.path.exists(os.path.join(wd, name))

    artifacts = {name: _stat(os.path.join(wd, name)) for name in [
        'extracted_data.json', 'proofs_index.json', 'base_vars.json',
        'handoff_validation.json', 'table_index_map.json', 'validate_report.json',
        'layout_validation.json', CHECKPOINT_NAME]}

    gate_error = None
    try:
        assert_handoff_ready(wd)
    except HandoffGateError as exc:
        gate_error = str(exc)

    plans = {name: ex(name) for name in TABLE_PLANS + CHAPTER_PLANS + LAST_PLANS}
    table_mds = {name: ex(name) for name in CHAPTER_TABLE_MDS}
    docxs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(wd, '*.docx'))
                   if not os.path.basename(p).startswith('~$'))
    stray = check_stray(wd, docxs)

    n_ch_plans = sum(1 for n in CHAPTER_PLANS if plans[n])
    n_tbl_plans = sum(1 for n in TABLE_PLANS if plans[n])

    # ---- 判定当前步骤 ----
    if not inputs['extracted_data_ok']:
        phase, status = 'step1_input', 'blocked'
        action = ('第一步：等待输入。extracted_data.json 不可用（%s）——它是本 SKILL 的唯一数据源，'
                  '请向用户/上游提取方索取（结构契约见 templates/extracted_data_schema.json）'
                  % inputs['extracted_data_error'])
        cmd = '（无脚本命令：拿到 extracted_data.json 后重跑本命令）'
    elif gate_error:
        phase, status = 'step1_handoff', 'blocked'
        action = '第一步：严格交接门禁未通过或已过期——%s' % gate_error
        cmd = ('python %s --work-dir %s --strict'
               % (_q(os.path.join(SCRIPT_DIR, 'validate_handoff.py')), _q(wd)))
    elif n_tbl_plans == 0 and n_ch_plans == 0:
        phase, status = 'step2_fill', 'not_started'
        action = ('第一步已就位（extracted_data %.1fKB）。第二步开工：先向用户汇报输入体检的数据缺口，'
                  '用初稿作基底时先跑 align_table_index.py，再生成 phase0/1/2/5 表格 fill_plan'
                  % inputs['extracted_data_kb'])
        cmd = ('python %s --blueprint %s --extracted %s --output %s --base-vars-out %s'
               % (_q(os.path.join(SCRIPT_DIR, 'gen_phase_fill_plan.py')),
                  _q(os.path.join(SKILL_DIR, 'templates', 'phase0_blueprints.json')),
                  _q(os.path.join(wd, 'extracted_data.json')),
                  _q(os.path.join(wd, 'fill_plan_phase0.json')),
                  _q(os.path.join(wd, 'base_vars.json'))))
    elif n_ch_plans < len(CHAPTER_PLANS):
        phase, status = 'step2_fill', 'in_progress'
        missing = [n for n in CHAPTER_PLANS if not plans[n]]
        action = ('第二步：按章并行撰写正文（缺 %s）。同一条回复里把缺的章一次性并行发出，'
                  '提示词用 references/chapter_writer_prompt.md；子agent产出的 ch{N}_tables.md '
                  '由主agent用 md_table_to_fill_plan.py 转坐标'
                  % '、'.join(n.replace('fill_plan_', '').replace('.json', '') for n in missing))
        cmd = '（子agent撰写，无脚本命令）'
    elif not plans['fill_plan_annex.json'] or not plans['fill_plan_phase5.phase6.json'] \
            or not plans['fill_plan_phase4.phase7.json']:
        phase, status = 'step2_phase6', 'in_progress'
        lack = []
        if not plans['fill_plan_annex.json']:
            lack.append('fill_plan_annex.json（附件1/附件2 清理，照 fill_plan_reference.md 第七节配方生成）')
        if not plans['fill_plan_phase5.phase6.json']:
            lack.append('fill_plan_phase5.phase6.json（由 phase5 蓝图生成，表15/16/22 整表重建）')
        if not plans['fill_plan_phase4.phase7.json']:
            lack.append('fill_plan_phase4.phase7.json（由 phase4 蓝图生成，第四章 12 张新表+表4-1 重建，全局最后应用）')
        action = 'phase6：附件清理 + 结构不匹配表重建，缺 ' + '；'.join(lack) + '。**必须最后应用**'
        cmd = ('python %s --template "<上一步输出.docx>" --fill-plan %s --validate-only'
               % (_q(os.path.join(SCRIPT_DIR, 'fill_docx.py')),
                  _q(os.path.join(wd, 'fill_plan_annex.json'))))
    elif not artifacts['validate_report.json']['exists']:
        phase, status = 'step3_validate', 'in_progress'
        action = '第三步：章节完备性检查 + 汇报（不足则重跑对应章子agent）'
        cmd = ('python %s "<输出.docx>" --chapters-only --proofs-index %s --work-dir %s'
               % (_q(os.path.join(SCRIPT_DIR, 'validate_output.py')),
                  _q(os.path.join(wd, 'proofs_index.json')), _q(wd)))
    elif not artifacts['layout_validation.json']['exists']:
        phase, status = 'step4_layout', 'in_progress'
        action = '第四步：统一Word版式并运行结构版式校验，随后必须渲染逐页视觉检查'
        cmd = ('python %s --input "<编号完成.docx>" --output "<成稿_formatted.docx>" --profile reits'
               % _q(os.path.join(SCRIPT_DIR, 'normalize_docx_style.py')))
    else:
        phase, status = 'step3_validate', 'completed'
        action = '内容与版式结构报告已存在：确认两者PASS并完成逐页渲染视觉检查后交付'
        cmd = ('python %s "<文件或目录>"   # dry-run 预览'
               % _q(os.path.join(SCRIPT_DIR, 'purge_file.py')))

    return {
        '_generated_by': 'scripts/pipeline_state.py',
        '_generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        '_warning': ('本文件由 pipeline_state.py 从 work_dir 实际产物**推导生成**，禁止手工编辑；'
                     'agent 自报的进度不作为依据。随时可重跑本脚本重建。'),
        'work_dir': wd,
        'skill_dir': SKILL_DIR,
        'scope': 'content_filling_only',
        'phase': phase,
        'status': status,
        'next_action': action,
        'next_command': cmd,
        'inputs': inputs,
        'artifacts': artifacts,
        'fill_plans': plans,
        'chapter_table_mds': table_mds,
        'docx_in_work_dir': docxs,
        'stray': stray,
    }


def write_checkpoint(state, path=None):
    path = path or os.path.join(state['work_dir'], CHECKPOINT_NAME)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        print('WARNING: checkpoint 写入失败：%s' % e, file=sys.stderr)
        return None


def _print_stray(stray):
    """打印越界产物体检（只诊断、不阻断）。"""
    suspect_names = {x['file'] for x in stray['suspect_docx']}
    print('--- 越界产物体检（子agent只应产 fill_plan_ch{N}.json + ch{N}_tables.md，N∈1/2/4/5/6）---')
    if not stray['stray_files'] and not suspect_names:
        print('  [OK] 未发现越界产物')
    for x in stray['stray_files']:
        print('  [越界] %-30s %s' % (x['file'], x['why']))
        print('         → %s' % x['action'])
    for x in stray['suspect_docx']:
        print('  [疑似] %s' % x['file'])
        for r in x['reasons']:
            print('         - %s' % r)
    if stray['docx_timeline']:
        print('  work_dir 内 docx（按修改时间 旧→新）:')
        for it in stray['docx_timeline']:
            print('    %s  %8.0fKB  %s%s'
                  % (it['mtime'] or '-', (it['size'] or 0) / 1024.0, it['file'],
                     '   ⚠ 疑似越界' if it['file'] in suspect_names else ''))
        print('  基底(HEAD)参考: %s'
              % (stray['head_candidate'] or '（无干净候选）'))
        print('    ↑ 仅为"最新的非可疑 docx"，**不是结论**——最终以你的逐批应用链条为准')
    for w in stray['warnings']:
        print('  ⚠ %s' % w)


def print_state(state):
    inp = state['inputs']
    plans_done = [n for n, v in state['fill_plans'].items() if v]
    plans_lack = [n for n, v in state['fill_plans'].items() if not v]
    mds_done = [n for n, v in state['chapter_table_mds'].items() if v]

    print('=== 填充流水线状态（work_dir: %s）===' % state['work_dir'])
    print('步骤/状态: %s / %s' % (state['phase'], state['status']))
    print('')
    print('--- 第一步：输入交接校验（现算，只读不阻断）---')
    print('  %s extracted_data.json（%.1fKB）%s'
          % ('[OK]' if inp['extracted_data_ok'] else '[--]', inp['extracted_data_kb'],
             '' if inp['extracted_data_ok'] else '  ← %s' % inp['extracted_data_error']))
    print('  %s proofs_index.json（附件编号真实性校验 + 附件1目录用）'
          % ('[OK]' if inp['proofs_index_ok'] else '[--]'))
    if inp['extracted_data_ok']:
        print('  关键字段: 已填 %d / 缺 %d%s'
              % (len(inp['key_fields_filled']), len(inp['key_fields_missing']),
                 ('  ← ' + '、'.join(inp['key_fields_missing'])) if inp['key_fields_missing'] else ''))
        print('  结构化数据源: 有 %d 处 / 缺 %d 处'
              % (len(inp['struct_sources_ok']), len(inp['struct_sources_missing'])))
        for x in inp['struct_sources_missing']:
            print('      [--] %-46s → %s' % (x['field'], x['used_by']))
        n_ok = len(inp.get('ch4_tables_ok') or [])
        n_all = n_ok + len(inp.get('ch4_tables_missing') or [])
        if n_all:
            print('  第四章表格数据源: 可生成 %d/%d 张' % (n_ok, n_all))
            for x in (inp.get('ch4_tables_missing') or []):
                print('      [%s] %-30s ← %s（取自%s）'
                      % ('可不涉及' if x['optional'] else '  --  ',
                         x['table'], x['field'], x['from_material']))
        print('  溯源字段覆盖率: %.1f%%（%d/%d 个数据对象带齐 _attachment_no+_doc_name；'
              '仅有 _source 的 %d 个）'
              % (inp['prov_pct'], inp['prov_stats']['with_prov'],
                 inp['prov_stats']['data_objects'], inp['prov_stats']['source_only']))
    if inp['gaps']:
        print('')
        print('  ⚠ 数据缺口（**第二步开工前必须逐条告知用户**，缺口处只能占位符，严禁编造）：')
        for g in inp['gaps']:
            print('    - %s' % g)
    print('')
    print('--- 第二步 fill_plan ---')
    print('  已生成(%d): %s' % (len(plans_done), '、'.join(plans_done) or '无'))
    print('  未生成(%d): %s' % (len(plans_lack), '、'.join(plans_lack) or '无'))
    print('  子agent表格 md(%d): %s' % (len(mds_done), '、'.join(mds_done) or '无'))
    print('')
    _print_stray(state['stray'])
    print('')
    print('--- 下一步该做什么 ---')
    print('  %s' % state['next_action'])
    print('  命令: %s' % state['next_command'])
    if state['stray']['warnings']:
        print('  ⚠ 另需先处置越界产物（见上）：可疑 docx 不得当基底，越界 fill_plan 不得进应用链')


def main():
    ap = argparse.ArgumentParser(
        description='填充流水线状态自检 / 恢复检查点（只读，exit 恒为0）')
    ap.add_argument('--work-dir', default=None,
                    help='工作目录（含 extracted_data.json）；不给则按当前目录推断')
    ap.add_argument('--extracted', default=None, help='extracted_data.json 路径（用于推断 work_dir）')
    ap.add_argument('--json', default=None,
                    help='另存一份状态 JSON 到该路径（默认只写 <work_dir>/checkpoint.json）')
    ap.add_argument('--quiet', action='store_true', help='只写文件不打印')
    args = ap.parse_args()

    wd = args.work_dir or infer_work_dir([args.extracted])
    state = scan_state(wd)
    write_checkpoint(state)
    if args.json:
        write_checkpoint(state, args.json)
    if not args.quiet:
        print_state(state)
        print('')
        print('checkpoint 已写入: %s' % os.path.join(state['work_dir'], CHECKPOINT_NAME))
        print('（该文件是脚本推导产物，禁止手工编辑；随时重跑本命令即可重建）')
    sys.exit(0)


if __name__ == '__main__':
    main()
