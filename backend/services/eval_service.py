"""
对比评测服务：把 AI 生成的章节（ch{n}.json）与"标准答案"docx 逐节对齐、
计算相似度/差异，并可调用大模型按评分维度打分。

存储（均在项目目录下，随数据卷备份）：
- workspace/projects/<pid>/benchmark/standard_ch{n}.docx   上传的标准答案原文
- workspace/projects/<pid>/benchmark/compare_ch{n}.json    对比结果缓存
- workspace/projects/<pid>/benchmark/scores_ch{n}.json     AI 打分历史（数组）
"""
import difflib
import json
import logging
import re
import time
from pathlib import Path

from backend.config import PROJECTS_DIR, safe_project_id

logger = logging.getLogger(__name__)

_CN_NUM = "一二三四五六七八九十"
_CHAPTER_RE = re.compile(rf"^([{_CN_NUM}])、\s*\S")            # 章标题：一、xxx
_SUB_RE = re.compile(rf"^（[{_CN_NUM}]+）\s*\S")                 # 小节：（一）xxx
_CN_NUM_MAP = {c: i + 1 for i, c in enumerate(_CN_NUM)}

# 展示/打分时的文本截断，避免超长载荷
_PANE_CAP = 2500      # 对比面板单侧最大字符
_EXCERPT_CAP = 1200   # 喂给评分模型的单节摘要上限
_CACHE_SCHEMA = 2     # 对比缓存结构版本：解析/过滤规则变化时+1，旧缓存自动作废


def _bench_dir(pid: str) -> Path:
    d = PROJECTS_DIR / safe_project_id(pid) / "benchmark"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(s: str) -> str:
    """相似度比较用的归一化：去空白。"""
    return re.sub(r"\s+", "", str(s or ""))


# ---------------------------------------------------------------- 标准答案解析

def _iter_body_items(doc):
    """按文档顺序遍历正文里的段落与表格，产出 ('p', para) / ('tbl', table)。"""
    from docx.document import Document as _Doc  # noqa: F401  仅类型提示
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "tbl", Table(child, doc)


def _table_text(tbl) -> str:
    lines = []
    for row in tbl.rows:
        cells = [re.sub(r"\s+", "", c.text) for c in row.cells]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def save_standard(pid: str, n: int, data: bytes, filename: str = "") -> dict:
    """保存标准答案 docx 并返回解析概况。"""
    d = _bench_dir(pid)
    path = d / f"standard_ch{n}.docx"
    path.write_bytes(data)
    (d / f"compare_ch{n}.json").unlink(missing_ok=True)   # 标准答案变了，对比缓存作废
    info = parse_standard(pid, n)
    return {
        "filename": filename or path.name,
        "size": path.stat().st_size,
        "chapter_title": info["chapter_title"],
        "section_count": len(info["sections"]),
    }


def parse_standard(pid: str, n: int) -> dict:
    """解析标准答案 docx 中第 n 章：章标题 + 小节列表（含表格文本）。
    找不到章边界时（上传的是单章文档），整份文档按一章处理。"""
    from docx import Document

    path = _bench_dir(pid) / f"standard_ch{n}.docx"
    if not path.exists():
        raise FileNotFoundError(f"第{n}章尚未上传标准答案")
    doc = Document(str(path))

    in_chapter = False
    found_boundary = False
    chapter_title = ""
    sections = []          # [{title, paras:[...]}]
    cur = None

    def _new_section(title: str):
        nonlocal cur
        cur = {"title": title.strip(), "paras": []}
        sections.append(cur)

    for kind, item in _iter_body_items(doc):
        if kind == "tbl":
            if in_chapter and cur is not None:
                cur["paras"].append(_table_text(item))
            continue
        text = item.text.strip()
        if not text:
            continue
        m = _CHAPTER_RE.match(text)
        if m:
            if _CN_NUM_MAP.get(m.group(1)) == n:
                in_chapter = True
                found_boundary = True
                chapter_title = text
                cur = None                # 空标题节延迟到首个正文段出现时再建，避免空行
                continue
            elif in_chapter:
                break                     # 下一章开始，本章结束
            else:
                continue                  # 还没到目标章
        if not in_chapter:
            continue
        if _SUB_RE.match(text):
            _new_section(text)
        else:
            if cur is None:
                _new_section("")
            cur["paras"].append(text)

    if not found_boundary:
        # 单章文档：重新整份解析，不设章边界
        sections = []
        cur = None
        for kind, item in _iter_body_items(doc):
            if kind == "tbl":
                if cur is not None:
                    cur["paras"].append(_table_text(item))
                continue
            text = item.text.strip()
            if not text:
                continue
            if _SUB_RE.match(text):
                _new_section(text)
            else:
                if cur is None:
                    _new_section("")
                cur["paras"].append(text)
        chapter_title = ""

    for s in sections:
        s["text"] = "\n".join(s["paras"])
        s.pop("paras", None)
    return {"chapter_title": chapter_title, "sections": sections}


