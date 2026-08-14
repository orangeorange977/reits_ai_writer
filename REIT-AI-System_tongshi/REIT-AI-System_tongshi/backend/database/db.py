"""
REIT-AI 法律文件生成系统 - 数据库初始化与操作
"""
import json
import logging
import aiosqlite
from pathlib import Path
from backend.config import DATABASE_PATH

logger = logging.getLogger(__name__)

# 预置项目数据目录
PRESET_PROJECTS_DIR = Path(__file__).parent.parent / "data" / "projects"

# 预置项目的 data_source_path 前缀标识
PRESET_PROJECT_PREFIX = "__preset__:"


async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    return db


async def init_database():
    """初始化数据库，创建表结构"""
    # 确保数据库目录存在
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        # 创建projects表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                data_source_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)

        # 创建chapters表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                chapter_id TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id)
            )
        """)

        # 创建project_metadata表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                meta_type TEXT NOT NULL,
                meta_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, meta_type)
            )
        """)

        await db.commit()
        print("  数据库初始化完成")


async def get_project_metadata(project_id: int, meta_type: str) -> dict | None:
    """获取项目元数据

    Args:
        project_id: 项目ID
        meta_type: 元数据类型 ('glossary'|'financial'|'inapplicable'|'query_point'|'attachment_ref')

    Returns:
        dict: 元数据字典，未找到返回None
    """
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT meta_data FROM project_metadata WHERE project_id = ? AND meta_type = ?",
            (project_id, meta_type)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["meta_data"]) if row["meta_data"] else None
        except (json.JSONDecodeError, TypeError):
            return None


async def save_project_metadata(project_id: int, meta_type: str, meta_data: dict) -> None:
    """保存/更新项目元数据（UPSERT逻辑）

    Args:
        project_id: 项目ID
        meta_type: 元数据类型 ('glossary'|'financial'|'inapplicable'|'query_point'|'attachment_ref')
        meta_data: 要保存的元数据字典
    """
    data_json = json.dumps(meta_data, ensure_ascii=False)
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        await db.execute(
            """INSERT INTO project_metadata (project_id, meta_type, meta_data)
               VALUES (?, ?, ?)
               ON CONFLICT(project_id, meta_type)
               DO UPDATE SET meta_data = excluded.meta_data, updated_at = CURRENT_TIMESTAMP""",
            (project_id, meta_type, data_json)
        )
        await db.commit()


async def delete_project_metadata(project_id: int, meta_type: str) -> None:
    """删除项目元数据

    Args:
        project_id: 项目ID
        meta_type: 元数据类型 ('glossary'|'financial'|'inapplicable'|'query_point'|'attachment_ref')
    """
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        await db.execute(
            "DELETE FROM project_metadata WHERE project_id = ? AND meta_type = ?",
            (project_id, meta_type)
        )
        await db.commit()


def is_preset_project(data_source_path: str) -> bool:
    """判断是否为预置示范项目"""
    return data_source_path.startswith(PRESET_PROJECT_PREFIX)


async def load_preset_projects():
    """加载预置示范项目数据到数据库（启动时调用，幂等操作）

    从 data/projects/ 目录读取JSON文件，将尚未导入的预置项目插入数据库。
    通过 data_source_path 的特殊前缀(__preset__:)标识预置项目并防止重复插入。
    """
    if not PRESET_PROJECTS_DIR.exists():
        return

    json_files = list(PRESET_PROJECTS_DIR.glob("*.json"))
    if not json_files:
        return

    # 延迟导入避免循环依赖
    from backend.mappings import load_ndrc_chapter_mapping

    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        for json_file in json_files:
            preset_key = json_file.stem  # 如 'wanguo_idc_reit'
            marker_path = f"{PRESET_PROJECT_PREFIX}{preset_key}"

            # 检查是否已导入
            cursor = await db.execute(
                "SELECT id FROM projects WHERE data_source_path = ?",
                (marker_path,)
            )
            if await cursor.fetchone():
                continue  # 已存在，跳过

            # 读取预置数据
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    preset_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"加载预置项目文件失败 {json_file}: {e}")
                continue

            project_name = preset_data.get("project_name", json_file.stem)
            created_at = preset_data.get("created_at", None)

            # 插入项目记录
            cursor = await db.execute(
                "INSERT INTO projects (name, data_source_path, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_name, marker_path, "generated", created_at, created_at)
            )
            project_id = cursor.lastrowid

            # 加载章节映射配置
            mapping = load_ndrc_chapter_mapping()
            chapters_config = {ch["id"]: ch for ch in mapping.get("chapters", [])}

            # 导入各章节数据
            chapters_data = preset_data.get("chapters", {})
            for chapter_id, chapter_content in chapters_data.items():
                chapter_conf = chapters_config.get(chapter_id)
                if not chapter_conf:
                    continue

                # 将预置数据格式转换为数据库存储格式
                data_json = _transform_preset_chapter_data(
                    chapter_conf, chapter_content
                )

                await db.execute(
                    "INSERT INTO chapters (project_id, chapter_id, title, status, data_json) VALUES (?, ?, ?, ?, ?)",
                    (project_id, chapter_id, chapter_conf["title"], "extracted",
                     json.dumps(data_json, ensure_ascii=False))
                )

            await db.commit()
            logger.info(f"预置示范项目已导入: {project_name} (ID={project_id})")
            print(f"  预置示范项目已导入: {project_name}")


def _transform_preset_chapter_data(chapter_conf: dict, chapter_content: dict) -> dict:
    """将预置JSON的章节数据转换为数据库存储格式

    预置格式: {"sections": {"1-1": {"fields": {...}}, "1-2": {...}}}
    数据库格式: {"sections": [{"section_id": "1-1", "title": "...", "fields": [...]}]}
    """
    preset_sections = chapter_content.get("sections", {})
    result_sections = []

    for section_conf in chapter_conf.get("sections", []):
        section_id = section_conf["id"]
        preset_section = preset_sections.get(section_id, {})

        if section_conf.get("has_subsections") and section_conf.get("subsections"):
            # 有子模块的section
            section_data = {
                "section_id": section_id,
                "title": section_conf["title"],
                "has_subsections": True,
                "subsections": [],
            }
            preset_subsections = preset_section.get("subsections", {})
            for sub_conf in section_conf["subsections"]:
                sub_id = sub_conf["id"]
                preset_sub = preset_subsections.get(sub_id, {})
                preset_fields = preset_sub.get("fields", {})

                sub_fields = []
                for field_def in sub_conf.get("fields", []):
                    field_id = field_def["id"]
                    value = preset_fields.get(field_id)
                    sub_fields.append({
                        "id": field_id,
                        "label": field_def["label"],
                        "type": field_def.get("type", "text"),
                        "value": value,
                        "source": "预置示范数据" if value is not None else "",
                        "confidence": 1.0 if value is not None else 0.0,
                    })
                section_data["subsections"].append({
                    "id": sub_id,
                    "title": sub_conf["title"],
                    "fields": sub_fields,
                })
            result_sections.append(section_data)
        else:
            # 普通section
            preset_fields = preset_section.get("fields", {})
            fields = []
            for field_def in section_conf.get("fields", []):
                field_id = field_def["id"]
                value = preset_fields.get(field_id)
                fields.append({
                    "id": field_id,
                    "label": field_def["label"],
                    "type": field_def.get("type", "text"),
                    "value": value,
                    "source": "预置示范数据" if value is not None else "",
                    "confidence": 1.0 if value is not None else 0.0,
                })
            result_sections.append({
                "section_id": section_id,
                "title": section_conf["title"],
                "fields": fields,
            })

    return {"sections": result_sections}
