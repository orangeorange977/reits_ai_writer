"""
Skill 执行器 - 让 Kimi 按 reits-reading 各章 SKILL.md 的要求生成章节内容

第一版只做第一章（reits-reading-ch1），且只喂 planning.md + 该章 SKILL.md，
不去解析证明材料/扫描件——因为第一章绝大部分字段在 planning.md 里已经给全了。
这样先验证"Kimi 能按 SKILL.md 把 planning 里的值填进章节结构"这个最核心的环节。
"""
import json
import logging
import re
from pathlib import Path
from html.parser import HTMLParser

from backend.config import (DATA_SOURCE_BASE, PROJECTS_DIR, safe_project_id,
                            MOONSHOT_MODEL)
from backend.services.kimi_client import chat, chat_with_tools
from backend.services import summary_service, tianyancha_client, materials_client
from backend.services import pack_service

logger = logging.getLogger(__name__)

# 各章配置来自默认模板包的 chapters.json：title 必须与官方模板里的 Heading1 一字不差；
# next 是下一章标题（界定本章在模板里的起止范围，最后一章为 None）；
# reading 是该章写作要求在包内的相对路径。
CHAPTERS = pack_service.get_chapters()


def chapters_for(pack_id: str = None) -> dict:
    """指定模板包的章节结构（None=默认包）；项目绑定了哪个包就用哪个包。"""
    return pack_service.get_chapters(pack_id)


def _project_dir(project_id: str = None) -> Path:
    """项目数据目录（workspace/projects/<项目ID>/），按项目隔离；
    未传/空值/非法值时用默认项目目录。"""
    return PROJECTS_DIR / safe_project_id(project_id)


def chapter_json_path(n: int, project_id: str = None) -> Path:
    """某章的结构化 JSON 路径（按项目隔离）。"""
    return _project_dir(project_id) / f"ch{n}.json"


def chapter_docx_path(n: int, project_id: str = None) -> Path:
    """某章生成的 Word 输出路径（按项目隔离）；目录不存在时自动创建，
    避免新项目首次预览/生成时因 output/ 缺失而写文件失败。"""
    path = _project_dir(project_id) / "output" / f"ch{n}_output.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

# 网页上选择的 Kimi 模型（全局设置，持久化在 workspace 根；各章生成都用它，
# 缺省用 .env 里的 MOONSHOT_MODEL）
_MODEL_SETTING_PATH = DATA_SOURCE_BASE / "model_setting.json"


def get_selected_model() -> str:
    """当前所选模型：优先读网页保存的，缺省回退到 .env 的 MOONSHOT_MODEL。"""
    try:
        if _MODEL_SETTING_PATH.exists():
            data = json.loads(_MODEL_SETTING_PATH.read_text(encoding="utf-8-sig"))
            m = (data or {}).get("model")
            if m:
                return m
    except Exception as e:
        logger.warning(f"读取模型设置失败: {e}")
    return MOONSHOT_MODEL


def set_selected_model(model: str) -> None:
    _MODEL_SETTING_PATH.write_text(
        json.dumps({"model": model}, ensure_ascii=False), encoding="utf-8"
    )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _esc_html(s: str) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _esc_attr(s: str) -> str:
    return _esc_html(s).replace('"', "&quot;")


# 脚注标记：段落字符串里用私用区字符夹住脚注文本（与模板包内 web_render 一致）
_FN_OPEN = chr(0xE010)
_FN_CLOSE = chr(0xE011)
_FN_RE = re.compile(_FN_OPEN + r"(.*?)" + _FN_CLOSE, re.DOTALL)

# 图块标记：整段就是一张框图，中间夹 base64(PNG) + 分隔符 + base64(drawio XML)
# PNG 给 Word/显示用，XML 供重新编辑用（与 web_render 一致）
_DIAGRAM_OPEN = chr(0xE012)
_DIAGRAM_CLOSE = chr(0xE013)
_DIAGRAM_SEP = chr(0x1F)  # 单元分隔符，不会出现在 base64 里
_DIAGRAM_RE = re.compile(_DIAGRAM_OPEN + r"(.*?)" + _DIAGRAM_CLOSE, re.DOTALL)


def _para_to_html(text: str, counter: list) -> str:
    """段落字符串（含脚注标记）-> HTML：脚注标记转成 <sup class="fn-ref"> 上标编号。"""
    out = []
    pos = 0
    for m in _FN_RE.finditer(text or ""):
        if m.start() > pos:
            out.append(_esc_html(text[pos:m.start()]))
        counter[0] += 1
        out.append(
            f'<sup class="fn-ref" data-fn="{_esc_attr(m.group(1))}" '
            f'contenteditable="false">{counter[0]}</sup>'
        )
        pos = m.end()
    if pos < len(text or ""):
        out.append(_esc_html(text[pos:]))
    return "".join(out)


# ---- reading skill JSON <-> 网页富文本 的互转 ----

def _section_blocks(sec: dict) -> list:
    """一节的有序块列表：优先用 sec['blocks']；否则按旧字段(段落→两列表→多列表)推导，兼容老数据。
    块类型：{type:p,text} / {type:kv,caption,rows:[{label,value}]} / {type:grid,caption,headers,rows}"""
    if isinstance(sec.get("blocks"), list):
        return sec["blocks"]
    blocks = []
    for p in sec.get("paragraphs", []) or []:
        blocks.append({"type": "p", "text": p})
    if sec.get("table"):
        blocks.append({"type": "kv", "caption": sec.get("table_caption", ""), "rows": sec["table"]})
    for g in sec.get("grid_tables", []) or []:
        blocks.append({"type": "grid", "caption": g.get("caption", ""),
                       "headers": g.get("headers", []) or [], "rows": g.get("rows", []) or []})
    return blocks


def _kv_rows_html(rows):
    return "".join(
        f"<tr><td>{_esc_html(r.get('label', ''))}</td>"
        f"<td>{_esc_html(r.get('value', ''))}</td></tr>" for r in (rows or []))


