"""
REIT-AI 法律文件生成系统 - 章节数据模型
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class ChapterInfo(BaseModel):
    """章节信息摘要"""
    id: int
    chapter_id: str
    title: str
    status: str
    section_count: int = 0


class ChapterData(BaseModel):
    """章节完整数据"""
    chapter_id: str
    title: str
    sections: List[Dict[str, Any]] = []
