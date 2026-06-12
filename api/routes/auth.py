"""认证端点 —— Sprint 5-1。

POST /auth/login: username + password -> JWT Bearer。

为什么 JSON body 而非 OAuth2 标准 form-data:
- Sprint 5-3 的 Next.js 端 fetch JSON 更顺手
- OAuth2PasswordBearer 仍能用在 dependency 端解 Authorization 头, 与登录格式无关

错误码:
- 401 Unauthorized: 用户名不存在 OR 密码错 (统一同一错误, 不泄漏用户存在性)
- 503 Service Unavailable: DB 或 JWT_SECRET 未配置 (走全局异常映射)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas import LoginRequest, TokenResponse
from src import auth, db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest) -> TokenResponse:
    found = db.load_user_by_username(body.username)
    if found is None:
        # 注意: 同一错误码, 不区分"不存在"与"密码错", 防用户枚举
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user, hashed_password = found
    if not auth.verify_password(body.password, hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    expires_minutes = auth.DEFAULT_EXPIRE_MINUTES
    import os
    raw = os.environ.get("JWT_EXPIRE_MINUTES")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                expires_minutes = v
        except ValueError:
            pass

    token = auth.create_access_token(user_id=user.user_id, role=user.role)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_minutes * 60,
        role=user.role,
    )
