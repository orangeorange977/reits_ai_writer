"""API路由模块

提供项目管理、模板包、技能（章节生成）和文件夹浏览的RESTful API路由。
"""

from .projects import router as projects_router
from .folders import router as folders_router

__all__ = [
    'projects_router',
    'folders_router',
]
