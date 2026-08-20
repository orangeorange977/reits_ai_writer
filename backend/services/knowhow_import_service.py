"""Import business Know-how DOCX files without letting the model rewrite them.

DOCX paragraphs and tables are converted deterministically to Markdown.  For a
bulk document, AI may choose section boundaries, but the returned content is
always sliced verbatim from that Markdown so an import cannot silently alter a
business rule.
"""
from __future__ import annotations

import io
import json
import re

from docx import Document
from docx.document import Document as _Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.oxml.ns import qn


def _blocks(parent):
    element = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _cell_text(cell: _Cell) -> str:
    return "<br>".join(
        re.sub(r"\s+", " ", paragraph.text).strip()
        for paragraph in cell.paragraphs if paragraph.text.strip()
    ).replace("|", "\\|")


def _markdown_table_rows(table: Table) -> list[list[str]]:
    """Keep the Word grid while emitting merged cell text only once."""
    rows: list[list[str]] = []
    for row in table.rows:
        seen = set()
        values = []
        for cell in row.cells:
            cell_id = id(cell._tc)
            values.append("" if cell_id in seen else _cell_text(cell))
            seen.add(cell_id)
        rows.append(values)
    return rows


def _numbering_resolver(doc):
    """Resolve a paragraph numId through Word's numbering/style links."""
    try:
        root = doc.part.numbering_part.element
    except (AttributeError, NotImplementedError):
        return lambda _num_id, _level: None

    nums, abstracts = {}, {}
    for child in root:
        if child.tag == qn("w:num"):
            num_id = child.get(qn("w:numId"))
            abstract = child.find(qn("w:abstractNumId"))
            if num_id is not None and abstract is not None:
                nums[num_id] = abstract.get(qn("w:val"))
        elif child.tag == qn("w:abstractNum"):
            abstracts[child.get(qn("w:abstractNumId"))] = child

    styles = {}
    for style in doc.styles:
        styles[str(style.style_id)] = style
        styles[str(style.name)] = style

    def resolve(num_id: str, level: int, seen=None):
        seen = set() if seen is None else seen
        key = (str(num_id), int(level))
        if key in seen:
            return None
        seen.add(key)
        abstract = abstracts.get(nums.get(str(num_id)))
        if abstract is None:
            return None
        for node in abstract.findall(qn("w:lvl")):
            if int(node.get(qn("w:ilvl"), "0")) != level:
                continue
            num_fmt = node.find(qn("w:numFmt"))
            start = node.find(qn("w:start"))
            return {
                "format": num_fmt.get(qn("w:val")) if num_fmt is not None else "decimal",
                "start": int(start.get(qn("w:val"), "1")) if start is not None else 1,
            }

        # Some Word producers point an abstract numbering definition at a
        # paragraph style which in turn points at the concrete definition.
        style_link = abstract.find(qn("w:numStyleLink"))
        if style_link is None:
            return None
        style = styles.get(style_link.get(qn("w:val"), ""))
        ppr = getattr(getattr(style, "element", None), "pPr", None)
        num_pr = getattr(ppr, "numPr", None) if ppr is not None else None
        linked_num_id = getattr(getattr(num_pr, "numId", None), "val", None)
        return resolve(str(linked_num_id), level, seen) if linked_num_id is not None else None

    return resolve


def _paragraph_list_info(paragraph: Paragraph, resolve_numbering):
    ppr = paragraph._p.pPr
    num_pr = getattr(ppr, "numPr", None) if ppr is not None else None
    if num_pr is None and paragraph.style is not None:
        style_ppr = getattr(paragraph.style.element, "pPr", None)
        num_pr = getattr(style_ppr, "numPr", None) if style_ppr is not None else None

    style_name = (paragraph.style.name if paragraph.style else "").lower()
    num_id = getattr(getattr(num_pr, "numId", None), "val", None)
    level = int(getattr(getattr(num_pr, "ilvl", None), "val", 0) or 0)
    resolved = resolve_numbering(str(num_id), level) if num_id is not None else None
    if resolved:
        kind = "bullet" if resolved["format"] == "bullet" else "number"
        return kind, str(num_id), level, resolved["start"]
    if "list bullet" in style_name or "项目符号" in style_name:
        return "bullet", str(num_id or style_name), level, 1
    if "list number" in style_name or "编号" in style_name:
        return "number", str(num_id or style_name), level, 1
    return None