def list_standards(pid: str) -> list:
    """已有标准答案的章节清单。"""
    d = _bench_dir(pid)
    out = []
    for p in sorted(d.glob("standard_ch*.docx")):
        m = re.match(r"standard_ch(\d+)\.docx", p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def delete_standard(pid: str, n: int) -> None:
    d = _bench_dir(pid)
    for name in (f"standard_ch{n}.docx", f"compare_ch{n}.json", f"scores_ch{n}.json"):
        (d / name).unlink(missing_ok=True)


# ---------------------------------------------------------------- 生成内容展平

def _block_text(b: dict) -> str:
    t = b.get("type")
    if t == "p":
        return str(b.get("text", "")).strip()
    if t == "kv":
        lines = []
        if b.get("caption"):
            lines.append(str(b["caption"]))
        for r in b.get("rows", []) or []:
            lines.append(f'{r.get("label", "")}：{r.get("value", "")}')
        return "\n".join(lines)
    if t == "grid":
        lines = []
        if b.get("caption"):
            lines.append(str(b["caption"]))
        hs = b.get("headers") or []
        if hs:
            lines.append(" | ".join(str(h) for h in hs))
        for row in b.get("rows", []) or []:
            cells = [(c.get("text", "") if isinstance(c, dict) else str(c)) for c in row]
            lines.append(" | ".join(cells))
        return "\n".join(lines)
    if t in ("figure", "image"):
        return str(b.get("caption") or "（图）")
    return str(b.get("text", "") or "").strip()


def load_generated(pid: str, n: int) -> list:
    """读 ch{n}.json，展平为 [{title, text, srcs}]；未生成时抛 FileNotFoundError。
    srcs 是各块的来源引注（生成时的溯源依据），供 AI 打分判断“有无依据”。"""
    p = PROJECTS_DIR / safe_project_id(pid) / f"ch{n}.json"
    if not p.exists():
        raise FileNotFoundError(f"第{n}章尚未生成内容")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    secs = data if isinstance(data, list) else data.get("sections", [])
    out = []
    for s in secs or []:
        blocks = s.get("blocks", []) or []
        text = "\n".join(t for t in (_block_text(b) for b in blocks) if t)
        srcs = [str(b.get("src", "")).strip() for b in blocks if str(b.get("src", "")).strip()]
        out.append({"title": str(s.get("title", "")).strip(), "text": text, "srcs": srcs})
    return out


# ---------------------------------------------------------------- 章节对齐与对比

def _align(gen_secs: list, std_secs: list) -> list:
    """生成小节 ↔ 标准小节对齐。返回行列表：
    {gen_title, std_title, status: matched|gen_only|std_only, gen_idx, std_idx}"""
    rows = []
    used_std = set()
    # 第一轮：标题完全一致 / 去括号编号后一致
    def _key(t):
        return re.sub(rf"^[（(][{_CN_NUM}]+[）)]\s*|^\d+[\.、]\s*", "", str(t or "")).strip()

    for gi, g in enumerate(gen_secs):
        hit = None
        for si, s in enumerate(std_secs):
            if si in used_std:
                continue
            if _key(g["title"]) and _key(g["title"]) == _key(s["title"]):
                hit = si
                break
        if hit is not None:
            used_std.add(hit)
            rows.append({"gen_idx": gi, "std_idx": hit, "status": "matched"})
    # 第二轮：剩余按相似度 >= 0.5 贪心配对
    left_g = [i for i in range(len(gen_secs)) if not any(r.get("gen_idx") == i for r in rows)]
    left_s = [i for i in range(len(std_secs)) if i not in used_std]
    pairs = []
    for gi in left_g:
        for si in left_s:
            r = difflib.SequenceMatcher(
                None, _key(gen_secs[gi]["title"]), _key(std_secs[si]["title"])).ratio()
            if r >= 0.5:
                pairs.append((r, gi, si))
    pairs.sort(reverse=True)
    for r, gi, si in pairs:
        if gi in left_g and si in left_s:
            left_g.remove(gi)
            left_s.remove(si)
            used_std.add(si)
            rows.append({"gen_idx": gi, "std_idx": si, "status": "matched"})
    for gi in left_g:
        rows.append({"gen_idx": gi, "std_idx": None, "status": "gen_only"})
    for si in left_s:
        rows.append({"gen_idx": None, "std_idx": si, "status": "std_only"})

    # 按标准小节顺序（多出的生成节插在其后）排好展示顺序
    def _pos(r):
        if r["std_idx"] is not None:
            return (r["std_idx"], 0)
        return (r["gen_idx"] + len(std_secs), 1)
    rows.sort(key=_pos)
    for r in rows:
        r["gen_title"] = gen_secs[r["gen_idx"]]["title"] if r["gen_idx"] is not None else ""
        r["std_title"] = std_secs[r["std_idx"]]["title"] if r["std_idx"] is not None else ""
    return rows


def _similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return 1.0
    if len(na) * len(nb) > 4_000_000:     # 超长文本降级：按行集合 Jaccard
        la, lb = set(a.splitlines()), set(b.splitlines())
        if not la and not lb:
            return 1.0
        return len(la & lb) / max(1, len(la | lb))
    return difflib.SequenceMatcher(None, na, nb).ratio()


def compare_chapter(pid: str, n: int, force: bool = False) -> dict:
    """对齐+相似度+差异标记；结果落盘缓存（标准/生成任一更新则重算）。"""
    d = _bench_dir(pid)
    cache = d / f"compare_ch{n}.json"
    std_path = d / f"standard_ch{n}.docx"
    gen_path = PROJECTS_DIR / safe_project_id(pid) / f"ch{n}.json"
    if not std_path.exists():
        raise FileNotFoundError(f"第{n}章尚未上传标准答案")
    if not gen_path.exists():
        raise FileNotFoundError(f"第{n}章尚未生成内容")

    if cache.exists() and not force:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8-sig"))
            if (cached.get("_meta", {}).get("std_mtime") == std_path.stat().st_mtime
                    and cached.get("_meta", {}).get("gen_mtime") == gen_path.stat().st_mtime
                    and cached.get("_meta", {}).get("schema") == _CACHE_SCHEMA):
                return cached
        except Exception:
            pass

    std = parse_standard(pid, n)
    gen_secs = load_generated(pid, n)
    std_secs = std["sections"]
    # 过滤双侧空节（标题空且正文空）：不展示也不计入统计
    def _nonempty(s):
        return bool(str(s.get("title", "")).strip() or _norm(s.get("text", "")))
    gen_secs = [s for s in gen_secs if _nonempty(s)]
    std_secs = [s for s in std_secs if _nonempty(s)]
    rows = _align(gen_secs, std_secs)

    sections = []
    sims = []
    for r in rows:
        gtext = gen_secs[r["gen_idx"]]["text"] if r["gen_idx"] is not None else ""
        stext = std_secs[r["std_idx"]]["text"] if r["std_idx"] is not None else ""
        sim = _similarity(gtext, stext) if r["status"] == "matched" else 0.0
        if r["status"] == "matched":
            sims.append(sim)
        sections.append({
            "status": r["status"],
            "similarity": round(sim, 3),
            "gen_title": r["gen_title"],
            "std_title": r["std_title"],
            "gen_text": gtext[:_PANE_CAP],
            "std_text": stext[:_PANE_CAP],
            "gen_chars": len(_norm(gtext)),
            "std_chars": len(_norm(stext)),
            "gen_truncated": len(gtext) > _PANE_CAP,
            "std_truncated": len(stext) > _PANE_CAP,
        })

    matched = sum(1 for r in rows if r["status"] == "matched")
    coverage = (matched / len(std_secs)) if std_secs else 0.0
    result = {
        "chapter": n,
        "chapter_title": std["chapter_title"],
        "summary": {
            "std_sections": len(std_secs),
            "gen_sections": len(gen_secs),
            "matched": matched,
            "gen_only": sum(1 for r in rows if r["status"] == "gen_only"),
            "std_only": sum(1 for r in rows if r["status"] == "std_only"),
            "coverage": round(coverage, 3),
            "avg_similarity": round(sum(sims) / len(sims), 3) if sims else 0.0,
        },
        "sections": sections,
        "_meta": {
            "schema": _CACHE_SCHEMA,
            "std_mtime": std_path.stat().st_mtime,
            "gen_mtime": gen_path.stat().st_mtime,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


# ---------------------------------------------------------------- AI 打分

_SRC_CAP = 800    # 每节喂给评分模型的依据清单字符上限
_SKILL_CAP = 9000  # 章skill文本喂给评分模型的上限（判断“skill是否有描述”）

_SCORE_SYSTEM = (
    "你是基础设施REITs发改委申报材料的资深评审专家。用户给你同一章节的两份内容："
    "【生成稿】是AI系统产出的草稿，【标准答案】是人工定稿的权威版本；"
    "另外提供：【生成稿依据】（生成时引用的材料来源清单，含原文摘录）和【本章skill】（写作要求）。\n"
    "**公平性总原则：只考有据可依的部分。**某个要点只有在满足下列任一条件时才算“有据可依”，"
    "缺失或写错才扣分：\n"
    "  a) 在【生成稿依据】的来源清单/原文摘录中能找到支撑（即AI本可以从材料中提取到）；\n"
    "  b) 【本章skill】明确要求输出的固定句式、小节结构、表格或填表规则。\n"
    "标准答案中的要点若既不在【生成稿依据】里、【本章skill】也没有描述（如依赖未上传的外部材料、"
    "人工线下补充的内容），归为“无依据要点”：列入 no_basis_points，**不得计入任何扣分**，"
    "也不得因为生成稿缺了它们而压低分数。\n"
    "**比对口径**：文字内容按**语义等价**判断——意思一致即可，措辞/句式不同不算差异、不算缺失；"
    "数字类关键要素（金额、日期、比例、面积、数量、年限、文号、证照编号等）要求**完全一致**，"
    "哪怕一位小数、一个单位的差别都算错误，要在 comment 中指出具体哪处数字不一致。\n"
    "请按以下维度给生成稿打分（各维度0-100，权重见括号，均只考有据可依的部分）：\n"
    "1. 内容覆盖度（30%）：标准答案中有据可依的小节、要点、表格在生成稿中是否齐全；"
    "缺失的有据要点在 missing_points 列出（每条注明依据出处）；\n"
    "2. 关键要素一致性（30%）：生成稿中写出的名称、数字、日期、文号、证照等关键信息，"
    "与标准答案、以及【生成稿依据】里的原文摘录是否一致，有无编造或错位；"
    "标准答案里的要素若在依据中查不到且生成稿未写，不算错；\n"
    "3. 表述规范度（20%）：文体是否贴合申报材料的正式公文语体，术语口径是否一致；\n"
    "4. 结构完整性（20%）：小节层级、表格结构、编号体系是否与标准答案一致"
    "（仅限 skill 有描述或依据中可推知的结构；标准答案独有的、skill未描述的结构差异不扣分）。\n"
    "只输出一个JSON对象，不要任何其他文字，格式：\n"
    '{"dimensions":[{"name":"内容覆盖度","weight":0.3,"score":0,"comment":""},'
    '{"name":"关键要素一致性","weight":0.3,"score":0,"comment":""},'
    '{"name":"表述规范度","weight":0.2,"score":0,"comment":""},'
    '{"name":"结构完整性","weight":0.2,"score":0,"comment":""}],'
    '"total":0,"summary":"","missing_points":["..."],"no_basis_points":["..."]}')


def _extract_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("评分模型未返回有效JSON")
    return json.loads(t[i:j + 1])


def score_chapter(pid: str, n: int) -> dict:
    """调大模型给第 n 章生成稿打分，结果追加进历史并返回本次结果。
    公平性：同时提供生成稿的来源依据（src）与本章 skill，评分只计有据可依的部分。"""
    from backend.services.kimi_client import chat
    from backend.services.skill_runner import get_selected_model

    comp = compare_chapter(pid, n)
    # 每节的来源依据清单（按标题对齐回对比行）
    src_by_title = {}
    try:
        for g in load_generated(pid, n):
            if g.get("srcs"):
                src_by_title[g["title"]] = "\n".join(g["srcs"])[:_SRC_CAP]
    except FileNotFoundError:
        pass
    # 本章 skill 文本（判断“skill 是否有描述”的依据）
    skill_text = ""
    try:
        from backend.services import pack_service
        skill_text = pack_service.reading_path(n).read_text(encoding="utf-8")[:_SKILL_CAP]
    except Exception as e:
        logger.warning(f"ch{n} 打分时读本章skill失败（不阻断）: {e}")

    parts = []
    for s in comp["sections"]:
        srcs = src_by_title.get(s.get("gen_title") or "", "")
        src_line = f"【生成稿依据】\n{srcs}\n" if srcs else "【生成稿依据】（本节无引注来源）\n"
        if s["status"] == "matched":
            parts.append(
                f"### 小节（生成稿标题）{s['gen_title']}\n"
                f"【生成稿】\n{s['gen_text'][:_EXCERPT_CAP]}\n"
                f"【标准答案】\n{s['std_text'][:_EXCERPT_CAP]}\n" + src_line)
        elif s["status"] == "std_only":
            parts.append(f"### 小节 {s['std_title']}\n【生成稿】（缺失）\n"
                         f"【标准答案】\n{s['std_text'][:_EXCERPT_CAP]}\n")
        else:
            parts.append(f"### 小节 {s['gen_title']}\n【生成稿】（标准答案无此节）\n"
                         f"{s['gen_text'][:_EXCERPT_CAP]}\n" + src_line)
    user_prompt = (
        f"以下是第{n}章的逐节对照。请严格按系统要求（尤其是公平性总原则）输出评分JSON。\n\n"
        + "===== 本章skill（写作要求，判断要点是否被skill描述）=====\n" + (skill_text or "（未取到）")
        + "\n\n===== 逐节对照 =====\n" + "\n".join(parts))

    model = get_selected_model()
    raw = chat([
        {"role": "system", "content": _SCORE_SYSTEM},
        {"role": "user", "content": user_prompt},
    ], model=model, temperature=0.2)
    try:
        score = _extract_json(raw)
    except Exception as e:
        logger.warning(f"ch{n} AI打分解析失败: {e}; raw={raw[:200]}")
        raise ValueError("评分模型返回格式异常，请重试") from e

    total = score.get("total")
    if not isinstance(total, (int, float)):
        dims = score.get("dimensions") or []
        total = sum(float(d.get("score", 0)) * float(d.get("weight", 0.25)) for d in dims)
    score["total"] = round(float(total), 1)
    score["model"] = model
    score["chapter"] = n
    score["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    d = _bench_dir(pid)
    hist_path = d / f"scores_ch{n}.json"
    hist = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8-sig"))
        except Exception:
            hist = []
    hist.append(score)
    hist = hist[-20:]                       # 最多保留 20 次
    hist_path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    return score


def get_scores(pid: str, n: int) -> list:
    p = _bench_dir(pid) / f"scores_ch{n}.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
