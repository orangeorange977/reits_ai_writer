"""Compile business Know-how into three editable, executable section artifacts.

The runtime source of truth remains the pack-level ``data-foundation/rules.json`` so
existing extraction/generation code does not need a second configuration system.  The
three business-facing artifacts are projections of that source of truth.  Saving one of
them parses it back into ``rules.json``; therefore they are executable, not decoration.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from backend.services import pack_service, section_skill_service, skill_runner

_ALLOWED_BLOCK_TYPES = {"p", "kv", "overview_table", "financial_grid"}
_RULES_REL = "data-foundation/rules.json"
_ARTIFACT_NAMES = {
    "extraction": "EXTRACTION_RULES.json",
    "generation": "GENERATION_SKILL.md",
    "audit": "AUDIT_SKILL.md",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _strip_json_fence(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)


def _knowhow_examples(know_how_text: str) -> list[dict]:
    """Keep the user's example verbatim in the executable generation Skill.

    Examples are reference material, never project facts.  Storing them in the
    compiled artifact (instead of loading Know-how at generation time) preserves
    the hard boundary between authoring input and the runtime Skill.
    """
    match = re.search(r"(?ims)^#{1,6}\s*#?示例#?\s*$\n?(.*)$", know_how_text or "")
    content = (match.group(1).strip() if match else "")
    return ([{
        "source": "Know-how #示例#",
        "reference_only": True,
        "content": content[:16000],
    }] if content else [])


def _read_rules(pack_id: str | None = None, use_default: bool = False) -> dict:
    path = pack_service.pack_path(_RULES_REL, pack_id) if use_default else pack_service.skill_text_path(_RULES_REL, pack_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_rules(rules: dict, pack_id: str | None = None) -> None:
    path = pack_service.override_path(_RULES_REL, pack_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def artifact_rel(section_id: str, kind: str, pack_id: str | None = None) -> str:
    config = section_skill_service.get_section(section_id, pack_id)
    if kind not in _ARTIFACT_NAMES:
        raise KeyError(f"未知编译产物类型：{kind}")
    return f"{config['skill']}/compiled/{_ARTIFACT_NAMES[kind]}"


def compiled_artifacts(section_id: str, pack_id: str | None = None) -> list[dict]:
    return [
        {"rel": artifact_rel(section_id, kind, pack_id), "kind": f"compiled_{kind}",
         "artifact_kind": kind, "section_id": section_id,
         "label": f"{section_id} {label}"}
        for kind, label in (
            ("extraction", "提取规则"), ("generation", "生成 SKILL"), ("audit", "AI 审核 SKILL"))
    ]


def _sections(field: dict) -> list[str]:
    values = field.get("used_by_sections")
    if isinstance(values, list) and values:
        return sorted({str(x).strip() for x in values if str(x).strip()})
    sid = str(field.get("section_id", "")).strip()
    return [sid] if sid else []


def _norm(value: object) -> str:
    return re.sub(r"[\s（）()【】\[\]：:、，,。.]", "", str(value or "")).lower()


def _field_signature(field: dict) -> tuple[str, ...]:
    """Semantic signature for safe cross-section de-duplication.

    Weighted by *where a fact comes from* (source_role + source_label + strategy),
    not by its display label.  Two Know-how documents describing the same
    extraction point routinely give it different Chinese wording for their own
    section's table (公司名称 vs 原始权益人名称 vs 发起人名称) even though it is
    the same underlying fact from the same source location — matching on label
    text alone misses that and creates duplicate fields.
    """
    return (
        _norm(field.get("source_role")),
        _norm(field.get("source_label") or field.get("label")),
        _norm(field.get("strategy")),
    )


def _role_signature(role: dict) -> str:
    """Stable semantic signature for a reusable material-selection rule.

    Role ids are authoring conveniences.  Two Know-how documents may call the
    same thing ``audit_reports`` and ``originator_financials``; if their selector
    and input semantics are identical they must become one shared rule.
    """
    semantic = {
        "input_kind": role.get("input_kind", "document"),
        "input_slot": role.get("input_slot", ""),
        "selector": role.get("selector") or {},
    }
    return json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_source_roles(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], dict[str, str], dict]:
    """Merge material-selection rules and alias semantically duplicate role ids."""
    output = [deepcopy(x) for x in existing if isinstance(x, dict) and x.get("id")]
    by_id = {str(x["id"]): x for x in output}
    by_signature = {_role_signature(x): x for x in output}
    aliases: dict[str, str] = {}
    report = {"added": 0, "updated": 0, "reused": 0, "merged": []}

    for raw in incoming:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        role = deepcopy(raw)
        role_id = str(role["id"])
        current = by_id.get(role_id)
        if current is not None:
            old_signature = _role_signature(current)
            current.update(role)
            if by_signature.get(old_signature) is current:
                by_signature.pop(old_signature, None)
            by_signature[_role_signature(current)] = current
            report["updated"] += 1
            continue

        canonical = by_signature.get(_role_signature(role))
        if canonical is not None:
            canonical_id = str(canonical["id"])
            aliases[role_id] = canonical_id
            report["reused"] += 1
            report["merged"].append({"from": role_id, "to": canonical_id})
            continue

        output.append(role)
        by_id[role_id] = role
        by_signature[_role_signature(role)] = role
        report["added"] += 1
    return output, aliases, report


def _replace_field_refs(value, aliases: dict[str, str], key: str = ""):
    if isinstance(value, dict):
        if key == "field_formats":
            return {aliases.get(k, k): _replace_field_refs(v, aliases, k) for k, v in value.items()}
        return {k: _replace_field_refs(v, aliases, k) for k, v in value.items()}
    if isinstance(value, list):
        if key in {"src_fields", "if_all", "required_fields", "field_ids"}:
            return [aliases.get(str(x), str(x)) for x in value]
        return [_replace_field_refs(x, aliases, key) for x in value]
    if isinstance(value, str):
        if key in {"field_id", "field", "left_field", "right_field"}:
            return aliases.get(value, value)
        for old, new in aliases.items():
            if old != new:
                value = value.replace("{{" + old + "}}", "{{" + new + "}}")
        return value
    return value


def _remove_section_fields(fields: list[dict], section_id: str) -> list[dict]:
    output = []
    for raw in fields:
        field = deepcopy(raw)
        uses = [x for x in _sections(field) if x != section_id]
        if not uses:
            continue
        field["used_by_sections"] = uses
        field["section_id"] = uses[0]
        output.append(field)
    return output


def _merge_fields(existing: list[dict], incoming: list[dict], section_id: str) -> tuple[list[dict], dict[str, str], dict]:
    """Merge the target section into the global dictionary and return aliases/report."""
    previous_by_id = {str(field.get("id")): field for field in existing if field.get("id")}
    candidates = _remove_section_fields(existing, section_id)
    for raw in incoming:
        field = deepcopy(raw)
        previous = previous_by_id.get(str(field.get("id", ""))) or {}
        for key in ("layer", "value_type", "unit"):
            if key not in field and key in previous:
                field[key] = deepcopy(previous[key])
        field["section_id"] = section_id
        field["used_by_sections"] = sorted(set(_sections(field) + [section_id]))
        candidates.append(field)

    output: list[dict] = []
    by_id: dict[str, dict] = {}
    by_signature: dict[tuple[str, ...], dict] = {}
    aliases: dict[str, str] = {}
    report = {"added": 0, "reused": 0, "merged": [], "conflicts": []}
    incoming_ids = {str(f.get("id", "")) for f in incoming}

    for raw in candidates:
        field = deepcopy(raw)
        field_id = str(field.get("id", "")).strip()
        if not field_id:
            continue
        signature = _field_signature(field)
        signature_safe = all(signature)
        canonical = by_id.get(field_id) or (by_signature.get(signature) if signature_safe else None)
        if canonical is None:
            field["used_by_sections"] = _sections(field)
            field["section_id"] = (field["used_by_sections"] or [section_id])[0]
            output.append(field)
            by_id[field_id] = field
            if signature_safe:
                by_signature[signature] = field
            if field_id in incoming_ids:
                report["added"] += 1
            continue

        canonical_id = str(canonical.get("id"))
        aliases[field_id] = canonical_id
        combined_sections = sorted(set(_sections(canonical) + _sections(field)))
        canonical["used_by_sections"] = combined_sections
        canonical["section_id"] = combined_sections[0] if combined_sections else section_id
        differing = [k for k in ("source_role", "source_label", "strategy", "unit", "value_type")
                     if canonical.get(k) and field.get(k) and str(canonical.get(k)) != str(field.get(k))]
        if field_id == canonical_id and differing and section_id in _sections(field):
            report["conflicts"].append({
                "field_id": field_id, "keys": differing,
                "decision": "采用本次编译规则，并保留共享使用关系",
            })
            for key, value in field.items():
                if key not in {"id", "section_id", "used_by_sections"} and value not in (None, "", []):
                    canonical[key] = deepcopy(value)
        report["reused"] += 1
        report["merged"].append({
            "from": field_id, "to": canonical_id, "used_by_sections": combined_sections,
        })
    return output, aliases, report


def _prepare_payload(payload: dict, section_id: str) -> dict:
    result = deepcopy(payload or {})
    result.setdefault("source_roles", [])
    result.setdefault("financial_metrics", [])
    for field in result.get("fields") or []:
        field["section_id"] = section_id
        field["used_by_sections"] = sorted(set(_sections(field) + [section_id]))
    return result


def _validate_compiled(payload: dict, section_id: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["AI 输出必须是一个 JSON 对象"]
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append("fields 必须是非空数组")
    else:
        seen = set()
        for idx, field in enumerate(fields):
            if not isinstance(field, dict):
                errors.append(f"fields[{idx}] 不是对象")
                continue
            if not field.get("id") or not field.get("label") or not field.get("source_role"):
                errors.append(f"fields[{idx}]（{field.get('id', '?')}）缺少 id/label/source_role")
            if field.get("section_id") and field.get("section_id") != section_id:
                errors.append(f"fields[{idx}]（{field.get('id', '?')}）section_id 与目标小节 {section_id} 不符")
            if field.get("id") in seen:
                errors.append(f"fields[{idx}] 的 id 重复：{field.get('id')}")
            if field.get("source_path"):
                errors.append(f"fields[{idx}]（{field.get('id', '?')}）不得绑定具体 source_path")
            if re.search(r"\.\d{4}$", str(field.get("id", ""))):
                errors.append(f"fields[{idx}]（{field.get('id', '?')}）不得把具体年份写入模板字段 ID")
            if re.fullmatch(r"audit_report_\d{4}", str(field.get("source_role", ""))):
                errors.append(f"fields[{idx}]（{field.get('id', '?')}）应使用通用 audit_reports 角色")
            seen.add(field.get("id"))
    for idx, role in enumerate(payload.get("source_roles") or []):
        if not isinstance(role, dict) or not role.get("id"):
            errors.append(f"source_roles[{idx}] 缺少 id")
            continue
        if any(role.get(key) for key in ("filename", "filename_contains", "fallback_contains", "source_path")):
            errors.append(f"source_roles[{idx}]（{role.get('id')}）不得绑定项目文件名；请使用 selector")
        if re.fullmatch(r"audit_report_\d{4}", str(role.get("id", ""))):
            errors.append(f"source_roles[{idx}] 不得使用具体年份角色")
    template = (payload.get("generation_templates") or {}).get(section_id)
    if not isinstance(template, dict) or not isinstance(template.get("blocks"), list) or not template.get("blocks"):
        errors.append(f"generation_templates.{section_id} 缺少非空 blocks")
    else:
        repeat = template.get("repeat_by")
        if repeat is not None and not (
                isinstance(repeat, str) and repeat.strip()
                or isinstance(repeat, dict) and str(repeat.get("field_id", "")).strip()):
            errors.append(f"generation_templates.{section_id}.repeat_by 必须是字段 ID 或含 field_id 的对象")
        examples = template.get("style_examples", [])
        if not isinstance(examples, list) or any(
                not isinstance(item, (str, dict)) for item in examples):
            errors.append(f"generation_templates.{section_id}.style_examples 必须是字符串或对象数组")
        for idx, block in enumerate(template["blocks"]):
            if not isinstance(block, dict) or block.get("type") not in _ALLOWED_BLOCK_TYPES:
                errors.append(f"generation_templates.{section_id}.blocks[{idx}] 的 type 未知或缺失（只能是 {'/'.join(sorted(_ALLOWED_BLOCK_TYPES))}）")
    checks = (payload.get("audit_checks") or {}).get(section_id)
    if not isinstance(checks, dict) or not isinstance(checks.get("checklist"), list):
        errors.append(f"audit_checks.{section_id} 缺少 checklist 数组")
    return errors


def _merge_payload_into_rules(rules: dict, payload: dict, section_id: str) -> tuple[dict, dict]:
    merged = deepcopy(rules)
    payload = _prepare_payload(payload, section_id)
    source_roles, role_aliases, role_report = _merge_source_roles(
        merged.get("source_roles", []), payload.get("source_roles") or [])
    if role_aliases:
        for field in payload.get("fields") or []:
            role = str(field.get("source_role", ""))
            field["source_role"] = role_aliases.get(role, role)
    fields, aliases, report = _merge_fields(merged.get("fields", []), payload["fields"], section_id)
    merged["fields"] = fields
    merged["source_roles"] = source_roles

    existing_metrics = {str(x.get("id")): x for x in merged.get("financial_metrics", []) if isinstance(x, dict)}
    for metric in payload.get("financial_metrics") or []:
        if isinstance(metric, dict) and metric.get("id"):
            existing_metrics[str(metric["id"])] = deepcopy(metric)
    merged["financial_metrics"] = list(existing_metrics.values())

    merged.setdefault("generation_templates", {})[section_id] = deepcopy(payload["generation_templates"][section_id])
    merged.setdefault("audit_checks", {})[section_id] = deepcopy(payload["audit_checks"][section_id])
    if aliases:
        merged["generation_templates"] = _replace_field_refs(merged["generation_templates"], aliases)
        merged["validations"] = _replace_field_refs(merged.get("validations", []), aliases)
    report["aliases"] = aliases
    report["source_role_aliases"] = role_aliases
    report["source_roles"] = role_report
    report["field_total_after_merge"] = len(merged.get("fields", []))
    return merged, report


def _section_fields(rules: dict, section_id: str) -> list[dict]:
    return [deepcopy(f) for f in rules.get("fields", []) if section_id in _sections(f)]


def _artifact_payload(rules: dict, section_id: str, kind: str):
    if kind == "extraction":
        roles = {f.get("source_role") for f in _section_fields(rules, section_id)}
        return {
            "schema_version": "1.0", "section_id": section_id,
            "source_roles": [deepcopy(r) for r in rules.get("source_roles", []) if r.get("id") in roles],
            "fields": _section_fields(rules, section_id),
            "financial_metrics": deepcopy(rules.get("financial_metrics", [])) if section_id == "2.3" else [],
        }
    if kind == "generation":
        return deepcopy((rules.get("generation_templates") or {}).get(section_id) or {})
    if kind == "audit":
        return deepcopy((rules.get("audit_checks") or {}).get(section_id) or {"checklist": []})
    raise KeyError(kind)


def artifact_text(section_id: str, kind: str, pack_id: str | None = None,
                  use_default: bool = False, rules: dict | None = None) -> str:
    rules = rules or _read_rules(pack_id, use_default=use_default)
    payload = _artifact_payload(rules, section_id, kind)
    if kind == "extraction":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    config = section_skill_service.get_section(section_id, pack_id)
    if kind == "generation":
        return (
            f"# {section_id} {config['title']} · 生成 SKILL\n\n"
            "> 本文件是可执行配置。正文说明可以修改；必须保留并正确编辑下方 JSON。保存后下一次生成本小节立即生效。\n\n"
            "## 可执行生成模板\n\n```json\n"
            + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
        )
    checklist = payload.get("checklist") or []
    return (
        f"# {section_id} {config['title']} · AI 审核 SKILL\n\n"
        "> 每行一个审核规则。保存后下一次生成后审核或手工审核立即生效。\n\n"
        "## 审核清单\n\n" + ("\n".join(f"- {item}" for item in checklist) or "- 检查关键事实是否有来源") + "\n"
    )


def _write_artifacts(section_id: str, rules: dict, pack_id: str | None = None) -> None:
    for kind in _ARTIFACT_NAMES:
        path = pack_service.override_path(artifact_rel(section_id, kind, pack_id), pack_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(artifact_text(section_id, kind, pack_id, rules=rules), encoding="utf-8")


def recompile(section_id: str, know_how_text: str, pack_id: str | None = None) -> dict:
    """Compile Know-how to a no-write preview including de-duplication report."""
    from backend.services.kimi_client import chat

    if not (know_how_text or "").strip():
        return {"ok": False, "errors": ["Know-how 原文为空，无法编译"], "preview": None}
    current = _read_rules(pack_id)
    example = {
        "source_roles": current.get("source_roles", []),
        "fields": _section_fields(current, section_id),
        "financial_metrics": current.get("financial_metrics", []) if section_id == "2.3" else [],
        "generation_templates": {section_id: (current.get("generation_templates") or {}).get(section_id)},
        "audit_checks": {section_id: (current.get("audit_checks") or {}).get(section_id)},
    }
    catalog = [
        {"id": f.get("id"), "label": f.get("label"), "source_role": f.get("source_role"),
         "source_label": f.get("source_label") or f.get("label"), "strategy": f.get("strategy"),
         "used_by_sections": _sections(f)}
        for f in current.get("fields", []) if f.get("id")
    ]
    prompt = (
        "你是REITs申报材料系统的规则编译器。把下面业务 Know-how 编译为可执行 JSON，目标小节固定为 "
        f"{section_id}。顶层必须是 source_roles / fields / generation_templates / audit_checks。\n"
        "先按职责拆分，严禁把 Know-how 整篇复制到任一产物：\n"
        "- Word/YAML 中的版本、日期、修订人、审核人、状态、来源文件和导入时间属于文档管理元数据，三件套全部忽略；\n"
        "- #资料来源#、文件选择、字段定位、抽取/换算口径只进入 source_roles/fields/financial_metrics；\n"
        "- #模板# 的最终正文结构、固定文字、表格、标题、多主体循环和缺失时如何显示，只进入 generation_templates；必须完整保留所有要求输出的正文结构，不得过度删减；\n"
        "- #一致性校验# 原则上只进入 audit_checks，不得塞进生成模板；只有防止错误成文所必需的门槛，才转成 if_all/else_template；\n"
        "- #示例# 必须保留到 generation_templates.<section>.style_examples，作为语言、结构和分析方式参考；"
        "示例不是项目事实，禁止把其中名称、日期、金额、结论或附件号用于当前项目。\n"
        "source_roles 只列本节新增或修改的语义材料角色，包含 id/label/required/match_prompt；"
        "项目文件使用 selector（document_type/extensions/filename_keywords_any/filename_keywords_all/"
        "path_keywords_any/exclude_keywords_any/subject_ref/repeat_by）。禁止写具体项目名、公司名、文件名、路径或年份。\n"
        "fields 每项含 id/label/section_id/group/required/source_role/source_label/strategy/extract_prompt；"
        "不得输出 source_path。判断“同一事实”以 source_role + source_label（从哪个来源的哪个位置取）为准，"
        "不要以 label 中文措辞是否相同为准——不同小节常用不同措辞描述同一个来源事实。"
        "编译前必须先查下方“全局已存在字段目录”：只要目标字段的 source_role + source_label 与目录中某条一致"
        "（即使 label 文字不同），必须直接复用该字段的 id，不得新造同义字段；"
        "只有 source_role 或 source_label 确实不同时才允许新建 id。"
        "最近三年及一期财务指标写入 financial_metrics，年份由申报基准日在项目运行时绑定，"
        "禁止生成 finance.xxx.2024 或 audit_report_2024 这类实例规则。"
        "strategy 只能是 table_exact、regex:<正则>、document_label、document_conclusion、document_list、external_company_lookup、external_public_search、document_search、derived_analysis、manual。\n"
        "generation_templates 的 block.type 只能是 p/kv/overview_table/financial_grid；p 用 {{field.id}}，kv 用 rows.field_id。"
        "Know-how 要求多个主体逐一完整输出时，generation_templates.<section>.repeat_by 必须设置为"
        "{\"field_id\":\"主体清单字段\",\"separator_regex\":\"[、,，;；/\\\\n]+\","
        "\"scoped_prefixes\":[\"originator.\",\"finance.\",\"compliance.\",\"credit.\"]}，"
        "循环序号使用 {{repeat.index}}，当前主体使用该 field_id；表格 caption 也允许字段占位符。\n"
        "Know-how 明确写“待定”或固定模板文字、且没有材料来源的表格项，应写成 kv.rows 的静态 value，不要创建数据底座字段。\n"
        "audit_checks.checklist 从一致性校验、缺失处理和来源要求提炼。禁止把 #示例# 的数值、名称、日期或结论当真实值。只输出 JSON。\n\n"
        "全局已存在字段目录（跨全部小节，source_role+source_label 相同即视为同一事实，必须复用其 id）：\n"
        + json.dumps(catalog, ensure_ascii=False)[:12000]
        + "\n\n当前同小节可执行三件套（优先复用字段 ID 和结构）：\n"
        + json.dumps(example, ensure_ascii=False)[:18000]
        + "\n\nKnow-how 原文：\n" + know_how_text[:30000]
    )
    raw = _strip_json_fence(chat([{"role": "user", "content": prompt}], model=skill_runner.get_selected_model(), temperature=0.2) or "")
    try:
        payload = _prepare_payload(json.loads(raw), section_id)
    except Exception as exc:
        return {"ok": False, "errors": [f"AI 输出不是合法 JSON：{exc}"], "preview": None, "raw": raw[:2000]}
    # 示例由确定性代码从 Know-how 原文复制，避免模型漏掉、改写或把示例事实
    # 混入 blocks。运行时只读取编译后的 generation Skill，不回读 Know-how。
    template = (payload.get("generation_templates") or {}).get(section_id)
    if isinstance(template, dict):
        template["style_examples"] = _knowhow_examples(know_how_text)
        template.setdefault("style_instructions", [
            "参考示例的正式申报材料文体、段落组织和分析方式",
            "仅使用当前项目数据，不得复制示例中的名称、日期、金额、结论或附件号",
            "表格和事实字段保持原值，缺失信息按生成规则保留待补充提示",
        ])
    errors = _validate_compiled(payload, section_id)
    if errors:
        return {"ok": False, "errors": errors, "preview": payload}
    hypothetical, merge_report = _merge_payload_into_rules(current, payload, section_id)
    artifacts = {kind: artifact_text(section_id, kind, pack_id, rules=hypothetical) for kind in _ARTIFACT_NAMES}
    return {"ok": True, "errors": [], "preview": payload, "merge_report": merge_report, "artifacts": artifacts}


def apply_compiled(section_id: str, payload: dict, pack_id: str | None = None) -> dict:
    payload = _prepare_payload(payload, section_id)
    errors = _validate_compiled(payload, section_id)
    if errors:
        raise ValueError("；".join(errors))
    current = _read_rules(pack_id)
    if not current:
        raise FileNotFoundError("当前模板包未配置数据底座规则，无法应用编译结果")
    rules, report = _merge_payload_into_rules(current, payload, section_id)
    rules["rule_version"] = f"{rules.get('rule_version', 'v1')}.recompile-{_now()}"
    rules.setdefault("compile_history", []).append({
        "section_id": section_id, "compiled_at": _now(), "merge_report": report,
    })
    rules["compile_history"] = rules["compile_history"][-50:]
    _write_rules(rules, pack_id)
    _write_artifacts(section_id, rules, pack_id)
    return rules


def _json_from_markdown(content: str) -> dict:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content or "", flags=re.I | re.S)
    if not match:
        raise ValueError("生成 SKILL 必须保留一个 JSON 代码块")
    return json.loads(match.group(1))


def _checklist_from_markdown(content: str) -> list[str]:
    items = []
    for line in (content or "").splitlines():
        match = re.match(r"\s*[-*]\s+(.+?)\s*$", line)
        if match and match.group(1).strip():
            items.append(match.group(1).strip())
    if not items:
        raise ValueError("审核 SKILL 至少需要一条以“- ”开头的审核规则")
    return items


def save_artifact(section_id: str, kind: str, content: str,
                  pack_id: str | None = None) -> dict:
    """Parse a business edit, synchronize runtime rules, and persist the artifact."""
    rules = _read_rules(pack_id)
    if not rules:
        raise FileNotFoundError("当前模板包未配置数据底座规则")
    if kind == "extraction":
        try:
            parsed = json.loads(content)
        except Exception as exc:
            raise ValueError(f"提取规则不是合法 JSON：{exc}") from exc
        payload = {
            "source_roles": parsed.get("source_roles", []),
            "fields": parsed.get("fields", []),
            "financial_metrics": parsed.get("financial_metrics", []),
            "generation_templates": {section_id: (rules.get("generation_templates") or {}).get(section_id)},
            "audit_checks": {section_id: (rules.get("audit_checks") or {}).get(section_id, {"checklist": []})},
        }
        payload = _prepare_payload(payload, section_id)
        errors = _validate_compiled(payload, section_id)
        if errors:
            raise ValueError("；".join(errors))
        rules, _ = _merge_payload_into_rules(rules, payload, section_id)
    elif kind == "generation":
        template = _json_from_markdown(content)
        if not isinstance(template.get("blocks"), list) or not template["blocks"]:
            raise ValueError("生成 SKILL 的 JSON 必须包含非空 blocks")
        bad = [b.get("type") for b in template["blocks"] if b.get("type") not in _ALLOWED_BLOCK_TYPES]
        if bad:
            raise ValueError(f"生成 SKILL 含不支持的 block.type：{bad}")
        rules.setdefault("generation_templates", {})[section_id] = template
    elif kind == "audit":
        rules.setdefault("audit_checks", {})[section_id] = {"checklist": _checklist_from_markdown(content)}
    else:
        raise ValueError(f"未知编译产物类型：{kind}")
    rules["rule_version"] = f"{rules.get('rule_version', 'v1')}.edit-{_now()}"
    _write_rules(rules, pack_id)
    path = pack_service.override_path(artifact_rel(section_id, kind, pack_id), pack_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return rules


def reset_artifact(section_id: str, kind: str, pack_id: str | None = None) -> dict:
    """Reset one artifact to the code-shipped component without losing other edits."""
    current, default = _read_rules(pack_id), _read_rules(pack_id, use_default=True)
    if kind == "extraction":
        payload = {
            "source_roles": default.get("source_roles", []),
            "fields": _section_fields(default, section_id),
            "generation_templates": {section_id: (current.get("generation_templates") or {}).get(section_id)},
            "audit_checks": {section_id: (current.get("audit_checks") or {}).get(section_id, {"checklist": []})},
        }
        current, _ = _merge_payload_into_rules(current, payload, section_id)
    elif kind == "generation":
        current.setdefault("generation_templates", {})[section_id] = deepcopy((default.get("generation_templates") or {}).get(section_id) or {})
    elif kind == "audit":
        current.setdefault("audit_checks", {})[section_id] = deepcopy((default.get("audit_checks") or {}).get(section_id) or {"checklist": []})
    else:
        raise ValueError(f"未知编译产物类型：{kind}")
    current["rule_version"] = f"{current.get('rule_version', 'v1')}.reset-{_now()}"
    _write_rules(current, pack_id)
    pack_service.override_path(artifact_rel(section_id, kind, pack_id), pack_id).unlink(missing_ok=True)
    return current


def reset_pack_rules_override(pack_id: str | None = None) -> None:
    pack_service.override_path(_RULES_REL, pack_id).unlink(missing_ok=True)
