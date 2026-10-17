"""B02 — 把服务器响应差异做成可观察的兼容性问题（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B02（批次 20 纯逻辑落点；``HttpClient`` / GUI 接线留后续）。

与已有模块的关系：
- **B02 步骤 1**（以配置对象集中管理 User-Agent、Accept、Accept-Language、
  Cookie 策略、超时与重定向预算，文档及子资源共用，避免在多个请求函数
  里硬编码不一致的标识）由 :class:`RequestConfig` 承担。
- **B02 步骤 2**（允许显式选择兼容标识用于诊断，界面和日志记录实际
  选择；保持产品标识与能力声明一致，不把标识切换描述成引擎兼容修复）
  由 ``RequestConfig.uapick``（``default`` / ``compat`` 两种标识）+
  ``describe()`` 承担。
- **B02 步骤 3**（记录主文档状态、最终 URL、Content-Type、解码后
  大小、请求 ID 和导航链；区分"传输完成""文档已提交""首屏已绘制"
  "存在脚本错误"）由 :class:`PageDiagnostics` 承担。
- **B02 步骤 4**（对"200 + 极小脚本页面 + 没有可见内容"的组合提供
  诊断提示，不单凭正文短小判失败；空文档本身也可能是合法页面）
  由 :class:`PageDiagnostics` 的 ``diagnose_small_body`` + 诊断级别
  词表承担。
- **B02 验收**（能解释当前收到哪一种页面；完整页仍需交由正常引擎
  处理；不得以关闭证书验证或固定 HTTPS→HTTP 作为通用修复）：
  诊断只解释、不修改请求；``RequestConfig`` 不暴露"关证书 / 降级
  协议"开关。

无 Tk / 无网络 I/O；``tests/test_request_config.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- B02 步骤 2：兼容标识词表（显式选择用于诊断，非引擎兼容修复） -----------

UAP_DEFAULT = "default"
UAP_COMPAT = "compat"

# 产品标识（能力声明一致；兼容标识仅诊断用，不改变引擎能力）。
_UA_TABLE = {
    UAP_DEFAULT: "ToyBrowser/1.0 (Python stdlib engine)",
    UAP_COMPAT: "Mozilla/5.0 (compatible; ToyBrowser-compat/1.0)",
}

# 缺省 Accept / Accept-Language（文档 + 子资源共用，B02 步骤 1）。
_DEFAULT_ACCEPT = "text/html,application/xhtml+xml,*/*;q=0.8"
_DEFAULT_ACCEPT_LANG = "zh-CN,zh;q=0.9,en;q=0.8"


@dataclass
class RequestConfig:
    """B02 步骤 1/2 — 请求配置对象（文档及子资源共用）。

    - ``ua`` — 实际 User-Agent（``default`` / ``compat`` 标识，B02 步骤 2
      "显式选择兼容标识用于诊断"）。
    - ``accept`` / ``accept_language`` — 集中管理（B02 步骤 1）。
    - ``cookie_policy`` — ``send`` / ``none``（Cookie 策略集中）。
    - ``timeout`` — 超时（秒，B02 步骤 1 "超时"）。
    - ``max_redirects`` — 重定向预算（B02 步骤 1 "重定向预算"）。
    - ``request_id`` — 请求 ID（B02 步骤 3 "记录请求 ID"）。

    **不**提供关闭证书验证或固定 HTTPS→HTTP 的开关（B02 验收 "不得
    以关闭证书验证或固定将 HTTPS 改成 HTTP 作为通用修复"）。
    """
    ua: str = UAP_DEFAULT
    accept: str = _DEFAULT_ACCEPT
    accept_language: str = _DEFAULT_ACCEPT_LANG
    cookie_policy: str = "send"
    timeout: float = 30.0
    max_redirects: int = 10
    request_id: str = ""

    # -- B02 步骤 2：标识选择（默认 / 兼容） --------------------------------

    @classmethod
    def with_ua(cls, ua: str, **kw) -> "RequestConfig":
        """B02 步骤 2 — 显式选择兼容标识（用于诊断；记录实际选择）。"""
        if ua not in _UA_TABLE:
            raise ValueError(f"unknown ua pick: {ua}")
        cfg = cls(**kw)
        cfg.ua = ua
        return cfg

    @property
    def ua_value(self) -> str:
        """B02 步骤 2 — 界面 / 日志记录实际选择的标识值。"""
        return _UA_TABLE.get(self.ua, _UA_TABLE[UAP_DEFAULT])

    def describe(self) -> str:
        """B02 步骤 2 — 描述实际选择（供日志 / 界面；不把切换描述成
        引擎兼容修复）。"""
        return (f"ua={self.ua}({self.ua_value}) "
                f"cookie={self.cookie_policy} "
                f"timeout={self.timeout}s "
                f"max-redirects={self.max_redirects} "
                f"accept-lang={self.accept_language}")

    # -- B02 步骤 1：子资源共用配置（继承文档配置） ----------------------

    def for_subresource(self) -> "RequestConfig":
        """B02 步骤 1 — 子资源共用同一配置（避免硬编码不一致标识）。"""
        sub = RequestConfig(ua=self.ua, accept="*/*",
                           accept_language=self.accept_language,
                           cookie_policy=self.cookie_policy,
                           timeout=self.timeout,
                           max_redirects=self.max_redirects,
                           request_id=self.request_id + ".sub")
        return sub


# ---------------------------------------------------------------------------
# B02 步骤 3：主文档诊断（区分 传输完成 / 文档已提交 / 首屏已绘制 / 脚本错误）
# ---------------------------------------------------------------------------

# 诊断级别词表（B02 步骤 3 "区分"四种状态"）。
DIAG_TRANSPORT_DONE = "transport-done"       # 传输完成
DIAG_DOC_COMMITTED = "doc-committed"         # 文档已提交
DIAG_FIRST_SCREEN = "first-screen"           # 首屏已绘制
DIAG_SCRIPT_ERROR = "script-error"          # 存在脚本错误

# B02 步骤 4：小正文诊断级别（不判失败，提示"可能合法"）。
DIAG_LEGITIMATE_EMPTY = "legitimate-empty"   # 空文档本身可能合法
DIAG_SCRIPT_REDIRECT = "script-redirect"     # 200 + 极小脚本跳转页
DIAG_NO_VISIBLE_CONTENT = "no-visible-content"  # 无可见内容（需脚本执行）
DIAG_FULL_PAGE = "full-page"                 # 完整页（交由正常引擎）


@dataclass
class PageDiagnostics:
    """B02 步骤 3/4 — 主文档可观察诊断（解释"当前收到哪一种页面"）。

    记录：主文档状态码 / 最终 URL / Content-Type / 解码后大小 / 请求 ID /
    导航链。区分"传输完成""文档已提交""首屏已绘制""存在脚本错误"。
    对"200 + 极小脚本页面 + 没有可见内容"提供诊断提示（B02 步骤 4），
    不单凭正文短小判失败；空文档本身也可能是合法页面。
    """
    status_code: int = 0
    final_url: str = ""
    content_type: str = ""
    decoded_size: int = 0
    request_id: str = ""
    nav_chain: list = field(default_factory=list)
    transport_done: bool = False
    doc_committed: bool = False
    first_screen: bool = False
    has_script_error: bool = False
    visible_content: bool = False
    diagnosis: str = ""

    # -- B02 步骤 3：状态标记（区分四种状态） ------------------------------

    def mark_transport_done(self) -> None:
        self.transport_done = True

    def mark_doc_committed(self) -> None:
        self.doc_committed = True

    def mark_first_screen(self) -> None:
        self.first_screen = True

    def mark_script_error(self) -> None:
        self.has_script_error = True

    def mark_visible_content(self, visible: bool) -> None:
        self.visible_content = visible

    def add_nav_hop(self, url: str) -> None:
        self.nav_chain.append(url)

    # -- B02 步骤 4：小正文 / 可见内容诊断 --------------------------------

    def diagnose_small_body(self, small_threshold: int = 2048) -> str:
        """B02 步骤 4 — 对"200 + 极小脚本页面 + 没有可见内容"提供诊断
        提示，不单凭正文短小判失败；空文档本身也可能是合法页面。

        优先级：
        1. 有可见内容 → ``full-page``（完整页，交由正常引擎处理）。
        2. 200 + 极小 + 无可见内容 + 有脚本错误 → ``script-redirect``
           （脚本跳转页，诊断"未执行脚本"）。
        3. 200 + 极小 + 无可见内容 + 文档未提交 → ``legitimate-empty``
           （空文档可能合法，不判失败）。
        4. 200 + 极小 + 无可见内容 + 文档已提交 → ``no-visible-content``
           （需脚本执行才能显示；不是"页面失败"）。
        5. 非 200 → 由调用方用 ``status_code`` 判错误页。
        """
        if self.visible_content:
            self.diagnosis = DIAG_FULL_PAGE
            return self.diagnosis
        if self.status_code == 200 and self.decoded_size <= small_threshold:
            if self.has_script_error:
                self.diagnosis = DIAG_SCRIPT_REDIRECT
            elif not self.doc_committed:
                # 文档尚未提交 → 空文档本身可能合法。
                self.diagnosis = DIAG_LEGITIMATE_EMPTY
            else:
                self.diagnosis = DIAG_NO_VISIBLE_CONTENT
        else:
            self.diagnosis = DIAG_NO_VISIBLE_CONTENT
        return self.diagnosis

    def explain(self) -> str:
        """B02 验收 — 能解释"当前收到哪一种页面"。

        输出诊断级别 + 导航链摘要 + 四状态标记；完整页（full-page）
        仍需交由正常引擎处理（本模块不修改请求，B02 验收）。
        """
        if not self.diagnosis:
            self.diagnose_small_body()
        state_flags = []
        if self.transport_done:
            state_flags.append("transport-done")
        if self.doc_committed:
            state_flags.append("doc-committed")
        if self.first_screen:
            state_flags.append("first-screen")
        if self.has_script_error:
            state_flags.append("script-error")
        chain = "→".join(self.nav_chain) if self.nav_chain else "single"
        return (f"diagnosis={self.diagnosis} "
                f"status={self.status_code} "
                f"ct={self.content_type} "
                f"size={self.decoded_size} "
                f"rid={self.request_id} "
                f"chain=[{chain}] "
                f"states={'+'.join(state_flags) or 'none'}")

    def is_legitimate_empty(self) -> bool:
        """B02 步骤 4 — 空文档本身也可能是合法页面（不判失败）。"""
        return self.diagnosis == DIAG_LEGITIMATE_EMPTY

    def needs_script_execution(self) -> bool:
        """B02 步骤 4 — 无可见内容但需脚本执行才能显示（不是失败）。"""
        return self.diagnosis in (DIAG_NO_VISIBLE_CONTENT,
                                 DIAG_SCRIPT_REDIRECT)
