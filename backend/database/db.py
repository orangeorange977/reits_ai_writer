"""
REIT-AI 法律文件生成系统 - 数据库初始化与操作
"""
from __future__ import annotations
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
                status TEXT DEFAULT 'active',
                pack_id TEXT DEFAULT NULL
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

        # 创建users表（步骤 3.2 登录认证）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建generation_jobs表（步骤 3.5：生成任务状态入 DB，多 worker/重启后可见）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS generation_jobs (
                project_id TEXT NOT NULL,
                chapter_n INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle',
                data_json TEXT,
                error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, chapter_n)
            )
        """)

        # 旧库升级：projects 表补 pack_id 列（项目绑定的模板包），幂等
        try:
            await db.execute("ALTER TABLE projects ADD COLUMN pack_id TEXT DEFAULT NULL")
        except Exception:
            pass  # 列已存在

        # 旧库升级：projects 表补 user_id 列（步骤 3.5 用户隔离），幂等
        try:
            await db.execute("ALTER TABLE projects ADD COLUMN user_id INTEGER DEFAULT 1")
        except Exception:
            pass  # 列已存在

        # 存量项目（含预置示范项目）归属 admin（id=1）
        try:
            await db.execute(
                "UPDATE projects SET user_id = 1 WHERE user_id IS NULL")
        except Exception as e:
            logger.warning(f"存量项目归属回填失败（不阻断启动）: {e}")

        # 存量项目（含预置示范项目）尚未绑包时，绑到默认模板包
        try:
            from backend.services import pack_service
            default_pack = pack_service.default_pack_id()
            await db.execute(
                "UPDATE projects SET pack_id = ? WHERE pack_id IS NULL OR pack_id = ''",
                (default_pack,)
            )
        except Exception as e:
            logger.warning(f"存量项目绑定默认模板包失败（不阻断启动）: {e}")

        await db.commit()
        print("  数据库初始化完成")


async def ensure_admin_user():
    """首次启动创建初始管理员账号（步骤 3.2）：
    密码取环境变量 ADMIN_INIT_PASSWORD；未配置时自动生成随机强密码并打印到控制台；
    初始账号标记 must_change_password=1，首次登录强制改密。"""
    from backend.services import auth as auth_service
    from backend.config import ADMIN_INIT_PASSWORD

    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        db.row_factory = aiosqlite.Row
        row = await db.execute_fetchall("SELECT id FROM users LIMIT 1")
        if row:
            return  # 已有任何用户，不重复创建
        if ADMIN_INIT_PASSWORD:
            password = ADMIN_INIT_PASSWORD
        else:
            password = auth_service.random_strong_password()
            print("  ⚠️  未配置 ADMIN_INIT_PASSWORD，已为 admin 生成随机初始密码：")
            print(f"      {password}")
            print("      请立即保存，首次登录后会被强制修改。")
        await db.execute(
            "INSERT INTO users (username, password_hash, role, must_change_password) VALUES (?, ?, 'admin', 1)",
            ("admin", auth_service.hash_password(password)),
        )
        await db.commit()
        print("  初始管理员账号 admin 已创建")


async def get_project_pack_id(project_id) -> str | None:
    """查项目绑定的模板包 ID；项目不存在/未绑包/ID非法时返回 None（用默认包）。"""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return None
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        cursor = await db.execute(
            "SELECT pack_id FROM projects WHERE id = ?", (pid,))
        row = await cursor.fetchone()
    if not row or not row[0]:
        return None
    return row[0]


async def get_project_owner_id(project_id) -> int | None:
    """查项目归属的用户 ID；项目不存在/ID非法时返回 None（步骤 3.5）。"""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return None
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        cursor = await db.execute(
            "SELECT user_id FROM projects WHERE id = ?", (pid,))
        row = await cursor.fetchone()
    if not row or row[0] is None:
        return None
    return int(row[0])


async def touch_project_updated_at(project_id) -> None:
    """项目内容发生变化（章节/摘要保存、生成完成）时刷新 updated_at，
    项目列表的“更新时间”以此为准。失败不阻断主流程。"""
    try:
        pid = int(project_id)
    except (TypeError, ValueError):
        return
    try:
        async with aiosqlite.connect(str(DATABASE_PATH)) as db:
            await db.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (pid,))
            await db.commit()
    except Exception:
        pass


async def upsert_generation_job(project_id: str, chapter_n: int, status: str,
                                data_json: str = None, error: str = None) -> None:
    """写入/更新生成任务状态（步骤 3.5：状态入 DB，重启/多 worker 后可见）。"""
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        await db.execute(
            """INSERT INTO generation_jobs (project_id, chapter_n, status, data_json, error, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id, chapter_n)
               DO UPDATE SET status = excluded.status,
                             data_json = excluded.data_json,
                             error = excluded.error,
                             updated_at = CURRENT_TIMESTAMP""",
            (str(project_id), chapter_n, status, data_json, error)
        )
        await db.commit()


async def get_generation_job(project_id: str, chapter_n: int) -> dict | None:
    """读取生成任务状态；无记录返回 None。"""
    async with aiosqlite.connect(str(DATABASE_PATH)) as db:
        cursor = await db.execute(
            "SELECT status, data_json, error FROM generation_jobs WHERE project_id = ? AND chapter_n = ?",
            (str(project_id), chapter_n)
        )
        row = await cursor.fetchone()
    if not row:
        return None
    data = None
    if row[1]:
        try:
            data = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            data = None
    return {"status": row[0], "data": data, "error": row[2]}


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
