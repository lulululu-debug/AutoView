"""JWT 签发与校验 (HS256 对称密钥)。

Claims:
- sub:  user_id (jose / OAuth2 约定)
- role: "hr" | "admin" (跟 src.schemas.User.role 一致)
- exp:  过期时间 (UTC timestamp)
- iat:  签发时间 (UTC timestamp)

为什么不存 username 进 claim:
- 减小 token 体积
- username 可能变 (改名), role + user_id 够撑业务

为什么 HS256 不用 RS256:
- 单服务 + 内网, 对称密钥够用; RS256 公私钥多一层管理
- 真上多服务 / 第三方 verify 时再切, 算法名换一下就行
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

DEFAULT_EXPIRE_MINUTES = 60
_ALGORITHM = "HS256"


class JwtNotConfigured(RuntimeError):
    """JWT_SECRET 未配置时, 任何签发/校验都抛本异常。
    与 DatabaseNotConfigured / RedisNotConfigured 一样的惰性约定。"""


class InvalidToken(RuntimeError):
    """token 解码失败 / 签名错 / 已过期 等都收敛到这一类异常,
    上游 (FastAPI dependency) 统一映射成 401。"""


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise JwtNotConfigured(
            "JWT_SECRET 未配置, 拒绝签发或校验 JWT。"
            "参考 .env.example, dev 期可用一段随机 hex 字符串。"
        )
    if len(secret) < 16:
        # 长度过短的 secret 直接拒, 防 dev 期偷懒留下安全坑
        raise JwtNotConfigured(
            "JWT_SECRET 长度不足 16, 视作未配置以避免低熵密钥",
        )
    return secret


def _expire_minutes() -> int:
    raw = os.environ.get("JWT_EXPIRE_MINUTES")
    if not raw:
        return DEFAULT_EXPIRE_MINUTES
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_EXPIRE_MINUTES
    except ValueError:
        return DEFAULT_EXPIRE_MINUTES


def create_access_token(
    *,
    user_id: str,
    role: str,
    expires_minutes: int | None = None,
) -> str:
    """签一个 access token, exp 跟 JWT_EXPIRE_MINUTES (默认 60) 走。"""
    now = datetime.now(timezone.utc)
    exp_delta = timedelta(minutes=expires_minutes or _expire_minutes())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + exp_delta).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """解码 + 校验签名 + 校验 exp; 任何问题统一抛 InvalidToken。
    返回 dict 是 jose 的原始 payload, 上游再取 sub / role。"""
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except JWTError as e:
        raise InvalidToken(str(e)) from e
