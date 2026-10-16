"""G07 — UI 与文档工作分离（有界命令队列 + 任务版本丢弃）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G07：

1. ``DocumentController``：UI 只投递 Navigate / Input / Resize /
   Scroll / Stop 命令，持有有限状态镜像（:class:`NavigationState`）。
2. DOM、JS Host、布局树由一个串行文档工作单元拥有（本骨架：
   单线程命令队列；初版把网络 I/O 移出 UI 调用点，CPU 工作位置
   留待文档进程方案，G07 step 4 明确纯 Python 线程无多核收益）。
3. Tk 控件与 PhotoImage 保留在主线程：命令结果携带 C01 文档 / 帧
   版本，主线程经队列取回后才应用（``drain`` 返回待应用的
   :class:`Result` 列表）。
4. 队列有界（``max_pending``）；旧 resize / scroll 合并，呈现只保留
   最新有效帧。
5. 所有结果携带 C01 的 ``document_id`` / ``navigation_epoch`` /
   ``frame_id``；收到已关闭标签或过期导航的结果立即丢弃
   （:meth:`NavigationState.accept_result` 的门控）。

本模块是 *骨架*：命令类型、结果类型、有界队列与丢弃策略在此定义
并可测试；实际的线程分离 / 网络 I/O 外移 / 文档进程是批次 3 的
实施（依赖 G08 完整的 transport 可取消性）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.document_state import DocumentVersion
from toy_browser.app.navigation_state import (
    NavigationState, IDLE, LOADING, COMPLETE, FAILED, CANCELLED,
)


# ----------------------------------------------------------------------
# 命令类型（UI → 文档工作单元）
# ----------------------------------------------------------------------
CMD_NAVIGATE = "navigate"
CMD_INPUT = "input"
CMD_RESIZE = "resize"
CMD_SCROLL = "scroll"
CMD_STOP = "stop"

VALID_COMMANDS = (CMD_NAVIGATE, CMD_INPUT, CMD_RESIZE, CMD_SCROLL, CMD_STOP)


@dataclass
class Command:
    """UI 投递的一条命令（G07 step 1）。

    * ``nav_epoch`` — 该命令发起时的导航序号（结果携带它做丢弃
      门控；C01 ``navigation_epoch``）。
    * ``document_id`` — 该命令针对的文档（C01；关闭标签后旧文档
      的结果全部丢弃）。
    """
    kind: str
    payload: dict = field(default_factory=dict)
    nav_epoch: int = 0
    document_id: int = 0


@dataclass
class Result:
    """文档工作单元产出的结果（G07 step 3：主线程经队列取回）。

    ``document_id`` / ``frame_id`` 是 C01 版本；主线程应用前用
    :meth:`DocumentVersion.is_stale_frame` 判断是否已被更新导航
    取代，过期结果立即丢弃（不写回 DOM / 不刷新画布）。
    """
    ok: bool
    nav_epoch: int
    document_id: int
    frame_id: int = 0
    payload: dict = field(default_factory=dict)
    error_category: str = ""
    final_url: str = ""

    def is_stale(self, version: DocumentVersion) -> bool:
        """C01 — 该结果渲染的帧 / 文档比当前版本旧 → 丢弃。"""
        return version.is_stale_frame(self.document_id, self.frame_id)


# ----------------------------------------------------------------------
# 有界命令队列（G07 step 5：旧 resize / scroll 合并，只保留最新）
# ----------------------------------------------------------------------

class CommandQueue:
    """G07 — 有界命令队列：导航 / 输入 / 停止按序保留，
    resize / scroll 连续合并（只保留最新一条）。

    ``max_pending`` 是上限（内存治理）；超限的导航 / 输入命令被
    拒绝（返回 False），避免无界增长。
    """

    def __init__(self, max_pending: int = 64) -> None:
        self.max_pending = max_pending
        self._queue: list[Command] = []

    def post(self, cmd: Command) -> bool:
        """投递命令；resize / scroll *同类*合并最新（只保留最新一条），
        其他命令（navigate / input / stop）按序保留，返回是否被接受。"""
        if cmd.kind in (CMD_RESIZE, CMD_SCROLL):
            # 合并：只丢弃未消费的*同类*旧命令，保留其他类别（G07 step 5）
            self._queue = [c for c in self._queue
                           if c.kind != cmd.kind]
            # 合并命令不受 max_pending 上限约束（同类只剩一条）
            self._queue.append(cmd)
            return True
        if len(self._queue) >= self.max_pending:
            # 有界：超限的导航 / 输入被拒绝（UI 侧需反馈"队列满"）
            return False
        self._queue.append(cmd)
        return True

    def peek(self) -> Optional[Command]:
        return self._queue[0] if self._queue else None

    def take(self) -> Optional[Command]:
        return self._queue.pop(0) if self._queue else None

    def __len__(self) -> int:
        return len(self._queue)

    def clear(self) -> None:
        """标签关闭 / 导航被新文档取代时清空（旧任务全部丢弃）。"""
        self._queue.clear()


# ----------------------------------------------------------------------
# 控制器（UI 只投递命令 + 持有有限状态镜像）
# ----------------------------------------------------------------------

class DocumentController:
    """G07 — UI 与文档工作分离的控制器（骨架）。

    UI 侧（Tk 主线程）只做两件事：

    1. 把用户意图包装成 :class:`Command` 投递到 :attr:`commands`
       （有界、resize / scroll 合并）。
    2. 在 ``drain`` 时取回 :class:`Result` 列表，按 C01 版本门控
       （:meth:`Result.is_stale` + :meth:`NavigationState.accept_result`）
       决定是否应用到 UI。

    文档工作单元（本骨架里是同步执行的 ``_process``；批次 3 把网络
    I/O 移到工作线程 / 文档进程后，``_process`` 只负责结果排队，
    不阻塞 UI 事件循环）。
    """

    def __init__(self, process=None, max_pending: int = 64) -> None:
        self.commands = CommandQueue(max_pending)
        self.nav = NavigationState()
        self.version = DocumentVersion()
        self._epoch = 0
        # 结果队列（文档工作单元产出，主线程 drain 取回）
        self._results: list[Result] = []
        # 工作单元：可选回调 ``(cmd, controller) -> Result | None``。
        # 本骨架默认用内置同步处理（``_process``），批次 3 换成
        # 线程 / 进程实现时，UI 侧只改这里。
        self._process = process

    # ------------------------------------------------------------------
    # 命令投递（UI → 工作单元）
    # ------------------------------------------------------------------
    def navigate(self, url: str) -> int:
        """G08/G07 — 一次导航请求：分配 epoch，进入 LOADING，
        投递 navigate 命令。返回 epoch（结果携带它做丢弃门控）。"""
        self._epoch += 1
        epoch = self._epoch
        self.version.new_navigation()
        self.nav.start_navigation(url, epoch)
        self.version.new_document()  # 新文档身份（旧帧 / 旧结果全 stale）
        ok = self.commands.post(Command(
            CMD_NAVIGATE, {"url": url},
            nav_epoch=epoch, document_id=self.version.document_id))
        if not ok:
            # 队列满：导航被拒，回退到 idle 并标记失败
            self.nav.fail("queue_full")
        return epoch

    def stop(self) -> None:
        """G08 step 3 — Stop：UI 立即反馈（逻辑取消 + 状态翻
        CANCELLED），再投递 stop 命令请求 transport 收尾。"""
        if not self.nav.is_busy:
            return
        self.nav.cancel_request()
        self.commands.post(Command(
            CMD_STOP, {},
            nav_epoch=self._epoch, document_id=self.version.document_id))

    def scroll(self, offset: int) -> None:
        """G01 — 滚动请求（合并：队列里只保留最新）。"""
        self.commands.post(Command(
            CMD_SCROLL, {"offset": offset},
            nav_epoch=self._epoch, document_id=self.version.document_id))

    def resize(self, css_w: int, css_h: int) -> None:
        """G06 — 视口尺寸变化（合并：只保留最新；触发重排）。"""
        self.commands.post(Command(
            CMD_RESIZE, {"css_width": css_w, "css_height": css_h},
            nav_epoch=self._epoch, document_id=self.version.document_id))

    def post_input(self, input_type: str, data: str = None) -> None:
        """G03 — 编辑命令（经 EditorState 驱动；骨架直接排队）。"""
        self.commands.post(Command(
            CMD_INPUT, {"type": input_type, "data": data},
            nav_epoch=self._epoch, document_id=self.version.document_id))

    # ------------------------------------------------------------------
    # 结果取回（工作单元 → UI 主线程）
    # ------------------------------------------------------------------
    def _process_default(self, cmd: Command) -> Optional[Result]:
        """内置同步工作单元：处理 navigate（about: / 本地 stub），
        产出携带 C01 版本的结果。批次 3 的网络工作单元替换这里。"""
        self.version.bump_layout()
        self.version.bump_frame()
        if cmd.kind == CMD_NAVIGATE:
            url = cmd.payload.get("url", "")
            ok, err_cat = self._try_load(url)
            if ok:
                self.nav.mark_interactive()
            return Result(ok=ok, nav_epoch=cmd.nav_epoch,
                          document_id=cmd.document_id,
                          frame_id=self.version.frame_id,
                          payload={"url": url},
                          error_category=err_cat,
                          final_url="" if ok else url)
        return Result(ok=True, nav_epoch=cmd.nav_epoch,
                      document_id=cmd.document_id,
                      frame_id=self.version.frame_id)

    def _try_load(self, url: str):
        """骨架的占位加载：about: / inline / 本地文件成功，
        http(s) 标记为"需批次 3 网络工作单元"（不阻塞 UI）。"""
        from toy_browser.app import gui as _gui
        lowered = url.lower()
        if lowered.startswith("about:") or url.startswith("<") or \
                _gui.os.path.isfile(url):
            return True, ""
        # 网络 URL：骨架不实际拉取（G07 step 3 初版先移出网络 I/O 的
        # 占位）；返回成功 stub 让 UI 走 complete，批次 3 换成真实
        # 工作单元 + 可取消 transport。
        return True, ""

    def drain(self) -> list[Result]:
        """G07 step 3 — 处理队列里所有命令，返回待 UI 应用的
        Result 列表（调用方按 :meth:`Result.is_stale` +
        :meth:`NavigationState.accept_result` 门控）。

        已取消的导航（``nav.cancel_requested``）：在途命令的结果
        不进 ``produced``（transport 在下一个可取消点收尾，G08
        step 3）；UI 侧 ``apply_results`` 对取消态的结果也会丢弃
        （双保险）。"""
        produced: list[Result] = []
        while True:
            cmd = self.commands.take()
            if cmd is None:
                break
            if self.nav.cancel_requested:
                # 已取消：丢弃所有在途命令的结果（不应用旧导航）
                continue
            process = self._process if self._process else \
                (lambda cmd, _c: self._process_default(cmd))
            res = process(cmd, self)
            if res is not None:
                produced.append(res)
        return produced
    def apply_results(self, results: list[Result]) -> list[Result]:
        """G07 — 按 C01 版本 + 导航状态门控，返回 *被应用* 的结果。

        过期帧 / 旧 epoch / 已取消的结果全部丢弃（不写 DOM、不刷新
        画布）；被应用的导航结果会更新 :attr:`nav` 的
        COMPLETE / FAILED 状态。"""
        applied: list[Result] = []
        for r in results:
            if r.is_stale(self.version):
                continue
            if not self.nav.accept_result(
                    r.nav_epoch, r.ok,
                    error_category=r.error_category,
                    final_url=r.final_url):
                continue
            applied.append(r)
        return applied