def _grid_rows_html(headers, rows):
    thead = ""
    if headers:
        thead = "<thead><tr>" + "".join(
            f"<th>{_esc_html(h)}</th>" for h in headers) + "</tr></thead>"
    body = ""
    for row in (rows or []):
        cells = ""
        for c in (row or []):
            if isinstance(c, dict):
                cs = int(c.get("colspan", 1) or 1)
                rs = int(c.get("rowspan", 1) or 1)
                attr = (f' colspan="{cs}"' if cs > 1 else "") + \
                       (f' rowspan="{rs}"' if rs > 1 else "")
                cells += f"<td{attr}>{_esc_html(c.get('text', ''))}</td>"
            else:
                cells += f"<td>{_esc_html(c)}</td>"
        body += f"<tr>{cells}</tr>"
    return thead, body


def _block_to_html(blk, fn_counter):
    t = blk.get("type")
    if t == "p":
        text = blk.get("text", "")
        dm = _DIAGRAM_RE.fullmatch(text or "")
        if dm:
            png_b64, _, xml_b64 = dm.group(1).partition(_DIAGRAM_SEP)
            return (f'<div class="doc-diagram" contenteditable="false" '
                    f'data-png="{png_b64}" data-xml="{xml_b64}">'
                    f'<img src="data:image/png;base64,{png_b64}" alt="框图"></div>')
        return f"<p>{_para_to_html(text, fn_counter)}</p>"
    if t == "kv":
        cap = _esc_html(blk.get("caption", ""))
        cap_html = f"<caption>{cap}</caption>" if cap else ""
        return (f'<table class="doc-table">{cap_html}'
                f'<tbody>{_kv_rows_html(blk.get("rows"))}</tbody></table>')
    if t == "grid":
        cap = _esc_html(blk.get("caption", ""))
        cap_html = f"<caption>{cap}</caption>" if cap else ""
        thead, body = _grid_rows_html(blk.get("headers", []) or [], blk.get("rows", []) or [])
        return f'<table class="doc-grid-table">{cap_html}{thead}<tbody>{body}</tbody></table>'
    return ""


def _sections_to_html(sections: list) -> list:
    """结构化 sections -> 每个子标题一块可读富文本 [{id,title,html}]（按有序块渲染，供编辑区显示）。"""
    out = []
    fn_counter = [0]  # 脚注编号跨整章连续
    for sec in sections:
        parts = [_block_to_html(b, fn_counter) for b in _section_blocks(sec)]
        out.append({
            "id": sec.get("id", ""),
            "title": sec.get("title", ""),
            "html": "".join(p for p in parts if p) or "<p></p>",
        })
    return out


class _HTMLToBlocks(HTMLParser):
    """把编辑区 HTML 解析成**有序块列表** blocks：段落 {type:p,text} /
    两列表 {type:kv,caption,rows:[{label,value}]} / 多列表 {type:grid,caption,headers,rows}，
    按它们在编辑区里出现的先后顺序排列（段落和表格穿插）。"""
    def __init__(self):
        super().__init__()
        self.blocks = []
        self._buf = []
        self._in_table = False
        self._table_kind = None  # "kv" 键值表 / "grid" 多列表
        self._grid = None        # 当前正在解析的多列表 {caption,headers,rows}
        self._kv = None          # 当前正在解析的两列表 {caption,rows}
        self._in_thead = False
        self._in_caption = False
        self._cap_buf = []
        self._row = None
        self._cell = None
        self._cell_span = (1, 1)  # 当前单元格的 (colspan, rowspan)
        self._in_fn = False  # 正处在脚注 <sup> 内（其可见编号要跳过）
        self._in_diagram = False  # 正处在图块 <div class="doc-diagram"> 内（内部 SVG 全部忽略）

    def _target(self):
        """当前文本应写入的缓冲：表格单元格 或 段落缓冲。"""
        if self._in_table and self._cell is not None:
            return self._cell
        return self._buf

    def handle_starttag(self, tag, attrs):
        if self._in_diagram:
            return  # 图块内部（svg/rect/text…）一律忽略
        if tag == "div" and "doc-diagram" in (dict(attrs).get("class") or ""):
            # 图块起始：flush 当前段落，把 PNG+XML(base64) 作为独立段落块存下
            self._flush_para()
            ad = dict(attrs)
            png = ad.get("data-png") or ""
            xml = ad.get("data-xml") or ""
            self.blocks.append({"type": "p",
                                "text": _DIAGRAM_OPEN + png + _DIAGRAM_SEP + xml + _DIAGRAM_CLOSE})
            self._in_diagram = True
            return
        if tag == "table":
            self._flush_para()
            self._in_table = True
            cls = dict(attrs).get("class") or ""
            if "doc-grid-table" in cls:
                self._table_kind = "grid"
                self._grid = {"caption": "", "headers": [], "rows": []}
            else:
                self._table_kind = "kv"
                self._kv = {"caption": "", "rows": []}
        elif tag == "caption" and self._in_table:
            self._in_caption = True
            self._cap_buf = []
        elif tag == "thead" and self._in_table:
            self._in_thead = True
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in ("td", "th") and self._in_table:
            self._cell = []
            ad = dict(attrs)

            def _sp(k):
                try:
                    return max(1, int(ad.get(k, 1)))
                except (TypeError, ValueError):
                    return 1
            self._cell_span = (_sp("colspan"), _sp("rowspan"))
        elif tag == "sup":
            ad = dict(attrs)
            if "fn-ref" in (ad.get("class") or ""):
                # 脚注：把脚注文本以标记形式嵌回段落字符串，跳过其可见编号
                self._target().append(_FN_OPEN + (ad.get("data-fn") or "") + _FN_CLOSE)
                self._in_fn = True
        elif tag == "br":
            self._target().append("\n")
        elif tag in ("p", "div") and not self._in_table:
            self._flush_para()

    def handle_endtag(self, tag):
        if self._in_diagram:
            # 图块内 SVG 无 div，第一个 </div> 即图块结束
            if tag == "div":
                self._in_diagram = False
            return
        if tag == "caption" and self._in_table:
            cap = "".join(self._cap_buf).strip()
            if self._grid is not None:
                self._grid["caption"] = cap
            elif self._kv is not None:
                self._kv["caption"] = cap
            self._in_caption = False
        elif tag == "table" and self._in_table:
            # 表格结束：作为一个有序块追加到 blocks（保持它在正文中的位置）
            if self._table_kind == "grid" and self._grid is not None:
                self.blocks.append({"type": "grid", **self._grid})
            elif self._kv is not None:
                self.blocks.append({"type": "kv", "caption": self._kv["caption"],
                                    "rows": self._kv["rows"]})
            self._in_table = False
            self._table_kind = None
            self._grid = None
            self._kv = None
            self._in_thead = False
        elif tag == "thead" and self._in_table:
            self._in_thead = False
        elif tag in ("td", "th") and self._in_table and self._row is not None:
            cs, rs = self._cell_span
            self._row.append(("".join(self._cell).strip(), cs, rs))
            self._cell = None
            self._cell_span = (1, 1)
        elif tag == "tr" and self._in_table and self._row is not None:
            if self._table_kind == "grid" and self._grid is not None:
                if self._in_thead:
                    self._grid["headers"] = [t for (t, _c, _r) in self._row]
                else:
                    cells = []
                    for (t, cs, rs) in self._row:
                        if cs > 1 or rs > 1:
                            cells.append({"text": t, "colspan": cs, "rowspan": rs})
                        else:
                            cells.append(t)
                    self._grid["rows"].append(cells)
            elif self._kv is not None:
                label = self._row[0][0] if len(self._row) > 0 else ""
                value = self._row[1][0] if len(self._row) > 1 else ""
                self._kv["rows"].append({"label": label, "value": value})
            self._row = None
        elif tag == "sup":
            self._in_fn = False
        elif tag in ("p", "div") and not self._in_table:
            self._flush_para()

    def handle_data(self, data):
        if self._in_diagram:
            return  # 图块内部文字（SVG 里的框内文字等）不是正文
        if self._in_caption:
            self._cap_buf.append(data)  # 多列表标题文字
            return
        if self._in_fn:
            return  # 脚注上标里的编号不是正文，丢弃
        if self._in_table and self._cell is not None:
            self._cell.append(data)
        elif not self._in_table:
            self._buf.append(data)

    def _flush_para(self):
        text = "".join(self._buf).strip()
        self._buf = []
        if text:
            self.blocks.append({"type": "p", "text": text})

    def result(self):
        self._flush_para()
        return self.blocks


