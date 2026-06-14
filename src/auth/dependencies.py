"""FastAPI 鉴权 dependency。

约定:
- 401 (AuthenticationError): 没 token / token 坏 / token 过期
- 403 (AuthorizationError): token 有效但角色不够

候选人端 Sprint 4 的端点不挂这些 dependency, 继续走 candidate_id 路径 soft-auth。
HR 端 Sprint 5-2 起的端点全部 require_hr_user。

Sprint 5.8: token 来源 = httpOnly cookie 优先, Authorization: Bearer 兜底。
理由: 浏览器 HR 端走 Cookie (httpOnly + SameSite=Strict, 抗 XSS); 测试 + 脚本
走 Bearer (evals 不动)。双路径在新 token 来源稳定后可以收一条。
"""
from __future__ import annotations

import os

from fastapi import Depends, Request

from src.auth.tokens import InvalidToken, decode_token
from src.schemas import User


class AuthenticationError(Exception):
    """缺 token / token 解码失败 / 已过期。映射到 401。"""


class AuthorizationError(Exception):
    """token 解码通过但 role 不够。映射到 403。"""


_HR_ROLES = frozenset({"hr", "admin"})

# Cookie 名 env-driven, 默认 "auth_token"; 改名让旧 cookie 自然失效 / 用 env
# 分 dev/prod cookie 互不影响。
DEFAULT_COOKIE_NAME = "auth_token"


def cookie_name() -> str:
    return os.environ.get("JWT_COOKIE_NAME") or DEFAULT_COOKIE_NAME


def _extract_token(request: Request) -> str:
    """Cookie 优先, Bearer 兜底。两者皆无 -> 401。"""
    cookie_token = request.cookies.get(cookie_name())
    if cookie_token:
        return cookie_token.strip()

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer ") :].strip()
        if bearer:
            return bearer

    raise AuthenticationError("缺少 token (cookie 或 Bearer 均无)")


def get_current_user(request: Request) -> User:
    """从 Cookie 或 Authorization 头解 JWT, 不查 DB (claims 已有 user_id + role)。
    上游 (require_hr_user 等) 通常不需要 username, 留作未来扩展用 load_user()。"""
    token = _extract_token(request)
    try:
        payload = decode_token(token)
    except InvalidToken as e:
        raise AuthenticationError(f"token 无效: {e}") from e

    user_id = payload.get("sub")
    role = payload.get("role")
    if not user_id or not role:
        raise AuthenticationError("token claims 缺 sub / role")
    return User(user_id=user_id, username="", role=role)


def require_hr_user(user: User = Depends(get_current_user)) -> User:
    """role 必须是 hr 或 admin。"""
    if user.role not in _HR_ROLES:
        raise AuthorizationError(
            f"需要 HR 权限, 当前 role={user.role}"
        )
    return user
