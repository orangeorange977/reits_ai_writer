"""发改委章节映射配置加载模块

提供发改委申报材料章节结构映射和附件清单映射的加载、查询功能。
映射配置基于《基础设施领域不动产投资信托基金（REITs）项目申报材料格式文本（2024年版）》。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

MAPPINGS_DIR = Path(__file__).parent

# 模块级缓存变量
_cache_glossary: dict | None = None
_cache_commitment_templates: dict | None = None
_cache_financial_standards: dict | None = None
_cache_metadata_config: dict | None = None


def load_ndrc_chapter_mapping() -> dict:
    """加载发改委章节映射配置

    Returns:
        dict: 包含七章完整结构的映射配置
    """
    with open(MAPPINGS_DIR / "ndrc_chapter_mapping.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_ndrc_attachments_mapping() -> dict:
    """加载发改委附件映射配置

    Returns:
        dict: 附件材料清单映射配置
    """
    with open(MAPPINGS_DIR / "ndrc_attachments_mapping.json", "r", encoding="utf-8") as f:
        return json.load(f)


def get_chapter_by_id(chapter_id: str) -> Optional[dict]:
    """根据ID获取章节配置

    Args:
        chapter_id: 章节ID，如 'chapter1', 'chapter2' 等

    Returns:
        dict: 章节配置字典，未找到返回None
    """
    mapping = load_ndrc_chapter_mapping()
    for chapter in mapping["chapters"]:
        if chapter["id"] == chapter_id:
            return chapter
    return None


def get_section_by_id(section_id: str) -> Optional[dict]:
    """根据ID获取小节配置

    Args:
        section_id: 小节ID，如 '1-1', '2-3' 等

    Returns:
        dict: 小节配置字典（含所属chapter_id），未找到返回None
    """
    mapping = load_ndrc_chapter_mapping()
    for chapter in mapping["chapters"]:
        for section in chapter["sections"]:
            if section["id"] == section_id:
                result = section.copy()
                result["chapter_id"] = chapter["id"]
                result["chapter_title"] = chapter["title"]
                return result
    return None


def get_all_fields() -> List[dict]:
    """获取所有字段定义（扁平列表）

    Returns:
        list: 所有字段定义列表，每个字段包含所属chapter_id和section_id
    """
    mapping = load_ndrc_chapter_mapping()
    fields = []
    for chapter in mapping["chapters"]:
        for section in chapter["sections"]:
            for field in section.get("fields", []):
                field_copy = field.copy()
                field_copy["chapter_id"] = chapter["id"]
                field_copy["chapter_title"] = chapter["title"]
                field_copy["section_id"] = section["id"]
                field_copy["section_title"] = section["title"]
                fields.append(field_copy)
    return fields


def get_required_fields() -> List[dict]:
    """获取所有必填字段

    Returns:
        list: 必填字段列表
    """
    all_fields = get_all_fields()
    return [f for f in all_fields if f.get("required", False)]


def get_field_by_id(field_id: str) -> Optional[dict]:
    """根据字段ID查找字段定义

    Args:
        field_id: 字段ID，如 'project_name', 'originator' 等

    Returns:
        dict: 字段定义字典，未找到返回None
    """
    all_fields = get_all_fields()
    for field in all_fields:
        if field["id"] == field_id:
            return field
    return None


def get_attachment_by_id(attachment_id: str) -> Optional[dict]:
    """根据附件ID查找附件定义

    Args:
        attachment_id: 附件ID，如 'att-1', 'att-22' 等

    Returns:
        dict: 附件定义字典（含所属category），未找到返回None
    """
    mapping = load_ndrc_attachments_mapping()
    for category in mapping["attachment_categories"]:
        for item in category["items"]:
            if item["id"] == attachment_id:
                result = item.copy()
                result["category"] = category["category"]
                result["category_id"] = category["category_id"]
                return result
    return None


def get_required_attachments() -> List[dict]:
    """获取所有必须提交的附件清单

    Returns:
        list: 必须提交的附件列表
    """
    mapping = load_ndrc_attachments_mapping()
    required = []
    for category in mapping["attachment_categories"]:
        for item in category["items"]:
            if item.get("required", False):
                item_copy = item.copy()
                item_copy["category"] = category["category"]
                required.append(item_copy)
    return required


def get_chapter_summary() -> List[Dict[str, str]]:
    """获取章节概要列表（用于前端展示目录）

    Returns:
        list: 章节概要 [{"id": "chapter1", "title": "一、项目基本情况", "section_count": 3}, ...]
    """
    mapping = load_ndrc_chapter_mapping()
    summary = []
    for chapter in mapping["chapters"]:
        summary.append({
            "id": chapter["id"],
            "title": chapter["title"],
            "section_count": len(chapter["sections"]),
            "sections": [
                {"id": s["id"], "title": s["title"], "field_count": len(s.get("fields", []))}
                for s in chapter["sections"]
            ]
        })
    return summary


def load_table_schemas() -> dict:
    """加载官方模板表格Schema定义

    Returns:
        表格Schema字典，包含所有表格的结构定义
    """
    schema_path = Path(__file__).parent / "ndrc_table_schemas.json"
    if not schema_path.exists():
        logger.warning(f"表格Schema文件不存在: {schema_path}")
        return {"version": "2024", "tables": []}

    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_glossary(cache: bool = True) -> dict:
    """加载发改委术语表配置

    Args:
        cache: 是否缓存结果，默认True

    Returns:
        dict: 术语表字典，加载失败返回空dict
    """
    global _cache_glossary
    if cache and _cache_glossary is not None:
        return _cache_glossary

    try:
        filepath = MAPPINGS_DIR / "ndrc_glossary.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"术语表文件不存在: {MAPPINGS_DIR / 'ndrc_glossary.json'}")
        data = {}
    except json.JSONDecodeError as e:
        logger.warning(f"术语表JSON解析失败: {e}")
        data = {}

    if cache:
        _cache_glossary = data
    return data


def load_commitment_templates(cache: bool = True) -> dict:
    """加载承诺函模板配置

    Args:
        cache: 是否缓存结果，默认True

    Returns:
        dict: 承诺函模板字典，加载失败返回空dict
    """
    global _cache_commitment_templates
    if cache and _cache_commitment_templates is not None:
        return _cache_commitment_templates

    try:
        filepath = MAPPINGS_DIR / "ndrc_commitment_templates.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"承诺函模板文件不存在: {MAPPINGS_DIR / 'ndrc_commitment_templates.json'}")
        data = {}
    except json.JSONDecodeError as e:
        logger.warning(f"承诺函模板JSON解析失败: {e}")
        data = {}

    if cache:
        _cache_commitment_templates = data
    return data


def load_financial_standards(cache: bool = True) -> dict:
    """加载财务指标标准配置

    Args:
        cache: 是否缓存结果，默认True

    Returns:
        dict: 财务指标标准字典，加载失败返回空dict
    """
    global _cache_financial_standards
    if cache and _cache_financial_standards is not None:
        return _cache_financial_standards

    try:
        filepath = MAPPINGS_DIR / "ndrc_financial_standards.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"财务指标标准文件不存在: {MAPPINGS_DIR / 'ndrc_financial_standards.json'}")
        data = {}
    except json.JSONDecodeError as e:
        logger.warning(f"财务指标标准JSON解析失败: {e}")
        data = {}

    if cache:
        _cache_financial_standards = data
    return data


def load_metadata_config(cache: bool = True) -> dict:
    """加载元数据配置

    Args:
        cache: 是否缓存结果，默认True

    Returns:
        dict: 元数据配置字典，加载失败返回空dict
    """
    global _cache_metadata_config
    if cache and _cache_metadata_config is not None:
        return _cache_metadata_config

    try:
        filepath = MAPPINGS_DIR / "ndrc_metadata_config.json"
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning(f"元数据配置文件不存在: {MAPPINGS_DIR / 'ndrc_metadata_config.json'}")
        data = {}
    except json.JSONDecodeError as e:
        logger.warning(f"元数据配置JSON解析失败: {e}")
        data = {}

    if cache:
        _cache_metadata_config = data
    return data
