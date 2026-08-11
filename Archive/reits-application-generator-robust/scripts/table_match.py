#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""表格字段匹配与占位判定公共模块（rebuild_tables 合并 / md 表转换共用）。

为什么独立成模块：
  · md_table_to_fill_plan.py 的三级模糊匹配（精确→去括号→包含）已在 ch1 实战验证，
    rebuild_tables 的「按字段名合并旧表已填内容」需要完全相同的匹配语义——
    两处各写一份必然漂移，故抽出共用。
  · 「占位判定」决定续填语义的边界（旧格是占位→新值可覆盖；旧格已填→旧值胜出），
    必须全工程一个口径，禁止在别处另写判定规则。
"""

import re

# ---------------- 归一化 ----------------

def norm(s):
    """归一化用于比对：去空白、全角括号统一、去尾部冒号"""
    s = (s or "").strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("：", ":")
    return s.rstrip(":")


def strip_paren(s):
    """去掉括号及其内容：运营起始时间(年月) → 运营起始时间"""
    return re.sub(r"[(（][^)）]*[)）]", "", s or "")


# ---------------- 字段名三级模糊匹配 ----------------

def match_label(want, left_map):
    """字段名 → 目标表行号，分三级回退。

    为什么要模糊回退（实测痛点）：子agent按官方模版切片写字段名，但业务在底稿里
    常改措辞——官方「运营起始时间（年月）」在底稿里是「运营起始时间」。若只做精确
    匹配会大量误报 FAIL，逼得人工逐个改；但也不能无脑模糊，否则会把值写错行。
    因此分级：精确 → 去括号 → 双向包含（且要求较短一方≥3字，防止「时间」这类
    短词乱命中），并把命中级别记进产物便于复核。

    left_map: {norm(标签): 行号或任意值}；返回 (命中值, 命中级别说明)。
    """
    key = norm(want)
    if key in left_map:
        return left_map[key], "精确"

    kp = norm(strip_paren(want))
    if kp and kp in left_map:
        return left_map[kp], "去括号"
    for lk, ri in left_map.items():
        if kp and norm(strip_paren(lk)) == kp:
            return ri, "去括号"

    cands = []
    for lk, ri in left_map.items():
        a, b = kp or key, norm(strip_paren(lk))
        if not a or not b:
            continue
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        if len(short) >= 3 and short in long_:
            cands.append((ri, lk))
    if len(cands) == 1:
        return cands[0][0], "包含"
    if len(cands) > 1:
        return None, "歧义(%s)" % "/".join(x[1][:14] for x in cands[:3])
    return None, "未命中"


# ---------------- 占位判定（续填语义的边界，全工程唯一口径） ----------------

# 含这些子串的格视为「占位」（可被新值覆盖）：脚本/子agent写入的待补标记，
# 以及业务认可的交付占位「（待定）」——有真值时应换成真值
_PLACEHOLDER_SUBSTRINGS = (
    "【待填写", "【需人工填写", "【待确认", "【数据缺失",
    "（待定）", "(待定)",
    "无需填写",          # 官方模版「——（无需填写）」格
)

# 整格只有这些字符 → 视为空：空白、省略号、破折号、下划线、斜杠、顿号点号
_PLACEHOLDER_FULL_RE = re.compile(r"^[\s…\u2026·\.。、\-—_/\\]*$")

# 官方模版提示语整格：（如有）（如涉及）（如适用）等
_TEMPLATE_HINT_RE = re.compile(r"^[（(](如有|如涉及|如适用|若有|如无[则可].{0,6})[)）]$")

# 官方模版指导文字：以祈使动词开头的整格说明（如表9 R11「说明基金管理人…是否存在
# 实际控制关系…」）——业务写的真内容是结论句，不会以命令式动词起句；
# 长度门槛防短词误伤（如真填了「说明：无」这类短句不判占位）
_TEMPLATE_GUIDANCE_RE = re.compile(r"^请?(说明|概述|列示|列明|简述|简要说明|简要分析|逐项说明)")


def is_placeholder(text):
    """单元格文本是否为「占位」（空/待填标记/模版提示语）——占位格可被新值覆盖。

    注意：判定必须偏保守。误判「已填」为「占位」会覆盖业务写的内容（严重）；
    误判「占位」为「已填」只是留下一个待补标记（validate_output 能抓到）。
    """
    t = (text or "").strip()
    if not t:
        return True
    if _PLACEHOLDER_FULL_RE.fullmatch(t):
        return True
    for sub in _PLACEHOLDER_SUBSTRINGS:
        if sub in t:
            return True
    if _TEMPLATE_HINT_RE.fullmatch(t):
        return True
    if len(t) >= 15 and _TEMPLATE_GUIDANCE_RE.match(t):
        return True
    return False
