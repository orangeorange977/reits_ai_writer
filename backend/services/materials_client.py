"""证明材料（申报材料文件夹）读取

给 Kimi 两个能力：列目录、抽取某个文件的文本。全部限定在"申报材料根目录"内，
带路径穿越防护（不会读到根目录以外）。文本类文件（PDF文字层/Word/Excel/纯文本）能读；
扫描件（无文字层的 PDF/图片）读不出正文，会如实说明——那部分需要后续 OCR/视觉能力。
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_TEXT_EXT = {".txt", ".md", ".csv", ".json"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_MAX_TEXT = 20000     # 单个文档最多返回的字符数（避免撑爆上下文）
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
        # 另起一行的条件：编号段 / 上一行是标题 / 上一句已完且本行像标题或引导句；
        # 上一句没完（行尾非句末标点）则无条件并入（碎片行合并）。
        new_line = new_para or (buf and buf_is_title) or \
            (bool(buf) and bool(_END_STOP.search(buf)) and (is_title or is_heading))
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
