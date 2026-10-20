"""R7-S02 — 一个 RequestContext 贯穿所有请求（纯逻辑决策层）。

Round-7 实施方案 R7-S02（P0）要求"一个 RequestContext 贯穿所有
请求"。仓库现状：

* ``net.DocumentPolicy``（R4 stage 0）已经是**文档级**策略上下文
  （doc_origin / csp_directives / cors / enforce），并被 CLI 与 GUI
  主链共用；
* ``net._subresource_cors_mode`` 已区分 classic-script（默认
  no-cors）与 module / ``crossorigin``（cors）；
* ``parse_csp_directives`` 已实现"无指令 vs 空指令"与"重复指令保留
  首次"（CSP3 §4.2.3）。

**缺的是第 1 点本身**：没有一个把一次请求的全部身份与语义
（destination / mode / credentials / 重定向链 / deadline / 导航代次）
串起来的 RequestContext——策略在文档级，模式判断散落在各调用点，
重定向逐跳没有重新评估的载体。本模块补齐这一层。

覆盖范围与"不冒充通过"
----------------------
本模块是**纯逻辑决策层**：不做 socket、不发请求、不读时钟（时钟由
调用点注入）。它回答的是"这个请求**该不该**发出、以什么模式发、
发出后响应可读吗"——判定与元数据的组织。真实发包后的响应头校验
（服务端真的回了什么 ACAO）在拿到响应后由调用点用同一 context 复核，
本模块只给出"届时需要校什么"（``requires_acao`` / ``opaque_response``
/ ``requires_preflight``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

__all__ = [
    "DEST_NAVIGATE", "DEST_CLASSIC_SCRIPT", "DEST_MODULE", "DEST_IMAGE",
    "DEST_STYLE", "DEST_FETCH", "DEST_XHR", "DEST_FONT",
    "VALID_DESTINATIONS",
    "MODE_NAVIGATE", "MODE_NO_CORS", "MODE_CORS", "MODE_SAME_ORIGIN",
    "VALID_MODES",
    "CRED_OMIT", "CRED_SAME_ORIGIN", "CRED_INCLUDE", "VALID_CREDENTIALS",
    "CSP_KIND_FOR_DESTINATION",
    "SENSITIVE_REQUEST_HEADERS", "FORBIDDEN_RESPONSE_HEADERS",
    "RequestContext", "RequestDecision",
    "default_mode_for", "default_credentials_for",
    "context_for_destination", "evaluate_request",
    "filter_response_headers", "response_type",
]


# ---------------------------------------------------------------------------
# destination / mode / credentials（第 4 点）
# ---------------------------------------------------------------------------

DEST_NAVIGATE = "navigate"
DEST_CLASSIC_SCRIPT = "classic-script"
DEST_MODULE = "module"
DEST_IMAGE = "image"
DEST_STYLE = "style"
DEST_FETCH = "fetch"
DEST_XHR = "xhr"
DEST_FONT = "font"

VALID_DESTINATIONS = (DEST_NAVIGATE, DEST_CLASSIC_SCRIPT, DEST_MODULE,
                      DEST_IMAGE, DEST_STYLE, DEST_FETCH, DEST_XHR,
                      DEST_FONT)

MODE_NAVIGATE = "navigate"
MODE_NO_CORS = "no-cors"
MODE_CORS = "cors"
MODE_SAME_ORIGIN = "same-origin"

VALID_MODES = (MODE_NAVIGATE, MODE_NO_CORS, MODE_CORS, MODE_SAME_ORIGIN)

CRED_OMIT = "omit"
CRED_SAME_ORIGIN = "same-origin"
CRED_INCLUDE = "include"

VALID_CREDENTIALS = (CRED_OMIT, CRED_SAME_ORIGIN, CRED_INCLUDE)

#: destination → CSP 指令名（去 ``-src`` 后缀，交给 ``_csp_values_for``）。
#: navigate 没有对应的 fetch 指令，按 CSP3 落到 ``default-src``。
CSP_KIND_FOR_DESTINATION = {
    DEST_CLASSIC_SCRIPT: "script",
    DEST_MODULE: "script",
    DEST_IMAGE: "img",
    DEST_STYLE: "style",
    DEST_FETCH: "connect",
    DEST_XHR: "connect",
    DEST_FONT: "font",
    DEST_NAVIGATE: "default",
}

#: 跨源请求 / 跨源重定向时必须剥离的敏感请求头（fetch §4.5 / §4.6）。
SENSITIVE_REQUEST_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "cookie2",
    "www-authenticate", "proxy-authenticate",
})

#: 禁止响应头（脚本不可读，浏览器过滤掉）：forbidden response-header
#: name 集合的常用子集。
FORBIDDEN_RESPONSE_HEADERS = frozenset({
    "set-cookie", "set-cookie2", "access-control-allow-origin",
    "access-control-allow-credentials", "access-control-allow-headers",
    "access-control-allow-methods", "access-control-expose-headers",
    "access-control-max-age", "access-control-request-headers",
    "access-control-request-method", "connection", "keep-alive",
    "transfer-encoding", "trailer", "upgrade", "proxy-connection",
})

#: 允许的 URL scheme（其余明确拒绝，不偷换成放行）。
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: 混合内容里被**阻断**的 destination（optionally-blockable 的 image
#: 按规范可升级而非阻断，这里显式放行并只标记为 mixed；脚本类一律阻断）。
_MIXED_BLOCKED_DESTINATIONS = frozenset({
    DEST_CLASSIC_SCRIPT, DEST_MODULE, DEST_STYLE, DEST_FETCH, DEST_XHR,
    DEST_FONT,
})


def _origin_of(url: str) -> str:
    """URL 的源（``scheme://host[:port]``）；解析失败退化为 ``""``。"""
    if not url or not isinstance(url, str):
        return ""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    port = None
    try:
        port = parts.port
    except Exception:
        port = None
    return f"{parts.scheme.lower()}://{host}:{port}" if port \
        else f"{parts.scheme.lower()}://{host}"