def _html_sections_to_structured(html_sections: list, chapter_title: str = "") -> dict:
    """网页富文本 [{id,title,html}] -> reading skill 结构化 JSON（每节一个有序 blocks 列表）。"""
    sections = []
    for sec in html_sections:
        parser = _HTMLToBlocks()
        parser.feed(sec.get("html", "") or "")
        sections.append({
            "id": sec.get("id", ""),
            "title": sec.get("title", ""),
            "blocks": parser.result(),
        })
    return {"chapter": chapter_title, "sections": sections}


def _load_json(n: int, project_id: str = None) -> dict:
    path = chapter_json_path(n, project_id)
    if path.exists():
        try:
            # utf-8-sig 兼容带 BOM 的文件（外部编辑器可能写入 BOM）
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            logger.warning(f"读取 ch{n}.json 失败: {e}")
    return {}


def _save_json(n: int, data: dict, project_id: str = None) -> None:
    path = chapter_json_path(n, project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _skeleton_section(n: int, i: int, title: str, tpl_entries: list) -> dict:
    """还没生成的小标题：用官方模板里该小标题下的表格骨架（空表）铺出 blocks，
    让编辑区即便没生成也能看到本节有哪些表、可直接手填。"""
    blocks = []
    for e in (tpl_entries or []):
        if e.get("kind") == "kv":
            blocks.append({
                "type": "kv", "caption": e.get("caption", ""),
                "rows": [{"label": (r[0] if r else ""), "value": ""} for r in e.get("rows", [])],
            })
        elif e.get("kind") == "grid":
            blocks.append({
                "type": "grid", "caption": e.get("caption", ""),
                "headers": list(e.get("headers", [])),
                "rows": [list(r) for r in e.get("rows", [])],
            })
    return {"id": f"{n}-{i}", "title": title, "blocks": blocks}


# 被误当成小标题的“编号项”：如 “1.奥飞数据”“2.固安聚龙”“（1）基本信息”“表3 ……”。
# 这些是某个模板小标题（如“（三）发起人（原始权益人）情况”）下的内容/枚举项，不是章节小标题，
# 不能各占一行 section——编辑区的小标题结构固定＝模板的（一）（二）…，多出来的一律并回上一节。
_ENUM_TITLE_RE = re.compile(
    r"^\s*(?:\d+[.．、]|[（(]\d+[）)]|表[#＃0-9A-Za-z])")


def _fold_enumerated_sections(sections: list, subtitles: list = None) -> list:
    """把被 Kimi 误当成 section 的编号项（“1.奥飞数据”等）并回它前面的正式小标题：
    标题变成该节 blocks 里的一个 p 段落，其原有 blocks 依次接在后面。
    这样编辑区的小标题行始终＝模板固定结构，公司枚举项落在（三）内部。
    subtitles 给出时，只有“既匹配编号样式、又不在模板小标题里”的才折叠（多一层保险）。"""
    tpl = {(t or "").strip() for t in (subtitles or [])}
    out = []
    for s in sections or []:
        title = (s.get("title") or "").strip()
        is_enum = bool(title) and bool(_ENUM_TITLE_RE.match(title)) and title not in tpl
        if out and is_enum:
            prev = out[-1]
            prev.setdefault("blocks", [])
            if title:
                prev["blocks"].append({"type": "p", "text": title})
            prev["blocks"].extend(s.get("blocks", []) or [])
        else:
            out.append(dict(s, blocks=list(s.get("blocks", []) or [])))
    return out


def get_chapter_content(n: int, subtitles: list = None,
                        template_tables: dict = None, table_start: int = 1,
                        project_id: str = None) -> dict:
    """给网页编辑区：每个子标题一块可读富文本。

    subtitles（来自官方模板的本章小标题）如果给出，则以它为权威骨架：按模板顺序铺出
    各小标题的编辑区，已生成/保存的内容按标题合并进去，没内容的就是空编辑区——这样
    还没生成时点开章节也能看到该章的小标题结构。

    template_tables（来自官方模板的多列表骨架 {小标题:[grid,...]}）如果给出，则给每个
    小标题补上它下面的多列表（空表），编辑区即便还没生成也能看到并编辑这些表格。

    table_start：本章第一张表在全篇里的表号（=模板中本章之前已有的表数+1），编辑区据此
    把表标题里的编号占位（表#/表c…）显示成连续序号。
    """
    template_tables = template_tables or {}
    saved = _load_json(n, project_id).get("sections", []) or []
    # 兜底：把误升级成 section 的编号项（“1.奥飞数据”等）折回其所属的模板小标题，锁定小标题结构
    saved = _fold_enumerated_sections(saved, subtitles)

    if subtitles:
        if saved:
            # 已生成：按 Kimi 产出的 JSON 顺序显示（reading 内容为准，"模板没有的新标题"也在其位置）；
            # 模板小标题若被漏掉，末尾补一个空的（带模板表格骨架），保证都能看到。
            struct = []
            seen = set()
            for s in saved:
                struct.append(s)
                seen.add(s.get("title", "").strip())
            for i, title in enumerate(subtitles, 1):
                if title.strip() not in seen:
                    struct.append(_skeleton_section(n, i, title, template_tables.get(title.strip())))
            source = "ready"
        else:
            # 还没生成：按模板小标题铺出空骨架（含模板表格）
            struct = [_skeleton_section(n, i, title, template_tables.get(title.strip()))
                      for i, title in enumerate(subtitles, 1)]
            source = "template"
        return {"source": source,
                "sections": _sections_to_html(_number_captions(struct, table_start))}

    # 没有模板子标题：回退到已保存内容
    if not saved:
        return {"source": "none", "sections": []}
    return {"source": "ready",
            "sections": _sections_to_html(_number_captions(saved, table_start))}


def get_chapter_structured(n: int, project_id: str = None) -> list:
    """给写作渲染：返回某章的结构化 sections（表标题编号由 web_render 全篇统一重排）。"""
    return _load_json(n, project_id).get("sections", [])


def save_chapter_content(n: int, html_sections: list, project_id: str = None,
                         pack_id: str = None) -> None:
    """网页保存：把编辑区 HTML 转回结构化 JSON（有序块）并落盘。"""
    title = chapters_for(pack_id).get(n, {}).get("title", "")
    _save_json(n, _html_sections_to_structured(html_sections, title), project_id)


def _format_saved_summary(project_id: str = None) -> str:
    """把用户已保存的摘要表/释义/其他基本信息格式化成文本，供拼进 prompt。"""
    saved = summary_service.get_summary_data(project_id)  # 优先返回已保存版本
    if not saved:
        return ""

    def block(title, rows):
        lines = [f"## {title}"]
        for r in rows or []:
            lines.append(f"- {r.get('label', '')}：{r.get('value', '')}")
        return "\n".join(lines)

    return "\n\n".join([
        block("摘要表", saved.get("summary_table")),
        block("释义", saved.get("glossary")),
        block("其他基本信息", saved.get("other_info")),
    ])


# 要求 Kimi 严格输出的 JSON 结构（对所有章节通用；子标题/表格以该章 SKILL.md 为准）
def _output_contract(chapter_title: str) -> str:
    return (
        "你必须只输出一个 JSON 对象（不要有任何解释文字、不要用```代码块包裹），结构如下：\n"
        "{\n"
        f'  "chapter": "{chapter_title}",\n'
        '  "sections": [\n'
        '    {\n'
        '      "id": "1",\n'
        '      "title": "（一）……",\n'
        '      "blocks": [\n'
        '        {"type": "p", "text": "正文段落；“1.基本信息”“（1）……”这类编号小标题也各作为一个 p 段落"},\n'
        '        {"type": "kv", "caption": "表#  ……", "rows": [{"label": "字段名（与SKILL.md一字不差）", "value": "……"}]},\n'
        '        {"type": "grid", "caption": "表#  ……",\n'
        '         "headers": ["列1表头", "列2表头", "……（与SKILL.md表头一字不差、顺序一致）"],\n'
        '         "rows": [["单元格", "单元格", "……"], ["……"]]},\n'
        '        {"type": "p", "text": "表格之后接着的正文……"}\n'
        '      ]\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "填写规则（务必遵守）：\n"
        "1. sections 覆盖本章 SKILL.md 里列出的各个 **（）子标题**（（一）（二）（三）…），title 用子标题原文。"
        "（）子标题本身**不要**再放进 blocks（它已经是 title）。\n"
        "2. blocks 是**有序**的内容块，顺序必须和 SKILL.md 里正文的先后完全一致（段落和表格该穿插就穿插，"
        "例如：先一段说明、再一张表、再一段说明）。三种块：\n"
        "   · p：一段正文。**“1.基本信息”“2.违法违规和信用情况”“（1）……”这类编号小标题也各写成一个 p 段落**"
        "（它们是正文层级的小标题，不是（）子标题）。\n"
        "   · kv：两列\"字段:值\"表（如表1）。label 与 SKILL.md 表格第一列**一字不差**。\n"
        "   · grid：多列表（列数≥3，如表2、表3~表10）。headers 与 SKILL.md 该表表头**一字不差、顺序一致**；"
        "rows 每行和 headers 等长。\n"
        "3. **表号一律写占位符“表#”**（如“表#  项目公司基本信息”），不要自己填数字——最终序号由系统按"
        "表格出现顺序自动排。\n"
        "4. **SKILL.md 中出现的每一张表都必须输出**（哪怕暂无数据）：没数据的单元格填 \"\"，整表暂无数据也要"
        "输出 caption+headers；绝不能省略整张表或只用文字描述代替。\n"
        "5. 值优先用“已保存的摘要表/释义/其他基本信息”里的真实值，其次 planning.md（引号标注要照抄的内容，"
        "文字照用、不要改写）；确实找不到依据的填 “【注：说明缺什么、建议去哪里核实】”，绝不编造数字。\n"
        "6. SKILL.md 里用【】标注的占位都要替换成真实值；替换后不保留【】（除【注：】外）。语言用正式申报材料文体；"
        "金额以“万元”为单位、保留两位小数。\n"
        "7. 【JSON 合法性——最重要，违反会导致整份输出解析失败】任何字符串值内部**绝对不能出现未转义的"
        "英文双引号 \"**。SKILL.md / planning.md 原文里那些用英文引号 \" \" 包裹或嵌套的句子，"
        "**输出时一律把这些引号改成中文引号“”**（例如原文 承诺：\"本公司…\" → 输出 承诺：“本公司…”），"
        "只改引号符号、不改里面的文字。字符串里也不要出现真实换行（用一段连续文本），"
        "如含反斜杠 \\ 需写成 \\\\。记住：值里面只允许中文引号“”‘’，不允许裸的英文 \"。\n"
    )


# 提供给 Kimi 的天眼查工具（企业工商/股权/人员信息）。参数统一用精确企业全名。
_TYC_TOOLS = [
    {"type": "function", "function": {
        "name": "search_companies",
        "description": "天眼查：按企业名称/简称/统一社会信用代码搜索，锚定目标企业的精确工商全名。"
                       "查具体企业信息前，先用它把简称/俗称确认成精确全名。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "企业名称/简称/统一社会信用代码等关键词"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_company_basic_profile",
        "description": "天眼查：按精确企业全名获取工商基础画像（注册资本、法定代表人、成立日期、"
                       "统一社会信用代码、注册地址、企业规模等）。company_name 请用 search_companies 得到的精确全名。",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string", "description": "精确企业全名"}
        }, "required": ["company_name"]}}},
    {"type": "function", "function": {
        "name": "get_group_info",
        "description": "天眼查：轻量识别公司所属集团、主公司与疑似实际控制人。",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string", "description": "精确企业全名"}
        }, "required": ["company_name"]}}},
    {"type": "function", "function": {
        "name": "get_company_group_profile",
        "description": "天眼查：公司集团/股权结构画像（集团成员、对外投资、投资方、控制链，适合梳理股权结构）。",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string", "description": "精确企业全名"}
        }, "required": ["company_name"]}}},
    {"type": "function", "function": {
        "name": "get_company_people",
        "description": "天眼查：公司主要人员/董监高列表。",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string", "description": "精确企业全名"}
        }, "required": ["company_name"]}}},
    {"type": "function", "function": {
        "name": "get_company_capabilities",
        "description": "天眼查：查某公司还能查哪些专项维度并返回真实内部工具名(tool_name)。"
                       "要查'股东/股权占比、实际控制人、财务、风险诉讼、资质'等基础画像以外的信息时，"
                       "先用它拿到 tool_name，再用 call_tool 调。company_id 用 search_companies 结果里的企业ID。",
        "parameters": {"type": "object", "properties": {
            "company_id": {"type": "string", "description": "企业ID（来自 search_companies 结果，保持原始字符串，不要写成科学计数法）"},
            "company_name": {"type": "string", "description": "精确企业全名"}
        }, "required": ["company_id"]}}},
    {"type": "function", "function": {
        "name": "call_tool",
        "description": "天眼查：调用某公司的一个专项内部工具，获取该维度明细。tool_name 必须逐字复制 "
                       "get_company_capabilities 返回的真实工具名——例如查股东及持股比例用 get_shareholder_info、"
                       "查实际控制人用 get_actual_controller、查财务用 get_financial_data。"
                       "arguments 按需传参（列表类工具必须传 page=1、page_size=20）。",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string", "description": "精确企业全名（优先传）"},
            "tool_name": {"type": "string", "description": "内部工具名，逐字复制 get_company_capabilities 的 tool_name 列"},
            "arguments": {"type": "object", "description": "该工具的参数，如 {\"page\":1,\"page_size\":20}"}
        }, "required": ["tool_name", "arguments"]}}},
]
_TYC_TOOL_NAMES = {t["function"]["name"] for t in _TYC_TOOLS}


