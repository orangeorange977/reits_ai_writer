"""重置管理员账号为用户指定的 admin / Admin@2026Reit（幂等，可重复执行）。

用途：生产/测试环境把现有管理员账号（users 表 id=1，项目均归属该账号）
的用户名和密码统一重置为指定凭据，并关闭"首次登录强制改密"标记。

用法（在仓库根目录执行）：
    python3 scripts/reset_admin_account.py            # 操作配置指向的数据库
    DB_PATH=/path/to/reits.db python3 scripts/reset_admin_account.py
    # 宿主机无项目依赖时：本地先算好哈希，再传过去直接改库
    PRECOMPUTED_HASH='pbkdf2_sha256$...' DB_PATH=/path/reits.db python3 scripts/reset_admin_account.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET_USERNAME = "admin"
TARGET_PASSWORD = "Admin@2026Reit"


def main():
    pre_hash = os.environ.get("PRECOMPUTED_HASH", "").strip()
    if pre_hash:
        # 宿主机无项目依赖：直接用预计算的哈希改库（纯标准库）
        db_path = os.environ.get("DB_PATH", "").strip()
        if not db_path or not os.path.exists(db_path):
            raise SystemExit("需指定存在的 DB_PATH 环境变量")
        pwd_hash = pre_hash
        print("（使用预计算哈希模式）")
    else:
        from backend.services import auth as auth_service
        from backend.config import DATABASE_PATH

        db_path = os.environ.get("DB_PATH", "").strip() or str(DATABASE_PATH)
        if not os.path.exists(db_path):
            raise SystemExit(f"数据库不存在：{db_path}")
        pwd_hash = auth_service.hash_password(TARGET_PASSWORD)

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, username FROM users ORDER BY id").fetchall()
        if not rows:
            raise SystemExit("users 表为空（尚未初始化），请先启动一次服务再运行本脚本")

        # 检查是否有重名冲突（除 id=1 外已存在 admin）
        conflict = [r for r in rows if r["username"] == TARGET_USERNAME and r["id"] != 1]
        if conflict:
            raise SystemExit(f"存在冲突账号（id={[r['id'] for r in conflict]}），请人工处理")

        conn.execute(
            "UPDATE users SET username = ?, password_hash = ?, must_change_password = 0 WHERE id = 1",
            (TARGET_USERNAME, pwd_hash),
        )
        conn.commit()

        # 回读验证
        check = conn.execute(
            "SELECT username, password_hash, must_change_password FROM users WHERE id = 1"
        ).fetchone()
        assert check["username"] == TARGET_USERNAME
        if not pre_hash:
            assert auth_service.verify_password(TARGET_PASSWORD, check["password_hash"])
        else:
            assert check["password_hash"] == pre_hash
        print(f"✅ 账号已重置：{TARGET_USERNAME} / {TARGET_PASSWORD}"
              f"（must_change_password={check['must_change_password']}）")
        print(f"   数据库：{db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
