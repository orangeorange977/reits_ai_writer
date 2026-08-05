"""登录认证核心（步骤 3.2）：密码哈希、JWT 签发/校验、登录失败锁定。

设计要点：
- 密码哈希：PBKDF2-HMAC-SHA256（标准库 hashlib，无额外依赖），随机盐 + 39 万次迭代
- JWT：HS256，密钥取 config.JWT_SECRET；未配置时生成进程内随机密钥并警告（仅限本地开发）
- 登录失败锁定：内存计数，连续失败 5 次锁定 15 分钟（按《修改步骤.md》3.2 约定）
"""
import hashlib
import hmac
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

from backend.config import JWT_SECRET, TOKEN_TTL_HOURS

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
PBKDF2_ITERATIONS = 390000
MAX_LOGIN_FAILS = 5
LOCK_SECONDS = 15 * 60

# 登录失败记录：{username: [失败次数, 锁定截止时间戳]}（内存即可，重启清零属可接受范围）
_login_fails: dict = {}

_fallback_secret: str = ""


def _jwt_secret() -> str:
    """签名密钥：环境变量优先；未配置时进程内随机生成（重启后旧 token 失效）。"""
    global _fallback_secret
    if JWT_SECRET:
        return JWT_SECRET
    if not _fallback_secret:
        _fallback_secret = secrets.token_hex(32)
        logger.warning("JWT_SECRET 未配置，已生成进程内随机密钥；重启后所有登录失效。生产部署必须配置 JWT_SECRET！")
    return _fallback_secret


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 哈希，格式：pbkdf2_sha256$迭代数$盐hex$摘要hex"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较，格式错误/算法不符一律 False。"""
    try:
        algo, iters, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def password_strength_ok(password: str) -> bool:
    """新密码强度：长度 >= 8，且大写/小写/数字/符号至少含三类。"""
    if len(password) < 8:
        return False
    kinds = sum([
        any(c.isupper() for c in password),
        any(c.islower() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    return kinds >= 3


def issue_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str):
    """校验成功返回 payload dict，失败（过期/伪造/格式错）返回 None。"""
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ===== 登录失败锁定 =====

def locked_seconds(username: str) -> int:
    """返回剩余锁定秒数；0 表示未锁定。"""
    rec = _login_fails.get(username)
    if not rec:
        return 0
    remaining = rec[1] - time.time()
    return int(remaining) if remaining > 0 else 0


def record_login_fail(username: str) -> int:
    """记录一次失败；达到阈值则上锁。返回剩余锁定秒数（0=未锁定）。"""
    rec = _login_fails.get(username)
    if rec is None:
        rec = [0, 0]
    elif rec[1] > 0 and rec[1] < time.time():
        rec = [0, 0]  # 上一轮锁定已到期，重新计数
    rec[0] += 1
    if rec[0] >= MAX_LOGIN_FAILS:
        rec[1] = time.time() + LOCK_SECONDS
        logger.warning(f"用户 {username} 连续登录失败 {rec[0]} 次，锁定 {LOCK_SECONDS // 60} 分钟")
    _login_fails[username] = rec
    return locked_seconds(username)


def clear_login_fails(username: str):
    _login_fails.pop(username, None)


def random_strong_password(length: int = 16) -> str:
    """生成随机强密码（四类字符齐全，剔除易混淆字符），用于未配置初始密码时兜底。"""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
    digits = "23456789"
    symbols = "!@#$%^&*-_=+"
    alphabet = letters + digits + symbols
    while True:
        pwd = [secrets.choice(letters.upper()[:23]), secrets.choice(letters[23:]),
               secrets.choice(digits), secrets.choice(symbols)]
        pwd += [secrets.choice(alphabet) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(pwd)
        return "".join(pwd)