def _tyc_executor(name: str, args: dict) -> str:
    if name not in _TYC_TOOL_NAMES:
        return f"（未知工具 {name}）"
    return tianyancha_client.call(name, args)


# 提供给 Kimi 的证明材料读取工具（列目录 + 读文档，限定在申报材料根目录内）。
_MAT_TOOLS = [
    {"type": "function", "function": {
        "name": "list_materials",
        "description": "列出本项目'申报材料/证明材料'目录下的文件（相对路径），可用关键词过滤"
                       "（如“承诺函”“营业执照”“审计”“4”）。SKILL.md 说“阅读X号文件下的…”时，"
                       "先用它按关键词找到确切的文件相对路径。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "过滤关键词，匹配文件路径/文件名；为空则列全部（有上限）"}
        }}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "读取申报材料目录下某个文件的文本内容（支持 PDF文字层/Word/Excel/文本，扫描件/图片自动OCR）。"
                       "path 用 list_materials 返回的相对路径。承诺函的落款日期在正文里、文件全称就是文件名，"
                       "都可由此获得。**大扫描件（尤其审计报告，几十页）不要整篇读**：先不带 pages 读，"
                       "工具会回它共几页并识别开头几页；确需某项时用 pages 只读相关页，避免超时/过载。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件相对路径（来自 list_materials）"},
            "anchor": {"type": "string", "description":
                       "关键词定位（有文字层的PDF最省，无需OCR）：给一个词（如“资产负债表”），"
                       "直接跳到它所在页并返回该页及随后几页——三张连续报表一次锁定。"
                       "读大审计报告取财务数据时优先用它（anchor=\"资产负债表\"）。扫描件会退回并提示改用 pages。"},
            "pages": {"type": "string", "description":
                      "仅 PDF：只读/只OCR这些页，形如 '5' '1-3' '2,4'。空=读全文"
                      "（扫描件默认只识别开头很少几页）。用于大扫描件定点取页；扫描件一次别要太多页（≤8）。"},
            "query": {"type": "string", "description":
                      "读扫描件/图片时你要找的具体内容（如“落款日期和落款单位”“审计意见”“货币资金金额”）。"
                      "给了它，视觉识别就只回相关内容、不逐字通读全页，输出更短更准。空=逐字识别全文。"}
        }, "required": ["path"]}}},
]
_MAT_TOOL_NAMES = {t["function"]["name"] for t in _MAT_TOOLS}

