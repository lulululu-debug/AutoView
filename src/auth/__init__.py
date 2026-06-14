"""认证与授权 —— Sprint 5-1 起。

职责:
- 密码哈希: bcrypt 直接用, 不引 passlib (轻一层)
- JWT: HS256 对称签名, 内置 user_id / role / exp 三个 claims
- FastAPI dependency: get_current_user, require_hr_user

候选人端 (Sprint 4) 继续用 candidate_id 作 path soft-auth, 不经过本层。
HR 端 API (Sprint 5-2 起) 一律走 require_hr_user 加 Bearer JWT。
"""
from __future__ import annotations

from src.auth.dependencies import (
    AuthenticationError,
    AuthorizationError,
    cookie_name,
    get_current_user,
    require_hr_user,
)
from src.auth.passwords import hash_password, verify_password
from src.auth.tokens import (
    DEFAULT_EXPIRE_MINUTES,
    InvalidToken,
    JwtNotConfigured,
    create_access_token,
    decode_token,
)

__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "DEFAULT_EXPIRE_MINUTES",
    "InvalidToken",
    "JwtNotConfigured",
    "cookie_name",
    "create_access_token",
    "decode_token",
    "get_current_user",
    "hash_password",
    "require_hr_user",
    "verify_password",
]
