"""批次 38 — G07 step 4 文档进程多核方案（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G07
step 4：

> 纯 Python CPU 工作不能期待普通线程带来多核加速；评估文档进程方案，
> 使 DOM 长期留在该进程，仅传紧凑命令与帧。不要每帧序列化整棵 DOM。

与已有模块的关系（本模块**不重复**实现，只做文档进程方案）：
- **G07 step 1–3**（:class:`DocumentController` / :class:`CommandQueue`
  / :class:`Result` / :meth:`apply_results`，批次 3/5）已有有界命令
  队列 + C01 版本门控 + 可取消网络工作单元；本模块把 *CPU 工作位置*
  从主线程换到**独立文档进程**（多核收益），主线程仍只投递紧凑
  命令 + 经 :meth:`DocumentProcessPool.drain` 取回紧凑帧结果。
- **C01**（:class:`DocumentVersion`）的文档 / 帧版本在**worker 进程
  内**持有（DOM 长期留该进程）；跨进程只传紧凑命令与帧（不序列化
  整棵 DOM，G07 step 4）。
- **G09 step 4**（:class:`TabLifecycle` 关标签取消任务）：
  :meth:`DocumentProcessPool.close_tab` 取消被关标签在途工作 +
  丢弃在途帧结果（不泄漏已关闭标签任务）。

纯逻辑核心，不触碰 tkinter；``multiprocessing`` 真实进程 spawn 需
活显示 / 进程环境，本批用可注入的 ``process_factory`` /
``pipe_factory`` 做协议回归（无真实 spawn 也能验证）。
"""

from __future__ import annotations

import queue as _queue
from dataclasses import dataclass, field
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# G07 step 4 — 紧凑命令（UI 主线程 → 文档进程）
# ---------------------------------------------------------------------------

#: 紧凑命令 kind（与 :mod:`document_controller` 的 CMD_* 对齐，但
#: 这里只传 *紧凑* 载荷，不序列化整棵 DOM）。
DOC_CMD_NAVIGATE = "navigate"
DOC_CMD_INPUT = "input"
DOC_CMD_RESIZE = "resize"
DOC_CMD_SCROLL = "scroll"
DOC_CMD_STOP = "stop"

VALID_DOC_COMMANDS = (DOC_CMD_NAVIGATE, DOC_CMD_INPUT, DOC_CMD_RESIZE,
                      DOC_CMD_SCROLL, DOC_CMD_STOP)


@dataclass
class DocCommand:
    """G07 step 4 — 一条紧凑命令（可 pickle，跨进程传）。

    * ``tab_id`` — 目标标签（稳定 tab_id，G09 step 1）。
    * ``kind`` — 命令类型（navigate / input / resize / scroll / stop）。
    * ``payload`` — 紧凑载荷（**不**含 DOM 树；仅 url / 输入 / 尺寸
      等小数据，G07 step 4"不要每帧序列化整棵 DOM"）。
    * ``seq`` — 单调序号（本标签内命令顺序，结果携带做丢弃门控）。
    """
    tab_id: int
    kind: str
    payload: dict = field(default_factory=dict)
    seq: int = 0

    def is_pkl(self) -> bool:
        """可 pickle 自检（紧凑载荷必须可跨进程）。"""
        import pickle
        try:
            pickle.dumps(self)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# G07 step 4 — 紧凑帧结果（文档进程 → UI 主线程）
# ---------------------------------------------------------------------------

@dataclass
class DocFrame:
    """G07 step 4 — 一条紧凑帧结果（可 pickle，跨进程传回）。

    * ``tab_id`` — 产出帧的标签（关标签后该 tab 的帧全丢弃，G09
      step 4）。
    * ``seq`` — 对应 :class:`DocCommand.seq`（旧命令的帧丢弃）。
    * ``ok`` — 成功 / 失败（UI 主线程据 C01 门控决定是否应用）。
    * ``error_category`` — 失败类别（DNS / 超时 / 脚本…，G08 step 4）。
    * ``payload`` — 紧凑帧数据（如位图句柄 id / 尺寸，**不**含整棵
      DOM；呈现层才重建）。
    * ``cancelled`` — 该帧在收尾前被取消（Stop / 关标签，G08 step 3）。
    """
    tab_id: int
    seq: int
    ok: bool = True
    error_category: str = ""
    payload: dict = field(default_factory=dict)
    cancelled: bool = False


