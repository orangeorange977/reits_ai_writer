"""证明材料（申报材料文件夹）读取

给 Kimi 两个能力：列目录、抽取某个文件的文本。全部限定在"申报材料根目录"内，
带路径穿越防护（不会读到根目录以外）。文本类文件（PDF文字层/Word/Excel/纯文本）能读；
扫描件（无文字层的 PDF/图片）读不出正文，会如实说明——那部分需要后续 OCR/视觉能力。
"""
import logging
import os
import re
from pathlib import Path

from backend.config import DATA_SOURCE_BASE

logger = logging.getLogger(__name__)

_TEXT_EXT = {".txt", ".md", ".csv", ".json"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_MAX_TEXT = 10000     # 单个文档最多返回的字符数（避免撑爆上下文；2026-08-13 由 20000 降半以控制多轮工具调用的累计 token 成本）
_MAX_FILES = 300      # list 最多返回的文件数
# 扫描件 OCR 控成本/时延：默认（未指定页码）只识别很少几页——大文件（审计报告等）绝不整篇 OCR，
# 否则一次视觉请求塞十几张图会超时/触发过载。需要更多页时由调用方用 pages 参数点名要。
_MAX_OCR_PAGES = 3            # 未指定 pages 时最多识别几页
_MAX_OCR_PAGES_EXPLICIT = 8   # 指定 pages 时单次最多识别几页
_OCR_DPI = 120               # 光栅化分辨率（越高越清但越重）


def _parse_pages(spec: str, total: int) -> list:
    """把 '1-3,5' 这类页码串解析成 0-based 页索引（去重、按序、限定有效范围）。空串返回 []。"""
    if not spec:
        return []
    nums = []
    for part in re.split(r"[,，;；\s]+", str(spec).strip()):
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–~至到]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            nums.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            nums.append(int(part))
    seen, out = set(), []
    for x in nums:
        if 1 <= x <= total and x not in seen:
            seen.add(x)
            out.append(x - 1)  # 转 0-based
    return out


def _ocr_local(images, query: str = "") -> str:
    """本地 OCR 兜底（tesseract 中英文）：不花钱、不依赖外部 API。
    精度不如视觉大模型（复杂表格/印章可能认错），但对承诺函/营业执照这类清晰扫描件够用。
    query 在本地模式无法“只答相关内容”，只能整页识别后原样返回。失败返回空串。"""
    try:
        import pytesseract
        from PIL import Image
        import io
        parts = []
        for i, img in enumerate(images, 1):
            try:
                t = pytesseract.image_to_string(
                    Image.open(io.BytesIO(img)).convert("RGB"), lang="chi_sim+eng")
            except Exception:
                # 个别容器缺中文语言包时退回纯英文识别，别整体失败
                t = pytesseract.image_to_string(Image.open(io.BytesIO(img)).convert("RGB"))
            t = (t or "").strip()
            if t:
                parts.append(f"〔第{i}张〕\n{t}" if len(images) > 1 else t)
        return "\n\n".join(parts).strip()
    except Exception as e:
        logger.warning(f"本地 OCR 失败: {e}")
        return ""


def _ocr(images, query: str = "") -> str:
    """图片文字识别：优先 Moonshot 视觉模型；未配置/key 失效时自动降级本地 OCR（免费兜底）。
    query 非空则只找与该问题相关的内容（仅视觉模型支持）。失败返回可读说明（不抛异常）。"""
    if not images:
        return ""
    from backend.config import MOONSHOT_API_KEY
    if MOONSHOT_API_KEY:
        try:
            from backend.services import kimi_client
            txt = (kimi_client.ocr_images(images, instruction=query) or "").strip()
            if txt:
                return txt
        except Exception as e:
            logger.warning(f"视觉识别失败（将转本地 OCR）: {e}")
    local = _ocr_local(images, query)
    if local:
        return "【以下为本地文字识别结果（精度有限，关键数字请核对原件）】\n" + local
    return ""


def _safe_join(root: Path, rel: str):
    """把相对路径安全地拼到 root 下；越权（跳出 root）则返回 None。"""
    try:
        base = root.resolve()
        p = (base / (rel or "")).resolve()
        if p == base or base in p.parents:
            return p
    except Exception:
        pass
    return None


