# -*- coding: utf-8 -*-
"""章节 JSON 健康门禁（借鉴 Archive 的 handoff_gate 契约校验，轻量版）。

在写入 Word / 生成预览之前，对章节结构化内容做结构健康检查：
- 清理畸形的小节/块（缺标题、未知块类型、空内容、表格结构损坏），
  让渲染永远拿到"干净"的数据，避免单条坏数据把整章预览打崩（500）；
- 同时产出业务语言的问题提示（"第3个小节内容缺失……"），随预览接口返回，
  前端黄色提醒，不阻断任何操作。

纯函数、无 I/O、全量容错：任何异常都降级为“原样放行”，绝不影响原有链路。
已知块类型与模板包 web_render 的渲染分支保持一致（p / kv / grid），
字段判定对齐渲染实际读取的字段：p 读 text，kv 读 rows，grid 读 headers + rows。
"""
import logging

logger = logging.getLogger(__name__)

_KNOWN_BLOCK_TYPES = {"p", "kv", "grid"}


def _block_ok(b) -> bool:
    """块是否结构完好。"""
    if not isinstance(b, dict):
        return False
    t = b.get("type")
    if t not in _KNOWN_BLOCK_TYPES:
        return False
    if t == "p":
        # 正文块：text 为空视为缺失（渲染读的就是 text）
        return bool(str(b.get("text") or "").strip())
    if t == "kv":
        return isinstance(b.get("rows"), list) and bool(b.get("rows"))
    if t == "grid":
        headers, rows = b.get("headers"), b.get("rows")
        if not isinstance(headers, list) or not headers:
            return False
        if not isinstance(rows, list):
            return False
        return all(isinstance(r, list) for r in rows)
    return False


def check_and_clean(sections):
    """健康门禁：返回 (干净的小节列表, 业务语言问题提示列表)。

    只提示不阻断；坏块被跳过、坏小节被剔除，渲染拿到的永远是可用数据。
    """
    warnings = []
    cleaned = []
    try:
        if not isinstance(sections, list) or not sections:
            return [], warnings
        for idx, sec in enumerate(sections, 1):
            if not isinstance(sec, dict):
                warnings.append(f"第{idx}个小节数据异常，预览已跳过该内容，请重新生成或手工补充。")
                continue
            title = str(sec.get("title") or "").strip()
            blocks = sec.get("blocks", []) or []
            if not isinstance(blocks, list):
                warnings.append(f"“{title or f'第{idx}个小节'}”内容结构异常，预览已跳过，请重新生成或手工补充。")
                continue
            good, dropped = [], 0
            for b in blocks:
                if _block_ok(b):
                    good.append(b)
                else:
                    dropped += 1
            if dropped:
                warnings.append(f"“{title or f'第{idx}个小节'}”有 {dropped} 处内容不完整或格式异常，"
                                f"预览中已跳过，请核对原文后手工补充。")
            if not title and not good:
                warnings.append(f"第{idx}个小节内容缺失，请补充后再导出 Word。")
                continue
            sec = dict(sec)
            sec["blocks"] = good
            cleaned.append(sec)
    except Exception as e:
        logger.warning("JSON 健康门禁失败（原样放行）：%s", e)
        return sections, []
    return cleaned, warnings