def _scheme_of(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    try:
        return (urlsplit(url.strip()).scheme or "").lower()
    except Exception:
        return ""


def default_mode_for(destination: str,
                     crossorigin: Optional[str] = None) -> Optional[str]:
    """R7-S02 第 4 点 — destination 的默认请求模式。

    * **classic script** 无 ``crossorigin`` → ``no-cors``（规范默认：
      第三方 classic 脚本**不得**因为缺 ACAO 被拦，审计的
      ``classic_cross_origin_script`` 用例）；
    * ``type=module`` 与带 ``crossorigin`` 的元素 → ``cors``；
    * image / style → ``no-cors``（不可被 JS 读取的子资源）；
    * fetch / XHR → ``cors``（响应要被 JS 读，跨源需 ACAO；同源时
      同源判定优先，见 :func:`evaluate_request`）；
    * navigate → ``navigate``。

    未知 destination 返回 ``None``——调用点必须**明确拒绝**，不得
    把 None 当成"放行"（第 5 点：不支持的模式明确拒绝，不偷换成放行）。
    """
    if destination == DEST_NAVIGATE:
        return MODE_NAVIGATE
    if destination == DEST_CLASSIC_SCRIPT:
        return MODE_CORS if crossorigin is not None else MODE_NO_CORS
    if destination == DEST_MODULE:
        return MODE_CORS
    if destination in (DEST_IMAGE, DEST_STYLE, DEST_FONT):
        return MODE_CORS if crossorigin is not None else MODE_NO_CORS
    if destination in (DEST_FETCH, DEST_XHR):
        return MODE_CORS
    return None


def default_credentials_for(destination: str,
                            crossorigin: Optional[str] = None) -> str:
    """R7-S02 第 4/5 点 — destination 的默认凭据模式。

    只有显式 ``crossorigin="use-credentials"`` 才是 ``include``；
    classic 脚本默认 ``omit``（既不发凭据也不要求 ACAC），其余默认
    ``same-origin``。
    """
    if destination in (DEST_CLASSIC_SCRIPT, DEST_MODULE,
                       DEST_IMAGE, DEST_STYLE, DEST_FONT):
        return CRED_INCLUDE if crossorigin == "use-credentials" \
            else (CRED_SAME_ORIGIN if crossorigin is not None else CRED_OMIT)
    return CRED_SAME_ORIGIN


# ---------------------------------------------------------------------------
# RequestContext（第 1 点）
# ---------------------------------------------------------------------------

@dataclass
class RequestContext:
    """R7-S02 第 1 点 — 一次请求的完整身份与语义。

    字段对照方案第 1 点：

    * ``document_origin`` — 文档源。**只**来自文档响应（``final_url``），
      **不得**由 ``<base href>`` 改写（方案第 2 点）；``<base>`` 只影响
      :attr:`base_url`（相对 URL 解析基准）。
    * ``base_url`` — 相对 URL 解析基准（可能是 ``<base href>``）。
    * ``site_for_cookies`` — cookie 上下文用的站点（默认同 document_origin）。
    * ``destination`` / ``mode`` / ``credentials`` — 第 4 点分类。
    * ``method`` / ``body`` / ``headers`` — 请求本身。
    * ``policy`` — :class:`net.DocumentPolicy`（文档级 CSP / CORS）。
    * ``redirect_chain`` — 重定向链（逐跳追加，第 3 点"逐跳重新评估"）。
    * ``deadline`` — 绝对截止时刻（单调时钟；None = 无限制）。
    * ``navigation_generation`` — 导航代次（旧代次的迟到请求不得落地）。
    """

    url: str = ""
    document_origin: str = ""
    base_url: str = ""
    site_for_cookies: str = ""
    destination: str = DEST_FETCH
    mode: str = MODE_CORS
    credentials: str = CRED_SAME_ORIGIN
    method: str = "GET"
    body: object = None
    headers: dict = field(default_factory=dict)
    policy: object = None
    redirect_chain: list = field(default_factory=list)
    deadline: Optional[float] = None
    navigation_generation: int = 0
    #: 该 context 的 document_origin 来源（"response" / "" 未知）。
    origin_source: str = ""

    # -- 派生 ------------------------------------------------------------
    @property
    def request_origin(self) -> str:
        """本次请求 URL 自身的源。"""
        return _origin_of(self.url)

    @property
    def is_cross_origin(self) -> bool:
        """跨源判定：请求源 ≠ 文档源（空源按跨源处理，最保守）。"""
        ro = self.request_origin
        if not ro or not self.document_origin:
            return True
        return ro.lower() != self.document_origin.lower()

    @property
    def base_origin(self) -> str:
        """``base_url`` 的源（可能与 document_origin 不同，仅供诊断）。"""
        return _origin_of(self.base_url)

    @property
    def base_rewrites_origin(self) -> bool:
        """``<base href>`` 是否指向了与文档源**不同**的源。

        诊断用：方案第 2 点要求 origin 不得由 ``<base>`` 改写，本属性
        把"存在差异"显式暴露出来（但 context 的 document_origin
        永远不变——:meth:`with_base` 只改 base_url）。
        """
        bo = self.base_origin
        return bool(bo) and bo.lower() != (self.document_origin or "").lower()

    # -- 派生 context ----------------------------------------------------
    def with_base(self, base_url: str) -> "RequestContext":
        """只换 ``base_url``（``<base href>``）——**绝不动**
        ``document_origin``（方案第 2 点）。"""
        ctx = self.clone()
        ctx.base_url = base_url or ""
        return ctx

    def for_destination(self, destination: str,
                        crossorigin: Optional[str] = None,
                        url: str = "") -> "RequestContext":
        """派生一个指定 destination 的 context（模式/凭据按规范默认）。"""
        ctx = self.clone()
        ctx.destination = destination
        if url:
            ctx.url = url
        mode = default_mode_for(destination, crossorigin)
        # 未知 destination → 保持显式 None（evaluate 时明确拒绝），
        # 不悄悄退化成放行。
        ctx.mode = mode if mode is not None else ""
        ctx.credentials = default_credentials_for(destination, crossorigin)
        return ctx

    def for_redirect(self, next_url: str) -> "RequestContext":
        """R7-S02 第 3 点 — 逐跳重定向：新 context 重新评估。

        重定向链追加当前 URL；跨源跳转时凭据降级为 ``omit`` 并剥离
        敏感头（fetch §4.6：跨源重定向不得携带 Authorization/Cookie）。
        """
        ctx = self.clone()
        if self.url:
            ctx.redirect_chain = list(self.redirect_chain) + [self.url]
        ctx.url = next_url
        if ctx.is_cross_origin:
            ctx.credentials = CRED_OMIT
            ctx.headers = {k: v for k, v in (self.headers or {}).items()
                           if k.lower() not in SENSITIVE_REQUEST_HEADERS}
        return ctx

    def clone(self) -> "RequestContext":
        import copy
        return copy.copy(self)

    # -- 生命周期 --------------------------------------------------------
    def is_expired(self, now: Optional[float] = None) -> bool:
        """deadline 是否已过。``now`` 由调用点注入（本模块不读时钟）。"""
        if self.deadline is None:
            return False
        if now is None:
            return False
        return float(now) >= float(self.deadline)

    def describe(self) -> str:
        return (f"{self.destination}/{self.mode}(cred={self.credentials}, "
                f"cross_origin={self.is_cross_origin}, "
                f"hops={len(self.redirect_chain)}, "
                f"nav_gen={self.navigation_generation})")


def context_for_destination(document_origin: str, url: str,
                            destination: str,
                            crossorigin: Optional[str] = None,
                            policy: object = None,
                            base_url: str = "",
                            navigation_generation: int = 0,
                            deadline: Optional[float] = None) -> RequestContext:
    """便捷构造：文档源 + 目标 URL + destination → 完整 context。"""
    return RequestContext(
        url=url,
        document_origin=document_origin,
        base_url=base_url or document_origin,
        site_for_cookies=document_origin,
        destination=destination,
        mode=default_mode_for(destination, crossorigin) or "",
        credentials=default_credentials_for(destination, crossorigin),
        policy=policy,
        navigation_generation=navigation_generation,
        deadline=deadline,
        origin_source="response",
    )


# ---------------------------------------------------------------------------
# 发送前判定（第 3 / 5 / 6 点）
# ---------------------------------------------------------------------------

@dataclass
class RequestDecision:
    """R7-S02 — 一次"这个请求能不能发、发出后响应可读吗"的判定。"""

    allowed: bool
    reason: str = ""
    blocked_by: str = ""
    requires_preflight: bool = False
    requires_acao: bool = False
    opaque_response: bool = False
    mixed_content: bool = False

    def describe(self) -> str:
        verdict = "ALLOW" if self.allowed else "DENY"
        by = f" by {self.blocked_by}" if self.blocked_by else ""
        flags = []
        if self.requires_preflight:
            flags.append("preflight")
        if self.requires_acao:
            flags.append("needs-ACAO")
        if self.opaque_response:
            flags.append("opaque")
        if self.mixed_content:
            flags.append("mixed-content")
        tail = f" [{' '.join(flags)}]" if flags else ""
        return f"{verdict}{by}: {self.reason}{tail}"


def evaluate_request(ctx: RequestContext,
                     now: Optional[float] = None) -> RequestDecision:
    """R7-S02 第 3/5/6 点 — **发送前**判定（被禁止的请求不得先发包）。

    判定顺序（任一拒绝即返回，绝不"先发再丢"）：

    1. destination 已知（未知 → 拒绝，第 5 点"不支持的模式明确拒绝"）；
    2. mode 已知且合法；
    3. scheme 在允许集合内（http/https，其余明确拒绝）；
    4. deadline 未过；
    5. 混合内容：https 文档里的 http 子资源——active（script /
       style / fetch / xhr / font）**阻断**，image 只标记；
    6. CSP：按 destination 对应的指令（connect-src / img-src /
       script-src …）经 ``DocumentPolicy.csp_allowed`` 判定；
    7. CORS：同源**无需** ACAO；跨源时 ``no-cors`` → opaque（可读
       性受限但请求合法）、``cors`` → 响应须带匹配 ACAO（标记
       ``requires_acao``，由响应到达时复核）、``same-origin`` 模式
       却跨源 → 拒绝。
    """
    # 1 — destination
    if ctx.destination not in VALID_DESTINATIONS:
        return RequestDecision(
            False, f"unknown destination '{ctx.destination}'",
            blocked_by="destination")

    # 2 — mode（空串 = default_mode_for 判不出 → 明确拒绝）
    if ctx.mode not in VALID_MODES:
        return RequestDecision(
            False,
            f"unsupported request mode '{ctx.mode or '<none>'}' for "
            f"destination '{ctx.destination}' (not silently downgraded)",
            blocked_by="mode")

    # 3 — scheme
    scheme = _scheme_of(ctx.url)
    if scheme not in ALLOWED_SCHEMES:
        return RequestDecision(
            False, f"unsupported scheme '{scheme or '<none>'}'",
            blocked_by="scheme")

    # 4 — deadline
    if ctx.is_expired(now):
        return RequestDecision(False, "deadline exceeded",
                               blocked_by="deadline")

    # 5 — 混合内容
    mixed = (_scheme_of(ctx.document_origin) == "https"
             and scheme == "http")
    if mixed and ctx.destination in _MIXED_BLOCKED_DESTINATIONS:
        return RequestDecision(
            False,
            "blockable mixed content: https document loading http "
            f"{ctx.destination}",
            blocked_by="mixed-content", mixed_content=True)

    # 6 — CSP
    pol = ctx.policy
    if pol is not None:
        kind = CSP_KIND_FOR_DESTINATION.get(ctx.destination, "default")
        try:
            allowed = pol.csp_allowed(kind, ctx.url)
        except Exception:
            allowed = True          # 策略不可用不得崩（P13）
            pol = None
        if not allowed:
            return RequestDecision(
                False,
                f"blocked by document CSP {kind}-src",
                blocked_by="csp")

    # 7 — CORS
    cross = ctx.is_cross_origin
    if not cross:
        # 同源无需 ACAO（第 4/5 点）
        return RequestDecision(
            True, "same-origin request (no ACAO required)",
            mixed_content=mixed)

    if ctx.mode == MODE_SAME_ORIGIN:
        return RequestDecision(
            False, "same-origin mode but cross-origin target",
            blocked_by="cors")

    if ctx.mode == MODE_NO_CORS:
        return RequestDecision(
            True, "cross-origin no-cors request (opaque response)",
            opaque_response=True, mixed_content=mixed)

    # MODE_CORS / MODE_NAVIGATE
    non_simple = (ctx.method or "GET").upper() not in ("GET", "HEAD", "POST")
    preflight = bool(non_simple or ctx.credentials == CRED_INCLUDE
                     or ctx.headers)
    return RequestDecision(
        True,
        "cross-origin cors request (response must carry a matching ACAO)",
        requires_preflight=preflight,
        requires_acao=True,
        mixed_content=mixed)


def filter_response_headers(ctx: RequestContext,
                            headers: dict) -> tuple:
    """R7-S02 第 5 点 — 禁止响应头过滤。

    返回 ``(visible, stripped)``：``visible`` 是脚本可读的头，
    ``stripped`` 是被过滤掉的头名列表。no-cors（opaque）响应一律
    **无**可读头。
    """
    if not headers:
        return ({}, [])
    if ctx.mode == MODE_NO_CORS and ctx.is_cross_origin:
        return ({}, sorted(headers.keys()))
    visible = {}
    stripped = []
    for k, v in headers.items():
        if str(k).lower() in FORBIDDEN_RESPONSE_HEADERS:
            stripped.append(k)
        else:
            visible[k] = v
    return (visible, stripped)


def response_type(ctx: RequestContext) -> str:
    """R7-S02 第 5 点 — JS 侧 ``Response.type``（Fetch 规范
    ``basic`` / ``cors`` / ``opaque`` / ``error``）的纯逻辑判定。

    与 :func:`evaluate_request` 的 CORS 判定（``opaque_response`` /
    ``requires_acao`` 标记）严格同源，不各写一套：

    * 同源 → ``"basic"``（脚本可读，无需 ACAO）；
    * 跨源 no-cors → ``"opaque"``（不可被 JS 读取，响应体 / 头不可见）；
    * 跨源 cors（响应须带匹配 ACAO）→ ``"cors"``；
    * 其它不支持的 mode（unknown destination / empty mode 等）→
      ``"error"``（规范：无法判定的响应类型，不得偷换成 basic 放行）。

    纯逻辑：不读时钟、不发请求、不 import 运行时；``ctx`` 由调用点
    按 destination 构造（:func:`context_for_destination`）。
    """
    decision = evaluate_request(ctx)
    if not decision.allowed:
        # 被发送前策略拒（scheme / mode / destination / 混合内容 /
        # same-origin-mode-却跨源）——规范语义是 "error" 类型。
        return "error"
    if decision.opaque_response:
        return "opaque"
    if decision.requires_acao:
        return "cors"
    # 同源请求（evaluate 在此处返回 allowed=True 且无 CORS 标记）。
    return "basic"