def list_materials(root: Path, keyword: str = "") -> str:
    """列出申报材料根目录下的文件（相对路径），可用关键词过滤（匹配路径/文件名，忽略大小写）。"""
    if not root or not root.is_dir():
        return "（未配置或找不到申报材料目录）"
    kw = (keyword or "").strip().lower()
    files = []
    try:
        for p in root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if not kw or kw in rel.lower():
                    files.append(rel)
                    if len(files) >= _MAX_FILES:
                        break
    except Exception as e:
        return f"（列目录失败：{e}）"
    if not files:
        return f"（未找到匹配“{keyword}”的文件）" if kw else "（目录为空）"
    head = f"共 {len(files)} 个文件（相对路径，可直接传给 read_document）："
    return head + "\n" + "\n".join(f"- {f}" for f in files)


def read_document(root: Path, rel_path: str, pages: str = "", query: str = "",
                  anchor: str = "") -> str:
    """读取申报材料目录下某个文件的文本内容（PDF文字层/Word/Excel/文本）。
    anchor：关键词定位（仅对有文字层的 PDF 有效、最省）。给一个词（如“资产负债表”），
    代码直接在文字层搜到它所在页，返回该页及随后几页——三张连续报表一次锁定，无需 OCR。
    扫描件/用词不同会退回 pages/OCR 并说明原因。
    pages：仅对 PDF 有效，形如 '1-3' '5' '2,4'，只读/只识别这些页——大扫描件定点取页，避免整篇 OCR。
    query：读扫描件/图片时要找什么（如“落款日期和落款单位”“审计意见”“某科目金额”），
    视觉识别只回相关内容、不通读全页，输出更短更准。"""
    if not root or not root.is_dir():
        return "（未配置申报材料目录）"
    p = _safe_join(root, rel_path)
    if p is None or not p.is_file():
        return f"（找不到文件或路径越权：{rel_path}）"
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            return _read_pdf(p, pages, query, anchor)
        if ext == ".docx":
            return _read_docx(p)
        if ext in (".xlsx", ".xlsm"):
            return _read_xlsx(p)
        if ext in _TEXT_EXT:
            return p.read_text(encoding="utf-8", errors="ignore")[:_MAX_TEXT]
        if ext in _IMAGE_EXT:
            # 图片材料（营业执照、盖章页等）直接走视觉识别
            return _ocr([p.read_bytes()], query) or "（图片未识别出文字）"
        return f"（暂不支持读取该类型文件：{ext}；文件名本身可作为“文件全称”使用）"
    except Exception as e:
        logger.warning(f"读取材料失败 {rel_path}: {e}")
        return f"（读取失败：{e}）"


_ANCHOR_SPAN = 4   # 关键词命中页之后再多返回几页（三张连续报表一般够）

# ===== 中文段落重排：PDF 文字层/OCR 输出常把一句话拆成碎片行（“）”“，”单独成行），
# 按 Word 的观感把同一段落的碎片行合并成整段，保留标题/编号段。 =====
_PUNCT_ONLY = re.compile(r'^[\s)）\]】,，;；:：。．.、…—~～《〈「『“”‘’"\'!?！？/\\|\-—]+$')
_NEW_PARA = re.compile(
    r'^(\d{1,3}[\.、．)）\]】]|[一二三四五六七八九十百零]+[、．.)）]|'
    r'[（(]\s*[一二三四五六七八九十\d]+\s*[)）]|第[一二三四五六七八九十百\d]+[章节条款部分])')
_END_STOP = re.compile(r'[。；：！？…]$')
_JOIN_HEAD = '，,、；;。）)》」』…—“”‘’"\'《〈'


def _reflow_text(text: str) -> str:
    """把碎片行合并成自然段落（仿 Word 观感）：
    - 纯标点行、以标点/引号开头的行 → 并入上一行；
    - 编号/中文序号/章节号开头 → 新段落；
    - 上一行以句末标点结尾 → 新段落；
    - 短而无标点的行（疑似标题）→ 独立成行；
    - 其余并入当前段落。"""
    out, buf = [], ''
    buf_is_title = False
    for raw in (text or '').split('\n'):
        ln = raw.strip()
        if not ln:
            if buf:
                out.append(buf); buf = ''; buf_is_title = False
            continue
        if buf and not buf_is_title and (_PUNCT_ONLY.match(ln) or ln[0] in _JOIN_HEAD):
            buf += ln
            continue
        new_para = bool(_NEW_PARA.match(ln))
        is_title = len(ln) <= 24 and not re.search(r'[，,。；;：:！!？?、（）()“”‘’"\'《》〈〉「」『』]', ln)
        is_heading = len(ln) <= 30 and ln.endswith(('：', ':'))
        # 另起一行的条件：编号段 / 上一行是标题 / 本行是引导句（行尾冒号）/
        # 上一行是引导句（冒号后内容在 Word 里另起一段）/ 上一句已完且本行像标题；
        # 上一句没完（行尾非句末标点）则无条件并入（碎片行合并）。
        new_line = new_para or (buf and buf_is_title) or is_heading or \
            (bool(buf) and buf.endswith(('：', ':'))) or \
            (bool(buf) and bool(_END_STOP.search(buf)) and is_title)
        if buf and not new_line:
            buf += ln
        else:
            if buf:
                out.append(buf)
            buf = ln
            buf_is_title = is_title
    if buf:
        out.append(buf)
    return '\n'.join(out)