# Moonshot 内置联网搜索（服务端执行，不需额外密钥）。
_WEB_SEARCH_TOOL = {"type": "builtin_function", "function": {"name": "$web_search"}}


def _make_materials_executor(root):
    def _exec(name: str, args: dict) -> str:
        if name == "list_materials":
            return materials_client.list_materials(root, args.get("keyword", ""))
        if name == "read_document":
            return materials_client.read_document(
                root, args.get("path", ""), args.get("pages", ""),
                args.get("query", ""), args.get("anchor", ""))
        return f"（未知工具 {name}）"
    return _exec


def run_chapter(n: int, subtitles: list = None, materials_path: str = None,
                project_id: str = None, pack_id: str = None) -> dict:
    """执行第 n 章：读 planning.md + 该章 SKILL.md + 已保存摘要，让 Kimi 产出结构化内容。

    subtitles（来自官方模板的本章小标题）如果给出，则强制 Kimi 用这几个小标题作为
    sections 的 title（一字不差），以便和模板/编辑区骨架对齐。

    pack_id：项目绑定的模板包（None=默认包），决定章节结构/写作要求/提示词里的材料类型。

    若配置了天眼查密钥，会把天眼查企业查询工具挂给 Kimi：Kimi 可在生成过程中查询
    参与主体的工商/股权/人员信息，据实填写，而不是编造。
    """
    cfg = chapters_for(pack_id)[n]
    planning = _read_text(pack_service.planning_path(pack_id))
    skill_md = _read_text(pack_service.reading_path(n, pack_id))
    saved_summary = _format_saved_summary(project_id)

    # 排版配置(write_config.json)只在“生成内容”这一步按需刷新（写作要求改过才真调模型）——
    # 从而彻底移出 Word 预览路径：改 skill 不再拖慢预览。失败不阻断本次生成。
    try:
        ensure_write_config(pack_id=pack_id)
    except Exception as e:
        logger.warning(f"刷新 write_config 失败（不影响本次生成）：{e}")

    mat_root = None
    if materials_path:
        try:
            p = Path(materials_path)
            if p.is_dir():
                mat_root = p
        except Exception:
            mat_root = None

    system_prompt = (
        f"你是{pack_service.material_label(pack_id)}的写作助手，正在执行'{cfg['title']}'的撰写。"
        "你会拿到几份材料：全局总纲 planning.md、用户在系统中已核对保存的"
        "'摘要表/释义/其他基本信息'、以及本章的写作要求 SKILL.md。"
        "你的任务是严格按 SKILL.md 的结构，优先用'已保存的摘要表/释义/其他基本信息'里的真实值"
        "把本章填好；这三部分里没有的，再看 planning.md。"
    )
    if tianyancha_client.is_enabled():
        system_prompt += (
            "\n\n你还配有天眼查企业数据查询工具。涉及参与主体（发起人/原始权益人/项目公司/基金管理人/"
            "中介机构等）的工商信息（注册资本、法定代表人、成立日期、统一社会信用代码、注册地址）、"
            "股权结构、股东及持股比例、实际控制人、董监高、财务时，务必调用天眼查工具查询后据实填写。\n"
            "查询路径：①先用 search_companies 把名称锚定成精确工商全名，并记下它返回的企业ID；"
            "②工商基础画像用 get_company_basic_profile；③**股东及精确持股比例、实际控制人、财务、"
            "风险等专项信息，get_company_basic_profile/get_company_group_profile 里没有，必须走："
            "先用 get_company_capabilities(企业ID) 拿到真实 tool_name，再用 call_tool 调**"
            "（如股东占比用 call_tool(tool_name='get_shareholder_info', arguments={'page':1,'page_size':20})）。\n"
            "特别注意：**股权关系/持股比例必须以 get_shareholder_info 的真实返回为准，一个字都不能编**——"
            "股东名称和百分比只能来自工具返回；工具查不到就如实写“【注：天眼查未查询到，待核实】”，"
            "绝对不许臆造股东名称或把比例凑成整数。"
        )
    if mat_root is not None:
        system_prompt += (
            "\n\n你还能读取本项目的**申报材料/证明材料**文件。凡是 SKILL.md 里说“阅读X号文件下的…”、"
            "“查看承诺函落款日期”、“该承诺函文件全称”、“审计报告”“信用记录”“营业执照”等需要看具体文件的地方，"
            "都要去读文件、据实填写，不要直接标“待补充”：先用 list_materials(keyword=…) 按关键词"
            "（如“承诺函”“营业执照”“审计”，或文件夹编号“4”）找到文件相对路径，再用 read_document(path=…) 读其文本。"
            "**文件名本身就是“文件全称”**；承诺函的**落款日期在其正文里**。"
            "read_document 会自动识别扫描件/图片（营业执照、承诺函等）里的文字，扫描件也要读、据实填。\n"
            "读文件时**带着目标读**，别整篇通读：\n"
            "· 取财务数据（从审计报告/财务报表里找三张合并报表）→ **首选 anchor 关键词定位**："
            "read_document(path=…, anchor=\"资产负债表\")，工具会直接跳到该页并返回它及随后几页——"
            "利润表、现金流量表通常紧随其后，一次就锁定三张表，且有文字层时根本不用 OCR。\n"
            "· 若返回提示“没找到该关键词/是扫描件”：再用 pages 定点读，且**扫描件一次别要太多页（≤8，分批小步读）**，"
            "配合 query 指明要找的科目（如 query=\"资产总计 负债合计 营业总收入 净利润 经营活动现金流量净额\"）。\n"
            "· 承诺函落款日期/审计意见等零散项 → 用 query 直接点名（如 query=\"落款日期和落款单位\"），只回相关内容。\n"
            "**绝不要把几十页审计报告整篇 OCR。** 扫描件表格数字 OCR 可能读错，拿不准的数字标"
            "“【注：OCR识别，请人工核对】”；识别不出或缺关键项才标“【注：…，请人工核对】”，绝不编造。"
        )
    system_prompt += (
        "\n\n你还能**联网搜索**（$web_search）。当某项公开信息在“已保存摘要/释义/其他基本信息”、天眼查、"
        "证明材料里都找不到的公开披露数据（如机构某时点的规模、业绩等），可以联网搜索后填写。"
        "但**联网所得务必谨慎核对**：优先采信官方/权威来源（公司官网、中基协、交易所、监管公示），"
        "注明是截至哪个时点/什么口径；若时点或口径对不上、或来源不可靠，宁可标“【注：网络来源，待人工核实】”，绝不凑数。"
    )

    subtitle_rule = ""
    if subtitles:
        listed = "\n".join(f"- {t}" for t in subtitles)
        subtitle_rule = (
            "\n\n# 本章小标题（来自官方模板，务必严格遵守）\n"
            "sections 的 title **只能是**下面这几个模板小标题，一个都不能多、一个都不能少，"
            "title 与下列文字**一字不差**、相对顺序一致：\n" + listed + "\n"
            "**严禁**把“1.【原始权益人1】”“2.固安聚龙”“（1）基本信息”“（2）财务状况”“表3 ……”"
            "这类**编号项/枚举项**单独作为一个 section！它们是某个模板小标题（如"
            "“（三）发起人（原始权益人）情况”）**内部的内容**——当有多个原始权益人/多张表时，"
            "把每个“1.…”“2.…”写成该 section 的 blocks 里的一个 p 段落（编号小标题），"
            "其下的说明、表格紧跟在这个 p 段落后面，全部装进**同一个模板小标题的 blocks 数组里**，"
            "顺序排列。总之：section 的数量恒等于上面列表的模板小标题数，绝不因主体数量增减而变化。\n"
        )

    user_prompt = (
        f"# 全局总纲 planning.md\n\n{planning}\n\n"
        f"# 已保存的摘要表/释义/其他基本信息（用户已核对，优先以此为准）\n\n{saved_summary}\n\n"
        f"# 本章（{cfg['title']}）写作要求\n\n{skill_md}\n\n"
        f"# 输出要求\n{_output_contract(cfg['title'])}"
        f"{subtitle_rule}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    # 组合工具集：天眼查（企业数据）+ 证明材料读取 + 联网搜索（Moonshot 内置，始终可用）
    tools = [_WEB_SEARCH_TOOL]
    if tianyancha_client.is_enabled():
        tools += _TYC_TOOLS
    mat_exec = None
    if mat_root is not None:
        tools += _MAT_TOOLS
        mat_exec = _make_materials_executor(mat_root)

    if tools:
        def _combined_executor(name, args):
            if name in _TYC_TOOL_NAMES:
                return _tyc_executor(name, args)
            if name in _MAT_TOOL_NAMES and mat_exec is not None:
                return mat_exec(name, args)
            return f"（未知工具 {name}）"
        raw = chat_with_tools(messages, tools, _combined_executor,
                              model=get_selected_model(), temperature=1.0)
    else:
        raw = chat(messages, model=get_selected_model(), temperature=1.0)

    if not (raw or "").strip():
        logger.warning(f"ch{n} 生成失败：模型返回空内容（多为工具调用过多/超时，稍后重试即可）")
        raise RuntimeError("模型未返回内容（可能是查询工具调用过多或超时），请重试生成")
    try:
        data = _parse_json(raw)
    except Exception as e:
        logger.warning(f"ch{n} 解析模型输出失败：{e}；原始输出前 500 字：{raw[:500]!r}")
        raise RuntimeError(f"模型输出不是有效 JSON（{e}），请重试生成") from e
    # 锁定小标题结构＝模板：把 Kimi 误当成 section 的编号项（“1.奥飞数据”等）折回其所属模板小标题
    if isinstance(data, dict) and data.get("sections"):
        data["sections"] = _fold_enumerated_sections(data["sections"], subtitles)
    try:
        _save_json(n, data, project_id)
    except Exception as e:
        logger.warning(f"写入 ch{n}.json 失败: {e}")

    return data


def load_web_render(pack_id: str = None):
    """加载模板包内的 web_render 渲染脚本（写作规则随包走）。

    每次都 reload：脚本在 templates-packs 下，不在后端自动重载范围内，
    reload 后改写作要求即时生效、无需重启服务（保留热重载机制）。
    """
    import importlib
    import os
    import sys
    # 把排版配置的落点告诉渲染脚本：write_config.json 是运行期数据（workspace 下），
    # 不进模板包；web_render 会优先读这个环境变量。
    os.environ["WRITE_CONFIG_PATH"] = str(WRITE_CONFIG_PATH)
    scripts_dir = str(pack_service.writing_script_dir(pack_id))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import web_render
    importlib.reload(web_render)
    return web_render


def chapter_subtitles(n: int, template_path: str, pack_id: str = None) -> list:
    """读官方模板里第 n 章的各个小标题（借模板包里的 web_render 解析）。"""
    if not template_path:
        return []
    try:
        web_render = load_web_render(pack_id)
        cfg = chapters_for(pack_id)[n]
        subs = web_render.list_chapter_subtitles(template_path, cfg["title"], cfg["next"])
        # 无小标题的章（大标题下直接是正文，如第六章）：用一个"本章正文"单块
        return subs if subs else [web_render._BODY_SECTION_TITLE]
    except Exception as e:
        logger.warning(f"读取第{n}章模板小标题失败: {e}")
        return []


def chapter_tables(n: int, template_path: str, pack_id: str = None) -> dict:
    """读官方模板里第 n 章各小标题下的多列表骨架 {小标题: [grid,...]}（用于编辑区显示空表）。"""
    if not template_path:
        return {}
    try:
        web_render = load_web_render(pack_id)
        cfg = chapters_for(pack_id)[n]
        return web_render.list_chapter_tables(template_path, cfg["title"], cfg["next"])
    except Exception as e:
        logger.warning(f"读取第{n}章模板多列表失败: {e}")
        return {}


def chapter_table_start(n: int, template_path: str, pack_id: str = None) -> int:
    """本章第一张表在全篇里的起始表号 = 模板中本章大标题之前已有的表数 + 1。"""
    if not template_path:
        return 1
    try:
        web_render = load_web_render(pack_id)
        return web_render.count_captions_before(template_path, chapters_for(pack_id)[n]["title"]) + 1
    except Exception as e:
        logger.warning(f"计算第{n}章起始表号失败: {e}")
        return 1


# 排版配置(write_config.json)是运行期产物，属项目数据，放 workspace 根（不进模板包）；
# 写作要求（writing/SKILL.md）从项目绑定的模板包读取。
WRITE_CONFIG_PATH = DATA_SOURCE_BASE / "write_config.json"


def _writing_skill_md(pack_id: str = None) -> Path:
    return pack_service.pack_path("writing/SKILL.md", pack_id)


_WRITE_CONFIG_KEYS = ("font", "body_pt", "table_pt", "footnote_pt",
                      "body_line_spacing", "table_line_spacing", "table_align",
                      "insert_unknown_headings")


def _load_write_config_dict() -> dict:
    if WRITE_CONFIG_PATH.exists():
        try:
            return json.loads(WRITE_CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
    return {}


def _write_config_stale(pack_id: str = None) -> bool:
    """写作要求（planning.md / 写作SKILL.md）比配置文件新，或配置不存在，就算过期。"""
    if not WRITE_CONFIG_PATH.exists():
        return True
    try:
        cfg_m = WRITE_CONFIG_PATH.stat().st_mtime
    except OSError:
        return True
    for src in (pack_service.planning_path(pack_id), _writing_skill_md(pack_id)):
        try:
            if src.exists() and src.stat().st_mtime > cfg_m:
                return True
        except OSError:
            pass
    return False


def ensure_write_config(force: bool = False, pack_id: str = None) -> dict:
    """让大模型读 planning.md + 写作SKILL.md 的自然语言写作要求，翻成 write_config.json，
    供 web_render.py 执行。只有配置过期（写作要求被改过）或 force 时才真正调一次大模型，
    平时只做几个文件时间戳比较，几乎零开销。失败时保留上一版配置、不中断写入。"""
    if not force and not _write_config_stale(pack_id):
        return _load_write_config_dict()

    planning = _read_text(pack_service.planning_path(pack_id))
    skill_md = _read_text(_writing_skill_md(pack_id))
    system_prompt = (
        "你是排版配置助手。下面是一份申报材料的写作/格式要求（自然语言）。"
        "请把其中和 Word 排版有关的要求，提炼成一个严格 JSON 配置对象，供写入程序直接使用。"
    )
    user_prompt = (
        f"# 全局总纲 planning.md\n\n{planning}\n\n"
        f"# 写作要求 writing/SKILL.md\n\n{skill_md}\n\n"
        "# 输出要求\n"
        "只输出一个 JSON 对象（不要任何解释、不要```代码块），字段如下（拿不准的字段就省略，不要瞎填）：\n"
        "{\n"
        '  "font": "正文/表格用的中文字体名，如 仿宋",\n'
        '  "body_pt": 正文字号的磅值数字（把中文字号换算成磅：小四=12、五号=10.5、小五=9、四号=14、三号=16、小三=15），\n'
        '  "table_pt": 表格文字字号磅值数字,\n'
        '  "footnote_pt": 脚注字号磅值数字,\n'
        '  "body_line_spacing": 正文行距倍数数字（如 1.3；没提就省略）,\n'
        '  "table_line_spacing": 表格文字行距倍数数字（如 1.0；没提就省略）,\n'
        '  "table_align": 表格文字水平对齐，取 "center"/"left"/"right" 之一（没提就省略）,\n'
        '  "insert_unknown_headings": 布尔值——若要求"reading skill 里模板没有的标题也要按顺序写入模板"'
        '则填 true，若要求"只按模板结构、丢弃模板没有的标题"则填 false（没提就省略）\n'
        "}\n"
        "只提炼要求里明确写到的项；字号一律换算成磅值数字，不要写“小四”这种中文。"
    )
    try:
        raw = chat([{"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}],
                   model=get_selected_model(), temperature=1.0)
        cfg = _parse_json(raw)
        clean = {k: cfg[k] for k in _WRITE_CONFIG_KEYS if k in cfg and cfg[k] not in (None, "")}
        WRITE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        WRITE_CONFIG_PATH.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"已根据写作要求刷新 write_config.json: {clean}")
        return clean
    except Exception as e:
        logger.warning(f"生成 write_config.json 失败，沿用上一版/默认: {e}")
        return _load_write_config_dict()


# 表标题编号占位：表# / 表c / 表3 等（表 后跟 #、单个字母、或一串数字）。编号不靠 Kimi 算，
# 由代码按"表格出现顺序"统一排：编辑区用 _number_captions（起始号按模板前面已有几张表），
# 最终 Word 由模板包内 web_render 全篇重排为准。
_CAP_LEAD_RE = re.compile(r"^表(?:[#＃]|[0-9]+|[A-Za-z])")


def _renumber_caption_text(cap: str, seq: int) -> str:
    """把一个表标题字符串开头的'表X'占位换成'表{seq}'。"""
    if not cap:
        return cap
    stripped = cap.lstrip()
    m = _CAP_LEAD_RE.match(stripped)
    if not m:
        return cap
    return cap[:len(cap) - len(stripped)] + f"表{seq}" + stripped[m.end():]


def _number_captions(sections: list, start_no: int) -> list:
    """给编辑区：按 section 顺序、每节内按有序块顺序，把带 caption 的表格块编号占位
    换成从 start_no 起的连续序号。"""
    seq = start_no
    out = []
    for s in sections:
        new_blocks = []
        for b in _section_blocks(s):
            if b.get("type") in ("kv", "grid") and b.get("caption"):
                b = dict(b)
                b["caption"] = _renumber_caption_text(b["caption"], seq)
                seq += 1
            new_blocks.append(b)
        s2 = dict(s)
        s2["blocks"] = new_blocks
        # 旧字段可能与 blocks 冲突，统一以 blocks 为准
        for k in ("paragraphs", "table", "table_caption", "grid_tables"):
            s2.pop(k, None)
        out.append(s2)
    return out


def _escape_inner_quotes(text: str) -> str:
    """兜底修复：把 JSON 字符串值内部“未转义的英文双引号”转义掉。
    模型常把中文正文里的英文引号（含 SKILL 原文里嵌套的 "…"）原样输出，破坏 JSON。
    逐字扫描：在字符串内遇到 " 时向后看，若其后（跳过空白）是结构符 :,}] 才认定为真正的收尾引号，
    否则视为正文引号、转义成 \\"。仅在严格解析失败后调用。"""
    out = []
    in_str = False
    i, n = 0, len(text)
    structural = {":", ",", "}", "]"}
    while i < n:
        ch = text[i]
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            i += 1
            continue
        if ch == "\\":                      # 保留已有转义对
            out.append(ch)
            if i + 1 < n:
                out.append(text[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j >= n or text[j] in structural:
                out.append('"')             # 真正的收尾引号
                in_str = False
            else:
                out.append('\\"')           # 正文里的引号 → 转义
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_json(raw: str) -> dict:
    """从模型回复里稳妥地取出 JSON（容忍多余的```/前后说明文字/正文里未转义的英文引号）。"""
    text = raw.strip()
    # 去掉可能的 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 截取第一个 { 到最后一个 }，去掉前后杂物
    start, end = text.find("{"), text.rfind("}")
    core = text[start:end + 1] if (start != -1 and end > start) else text
    last = None
    for candidate in (core, _escape_inner_quotes(core)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            last = e
    # 仍失败：打印出错位置附近内容，便于定位是哪句话的引号/字符坏了
    off = getattr(last, "pos", 0) or 0
    logger.warning(
        f"解析模型输出 JSON 失败：{last}；总长={len(core)}；"
        f"出错位置附近：…{core[max(0, off - 80):off + 80]!r}…")
    raise last
