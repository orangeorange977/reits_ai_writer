"""增改登录账号（供服务器上管理同事账号用）。

用法：
    python set_password.py 用户名 密码        # 新增或修改一个账号
    python set_password.py --del 用户名        # 删除一个账号
    python set_password.py --list             # 列出所有账号

账号存到同目录的 accounts.json（只存密码的 sha256，不存明文）。
一旦 accounts.json 里有账号，网站就会要求登录；删空则恢复免登录。
"""
import hashlib
import json
import sys
from pathlib import Path

ACC = Path(__file__).parent / "accounts.json"


def _load():
    if ACC.exists():
        try:
            d = json.loads(ACC.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def _save(d):
    ACC.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == "--list":
        d = _load()
        if not d:
            print("（暂无账号，网站当前免登录）")
        else:
            print("已有账号：")
            for u in d:
                print("  -", u)
        return
    if args[0] == "--del":
        if len(args) < 2:
            print("用法：python set_password.py --del 用户名")
            return
        d = _load()
        if d.pop(args[1], None) is not None:
            _save(d)
            print(f"已删除账号：{args[1]}")
        else:
            print(f"没有这个账号：{args[1]}")
        return
    if len(args) < 2:
        print("用法：python set_password.py 用户名 密码")
        return
    user, pw = args[0], args[1]
    d = _load()
    d[user] = hashlib.sha256(pw.encode("utf-8")).hexdigest()
    _save(d)
    print(f"已设置账号 {user}（共 {len(d)} 个账号，网站现在需要登录）")


if __name__ == "__main__":
    main()
