# -*- coding: utf-8 -*-
"""串章修复验证：模拟前端竞态写坏的 ch1.json，验证目录修复 + 保存护栏。"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from backend.services import skill_runner as sr

tmp = pathlib.Path(tempfile.mkdtemp(prefix="cross_ch_test_"))
sr.PROJECTS_DIR = tmp  # 隔离：不碰真实项目数据

CH1 = ['（一）项目概况', '（二）特殊限定情况说明', '（三）可扩募资产情况']
CH2 = ['（一）法律关系', '（二）项目公司情况', '（三）发起人（原始权益人）情况',
       '（四）运营管理机构情况', '（五）基金管理人和资产支持证券管理人情况', '（六）有关中介机构情况']
CH3 = ['（一）产品要素', '（二）产品架构', '（三）实施步骤', '（四）目前进展', '（五）其他需要说明的重大情况']
FOREIGN_FOR_1 = CH2 + CH3

# 1) 被串章写坏的 ch1.json：第三章小标题（带内容）在前 + 第一章小标题在后（=截图症状）
polluted = {"chapter": "一、项目基本情况", "sections": [
    {"id": "1", "title": "（一）产品要素", "blocks": [{"type": "p", "text": "第三章内容"}]},
    {"id": "2", "title": "（二）产品架构", "blocks": [{"type": "p", "text": "第三章内容2"}]},
    {"id": "3", "title": "（一）项目概况", "blocks": [{"type": "p", "text": "第一章内容"}]},
]}
sr._save_json(1, polluted, 't1')

res = sr.get_chapter_content(1, CH1, {}, 1, 't1', FOREIGN_FOR_1)
titles = [s['title'] for s in res['sections']]
assert titles == CH1, f"目录未修复: {titles}"
assert '第一章内容' in res['sections'][0]['html'], "本章内容丢失"
assert all('第三章内容' not in s['html'] for s in res['sections']), "他章内容未剔除"
print("OK1 目录修复:", titles)

# 2) 保存护栏：跨章误存（提交小标题与本章模板无交集）必须被拒写
p = sr.chapter_json_path(1, 't1')
before = p.read_text(encoding='utf-8')
sr.save_chapter_content(1, [{"id": "1", "title": "（一）产品要素", "html": "<p>坏数据</p>"}],
                        't1', None, CH1)
assert p.read_text(encoding='utf-8') == before, "护栏未生效：坏数据覆盖了原文件"
print("OK2 串章保存被拦截，原数据完好")

# 3) 正常保存（含本章小标题）不受护栏影响
sr.save_chapter_content(1, [{"id": "1", "title": "（一）项目概况", "html": "<p>正常编辑</p>"}],
                        't1', None, CH1)
d = sr._load_json(1, 't1')
assert d['sections'][0]['title'] == '（一）项目概况'
assert '正常编辑' in d['sections'][0]['blocks'][0]['text']
print("OK3 正常保存不受影响")

# 4) 全章小标题缓存：真实模板解析 + 二次调用命中缓存
tpl = str(pathlib.Path(__file__).resolve().parent /
          "templates-packs/reits-ndrc-2024/template.docx")
all_subs = sr.all_chapters_subtitles(tpl)
assert all_subs[1] == CH1 and all_subs[3] == CH3, all_subs
assert sr.all_chapters_subtitles(tpl) is all_subs, "缓存未命中"
print("OK4 全章小标题解析与缓存正常:", {k: len(v) for k, v in all_subs.items()})

print("ALL TESTS PASSED")
