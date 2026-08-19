"""Business-maintained inputs kept outside the extracted data middle layer."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.config import PROJECTS_DIR, safe_project_id

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _materials(project_id: str | None) -> Path:
    return PROJECTS_DIR / safe_project_id(project_id) / "materials"


def input_path(project_id: str | None) -> Path:
    return PROJECTS_DIR / safe_project_id(project_id) / "manual_inputs.json"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _discover_input(root: Path, slot: str) -> Path | None:
    """Identify the two business-owned inputs from their table schema.

    Filenames are intentionally ignored: the same Know-how pack must work for a
    different project whose business users name these workbooks differently.
    """
    candidates = sorted(root.rglob("*.docx"), key=lambda p: (len(p.parts), len(str(p)))) if root.is_dir() else []
    signatures = ({"项目名称", "申报基准日", "原始权益人"}
                  if slot == "project_summary"
                  else {"子项目名称", "建设内容和规模", "运营起始时间"})
    for candidate in candidates:
        try:
            labels = {row.get("label", "").strip() for row in _rows(_read(candidate))}
        except Exception:
            continue
        if signatures.issubset(labels):
            return candidate
    return None


def _read(path: Path | None) -> dict:
    if not path:
        return {"paragraphs": [], "tables": []}
    # Shared ordered DOCX reader; imported lazily to avoid service import cycles.
    from backend.services.data_foundation_service import _read_docx_data
    return _read_docx_data(path)


def _rows(data: dict) -> list[dict]:
    from backend.services.data_foundation_service import _table_rows
    return _table_rows(data)


def build_manual_inputs(project_id: str | None) -> dict:
    root = _materials(project_id)
    summary = _discover_input(root, "project_summary")
    overview = _discover_input(root, "project_overview")
    summary_data, overview_data = _read(summary), _read(overview)

    def source(role: str, label: str, path: Path | None) -> dict:
        return {
            "role": role,
            "label": label,
            "kind": "manual_input",
            "status": "located" if path else "missing",
            "path": path.relative_to(root).as_posix() if path else "",
            "filename": path.name if path else "",
            "sha256": _sha(path) if path else "",
        }

    data = {
        "schema_version": "1.0",
        "project_id": safe_project_id(project_id),
        "updated_at": _now(),
        "sources": [
            source("user_summary", "摘要表", summary),
            source("project_overview_table", "项目概况表", overview),
        ],
        "summary": {
            "paragraphs": summary_data.get("paragraphs", []),
            "rows": _rows(summary_data),
        },
        "project_overview": {
            "paragraphs": overview_data.get("paragraphs", []),
            "rows": _rows(overview_data),
        },
    }
    target = input_path(project_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def load_manual_inputs(project_id: str | None, refresh_if_stale: bool = True) -> dict | None:
    target = input_path(project_id)
    if not target.exists():
        return build_manual_inputs(project_id)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return build_manual_inputs(project_id)
    if refresh_if_stale:
        root = _materials(project_id)
        saved = {s.get("role"): s.get("sha256") for s in data.get("sources", [])}
        current = {
            "user_summary": _discover_input(root, "project_summary"),
            "project_overview_table": _discover_input(root, "project_overview"),
        }
        if any((_sha(path) if path else "") != saved.get(role, "")
               for role, path in current.items()):
            return build_manual_inputs(project_id)
    return data


def row_maps(data: dict | None) -> dict[str, dict[str, dict]]:
    data = data or {}
    return {
        "user_summary": {r.get("label", "").strip(): r for r in data.get("summary", {}).get("rows", []) if r.get("label")},
        "project_overview_table": {r.get("label", "").strip(): r for r in data.get("project_overview", {}).get("rows", []) if r.get("label")},
    }


def prompt_context(project_id: str | None) -> str:
    data = load_manual_inputs(project_id)
    lines = ["# 业务人工输入（以业务上传的两份 Word 为准）"]
    for group, title in (("summary", "摘要表"), ("project_overview", "项目概况表")):
        lines.append(f"## {title}")
        for row in (data or {}).get(group, {}).get("rows", []):
            lines.append(f"- {row.get('label')}：{row.get('value')}")
    return "\n".join(lines)
