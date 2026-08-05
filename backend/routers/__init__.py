"""API路由模块

提供项目管理、章节管理、文档生成和文件夹浏览的RESTful API路由。
"""

from .projects import router as projects_router
from .chapters import router as chapters_router
from .folders import router as folders_router
from .generate import router as generate_router

__all__ = [
    'projects_router',
    'chapters_router',
    'folders_router',
    'generate_router',
]
