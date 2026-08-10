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
MOONSHOT_VISION_MODEL = os.environ.get("MOONSHOT_VISION_MODEL", "")

# ===== 天眼查 MCP（企业数据）接入 =====
# 密钥从 .env 读；服务地址为天眼查官方 MCP 端点（Streamable HTTP / JSON-RPC 2.0）
TIANYANCHA_MCP_KEY = os.environ.get("TIANYANCHA_MCP_KEY", "")
TIANYANCHA_MCP_URL = os.environ.get("TIANYANCHA_MCP_URL", "https://mcp.tianyancha.com/v1")

# ===== Skill 部署配置 =====
# skills 目录（reits-reading-ch1~ch7、reits-writing、reits-diagrams、planning.md 所在处）
# 默认按打包结构自动定位：<AI test>/skills 与 <AI test>/REIT-AI-System/REIT-AI-System 同级，
# 这样整个 "AI test" 文件夹拷到任何电脑/盘符都能找到，不用改代码。也可用环境变量 SKILLS_DIR 覆盖。
# config.py 路径：<AI test>/REIT-AI-System/REIT-AI-System/backend/config.py → parents[3] = <AI test>
_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[3] / "skills"
SKILLS_DIR = Path(os.environ.get("SKILLS_DIR", str(_DEFAULT_SKILLS_DIR)))
PLANNING_MD_PATH = SKILLS_DIR / "planning.md"

# 工作空间根目录（数据库、输出等运行期文件的基准）。
# 默认放在网站目录内的 workspace/，随文件夹一起移动、自动创建，不再写到 C 盘固定路径。
# 需要指向别处时可用环境变量 DATA_SOURCE_BASE 覆盖。
DATA_SOURCE_BASE = Path(os.environ.get(
    "DATA_SOURCE_BASE",
    str(Path(__file__).resolve().parents[1] / "workspace"),
))

# 发改委材料路径
NDRC_MATERIALS_PATH = DATA_SOURCE_BASE / "1.发改委阶段"

# 证监会阶段材料路径
CSRC_MATERIALS_PATH = DATA_SOURCE_BASE / "2.证监会阶段【启用】"

# 应用目录
APP_DIR = DATA_SOURCE_BASE / "app"

# 输出目录
OUTPUT_DIR = APP_DIR / "output"

# 数据库路径
DATABASE_PATH = APP_DIR / "backend" / "database" / "reits.db"

# 服务配置
APP_HOST = "127.0.0.1"
APP_PORT = 8000

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
