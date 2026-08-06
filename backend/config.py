"""
REIT-AI 法律文件生成系统 - 配置文件
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ===== Kimi(Moonshot) 大模型接入配置 =====
# API Key 从环境变量/.env读取，不要硬编码在代码里
MOONSHOT_API_KEY = os.environ.get("MOONSHOT_API_KEY", "")
MOONSHOT_BASE_URL = os.environ.get("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
MOONSHOT_MODEL = os.environ.get("MOONSHOT_MODEL", "kimi-k3")
# 视觉识别（识别扫描件/图片文字）用的模型。默认空=直接用主模型（kimi-k3 本身支持读图）；
# 如需指定专门的视觉模型，用环境变量 MOONSHOT_VISION_MODEL 覆盖。
# 注意：DeepSeek 不支持读图，视觉识别始终走 Moonshot。
MOONSHOT_VISION_MODEL = os.environ.get("MOONSHOT_VISION_MODEL", "")

# ===== DeepSeek 大模型接入配置 =====
# 同样兼容 OpenAI 接口；模型名以 deepseek 开头，客户端据此自动路由到 DeepSeek。
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ===== 天眼查 MCP（企业数据）接入 =====
# 密钥从 .env 读；服务地址为天眼查官方 MCP 端点（Streamable HTTP / JSON-RPC 2.0）
TIANYANCHA_MCP_KEY = os.environ.get("TIANYANCHA_MCP_KEY", "")
TIANYANCHA_MCP_URL = os.environ.get("TIANYANCHA_MCP_URL", "https://mcp.tianyancha.com/v1")

# ===== 模板包（templates-packs）配置 =====
# 引擎不认识具体业务：章节结构、写作要求、官方模板、渲染脚本全部在包内。
# 默认在仓库根目录的 templates-packs/，可用环境变量 PACKS_DIR 覆盖。
PACKS_DIR = Path(os.environ.get(
    "PACKS_DIR",
    str(Path(__file__).resolve().parents[1] / "templates-packs"),
))

# 工作空间根目录（数据库、输出等运行期文件的基准）。
# 默认放在网站目录内的 workspace/，随文件夹一起移动、自动创建，不再写到 C 盘固定路径。
# 需要指向别处时可用环境变量 DATA_SOURCE_BASE 覆盖。
DATA_SOURCE_BASE = Path(os.environ.get(
    "DATA_SOURCE_BASE",
    str(Path(__file__).resolve().parents[1] / "workspace"),
))

# 应用目录
APP_DIR = DATA_SOURCE_BASE / "app"

# 项目数据根目录（workspace/projects/<项目ID>/）：摘要表、各章 JSON、生成产物按项目隔离。
PROJECTS_DIR = DATA_SOURCE_BASE / "projects"
DEFAULT_PROJECT_ID = "default"


def safe_project_id(project_id) -> str:
    """净化外部传入的项目 ID：取文件名部分防目录穿越，
    空值或 '.'/'..' 等特殊值落回默认项目。"""
    pid = str(project_id or "").strip()
    safe = Path(pid).name if pid else ""
    if safe in ("", ".", ".."):
        return DEFAULT_PROJECT_ID
    return safe


def _env_int(name: str, default: int) -> int:
    """读整数环境变量；非法值回退默认并告警（避免配错直接崩进程，步骤 3.6 复查补强）。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[config] 警告：环境变量 {name}={raw!r} 不是合法整数，回退默认值 {default}")
        return default

# 输出目录
OUTPUT_DIR = APP_DIR / "output"

# 数据库路径
DATABASE_PATH = APP_DIR / "backend" / "database" / "reits.db"

# 服务配置（步骤 3.6：环境化）
# 本地体验默认 127.0.0.1:8000；服务器部署时设 APP_HOST=0.0.0.0（由 Nginx 反代对外）
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
APP_PORT = _env_int("APP_PORT", 8000)

# CORS 白名单（步骤 3.6）：逗号分隔的允许来源，如 "https://reit.example.com"。
# 留空=不挂 CORS 中间件（前后端同源部署时浏览器不发跨域请求，最安全）。
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

# AI 接口限流（步骤 3.6）：每用户每分钟最多调用次数（防 Kimi key 被刷），0=不限
AI_RATE_LIMIT_PER_MINUTE = _env_int("AI_RATE_LIMIT_PER_MINUTE", 10)

# 模板目录
TEMPLATES_DIR = APP_DIR / "backend" / "templates"

# 映射文件目录
MAPPINGS_DIR = APP_DIR / "backend" / "mappings"

# 官方格式模板配置
OFFICIAL_TEMPLATE_DIR = APP_DIR / "backend" / "templates" / "official"
NDRC_OFFICIAL_TEMPLATE = OFFICIAL_TEMPLATE_DIR / "ndrc_2024.docx"
USE_OFFICIAL_TEMPLATE = True  # 开关：True使用官方模板生成，False使用Jinja2管线

# 增强功能总开关
ENABLE_ENHANCEMENTS = True

# ===== 登录认证（步骤 3.2）=====
# JWT 签名密钥：生产部署必须显式设置（部署时生成随机值）；
# 未设置时后端会生成进程内随机密钥（重启后旧 token 失效，仅限本地开发）。
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
# 初始管理员密码：首次启动创建 admin 账号用；未设置时自动生成随机强密码并打印到控制台。
ADMIN_INIT_PASSWORD = os.environ.get("ADMIN_INIT_PASSWORD", "").strip()
# token 有效期（小时）
TOKEN_TTL_HOURS = _env_int("TOKEN_TTL_HOURS", 12)