def ocr_page_text(fp: Path, page_idx: int) -> str:
    """扫描件单页 OCR 文本（免费本地 tesseract，带磁盘缓存：同一页只识别一次）。
    用于“摘录在第几页”的逐页搜索。"""
    import hashlib
    import fitz
    st = fp.stat()
    key = hashlib.md5(f"{fp}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()
    cache_dir = DATA_SOURCE_BASE / ".ocr_cache" / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    cf = cache_dir / f"p{page_idx}.txt"
    if cf.exists():
        return cf.read_text(encoding="utf-8", errors="ignore")
    doc = fitz.open(str(fp))
    try:
        pix = doc[page_idx].get_pixmap(dpi=100)
    finally:
        doc.close()
    txt = _ocr_local([pix.tobytes("png")])
    try:
        cf.write_text(txt or "", encoding="utf-8")
    except Exception:
        pass
    return txt or ""


def pdf_page_count(fp: Path) -> int:
    import fitz
    doc = fitz.open(str(fp))
    n = doc.page_count
    doc.close()
    return n


def ocr_page_highlight_box(fp: Path, page_idx: int, toks: list):
    """在指定页的 OCR 词级坐标里框出摘录位置，返回 [x0,y0,x1,y1]（dpi=100 坐标）。
    toks：特征词（数字/中文片段/模糊关键词）。
    多级兜底保证“页级投票命中就一定能画框”：①单行滑窗命中；②相邻 2~3 行合并命中
    （特征词被 OCR 行切断时，整页文本能中、单行永远不中，合并行可中）；
    ③命中区间按相邻归簇、取得分最高的簇（摘录所在段落），上下各扩一行。全不中才返回 None。"""
    import io
    import fitz
    import pytesseract
    from PIL import Image

    if not toks:
        return None
    wins = []
    for t in toks:
        for w in _tok_windows(t):
            if w not in wins:
                wins.append(w)
    if not wins:
        return None
    doc = fitz.open(str(fp))
    try:
        pix = doc[page_idx].get_pixmap(dpi=100)
    finally:
        doc.close()
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    d = pytesseract.image_to_data(img, lang="chi_sim+eng", output_type=pytesseract.Output.DICT)
    lines = {}  # (block,par,line) -> {text, box}
    for i in range(len(d["text"])):
        txt = (d["text"][i] or "").strip()
        if not txt:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        e = lines.setdefault(key, {"text": "", "box": None})
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        b = e["box"]
        e["box"] = [x, y, x + w, y + h] if b is None else [min(b[0], x), min(b[1], y), max(b[2], x + w), max(b[3], y + h)]
        e["text"] += txt
    lst = [{"nt": norm_q(v["text"]), "box": v["box"]} for v in lines.values() if v["box"]]

    def score(nt):
        return sum(1 for w in wins if w in nt)

    # ①单行；②相邻 2~3 行合并（同一起始行取最小能中的合并宽度）
    spans = []  # (起, 止, 得分)
    for i in range(len(lst)):
        for j in range(3):
            if i + j >= len(lst):
                break
            s = score("".join(x["nt"] for x in lst[i:i + j + 1]))
            if s:
                spans.append((i, i + j, s))
                break
    if not spans:
        return None
    # 相邻/重叠区间归簇，取得分最高的簇（即摘录所在段落）
    spans.sort()
    clusters = []
    cur = list(spans[0])
    for s0, s1, sc in spans[1:]:
        if s0 <= cur[1] + 1:
            cur[1] = max(cur[1], s1)
            cur[2] += sc
        else:
            clusters.append(cur)
            cur = [s0, s1, sc]
    clusters.append(cur)
    best = max(clusters, key=lambda c: c[2])
    lo = max(0, best[0] - 1)
    hi = min(len(lst) - 1, best[1] + 1)
    boxes = [lst[k]["box"] for k in range(lo, hi + 1)]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    W, H = img.size
    return [max(0, x0 - 12), max(0, y0 - 12), min(W, x1 + 12), min(H, y1 + 12)]


def pdf_has_text_layer(fp: Path) -> bool:
    import fitz
    doc = fitz.open(str(fp))
    try:
        return any(doc[i].get_text().strip() for i in range(min(doc.page_count, 6)))
    finally:
        doc.close()


# ===== 依据定位共享能力：搜页归一/特征词/文字层搜页/原文截取/文件解析，
# 供 projects.py（点击预览）与 skill_runner.py（生成后自检）共用 =====

def norm_q(s: str) -> str:
    """搜页归一：抹平空白/连字符/破折号/逗号（AI 摘录 “A18” vs 原文 “A-18”；
    千分位逗号 “23,400,737,827.40” vs “23400737827.40”）。"""
    return re.sub(r"[\s\-—–,，]", "", s or "")


def quote_tokens(quote: str):
    """摘录的特征词：数字（≥4位，千分位逗号先抹平避免拆碎）+ 中文片段（≥6字，最多8个）。
    片段再按虚词拆出核心专名（“以国际信息云聚核港”→“国际信息云聚核港”），供搜页与高亮框共用。"""
    nums = [t for t in re.findall(r"\d+(?:\.\d+)?", norm_q(quote or "")) if len(t) >= 4]
    frags = []
    for f in re.split(r"[，。；：、！？…〈〉《》()（）\"\u201c\u201d\s]+", quote or ""):
        f = f.strip()
        if len(f) >= 6 and f not in frags:
            frags.append(f)
        for sub in re.split(r"[以与其及作为系指在为或是等]+", f):
            sub = sub.strip()
            if 6 <= len(sub) <= 14 and sub not in frags:
                frags.append(sub)
    return nums, frags[:8]


def fuzzy_quote_tokens(quote: str) -> list:
    """模糊定位用的关键词级特征词（3~12字）：中文串按标点+虚词切出核心专名/术语，另加≥2位数字。
    逐字/片段投票不中时（AI 摘录是改写/概括），用“关键词重叠度”搜页+框大致位置。"""
    q = re.sub(r"[（(]\s*第\s*\d+\s*页\s*[）)]", "", quote or "")
    toks = []
    for t in re.findall(r"\d+(?:\.\d+)?", q):
        if len(t) >= 2 and t not in toks:
            toks.append(t)
    for seg in re.split(r"[，。；：、！？…〈〉《》()（）\"“”‘’\s]+", q):
        seg = seg.strip()
        if len(seg) < 3:
            continue
        # 虚词切开保留核心专名（“经查询信用中国网站”→“信用中国网站”；“无重大违法违规记录”→“重大违法违规记录”）
        for sub in re.split(r"[以与其及作为系指在为或是经近年在等无未不的了之于对查询出具]+", seg):
            sub = sub.strip()
            if 3 <= len(sub) <= 12 and sub not in toks:
                toks.append(sub)
        if 3 <= len(seg) <= 12 and seg not in toks:
            toks.append(seg)
    return toks[:14]


def fuzzy_match_tokens(text_norm: str, toks: list) -> list:
    """归一化文本里命中的特征词子集。整词不中时用 5 字滑窗容忍 OCR 缺字/多字
    （如原文识别成“家企业信用信息公示系统”，整词“国家企业信用信息公示”不中、滑窗可中）。"""
    out = []
    for t in toks:
        ws = _tok_windows(t)
        if ws and any(w in (text_norm or "") for w in ws):
            out.append(t)
    return out


def _tok_windows(tok: str, w: int = 5) -> list:
    """特征词的归一化滑窗（短词整词、长词切 5 字窗），供容忍匹配/画框共用。"""
    t = norm_q(tok)
    if not t:
        return []
    if len(t) <= w:
        return [t]
    return [t[i:i + w] for i in range(len(t) - w + 1)]

def fuzzy_hit_threshold(toks: list) -> int:
    """模糊命中阈值：特征词越多要求命中越多，少时至少 2 个（防单关键词误中他页）。"""
    return 3 if len(toks) >= 6 else 2


# 概括性摘录弱命中停词：机构名/结构词 4 字窗（无主题区分度，不参与弱命中投票）
_WEAK_STOP_4 = {"有限公司", "有限责任", "法定代表", "统一社会", "社会信用", "基础设施",
                "不动产投", "产投资信", "投资信托", "信托基金", "发展改革", "改革委员",
                "监督管理", "项目公司", "原始权益"}


def weak_topic_tokens(quote: str) -> list:
    """概括性摘录的弱命中词表：特征词切 4 字窗（如“申报材料”），剔除机构/结构停词。
    仅在逐字/关键词全不中时兜底启用：摘录若是 AI 改写概括、原文无逐字对应，
    用主题词重叠找最相关页，避免“找不到”死胡同。"""
    out = []
    for t in fuzzy_quote_tokens(quote or ""):
        nt = norm_q(t)
        for i in range(len(nt) - 3):
            w = nt[i:i + 4]
            if w not in _WEAK_STOP_4 and w not in out:
                out.append(w)
    return out[:40]


def weak_topic_match(text_norm: str, wtoks: list) -> list:
    """页归一化文本命中的主题词子集（弱命中）。"""
    return [w for w in (wtoks or []) if w in (text_norm or "")]


def fuzzy_quote_page_hit(doc, n: int, quote: str):
    """文字层模糊搜页：按关键词重叠数取最优页，返回 (页码从1起, 命中特征词列表)。
    达阈值直接取最优页；都不达阈值时宽松兜底（重叠≥2，或≤6页小文档重叠≥1）取最高分页框大致位置。"""
    toks = fuzzy_quote_tokens(quote)
    if len(toks) < 2:
        return None
    need = fuzzy_hit_threshold(toks)
    best = None
    for i in range(n):
        tn = norm_q(doc[i].get_text())
        if len(tn) < 8:
            continue
        m = fuzzy_match_tokens(tn, toks)
        if m and (best is None or len(m) > len(best[1])):
            best = (i + 1, m)
    if best and (len(best[1]) >= need or len(best[1]) >= 2 or (n <= 6 and len(best[1]) >= 1)):
        return best
    return None


def quote_page_hit(doc, n: int, quote: str):
    """文字层搜页：逐字 / 去空白 / 片段投票（命中≥2 或单片段≥10字）。AI 摘录常是改写，逐字匹配太严。"""
    q = (quote or "").strip()
    if not q:
        return None
    qn = re.sub(r"\s+", "", q)
    for i in range(n):
        t = doc[i].get_text()
        if q in t or qn in re.sub(r"\s+", "", t):
            return i + 1
    nums, frags = quote_tokens(q)
    if not frags and not nums:
        return None
    for i in range(n):
        tn = norm_q(doc[i].get_text())
        if any(len(f) >= 10 and norm_q(f) in tn for f in frags) or sum(1 for f in frags if norm_q(f) in tn) >= 2 \
                or (nums and sum(1 for x in nums if x in tn) >= 2):
            return i + 1
    return None


def page_original_snippet(doc, page_idx: int, quote: str, max_len: int = 140) -> str:
    """在指定页的文字层里按片段/数字特征截取含摘录的连续原文行（去换行压空白），
    供生成自检时把 AI 改写过的摘录替换成可逐字命中的原文。找不到返回空串。"""
    nums, frags = quote_tokens(quote)
    cands = [norm_q(f) for f in frags if len(f) >= 6] + list(nums)
    qn = norm_q((quote or "").strip())[:16]
    if qn and len(qn) >= 6:
        cands.append(qn)
    if not cands:
        return ""
    lines = []
    for blk in doc[page_idx].get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if len(txt.strip()) >= 4:
                lines.append(txt.strip())
    hit = [False] * len(lines)
    for i, txt in enumerate(lines):
        nt = norm_q(txt)
        if any(c in nt or nt in c for c in cands):
            hit[i] = True
    if not any(hit):
        return ""
    s = hit.index(True)
    e = s
    for j in range(s + 1, min(len(lines), s + 6)):
        if hit[j]:
            e = j
        elif len(norm_q(lines[j])) >= 4:
            break
    snip = re.sub(r"\s+", "", "".join(lines[s:e + 1]))
    if len(snip) > max_len:
        center = next((snip.find(c) for c in cands if c in snip), 0)
        a = max(0, center - max_len // 2)
        snip = snip[a:a + max_len]
    return snip


def locate_quote_in_pdf(fp: Path, quote: str, cached_only: bool = False):
    """在 PDF 里定位摘录：返回 (页码从1起, 该页可逐字命中的原文片段, 是否逐字原文, 是否文字层PDF)。
    有文字层：逐字→片段投票搜页，再截取原文片段；
    扫描件：逐页 OCR（cached_only=True 时只用已缓存页，避免整篇 OCR 拖慢生成）。全找不到返回 None。"""
    import fitz
    q = (quote or "").strip()
    if not q or fp.suffix.lower() != ".pdf":
        return None
    doc = fitz.open(str(fp))
    try:
        n = doc.page_count
        if any(doc[i].get_text().strip() for i in range(min(n, 6))):
            hit = quote_page_hit(doc, n, q)
            if not hit:
                return None
            qn = norm_q(q)
            is_verbatim = False
            for i in range(n):
                if qn in norm_q(doc[i].get_text()):
                    is_verbatim = True
                    break
            return hit, page_original_snippet(doc, hit - 1, q), is_verbatim, True
    finally:
        doc.close()
    # 扫描件：用逐页 OCR 文本搜（与运行时搜页同一份磁盘缓存）；
    # cached_only=True 时短文档（≤12页，承诺函/证明类）允许主动 OCR 保底，长文档只用已缓存页
    import hashlib
    qn = norm_q(q)
    nums, frags = quote_tokens(q)
    n = pdf_page_count(fp)
    st = fp.stat()
    key = hashlib.md5(f"{fp}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()
    cache_dir = DATA_SOURCE_BASE / ".ocr_cache" / key
    allow_full = (not cached_only) or n <= 12
    for i in range(n):
        if not allow_full and not (cache_dir / f"p{i}.txt").exists():
            continue
        t = norm_q(ocr_page_text(fp, i))
        if not t:
            continue
        if qn in t:
            return i + 1, re.sub(r"\s+", "", q)[:140], True, False
        if (nums and any(x in t for x in nums)) or (frags and sum(1 for f in frags if norm_q(f) in t) >= 2):
            return i + 1, "", False, False
    return None


def resolve_material_ref(name: str, mat_root: Path):
    """依据里写的文件名/文件夹名 → 磁盘真实文件（返回相对 mat_root 的路径字符串）。
    与前端 _findMaterialPath 同源的多级规则：精确→包含→多文件混写拆分→编号+核心词→
    任意位置编号→描述性子串→核心词全含→文件夹级。找不到返回 None。"""
    name = re.sub(r"[《》]", "", name or "").strip().strip("；;，, ")
    # 路径后可能跟着 〈摘录〉：文件名本身含括号时 〈…〉 可能跨多层，按“最后一个 〉”切
    ia = name.find("〈")
    if ia >= 0:
        ie = name.rfind("〉")
        if ie > ia:
            name = name[:ia].strip()
    name = name.strip()
    if "/" in name:
        name = name.split("/")[-1].strip()
    if not name or len(name) < 3:
        return None
    files, dirs = [], set()
    for root, ds, fs in os.walk(mat_root):
        for f in fs:
            rel = str(Path(root, f).relative_to(mat_root))
            files.append(rel)
            dirs.add(str(Path(root).relative_to(mat_root)))
    dirs.discard(".")

    def base(p):
        return p.split("/")[-1]

    def stem(s):
        return re.sub(r"\.[^.]+$", "", s)

    def squish(s):
        # 空白无关归一：AI 写的路径常比真实文件名多/少空格（“建设 用地” vs “建设用地”）
        return re.sub(r"\s+", "", s or "")

    for p in files:
        if base(p) == name:
            return p
    for p in files:
        if squish(base(p)) == squish(name):
            return p
    for p in files:
        if name in base(p) or base(p) in name:
            return p
    s1 = stem(name)
    if len(s1) >= 4:
        for p in files:
            if s1 in stem(base(p)) or stem(base(p)) in s1:
                return p
        ns1 = squish(s1)
        for p in files:
            sp = squish(stem(base(p)))
            if ns1 in sp or sp in ns1:
                return p
    # 多文件混写（“法律意见书、不动产权证书、估价报告”）：拆开逐项递归；
    # 仅当各段都能解析时才采信，避免把文件名里的“及/和”（如 审计报告及财务报表）误拆
    for sep in ["、", "及", "和", "，"]:
        if sep in name:
            parts = [x.strip() for x in name.split(sep) if x.strip()]
            if len(parts) >= 2:
                rs = [resolve_material_ref(x, mat_root) for x in parts]
                if all(rs):
                    return rs[0]
    m = re.match(r"^(\d+(?:[-—]\d+)?)\s*[、.．]?(.+)$", name)
    if m:
        num, core = m.group(1).replace("—", "-"), stem(m.group(2).strip())
        if core:
            cands = [p for p in files if stem(base(p)).startswith(num) and core in stem(base(p))]
            if cands:
                return cands[0]
            cands = [p for p in files if ("-" + num) in ("-" + stem(base(p))) or stem(base(p)).startswith(num + "号")]
            if cands:
                return cands[0]
    m2 = re.search(r"(\d+(?:[-—]\d+)?)\s*号?", name)
    if m2:
        num2 = m2.group(1).replace("—", "-")
        cands = [p for p in files
                 if num2 in stem(base(p)) or num2.split("-")[-1] in stem(base(p)).split("-")]
        if cands:
            return cands[0]
    toks = [t for t in re.split(r"[\s，。、；：/]+", stem(name)) if len(t) >= 2]
    if toks:
        cands = [(sum(1 for t in toks if t in stem(base(p))), p) for p in files]
        cands = [(c, p) for c, p in cands if c >= min(2, len(toks))]
        if cands:
            return max(cands, key=lambda x: x[0])[1]
    nm2 = re.sub(r"^[号文件No.\s]+", "", name).strip()
    if len(nm2) >= 4:
        for d in dirs:
            dn = d.split("/")[-1]
            if dn == nm2 or dn in nm2 or nm2 in dn:
                under = sorted(p for p in files if p.startswith(d + "/"))
                if under:
                    return under[0]
    return None


def _read_pdf(p: Path, pages: str = "", query: str = "", anchor: str = "") -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(str(p))
    n_pages = doc.page_count
    has_text = any(doc[i].get_text().strip() for i in range(min(n_pages, 6)))
    anchor = (anchor or "").strip()

    # ① 关键词定位（仅文字层有效、最省）：跳到含 anchor 的页，返回它及随后几页——
    #    如“资产负债表”命中即锁定这一段（利润表/现金流量表通常紧随其后）。
    if anchor and has_text:
        hit = next((i for i in range(n_pages) if anchor in doc[i].get_text()), None)
        if hit is not None:
            hi = min(n_pages, hit + _ANCHOR_SPAN + 1)
            parts = [f"［第{j + 1}页］\n{_reflow_text(doc[j].get_text())}" for j in range(hit, hi)]
            doc.close()
            head = f"（已在第 {hit + 1} 页定位到“{anchor}”，返回第 {hit + 1}–{hi} 页文字。）\n"
            return (head + "\n".join(parts)).strip()[:_MAX_TEXT]

    want = _parse_pages(pages, n_pages)          # 指定的 0-based 页索引（空=未指定）
    scan_idxs = want if want else list(range(n_pages))

    # ② 文字层（很便宜）：有文字层直接返回，不做任何 OCR
    parts, total = [], 0
    for i in scan_idxs:
        t = _reflow_text(doc[i].get_text())
        parts.append(t)
        total += len(t)
        if total >= _MAX_TEXT:
            break
    text = "\n".join(parts).strip()
    if text:
        doc.close()
        note = ""
        if anchor:  # 有文字层但没搜到锚点：多半是用词不同（如无“合并”二字）
            note = (f"（注：文字层里没找到“{anchor}”，可能用词不同/无“合并”二字；"
                    f"以下为{'指定页' if want else '全文'}文字，请自行辨认三张报表。）\n")
        return (note + text)[:_MAX_TEXT]

    # ③ 没有文字层 → 扫描件：只把“真正需要的少数几页”转图片做 OCR，绝不整篇 OCR
    if want:
        ocr_idxs = want[:_MAX_OCR_PAGES_EXPLICIT]
    else:
        ocr_idxs = list(range(min(n_pages, _MAX_OCR_PAGES)))
    images = [doc[i].get_pixmap(dpi=_OCR_DPI).tobytes("png") for i in ocr_idxs]
    doc.close()
    ocr = _ocr(images, query)
    # 后台把本次读过的页写入逐页 OCR 缓存（免费本地 tesseract）：
    # 生成后的依据自检/运行时搜页即可直接命中这些页，不阻塞本次读取
    try:
        import threading

        def _cache_pages(fp=str(p), idxs=list(ocr_idxs)):
            for i in idxs:
                try:
                    ocr_page_text(Path(fp), i)
                except Exception:
                    pass
        threading.Thread(target=_cache_pages, daemon=True).start()
    except Exception:
        pass
    if not ocr:
        return (f"（该 PDF 疑似扫描件（共 {n_pages} 页），视觉识别未返回文字。"
                "文件名可作为“文件全称”使用；如需某页内容，请用 pages 指定页码重试。）")
    done = sorted(i + 1 for i in ocr_idxs)        # 1-based，供提示
    notes = []
    if anchor:  # 扫描件无法按关键词定位
        notes.append(f"该文件为扫描件，无法按关键词“{anchor}”自动定位；已识别第 {done} 页，"
                     "请据此判断报表在第几页后，用 pages 精确重读。")
    if want and len(want) > _MAX_OCR_PAGES_EXPLICIT:
        rest = sorted(i + 1 for i in want[_MAX_OCR_PAGES_EXPLICIT:])
        notes.append(f"你请求的页较多，扫描件单次最多识别 {_MAX_OCR_PAGES_EXPLICIT} 页，已识别第 {done} 页；"
                     f"其余第 {rest} 页请再调一次 pages 读取（分批小步读，别一次要太多页）。")
    elif not want and n_pages > _MAX_OCR_PAGES:
        notes.append(f"该文件共 {n_pages} 页，为控成本仅识别了第 {done} 页；如需其它页请用 pages 指定。")
    note = ("（注：" + " ".join(notes) + "）\n") if notes else ""
    return ("【以下为扫描件的视觉识别结果，供参考，请核对】\n" + note + _reflow_text(ocr)).strip()[:_MAX_TEXT]


def _read_docx(p: Path) -> str:
    import docx
    d = docx.Document(str(p))
    text = "\n".join(par.text for par in d.paragraphs)
    return text[:_MAX_TEXT] if text.strip() else "（Word 文档无文字内容）"


def _read_xlsx(p: Path) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    out, total = [], 0
    for ws in wb.worksheets:
        out.append(f"# 工作表：{ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                line = "\t".join(cells)
                out.append(line)
                total += len(line)
        if total >= _MAX_TEXT:
            break
    wb.close()
    return "\n".join(out)[:_MAX_TEXT]


def _read_pptx(p: Path) -> str:
    """从 .pptx 里按幻灯片顺序抽取文字（不依赖 python-pptx，直接解压读 slideN.xml）。"""
    import zipfile
    from html import unescape
    out, total = [], 0
    with zipfile.ZipFile(str(p)) as z:
        slides = sorted(
            [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda s: int(re.search(r"(\d+)", s).group(1)))
        for i, n in enumerate(slides, 1):
            xml = z.read(n).decode("utf-8", "ignore")
            lines = []
            for para in re.findall(r"<a:p[ >].*?</a:p>", xml, re.DOTALL):
                runs = re.findall(r"<a:t>(.*?)</a:t>", para, re.DOTALL)
                line = unescape("".join(runs)).strip()
                if line:
                    lines.append(line)
            if lines:
                block = f"【第{i}页】\n" + "\n".join(lines)
                out.append(block)
                total += len(block)
                if total >= _MAX_TEXT:
                    break
    text = "\n\n".join(out).strip()
    return text[:_MAX_TEXT] if text else "（PPT 未提取到文字）"


def extract_file_text(p: Path, query: str = "") -> str:
    """通用文件取文本：按扩展名分派（PDF/Word/Excel/PPT/文本/图片OCR）。用于 AI 辅助读取用户上传的素材。"""
    ext = p.suffix.lower()
    try:
        if ext == ".pdf":
            return _read_pdf(p, query=query)
        if ext == ".docx":
            return _read_docx(p)
        if ext in (".xlsx", ".xlsm"):
            return _read_xlsx(p)
        if ext == ".pptx":
            return _read_pptx(p)
        if ext in _TEXT_EXT:
            return p.read_text(encoding="utf-8", errors="ignore")[:_MAX_TEXT]
        if ext in _IMAGE_EXT:
            return _ocr([p.read_bytes()], query) or "（图片未识别出文字）"
        if ext in (".doc", ".ppt", ".xls"):
            return f"（旧版 {ext} 格式暂不支持解析，请在 Office 里另存为 {ext}x 后再上传）"
        return f"（暂不支持的文件类型：{ext}）"
    except Exception as e:
        logger.warning(f"解析上传文件失败 {p.name}: {e}")
        return f"（解析失败：{e}）"


def fetch_url_text(url: str, limit: int = _MAX_TEXT) -> str:
    """抓取一个网页链接的正文文字（去掉脚本/样式/导航等）。仅支持 http/https。"""
    url = (url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"（跳过无效链接：{url}）"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) REIT-AI/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            charset = resp.headers.get_content_charset()
            raw = resp.read(4_000_000)  # 最多 4MB，防超大页
    except Exception as e:
        return f"（抓取链接失败：{e}）"
    text = ""
    try:
        from lxml import html as _lh
        doc = _lh.fromstring(raw)
        for bad in doc.xpath("//script|//style|//noscript|//nav|//header|//footer|//aside|//form"):
            parent = bad.getparent()
            if parent is not None:
                parent.remove(bad)
        text = doc.text_content()
    except Exception:
        try:
            text = re.sub(r"<[^>]+>", " ", raw.decode(charset or "utf-8", "ignore"))
        except Exception as e:
            return f"（网页解析失败：{e}）"
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    text = text.strip()
    return text[:limit] if text else "（该网页未提取到正文文字）"
