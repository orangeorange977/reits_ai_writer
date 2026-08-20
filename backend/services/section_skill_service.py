"""Small-section Skill registry and generation.

The UI works with business sections, not seven large chapter agents.  A section
Skill defines extraction/write rules; generation consumes the saved data
foundation and writes only that section into the legacy chapter JSON so the
existing Word renderer remains compatible.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy

from backend.services import data_foundation_service, pack_service, skill_runner


def _registry(pack_id: str | None = None) -> list[dict]:
    path = pack_service.pack_path("sections.json", pack_id)
    return json.loads(path.read_text(encoding="utf-8")).get("sections", [])


def get_section(section_id: str, pack_id: str | None = None) -> dict:
    section = next((x for x in _registry(pack_id) if x.get("id") == section_id), None)
    if not section:
        raise KeyError(f"当前模板包没有小节 {section_id}")
    return section


def _skill_text(config: dict, pack_id: str | None = None) -> str:
    rel = f"{config['skill']}/SKILL.md"
    return pack_service.skill_text_path(rel, pack_id).read_text(encoding="utf-8")


def _saved_section(project_id: str | None, config: dict) -> dict | None:
    sections = skill_runner.get_chapter_structured(config["chapter_n"], project_id)
    return next((deepcopy(x) for x in sections if str(x.get("title", "")).strip() == config["title"]), None)


def _strip_json_fence(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)


def _numeric_facts(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?", text or ""))


def _reanchor_provenance(text: str, provenance: list[dict]) -> list[dict] | None:
    """Keep citations usable after AI rewrites a paragraph around exact fact strings."""
    output = []
    cursor = 0
    for raw in provenance or []:
        item = deepcopy(raw)
        value = str(item.get("display_value", ""))
        if not value:
            output.append(item)
            continue
        start = text.find(value, cursor)
        if start < 0:
            start = text.find(value)
        if start < 0:
            return None
        item["start"], item["end"] = start, start + len(value)
        cursor = item["end"]
        output.append(item)
    return output


def _polish_with_generation_skill(draft: dict, generation_skill: dict) -> tuple[dict, str, str]:
    """Use the compiled runtime Skill—not Know-how—to improve narrative paragraphs.

    Tables and exact values remain deterministic.  If the model changes/adds a
    numeric fact or drops a cited value, that paragraph is rejected and the
    template-rendered original is retained.
    """
    examples = generation_skill.get("style_examples") or []
    instructions = generation_skill.get("style_instructions") or []
    if not examples and not instructions:
        return draft, "skill_template", "生成 SKILL 未配置写作示例，已按可执行模板生成"
    candidates = []
    for index, block in enumerate(draft.get("blocks") or []):
        text = str(block.get("text", "")).strip()
        if block.get("type") == "p" and len(text) >= 20 and not text.startswith("【待"):
            candidates.append({"index": index, "text": text})
    if not candidates:
        return draft, "skill_template", "没有需要润色的叙述段落，已按可执行模板生成"

    prompt = (
        "你是REITs申报材料撰写器。下面的‘真实生成 SKILL’是本次唯一写作规则，Know-how 原文不会提供、也不得推测。\n"
        "只润色给出的叙述段落，表格由系统确定性生成。必须遵守：\n"
        "1. 示例只参考文体、结构和分析方法，绝不能复制示例里的名称、地址、日期、金额、结论或附件号；\n"
        "2. 不新增、删除、改写任何项目事实或数字；原段落中的事实值必须保持原字符串；\n"
        "3. 不补充无底稿支持的原因或判断；不改变【待补充】类提示；\n"
        "4. 只返回 JSON：{\"paragraphs\":[{\"index\":0,\"text\":\"...\"}]}。\n\n"
        "真实生成 SKILL：\n" + json.dumps(generation_skill, ensure_ascii=False)[:24000]
        + "\n\n当前项目待润色段落：\n" + json.dumps(candidates, ensure_ascii=False)
    )
    try:
        from backend.services.kimi_client import chat
        raw = chat([{"role": "user", "content": prompt}],
                   model=skill_runner.get_selected_model(), temperature=0.4)
        parsed = json.loads(_strip_json_fence(raw))
    except Exception as exc:
        return draft, "skill_template_fallback", f"生成 SKILL AI 润色失败，已保留模板结果：{exc}"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("paragraphs"), list):
        return draft, "skill_template_fallback", "生成 SKILL AI 返回格式无效，已保留模板结果"

    result = deepcopy(draft)
    by_index = {int(item.get("index")): str(item.get("text", "")).strip()
                for item in parsed.get("paragraphs", []) if str(item.get("index", "")).isdigit()}
    accepted = 0
    for candidate in candidates:
        index, original = candidate["index"], candidate["text"]
        rewritten = by_index.get(index, "")
        if not rewritten or len(rewritten) > max(300, len(original) * 3):
            continue
        if not _numeric_facts(rewritten).issubset(_numeric_facts(original)):
            continue
        if any(marker in rewritten and marker not in original
               for marker in ("虚构示例", "（虚构）", "国金数据", "万国数据", "星河数字", "赵某")):
            continue
        provenance = _reanchor_provenance(
            rewritten, result["blocks"][index].get("provenance") or [])
        if provenance is None:
            continue
        result["blocks"][index]["text"] = rewritten
        result["blocks"][index]["provenance"] = provenance
        accepted += 1
    if not accepted:
        return draft, "skill_template_fallback", "AI 返回内容未通过事实保护检查，已保留模板结果"
    return result, "generation_skill_ai", f"已按真实生成 SKILL 润色 {accepted} 个叙述段落"


def list_all_official_sections(project_id: str | None = None, pack_id: str | None = None) -> list[dict]:
    """全部官方二级小节（含尚无小节级 Know-how 的）——业务和 Skill 管理页共用的唯一小节清单。

    编号取官方模板小标题在其所属章节内的出现顺序 {章号}.{序号}，与已配置的
    sections.json 条目按 (chapter_n, title) 精确匹配后标注 configured。已配置的小节
    额外带生成/数据状态；未配置的小节只有 id/title/chapter 信息，用于业务看到
    "申报材料总共多少节、还缺哪些 Know-how"，不出现 skill/generated 等字段。
    """
    template = pack_service.template_docx(pack_id)
    all_subs = skill_runner.all_chapters_subtitles(str(template), pack_id) if template.exists() else {}
    chapters = skill_runner.chapters_for(pack_id)
    configured = {(c.get("chapter_n"), str(c.get("title", "")).strip()): c for c in _registry(pack_id)}

    foundation = data_foundation_service.load_foundation(project_id, pack_id=pack_id) if project_id is not None else None
    fields = (foundation or {}).get("fields", [])

    out = []
    for n in sorted(chapters):
        chapter_title = chapters[n]["title"]
        for i, title in enumerate(all_subs.get(n, []), 1):
            cfg = configured.get((n, str(title).strip()))
            row = {
                "id": f"{n}.{i}", "title": title, "chapter_n": n,
                "chapter_title": chapter_title, "configured": bool(cfg),
            }
            if cfg:
                relevant = [x for x in fields
                            if (x.get("section_id") == cfg["id"] or cfg["id"] in (x.get("used_by_sections") or []))
                            and x.get("status") != "disabled"]
                row.update({
                    **cfg,
                    "generated": bool(_saved_section(project_id, cfg)) if project_id is not None else False,
                    "data_ready": bool(foundation) and not (foundation or {}).get("stale"),
                    "field_total": len(relevant),
                    "field_filled": sum(bool(str(x.get("value", "")).strip()) for x in relevant),
                    "required_missing": sum(bool(x.get("required")) and not str(x.get("value", "")).strip() for x in relevant),
                    "skill_excerpt": _skill_text(cfg, pack_id).split("---", 2)[-1].strip()[:500],
                })
            out.append(row)
    return out


def list_sections(project_id: str | None, pack_id: str | None = None) -> list[dict]:
    """向后兼容：只返回已配置小节级 Know-how 的小节（旧调用点仍在用）。"""
    return [s for s in list_all_official_sections(project_id, pack_id) if s.get("configured")]


def generate_section(project_id: str | None, section_id: str,
                     pack_id: str | None = None) -> dict:
    config = get_section(section_id, pack_id)
    foundation = data_foundation_service.load_foundation(project_id, pack_id=pack_id)
    if not foundation:
        raise RuntimeError("尚未提取数据，请先在数据提取页点击“提取数据”")
    if foundation.get("stale"):
        raise RuntimeError("上传材料已变化，请重新提取数据后再生成")
    rules = data_foundation_service.load_rules(pack_id=pack_id)
    generation_skill = deepcopy((rules.get("generation_templates") or {}).get(section_id) or {})
    if not generation_skill.get("blocks"):
        raise RuntimeError(f"小节 {section_id} 尚无可执行生成 SKILL，请先在 Know-how 页编译并应用")
    if foundation.get("rule_version") != rules.get("rule_version"):
        raise RuntimeError("真实生成 SKILL 已更新，请先提取本节数据，再重新生成")
    draft = deepcopy((foundation.get("drafts") or {}).get(section_id))
    if not draft:
        raise RuntimeError(f"小节 {section_id} 的 Skill 尚未生成可用草稿")
    generated, mode, note = _polish_with_generation_skill(draft, generation_skill)
    result = skill_runner.upsert_structured_section(
        config["chapter_n"], generated, project_id,
        [f"真实生成 SKILL：{config['skill']}/compiled/GENERATION_SKILL.md", "数据中间层当前快照"], pack_id,
    )
    return {"config": config, "section": generated, "chapter": result,
            "generation_mode": mode, "message": note}


def generate_chapter_sections(project_id: str | None, chapter_n: int,
                              pack_id: str | None = None) -> dict:
    """按小节 Skill 顺序生成一章，不调用旧的大章 Agent。

    只生成已经配置 Know-how 的官方二级小节；未配置的小节显式列入 skipped，
    单个小节失败不会阻断同章其余小节，便于业务一次点击后集中处理异常。
    """
    official = [row for row in list_all_official_sections(project_id, pack_id)
                if row.get("chapter_n") == chapter_n]
    if not official:
        raise KeyError(f"第 {chapter_n} 章不存在")

    configured = [row for row in official if row.get("configured")]
    if not configured:
        raise RuntimeError(f"第 {chapter_n} 章尚未配置任何小节 Know-how")

    # 先做一次章级前置检查，避免基础数据不可用时把同一个错误重复列很多遍。
    foundation = data_foundation_service.load_foundation(project_id, pack_id=pack_id)
    if not foundation:
        raise RuntimeError("尚未提取数据，请先在数据提取页点击“提取数据”")
    if foundation.get("stale"):
        raise RuntimeError("上传材料已变化，请重新提取数据后再生成")

    generated, failed = [], []
    for row in configured:
        try:
            result = generate_section(project_id, row["id"], pack_id)
            generated.append({
                "id": row["id"],
                "title": row["title"],
                "chapter_n": chapter_n,
                "generated": True,
            })
        except Exception as exc:  # 一节失败不应阻断整章中其他已配置小节
            failed.append({
                "id": row["id"],
                "title": row["title"],
                "error": str(exc),
            })

    skipped = [{
        "id": row["id"],
        "title": row["title"],
        "reason": "未配置 Know-how",
    } for row in official if not row.get("configured")]
    return {
        "chapter_n": chapter_n,
        "chapter_title": official[0].get("chapter_title", ""),
        "official_total": len(official),
        "configured_total": len(configured),
        "generated_total": len(generated),
        "failed_total": len(failed),
        "skipped_total": len(skipped),
        "generated_sections": generated,
        "failed_sections": failed,
        "skipped_sections": skipped,
    }


def get_section_content(project_id: str | None, section_id: str,
                        pack_id: str | None = None) -> dict:
    config = get_section(section_id, pack_id)
    section = _saved_section(project_id, config)
    rules = data_foundation_service.load_rules(pack_id=pack_id)
    return {"config": config, "generated": bool(section), "section": section,
            "generation_skill": deepcopy((rules.get("generation_templates") or {}).get(section_id) or {})}
