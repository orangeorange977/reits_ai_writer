"""
REIT-AI 法律文件生成系统 - FastAPI应用入口
"""
import os
import sys
import time
from collections import deque
from pathlib import Path

# 确保 app/ 目录在 sys.path 中，使 backend 作为包可被导入（支持子模块的相对导入）
_APP_DIR = str(Path(__file__).parent.parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# 业务日志开到 INFO：工具调用/生成过程等核实线索默认可见（默认 WARNING 会吞掉）
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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
from fastapi.responses import FileResponse, JSONResponse

from backend.config import APP_HOST, APP_PORT, CORS_ORIGINS, AI_RATE_LIMIT_PER_MINUTE
from backend.database.db import init_database, load_preset_projects, ensure_admin_user
from backend.routers import projects_router, folders_router
from backend.routers.auth import router as auth_router
from backend.routers.packs import router as packs_router
from backend.routers.skills import router as skills_router
from backend.routers.evaluation import router as eval_router
from backend.services.auth import decode_token

# 创建FastAPI实例
app = FastAPI(
    title="REIT-AI 法律文件生成系统",
    description="申报材料智能生成平台（引擎通用，具体材料类型由模板包定义）",
    version="1.0.0"
)

# 配置CORS中间件（步骤 3.6：收紧）
# 同源部署（前端由本服务同端口托管）时浏览器不发跨域请求，默认不挂 CORS；
# 只有显式配置 CORS_ORIGINS 白名单时才挂载，彻底去掉 allow_origins=["*"]。
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


# 前端静态文件禁用浏览器缓存（本地开发环境）：
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


# ===== AI 接口每用户限流（步骤 3.6：防 Kimi key 被刷）=====
# 只限高成本 AI 入口：章节生成 run、AI 辅助写作两个接口；内存滑动窗口，
# 单 worker 部署下够用；必须在 auth 中间件内层才能读到 request.state.user。
_ai_calls: dict = {}   # user_id -> deque[时间戳]


def _is_ai_burst(path: str, method: str) -> bool:
    if method != "POST":
        return False
    if path in ("/api/skills/ai-edit", "/api/skills/ai-compose"):
        return True
    if path.startswith("/api/eval/score/"):
        return True
    return path.startswith("/api/skills/chapter/") and path.endswith("/run")


@app.middleware("http")
async def ai_rate_limit_middleware(request, call_next):
    if AI_RATE_LIMIT_PER_MINUTE > 0 and _is_ai_burst(request.url.path, request.method):
        user = getattr(request.state, "user", None) or {}
        uid = user.get("sub", "anon")
        now = time.time()
        window = _ai_calls.setdefault(uid, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= AI_RATE_LIMIT_PER_MINUTE:
            return JSONResponse(
                {"detail": f"AI 调用过于频繁，请稍后再试（限 {AI_RATE_LIMIT_PER_MINUTE} 次/分钟）"},
                status_code=429,
            )
        window.append(now)
    return await call_next(request)


# ===== 全局登录鉴权（步骤 3.2）=====
# 除公开路径外，所有 /api/* 与接口文档一律要求有效 JWT，中间件统一拦截，无一遗漏。
_PUBLIC_PATHS = ("/api/auth/login", "/api/health")
_PROTECTED_DOC_PREFIXES = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def auth_middleware(request, call_next):
    path = request.url.path
    need_auth = (
        (path.startswith("/api/") and path not in _PUBLIC_PATHS)
        or path.startswith(_PROTECTED_DOC_PREFIXES)
    )
    if need_auth and request.method != "OPTIONS":
        header = request.headers.get("authorization", "")
        token = header[len("Bearer "):].strip() if header.startswith("Bearer ") else ""
        payload = decode_token(token) if token else None
        if not payload:
            return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
        request.state.user = payload
    return await call_next(request)


# 前端静态文件目录
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


# ===== API路由 =====

# 注册路由模块（必须在静态文件挂载和通配符路由之前）
app.include_router(auth_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(folders_router, prefix="/api")
app.include_router(packs_router, prefix="/api")
app.include_router(skills_router, prefix="/api")
app.include_router(eval_router, prefix="/api")


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
    # 首次启动创建初始管理员账号（步骤 3.2）
    await ensure_admin_user()
    # 加载预置示范项目
    await load_preset_projects()


# ===== 静态文件挂载 =====
# 挂载前端静态文件（必须放在所有API路由之后）
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")


@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    """服务前端页面，所有非API请求返回index.html"""
    # /api 及 /api/* 下的未知路径直接 404，避免被 SPA 兜底成 HTML（也防止已删除的接口残留为 200）
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
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
