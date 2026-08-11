#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
契约同源校验：只比对提取端与生成端必须字节一致的交接契约、字段结构、
引用规则和交接校验器。两端执行脚本职责不同，不再错误要求全部同名。

为什么需要它：
  子SKILL持有的是**完整副本**（不是软链），这样才能脱离主SKILL单独运行，同时保证主SKILL
  一字未动、零影响。代价是同一文件存在两份 —— 若只改一侧，覆盖率门槛、溯源字段、限页策略
  等口径就会悄悄分叉（这类"两份文档/两份实现说法不一致"是本项目历史事故的常见形态）。
  因此约定：**改任一侧后必须同步另一侧并跑一次本脚本**。

判定：
  - 找不到主SKILL（单独部署场景）→ 打印说明并 exit=0（不阻断）
  - 全部一致                      → exit=0
  - 有差异/缺失                    → 逐个列出（含两侧 mtime，便于判断哪边是新改的）并 exit=1

用法:
  python verify_sync.py                       # 自动定位主SKILL（默认 ../../..）
  python verify_sync.py --main-skill-dir <主skill目录>
  python verify_sync.py --json
"""

import argparse
import datetime
import hashlib
import json
import os
import sys

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUBSKILL_DIR = os.path.dirname(SCRIPT_DIR)

# 需要保持与主SKILL一致的文件（相对各自 skill 根目录）
SYNCED_FILES = [
    'scripts/validate_handoff.py',
    'templates/handoff_contract.json',
    'templates/extracted_data_schema.json',
    'templates/data_crossref.json',
    'templates/citation_rules.json',
    'templates/mapping_rules.json',
]

# 主SKILL的识别标志（避免把任意目录当成主SKILL）
MAIN_MARKERS = ('SKILL.md', 'scripts/gen_phase_fill_plan.py', 'scripts/fill_docx.py')


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _mtime(path):
    try:
        return datetime.datetime.fromtimestamp(
            os.stat(path).st_mtime).isoformat(timespec='seconds')
    except Exception:
        return None


def looks_like_main_skill(d):
    return bool(d) and all(os.path.exists(os.path.join(d, m)) for m in MAIN_MARKERS)


def locate_main_skill(explicit=None):
    """定位主SKILL目录：显式参数 > 上两级（<主skill>/subskills/<本skill>）> 上一级。"""
    cands = []
    if explicit:
        cands.append(os.path.abspath(explicit))
    cands.append(os.path.dirname(os.path.dirname(SUBSKILL_DIR)))   # <main>/subskills/<sub> → <main>
    cands.append(os.path.dirname(SUBSKILL_DIR))
    for c in cands:
        if looks_like_main_skill(c):
            return c
    return None


def compare(main_dir):
    rows = []
    for rel in SYNCED_FILES:
        sub_p = os.path.join(SUBSKILL_DIR, rel.replace('/', os.sep))
        main_p = os.path.join(main_dir, rel.replace('/', os.sep))
        sub_h, main_h = _sha256(sub_p), _sha256(main_p)
        if sub_h is None:
            status = 'missing_in_subskill'
        elif main_h is None:
            status = 'missing_in_main'
        elif sub_h == main_h:
            status = 'same'
        else:
            status = 'DIFF'
        rows.append({
            'file': rel, 'status': status,
            'subskill': {'path': sub_p, 'sha256': sub_h, 'mtime': _mtime(sub_p)},
            'main_skill': {'path': main_p, 'sha256': main_h, 'mtime': _mtime(main_p)},
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description='子SKILL与主SKILL同名脚本/模板的同源校验')
    ap.add_argument('--main-skill-dir', default=None,
                    help='主SKILL根目录（默认自动定位 ../..，即 <主skill>/subskills/<本skill> 的上两级）')
    ap.add_argument('--json', action='store_true', help='只输出 JSON')
    args = ap.parse_args()

    main_dir = locate_main_skill(args.main_skill_dir)
    if not main_dir:
        result = {'checked': False, 'reason': 'main_skill_not_found',
                  'subskill_dir': SUBSKILL_DIR, 'files': [], 'exit': 0}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print('[跳过] 未找到主SKILL目录（独立部署场景）：子SKILL自带完整副本，可独立运行。')
            print('       如需比对，用 --main-skill-dir 指定 reits-application-generator 根目录。')
        sys.exit(0)

    rows = compare(main_dir)
    bad = [r for r in rows if r['status'] != 'same']
    result = {'checked': True, 'subskill_dir': SUBSKILL_DIR, 'main_skill_dir': main_dir,
              'total': len(rows), 'diff_count': len(bad), 'files': rows,
              'exit': 1 if bad else 0}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(result['exit'])

    print('=== 子SKILL ↔ 主SKILL 同源校验 ===')
    print('子SKILL : %s' % SUBSKILL_DIR)
    print('主SKILL : %s' % main_dir)
    print('')
    for r in rows:
        mark = '[OK]' if r['status'] == 'same' else '[!!]'
        print('  %s %-46s %s' % (mark, r['file'], r['status']))
    print('')
    if not bad:
        print('✅ %d 个文件全部一致（口径未漂移）。' % len(rows))
        sys.exit(0)

    print('❌ %d 个文件不一致 —— 提取域口径可能已分叉，必须同步后重跑本脚本：' % len(bad))
    for r in bad:
        print('  - %s（%s）' % (r['file'], r['status']))
        print('      子SKILL : mtime=%s  sha=%s' % (r['subskill']['mtime'],
                                                    (r['subskill']['sha256'] or '')[:12]))
        print('      主SKILL : mtime=%s  sha=%s' % (r['main_skill']['mtime'],
                                                    (r['main_skill']['sha256'] or '')[:12]))
    print('  → 以 mtime 较新的一侧为准复制到另一侧（两侧应始终字节一致）。')
    sys.exit(1)


if __name__ == '__main__':
    main()