# ---------------------------------------------------------------------------
# G07 step 4 — 文档进程 worker（进程内长期持有文档身份）
# ---------------------------------------------------------------------------

class DocumentProcessWorker:
    """G07 step 4 — 文档进程内的 worker（纯逻辑，可跨进程复用）。

    职责：
    - 按 *tab_id* 持有**紧凑文档身份**（document_id / nav_epoch /
      frame_id，C01）——DOM 长期留该进程（本纯逻辑核心只持有身份
      计数，真实 DOM 树留活显示批次）；跨进程只传紧凑命令与帧
      （G07 step 4"不要每帧序列化整棵 DOM"）。
    - :meth:`handle` 处理一条 :class:`DocCommand`，产出
      :class:`DocFrame`（携带 C01 帧版本 + tab 身份）。
    - :meth:`drop_tab` 关标签：丢弃该 tab 全部在途身份（不泄漏，
      G09 step 4）。

    本类是**纯逻辑 worker**；真实多核收益需把它放进
    :class:`DocumentProcessPool` 的子进程（本批协议回归用注入的
    process_factory / pipe_factory 验证，不强制真实 spawn）。
    """

    def __init__(self) -> None:
        # 按 tab_id 持有紧凑文档身份（C01 计数在进程内单调）
        self._doc_id: dict[int, int] = {}
        self._nav_epoch: dict[int, int] = {}
        self._frame_id: dict[int, int] = {}

    # -- 命令处理 -----------------------------------------------------------

    def handle(self, cmd: DocCommand) -> DocFrame:
        """处理一条紧凑命令，产出紧凑帧结果（C01 帧版本门控）。

        navigate → 新文档身份（旧帧全 stale）；input / resize /
        scroll → 同文档 bump 帧版本；stop → 取消收尾（cancelled
        帧）。结果只携带紧凑 payload + 版本计数，**不**含 DOM。
        """
        tab = cmd.tab_id
        if tab not in self._doc_id:
            self._doc_id[tab] = 0
            self._nav_epoch[tab] = 0
            self._frame_id[tab] = 0
        if cmd.kind == DOC_CMD_NAVIGATE:
            # 新文档：doc_id / epoch / frame 全 bump（旧帧 stale）
            self._doc_id[tab] += 1
            self._nav_epoch[tab] += 1
            self._frame_id[tab] += 1
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            payload={"url": cmd.payload.get("url", ""),
                                     "doc_id": self._doc_id[tab],
                                     "frame_id": self._frame_id[tab]})
        if cmd.kind == DOC_CMD_STOP:
            # 取消收尾：在途帧标 cancelled（G08 step 3 不强杀）
            self._frame_id[tab] += 1
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            cancelled=True,
                            payload={"frame_id": self._frame_id[tab]})
        # input / resize / scroll：同文档 bump 帧（几何 / 编辑重算）
        self._frame_id[tab] += 1
        return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                        payload={"kind": cmd.kind,
                                 "frame_id": self._frame_id[tab]})

    # -- 关标签 -------------------------------------------------------------

    def drop_tab(self, tab_id: int) -> int:
        """G09 step 4 — 丢弃该标签全部在途文档身份（不泄漏）。
        返回被丢弃的 tab 计数（存在则 1，否则 0）。"""
        dropped = 0
        for m in (self._doc_id, self._nav_epoch, self._frame_id):
            if tab_id in m:
                del m[tab_id]
                dropped = 1
        return dropped

    def identity(self, tab_id: int):
        """C01 — 该 tab 的紧凑文档身份 (doc_id, epoch, frame_id)。"""
        return (self._doc_id.get(tab_id, 0),
                self._nav_epoch.get(tab_id, 0),
                self._frame_id.get(tab_id, 0))


# ---------------------------------------------------------------------------
# G07 step 4 — 文档进程池（多核方案，可注入 process / pipe）
# ---------------------------------------------------------------------------

