"""
REIT-AI 法律文件生成系统 - 项目数据模型
"""
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class ProjectCreate(BaseModel):
    """创建项目请求模型"""
    name: str
    data_source_path: str


class ProjectResponse(BaseModel):
    """项目响应模型"""
    id: int
    name: str
    data_source_path: str
    status: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
