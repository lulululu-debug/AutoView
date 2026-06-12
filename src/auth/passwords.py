"""bcrypt 密码哈希。

直接用 bcrypt, 不引 passlib:
- bcrypt 5.x 是稳定且自带盐 / 迭代算 cost 的成熟实现
- passlib 在我们的场景只是再包一层, 多一个间接依赖
"""
from __future__ import annotations

import bcrypt

# bcrypt 默认 cost=12, 约 200~300ms/hash, 防暴力破解够用。
# 测试通过 BCRYPT_ROUNDS 环境变量可降到 4 (跑得快, 但绝不能在 prod 用)。
import os

_DEFAULT_ROUNDS = 12


def _rounds() -> int:
    raw = os.environ.get("BCRYPT_ROUNDS")
    if not raw:
        return _DEFAULT_ROUNDS
    try:
        v = int(raw)
        # 4 是 bcrypt 的最低值, 高于 14 在 dev / test 中没意义
        return max(4, min(v, 14))
    except ValueError:
        return _DEFAULT_ROUNDS


def hash_password(password: str) -> str:
    """对明文密码做 bcrypt 哈希, 返回 UTF-8 字符串。"""
    if not password:
        raise ValueError("password 不能为空")
    salt = bcrypt.gensalt(rounds=_rounds())
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验明文密码是否对得上 hash。
    bcrypt 内部做了恒等时间比较, 不会泄漏字符匹配位置。"""
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # 输入格式坏掉时一律视作失败, 不向上游泄漏 hash 内部细节
        return False