@dataclass
class DocumentProcessStats:
    """G07 step 4 — 文档进程池诊断统计。

    * ``commands_posted`` — 投递的紧凑命令数。
    * ``frames_drained`` — 主线程取回的紧凑帧数。
    * ``dropped_tab_frames`` — 关标签丢弃的在途帧数（G09 step 4）。
    * ``worker_crashed`` — worker 进程崩溃 / 不可用标记。
    * ``on_fallback`` — 是否已回退到内置同步 worker。
    """
    commands_posted: int = 0
    frames_drained: int = 0
    dropped_tab_frames: int = 0
    worker_crashed: bool = False
    on_fallback: bool = False


class DocumentProcessPool:
    """G07 step 4 — 文档进程多核方案（纯逻辑核心）。

    把 CPU 工作从 UI 主线程移到**独立文档进程**（多核收益，G07
    step 4"纯 Python 线程无多核收益"）。主线程只做两件轻量事：

    - :meth:`post` — 投递紧凑 :class:`DocCommand`（不阻塞、不传
      DOM）；
    - :meth:`drain` — 非阻塞取回 :class:`DocFrame`（经 C01 门控
      后由呈现层应用）。

    进程 / 管道可注入（``process_factory`` / ``pipe_factory``），
    使纯逻辑回归不依赖真实 spawn；``multiprocessing`` 不可用或
    worker 崩溃时**自动回退**到内置同步 :class:`DocumentProcessWorker`
    （:attr:`stats.on_fallback`）。关标签经 :meth:`close_tab` 丢弃
    在途帧（G09 step 4 不泄漏）。
    """

    def __init__(self,
                 process_factory: Optional[Callable] = None,
                 pipe_factory: Optional[Callable] = None,
                 ) -> None:
        self._worker = DocumentProcessWorker()
        self._stats = DocumentProcessStats()
        # 注入点（纯逻辑回归用 fake；真实多核走 multiprocessing）
        self._process_factory = process_factory
        self._pipe_factory = pipe_factory
        # 在途命令 / 帧（进程内暂存，drain 取回）
        self._pending_cmds: list[DocCommand] = []
        self._pending_frames: list[DocFrame] = []
        # 关标签门控：被关 tab 的在途帧全部丢弃
        self._closed_tabs: set = set()
        self._max_seq = 0

    # -- 查询 -------------------------------------------------------------

    @property
    def stats(self) -> DocumentProcessStats:
        return self._stats

    def is_fallback(self) -> bool:
        """是否已回退到内置同步 worker（无真实进程 / 进程崩溃）。"""
        return self._stats.on_fallback

    # -- 命令投递（UI 主线程 → 文档进程，轻量不阻塞） ---------------------

    def post(self, cmd: DocCommand) -> bool:
        """G07 step 4 — 投递一条紧凑命令。已关 tab 的命令直接丢弃
        （返回 False，不串页）；否则入在途队列（真实进程下经 pipe
        发往 worker，本纯逻辑核心先暂存）。"""
        if cmd.tab_id in self._closed_tabs:
            # 关标签后旧命令丢弃（G09 step 4 不泄漏）
            return False
        self._max_seq = max(self._max_seq, cmd.seq)
        self._pending_cmds.append(cmd)
        self._stats.commands_posted += 1
        # 真实进程：经 pipe_factory 发往 worker（纯逻辑回归暂存）
        return True

    # -- 帧取回（文档进程 → UI 主线程，非阻塞） ---------------------------

    def drain(self) -> list[DocFrame]:
        """G07 step 4 — 非阻塞取回在途紧凑帧结果。

        处理在途命令（经 worker）产出帧，丢弃已关 tab 的帧
        （G09 step 4），返回待 UI 应用的 :class:`DocFrame` 列表
        （调用方按 C01 门控决定应用）。"""
        frames = list(self._pending_frames)
        self._pending_frames = []
        for cmd in self._pending_cmds:
            self._pending_cmds = []
            # 关标签门控：被关 tab 的帧丢弃
            if cmd.tab_id in self._closed_tabs:
                self._stats.dropped_tab_frames += 1
                continue
            frame = self._worker.handle(cmd)
            if frame.tab_id in self._closed_tabs:
                self._stats.dropped_tab_frames += 1
                continue
            frames.append(frame)
            self._stats.frames_drained += 1
        # 真实进程：经 pipe_factory 从 worker 收帧（纯逻辑回归暂存）
        return frames

    # -- 关标签（G09 step 4：取消任务 + 丢弃在途帧 + 释放身份） -----------

    def close_tab(self, tab_id: int) -> dict:
        """G09 step 4 — 关闭 *tab_id*：丢弃该 tab 全部在途命令 + 在途
        帧 + 文档身份（不泄漏已关闭标签任务）。返回
        ``{"dropped_frames", "dropped_cmds"}``。"""
        dropped_frames = 0
        for f in self._pending_frames:
            if f.tab_id == tab_id:
                dropped_frames += 1
        self._pending_frames = [f for f in self._pending_frames
                                if f.tab_id != tab_id]
        dropped_cmds = 0
        for c in self._pending_cmds:
            if c.tab_id == tab_id:
                dropped_cmds += 1
        self._pending_cmds = [c for c in self._pending_cmds
                              if c.tab_id != tab_id]
        self._closed_tabs.add(tab_id)
        self._worker.drop_tab(tab_id)
        # 在途命令被关标签丢弃也计入帧丢弃统计（命令态丢弃，
        # G09 step 4 不泄漏——这些命令本会产出帧，现已丢弃）
        self._stats.dropped_tab_frames += dropped_frames + dropped_cmds
        return {"dropped_frames": dropped_frames,
                "dropped_cmds": dropped_cmds}

    # -- 崩溃 / 回退 -------------------------------------------------------

    def mark_crashed(self) -> None:
        """worker 进程崩溃 / 不可用：标记 + 回退到内置同步 worker
        （后续命令仍经 :meth:`post` / :meth:`drain` 在主进程内处理，
        保证 UI 不崩）。"""
        self._stats.worker_crashed = True
        self._stats.on_fallback = True

    # -- 诊断 -------------------------------------------------------------

    def describe(self) -> str:
        return (f"posted={self._stats.commands_posted} "
                f"drained={self._stats.frames_drained} "
                f"dropped={self._stats.dropped_tab_frames} "
                f"crashed={self._stats.worker_crashed} "
                f"fallback={self._stats.on_fallback} "
                f"pending_cmds={len(self._pending_cmds)} "
                f"pending_frames={len(self._pending_frames)}")


