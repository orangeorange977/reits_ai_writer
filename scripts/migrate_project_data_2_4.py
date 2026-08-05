"""一次性迁移脚本（修改步骤 2.4）：把过渡期 default 项目目录的数据
归入数据库里每个已有项目的目录（workspace/projects/<项目ID>/）。

背景：步骤 2.2 时章节 JSON/摘要表先落在 workspace/projects/default/；
步骤 2.4 引入 project_id 维度后，前端会按真实项目 ID 请求，
数据应落在 workspace/projects/<项目ID>/ 下。

用法：python3 scripts/migrate_project_data_2_4.py
幂等：目标已有同名文件不覆盖，可重复执行；default 目录保留作兜底。
"""
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "workspace" / "app" / "backend" / "database" / "reits.db"
PROJECTS = ROOT / "workspace" / "projects"
DEFAULT_DIR = PROJECTS / "default"


def main():
    if not DEFAULT_DIR.exists():
        print("default 目录不存在，无需迁移。")
        return
    if not DB.exists():
        print(f"未找到数据库：{DB}")
        sys.exit(1)

    conn = sqlite3.connect(DB)
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    finally:
        conn.close()

    if not ids:
        print("数据库中还没有项目，数据继续留在 default 目录（未选项目时的兜底）。")
        return

    files = [p for p in DEFAULT_DIR.rglob("*") if p.is_file()]
    if not files:
        print("default 目录为空，无需迁移。")
        return

    for pid in ids:
        dest_dir = PROJECTS / str(pid)
        for src in files:
            rel = src.relative_to(DEFAULT_DIR)
            dest = dest_dir / rel
            if dest.exists():
                print(f"  跳过（已存在）: projects/{pid}/{rel}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            print(f"  迁移: default/{rel} -> projects/{pid}/")

    print(f"迁移完成：{len(files)} 个文件分发到项目 {ids}；default 目录保留作兜底。")


if __name__ == "__main__":
    main()
