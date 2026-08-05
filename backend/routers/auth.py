"""登录认证路由（步骤 3.2）：登录签发 token、修改密码、当前用户信息。

本路由的 /login 是全站唯一的公开 API（连同 /api/health），
其余所有 /api/* 由 main.py 的鉴权中间件统一拦截，无一遗漏。
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.database.db import get_db
from backend.services import auth as auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/login")
async def login(body: LoginRequest):
    """账号密码登录，成功签发 JWT。连续失败 5 次锁定 15 分钟。"""
    username = (body.username or "").strip()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="请输入用户名和密码")

    lock = auth_service.locked_seconds(username)
    if lock > 0:
        raise HTTPException(status_code=423, detail=f"失败次数过多，请 {lock // 60 + 1} 分钟后再试")

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, username, password_hash, role, must_change_password FROM users WHERE username = ?",
            (username,),
        )
        if not rows or not auth_service.verify_password(body.password, rows[0]["password_hash"]):
            lock = auth_service.record_login_fail(username)
            detail = "用户名或密码错误"
            if lock > 0:
                detail = f"失败次数过多，账号已锁定 {lock // 60 + 1} 分钟"
            raise HTTPException(status_code=401, detail=detail)

        user = rows[0]
        auth_service.clear_login_fails(username)
        token = auth_service.issue_token(user["id"], user["username"], user["role"])
        return {
            "token": token,
            "username": user["username"],
            "role": user["role"],
            "must_change_password": bool(user["must_change_password"]),
        }
    finally:
        await db.close()


@router.get("/me")
async def me(request: Request):
    """当前登录用户信息（token 由鉴权中间件校验，payload 放在 request.state.user）。"""
    payload = getattr(request.state, "user", None)
    if not payload:
        raise HTTPException(status_code=401, detail="未登录")
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT username, role, must_change_password FROM users WHERE id = ?",
            (int(payload.get("sub", 0)),),
        )
        if not rows:
            raise HTTPException(status_code=401, detail="用户不存在")
        return {
            "username": rows[0]["username"],
            "role": rows[0]["role"],
            "must_change_password": bool(rows[0]["must_change_password"]),
        }
    finally:
        await db.close()


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request):
    """修改当前用户密码（首次登录强制改密也走这里）。"""
    payload = getattr(request.state, "user", None)
    if not payload:
        raise HTTPException(status_code=401, detail="未登录")

    if not auth_service.password_strength_ok(body.new_password):
        raise HTTPException(
            status_code=400,
            detail="新密码太弱：至少 8 位，且大写/小写/数字/符号至少包含三类",
        )

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, password_hash FROM users WHERE id = ?",
            (int(payload.get("sub", 0)),),
        )
        if not rows:
            raise HTTPException(status_code=401, detail="用户不存在")
        if not auth_service.verify_password(body.old_password, rows[0]["password_hash"]):
            raise HTTPException(status_code=400, detail="原密码错误")
        if body.new_password == body.old_password:
            raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

        await db.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (auth_service.hash_password(body.new_password), rows[0]["id"]),
        )
        await db.commit()
        return {"status": "ok", "message": "密码已修改"}
    finally:
        await db.close()
