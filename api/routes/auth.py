"""认证端点 —— Sprint 5-1, Sprint 5.8 起加 Cookie 路径。

POST /auth/login: username + password -> JWT
- Set-Cookie httpOnly + SameSite=Strict (浏览器 HR 端走这条, 抗 XSS)
- Response body 同时含 access_token (evals + 脚本走 Bearer 路径, 转期共存)

GET  /auth/me:     require_hr_user 返 {user_id, username, role} - HrGuard 用它
                   判 session 是否仍有效 (httpOnly cookie JS 读不到, 必须问 server)
POST /auth/logout: Set-Cookie Max-Age=0 把 token cookie 清掉

为什么 JSON body 而非 OAuth2 标准 form-data:
- Sprint 5-3 的 Next.js 端 fetch JSON 更顺手
- OAuth2PasswordBearer 仍能用在 dependency 端解 Authorization 头, 与登录格式无关

错误码:
- 401 Unauthorized: 用户名不存在 OR 密码错 (统一同一错误, 不泄漏用户存在性)
- 503 Service Unavailable: DB 或 JWT_SECRET 未配置 (走全局异常映射)
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserMe
from src import auth, db
from src.schemas import User

router = APIRouter(prefix="/auth", tags=["auth"])

HrUser = Annotated[User, Depends(auth.require_hr_user)]


def _expires_minutes() -> int:
    raw = os.environ.get("JWT_EXPIRE_MINUTES")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return auth.DEFAULT_EXPIRE_MINUTES


def _cookie_secure() -> bool:
    """dev http 默 false; prod 部署翻 JWT_COOKIE_SECURE=true。
    Secure=true 时浏览器只在 https 上送 cookie, dev localhost http 会送不了。"""
    return os.environ.get("JWT_COOKIE_SECURE", "").lower() in ("1", "true", "yes")


def _set_auth_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=auth.cookie_name(),
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response) -> TokenResponse:
    found = db.load_user_by_username(body.username)
    if found is None:
        # 注意: 同一错误码, 不区分"不存在"与"密码错", 防用户枚举
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user, hashed_password = found
    if not auth.verify_password(body.password, hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    expires_minutes = _expires_minutes()
    token = auth.create_access_token(user_id=user.user_id, role=user.role)

    # Sprint 5.8: 同时返 cookie (浏览器) + body 里的 access_token (evals/脚本)。
    _set_auth_cookie(response, token, expires_minutes * 60)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_minutes * 60,
        role=user.role,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, response: Response) -> TokenResponse:
    """Sprint 6.8: HR 自助注册, 成功即登录 (set cookie)。

    - REGISTER_INVITE_CODE 配置时邀请码必须匹配 (403); 未配置 = 开放注册
    - 重名 409 —— 注册场景要明确告知"名字被占", 与登录 401 防枚举语义不同:
      注册接口本身就会暴露占用信息, 无枚举增益
    - 永远注册为 role=hr; admin 只能种子脚本创建
    - Sprint 6.8 task 2 起, 新账号的数据与他人隔离 (owner 落在 job 上)
    """
    invite = os.environ.get("REGISTER_INVITE_CODE")
    if invite and body.invite_code != invite:
        raise HTTPException(status_code=403, detail="邀请码错误")
    if db.load_user_by_username(body.username) is not None:
        raise HTTPException(status_code=409, detail="用户名已被占用")

    import uuid

    user_id = uuid.uuid4().hex
    try:
        db.save_user(
            user_id=user_id,
            username=body.username,
            hashed_password=auth.hash_password(body.password),
            role="hr",
        )
    except Exception:
        # 并发重名撞 unique 约束等: 统一按占用处理
        raise HTTPException(status_code=409, detail="用户名已被占用")

    expires_minutes = _expires_minutes()
    token = auth.create_access_token(user_id=user_id, role="hr")
    _set_auth_cookie(response, token, expires_minutes * 60)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_minutes * 60,
        role="hr",
    )


@router.get("/me", response_model=UserMe)
def me(user: HrUser) -> UserMe:
    """Sprint 5.8: HrGuard 用它判 session 是否有效 (cookie httpOnly JS 看不到,
    必须问 server)。401 时前端跳登录页。username 字段在 token 里没有, 这里
    重新查 DB 拿一个完整对象给 UI 展示。"""
    full = db.load_user(user.user_id)
    if full is None:
        # token 通过但 DB 里用户被删: 当登录失效处理
        raise HTTPException(status_code=401, detail="账号已注销")
    return UserMe(user_id=full.user_id, username=full.username, role=full.role)


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    """Sprint 5.8: server 帮清 cookie (httpOnly JS 清不了)。
    不要求 auth dependency: 即使 token 已过期 / 无效, 前端"退出"按钮也应该能调,
    让本地 cookie 清掉。"""
    # Max-Age=0 + 同样的 path/samesite/secure 才能覆盖原 cookie
    response.set_cookie(
        key=auth.cookie_name(),
        value="",
        max_age=0,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )
    response.status_code = 204
    return response
