# -*- coding: utf-8 -*-
"""剔除串章误存的 section：标题精确等于其他章节模板小标题的块必是串章数据。"""
import json
import os
import sys

sys.path.insert(0, '/app')
from backend.services import skill_runner

TPL = '/app/templates-packs/reits-ndrc-2024/template.docx'
PACK = 'reits-ndrc-2024'
BASE = '/app/workspace/projects/15'

all_subs = skill_runner.all_chapters_subtitles(TPL, PACK)
for n in sorted(all_subs.keys()):
    p = f'{BASE}/ch{n}.json'
    if not os.path.exists(p):
        continue
    d = json.load(open(p, encoding='utf-8'))
    secs = d.get('sections', []) or []
    ft = {(t or '').strip() for m, ss in all_subs.items() if m != n for t in (ss or [])}
    kept = [s for s in secs if (s.get('title') or '').strip() not in ft]
    if len(kept) != len(secs):
        d['sections'] = kept
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"ch{n}: 剔除 {len(secs) - len(kept)} 个串章节，剩 {len(kept)}")
    else:
        print(f"ch{n}: 干净（{len(secs)} 节）")
