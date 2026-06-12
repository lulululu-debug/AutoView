"""FastAPI 鉴权 dependency。

约定:
- 401 (AuthenticationError): 没 token / token 坏 / token 过期
- 403 (AuthorizationError): token 有效但角色不够

候选人端 Sprint 4 的端点不挂这些 dependency, 继续走 candidate_id 路径 soft-auth。
HR 端 Sprint 5-2 起的端点全部 require_hr_user。
"""
from __future__ import annotations

from fastapi import Depends, Request

from src.auth.tokens import InvalidToken, decode_token
from src.schemas import User


class AuthenticationError(Exception):
    """缺 token / token 解码失败 / 已过期。映射到 401。"""


class AuthorizationError(Exception):
    """token 解码通过但 role 不够。映射到 403。"""


_HR_ROLES = frozenset({"hr", "admin"})


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthenticationError("缺少 Bearer token")
    token = auth[len("Bearer ") :].strip()
    if not token:
        raise AuthenticationError("Bearer token 为空")
    return token


def get_current_user(request: Request) -> User:
    """从 Authorization 头解 JWT, 不查 DB (claims 已有 user_id + role)。
    上游 (require_hr_user 等) 通常不需要 username, 留作未来扩展用 load_user()。"""
    token = _extract_bearer(request)
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
