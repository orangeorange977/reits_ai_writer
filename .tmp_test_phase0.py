# -*- coding: utf-8 -*-
"""阶段0验证：模拟 run_chapter 的真实加载路径，确认 ch2/ch4/ch5 指南已含双遍核对要求；
同时确认未改动的 ch1/ch3/ch6/ch7 内容不受影响（回归）。"""
import sys
sys.path.insert(0, '.')
from backend.services import pack_service

MARK = "双遍核对要求（本章必做）"
EXPECT_MARK = {2: True, 4: True, 5: True, 1: False, 3: False, 6: False, 7: False}

fails = []
for n, expect in sorted(EXPECT_MARK.items()):
    path = pack_service.reading_path(n)
    text = path.read_text(encoding="utf-8")
    got = MARK in text
    status = "OK" if got == expect else "FAIL"
    print(f"[{status}] ch{n}: 指南={path.name} {len(text)}字 含双遍要求={got}（期望={expect}")
    if got != expect:
        fails.append(n)

# 关键细则抽查（ch2 必含五项核对点）
ch2 = pack_service.reading_path(2).read_text(encoding="utf-8")
for kw in ("续表", "单位", "合计行", "负号与百分号", "期间"):
    ok = kw in ch2.split(MARK)[-1]
    print(f"[{'OK' if ok else 'FAIL'}] ch2 核对点含『{kw}』")
    if not ok:
        fails.append("ch2:" + kw)

print("\n结论:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
