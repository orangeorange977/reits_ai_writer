"""登录鉴权：账号密码 + 签名会话 Cookie。

设计（面向 3-5 人小团队，简单可靠）：
- 账号存 accounts.json（项目根）：{"用户名": "sha256(密码)"}，用 set_password.py 增改；
- 是否启用登录：**accounts.json 存在且有账号就启用**，否则整站开放（本地开发免登录）；
- 登录成功下发签名 Cookie（HMAC 签名 + 7 天有效期），后续请求校验；
- 签名密钥存 .app_secret（首次自动生成），服务重启后已登录的会话仍有效。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

_ROOT = Path(__file__).parent.parent
ACCOUNTS_PATH = _ROOT / "accounts.json"
_SECRET_PATH = _ROOT / ".app_secret"
COOKIE_NAME = "reit_session"
_SESSION_DAYS = 7


def _get_secret() -> bytes:
    try:
        if _SECRET_PATH.exists():
            s = _SECRET_PATH.read_text(encoding="utf-8").strip()
            if s:
                return s.encode("utf-8")
    except Exception:
        pass
    s = secrets.token_hex(32)
    try:
        _SECRET_PATH.write_text(s, encoding="utf-8")
    except Exception:
        pass
    return s.encode("utf-8")


_SECRET = _get_secret()


def _hash_pw(password: str) -> str:
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def _load_accounts() -> dict:
    try:
        if ACCOUNTS_PATH.exists():
            d = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def auth_enabled() -> bool:
    """有账号才启用登录；没有 accounts.json（或为空）则整站开放。"""
    return len(_load_accounts()) > 0


def verify_credentials(username: str, password: str) -> bool:
    stored = _load_accounts().get((username or "").strip())
    if not stored:
        return False
    return hmac.compare_digest(str(stored), _hash_pw(password))


def make_token(username: str) -> str:
    exp = str(int(time.time()) + _SESSION_DAYS * 86400)
    msg = f"{username}|{exp}"
    sig = hmac.new(_SECRET, msg.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{msg}|{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def verify_token(token: str):
    """校验 Cookie 里的 token，通过则返回用户名，否则 None。"""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, exp, sig = raw.rsplit("|", 2)
        good = hmac.new(_SECRET, f"{username}|{exp}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig):
            return None
        if int(exp) < int(time.time()):
            return None
        return username
    except Exception:
        return None


def current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    return verify_token(token) if token else None


router = APIRouter(prefix="/api/auth", tags=["登录"])


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


@router.post("/login")
async def login(body: LoginBody, response: Response):
    if not verify_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    token = make_token(body.username.strip())
    # 注意：不设 secure=True，以便在纯 HTTP（http://IP:8000）下 Cookie 也能下发；
    # 将来配了 HTTPS 可再加 secure=True。
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=_SESSION_DAYS * 86400, path="/",
    )
    return {"status": "ok", "username": body.username.strip()}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/me")
async def me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"username": user}
