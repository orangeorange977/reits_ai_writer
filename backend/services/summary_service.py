"""
摘要表 / 释义 / 其他基本信息 数据服务

数据的唯一来源是保存文件 summary_saved.json（由用户在网页上编辑、或上传 Excel 导入后保存产生）。
没有保存过时返回空结构，等用户录入——不再自动从 docx 或 planning.md 解析。
"""
import io
import json
import logging
from pathlib import Path

from openpyxl import load_workbook

from backend.config import PROJECTS_DIR, safe_project_id

logger = logging.getLogger(__name__)

# 用户在网页上核对/编辑/导入后保存的摘要表数据（唯一可信来源），按项目隔离存放
# （workspace/projects/<项目ID>/summary_saved.json）；未传项目时用默认项目目录。


def saved_summary_path(project_id: str = None) -> Path:
    return PROJECTS_DIR / safe_project_id(project_id) / "summary_saved.json"


_GROUP_KEYS = ("summary_table", "glossary", "other_info")


def save_summary_data(data: dict, project_id: str = None) -> None:
    """把网页上编辑好的摘要表/释义/其他基本信息保存到该项目的 JSON 文件。"""
    clean = {k: (data.get(k) or []) for k in _GROUP_KEYS}
    path = saved_summary_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_saved_summary(project_id: str = None):
    """读取该项目已保存的摘要表数据；没有则返回 None。"""
    path = saved_summary_path(project_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: (data.get(k) or []) for k in _GROUP_KEYS}
        except Exception as e:
            logger.warning(f"读取已保存摘要表失败: {e}")
    return None


def get_summary_data(project_id: str = None) -> dict:
    """返回 {summary_table, glossary, other_info}。

    唯一来源是该项目的保存文件；没有保存过则返回空结构（三个空列表），
    由用户在网页上录入或 Excel 导入后保存。
    """
    saved = load_saved_summary(project_id)
    if saved is not None:
        return saved
    return {"summary_table": [], "glossary": [], "other_info": []}


# Excel 三个 sheet 名 -> 结果里的键
_SHEET_MAP = {
    "摘要表": "summary_table",
    "释义": "glossary",
    "其他基本信息": "other_info",
}


def parse_import_excel(file_bytes: bytes) -> dict:
    """解析用户上传的 Excel：三个 sheet（摘要表/释义/其他基本信息），
    每 sheet 第一列=键、第二列=值。返回 {summary_table, glossary, other_info}。"""
    result = {"summary_table": [], "glossary": [], "other_info": []}
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)

    for sheet_name, result_key in _SHEET_MAP.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        for row in ws.iter_rows(values_only=True):
            if not row or all(c is None for c in row):
                continue
            label = "" if (len(row) < 1 or row[0] is None) else str(row[0]).strip()
            value = "" if (len(row) < 2 or row[1] is None) else str(row[1]).strip()
            if label == "" and value == "":
                continue
            result[result_key].append({"label": label, "value": value})

    return result
