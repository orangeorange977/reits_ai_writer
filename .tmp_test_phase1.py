# -*- coding: utf-8 -*-
"""阶段1验证①：缺件检测 fixture 单测。
用例：A 完整包（零缺件）；B 缺2项必交材料；C 文件名变体；D 空目录；E 目录不存在。"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')
from backend.services.materials_catalog import check_materials

BASE = Path(tempfile.mkdtemp(prefix="catalog_test_"))

# —— 25 项材料的典型目录结构（按五大文件夹组织）——
COMPLETE = [
    "一、参与主体情况/1 发起人、项目公司、运营管理机构的营业执照复印件，项目公司章程复印件/营业执照-项目公司.pdf",
    "一、参与主体情况/1 发起人、项目公司、运营管理机构的营业执照复印件，项目公司章程复印件/项目公司章程.pdf",
    "一、参与主体情况/2 底层资产持有主体及原始权益人经审计的财务报告/审计报告2023.pdf",
    "一、参与主体情况/2 底层资产持有主体及原始权益人经审计的财务报告/审计报告2024.pdf",
    "一、参与主体情况/3 信用记录查询结果/信用中国查询结果截图.png",
    "一、参与主体情况/4 关于自身运营情况的承诺函/项目公司运营情况承诺函.pdf",
    "一、参与主体情况/5 基金管理人执业资格承诺函/基金管理人执业资格承诺函.pdf",
    "一、参与主体情况/6 中介机构执业资格承诺函/律师事务所执业资格承诺函.pdf",
    "二、项目基本条件/政府批准文件/项目立项批复.pdf",
    "二、项目基本条件/权属和项目运营承诺函/发起人权属承诺函.pdf",
    "二、项目基本条件/法律意见书/法律意见书（项目合规）.pdf",
    "二、项目基本条件/稳定运营承诺函/影响项目稳定运营重要因素的承诺函.pdf",
    "三、项目合规情况/投资管理手续/环评批复.pdf",
    "三、项目合规情况/投资管理手续/施工许可证.pdf",
    "三、项目合规情况/连廊夹层承诺函/连廊夹层建筑情况承诺函.pdf",
    "三、项目合规情况/土地合规/不动产权证书.pdf",
    "三、项目合规情况/可转让性/转让相关无异议函.pdf",
    "三、项目合规情况/可转让性承诺函/发起人可转让性承诺函.pdf",
    "三、项目合规情况/可转让性承诺函/基金管理人可转让性承诺函.pdf",
    "三、项目合规情况/转让法律意见书/关于转让行为合法性的法律意见书.pdf",
    "三、项目合规情况/税收承诺函/税收处理承诺函.pdf",
    "四、募集资金用途/回收资金使用承诺函.pdf",
    "五、其他材料/发起人真实有效合规完备承诺函.pdf",
    "五、其他材料/中介机构真实有效合规完备承诺函.pdf",
]

def build(root: Path, rels):
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 fake")

fails = []

# A：完整包 → 必交项零缺件
a = BASE / "A_complete"; build(a, COMPLETE)
r = check_materials(a)
ok = r["available"] and not r["missing"] and r["message"] == ""
print(f"[{'OK' if ok else 'FAIL'}] A 完整包: available={r['available']} 缺件={len(r['missing'])} 提醒={len(r['optional_missing'])} found={r['found_count']}/{r['required_count']}")
if not ok:
    fails.append("A"); print("   误报:", r["missing"])

# B：删掉"营业执照"和"土地合规"两类 → 必须精确报出第1、15项
b = BASE / "B_missing2"
build(b, [x for x in COMPLETE if "营业执照" not in x and "不动产权" not in x])
r = check_materials(b)
nos = sorted(m["no"] for m in r["missing"])
ok = r["available"] and nos == [1, 15] and "缺少2项材料" in r["message"]
print(f"[{'OK' if ok else 'FAIL'}] B 缺2项: 报出缺件={nos}（期望[1,15]） message={r['message'][:40]}...")
if not ok:
    fails.append("B")

# C：文件名变体（无大类文件夹、命名口语化）→ 兜底全目录匹配，不得误报缺件
c = BASE / "C_variant"
variant = [
    "材料/营业执照扫描件.pdf", "材料/公司章程.pdf", "材料/2023年度审计报告.pdf",
    "材料/信用查询截图.png", "材料/运营情况承诺函.pdf", "材料/基金管理人执业资格承诺.pdf",
    "材料/中介执业资格承诺.pdf", "材料/政府批复.pdf", "材料/权属承诺.pdf",
    "材料/法律意见书.pdf", "材料/稳定运营承诺.pdf", "材料/环评批复文件.pdf",
    "材料/连廊承诺.pdf", "材料/土地证.pdf", "材料/可转让性文件.pdf",
    "材料/可转让性承诺.pdf", "材料/转让法律意见书.pdf", "材料/税收承诺.pdf",
    "材料/回收资金承诺.pdf", "材料/真实有效合规完备承诺-发起人.pdf",
    "材料/真实有效合规完备承诺-中介.pdf",
]
build(c, variant)
r = check_materials(c)
ok = r["available"] and not r["missing"]
print(f"[{'OK' if ok else 'FAIL'}] C 变体命名兜底: 缺件={[m['no'] for m in r['missing']]}（期望[]）")
if not ok:
    fails.append("C")

# D：空目录 → available=False（前端不展示）
d = BASE / "D_empty"; d.mkdir()
r = check_materials(d)
ok = not r["available"]
print(f"[{'OK' if ok else 'FAIL'}] D 空目录: available={r['available']}（期望False）")
if not ok:
    fails.append("D")

# E：目录不存在 → available=False 且不抛异常
r = check_materials(BASE / "不存在")
ok = not r["available"]
print(f"[{'OK' if ok else 'FAIL'}] E 目录不存在: available={r['available']}（期望False）")
if not ok:
    fails.append("E")

shutil.rmtree(BASE, ignore_errors=True)
print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
