"""R7-S03 第 2 点 — 真实 site-for-cookies 与凭据上下文（纯逻辑决策层）。

仓库现状（R7-S05 已做的部分）
----------------------------
``net._subresource_cookie_context(url, doc_origin)`` 已经会给**子资源**
请求算 ``is_cross_site``（按 scheme + registrable domain，不是裸 host
比较），``_follow_redirects`` 也把 ``cookie_context`` 透传到每一跳。

**还缺的正是第 2 点本身**：

* 透传到每一跳的是**同一个 dict**，跨站标记在**第 0 跳**算完之后就
  不再变化——``a.test`` 文档里的子资源 302 到 ``b.test``，第二跳仍然
  带着"同站"标记，Lax cookie 因此泄漏到跨站目标（审计的
  "重定向不使用真实 site-for-cookies"）。
* **凭据模式（credentials mode）根本没有参与 cookie 发送判定**：
  ``credentials: 'omit'`` 的跨源 fetch、无 ``crossorigin`` 的 classic
  脚本，都会照发所有匹配 cookie。
* 调用点缺省是"同站 + 顶层 + GET"——默认值本身就在替调用方做安全
  决策，文档导航之外的入口（JS fetch / XHR）走的也是这个默认值。

本模块把这三件事收敛成一个**不可变**的 ``CookieRequestContext``：

* :meth:`is_cross_site` —— 按 **schemeful same-site**（RFC 6265bis
  §5.2）把请求 URL 的站点与 ``document_site`` 比；``document_site``
  为空（未知 / opaque 顶层）一律按**跨站**处理，宁可不发；
* :meth:`sends_cookies` —— 凭据模式门禁（``omit`` 不发；
  ``same-origin`` 仅在同源时发；``include`` 才无条件发）；
* :meth:`for_redirect` —— 每一跳**重新**求值与**降级**（跨源跳转把
  凭据降为 ``omit``，与 :mod:`request_context` 的 ``for_redirect`` 同一
  规则），并把整条链留在 ``redirect_chain`` 里供审计。

覆盖范围与"不冒充通过"
----------------------
纯逻辑：不发请求、不碰 socket、不读时钟。它只回答"这次发送**该带
哪些 cookie 语义**"。真正的 ``Cookie`` 头拼装仍由
``cookie_store.CookieJar.cookie_header_for`` 完成（本模块只喂给它
**真实算出来**的三个开关，不再让它吃缺省值）。操作系统级凭据存储、
真实第三方 cookie 分区（CHIPS / storage partitioning）**未实现**，
只保证"不越权发送"。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional
from urllib.parse import urljoin

from toy_browser.network.request_context import (
    CRED_INCLUDE, CRED_OMIT, CRED_SAME_ORIGIN, VALID_CREDENTIALS,
    DEST_NAVIGATE, _origin_of,
)

__all__ = [
    "CookieRequestContext",
    "for_navigation", "for_subresource", "unknown_context",
    "from_legacy_dict", "from_request_context",
    "same_origin", "site_for_cookies_of",
]


# ---------------------------------------------------------------------------
# 站点 / 源 计算
# ---------------------------------------------------------------------------

def _site_for_cookies(url: str) -> str:
    """URL 的 site-for-cookies（``scheme://registrable-domain``）。

    复用 :func:`cookie_store.site_for_cookies`（schemeful same-site）；
    cookie_store 出问题（或 URL 无法解析）时退化为 ``""``——空串在
    :class:`CookieRequestContext` 里代表**未知站点**，一律按跨站处理。
    """
    s = str(url or "")
    if not s or "://" not in s:
        return ""
    try:
        from toy_browser.network import cookie_store as _cs
        return _cs.site_for_cookies(s)
    except Exception:
        return ""


def site_for_cookies_of(url: str) -> str:
    """:func:`_site_for_cookies` 的公开别名。"""
    return _site_for_cookies(url)


def same_origin(a: str, b: str) -> bool:
    """两个 URL/源字符串是否同源（scheme + host + port）。

    任一侧为空 → ``False``（未知源不等于同源）。
    """
    oa, ob = _origin_of(a), _origin_of(b)
    return bool(oa) and oa == ob


# ---------------------------------------------------------------------------
# CookieRequestContext
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CookieRequestContext:
    """一次发送的 cookie 语义（第 2 点）。

    Attributes
    ----------
    url:
        本次请求的绝对 URL。
    document_site:
        **顶层文档的 site-for-cookies**（``scheme://registrable-domain``）。
        顶层导航时它等于请求 URL 自己的站点。空串 = 未知（opaque 或
        调用方没给）→ 一律按跨站处理。
    document_origin:
        顶层文档的源，仅用于 ``credentials=same-origin`` 判定。
    destination:
        ``navigate`` / ``script`` / ``style`` / ``image`` / ``fetch`` …
        （沿用 :mod:`request_context` 的常量）。
    method:
        HTTP 方法（大小写不敏感）。
    credentials_mode:
        ``omit`` / ``same-origin`` / ``include``。
    redirect_chain:
        已经走过的跳转 URL（**不含** ``url`` 本身），供审计与循环检测。
    """

    url: str = ""
    document_site: str = ""
    document_origin: str = ""
    destination: str = ""
    method: str = "GET"
    credentials_mode: str = CRED_SAME_ORIGIN
    redirect_chain: tuple = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", str(self.url or ""))
        object.__setattr__(self, "method", (self.method or "GET").upper())
        mode = (self.credentials_mode or CRED_SAME_ORIGIN).lower()
        if mode not in VALID_CREDENTIALS:
            # 未知凭据模式不得被当成 include —— 降级为 omit（保守）。
            mode = CRED_OMIT
        object.__setattr__(self, "credentials_mode", mode)
        object.__setattr__(
            self, "redirect_chain", tuple(self.redirect_chain or ()))

    # -- 判定 ---------------------------------------------------------
    @property
    def is_top_level(self) -> bool:
        """是否顶层导航（决定 Lax cookie 能否跨站发送）。

        只有 ``destination == navigate`` 才算顶层；其余（含 JS
        fetch / XHR / 子资源）都不是——旧代码在没有 context 时一律
        按顶层处理，这是审计点名的问题。
        """
        return self.destination == DEST_NAVIGATE

    @property
    def is_cross_site(self) -> bool:
        """请求站点 vs 顶层站点（schemeful same-site）。

        ``document_site`` 为空（未知）→ ``True``（保守，不发
        Lax/Strict cookie）；顶层导航自己与自己比，必然同站。
        """
        if self.is_top_level:
            # 顶层导航：站点就是自己（跨站跳转由 for_redirect 处理，
            # 跳转请求仍属于"发起它的那次导航"，站点仍取自自身）。
            return False
        doc_site = (self.document_site or "").strip()
        if not doc_site:
            return True
        req_site = _site_for_cookies(self.url)
        if not req_site:
            return True
        return req_site != doc_site

    @property
    def is_cross_origin(self) -> bool:
        """请求源 vs 顶层文档源。"""
        if not self.document_origin:
            return True
        return not same_origin(self.url, self.document_origin)

    @property
    def sends_cookies(self) -> bool:
        """凭据门禁：本次发送是否**允许**携带 cookie。

        * ``omit`` → False（跨源无凭据 fetch、无 crossorigin 的
          classic 脚本都在这里被拦住）；
        * ``same-origin`` → 仅同源；
        * ``include`` → True。
        """
        if self.credentials_mode == CRED_OMIT:
            return False
        if self.credentials_mode == CRED_INCLUDE:
            return True
        return not self.is_cross_origin

    # -- 与 jar 的界面 -------------------------------------------------
    def jar_kwargs(self) -> dict:
        """喂给 ``CookieJar.cookie_header_for`` 的三个开关（真实值）。"""
        return {"is_cross_site": self.is_cross_site,
                "method": self.method,
                "is_top_level": self.is_top_level}

    def cookie_header(self, jar) -> str:
        """向 *jar* 请求 ``Cookie`` 头；凭据门禁关闭时返回 ``""``。

        jar 抛异常（mock / 坏数据）时退化为 ``""``——P13：不允许
        cookie 判定把页面加载打挂。
        """
        if not self.sends_cookies:
            return ""
        try:
            return jar.cookie_header_for(self.url, **self.jar_kwargs()) or ""
        except Exception:
            return ""

    def ingest(self, jar, headers: dict) -> int:
        """把响应头里的 Set-Cookie 入库（GET/POST/跳转**统一**入口）。"""
        try:
            return int(jar.ingest_response(self.url, headers) or 0)
        except Exception:
            return 0

    # -- 跳转 ---------------------------------------------------------
    def for_redirect(self, location: str,
                     status: int = 0) -> "CookieRequestContext":
        """第 2 点核心：为下一跳**重新**造一个 context。

        * URL 按 RFC 3986 相对解析（``urljoin``）得到绝对地址；
        * ``document_site`` / ``document_origin`` **不变**——跳转请求
          仍属于发起它的那次文档加载，站点归属不会因为服务端指了个
          新地址就变成"同站"；
        * 跨源跳转把凭据降为 ``omit``（与
          :meth:`request_context.RequestContext.for_redirect` 同一规则：
          跨源后不再携带凭据与敏感头）；
        * ``Location`` 缺失 / 无法解析 → 原样返回（调用方应当停下）。
        """
        loc = str(location or "").strip()
        if not loc:
            return self
        try:
            nxt = urljoin(self.url or loc, loc)
        except Exception:
            return self
        if not nxt or nxt == self.url:
            return self
        cross_origin = not same_origin(nxt, self.url) \
            if self.url else True
        mode = self.credentials_mode
        if cross_origin and not self.is_top_level and mode != CRED_OMIT:
            # 跨源跳转：非导航请求降级为 omit（fetch §4.6 的保守实现：
            # 跨源后不再携带凭据与敏感头）。**顶层导航不降级**——
            # 站点自己的 cookie 跟着导航走才是浏览器行为
            # （SameSite=Lax 允许跨站顶层 GET），且 jar 的 host 匹配
            # 已经保证只发目标站自己的 cookie。
            mode = CRED_OMIT
        return replace(
            self,
            url=nxt,
            credentials_mode=mode,
            redirect_chain=self.redirect_chain + (self.url,),
        )

    # -- 可观测 -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "document_site": self.document_site or "",
            "document_origin": self.document_origin or "",
            "destination": self.destination,
            "method": self.method,
            "credentials_mode": self.credentials_mode,
            "is_top_level": self.is_top_level,
            "is_cross_site": self.is_cross_site,
            "is_cross_origin": self.is_cross_origin,
            "sends_cookies": self.sends_cookies,
            "redirect_hops": len(self.redirect_chain),
        }

    def describe(self) -> str:
        d = self.to_dict()
        flags = []
        if d["is_top_level"]:
            flags.append("top-level")
        if d["is_cross_site"]:
            flags.append("cross-site")
        if d["is_cross_origin"]:
            flags.append("cross-origin")
        return (f"cookies({d['credentials_mode']}"
                f"{',' + ','.join(flags) if flags else ''}"
                f") -> {'send' if d['sends_cookies'] else 'omit'}")


# ---------------------------------------------------------------------------
# 构造入口
# ---------------------------------------------------------------------------

def for_navigation(url: str, method: str = "GET",
                   credentials_mode: str = CRED_INCLUDE) -> CookieRequestContext:
    """顶层文档导航：站点就是请求 URL 自己（必然同站）。

    凭据默认 ``include``——导航本身就是"用户去这个站"，带上该站的
    cookie 才是浏览器行为（SameSite 过滤仍由 jar 按 cross-site 标记
    执行，而顶层导航的 cross-site 恒为 False）。
    """
    return CookieRequestContext(
        url=str(url or ""),
        document_site=_site_for_cookies(url),
        document_origin=_origin_of(url),
        destination=DEST_NAVIGATE,
        method=method,
        credentials_mode=credentials_mode,
    )


def for_subresource(url: str, document_url: str,
                    destination: str = "",
                    method: str = "GET",
                    credentials_mode: str = CRED_SAME_ORIGIN,
                    ) -> CookieRequestContext:
    """子资源 / fetch / XHR：站点与源都取**顶层文档**的。"""
    return CookieRequestContext(
        url=str(url or ""),
        document_site=_site_for_cookies(document_url),
        document_origin=_origin_of(document_url),
        destination=destination or "",
        method=method,
        credentials_mode=credentials_mode,
    )


def unknown_context(url: str, method: str = "GET") -> CookieRequestContext:
    """调用方**没有**提供文档归属时使用的保守上下文。

    ``document_site`` / ``document_origin`` 都为空 → 跨站 + 跨源 +
    凭据 ``omit`` ⇒ 不发任何 cookie。这是与旧代码"缺省同站顶层
    GET"的根本区别：**没有信息就不发**，而不是按最宽松猜。
    """
    return CookieRequestContext(
        url=str(url or ""),
        document_site="",
        document_origin="",
        destination="",
        method=method,
        credentials_mode=CRED_OMIT,
    )


def from_legacy_dict(url: str, d: Optional[dict]) -> CookieRequestContext:
    """把旧的 ``{"is_cross_site", "method", "is_top_level"}`` dict 提升为
    类型化 context（兼容 :func:`net._subresource_cookie_context` 等既有
    调用点）。

    dict 里没有凭据模式（旧调用点根本没建模 credentials）→ 取
    ``include``，让**既有行为不变**（旧代码总是发，只靠 jar 的
    Secure/SameSite 过滤）；跨站 / 顶层标记以 dict 为准。

    ``document_site`` 反推：dict 说同站就把它设成请求自己的站点
    （重算 ⇒ 同站），说跨站就置空（空 ⇒ 跨站）。这样既保留 dict
    语义，又让后续 :meth:`is_cross_site` 走同一条真实计算路径。
    """
    d = d or {}
    is_top = bool(d.get("is_top_level", False))
    cross = bool(d.get("is_cross_site", False))
    return CookieRequestContext(
        url=str(url or ""),
        document_site=_site_for_cookies(url) if (is_top or not cross) else "",
        document_origin=_origin_of(url),
        destination=DEST_NAVIGATE if is_top else "",
        method=str(d.get("method", "GET") or "GET"),
        credentials_mode=CRED_INCLUDE,
        redirect_chain=(),
    )


def from_request_context(rc) -> CookieRequestContext:
    """从 :class:`request_context.RequestContext` 派生 cookie 上下文。

    这样"请求语义"只有一份来源：destination / method / credentials /
    document_origin 全部沿用，避免两处各写一套默认值。
    """
    url = getattr(rc, "url", "") or ""
    doc_origin = getattr(rc, "document_origin", "") or ""
    dest = getattr(rc, "destination", "") or ""
    return CookieRequestContext(
        url=url,
        document_site=_site_for_cookies(doc_origin or url),
        document_origin=doc_origin,
        destination=dest,
        method=getattr(rc, "method", "GET") or "GET",
        credentials_mode=getattr(rc, "credentials", CRED_SAME_ORIGIN)
        or CRED_SAME_ORIGIN,
    )
