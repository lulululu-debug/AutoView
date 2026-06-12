"""种 HR / admin 用户的运维脚本 —— Sprint 5-1。

用法:
    python -m scripts.seed_users --username hr1 --password 'somepw' --role hr
    python -m scripts.seed_users --username admin --password 'xxx' --role admin

设计:
- 不走标准 register 流程: 这是 dev / 内部账号种用的, 没邮件验证、没限速
- 同 username 重跑会 upsert (改密码), 故意做成"管理员重置密码"的快捷工具
- 真到外部用户接入时, 再做 POST /auth/register / 邀请流
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys

from src import auth, db
from src.schemas import User

log = logging.getLogger(__name__)

_ALLOWED_ROLES = ("hr", "admin")


def seed_user(*, username: str, password: str, role: str) -> User:
    """种或更新一个用户。返回种好的 User pydantic (不含 hash)。"""
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"role 必须是 {_ALLOWED_ROLES} 之一, 收到 {role!r}")
    existing = db.load_user_by_username(username)
    user_id = existing[0].user_id if existing else secrets.token_hex(16)
    hashed = auth.hash_password(password)
    db.save_user(
        user_id=user_id,
        username=username,
        hashed_password=hashed,
        role=role,
    )
    return User(user_id=user_id, username=username, role=role)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="种 HR / admin 账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--role", default="hr", choices=_ALLOWED_ROLES,
        help="默认 hr; admin 会拿到所有 HR 端点权限",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 保证 users 表存在
    db.init_db()
    user = seed_user(
        username=args.username, password=args.password, role=args.role,
    )
    print(f"[seed_users] ok: user_id={user.user_id} username={user.username} role={user.role}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
