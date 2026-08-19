"""Non-blocking audit layer for generated report content."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.config import PROJECTS_DIR, safe_project_id
from backend.services import data_foundation_service

_AUDIT_WRITE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def audit_path(project_id: str | None) -> Path:
    return PROJECTS_DIR / safe_project_id(project_id) / "report_audit.json"


def load_audit(project_id: str | None) -> dict:
    path = audit_path(project_id)
    if not path.exists():
        return {"schema_version": "1.0", "project_id": safe_project_id(project_id), "runs": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": "1.0", "project_id": safe_project_id(project_id), "runs": {}}


def _write(project_id: str | None, data: dict) -> None:
    path = audit_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _chapter(project_id: str | None, chapter_n: int) -> dict:
    path = PROJECTS_DIR / safe_project_id(project_id) / f"ch{chapter_n}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _section_key(chapter_n: int, section: dict) -> str:
    sid = str(section.get("id", "")).strip()
    return f"{chapter_n}.{sid}" if sid and not sid.startswith(f"{chapter_n}.") else (sid or f"{chapter_n}.unknown")


def _block_plain(block: dict) -> str:
    if block.get("type") == "p":
        return str(block.get("text", ""))
    if block.get("type") == "kv":
        return "\n".join(f"{r.get('label', '')}：{r.get('value', '')}" for r in block.get("rows", []))
    if block.get("type") == "grid":
        return "\n".join(" | ".join(map(str, row)) for row in block.get("rows", []))
    return ""


def _issue(kind: str, severity: str, location: str, description: str,
           suggestion: str = "", evidence: str = "") -> dict:
    return {
        "type": kind, "severity": severity, "location": location,
        "description": description, "suggestion": suggestion, "evidence": evidence,
        "status": "open",
    }


def deterministic_audit(section: dict, foundation: dict | None = None) -> list[dict]:
    issues = []
    title = section.get("title", "未命名小节")
    blocks = section.get("blocks") or []
    if not blocks:
        return [_issue("missing_content", "error", title, "本小节尚无正文内容", "生成或补充本小节后重新审核")]
    for idx, block in enumerate(blocks, 1):
        location = f"{title} / 第{idx}个内容块"
        text = _block_plain(block)
        placeholders = sorted(set(re.findall(r"【[^】]*(?:待|缺失|无法|请人工)[^】]*】", text)))
        for placeholder in placeholders:
            issues.append(_issue("placeholder", "warning", location, f"仍有待处理占位：{placeholder}", "补充证据后重生成，或由业务确认保留"))
        if block.get("type") == "kv":
            for row in block.get("rows", []):
                label, value = str(row.get("label", "")).strip(), str(row.get("value", "")).strip()
                is_group_heading = label in {"项目总体情况", "子项目 1", "子项目1"} or label.startswith("子项目 ")
                if label and not value and not is_group_heading:
                    issues.append(_issue("empty_cell", "warning", f"{location} / {label}", "表格字段为空", "补充数据或说明不适用"))
        if block.get("type") == "grid":
            for row_idx, row in enumerate(block.get("rows", []), 1):
                if any(not str(cell).strip() for cell in row[1:]):
                    issues.append(_issue("empty_cell", "warning", f"{location} / 第{row_idx}行", "表格存在空白数据单元格", "核对底稿提取结果"))
        # Key-fact-level traceability: facts with numbers/dates need at least one source.
        key_fact = bool(re.search(r"\d{4}年|\d+(?:[,.]\d+)*(?:万元|亿元|%|㎡|个|kW)", text))
        if key_fact and not str(block.get("src", "")).strip():
            issues.append(_issue("missing_source", "warning", location, "包含日期或数值的关键事实未附来源", "补充到文件及页码的来源定位", text[:180]))
    for field in (foundation or {}).get("fields", []):
        if field.get("status") == "conflict" or len(field.get("candidates") or []) > 1:
            issues.append(_issue(
                "source_conflict", "warning", f"数据字段 / {field.get('label')}",
                "同一事实存在多个来源候选，系统已使用当前值",
                "核对候选来源；如需调整，在数据中间层覆盖当前值",
                json.dumps(field.get("candidates", [])[:3], ensure_ascii=False),
            ))
    # Stable de-duplication makes repeated audit runs comparable.
    seen, output = set(), []
    for item in issues:
        key = (item["type"], item["location"], item["description"])
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _facts_for_ai(foundation: dict | None) -> list[dict]:
    rows = []
    for field in (foundation or {}).get("fields", []):
        if not str(field.get("value", "")).strip():
            continue
        source = field.get("source") or {}
        rows.append({
            "name": field.get("label"), "value": field.get("value"),
            "status": field.get("status"), "source": source.get("path"),
            "locator": source.get("locator"),
        })
    return rows


def _section_id_for_title(chapter_n: int, title: str, pack_id: str | None) -> str:
    """Match a saved section's title back to its small-section id (e.g. "2.3") so the
    section-specific audit checklist (rules.json["audit_checks"]) can be found. Sections
    without a dedicated Know-how simply have no checklist — this looks up nothing extra."""
    from backend.services import section_skill_service
    for config in section_skill_service._registry(pack_id):
        if config.get("chapter_n") == chapter_n and str(config.get("title", "")).strip() == title.strip():
            return config.get("id", "")
    return ""


def _ai_audit(section: dict, foundation: dict | None, checklist: list[str] | None = None) -> list[dict]:
    from backend.services import skill_runner
    from backend.services.kimi_client import chat
    from backend.config import MOONSHOT_API_KEY, DEEPSEEK_API_KEY

    model = skill_runner.get_selected_model()
    if model.lower().startswith("deepseek") and not DEEPSEEK_API_KEY:
        return []
    if not model.lower().startswith("deepseek") and not MOONSHOT_API_KEY:
        return []
    compact_blocks = []
    for block in section.get("blocks") or []:
        compact = {
            "type": block.get("type"), "caption": block.get("caption", ""),
            "text": str(block.get("text", ""))[:5000],
            "headers": block.get("headers") or [], "rows": block.get("rows") or [],
            "has_source": bool(str(block.get("src", "")).strip()),
            "source_summary": str(block.get("src", ""))[:800],
        }
        compact_blocks.append(compact)
    payload = {
        "section": {"id": section.get("id"), "title": section.get("title"),
                    "blocks": compact_blocks},
        "known_facts": _facts_for_ai(foundation),
    }
    checklist_note = (
        ("\n\n本小节业务 Know-how 明确要求核对以下要点，逐条检查是否满足：\n"
         + "\n".join(f"- {item}" for item in checklist))
        if checklist else ""
    )
    prompt = (
        "你是基础设施REITs申报材料复核人员。审核下面一个二级小节，重点检查："
        "与已知事实不一致、内部前后矛盾、关键结论缺证据、重大遗漏、时间口径错误、"
        "不审慎或不符合正式申报语体。不要因为表述风格不同而报错，也不要虚构新事实。"
        + checklist_note +
        "\n\n输出 JSON：{\"issues\":[{\"type\":\"\",\"severity\":\"error|warning|info\","
        "\"location\":\"\",\"description\":\"\",\"suggestion\":\"\",\"evidence\":\"\"}]}。"
        "最多输出8项确有依据的问题；description不超过120字，suggestion和evidence各不超过100字。"
        "只输出完整 JSON，不要 Markdown。\n\n" + json.dumps(payload, ensure_ascii=False)[:70000]
    )
    raw = chat([{"role": "user", "content": prompt}], model=model, temperature=0.2)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Long sections occasionally make a model emit a truncated JSON object.  One
        # bounded retry is cheaper and clearer than surfacing a parser exception to business.
        retry = chat([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": (raw or "")[:12000]},
            {"role": "user", "content": (
                "上一结果不是完整 JSON。请重新输出，最多保留最重要的5项；每项只写 type、severity、"
                "location、description、suggestion、evidence，所有文字简短。只输出一个完整 JSON 对象。"
            )},
        ], model=model, temperature=0.1)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (retry or "").strip(), flags=re.I)
        parsed = json.loads(cleaned)
    output = []
    for item in parsed.get("issues", []):
        if not isinstance(item, dict) or not item.get("description"):
            continue
        item = {
            "type": item.get("type", "ai_review"),
            "severity": item.get("severity", "warning") if item.get("severity") in {"error", "warning", "info"} else "warning",
            "location": item.get("location", section.get("title", "")),
            "description": item.get("description", ""),
            "suggestion": item.get("suggestion", ""),
            "evidence": item.get("evidence", ""),
            "status": "open",
            "reviewer": "AI",
        }
        output.append(item)
    return output


def audit_chapter(project_id: str | None, chapter_n: int, use_ai: bool = True,
                  section_title: str = "", pack_id: str | None = None) -> dict:
    request_seq = time.time_ns()
    chapter = _chapter(project_id, chapter_n)
    if not chapter:
        raise FileNotFoundError(f"第{chapter_n}章尚未生成")
    foundation = data_foundation_service.load_foundation(project_id, pack_id=pack_id)
    selected = [
        section for section in chapter.get("sections", [])
        if not section_title or str(section.get("title", "")).strip() == section_title.strip()
    ]
    if section_title and not selected:
        raise FileNotFoundError(f"未找到小节：{section_title}")
    run_updates = {}
    rules = data_foundation_service.load_rules(pack_id) if use_ai else {}
    for section in selected:
        key = _section_key(chapter_n, section)
        configured_section_id = _section_id_for_title(
            chapter_n, str(section.get("title", "")), pack_id)
        deterministic = deterministic_audit(section, foundation)
        ai_issues, ai_error = [], ""
        if use_ai:
            checklist = (rules.get("audit_checks", {}).get(configured_section_id) or {}).get("checklist")
            try:
                ai_issues = _ai_audit(section, foundation, checklist)
            except Exception as exc:
                ai_error = str(exc)
        issues = deterministic + ai_issues
        for issue in issues:
            issue["section_id"] = configured_section_id
            issue["chapter"] = chapter_n
        run_updates[key] = {
            "key": key,
            "chapter": chapter_n,
            "section_id": configured_section_id or section.get("id", ""),
            "title": section.get("title", ""),
            "audited_at": _now(),
            "request_seq": request_seq,
            "ai_requested": bool(use_ai),
            "ai_status": "failed" if ai_error else ("completed" if ai_issues else "not_configured_or_no_issue"),
            "ai_error": ai_error,
            "issues": issues,
            "stats": {
                "total": len(issues),
                "error": sum(i.get("severity") == "error" for i in issues),
                "warning": sum(i.get("severity") == "warning" for i in issues),
                "info": sum(i.get("severity") == "info" for i in issues),
            },
        }
    # Several section generations may finish at the same time. Merge only at write
    # time so a slow AI audit cannot overwrite a newer run saved by another thread.
    with _AUDIT_WRITE_LOCK:
        store = load_audit(project_id)
        saved_runs = store.setdefault("runs", {})
        for key, update in run_updates.items():
            current = saved_runs.get(key) or {}
            if int(current.get("request_seq") or 0) <= request_seq:
                saved_runs[key] = update
        if store.get("whole_report"):
            store["whole_report"]["stale"] = True
            store["whole_report"]["stale_reason"] = "已有小节重新生成或重新审核，请重新运行全文一致性校验"
        store["updated_at"] = _now()
        _write(project_id, store)
    return store


def audit_whole_report(project_id: str | None, pack_id: str | None = None) -> dict:
    """跨小节一致性校验：全部小节都生成完之后运行一次，检查单独看任何一节都发现不了、
    放在一起才能看出的矛盾（主体名称、日期口径、数字口径、附件呼应等）。由业务在
    report-audit/SKILL.md 维护的 Know-how 驱动，编辑后下次运行立即生效，不需要额外编译。
    只检查跨节问题，单节内部问题已经在小节审核（deterministic_audit/_ai_audit）里处理。
    """
    from backend.services import pack_service, skill_runner
    from backend.services.kimi_client import chat
    from backend.config import MOONSHOT_API_KEY, DEEPSEEK_API_KEY

    sections_payload = []
    for chapter_n in range(1, 8):
        chapter = _chapter(project_id, chapter_n)
        for section in chapter.get("sections", []):
            blocks = section.get("blocks") or []
            if not blocks:
                continue
            sections_payload.append({
                "chapter": chapter_n, "title": section.get("title", ""),
                "text": "\n".join(_block_plain(b) for b in blocks)[:4000],
            })
    if not sections_payload:
        raise FileNotFoundError("尚无已生成的小节，无法运行全文一致性校验")

    try:
        know_how = pack_service.skill_text_path("report-audit/SKILL.md", pack_id).read_text(encoding="utf-8")
    except Exception:
        know_how = ""

    model = skill_runner.get_selected_model()
    has_key = DEEPSEEK_API_KEY if model.lower().startswith("deepseek") else MOONSHOT_API_KEY
    issues, error = [], ""
    if has_key:
        prompt = (
            "你是基础设施REITs申报材料复核人员，正在做全文一致性校验：不检查单节内部问题"
            "（那部分已经在小节审核里做过），只找跨小节才能发现的矛盾，例如同一主体名称在"
            "不同小节不一致、同一事实数字或口径不一致、日期基准不一致、附件呼应不上等。"
            "以下是业务维护的校验要点（Know-how 原文）：\n" + know_how[:6000]
            + "\n\n以下是已生成的全部小节内容：\n"
            + json.dumps(sections_payload, ensure_ascii=False)[:80000]
            + "\n\n只报告确有依据的矛盾；信息不足以判断时标注需要人工复核，不要直接判错。"
              "输出 JSON：{\"issues\":[{\"type\":\"\",\"severity\":\"error|warning|info\","
              "\"location\":\"\",\"description\":\"\",\"suggestion\":\"\"}]}。只输出 JSON。"
        )
        try:
            raw = chat([{"role": "user", "content": prompt}], model=model, temperature=0.2)
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
            parsed = json.loads(raw)
            for item in parsed.get("issues", []):
                if not isinstance(item, dict) or not item.get("description"):
                    continue
                severity = item.get("severity") if item.get("severity") in {"error", "warning", "info"} else "warning"
                issues.append(_issue("cross_section", severity, item.get("location", "全文"),
                                     item.get("description", ""), item.get("suggestion", "")))
        except Exception as exc:
            error = str(exc)
    else:
        error = "未配置模型密钥，无法运行全文一致性校验"

    whole_report = {
        "audited_at": _now(), "sections_checked": len(sections_payload),
        "stale": False, "stale_reason": "",
        "issues": issues, "error": error,
        "stats": {
            "total": len(issues),
            "error": sum(i["severity"] == "error" for i in issues),
            "warning": sum(i["severity"] == "warning" for i in issues),
            "info": sum(i["severity"] == "info" for i in issues),
        },
    }
    with _AUDIT_WRITE_LOCK:
        store = load_audit(project_id)
        store["whole_report"] = whole_report
        store["updated_at"] = _now()
        _write(project_id, store)
    return store


def audit_report(project_id: str | None, use_ai: bool = True, pack_id: str | None = None) -> dict:
    found = False
    result = load_audit(project_id)
    for chapter_n in range(1, 8):
        if _chapter(project_id, chapter_n):
            found = True
            result = audit_chapter(project_id, chapter_n, use_ai=use_ai, pack_id=pack_id)
    if not found:
        raise FileNotFoundError("尚无可审核的已生成章节")
    return result
