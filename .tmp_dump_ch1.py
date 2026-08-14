# -*- coding: utf-8 -*-
import json
d = json.load(open('/app/workspace/projects/15/ch1.json', encoding='utf-8'))
for sec in d.get('sections', []):
    t = sec.get('title', '')
    for b in sec.get('blocks', []):
        if b.get('type') == 'kv' and '概况' in t:
            print('== 表1')
            for r in b.get('rows', []):
                print('  ', repr(r.get('label', ''))[:36], '=>', repr(r.get('value', ''))[:80])
        if b.get('type') == 'grid':
            print('==', b.get('caption'))
            for r in b.get('rows', []):
                print('  ', [str(c)[:24] for c in r][:4])
        if b.get('type') == 'p' and '扩募' in t:
            print('== 扩募段落:', b.get('text', '')[:260])
