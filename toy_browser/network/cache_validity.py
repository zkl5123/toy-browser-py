"""R7-S03 第 4 点 — 缓存校验（纯逻辑决策层）。

方案原文："缓存校验 Vary、no-store/no-cache、授权响应、304 头合并；
缓存记录不得串 origin/凭据策略。"

仓库现状（P23.2/P23.3 已有的部分）
--------------------------------
``net.CacheStore`` 已经是"启发式磁盘缓存"：``max-age`` / ``no-cache`` /
``no-store`` / 无 ``max-age`` 时按 ``Date``-``Last-Modified`` 的 10%
启发式 TTL，304 复用 body 并刷新 freshness。

**缺的正是第 4 点本身**：

* **Vary 完全没实现** —— ``Vary: Accept-Encoding`` 的响应被之后任何
  ``Accept-Encoding`` 的请求命中（缓存串味）；``Vary: *`` 也照存。
* **Authorization 响应没特殊处理** —— 带 ``Authorization`` 请求头拿到
  的响应进了**共享**缓存，另一个无凭据的调用方可能读到它
  （RFC 7234 §3.2 要求除非响应显式 ``public`` / ``must-revalidate``
  / ``s-maxage``）。
* **304 头合并缺失** —— ``_single_get`` 里 304 分支直接把 304 的头当
  成响应头返回（注释写着"merge in the cached headers"，但代码没
  做），于是 ``Content-Type``、``Content-Security-Policy`` 这类只在
  原始 200 里出现的头在 304 之后**消失**——审计验收项"304 后安全
  响应头仍正确"当前是失败的。
* **缓存键只有 URL** —— 不含凭据策略，"不得串凭据策略"无从保证。

覆盖范围与"不冒充通过"
----------------------
纯逻辑：不落盘、不发请求、不读时钟（TTL 计算仍在 ``net._CacheEntry``
里，本模块只做**能不能存 / 能不能用 / 头怎么合**的判定）。真实磁盘
格式、缓存容量驱逐、HTTP/2 push cache 等**未实现**。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "CACHEABLE_STATUS", "HOP_BY_HOP_HEADERS",
    "CacheDecision",
    "parse_cache_control", "vary_fields", "vary_matches",
    "cache_key", "is_storable", "must_revalidate",
    "merge_304_headers", "describe_decision",
]


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: RFC 7231 §6.1 —— 默认可缓存的状态码（玩具实现的共享缓存口径）。
CACHEABLE_STATUS = frozenset({
    200, 203, 204, 206, 300, 301, 308, 404, 405, 410, 414, 501,
})

#: 逐跳头：304 合并时必须跳过（它们不属于"已存的响应头"）。
HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding",
    "upgrade", "content-length",
})


# ---------------------------------------------------------------------------
# Cache-Control / Vary
# ---------------------------------------------------------------------------

def parse_cache_control(value: str) -> dict:
    """把 ``Cache-Control`` 解析成 ``{directive: arg}``。

    无参指令值为 ``True``（``no-store`` → ``{"no-store": True}``），
    带参指令值为字符串（``max-age=60`` → ``{"max-age": "60"}``）。
    指令名小写；同名重复**保留首次**（与 CSP 的处理一致）。
    """
    out: dict = {}
    if not value or not isinstance(value, str):
        return out
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, _, arg = part.partition("=")
            name = name.strip().lower()
            out.setdefault(name, arg.strip().strip('"'))
        else:
            name = part.strip().lower()
            out.setdefault(name, True)
    return out


def vary_fields(headers: dict) -> list:
    """响应 ``Vary`` 里的字段名列表（小写、去重、保序）。

    ``Vary: *`` 返回 ``["*"]``。
    """
    raw = headers.get("vary") if isinstance(headers, dict) else None
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(x) for x in raw)
    raw = str(raw or "")
    if not raw.strip():
        return []
    if raw.strip() == "*":
        return ["*"]
    out: list = []
    for part in raw.split(","):
        name = part.strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def vary_matches(vary, stored_request_headers: dict,
                 new_request_headers: dict) -> bool:
    """第 4 点 — Vary 匹配：缓存条目能否被这次请求复用。

    规则（RFC 7234 §4.1）：``Vary`` 列出的**每个**字段，已存请求的
    值与本次请求的值必须相同（都缺失也算相同）。任一字段不同 ⇒
    不命中；``Vary: *`` 永不命中（无法可靠匹配）。
    """
    fields_ = list(vary or [])
    if not fields_:
        return True
    if "*" in fields_:
        return False
    stored = stored_request_headers or {}
    new = new_request_headers or {}
    for name in fields_:
        key = str(name).lower()
        sv = _header_value(stored.get(key))
        nv = _header_value(new.get(key))
        if sv != nv:
            return False
    return True


def _header_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


# ---------------------------------------------------------------------------
# 缓存键（不得串 origin / 凭据策略）
# ---------------------------------------------------------------------------

def cache_key(url: str, credentials_mode: str = "",
              vary: Optional[list] = None,
              request_headers: Optional[dict] = None) -> str:
    """第 4 点 — 缓存键 = URL + 凭据模式 + Vary 字段值。

    * URL 自带 origin（scheme/host/port），所以**不会串 origin**；
    * 凭据模式参与键：``omit`` / ``same-origin`` / ``include`` 三种
      请求拿到的响应**互不可见**（"缓存记录不得串凭据策略"）；
    * 有 ``Vary`` 时把各字段值拼进键（这样不同 Accept-Encoding 的请求
      命中不同条目，而不需要在磁盘上存"已存请求头"）。

    已知限制（如实记录）：``net.CacheStore`` 目前的磁盘布局是每个
    (URL, 凭据模式) 一个槽位，因此**不**使用 *vary* / *request_headers*
    参数，而是靠 :func:`vary_matches` 比对已存的请求头——变体会互相
    覆盖，但**绝不会错配**（不匹配即 miss 回源）。多变体并存需要变体
    索引，本轮未做；*vary* 参数留给将来的多变体实现。
    """
    base = str(url or "")
    cred = (credentials_mode or "").strip().lower()
    parts = [base, "cred=" + (cred or "unset")]
    for name in list(vary or []):
        if name == "*":
            parts.append("vary=*")
            continue
        parts.append(f"vary[{name}]={_header_value((request_headers or {}).get(name))}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 可存性判定
# ---------------------------------------------------------------------------

@dataclass
class CacheDecision:
    """一次"这个响应能不能进缓存"的判定结果（可观测）。"""
    allowed: bool
    reason: str = ""
    vary: tuple = field(default_factory=tuple)
    no_store: bool = False
    no_cache: bool = False
    must_revalidate: bool = False

    def __bool__(self) -> bool:
        return self.allowed


def is_storable(status: int, response_headers: dict,
                request_headers: Optional[dict] = None,
                credentials_mode: str = "") -> CacheDecision:
    """第 4 点 — 响应能否被**共享缓存**存储。

    顺序（与 :mod:`request_context` 的"不支持就明确拒绝"同一口径，
    每个拒绝都带 reason，绝不静默放行）：

    1. ``no-store`` → 拒绝；
    2. 状态码不在可缓存集合 → 拒绝；
    3. ``Vary: *`` → 拒绝（永不匹配，存了也没用）；
    4. **Authorization**：请求带了 ``Authorization`` 而响应既没有
       ``public`` 也没有 ``must-revalidate`` / ``s-maxage`` → 拒绝
       （RFC 7234 §3.2：共享缓存不得用带凭据请求的响应去服务其他
       请求）。
    """
    headers = response_headers or {}
    req = request_headers or {}
    cc = parse_cache_control(headers.get("cache-control", ""))
    if "no-store" in cc:
        return CacheDecision(False, "no-store", no_store=True)
    try:
        status_i = int(status)
    except (TypeError, ValueError):
        return CacheDecision(False, f"unparsable status {status!r}")
    if status_i not in CACHEABLE_STATUS:
        return CacheDecision(False, f"status {status_i} is not cacheable")
    vary = tuple(vary_fields(headers))
    if "*" in vary:
        return CacheDecision(False, "Vary: * (never matches)", vary=vary)
    # RFC 7234 §3.2 — 带凭据请求的响应。
    if _has_authorization(req):
        ok = any(k in cc for k in ("public", "must-revalidate", "s-maxage"))
        if not ok:
            return CacheDecision(
                False,
                "response to an authenticated request without "
                "public/must-revalidate/s-maxage (RFC 7234 3.2)",
                vary=vary)
    return CacheDecision(
        True,
        "cacheable",
        vary=vary,
        no_cache=("no-cache" in cc),
        must_revalidate=("must-revalidate" in cc),
    )


def _has_authorization(req: dict) -> bool:
    if not req:
        return False
    for k, v in req.items():
        if str(k).lower() == "authorization" and str(v or "").strip():
            return True
    return False


def must_revalidate(response_headers: dict) -> bool:
    """是否必须在使用前重新校验（``no-cache`` / ``must-revalidate``）。"""
    cc = parse_cache_control((response_headers or {}).get("cache-control", ""))
    return bool("no-cache" in cc or "must-revalidate" in cc)


# ---------------------------------------------------------------------------
# 304 头合并（RFC 7234 §4.3.4）
# ---------------------------------------------------------------------------

def merge_304_headers(stored_headers: dict,
                      incoming_headers: dict) -> dict:
    """第 4 点 — 304 到达后更新**已存**的响应头。

    规则：以**已存头为底**，用 304 携带的头**覆盖/新增**（304 只带
    变化了的头，没有带的头必须继续沿用旧的，否则 304 之后
    ``Content-Type`` / ``Content-Security-Policy`` 这类安全头会消失
    ——审计验收项"304 后安全响应头仍正确"）。

    * 跳过逐跳头与 ``Content-Length``（body 来自缓存，长度仍是旧的）；
    * ``set-cookie`` 走**多值列表**保留（第 3 点：重复 Set-Cookie
      全部保留），且 304 里的 Set-Cookie 与旧值合并而不是覆盖；
    * 输入为空 → 原样返回已存头的副本。
    """
    stored = dict(stored_headers or {})
    incoming = incoming_headers or {}
    if not incoming:
        return stored
    for name, value in incoming.items():
        key = str(name).lower()
        if key in HOP_BY_HOP_HEADERS:
            continue
        if key == "set-cookie":
            merged = _as_list(stored.get(key)) + _as_list(value)
            seen: list = []
            for item in merged:
                if item not in seen:
                    seen.append(item)
            stored[key] = seen
            continue
        stored[key] = value
    return stored


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def describe_decision(d: CacheDecision) -> str:
    """审计用的单行描述（被拒时把 reason 带上）。"""
    return f"cache:{'store' if d.allowed else 'skip'}" + (
        f" ({d.reason})" if d.reason else "")