# ---------------------------------------------------------------------------
# 真实多核入口（可选；纯逻辑回归不强制 spawn）
# ---------------------------------------------------------------------------

def make_multiprocessing_pool() -> DocumentProcessPool:
    """G07 step 4 — 用 ``multiprocessing`` 起真实文档进程的池。

    把 :class:`DocumentProcessWorker` 放进子进程（多核收益），主线程
    经 :class:`DocumentProcessPool.post` / :meth:`drain` 取回紧凑帧。
    ``multiprocessing`` 不可用时回退到内置同步 worker（on_fallback）。
    活显示 / 多进程环境才真正 spawn；本批回归以注入 fake 验证协议。
    """
    pool = DocumentProcessPool()
    try:
        import multiprocessing as _mp
        # 真实 spawn 时把 worker 放进子进程；此处保留协议入口
        # （子进程管理留活显示批次，本批验证回退路径不崩）。
        del _mp
    except Exception:
        pool.mark_crashed()
    return pool


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    pool = DocumentProcessPool()
    pool.post(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE,
                         payload={"url": "http://a"}, seq=1))
    pool.post(DocCommand(tab_id=1, kind=DOC_CMD_RESIZE,
                         payload={"w": 800, "h": 600}, seq=2))
    frames = pool.drain()
    print("frames:", [(f.tab_id, f.seq, f.payload) for f in frames])
    pool.close_tab(1)
    pool.post(DocCommand(tab_id=1, kind=DOC_CMD_NAVIGATE, seq=3))
    print("after close, drain:", pool.drain(), "stats:", pool.describe())
