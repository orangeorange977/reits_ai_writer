#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
流水线状态自检 + 阶段B覆盖率硬联锁（抗上下文压缩的核心组件）。

两个用途：

【用途1｜CLI：恢复检查点】
    python pipeline_state.py --work-dir <work_dir>
  扫描 work_dir 里**实际存在的产物**，推导「当前在第几步 / 覆盖率多少 / 下一条该跑什么命令」，
  并把结论写入 <work_dir>/checkpoint.json。会话被压缩、中断、换人接手后，跑这一条即可完全
  恢复进度认知，不依赖上下文里的任何记忆。exit 恒为 0（只读诊断，不阻断）。

【用途2｜被其他脚本 import：硬联锁】
  gen_phase_fill_plan.py / fill_docx.py / validate_output.py 调用 coverage_gate()，
  阶段B覆盖率未达标就**拒绝执行（exit=3）**，把"不得进入第四步"从 agent 自觉变成退出码。

设计要点（为什么不做成"agent 手写状态文件"）：
  checkpoint.json 是**推导产物，不是真相**，随时可由本脚本重建，禁止手工编辑。
  `coverage_passed` 一律现算：分母取 proofs_index.json 的 material_index，
  分子取 extracted_data.json 的 `_metadata.read_items ∪ 全文 _source`（复用
  check_extraction_coverage.build_coverage 的双重核验逻辑）。
  **另含核心材料页级门槛**（复用 check_extraction_coverage.enrich_core_pages）：审计/评估/
  估值/法律意见书/营业执照/不动产权证必须**整份读完**（全部页图 + txt 单元都有已读证据），
  且阶段A 不能漏渲（页图数 < PDF总页数 视为未读完，next_command 会改推阶段A补渲命令）。
  这一条防的是"读了评估报告前3页摘要就 PASS"——实测导致表4-4~4-15 全是空表。
  这样即使 extraction_coverage.json 缺失/过期/被改，门槛判定也不会被自报数据糊弄。

【门槛参数下限保护（红线第5条）】
  除了"分子分母现算"，**尺子本身也受保护**：阈值只允许调高（加严）。任何来源给出 <80 的阈值
  （命令行 --threshold、extraction_coverage.json 的 threshold_pct）都会被抬回 THRESHOLD_FLOOR，
  并记入 checkpoint.json 的 `threshold_tampering`；`--critical-keywords` 删关键词同样只会被
  并回默认集。报告里的 `pass` 字段一律不采信（可能是用被调低的阈值算出来的）。
  唯一合法的越权通道仍是 --force-low-coverage（记 gate_bypasses + 第五步必 FAIL）。

【提取域模式（子skill reits-proof-extractor）】
  本文件在主skill与子skill目录下**保持字节一致**（同一实现，避免口径漂移）。运行时按
  「同目录是否存在 gen_phase_fill_plan.py」自动判断作用域：
    - 存在（主skill）  → 全流程模式，阶段推导覆盖第2~5步，行为与历史完全一致；
    - 不存在（子skill）→ 提取域模式，阶段推导在"阶段B覆盖率达标"处收口为产物交接，
                         不再打印指向不存在脚本的第四步命令。
  两种模式的覆盖率现算逻辑、阈值下限保护、留痕字段完全相同。