def docx_to_markdown(data: bytes) -> str:
    """Preserve body order and text; render every Word list as neutral bullets.

    Know-how list numbering is presentation, not business data. Normalizing both
    numbered and bulleted Word styles to Markdown bullets avoids false sequences
    caused by DOCX numbering definitions, copied fragments, or custom styles.
    """
    doc = Document(io.BytesIO(data))
    resolve_numbering = _numbering_resolver(doc)
    output: list[str] = []
    for block in _blocks(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = (block.style.name if block.style else "").lower()
            list_info = _paragraph_list_info(block, resolve_numbering)
            if style.startswith("heading"):
                match = re.search(r"(\d+)", style)
                level = min(6, int(match.group(1))) if match else 2
                output.append(f"{'#' * level} {text}")
            elif list_info:
                _kind, _num_id, level, _start = list_info
                indent = "  " * level
                output.append(f"{indent}- {text}")
            elif re.match(r"^[•·●○▪▫◦‣]\s*", text):
                output.append("- " + re.sub(r"^[•·●○▪▫◦‣]\s*", "", text))
            elif re.fullmatch(r"#.+#", text):
                output.append(f"## {text}")
            elif re.fullmatch(r"【.+】", text):
                output.append(f"### {text}")
            else:
                output.append(text)
        else:
            rows = _markdown_table_rows(block)
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            table_lines = [
                "| " + " | ".join(rows[0]) + " |",
                "| " + " | ".join(["---"] * width) + " |",
                *("| " + " | ".join(row) + " |" for row in rows[1:]),
            ]
            # A Markdown table is one block. Blank lines between its rows make the
            # preview parser treat the separator as an independent empty table.
            output.append("\n".join(table_lines))
    return "\n\n".join(output).strip() + "\n"


def _metadata_section(markdown: str, sections: list[dict]) -> str:
    head = markdown[:3000]
    for section in sections:
        sid, title = str(section.get("id", "")), str(section.get("title", "")).strip()
        if (sid and re.search(rf"(?:模板名称\s*\|[^\n]*\b{re.escape(sid)}\b|\b{re.escape(sid)}\s+[^\n]*)", head)
                or title and title in head):
            return sid
    return ""


def _deterministic_ranges(markdown: str, sections: list[dict]) -> dict[str, str]:
    hits = []
    for section in sections:
        title = str(section.get("title", "")).strip()
        if not title:
            continue
        match = re.search(rf"(?m)^.*{re.escape(title)}.*$", markdown)
        if match:
            hits.append((match.start(), section["id"]))
    hits.sort()
    return {
        sid: markdown[start:(hits[index + 1][0] if index + 1 < len(hits) else len(markdown))].strip() + "\n"
        for index, (start, sid) in enumerate(hits)
    }


def split_bulk_markdown(markdown: str, sections: list[dict]) -> tuple[dict[str, str], str]:
    """Use AI for boundaries; fall back to exact title matching if AI is unavailable."""
    lines = markdown.splitlines(keepends=True)
    numbered = "".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    allowed = {str(section["id"]): section for section in sections}
    prompt = (
        "你只负责把一份业务Know-how按官方小节切分，不得改写正文。根据带行号原文，"
        "返回JSON：{\"sections\":[{\"section_id\":\"1.1\",\"start_line\":1,\"end_line\":20}]}。"
        "行号包含首尾，区间不得重叠；无法判断的内容不要分配。官方小节：\n"
        + json.dumps([{"id": x["id"], "title": x["title"]} for x in sections], ensure_ascii=False)
        + "\n原文：\n" + numbered[:90000]
    )
    try:
        from backend.services import skill_runner
        from backend.services.kimi_client import chat
        raw = chat([{"role": "user", "content": prompt}], model=skill_runner.get_selected_model(), temperature=0)
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I))
        result = {}
        occupied: set[int] = set()
        for item in parsed.get("sections", []):
            sid = str(item.get("section_id", ""))
            start, end = int(item.get("start_line", 0)), int(item.get("end_line", 0))
            indexes = set(range(start, end + 1))
            if sid not in allowed or start < 1 or end < start or end > len(lines) or indexes & occupied:
                continue
            occupied.update(indexes)
            result[sid] = "".join(lines[start - 1:end]).strip() + "\n"
        if result:
            return result, "ai"
    except Exception:
        pass
    return _deterministic_ranges(markdown, sections), "title_fallback"


def import_docx_files(files: list[tuple[str, bytes]], sections: list[dict], section_id: str = "") -> dict:
    if section_id and section_id not in {str(x.get("id")) for x in sections}:
        raise ValueError(f"未知小节：{section_id}")
    imports, warnings = {}, []
    methods = []
    for filename, data in files:
        if not filename.lower().endswith(".docx"):
            warnings.append(f"{filename}：仅支持 DOCX")
            continue
        markdown = docx_to_markdown(data)
        if section_id:
            imports[section_id] = markdown
            methods.append("selected_section")
            continue
        detected = _metadata_section(markdown, sections)
        if detected:
            imports[detected] = markdown
            methods.append("document_metadata")
            continue
        split, method = split_bulk_markdown(markdown, sections)
        imports.update(split)
        methods.append(method)
        if not split:
            warnings.append(f"{filename}：无法识别对应小节")
    return {"imports": imports, "warnings": warnings, "method": ",".join(sorted(set(methods)))}
