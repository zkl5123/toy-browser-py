"""G08 step 3 + G07 step 3 — 可取消网络工作单元（transport）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md：

* G08 step 3 — Stop 先逻辑取消并回复 UI，再请求可取消 transport
  关闭/超时；**不强杀 Python 工作线程**。
* G07 step 3 — 初版先把**网络 I/O 移出 UI 调用点**（不再让
  :meth:`PageInstance.load_url` 在主线程里同步阻塞 fetch）；结果
  携带 C01 文档/帧版本，UI 经队列取回后才应用。

本模块把网络 I/O 放进一个可选的**工作线程**（默认同步，方便
测试与离线环境），核心是**取消语义**：

* :class:`CancellableTransport` — 一次 fetch 任务。``run()`` 调用
  注入的 ``fetch`` 回调（同步或可被打断）；``cancel()`` 立即置
  取消标志（UI 侧"立即反馈"），工作线程在**下一个可取消点**收尾
  （见 :func:`_cooperative_sleep` 的取消检查点；不强杀线程）。
* 结果 :class:`TransportResult` 携带 :class:`DocumentVersion` 门控
  所需的 ``(document_id, frame_id)`` 与 :class:`NavigationState`
  所需的 ``nav_epoch``；过期 / 已取消的结果由调用方丢弃（与 G07
  :class:`Result` / G08 状态机配合）。
* **可取消 sleep**（``_cooperative_sleep``）：网络客户端本身在
  socket 阻塞时无法被外部强杀；这里用"分片 sleep + 每片检查取消
  标志"模拟可取消的收尾点，让慢服务器场景可测（Stop 可立即反馈，
  过期完成事件无效）。

测试不需要真实网络：注入一个可被打断的 ``fetch`` 回调（如分片
sleep 后返回），验证取消后立即反馈 + 过期结果被丢弃。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# 一个 fetch 回调的签名：``fetch(url, transport) -> (ok, error_category,
# final_url, detail)``。``transport`` 让回调在可取消点检查
# ``transport.cancelled`` 并尽早收尾（不强杀）。
FetchFn = Callable[[str, "CancellableTransport"],
                   "tuple[bool, str, str, str]"]


# 可取消 sleep 的分片步长（秒）。分片足够小让取消"立即"生效，
# 又不至于高频轮询；测试里注入更小的值以加快用例。
_COOP_SLICE_S = 0.005


@dataclass
class TransportResult:
    """一次 fetch 任务的结果（G07：携带 C01 版本 + 导航 epoch）。

    * ``ok`` — 成功 / 失败。
    * ``error_category`` — 失败分类（与 G08 / ``net.categorize_fetch_error``
      词表对齐）。
    * ``final_url`` — 重定向链最终 URL（无重定向时 = 请求 URL）。
    * ``detail`` — 诊断摘要（控制台保留原始错误，G08 step 4）。
    * ``cancelled`` — 任务被 Stop 取消（过期完成事件无效）。
    * ``document_id`` / ``frame_id`` / ``nav_epoch`` — C01 门控字段
      （过期结果被 :meth:`DocumentVersion.is_stale_frame` /
      :meth:`NavigationState.accept_result` 丢弃）。
    """
    ok: bool
    error_category: str = ""
    final_url: str = ""
    detail: str = ""
    cancelled: bool = False
    document_id: int = 0
    frame_id: int = 0
    nav_epoch: int = 0

    def is_stale(self, version) -> bool:
        """C01 — 该结果渲染的帧 / 文档比当前版本旧 → 丢弃。"""
        return version.is_stale_frame(self.document_id, self.frame_id)


def _cooperative_sleep(seconds: float, transport: "CancellableTransport",
                       slice_s: float = _COOP_SLICE_S) -> bool:
    """可取消的 sleep：分片等待，每片检查 ``transport.cancelled``。

    返回 ``True`` 表示睡满（未被取消）；返回 ``False`` 表示中途被
    取消。这是 G08 step 3 的"下一个可取消点"——不强杀线程，线程
    自己检查标志后尽快收尾。"""
    remaining = float(seconds)
    while remaining > 0:
        if transport.cancelled:
            return False
        step = min(slice_s, remaining)
        time.sleep(step)
        remaining -= step
    return not transport.cancelled


class CancellableTransport:
    """G08 step 3 — 一次可取消的网络 fetch 任务。

    * ``run(fetch, url)`` — 同步执行（默认）；``async_=True`` 时投到
      工作线程（``threading.Thread``，纯 Python；G07 step 4 说明
      纯 Python 线程无多核收益，文档建议评估文档进程方案——本骨架
      先把 I/O 移出 UI 调用点 + 落地取消语义，进程方案后续）。
    * ``cancel()`` — 立即置取消标志（UI 侧立即反馈"已停止"）；
      工作线程在下一个可取消点（:func:`_cooperative_sleep` / 回调
      内的检查点）收尾，**不强杀线程**。
    * ``wait(timeout)`` — 主线程取回结果（已取消 / 已超时 / 完成）。

    结果经 :class:`TransportResult` 携带 C01 版本，UI 取回后由
    :meth:`NavigationState.accept_result` 门控（过期 / 取消丢弃）。
    """

    def __init__(self, document_id: int = 0, frame_id: int = 0,
                 nav_epoch: int = 0,
                 thread_name: str = "toy-fetch") -> None:
        self.document_id = document_id
        self.frame_id = frame_id
        self.nav_epoch = nav_epoch
        self._lock = threading.Lock()
        self._cancelled = False
        self._done = threading.Event()
        self._result: Optional[TransportResult] = None
        self._thread: Optional[threading.Thread] = None
        self.thread_name = thread_name

    # ------------------------------------------------------------------
    # 取消（G08 step 3：逻辑取消 + UI 立即反馈，不强杀线程）
    # ------------------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        """G08 — Stop：立即置取消标志（UI 侧"已停止"立即反馈）。

        工作线程在下一个可取消点（:func:`_cooperative_sleep` 的
        分片检查点 / 回调内的检查点）观察到 ``cancelled`` 后尽快
        收尾；这里**不**强杀线程（G08 step 3）。"""
        with self._lock:
            self._cancelled = True
        # 若工作线程仍在跑，唤醒它（_cooperative_sleep 分片检查点
        # 会尽快观察到取消标志并返回）。
        self._done.wait(0)  # no-op：仅确保 Event 已就绪

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def run(self, fetch: FetchFn, url: str, async_: bool = False) -> None:
        """执行一次 fetch。``async_=False`` 同步内联执行；
        ``async_=True`` 投到工作线程（主线程立即返回，后续
        :meth:`wait` 取结果）。

        工作单元在可取消点检查 ``self.cancelled``；被取消时
        立即产出 ``cancelled=True`` 的 :class:`TransportResult`
        （UI 侧"立即反馈"），不强杀线程。"""
        def _worker() -> None:
            if self.cancelled:
                self._finish(TransportResult(
                    ok=False, cancelled=True,
                    error_category="cancelled",
                    document_id=self.document_id,
                    frame_id=self.frame_id,
                    nav_epoch=self.nav_epoch))
                return
            try:
                ok, err_cat, final_url, detail = fetch(url, self)
            except Exception as e:  # noqa: BLE001 - transport 不吞取消
                if self.cancelled:
                    self._finish(TransportResult(
                        ok=False, cancelled=True,
                        error_category="cancelled",
                        detail=str(e),
                        document_id=self.document_id,
                        frame_id=self.frame_id,
                        nav_epoch=self.nav_epoch))
                    return
                ok, err_cat, final_url = False, "connect", ""
                detail = f"{type(e).__name__}: {e}"
            self._finish(TransportResult(
                ok=ok, error_category=err_cat, final_url=final_url,
                detail=detail,
                cancelled=self.cancelled,
                document_id=self.document_id,
                frame_id=self.frame_id,
                nav_epoch=self.nav_epoch))

        if async_:
            self._thread = threading.Thread(target=_worker,
                                             name=self.thread_name)
            self._thread.daemon = True
            self._thread.start()
        else:
            _worker()

    def _finish(self, result: TransportResult) -> None:
        with self._lock:
            self._result = result
        self._done.set()

    # ------------------------------------------------------------------
    # 取结果（G07 step 3：主线程经队列 / after 取回）
    # ------------------------------------------------------------------
    def wait(self, timeout: Optional[float] = None) -> Optional[TransportResult]:
        """阻塞至工作线程完成（``timeout`` 秒，``None`` 表示等待结束）。

        返回 :class:`TransportResult`（完成 / 取消 / 失败）；超时
        时返回 ``None``（调用方可自行判为超时并标记 FAILED）。"""
        got = self._done.wait(timeout)
        if not got:
            return None
        with self._lock:
            return self._result

    @property
    def result(self) -> Optional[TransportResult]:
        """非阻塞取结果（未完成的返回 None）。"""
        with self._lock:
            return self._result

    @property
    def finished(self) -> bool:
        return self._done.is_set()
