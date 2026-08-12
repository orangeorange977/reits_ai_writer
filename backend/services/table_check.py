# -*- coding: utf-8 -*-
"""财务表勾稽校验器（借鉴 Archive 的数值勾稽思路，轻量嵌入现有章节管线）。

对章节结构化内容里的表格（grid 块）做三条基础勾稽检查，问题只提示、不阻断：
1. 资产负债率重算：同表里找到"资产总额/负债总额/资产负债率"三行，
   按列重算 负债/资产×100% 与表内披露的比率比对；
2. 期间表头与列数一致：数据行的列数与表头列数不一致时报出（常见于跨页续表漏列、
   期间列错位）；
3. 同一指标跨表一致：同一行标签 + 同一列期间在不同表里的数值不一致时报出。

设计约束：
- 纯函数、无 I/O，输入 sections（[{"title","blocks":[{"type":"grid","caption","headers","rows"}]}]）；
- 全量容错：任何解析失败跳过该表，绝不影响预览/保存；
- 输出业务语言（"表13：……请核对"），不暴露内部规则名等技术细节；
- 容差：绝对 0.01 + 相对 0.5%（金额多为万元两位小数，防止四舍五入误报）。
"""
import logging
import re

logger = logging.getLogger(__name__)

# 数值解析：允许千分位逗号、负号、括号负数（会计习惯）、单位后缀
_NUM_RE = re.compile(r"^-?[\d,]+(\.\d+)?$")
_PAREN_NEG_RE = re.compile(r"^[（(][\d,]+(\.\d+)?[）)]$")


def _parse_num(cell):
    """把单元格解析成数值；非数值/备注/空缺返回 None。"""
    if cell is None:
        return None
    s = str(cell).strip()
    if not s or "【注" in s or s in ("—", "–", "-", "/", "不适用", "无"):
        return None
    # 去掉常见单位后缀与空格
    for suf in ("万元", "亿元", "%", "％", "元", "㎡", "平方米"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
            break
    neg = False
    if _PAREN_NEG_RE.match(s):
        neg = True
        s = s[1:-1]
    if not _NUM_RE.match(s):
        return None
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


def _close(a: float, b: float) -> bool:
    """数值容差比对：绝对 0.01 + 相对 0.5%。"""
    return abs(a - b) <= max(0.01, abs(b) * 0.005)


def _fmt(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".") if "." in f"{v:,.2f}" else f"{v:,.2f}"


def _grids(sections):
    """取出 (小节标题, grid 块) 序列；跳过畸形的 section。"""
    out = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        for b in sec.get("blocks", []) or []:
            if isinstance(b, dict) and b.get("type") == "grid":
                out.append((sec.get("title", ""), b))
    return out


def _label(cell) -> str:
    return str(cell or "").strip()


def _check_debt_ratio(caption, headers, rows, issues):
    """规则1：资产负债率重算。"""
    asset_row = debt_row = ratio_row = None
    for r in rows:
        if not r:
            continue
        lab = _label(r[0])
        if ("资产总额" in lab or "总资产" in lab) and asset_row is None:
            asset_row = r
        elif ("负债总额" in lab or "总负债" in lab) and debt_row is None:
            debt_row = r
        elif "资产负债率" in lab and ratio_row is None:
            ratio_row = r
    if not (asset_row and debt_row and ratio_row):
        return
    for i in range(1, len(headers or [])):
        a = _parse_num(asset_row[i]) if i < len(asset_row) else None
        d = _parse_num(debt_row[i]) if i < len(debt_row) else None
        rep = _parse_num(ratio_row[i]) if i < len(ratio_row) else None
        if a in (None, 0) or d is None or rep is None:
            continue
        calc = d / a * 100
        got = rep * 100 if rep <= 1.5 and calc > 1.5 else rep  # 兼容 0.56 与 56% 两种写法
        if not _close(calc, got):
            col = _label(headers[i]) if i < len(headers) else f"第{i}列"
            issues.append({
                "caption": caption,
                "message": f"{caption or '表格'}：按“{col}”列的负债总额/资产总额重算，"
                           f"资产负债率约为 {calc:.2f}%，与表内 {got:.2f}% 不一致，请核对原始数据。"})


def _check_col_count(caption, headers, rows, issues):
    """规则2：数据行列数与表头一致（跨页续表漏列/期间错位的常见信号）。"""
    n = len(headers or [])
    if n < 2:
        return
    for idx, r in enumerate(rows or []):
        if not r:
            continue
        if len(r) != n:
            issues.append({
                "caption": caption,
                "message": f"{caption or '表格'}：第 {idx + 1} 行列数（{len(r)} 列）"
                           f"与表头（{n} 列）不一致，可能是续表漏列或期间错位，请核对。"})
            break  # 一张表只报一次，避免刷屏


_PERIOD_RE = re.compile(r"(20\d{2}|19\d{2})\s*年")


def _check_cross_table(section_grids, issues):
    """规则3：同一指标（行标签+列期间）在不同表里的数值应一致。"""
    index = {}  # (label, period) -> [(caption, value)]
    for caption, grid in section_grids:
        headers = grid.get("headers") or []
        period_cols = {i: _PERIOD_RE.search(str(h)).group(0)
                       for i, h in enumerate(headers) if _PERIOD_RE.search(str(h))}
        for r in grid.get("rows", []) or []:
            if not r:
                continue
            lab = _label(r[0])
            if not lab or "合计" in lab:
                continue
            for i, period in period_cols.items():
                v = _parse_num(r[i]) if i < len(r) else None
                if v is None:
                    continue
                index.setdefault((lab, period), []).append((caption, v))
    reported = set()
    for (lab, period), hits in index.items():
        caps = {c for c, _ in hits}
        if len(caps) < 2:
            continue  # 只跨表比较；同表内重复标签不比
        vals = {round(v, 6) for _, v in hits}
        if len(vals) > 1 and (lab, period) not in reported:
            reported.add((lab, period))
            detail = "、".join(f"{c or '表格'}为 {_fmt(v)}" for c, v in hits[:3])
            issues.append({
                "caption": hits[0][0],
                "message": f"“{lab}”在 {period} 的数值跨表不一致（{detail}），请核对哪处为准。"})


def check_sections(sections) -> list:
    """对章节结构化内容做勾稽检查，返回 [{caption, message}]（业务语言提示）。"""
    issues = []
    try:
        grids = _grids(sections)
        for _, grid in grids:
            headers = grid.get("headers") or []
            rows = grid.get("rows") or []
            caption = str(grid.get("caption") or "").strip()
            try:
                _check_col_count(caption, headers, rows, issues)
                _check_debt_ratio(caption, headers, rows, issues)
            except Exception as e:
                logger.warning("勾稽检查跳过表 %s：%s", caption, e)
        _check_cross_table(grids, issues)
    except Exception as e:
        logger.warning("勾稽检查整体失败（不影响预览）：%s", e)
    return issues
