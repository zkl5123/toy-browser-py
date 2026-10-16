"""G08 — 导航状态、停止与错误（状态机 + UI 状态映射）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G08：

1. 建立 ``idle / loading / interactive / complete / failed /
   cancelled`` 状态；记录当前 URL、最终 URL、主文档与资源进度。
2. UI 立即显示 loading，刷新按钮变停止；完成、失败和取消分别
   更新状态，不统一写成 "Loaded"。
3. Stop 先逻辑取消并回复 UI，再请求可取消 transport 关闭/超时；
   不强杀 Python 工作线程。
4. 错误页区分 DNS / TLS / 超时 / HTTP / 脚本错误；给重试入口，
   控制台保留原始错误。
5. 缓存命中、跳转和失败不得把地址栏正在编辑的文字意外覆盖；
   失焦/导航提交后再同步最终 URL。

本模块是纯逻辑状态机（不依赖 tkinter / 网络），供
:class:`TabBrowser` 与各 UI 状态映射调用；G07 的
:class:`DocumentController` 用它携带每个工作结果的 C01 文档/帧
版本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ----------------------------------------------------------------------
# 状态常量
# ----------------------------------------------------------------------
IDLE = "idle"
LOADING = "loading"
INTERACTIVE = "interactive"
COMPLETE = "complete"
FAILED = "failed"
CANCELLED = "cancelled"

VALID_STATES = (IDLE, LOADING, INTERACTIVE, COMPLETE, FAILED, CANCELLED)


@dataclass
class NavigationState:
    """G08 — 单个标签页的导航状态机。

    * ``state`` — 当前状态（见模块常量）。
    * ``current_url`` — 正在加载 / 已加载的 URL。
    * ``final_url`` — 重定向链的最终 URL（无重定向时 = current_url）。
    * ``error_category`` — 失败时的分类（"dns" / "connect" /
      "timeout" / "http" / "tls" / "redirect" / "bad_url" /
      "script" / ""），供 UI 错误页与状态栏摘要。
    * ``progress`` — 0.0–1.0 完成度（仅 ``PHASE_DONE`` = 1.0）。
    * ``cancel_requested`` — Stop 已按下（UI 立即反馈；transport
      在下一个可取消点关闭/超时，不强杀工作线程）。
    * ``epoch`` — 本次导航的导航序号（与 C01 ``navigation_epoch``
      对应；收到更早 epoch 的结果时丢弃）。
    """

    state: str = IDLE
    current_url: str = ""
    final_url: str = ""
    error_category: str = ""
    progress: float = 0.0
    cancel_requested: bool = False
    epoch: int = 0
    # 状态变化日志（诊断用；(old_state, new_state, url) 三元组）。
    transitions: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # 状态转移（G08 step 2：完成 / 失败 / 取消分别更新）
    # ------------------------------------------------------------------
    def start_navigation(self, url: str, epoch: int) -> None:
        """G08 — 一次导航开始：立刻进入 LOADING（UI 立即反馈，
        刷新按钮变停止）。"""
        self._transition(LOADING, url)
        self.current_url = url
        self.final_url = url
        self.epoch = epoch
        self.error_category = ""
        self.progress = 0.0
        self.cancel_requested = False

    def mark_interactive(self) -> None:
        """主文档已渲染、可交互（子资源可能仍在拉取）。"""
        self._transition(INTERACTIVE, self.current_url)

    def complete(self, final_url: Optional[str] = None,
                 progress: float = 1.0) -> None:
        """加载成功：COMPLETE（区分于 legacy 统一写 "Loaded"）。"""
        self._transition(COMPLETE, self.current_url)
        if final_url:
            self.final_url = final_url
        self.progress = progress

    def fail(self, category: str, url: Optional[str] = None) -> None:
        """加载失败：FAILED + 错误分类（DNS / 超时 / HTTP / TLS / …）。"""
        self._transition(FAILED, url or self.current_url)
        self.error_category = category
        self.progress = 0.0

    def cancel(self, url: Optional[str] = None) -> None:
        """Stop：先逻辑取消（UI 立即反馈），再请求 transport 关闭。

        这里只置标志 + 转 CANCELLED；真正的 socket 关闭由
        transport 在下一个可取消点执行（G08 step 3，不强杀线程）。
        """
        self._transition(CANCELLED, url or self.current_url)
        self.cancel_requested = True

    def cancel_request(self) -> None:
        """UI 侧：按下 Stop。立即把状态翻到 CANCELLED，
        transport 看到 ``cancel_requested`` 后尽快收尾。"""
        if self.state in (COMPLETE, FAILED, CANCELLED, IDLE):
            return  # 无进行中导航可取消
        self.cancel_requested = True
        self._transition(CANCELLED, self.current_url)
    # ------------------------------------------------------------------
    # UI 状态映射（G08 step 2）
    # ------------------------------------------------------------------
    @property
    def is_busy(self) -> bool:
        """处于 LOADING / INTERACTIVE（刷新按钮显示为"停止"）。"""
        return self.state in (LOADING, INTERACTIVE)

    @property
    def stop_button_visible(self) -> bool:
        return self.is_busy

    def ui_label(self) -> str:
        """状态栏 / 地址栏前缀文本（区分完成 / 失败 / 取消，
        不统一写成 "Loaded"）。"""
        return {
            IDLE: "Ready",
            LOADING: "Loading",
            INTERACTIVE: "Interactive",
            COMPLETE: "Loaded",
            FAILED: "Failed",
            CANCELLED: "Stopped",
        }.get(self.state, "Ready")

    def can_retry(self) -> bool:
        """失败 / 取消后可重试。"""
        return self.state in (FAILED, CANCELLED)

    # ------------------------------------------------------------------
    # 结果丢弃（G08 step 3 + G07：旧 epoch 的结果不覆盖新导航）
    # ------------------------------------------------------------------
    def is_stale_result(self, result_epoch: int) -> bool:
        """一个加载结果携带的 epoch 比当前导航旧 → 丢弃（
        A 文档的结果不写入 B 文档，G07 step 6）。"""
        return result_epoch < self.epoch

    def accept_result(self, result_epoch: int, ok: bool,
                      error_category: str = "",
                      final_url: str = "") -> bool:
        """G07 step 6 — 收到异步加载结果：旧 epoch / 已取消时丢弃，
        否则更新 COMPLETE / FAILED。返回是否被应用。"""
        if self.is_stale_result(result_epoch):
            return False
        if self.cancel_requested:
            return False
        if ok:
            self.complete(final_url=final_url)
        else:
            self.fail(error_category, url=self.current_url)
        return True

    # ------------------------------------------------------------------
    def _transition(self, new_state: str, url: str) -> None:
        old = self.state
        self.state = new_state
        if new_state == IDLE:
            self.current_url = ""
            self.final_url = ""
            self.progress = 0.0
        self.transitions.append((old, new_state, url))

    def reset(self) -> None:
        """G08 — 回到 idle（关闭标签 / 新会话）。"""
        self.state = IDLE
        self.current_url = ""
        self.final_url = ""
        self.error_category = ""
        self.progress = 0.0
        self.cancel_requested = False
        self.epoch = 0
        self.transitions.clear()


# ----------------------------------------------------------------------
# 错误分类辅助（与 net.categorize_fetch_error 的分类词表对齐）
# ----------------------------------------------------------------------
ERROR_CATEGORY_LABELS = {
    "dns": "DNS 解析失败",
    "connect": "无法连接服务器",
    "timeout": "请求超时",
    "http": "HTTP 错误",
    "tls": "TLS 证书错误",
    "redirect": "重定向循环",
    "bad_url": "无效 URL",
    "script": "脚本错误",
    "": "加载失败",
}


def describe_error(state: NavigationState) -> str:
    """G08 step 4 — 状态栏 / 诊断区的失败摘要（控制台保留原始错误）。"""
    if state.state != FAILED:
        return ""
    return f"{ERROR_CATEGORY_LABELS.get(state.error_category, '加载失败')}：" \
           f"{state.current_url}"
