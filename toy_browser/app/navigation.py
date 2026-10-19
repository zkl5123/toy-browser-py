"""B01 + B05 — 统一导航控制器与 location 导航语义（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B01 / B05（第五轮批次 17 纯逻辑落点；GUI / 活显示接线留后续批次）。

B01（统一导航入口）
==================
- :class:`NavRequest` — 导航请求：源文档、目标 URL、触发方式（source）、
  是否替换历史（replace）、导航序号（seq，单调，用于丢弃旧任务）。
  本地文件 / about 页 / http(s) 经 :meth:`NavigationController.dispatch`
  的**同一入口**分派（B01 步骤 1）。
- :class:`NavigationController` — 地址栏输入分类（B01 步骤 2/3）：
  URL / 相对 URL / 普通文本；相对 URL 基于当前文档基址解析；
  搜索引擎补全是**独立策略钩子**（``search_url_for``），不是把所有
  未知输入当本地文件。提交成功统一更新地址栏 / 标题 / 当前 URL / 历史
  （B01 步骤 4）；失败保留请求目标与错误类型（不静默改界面文本）。

B05（location 与文档 URL 的导航语义）
====================================
- :class:`Location` — 替换 ``web_api.make_location`` 的纯字典：
  宿主对象读取**当前已提交文档 URL**（构造时传入 ``doc_url``，
  B05 步骤 1），支持 href 赋值 / ``assign`` / ``replace`` / ``reload``
  （B05 步骤 2）——调用**转成导航任务**（返回 :class:`NavRequest`），
  不在 JS 侧同步递归加载整个网页。
- 相对目标与最终响应 URL 使用 stdlib URL 解析（B05 步骤 3）：
  ``replace`` 替换当前历史项、``assign`` 新建历史项；**同文档 hash 导航
  与跨文档加载分开**（:meth:`Location.is_same_doc` / :meth:`resolve_target`）。
- :class:`NavBudget`（B05 步骤 4）：HTTP 重定向 / 脚本跳转 / meta
  refresh 共用导航次数与时间预算 + 取消标记；:meth:`NavigationController.
  record_hop` 在预算耗尽时拒绝继续（循环跳转保护）。

所有类型均为纯逻辑（无 Tk / 无网络 I/O），测试用
``tests/test_navigation.py`` 覆盖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

# --- B01 触发方式（source）词表 ------------------------------------------------

SRC_LAUNCH = "launch"        # 启动参数（B01 步骤 2：initial_path 经事件队列）
SRC_ADDRESS = "address"      # 地址栏
SRC_LINK = "link"            # 页面内链接
SRC_SCRIPT = "script"       # location / 脚本导航（B05）
SRC_REDIRECT = "redirect"   # HTTP 重定向
SRC_META = "meta"           # meta refresh
SRC_BOOKMARK = "bookmark"
SRC_FILE = "file"

_VALID_SOURCES = {SRC_LAUNCH, SRC_ADDRESS, SRC_LINK, SRC_SCRIPT,
                  SRC_REDIRECT, SRC_META, SRC_BOOKMARK, SRC_FILE}


def _scheme_of(url: str) -> str:
    m = re.match(r"\s*([a-zA-Z][a-zA-Z0-9+.\-]*)://", url or "")
    return (m.group(1).lower() if m else "")


def _is_absolute_url(url: str) -> bool:
    return bool(_scheme_of(url))


def _is_localish(url: str) -> bool:
    """B01 步骤 2 — 本地文件 / about 页识别（不丢 URL，仅分类）。"""
    s = (url or "").strip()
    if not s:
        return False
    low = s.lower()
    if low.startswith("about:"):
        return True
    if low.startswith("file://") or low.startswith("file:"):
        return True
    if not _is_absolute_url(low):
        # 相对 / 裸路径 / 文件名（不含 scheme，不含 " " 且不是裸文本 URL）
        # 归本地；裸文本（含空格或无路径特征）由 classify_address_input 区分。
        return "/" in low or "\\" in low or low.endswith((".html", ".htm", ".js", ".css"))
    return False


def _is_url_like(s: str) -> bool:
    """B01 批次 55 — 启动参数"URL 形态"判定（与 ``classify_address_input``
    的"网址"分支同源，保证启动入口与地址栏入口语义一致）。

    含空格的字符串视为普通文本（**不**是 URL 形态）。其余满足任一即
    视为 URL 形态：
    - 绝对 URL（含 ``://`` scheme，``http://`` / ``https://`` 等）；
    - ``about:`` / ``file:`` / 任意 ``scheme:`` 协议形式；
    - 含路径 / 查询特征（``/`` 或 ``?``）；
    - 裸主机名（形如 ``localhost:8000`` / ``example.com`` /
      ``www.baidu.com``）。

    纯文本（如 ``hello world`` / ``foo bar``）**不**视为 URL 形态 ——
    启动参数不应被误当 URL 或本地文件导航，回落 ``about:blank``。
    """
    s = (s or "").strip()
    if not s:
        return False
    if " " in s:
        return False  # 含空格 = 普通文本，非 URL 形态
    if _is_absolute_url(s):
        return True
    low = s.lower()
    if low.startswith("about:") or low.startswith("file:"):
        return True
    if "/" in s or "?" in s:
        return True
    # 裸主机名：``[name]:[port]`` 或 ``domain``（形如 localhost:8000 /
    # example.com / www.baidu.com）——字母数字 . - : 组成且无其它杂字符。
    if re.fullmatch(r"[a-zA-Z0-9.\-]+(:[0-9]+)?", s):
        return True
    return False



@dataclass
class NavRequest:
    """B01 步骤 1 — 导航请求对象（统一入口的最小契约）。

    - ``target``      目标 URL（相对形式允许，dispatch 时解析）
    - ``source``      触发方式（见 SRC_* 词表）
    - ``replace``     是否替换当前历史项（True = replace / 重定向）
    - ``seq``         导航序号（单调；Controller 分配，旧 seq 结果丢弃）
    - ``origin_url``  发起导航的源文档 URL（重定向 / 脚本跳转的上下文）
    """
    target: str
    source: str = SRC_ADDRESS
    replace: bool = False
    seq: int = 0
    origin_url: str = ""
    error: Optional[str] = None   # 请求失败时保留错误类型（B01 步骤 4）

    def kind(self) -> str:
        s = _scheme_of(self.target)
        if s in ("http", "https"):
            return "web"
        if s == "file":
            return "file"
        if self.target and self.target.strip().lower().startswith("about:"):
            return "about"
        return "local" if _is_localish(self.target) else "unknown"

    def summary(self) -> str:
        return (f"[nav seq={self.seq} {self.kind()} src={self.source}"
                f"{' replace' if self.replace else ''}] {self.target}"
                + (f" ({self.error})" if self.error else ""))


@dataclass
class NavOutcome:
    """B01 步骤 4 — 提交结果（成功 / 失败各自的结构化状态）。"""
    ok: bool
    request: NavRequest
    final_url: str = ""
    error: Optional[str] = None
    committed: bool = False   # "文档已提交"（区别于"传输完成"）

    @property
    def seq(self) -> int:
        return self.request.seq


class NavigationController:
    """B01 — 启动参数 / 地址栏 / 链接共用的统一导航控制器（纯逻辑）。

    历史 = 与 ``HistoryCommitter``（批次 8）同语义的游标栈（back/fwd 游标
    移动、新导航清空前进栈、同址去重原地更新），此处保持最小子集以便
    location 语义（B05 的 assign/replace）直接消费；真实 GUI 接线复用
    ``HistoryCommitter``。
    """

    def __init__(self, initial_url: str = ""):
        self.current_url: str = initial_url or "about:blank"
        self.title: str = ""
        self.address_text: str = initial_url or ""
        self._seq: int = 0
        # 历史（URL 项；与 HistoryCommitter 的游标语义一致的最小子集）
        # 初始项 = 当前文档（about:blank 或启动 URL）。
        self._back: list[str] = [self.current_url]
        self._fwd: list[str] = []
        self._cursor_pos: int = 0
        self._last_error: Optional[str] = None
        self._hops_this_nav: int = 0
        self._active_nav_seq: Optional[int] = None

    # -- B01 步骤 1：统一分派入口 ------------------------------------------------

    def dispatch(self, request: NavRequest) -> NavOutcome:
        """本地文件 / about / http(s) 经同一入口分派（纯逻辑提交）。

        - 分配 ``seq``（单调）；旧 seq 的在途结果由调用方经
          :meth:`drop_stale` 丢弃。
        - 成功：更新地址栏 / 标题 / 当前 URL / 历史（B01 步骤 4）。
        - 失败：``NavOutcome.ok=False`` + ``error`` 保留请求目标与错误
          类型（**不**改界面文本）。
        """
        self._seq += 1
        request.seq = self._seq
        self._active_nav_seq = self._seq
        self._hops_this_nav = 0
        # 失败路径（B01 步骤 4 "保留请求目标与错误类型"）：不改历史游标。
        if request.error is not None:
            self._last_error = request.error
            self.address_text = request.target
            return NavOutcome(ok=False, request=request, final_url=request.target,
                              error=request.error, committed=False)
        # 纯逻辑：分派即"提交"（真实网络 I/O 属后续批次；此处记录目标
        # 与分类，地址栏/历史按成功语义更新）。
        self._last_error = None
        self.address_text = request.target
        self._commit_history(request, ok=True)
        return NavOutcome(ok=True, request=request, final_url=request.target,
                          committed=True)

    def drop_stale(self, seq: int) -> bool:
        """B01 / B10 — 旧导航序号的结果丢弃（真值 = 该 seq 已过期）。"""
        if self._active_nav_seq is not None and seq != self._active_nav_seq:
            return True
        return False

    def _commit_history(self, req: NavRequest, ok: bool) -> None:
        if not ok:
            # B01 步骤 4 — 失败：保留请求目标与错误类型，不改历史游标。
            self._last_error = req.error
            return
        if req.replace:
            # replace（location.replace / 重定向）：原地替换当前项，游标不动。
            self._back[self._cursor_pos] = req.target
            self.current_url = req.target
        else:
            # assign（启动 / 地址栏 / 链接 / location.assign）：新建项，清前进栈。
            self._back.append(req.target)
            self._cursor_pos += 1
            self._fwd.clear()
            self.current_url = req.target

    # -- B01 步骤 2：启动导航 ----------------------------------------------------

    def launch(self, initial_path: str) -> NavRequest:
        """B01 步骤 2 — 启动参数经事件队列提交（本地 / about / http 同入口）。"""
        return NavRequest(target=initial_path, source=SRC_LAUNCH)

    # -- B01 步骤 3：地址栏输入分类 ----------------------------------------------

    def classify_address_input(self, text: str, search_url_for=None) -> NavRequest:
        """B01 步骤 3 — 地址栏输入三分类。

        - **网址**（含 scheme 的绝对 URL）→ 原样导航；
        - **相对 URL**（相对当前文档基址可解析、且带路径 / 查询特征）
          → ``urljoin`` 到当前文档后导航；
        - **普通文本** → 搜索引擎补全（``search_url_for(text)`` 策略钩子，
          B01 步骤 3 "搜索引擎地址补全作为独立产品策略"；未提供钩子时
          返回目标为原文、source=address 的失败分类，**不当本地文件**）。
        """
        s = (text or "").strip()
        if not s:
            return NavRequest(target="", source=SRC_ADDRESS, error="empty-input")
        if _is_absolute_url(s):
            return NavRequest(target=s, source=SRC_ADDRESS)
        # 含 ":// " 的协议相对 / 裸文本带 scheme 前缀
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", s):
            return NavRequest(target=s, source=SRC_ADDRESS)
        # 相对 URL：相对当前文档基址解析（B05 步骤 3 共用 URL 解析器）。
        resolved = urljoin(self.current_url, s)
        # about:blank / 无基址时 urljoin 回退为原文：只有真正解析出基址
        # （resolved != s）或带路径 / 查询特征才算相对 URL；否则归普通文本。
        if resolved != s:
            return NavRequest(target=resolved, source=SRC_ADDRESS)
        if any(ch in s for ch in "/?") or "." in s.split("/")[-1]:
            return NavRequest(target=s, source=SRC_ADDRESS)
        # 普通文本 → 搜索策略钩子（独立产品策略，B01 步骤 3）。
        if search_url_for is not None:
            target = search_url_for(s)
            if target:
                return NavRequest(target=target, source=SRC_ADDRESS)
        return NavRequest(target=s, source=SRC_ADDRESS, error="plain-text-no-search-policy")

    # -- B01 步骤 4：失败重试 / 状态 ---------------------------------------------

    def last_error(self) -> Optional[str]:
        return self._last_error

    def can_back(self) -> bool:
        return self._cursor_pos > 0

    def can_forward(self) -> bool:
        return self._cursor_pos < len(self._back) - 1

    def back(self) -> Optional[str]:
        if not self.can_back():
            return None
        self._cursor_pos -= 1
        self.current_url = self._back[self._cursor_pos]
        return self.current_url

    def forward(self) -> Optional[str]:
        if not self.can_forward():
            return None
        self._cursor_pos += 1
        self.current_url = self._back[self._cursor_pos]
        return self.current_url

    # -- B05 步骤 4：跳转预算（重定向 / 脚本跳转 / meta refresh 共用） -----------

    def record_hop(self, req: NavRequest, budget: "NavBudget") -> NavRequest:
        """一次跳转（重定向 / 脚本 / meta）计入预算；耗尽则拒绝继续。"""
        self._hops_this_nav += 1
        if budget.exhausted(self._hops_this_nav):
            req.error = "nav-budget-exhausted"
            self._last_error = "nav-budget-exhausted"
            return req
        return req


@dataclass
class NavBudget:
    """B05 步骤 4 — 导航次数 / 时间预算 + 取消标记（共用）。"""
    max_hops: int = 10            # 跳转次数预算（循环跳转保护）
    max_seconds: float = 30.0     # 时间预算
    cancelled: bool = False

    def exhausted(self, hops: int) -> bool:
        return self.cancelled or hops > self.max_hops


def select_pending_navigation_target(
    pending_nav,
    base_url: str,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
) -> Optional[str]:
    """R7-A01 — 统一 B05 pending navigation follow 契约。

    CLI/GUI 不再各自重复“seen 去重 + 协议过滤 + reload 不跟跳”规则：
    所有脚本同步产生的导航都经此函数解析出唯一可跟跳目标。

    * ``pending_nav`` — host._pending_nav（str 或 NavRequest 混排）
    * ``base_url``    — 当前已提交文档 URL（reload 判定基准）
    * ``allowed_schemes`` — 允许跟跳的协议（默认 http/https）

    返回首个可跟跳目标，或 None（不跟跳 / 全为 reload / 全被过滤）。
    """
    seen = {base_url}
    allowed = tuple(s.lower() for s in allowed_schemes)
    for req in list(pending_nav or []):
        target = getattr(req, "target", req) if not isinstance(req, str) else req
        if not target:
            continue
        if target in seen:
            continue
        low = target.lower()
        # 协议过滤：仅允许 http/https（与 CLI 既有 B05 段同契约）。
        scheme_hit = any(low.startswith(f"{s}://") for s in allowed)
        if not scheme_hit:
            continue
        if target == base_url:
            continue  # reload：同文档非跨文档导航，不跟跳
        seen.add(target)
        return target
    return None


# ---------------------------------------------------------------------------
# B05 — Location：location 与文档 URL 的导航语义
# ---------------------------------------------------------------------------

class Location:
    """B05 — 替换 ``web_api.make_location`` 纯字典的宿主 location 对象。

    - 读取**当前已提交文档 URL**（``doc_url``，构造来源唯一，B05 步骤 1
      "核对 host.url 与 host.doc_url 的不同使用位置，统一初始化来源"）；
    - ``href`` 赋值 / ``assign`` / ``replace`` / ``reload`` 返回
      :class:`NavRequest`（导航任务，B05 步骤 2 "禁止在 JS getter/setter
      中同步递归加载整个网页"——调用方 / 控制器消费该任务）；
    - 相对目标用 stdlib URL 解析（B05 步骤 3）：``replace`` 替换历史项、
      ``assign`` 新建历史项；同文档 hash 导航与跨文档加载分开
      （:meth:`is_same_doc`）。
    """

    def __init__(self, doc_url: str = "", controller: Optional[NavigationController] = None):
        self._url = doc_url or "about:blank"
        self._ctrl = controller

    # -- B05 步骤 1：统一初始化来源 ---------------------------------------------

    @property
    def href(self) -> str:
        return self._url

    def _set_url(self, url: str) -> None:
        self._url = url

    @property
    def protocol(self) -> str:
        p = urlparse(self._url)
        return (p.scheme or "about") + ":"

    @property
    def host(self) -> str:
        p = urlparse(self._url)
        return p.netloc or ""

    @property
    def hostname(self) -> str:
        p = urlparse(self._url)
        return p.hostname or ""

    @property
    def port(self) -> str:
        p = urlparse(self._url)
        return str(p.port) if p.port else ""

    @property
    def pathname(self) -> str:
        p = urlparse(self._url)
        return p.path or "/"

    @property
    def search(self) -> str:
        p = urlparse(self._url)
        return ("?" + p.query) if p.query else ""

    @property
    def hash(self) -> str:
        p = urlparse(self._url)
        return ("#" + p.fragment) if p.fragment else ""

    @property
    def origin(self) -> str:
        p = urlparse(self._url)
        if p.scheme in ("http", "https"):
            return f"{p.scheme}://{p.netloc}"
        return "about:blank" if not p.netloc else ""

    # -- B05 步骤 2：assign / replace / reload / href 赋值 -> 导航任务 -----------

    def assign(self, url: str) -> NavRequest:
        """新建历史项（B05 步骤 3）。同文档 hash 不新建。"""
        target = self.resolve_target(url)
        same = self.is_same_doc(target, ignore_fragment=True)
        req = NavRequest(target=target, source=SRC_SCRIPT, replace=False)
        if self._ctrl is not None:
            self._ctrl.dispatch(req)
        # 同文档 hash：更新本地 URL（不提交跨文档导航）
        if same:
            self._set_url(target)
        return req

    def replace(self, url: str) -> NavRequest:
        """替换当前历史项（B05 步骤 3；百度 227 字节页 location.replace 语义）。"""
        target = self.resolve_target(url)
        req = NavRequest(target=target, source=SRC_SCRIPT, replace=True)
        if self._ctrl is not None:
            self._ctrl.dispatch(req)
        if self.is_same_doc(target, ignore_fragment=True):
            self._set_url(target)
        return req

    def reload(self) -> NavRequest:
        req = NavRequest(target=self._url, source=SRC_SCRIPT, replace=True)
        if self._ctrl is not None:
            self._ctrl.dispatch(req)
        return req

    def set_href(self, url: str) -> NavRequest:
        """href 赋值 = assign 语义（JS ``location.href = ...``）。"""
        return self.assign(url)

    # -- B05 步骤 3：相对目标解析 + 同文档判定 ----------------------------------

    def resolve_target(self, target: str) -> str:
        """相对目标基于当前文档 URL 解析（stdlib URL 解析器）。"""
        t = (target or "").strip()
        if not t:
            return self._url
        if _is_absolute_url(t) or t.lower().startswith("about:") or t.lower().startswith("file:"):
            return t
        return urljoin(self._url, t)

    def is_same_doc(self, target: str, ignore_fragment: bool = False) -> bool:
        """同文档（hash 导航）判定：protocol + host + path(+query) 相同。

        B05 步骤 3 "hash 同文档导航与跨文档加载分开"：ignore_fragment=True
        时 ``#`` 差异不算跨文档。
        """
        a, b = urlparse(self._url), urlparse(self.resolve_target(target))
        if (a.scheme, a.netloc, a.path) != (b.scheme, b.netloc, b.path):
            return False
        if not ignore_fragment:
            return (a.query, a.fragment) == (b.query, b.fragment)
        return a.query == b.query

    def as_dict(self) -> dict:
        """与 ``web_api.make_location`` 同键的纯字典投影（兼容旧消费方）。"""
        return {
            "href": self.href, "protocol": self.protocol, "host": self.host,
            "hostname": self.hostname, "port": self.port,
            "pathname": self.pathname, "search": self.search,
            "hash": self.hash, "origin": self.origin,
        }

    def __repr__(self) -> str:
        return f"Location({self.href!r})"
