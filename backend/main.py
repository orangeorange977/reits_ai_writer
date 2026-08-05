"""
REIT-AI 法律文件生成系统 - FastAPI应用入口
"""
import os
import sys
from pathlib import Path

# 确保 app/ 目录在 sys.path 中，使 backend 作为包可被导入（支持子模块的相对导入）
_APP_DIR = str(Path(__file__).parent.parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# 确保 fastapi._compat模块可用（处理版本混乱问题）
try:
    import site
    from pathlib import Path as _P
    _user_site = _P(site.getusersitepackages())
    _compat_dir = _user_site / 'fastapi' / '_compat'
    if _compat_dir.is_dir():
        import shutil
        shutil.rmtree(str(_compat_dir), ignore_errors=True)
except Exception:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import APP_HOST, APP_PORT
from backend.database.db import init_database, load_preset_projects
from backend.routers import projects_router, folders_router
from backend.routers.enhancements import router as enhancements_router
from backend.routers.packs import router as packs_router
from backend.routers.skills import router as skills_router

# 创建FastAPI实例
app = FastAPI(
    title="REIT-AI 法律文件生成系统",
    description="申报材料智能生成平台（引擎通用，具体材料类型由模板包定义）",
    version="1.0.0"
)

# 配置CORS中间件（本地开发用，允许所有来源）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 静态资源禁用浏览器缓存（本地开发环境）：
# 否则每次改前端 JS/CSS/HTML 后，浏览器可能仍用旧缓存，导致新逻辑不生效。
@app.middleware("http")
async def _no_cache_static(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# 前端静态文件目录
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ===== API路由 =====

# 注册路由模块（必须在静态文件挂载和通配符路由之前）
app.include_router(projects_router, prefix="/api")
app.include_router(folders_router, prefix="/api")
app.include_router(enhancements_router, prefix="/api")
app.include_router(packs_router, prefix="/api")
app.include_router(skills_router, prefix="/api")


@app.get("/api/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "service": "REIT-AI 法律文件生成系统",
        "version": "1.0.0"
    }


@app.get("/api/info")
async def system_info():
    """系统信息"""
    return {
        "name": "REIT-AI 法律文件生成系统",
        "version": "1.0.0",
        "python_version": sys.version,
        "platform": sys.platform
    }


# ===== 启动事件 =====

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    print("=" * 50)
    print("  REIT-AI 法律文件生成系统")
    print("=" * 50)
    print(f"  服务地址: http://{APP_HOST}:{APP_PORT}")
    print(f"  API文档: http://{APP_HOST}:{APP_PORT}/docs")
    print("=" * 50)
    # 初始化数据库
    await init_database()
    # 加载预置示范项目
    await load_preset_projects()


# ===== 静态文件挂载 =====
# 挂载前端静态文件（必须放在所有API路由之后）
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """服务前端页面，所有非API请求返回index.html"""
    file_path = FRONTEND_DIR / full_path
    if full_path and file_path.exists() and file_path.is_file():
        return FileResponse(str(file_path))
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ===== 主入口 =====

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=True
    )
