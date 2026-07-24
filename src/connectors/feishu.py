"""飞书 OpenAPI 连接器 —— Sprint 6.7 task 1。

知识管线「从飞书导入」的唯一网络调用点。直连 OpenAPI(拍板不经 MCP);
app 凭证与 lark-mcp 同源(FEISHU_APP_ID/SECRET)。

权限分层(2026-07-18 实战结论, memory: feishu-wiki-batch-ingest):
- 单篇文档: tenant_access_token 即可读
- **列 wiki 子节点: 个人 wiki 对 tenant token 返 131006, 必须 user OAuth**;
  授权 URL 的 scope 必须显式携带, 否则只发基础 scope
- user/refresh token 只存 Redis(TTL 对齐飞书 2h/30d), 不落 PG

调用约定:
- 未配置 → FeishuNotConfigured; 需授权而无 user token → FeishuNotAuthorized;
  飞书业务错误 → FeishuApiError(code, msg)。API 层做异常映射, 不在这里吞。
- 权限类错误码(如 131006)自动用 user token 重试一次(有则)。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Any

import os

log = logging.getLogger(__name__)

_BASE = "https://open.feishu.cn/open-apis"
_TIMEOUT = 15.0
_OAUTH_SCOPES = "wiki:wiki:readonly docx:document:readonly"
# 权限不足类错误码: 触发 tenant -> user token 重试
_PERMISSION_CODES = {131006, 99991672, 99991679}

_REDIS_USER_TOKEN = "feishu:user_token"
_REDIS_REFRESH_TOKEN = "feishu:refresh_token"
_REFRESH_TTL_SECONDS = 30 * 24 * 3600


class FeishuNotConfigured(RuntimeError):
    """FEISHU_APP_ID / FEISHU_APP_SECRET 未配置。"""


class FeishuNotAuthorized(RuntimeError):
    """该操作需要 user OAuth(如列 wiki 子节点), 且当前无有效 user token。"""


class FeishuApiError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(f"飞书 API 错误 code={code}: {msg}")
        self.code = code
        self.msg = msg


# ---- 凭证解析: env 优先 (部署钉死), DB 兜底 (HR 前端配置) ----
# Sprint 6.7 task 5: app_secret 在 DB 中用 JWT_SECRET 派生密钥 Fernet 加密
# (JWT_SECRET 本就是启动必配项, 不新增环境变量)。secret 永不回传前端。

_cred_cache: dict = {"value": None, "at": 0.0}
_CRED_CACHE_SECONDS = 60.0


def _fernet():
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        return None
    import base64
    import hashlib

    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    f = _fernet()
    if f is None:
        raise FeishuNotConfigured("JWT_SECRET 未配置, 无法加密存储飞书凭证")
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt_secret(token: str) -> str | None:
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        log.warning("飞书 app_secret 解密失败 (JWT_SECRET 变更?), 视为未配置")
        return None


def invalidate_credentials_cache() -> None:
    _cred_cache["value"] = None
    _cred_cache["at"] = 0.0


def _credentials() -> tuple[str, str, str] | None:
    """返回 (app_id, app_secret, source); source: "env" | "db"。无则 None。"""
    env_id = os.environ.get("FEISHU_APP_ID")
    env_secret = os.environ.get("FEISHU_APP_SECRET")
    if env_id and env_secret:
        return (env_id, env_secret, "env")

    if _cred_cache["value"] is not None and time.time() - _cred_cache["at"] < _CRED_CACHE_SECONDS:
        return _cred_cache["value"]

    try:
        from src import db
        app_id = db.get_app_setting("feishu_app_id")
        enc = db.get_app_setting("feishu_app_secret")
    except Exception:
        return None  # PG 未配置/不可用: 只认 env
    if not app_id or not enc:
        return None
    secret = _decrypt_secret(enc)
    if not secret:
        return None
    value = (app_id, secret, "db")
    _cred_cache["value"] = value
    _cred_cache["at"] = time.time()
    return value


def credential_source() -> str | None:
    c = _credentials()
    return c[2] if c else None


def app_id_masked() -> str | None:
    c = _credentials()
    if not c:
        return None
    app_id = c[0]
    return app_id[:8] + "***" if len(app_id) > 8 else app_id[:2] + "***"


def is_configured() -> bool:
    return _credentials() is not None


def test_credentials(app_id: str, app_secret: str) -> None:
    """保存前的连通性测试: 直接换一次 tenant token, 失败抛 FeishuApiError。"""
    _request(
        "POST", "/auth/v3/tenant_access_token/internal",
        body={"app_id": app_id, "app_secret": app_secret},
    )


# ---- 底层请求 ----

def _request(
    method: str, path: str, *,
    token: str | None = None,
    params: dict | None = None,
    body: dict | None = None,
) -> dict:
    url = f"{_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code", 0) != 0:
        raise FeishuApiError(payload.get("code", -1), payload.get("msg", ""))
    return payload


# ---- tenant token(进程内缓存至过期) ----

_tenant_cache: dict[str, Any] = {"token": None, "expire_at": 0.0}


def _tenant_token() -> str:
    creds = _credentials()
    if creds is None:
        raise FeishuNotConfigured("飞书凭证未配置 (env 或前端设置)")
    if _tenant_cache["token"] and time.time() < _tenant_cache["expire_at"]:
        return _tenant_cache["token"]
    payload = _request(
        "POST", "/auth/v3/tenant_access_token/internal",
        body={"app_id": creds[0], "app_secret": creds[1]},
    )
    _tenant_cache["token"] = payload["tenant_access_token"]
    # 提前 60s 过期, 防边界撞线
    _tenant_cache["expire_at"] = time.time() + int(payload.get("expire", 3600)) - 60
    return _tenant_cache["token"]


# ---- user OAuth ----

def authorize_url(state: str = "feishu-import") -> str:
    """HR 点击的授权链接。scope 必须显式写进 URL(实战坑: 不写只给基础 scope)。"""
    creds = _credentials()
    if creds is None:
        raise FeishuNotConfigured("飞书凭证未配置 (env 或前端设置)")
    redirect = os.environ.get(
        "FEISHU_OAUTH_REDIRECT",
        "http://localhost:8000/admin/feishu/oauth/callback",
    )
    q = urllib.parse.urlencode({
        "app_id": creds[0],
        "redirect_uri": redirect,
        "scope": _OAUTH_SCOPES,
        "state": state,
    })
    return f"{_BASE}/authen/v1/authorize?{q}"


def exchange_code(code: str) -> None:
    """OAuth 回调的 code 换 user token, 存 Redis(不落 PG)。"""
    redirect = os.environ.get(
        "FEISHU_OAUTH_REDIRECT",
        "http://localhost:8000/admin/feishu/oauth/callback",
    )
    creds = _credentials()
    if creds is None:
        raise FeishuNotConfigured("飞书凭证未配置")
    payload = _request(
        "POST", "/authen/v2/oauth/token",
        body={
            "grant_type": "authorization_code",
            "client_id": creds[0],
            "client_secret": creds[1],
            "code": code,
            "redirect_uri": redirect,
        },
    )
    _store_user_tokens(payload)


def _store_user_tokens(payload: dict) -> None:
    from src.cache.base import get_redis
    r = get_redis()
    access = payload.get("access_token")
    if access:
        ttl = max(60, int(payload.get("expires_in", 7200)) - 60)
        r.set(_REDIS_USER_TOKEN, access, ex=ttl)
    refresh = payload.get("refresh_token")
    if refresh:
        r.set(_REDIS_REFRESH_TOKEN, refresh, ex=_REFRESH_TTL_SECONDS)


def _user_token() -> str | None:
    """取有效 user token; 过期但有 refresh 则刷新; 都没有返回 None。"""
    from src.cache.base import RedisNotConfigured, get_redis
    try:
        r = get_redis()
    except RedisNotConfigured:
        return None
    token = r.get(_REDIS_USER_TOKEN)
    if token:
        return token
    refresh = r.get(_REDIS_REFRESH_TOKEN)
    if not refresh:
        return None
    try:
        payload = _request(
            "POST", "/authen/v2/oauth/token",
            body={
                "grant_type": "refresh_token",
                "client_id": (_credentials() or ("", "", ""))[0],
                "client_secret": (_credentials() or ("", "", ""))[1],
                "refresh_token": refresh,
            },
        )
    except FeishuApiError:
        log.warning("飞书 refresh token 失效, 需要重新授权")
        return None
    _store_user_tokens(payload)
    from src.cache.base import get_redis as _gr
    return _gr().get(_REDIS_USER_TOKEN)


def is_authorized() -> bool:
    return _user_token() is not None


# ---- 带权限重试的调用 ----

def _call(method: str, path: str, *, params: dict | None = None) -> dict:
    """先 tenant; 权限类错误且有 user token 时自动重试一次。"""
    try:
        return _request(method, path, token=_tenant_token(), params=params)
    except FeishuApiError as e:
        if e.code not in _PERMISSION_CODES:
            raise
        user = _user_token()
        if user is None:
            raise FeishuNotAuthorized(
                f"该操作需要 user 授权 (飞书 code={e.code}); "
                f"请先完成 OAuth 连接"
            ) from e
        return _request(method, path, token=user, params=params)


# ---- wiki / docx ----

def get_wiki_node(node_token: str) -> dict:
    """wiki 节点元信息: {title, obj_token, obj_type, has_child, space_id, ...}"""
    payload = _call(
        "GET", "/wiki/v2/spaces/get_node",
        params={"token": node_token, "obj_type": "wiki"},
    )
    return payload["data"]["node"]


def list_wiki_children(space_id: str, parent_node_token: str) -> list[dict]:
    """列 wiki 目录的直接子节点(分页拉全)。个人 wiki 需 user token(实战: 131006)。"""
    nodes: list[dict] = []
    page_token = ""
    while True:
        params = {
            "parent_node_token": parent_node_token, "page_size": 50,
        }
        if page_token:
            params["page_token"] = page_token
        payload = _call(
            "GET", f"/wiki/v2/spaces/{space_id}/nodes", params=params,
        )
        data = payload["data"]
        nodes.extend(data.get("items") or [])
        if not data.get("has_more"):
            return nodes
        page_token = data.get("page_token", "")
        if not page_token:
            return nodes


def fetch_document_blocks(document_id: str) -> list[dict]:
    """拉 docx 全部 block(分页)。结构化 heading 由 blocks 携带 ——
    这是产品化相对上次手工 raw_content 路径的关键升级(标题层级不丢)。"""
    blocks: list[dict] = []
    page_token = ""
    while True:
        params: dict = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        payload = _call(
            "GET", f"/docx/v1/documents/{document_id}/blocks", params=params,
        )
        data = payload["data"]
        blocks.extend(data.get("items") or [])
        if not data.get("has_more"):
            return blocks
        page_token = data.get("page_token", "")
        if not page_token:
            return blocks


# ---- URL 解析(纯函数, eval 直接锁) ----

def parse_url(url: str) -> tuple[str, str] | None:
    """解析飞书链接 -> (kind, token)。kind: "wiki" | "docx"。
    支持 *.feishu.cn / *.larksuite.com / my.feishu.cn 的
    /wiki/<token> 与 /docx/<token> 形态; 无法识别返回 None。"""
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return None
    if not parsed.netloc or not (
        parsed.netloc.endswith(".feishu.cn")
        or parsed.netloc.endswith(".larksuite.com")
        or parsed.netloc == "my.feishu.cn"
    ):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("wiki", "docx"):
        token = parts[1]
        if token:
            return (parts[0], token)
    return None


def reset_for_testing() -> None:
    """测试用: 清 tenant token 进程缓存。业务代码不要调。"""
    _tenant_cache["token"] = None
    _tenant_cache["expire_at"] = 0.0
    invalidate_credentials_cache()