"""

import argparse
import datetime
import glob
import json
import os
import sys

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

# 复用覆盖率计算逻辑（唯一实现，避免两套口径漂移）
try:
    from check_extraction_coverage import (
        build_coverage, DEFAULT_CATALOG, DEFAULT_GROUPS, DEFAULT_CRITICAL_KEYWORDS,
        build_page_evidence, enrich_core_pages)
    _HAS_COVERAGE_LIB = True
except Exception as _e:      # pragma: no cover
    _HAS_COVERAGE_LIB = False
    _COVERAGE_IMPORT_ERR = str(_e)
    DEFAULT_CRITICAL_KEYWORDS = ['审计报告', '评估报告', '估值报告',
                                 '营业执照', '不动产权证', '法律意见书']

CHECKPOINT_NAME = 'checkpoint.json'
DEFAULT_THRESHOLD = 80.0

# 【门槛参数下限保护】覆盖率阈值只允许**调高**（加严），任何来源（命令行 --threshold、
# extraction_coverage.json 的 threshold_pct）给出低于本值的阈值一律被抬回本值，并记为篡改。
# 原因：三处硬联锁都调用 compute_coverage(threshold=None)，历史上会**继承报告里的
# threshold_pct**——只要跑一次 `--threshold 10` 把 10 写进报告，第四步两个脚本与第五步复核
# 就会集体按 10% 判 PASS，且 gate_bypasses 里一条记录都没有（比 --force-low-coverage 更隐蔽）。
# 所以尺子本身必须受保护：现算的是分子分母，下限锁的是尺子。
THRESHOLD_FLOOR = 80.0

# work_dir 识别标志文件（任一存在即认为是本 SKILL 的工作目录）
MARKERS = ('proofs_index.json', 'extracted_data.json', 'extraction_coverage.json',
           CHECKPOINT_NAME)

CHAPTER_PLANS = ['fill_plan_ch%d.json' % n for n in range(2, 8)]
TABLE_PLANS = ['fill_plan_phase0.json', 'fill_plan_phase1.json',
               'fill_plan_phase2.json', 'fill_plan_phase5.json']
LAST_PLANS = ['fill_plan_annex.json', 'fill_plan_phase5.phase6.json']

# 【作用域自动判定】同目录有无第四步脚本 → 全流程模式 / 提取域模式（子skill）。
# 主skill目录必然存在 gen_phase_fill_plan.py，因此主skill行为与历史完全一致。
EXTRACTION_ONLY = not os.path.exists(os.path.join(SCRIPT_DIR, 'gen_phase_fill_plan.py'))

# 提取域（步骤1~3）的交接产物清单：子skill跑完后交给主skill第四步的全部文件
HANDOFF_ARTIFACTS = ('proofs_index.json', 'missing_materials.json', 'extracted_data.json',
                     'extraction_coverage.json', 'specialized_extraction_validation.json',
                     'handoff_validation.json', CHECKPOINT_NAME)


# ---------------------------------------------------------------- 基础工具

def _load_json(path):
    """读 JSON，任何异常（不存在/损坏/编码）都返回 None，不抛出。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _stat(path):
    try:
        st = os.stat(path)
        return {'exists': True, 'size': st.st_size,
                'mtime': datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')}
    except Exception:
        return {'exists': False}


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except Exception:
        return None


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


# ---------------------------------------------------------------- 覆盖率现算

def compute_coverage(work_dir, threshold=None, critical_keywords=None):
    """现算阶段B覆盖率。返回统一结构，绝不抛异常。

    scope 取值：
      no_pipeline  —— 目录里没有任何流水线产物（可能不是 work_dir）→ 门槛不适用
      no_extracted —— 有 proofs_index 但没有 extracted_data → 阶段B尚未开始
      live         —— 现算成功（最可信）
      report       —— 无法现算（缺 proofs_index 或计算库不可用），退回读 extraction_coverage.json
      unknown      —— 既不能现算也没有报告 → 视为未校验
    """
    idx_path = os.path.join(work_dir, 'proofs_index.json')
    ext_path = os.path.join(work_dir, 'extracted_data.json')
    rep_path = os.path.join(work_dir, 'extraction_coverage.json')

    idx = _load_json(idx_path)
    ext = _load_json(ext_path)
    rep = _load_json(rep_path)

    thr_req = threshold
    thr_src = 'arg' if thr_req is not None else None
    if thr_req is None and isinstance(rep, dict):
        try:
            thr_req = float(rep.get('threshold_pct'))
            thr_src = 'report'
        except (TypeError, ValueError):
            thr_req = None
    if thr_req is None:
        thr_req, thr_src = DEFAULT_THRESHOLD, 'default'

    # 只允许调高：低于下限的阈值一律抬回下限，并标记为"门槛参数被调低"
    thr = max(thr_req, THRESHOLD_FLOOR)
    thr_lowered = thr_req < THRESHOLD_FLOOR

    # 核心材料关键词同理：只允许**加严**（并入默认集），不允许靠 --critical-keywords 删关键词
    # 来绕过"核心材料100%已读"——这是与调低 --threshold 同类的绕过手法。
    kws_req = list(critical_keywords or [])
    if not kws_req and isinstance(rep, dict) and rep.get('critical_keywords'):
        kws_req = list(rep.get('critical_keywords'))
    kws = list(DEFAULT_CRITICAL_KEYWORDS)
    for k in kws_req:
        if k and k not in kws:
            kws.append(k)
    kws_reduced = sorted(set(DEFAULT_CRITICAL_KEYWORDS) - set(kws_req)) if kws_req else []

    info = {
        'scope': 'unknown',
        'passed': False,
        'work_dir': os.path.abspath(work_dir),
        'threshold_pct': thr,
        'threshold_requested_pct': thr_req,
        'threshold_source': thr_src,
        'threshold_floor_pct': THRESHOLD_FLOOR,
        'threshold_lowered': thr_lowered,
        'critical_keywords_dropped': kws_reduced,
        'coverage_pct': 0.0,
        'read_files': 0,
        'total_files': 0,
        'unread_count': 0,
        'critical_unread': [],
        'core_page_unread': [],
        'core_page_stats': {},
        'core_render_gaps': [],
        'suspicious_count': 0,
        'read_items_claimed': 0,
        'report_exists': isinstance(rep, dict),
        'report_pass': bool(rep.get('pass')) if isinstance(rep, dict) else None,
        'report_stale': False,
        'proofs_index': idx_path if os.path.exists(idx_path) else None,
        'extracted': ext_path if os.path.exists(ext_path) else None,
        'report': rep_path if os.path.exists(rep_path) else None,
        'note': '',
    }

    # extracted 比覆盖率报告新 → 报告过期（仅作提示，判定一律以现算为准）
    m_ext, m_rep = _mtime(ext_path), _mtime(rep_path)
    if m_ext and m_rep and m_ext > m_rep + 1:
        info['report_stale'] = True

    def add_note(text):
        info['note'] = (info['note'] + ' ' if info['note'] else '') + text

    if thr_lowered:
        add_note('⚠️ 检测到门槛参数被调低（%s 给出 %s%%，低于下限 %s%%）——已按下限判定，调低无效。'
                 % ({'arg': '命令行 --threshold',
                     'report': 'extraction_coverage.json 的 threshold_pct'}.get(thr_src, thr_src),
                    thr_req, THRESHOLD_FLOOR))
    if kws_reduced:
        add_note('⚠️ 核心材料关键词被删减（缺 %s）——已并回默认集，删减无效。' % '/'.join(kws_reduced))

    if isinstance(ext, dict):
        meta = ext.get('_metadata')
        if isinstance(meta, dict) and isinstance(meta.get('read_items'), list):
            info['read_items_claimed'] = len(meta['read_items'])

    # 目录里什么都没有 → 不是 work_dir，门槛不适用
    if idx is None and ext is None and rep is None:
        info['scope'] = 'no_pipeline'
        add_note('目录内无 proofs_index/extracted_data/extraction_coverage，判定为非本流水线工作目录')
        return info

    if idx is not None and ext is None:
        info['scope'] = 'no_extracted'
        add_note('已扫描材料但 extracted_data.json 不存在 —— 阶段B尚未开始')
        try:
            info['total_files'] = sum(len(v) for v in (idx.get('material_index') or {}).values())
        except Exception:
            pass
        return info

    if _HAS_COVERAGE_LIB and isinstance(idx, dict) and isinstance(ext, dict):
        catalog = _load_json(DEFAULT_CATALOG) or {}
        groups = _load_json(DEFAULT_GROUPS) or {}
        try:
            r = build_coverage(idx, ext, catalog, groups, kws)
            # 【核心材料页级门槛】与 check_extraction_coverage 同源现算：核心材料（审计/评估/
            # 估值/法律意见书/营业执照/不动产权证）必须**整份读完**（全部页图+txt 单元），
            # 且阶段A 不能漏渲。防"读了评估报告前3页摘要就 PASS → 表4-4~4-15 全空"。
            core_unread, core_stats, core_gaps = [], {}, []
            try:
                page_pairs, txt_reads = build_page_evidence(ext)
                enrich_core_pages(r, work_dir, kws, page_pairs, txt_reads)
                core_unread = r.get('core_page_unread') or []
                core_stats = r.get('core_page_stats') or {}
                core_gaps = r.get('core_render_gaps') or []
            except Exception as e:
                add_note('核心材料页级门槛现算失败(%s)，本次仅按文件级判定' % e)
            info.update({
                'scope': 'live',
                'coverage_pct': r['coverage_pct'],
                'read_files': r['read_files'],
                'total_files': r['total_files'],
                'unread_count': len(r['unread']),
                'critical_unread': r['critical_unread'],
                'core_page_unread': [u.get('file') for u in core_unread],
                'core_page_stats': core_stats,
                'core_render_gaps': core_gaps,
                'suspicious_count': len(r['suspicious_self_report_only']),
            })
            info['passed'] = (r['total_files'] > 0
                              and r['coverage_pct'] >= thr
                              and not r['critical_unread']
                              and not core_unread)
            if core_unread:
                add_note('核心材料未整份读完（页级门槛）：%d 份 —— 评估/审计报告的现金流预测表、'
                         '运营费用参数、资本性支出、可比实例都在报告中后部，未读完则表4-1~4-15 '
                         '必然大面积空表。%s'
                         % (len(core_unread),
                            '其中 %d 份是阶段A漏渲（页图不在盘上），需先重跑 batch_render_pdfs.py。'
                            % len(core_gaps) if core_gaps else ''))
            if r['total_files'] == 0:
                add_note('proofs_index 的 material_index 为空（0 份材料），不以空索引放行')
            return info
        except Exception as e:
            add_note('现算失败(%s)，退回读 extraction_coverage.json' % e)

    if isinstance(rep, dict):
        info.update({
            'scope': 'report',
            'coverage_pct': rep.get('coverage_pct', 0.0),
            'read_files': rep.get('read_files', 0),
            'total_files': rep.get('total_files', 0),
            'unread_count': len(rep.get('unread') or []),
            'critical_unread': rep.get('critical_unread') or [],
            'core_page_unread': [u.get('file') if isinstance(u, dict) else u
                                 for u in (rep.get('core_page_unread') or [])],
            'core_page_stats': rep.get('core_page_stats') or {},
            'core_render_gaps': rep.get('core_render_gaps') or [],
            'suspicious_count': len(rep.get('suspicious_self_report_only') or []),
        })
        # 【不信任报告的 pass 字段】报告里的 pass 可能是用被调低的阈值算出来的，
        # 这里一律用受下限保护的阈值重算一遍（分子分母仍取报告值，因为现算不可用）。
        try:
            rep_cov = float(info['coverage_pct'])
        except (TypeError, ValueError):
            rep_cov = 0.0
        info['passed'] = (bool(rep.get('pass'))
                          and info['total_files'] > 0
                          and rep_cov >= thr
                          and not info['critical_unread']
                          and not info['core_page_unread'])
        add_note('无法现算（缺 proofs_index 或计算库不可用），本次采用报告值（pass 字段不采信，'
                 '按阈值 %s%% 重算）%s' % (thr, '；且报告已过期（extracted_data 更新在后）'
                                          if info['report_stale'] else ''))
        return info

    info['scope'] = 'unknown'
    add_note('既无法现算也没有 extraction_coverage.json —— 视为覆盖率校验从未执行')
    return info


def coverage_command(work_dir):
    return ('python %s --proofs-index %s --extracted %s --output %s'
            % (_q(os.path.join(SCRIPT_DIR, 'check_extraction_coverage.py')),
               _q(os.path.join(work_dir, 'proofs_index.json')),
               _q(os.path.join(work_dir, 'extracted_data.json')),
               _q(os.path.join(work_dir, 'extraction_coverage.json'))))


def next_batch_command(work_dir, n=8):
    """阶段B批次驱动命令（**页级配额**）：打印进度 + 下一批该读的 n 张页图完整路径。
    用 --next-pages 而不是 --next：批次单位是"张"而非"份"，一份上百页的审计/评估报告
    不会独占一轮，主agent才能真的在一条消息里并行读完。"""
    return ('python %s --proofs-index %s --extracted %s --next-pages %d'
            % (_q(os.path.join(SCRIPT_DIR, 'check_extraction_coverage.py')),
               _q(os.path.join(work_dir, 'proofs_index.json')),
               _q(os.path.join(work_dir, 'extracted_data.json')), n))


def mark_batch_command(work_dir, n=8):
    """阶段B「登记上一轮 + 取下一批」二合一命令：进度登记由脚本代写，免手工编辑大 JSON。"""
    return ('python %s --proofs-index %s --extracted %s --mark-batch --next-pages %d'
            % (_q(os.path.join(SCRIPT_DIR, 'check_extraction_coverage.py')),
               _q(os.path.join(work_dir, 'proofs_index.json')),
               _q(os.path.join(work_dir, 'extracted_data.json')), n))



# ---------------------------------------------------------------- 硬联锁

def coverage_gate(work_dir=None, hint_paths=(), force=False, stage='',
                  threshold=None, exit_code=3):
    """阶段B覆盖率硬联锁：不达标直接 sys.exit(exit_code)，把纪律变成退出码。

    force=True 时放行，但会在 checkpoint.json 的 gate_bypasses 里**留痕**，
    第五步全量校验会据此 FAIL，交付汇报必须列明。
    返回 coverage info（放行时）。
    """
    wd = work_dir or infer_work_dir(hint_paths)
    info = compute_coverage(wd, threshold=threshold)
    label = '[覆盖率门槛]' + (' %s' % stage if stage else '')

    # 门槛参数被调低/核心关键词被删减 → 已按下限判定（调低无效），但必须留痕并喊出来
    if info.get('threshold_lowered') or info.get('critical_keywords_dropped'):
        print('%s ⚠️⚠️ %s' % (label, info['note']), file=sys.stderr)
        print('%s    红线：严禁篡改门槛参数绕过校验。门槛只允许调高（加严）；确有正当理由请用 '
              '--force-low-coverage（留痕 + 第五步必 FAIL），不要动阈值。' % label, file=sys.stderr)
        record_threshold_tamper(wd, stage, info)

    if info['scope'] == 'no_pipeline':
        print('%s ⚠️ 跳过：%s（work_dir=%s）。如非预期请用 --work-dir 显式指定工作目录。'
              % (label, info['note'], wd), file=sys.stderr)
        return info

    if info['passed']:
        extra = '（现算）' if info['scope'] == 'live' else '（报告值）'
        core = info.get('core_page_stats') or {}
        print('%s ✅ 通过%s：%s/%s = %s%%（阈值 %s%%），核心材料已读完%s'
              % (label, extra, info['read_files'], info['total_files'],
                 info['coverage_pct'], info['threshold_pct'],
                 '且已整份读完（页级 %s/%s 单元）'
                 % (core.get('read_units'), core.get('total_units')) if core.get('files') else ''))
        if info['suspicious_count']:
            print('%s ⚠️ 另有 %d 份仅自报已读、无 _source 佐证，建议复核'
                  % (label, info['suspicious_count']))
        return info

    # ---- 未达标：打印可行动信息 ----
    print('', file=sys.stderr)
    print('=' * 72, file=sys.stderr)
    print('❌ %s 阶段B数据提取覆盖率未达标 —— 拒绝执行本步骤（exit=%d）'
          % (label, exit_code), file=sys.stderr)
    print('=' * 72, file=sys.stderr)
    print('  work_dir       : %s' % wd, file=sys.stderr)
    if info['scope'] == 'no_extracted':
        print('  状态           : 阶段B尚未开始（无 extracted_data.json）', file=sys.stderr)
        if info['total_files']:
            print('  待读材料文件数 : %d' % info['total_files'], file=sys.stderr)
    elif info['scope'] == 'unknown':
        print('  状态           : 覆盖率校验从未执行（无报告且无法现算）', file=sys.stderr)
    else:
        print('  覆盖率         : %s/%s = %s%%（阈值 %s%%）%s'
              % (info['read_files'], info['total_files'], info['coverage_pct'],
                 info['threshold_pct'], '（现算）' if info['scope'] == 'live' else '（报告值）'),
              file=sys.stderr)
        print('  未读文件       : %d 份' % info['unread_count'], file=sys.stderr)
        if info['critical_unread']:
            print('  核心材料未读   : %d 份（必须100%%读完）' % len(info['critical_unread']),
                  file=sys.stderr)
            for p in info['critical_unread'][:5]:
                print('      - %s' % p, file=sys.stderr)
            if len(info['critical_unread']) > 5:
                print('      ... 及另外 %d 份' % (len(info['critical_unread']) - 5), file=sys.stderr)
        if info.get('core_page_unread'):
            core = info.get('core_page_stats') or {}
            print('  核心材料未读完 : %d 份未**整份读完**（页级门槛：单元 %s/%s）'
                  % (len(info['core_page_unread']), core.get('read_units'),
                     core.get('total_units')), file=sys.stderr)
            for p in info['core_page_unread'][:5]:
                print('      - %s' % p, file=sys.stderr)
            if len(info['core_page_unread']) > 5:
                print('      ... 及另外 %d 份' % (len(info['core_page_unread']) - 5), file=sys.stderr)
            if info.get('core_render_gaps'):
                print('      ⚠️ 其中 %d 份是**阶段A漏渲**（页图不在盘上，阶段B排不出来）：'
                      '先重跑 batch_render_pdfs.py 补渲（核心报告默认全量渲染，按页续渲）'
                      % len(info['core_render_gaps']), file=sys.stderr)
    if info['note']:
        print('  说明           : %s' % info['note'], file=sys.stderr)
    print('', file=sys.stderr)
    print('  ▶ 正确做法：回阶段B按**页级配额**继续读图（一轮 8 张，读完写盘 + 用 --mark-batch 登记）——', file=sys.stderr)
    print('    这一条会打印进度 + 下一批 8 张页图的**完整路径**（可直接一条消息里并行读完）：', file=sys.stderr)
    print('      %s' % next_batch_command(wd), file=sys.stderr)
    print('    读完本轮后「登记上一轮 + 取下一批」二合一：', file=sys.stderr)
    print('      %s' % mark_batch_command(wd), file=sys.stderr)
    print('    队列清空后跑门槛模式收尾，PASS 才能执行本步骤：', file=sys.stderr)
    print('      %s' % coverage_command(wd), file=sys.stderr)
    print('  ▶ 恢复进度认知（会话被压缩/中断后先跑这条）：', file=sys.stderr)
    print('      python %s --work-dir %s'
          % (_q(os.path.join(SCRIPT_DIR, 'pipeline_state.py')), _q(wd)), file=sys.stderr)
    print('', file=sys.stderr)

    if force:
        record_bypass(wd, stage, info)
        print('⚠️⚠️ 已用 --force-low-coverage 强行放行：本次越权已记入 %s 的 gate_bypasses，'
              '第五步全量校验会 FAIL，交付汇报必须向用户列明。'
              % os.path.join(wd, CHECKPOINT_NAME), file=sys.stderr)
        return info

    print('  （确有正当理由才可用 --force-low-coverage 越过，越过会留痕并在第五步 FAIL；'
          '严禁改小 --threshold 或删 --critical-keywords 来"过关"——下限保护会抬回 %s%% 并留痕）'
          % THRESHOLD_FLOOR, file=sys.stderr)
    sys.exit(exit_code)


def _update_checkpoint(work_dir, mutate):
    """读-改-写 checkpoint.json（保留其余字段）。任何异常只告警不抛出。"""
    path = os.path.join(work_dir, CHECKPOINT_NAME)
    cp = _load_json(path)
    if not isinstance(cp, dict):
        cp = {}
    mutate(cp)
    cp.setdefault('_warning', '本文件由 pipeline_state.py 推导生成，禁止手工编辑')
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cp, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('WARNING: checkpoint 留痕写入失败：%s' % e, file=sys.stderr)


def record_bypass(work_dir, stage, info):
    """把一次越权放行写入 checkpoint.json 的 gate_bypasses（事件日志，重建 checkpoint 时保留）。"""
    def mutate(cp):
        log = cp.get('gate_bypasses')
        if not isinstance(log, list):
            log = []
        log.append({
            'at': datetime.datetime.now().isoformat(timespec='seconds'),
            'kind': 'force_low_coverage',
            'stage': stage or 'unknown',
            'coverage_pct': info.get('coverage_pct'),
            'read_files': info.get('read_files'),
            'total_files': info.get('total_files'),
            'critical_unread': len(info.get('critical_unread') or []),
        })
        cp['gate_bypasses'] = log
    _update_checkpoint(work_dir, mutate)


def record_threshold_tamper(work_dir, stage, info):
    """把一次"门槛参数被调低/核心关键词被删减"写入 checkpoint.json 的 threshold_tampering。

    与 gate_bypasses 分开记：调低本身**已被下限保护抬回**（不影响判定），但属红线行为，
    第五步会据此报项，交付汇报必须列明。同一 (stage, 请求值) 只记一次，避免反复跑刷满日志。
    """
    entry = {
        'at': datetime.datetime.now().isoformat(timespec='seconds'),
        'kind': 'threshold_lowered' if info.get('threshold_lowered') else 'critical_keywords_dropped',
        'stage': stage or 'unknown',
        'requested_pct': info.get('threshold_requested_pct'),
        'effective_pct': info.get('threshold_pct'),
        'floor_pct': info.get('threshold_floor_pct'),
        'source': info.get('threshold_source'),
        'critical_keywords_dropped': info.get('critical_keywords_dropped') or [],
        'note': '门槛参数只允许调高；本次调低已被下限保护忽略，但记为红线违规',
    }

    def mutate(cp):
        log = cp.get('threshold_tampering')
        if not isinstance(log, list):
            log = []
        for e in log:
            if (e.get('stage') == entry['stage'] and e.get('kind') == entry['kind']
                    and e.get('requested_pct') == entry['requested_pct']
                    and (e.get('critical_keywords_dropped') or []) == entry['critical_keywords_dropped']):
                return
        log.append(entry)
        cp['threshold_tampering'] = log
    _update_checkpoint(work_dir, mutate)


def read_bypasses(work_dir):
    cp = _load_json(os.path.join(work_dir, CHECKPOINT_NAME))
    if isinstance(cp, dict) and isinstance(cp.get('gate_bypasses'), list):
        return cp['gate_bypasses']
    return []


def read_threshold_tampering(work_dir):
    cp = _load_json(os.path.join(work_dir, CHECKPOINT_NAME))
    if isinstance(cp, dict) and isinstance(cp.get('threshold_tampering'), list):
        return cp['threshold_tampering']
    return []


# ---------------------------------------------------------------- 状态推导

def scan_state(work_dir, threshold=None):
    """扫描 work_dir 现有产物，推导当前阶段、状态与下一条命令。"""
    wd = os.path.abspath(work_dir)
    cov = compute_coverage(wd, threshold=threshold)

    def ex(name):
        return os.path.exists(os.path.join(wd, name))

    images_dir = os.path.join(wd, 'images')
    n_img_dirs = 0
    images_has_output = False
    if os.path.isdir(images_dir):
        try:
            for e in os.scandir(images_dir):
                if e.is_dir():
                    n_img_dirs += 1
                    images_has_output = True
                elif e.name.endswith('.txt'):
                    images_has_output = True
        except Exception:
            n_img_dirs = 0

    artifacts = {name: _stat(os.path.join(wd, name)) for name in [
        'proofs_index.json', 'missing_materials.json', 'batch_render_report.json',
        '_stage_a_complete.json', 'extracted_data.json', 'extraction_coverage.json',
        'specialized_extraction_validation.json', 'handoff_validation.json',
        'base_vars.json', 'validate_report.json', CHECKPOINT_NAME]}
    artifacts['images/'] = {'exists': os.path.isdir(images_dir), 'sub_dirs': n_img_dirs}

    # 阶段A是否已完成：与 batch_render_pdfs.py 同一判据（报告/完成标记/images 已有产物任一即算）,
    # 避免报告文件被清理后误判"退回阶段A"
    stage_a_done = (artifacts['batch_render_report.json']['exists']
                    or artifacts['_stage_a_complete.json']['exists']
                    or images_has_output)

    plans = {name: ex(name) for name in TABLE_PLANS + CHAPTER_PLANS + LAST_PLANS}
    docxs = sorted(os.path.basename(p) for p in glob.glob(os.path.join(wd, '*.docx'))
                   if not os.path.basename(p).startswith('~$'))

    n_ch_plans = sum(1 for n in CHAPTER_PLANS if plans[n])
    n_tbl_plans = sum(1 for n in TABLE_PLANS if plans[n])
    specialized_report = (_load_json(
        os.path.join(wd, 'specialized_extraction_validation.json')) or {})
    specialized_mtime = artifacts['specialized_extraction_validation.json'].get('mtime')
    specialized_fresh = bool(specialized_mtime)
    if specialized_fresh:
        for source_name in ('extracted_data.json', 'proofs_index.json',
                            'extraction_coverage.json'):
            if (artifacts.get(source_name) or {}).get('mtime', '') > specialized_mtime:
                specialized_fresh = False
    specialized_ready = (specialized_report.get('verdict') == 'READY'
                         and specialized_report.get('strict') is True
                         and specialized_fresh)
    handoff_report = _load_json(os.path.join(wd, 'handoff_validation.json')) or {}
    handoff_mtime = artifacts['handoff_validation.json'].get('mtime')
    handoff_fresh = bool(handoff_mtime)
    if handoff_fresh:
        handoff_ts = artifacts['handoff_validation.json'].get('mtime') or ''
        for source_name in ('extracted_data.json', 'proofs_index.json',
                            'extraction_coverage.json',
                            'specialized_extraction_validation.json'):
            if (artifacts.get(source_name) or {}).get('mtime', '') > handoff_ts:
                handoff_fresh = False
    handoff_ready = (handoff_report.get('verdict') == 'READY'
                     and handoff_report.get('strict') is True and handoff_fresh)

    # ---- 判定当前阶段 ----
    if not artifacts['proofs_index.json']['exists']:
        phase, status = 'step2_scan', 'not_started'
        action = '第二步：解压 + 扫描材料 + 缺件核对（尚无 proofs_index.json）'
        cmd = ('python %s "<proof_dir>" --output %s'
               % (_q(os.path.join(SCRIPT_DIR, 'scan_proofs.py')),
                  _q(os.path.join(wd, 'proofs_index.json'))))
    elif not stage_a_done:
        phase, status = 'step3_A_render', 'in_progress'
        action = '第三步阶段A：批量分类 + 拆图/提取文字（反复跑到打印"已全部处理完毕"）'
        cmd = ('python %s "<proof_dir>" --work-dir %s --time-budget 240 --max-pages 30'
               % (_q(os.path.join(SCRIPT_DIR, 'batch_render_pdfs.py')), _q(wd)))
    elif not cov['passed']:
        phase, status = 'step3_B_reading', 'in_progress'
        if cov['scope'] == 'no_extracted':
            action = ('第三步阶段B：主agent分小批并行读图（尚未开始）。直接跑下面的**页级配额**驱动命令'
                      '（extracted_data.json 不存在时脚本会自动建壳），它会直给本轮 8 张页图的完整路径；'
                      '**同一条消息里并行读完**，读完写盘后用 `--mark-batch --next-pages 8` 登记并取下一批。'
                      '禁止委派子agent（红线第1条）')
            cmd = next_batch_command(wd)
        elif cov.get('core_render_gaps'):
            # 阶段A漏渲：缺的页图不在盘上 → 阶段B 无论怎么读都补不回来，必须先回阶段A
            phase = 'step3_A_render'
            action = ('⚠️ 回到第三步阶段A：**%d 份核心材料（评估/审计等）的页图渲染不全**'
                      '（页图数 < PDF总页数），缺的页阶段B 根本排不出来 —— 表4-1~4-15 需要的'
                      '现金流预测表/运营费用参数/资本性支出/可比实例多在这些中后部页里。'
                      '先重跑阶段A补渲（核心报告默认全量渲染，按页续渲只补缺页），'
                      '再回 `--next-pages 8` 继续读'
                      % len(cov['core_render_gaps']))
            cmd = ('python %s "<proof_dir>" --work-dir %s --time-budget 240'
                   % (_q(os.path.join(SCRIPT_DIR, 'batch_render_pdfs.py')), _q(wd)))
        else:
            extra = ''
            if cov.get('core_page_unread'):
                cps = cov.get('core_page_stats') or {}
                extra = ('；**另有 %d 份核心材料未整份读完**（页级门槛，单元 %s/%s）——'
                         '评估/审计报告必须读到附表/现金流预测表为止'
                         % (len(cov['core_page_unread']), cps.get('read_units'),
                            cps.get('total_units')))
            action = ('第三步阶段B：**覆盖率未达标，禁止进入第四步**。已读 %s/%s（%s%%）%s，'
                      '跑下面的页级配额命令取下一批（直给全部页图路径，一条消息里并行读完），'
                      '读完写盘后加 `--mark-batch` 登记并顺带取下一批；禁止委派子agent'
                      % (cov['read_files'], cov['total_files'], cov['coverage_pct'], extra))
            cmd = next_batch_command(wd)
    elif EXTRACTION_ONLY and not specialized_ready:
        phase, status = 'step3_C_specialized', 'in_progress'
        action = ('文件和核心页覆盖已通过；现在执行法律意见书、房地产估价报告、'
                  '第二章财务数据与重大变化原因的专项严格门禁。只有 verdict=READY '
                  '才能进入总交接校验。')
        cmd = ('python %s --work-dir %s --strict'
               % (_q(os.path.join(SCRIPT_DIR, 'validate_specialized_extraction.py')),
                  _q(wd)))
    elif EXTRACTION_ONLY and not handoff_ready:
        phase, status = 'step3_C_contract', 'in_progress'
        action = ('文件和核心页覆盖已通过；现在执行字段、表格、溯源与契约严格门禁。'
                  '只有 verdict=READY 才能交接，READY_WITH_GAPS 仍需补提。')
        cmd = ('python %s --work-dir %s --strict'
               % (_q(os.path.join(SCRIPT_DIR, 'validate_handoff.py')), _q(wd)))
    elif EXTRACTION_ONLY:
        # 提取域模式：覆盖率与交接契约都通过后才收口
        phase, status = 'step3_done_handoff', 'completed'
        lack_handoff = [n for n in HANDOFF_ARTIFACTS
                        if n != CHECKPOINT_NAME and not ex(n)]
        action = ('步骤1~3 全部完成且阶段B覆盖率达标 → **把 work_dir 交回主skill第四步**。'
                  '交接清单：%s%s。主skill会对同一门槛现算复核（不信任本文件的旧结论），'
                  '覆盖率门禁与严格交接门禁均已通过，生成端还会复核报告新鲜度'
                  % ('、'.join(HANDOFF_ARTIFACTS),
                     ('；⚠️ 当前缺 %s' % '、'.join(lack_handoff)) if lack_handoff else ''))
        cmd = ('python %s --proofs-index %s --extracted %s --output %s'
               % (_q(os.path.join(SCRIPT_DIR, 'check_extraction_coverage.py')),
                  _q(os.path.join(wd, 'proofs_index.json')),
                  _q(os.path.join(wd, 'extracted_data.json')),
                  _q(os.path.join(wd, 'extraction_coverage.json'))))
    elif n_tbl_plans == 0 and n_ch_plans == 0:
        phase, status = 'step4_fill', 'not_started'
        action = '第四步：覆盖率已达标。先跑阶段C交叉验证，再生成 phase0/1/2/5 表格 fill_plan'
        cmd = ('python %s --blueprint %s --extracted %s --output %s --base-vars-out %s'
               % (_q(os.path.join(SCRIPT_DIR, 'gen_phase_fill_plan.py')),
                  _q(os.path.join(SKILL_DIR, 'templates', 'phase0_blueprints.json')),
                  _q(os.path.join(wd, 'extracted_data.json')),
                  _q(os.path.join(wd, 'fill_plan_phase0.json')),
                  _q(os.path.join(wd, 'base_vars.json'))))
    elif n_ch_plans < len(CHAPTER_PLANS):
        phase, status = 'step4_fill', 'in_progress'
        missing = [n for n in CHAPTER_PLANS if not plans[n]]
        action = ('第四步：按章并行撰写正文（缺 %s）。同一条回复里把缺的章一次性并行发出，'
                  '提示词用 references/chapter_writer_prompt.md'
                  % '、'.join(n.replace('fill_plan_', '').replace('.json', '') for n in missing))
        cmd = '（子agent撰写，无脚本命令）'
    elif not plans['fill_plan_annex.json'] or not plans['fill_plan_phase5.phase6.json']:
        phase, status = 'step4_phase6', 'in_progress'
        lack = []
        if not plans['fill_plan_annex.json']:
            lack.append('fill_plan_annex.json（附件1/附件2 清理，照 fill_plan_reference.md 第七节配方生成）')
        if not plans['fill_plan_phase5.phase6.json']:
            lack.append('fill_plan_phase5.phase6.json（由 phase5 蓝图生成，表15/16/22 整表重建）')
        action = 'phase6：附件清理 + 结构不匹配表重建，缺 ' + '；'.join(lack) + '。**必须最后应用**'
        cmd = ('python %s --template "<上一步输出.docx>" --fill-plan %s --validate-only'
               % (_q(os.path.join(SCRIPT_DIR, 'fill_docx.py')),
                  _q(os.path.join(wd, 'fill_plan_annex.json'))))
    elif not artifacts['validate_report.json']['exists']:
        phase, status = 'step5_validate', 'in_progress'
        action = '第五步：章节完备性检查 + 汇报（不足则重跑对应章子agent）'
        cmd = ('python %s "<输出.docx>" --chapters-only --proofs-index %s'
               % (_q(os.path.join(SCRIPT_DIR, 'validate_output.py')),
                  _q(os.path.join(wd, 'proofs_index.json'))))
    else:
        phase, status = 'step5_validate', 'completed'
        action = '第五步已出校验报告：确认无 FAIL 后向用户汇报并交付；交付后才可用 purge_file.py 清理'
        cmd = ('python %s "<文件或目录>"   # dry-run 预览'
               % _q(os.path.join(SCRIPT_DIR, 'purge_file.py')))

    bypasses = read_bypasses(wd)
    # CLI 侧也执行门槛参数下限保护：调低无效 + 留痕（先写再读，保证 checkpoint 重建后不丢）
    if cov.get('threshold_lowered') or cov.get('critical_keywords_dropped'):
        record_threshold_tamper(wd, 'pipeline_state', cov)
    tampering = read_threshold_tampering(wd)

    return {
        '_generated_by': 'scripts/pipeline_state.py',
        '_generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        '_warning': ('本文件由 pipeline_state.py 从 work_dir 实际产物**推导生成**，禁止手工编辑；'
                     'agent 自报的进度不作为依据。随时可重跑本脚本重建。'),
        'work_dir': wd,
        'skill_dir': SKILL_DIR,
        'scope': 'extraction_only' if EXTRACTION_ONLY else 'full_pipeline',
        'phase': phase,
        'status': status,
        'next_action': action,
        'next_command': cmd,
        'extraction': {
            'coverage_check_run': cov['report_exists'],
            'coverage_passed': cov['passed'],
            'coverage_pct': cov['coverage_pct'],
            'read_count': cov['read_files'],
            'total_count': cov['total_files'],
            'unread_count': cov['unread_count'],
            'critical_unread_count': len(cov['critical_unread']),
            'core_page_unread_count': len(cov.get('core_page_unread') or []),
            'core_page_stats': cov.get('core_page_stats') or {},
            'core_render_gap_count': len(cov.get('core_render_gaps') or []),
            'suspicious_count': cov['suspicious_count'],
            'read_items_claimed': cov['read_items_claimed'],
            'threshold_pct': cov['threshold_pct'],
            'threshold_requested_pct': cov['threshold_requested_pct'],
            'threshold_source': cov['threshold_source'],
            'threshold_floor_pct': cov['threshold_floor_pct'],
            'threshold_lowered': cov['threshold_lowered'],
            'critical_keywords_dropped': cov['critical_keywords_dropped'],
            'judged_by': cov['scope'],
            'report_stale': cov['report_stale'],
            'note': cov['note'],
            'specialized_validation_ready': specialized_ready,
            'specialized_validation_fresh': specialized_fresh,
        },
        'artifacts': artifacts,
        'fill_plans': plans,
        'docx_in_work_dir': docxs,
        'gate_bypasses': bypasses,
        'threshold_tampering': tampering,
        'blocking': (not cov['passed'] and cov['scope'] != 'no_pipeline'),
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


def print_state(state):
    e = state['extraction']
    print('=== 流水线状态（由磁盘产物推导，不依赖上下文） ===')
    print('work_dir : %s' % state['work_dir'])
    print('当前阶段 : %s / %s' % (state['phase'], state['status']))
    print('')
    print('--- 产物 ---')
    for name, st in state['artifacts'].items():
        if name == 'images/':
            mark = '[OK]' if st['exists'] else '[--]'
            print('  %s %-26s %s' % (mark, 'images/',
                                     ('%d 个页图目录' % st['sub_dirs']) if st['exists'] else '不存在'))
            continue
        mark = '[OK]' if st.get('exists') else '[--]'
        detail = ('%.1f KB  %s' % (st['size'] / 1024.0, st['mtime'])) if st.get('exists') else '不存在'
        print('  %s %-26s %s' % (mark, name, detail))
    print('')
    print('--- 阶段B覆盖率（判定依据：%s）---' % e['judged_by'])
    print('  已读/总数   : %s/%s = %s%%（阈值 %s%%，下限 %s%%，只可调高）'
          % (e['read_count'], e['total_count'], e['coverage_pct'], e['threshold_pct'],
             e.get('threshold_floor_pct')))
    if e.get('threshold_lowered'):
        print('  ⚠️⚠️ 门槛参数被调低（%s 给出 %s%%）→ 已抬回下限判定，调低无效且已留痕'
              % (e.get('threshold_source'), e.get('threshold_requested_pct')))
    if e.get('critical_keywords_dropped'):
        print('  ⚠️⚠️ 核心材料关键词被删减（缺 %s）→ 已并回默认集，删减无效且已留痕'
              % '/'.join(e['critical_keywords_dropped']))
    print('  未读         : %s 份；核心材料未读 %s 份' % (e['unread_count'], e['critical_unread_count']))
    cps = e.get('core_page_stats') or {}
    if cps.get('files'):
        print('  核心材料页级 : %s/%s 份整份读完；单元 %s/%s = %s%%（**必须100%%**：评估/审计报告的'
              '现金流预测表、运营费用参数、资本性支出、可比实例都在中后部）'
              % (cps.get('files_done'), cps.get('files'), cps.get('read_units'),
                 cps.get('total_units'), cps.get('pct')))
    if e.get('core_page_unread_count'):
        print('  ❌ 核心材料未整份读完 %s 份%s'
              % (e['core_page_unread_count'],
                 '（其中 %s 份是阶段A漏渲，需先重跑 batch_render_pdfs.py 补渲）'
                 % e.get('core_render_gap_count') if e.get('core_render_gap_count') else ''))

    print('  自报 read_items 条数: %s；仅自报无佐证: %s 份'
          % (e['read_items_claimed'], e['suspicious_count']))
    print('  校验脚本跑过 : %s' % ('是' if e['coverage_check_run'] else '否 ← 从未执行'))
    print('  门槛通过     : %s' % ('✅ 是' if e['coverage_passed'] else '❌ 否'))
    if e['report_stale']:
        print('  ⚠️ extraction_coverage.json 早于 extracted_data.json（报告过期，已按现算判定）')
    if e['note']:
        print('  说明         : %s' % e['note'])
    print('')
    plans_done = [n for n, v in state['fill_plans'].items() if v]
    plans_lack = [n for n, v in state['fill_plans'].items() if not v]
    if state.get('scope') == 'extraction_only':
        print('--- 交接产物（步骤1~3 提取域，交给主skill第四步）---')
        for name in HANDOFF_ARTIFACTS:
            st = state['artifacts'].get(name, {})
            print('  %s %s' % ('[OK]' if st.get('exists') else '[--]', name))
        print('  说明: 本目录为**提取域子skill**（无第四步脚本），不推导第四~五步进度；'
              '第四步的表格生成/写文档由主skill执行。')
    else:
        print('--- 第四步 fill_plan ---')
        print('  已生成(%d): %s' % (len(plans_done), '、'.join(plans_done) or '无'))
        print('  未生成(%d): %s' % (len(plans_lack), '、'.join(plans_lack) or '无'))
    if state['docx_in_work_dir']:
        print('  work_dir 内 docx: %s' % '、'.join(state['docx_in_work_dir']))
    print('')
    if state['gate_bypasses']:
        print('⚠️⚠️ 检测到 %d 次覆盖率门槛越权放行（--force-low-coverage）：'
              % len(state['gate_bypasses']))
        for b in state['gate_bypasses']:
            print('   - %s  %s  覆盖率 %s%%' % (b.get('at'), b.get('stage'), b.get('coverage_pct')))
        print('   → 第五步全量校验会 FAIL，交付汇报必须向用户列明。')
        print('')
    if state.get('threshold_tampering'):
        print('⚠️⚠️ 检测到 %d 次门槛参数篡改（调低阈值/删核心关键词，红线第5条）：'
              % len(state['threshold_tampering']))
        for t in state['threshold_tampering']:
            print('   - %s  %s  %s（请求 %s%% → 实际按 %s%% 判定）'
                  % (t.get('at'), t.get('stage'), t.get('kind'),
                     t.get('requested_pct'), t.get('effective_pct')))
        print('   → 调低本身已被下限保护忽略（不影响判定），但第五步会报项、交付汇报必须列明。')
        print('')
    if state['blocking']:
        print('❌ 覆盖率门槛未通过：**无论上下文里看到什么，都不得进入第四步**。')
        if state.get('scope') == 'extraction_only':
            print('   提取域子skill的产物此时不得交接；主skill的 gen_phase_fill_plan.py / '
                  'fill_docx.py 收到这样的 work_dir 也会以 exit=3 拒绝执行。')
        else:
            print('   gen_phase_fill_plan.py / fill_docx.py 会以 exit=3 拒绝执行。')
    print('▶ 下一步：%s' % state['next_action'])
    print('▶ 命令  ：%s' % state['next_command'])


def main():
    ap = argparse.ArgumentParser(
        description='流水线状态自检（恢复检查点）：扫描 work_dir 产物推导当前进度，写 checkpoint.json')
    ap.add_argument('--work-dir', '-w', default='.', help='工作目录（默认当前目录）')
    ap.add_argument('--output', '-o', default=None,
                    help='checkpoint 输出路径（默认 <work_dir>/checkpoint.json）')
    ap.add_argument('--threshold', type=float, default=None,
                    help='覆盖率阈值%%（默认取报告值或 80）。**只能调高（加严）**：低于下限 80 一律'
                         '被抬回 80 并记入 checkpoint 的 threshold_tampering')
    ap.add_argument('--json', action='store_true', help='只输出 JSON（供程序消费）')
    ap.add_argument('--no-write', action='store_true', help='不写 checkpoint.json')
    args = ap.parse_args()

    if not os.path.isdir(args.work_dir):
        print('ERROR: work_dir 不存在：%s' % args.work_dir, file=sys.stderr)
        sys.exit(2)

    state = scan_state(args.work_dir, threshold=args.threshold)
    if not args.no_write:
        path = write_checkpoint(state, args.output)
        state['_checkpoint_path'] = path

    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print_state(state)
        if not args.no_write and state.get('_checkpoint_path'):
            print('\ncheckpoint: %s' % state['_checkpoint_path'])


if __name__ == '__main__':
    main()
