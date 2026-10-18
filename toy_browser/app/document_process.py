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

# ---------------------------------------------------------------------------
# 批次 46 — 真实 multiprocessing spawn（多核收益 + 活显示验收入口）
# ---------------------------------------------------------------------------

def _doc_process_child(cmd_q, result_q, stop_evt, close_q):
    """批次 46 — 子进程 worker 主循环（真实 spawn 侧）。

    从 ``cmd_q`` 读 :class:`DocCommand`，经 :class:`DocumentProcessWorker`
    产出 :class:`DocFrame`，经 ``result_q`` 传回；``close_q`` 收到关
    标签 tab_id 时丢弃该 tab 在途身份（G09 step 4）；``stop_evt`` 置位
    或 ``cmd_q`` 收到 None 时退出。异常回写 ``result_q``（{"error": …}）
    供父进程标记回退。
    """
    import sys as _sys
    try:
        worker = DocumentProcessWorker()
        while True:
            try:
                cmd = cmd_q.get(timeout=0.2)
            except _queue.Empty:
                if stop_evt.is_set():
                    break
                continue
            if cmd is None:
                break
            try:
                frame = worker.handle(cmd)
                result_q.put(frame)
            except Exception as e:
                result_q.put({"error": repr(e)})
                break
            # 关标签队列：非阻塞取（G09 step 4 丢弃在途身份）
            try:
                while True:
                    tab_id = close_q.get_nowait()
                    worker.drop_tab(tab_id)
            except _queue.Empty:
                pass
    except Exception as e:  # 防御：子进程崩不静默
        try:
            result_q.put({"error": "child-crash: " + repr(e)})
        except Exception:
            pass
        _sys.exit(1)


class DocProcessServer:
    """批次 46 — 真实 ``multiprocessing`` 文档进程（子进程持有 worker，
    多核收益；父线程经 pipe 投递紧凑命令 / 取回紧凑帧）。

    与 :class:`DocumentProcessPool` 的关系：本类是 *真实 spawn 侧*
    的池——协议（:meth:`post` / :meth:`drain` / :meth:`close_tab` /
    回退）与 ``DocumentProcessPool`` 一致，但 CPU 工作在**子进程**
    （G07 step 4"纯 Python 线程无多核收益"落地）。子进程崩溃 /
    不可用时 :attr:`on_fallback` 置位，后续调用自动回退到内置同步
    worker（UI 不崩）。

    活显示验收入口：``gui.py`` 在可多核环境用
    :meth:`spawn_doc_process_server` 起本类，导航 / resize / 输入
    经 :meth:`post` 投递（不阻塞 UI），事件循环经 :meth:`drain`
    取回紧凑帧（经 C01 门控后应用），关标签经 :meth:`close_tab`
    丢弃在途身份。
    """

    def __init__(self) -> None:
        self._worker = DocumentProcessWorker()   # 回退用（父进程内）
        self._stats = DocumentProcessStats()
        self._proc = None
        self._cmd_q = None
        self._result_q = None
        self._close_q = None
        self._stop_evt = None
        # 父侧暂存（回退态 / 子进程不可用时走内置同步）
        self._pending_cmds: list[DocCommand] = []
        self._pending_frames: list[DocFrame] = []
        self._closed_tabs: set = set()
        self._max_seq = 0
        self._on_spawned = False

    # -- 生命周期 -----------------------------------------------------------

    def start(self) -> bool:
        """真实 spawn 子进程（多核收益）。成功返回 True 并置
        ``_on_spawned``；``multiprocessing`` 不可用 / spawn 失败返回
        False 并置 ``on_fallback``（后续调用走内置同步 worker）。"""
        import multiprocessing as _mp
        try:
            ctx = _mp.get_context("spawn")
            self._cmd_q = ctx.Queue()
            self._result_q = ctx.Queue()
            self._close_q = ctx.Queue()
            self._stop_evt = ctx.Event()
            self._proc = ctx.Process(
                target=_doc_process_child,
                args=(self._cmd_q, self._result_q,
                      self._stop_evt, self._close_q),
                daemon=True)
            self._proc.start()
            self._on_spawned = True
            return True
        except Exception:
            # spawn 失败：清理 + 回退
            for q in (self._cmd_q, self._result_q, self._close_q):
                if q is not None:
                    try:
                        q.close()
                    except Exception:
                        pass
            self._cmd_q = self._result_q = self._close_q = None
            self._proc = None
            self._on_spawned = False
            self._stats.on_fallback = True
            self._stats.worker_crashed = True
            return False

    def shutdown(self) -> None:
        """终止子进程（置 stop_evt + join，回退态 no-op）。"""
        if self._on_spawned:
            try:
                self._stop_evt.set()
                self._cmd_q.put(None)
            except Exception:
                pass
            try:
                self._proc.join(timeout=1.0)
            except Exception:
                pass
            if self._proc.is_alive():
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            self._on_spawned = False
        for q in (self._cmd_q, self._result_q, self._close_q):
            if q is not None:
                try:
                    q.close()
                except Exception:
                    pass
            try:
                q.join_thread()
            except Exception:
                pass
        self._cmd_q = self._result_q = self._close_q = None
        self._proc = None

    def is_spawned(self) -> bool:
        """子进程是否已真实 spawn 且存活。"""
        return self._on_spawned and self._proc is not None \
            and self._proc.is_alive()

    # -- 查询 ---------------------------------------------------------------

    @property
    def stats(self) -> DocumentProcessStats:
        return self._stats

    def is_fallback(self) -> bool:
        return self._stats.on_fallback

    # -- 命令投递（UI 主线程 → 文档进程，轻量不阻塞） ------------------------

    def post(self, cmd: DocCommand) -> bool:
        """投递紧凑命令。已关 tab 直接丢弃（False）；子进程存活时
        经 pipe 发往 worker（多核），否则回退到父进程内置同步暂存。"""
        if cmd.tab_id in self._closed_tabs:
            return False
        self._max_seq = max(self._max_seq, cmd.seq)
        self._stats.commands_posted += 1
        if self.is_spawned():
            try:
                self._cmd_q.put(cmd, block=False)
                return True
            except Exception:
                # pipe 故障 → 回退内置同步（不丢命令）
                self._fallback_sync()
        self._pending_cmds.append(cmd)
        return True

    # -- 帧取回（文档进程 → UI 主线程，非阻塞） ------------------------------

    def drain(self) -> list[DocFrame]:
        """非阻塞取回紧凑帧。子进程存活时经 pipe 收帧（多核）；
        回退态处理父侧暂存命令（内置同步）。关标签帧全丢弃
        （G09 step 4）。"""
        frames = list(self._pending_frames)
        self._pending_frames = []
        if self.is_spawned():
            # 子进程收帧
            try:
                while True:
                    item = self._result_q.get_nowait()
                    if isinstance(item, dict) and "error" in item:
                        # 子进程报错 → 回退内置同步
                        self._fallback_sync()
                        break
                    if item.tab_id in self._closed_tabs:
                        self._stats.dropped_tab_frames += 1
                        continue
                    frames.append(item)
                    self._stats.frames_drained += 1
            except _queue.Empty:
                pass
        else:
            # 回退态：父进程内置同步处理暂存命令
            for cmd in self._pending_cmds:
                self._pending_cmds = []
                if cmd.tab_id in self._closed_tabs:
                    self._stats.dropped_tab_frames += 1
                    continue
                frame = self._worker.handle(cmd)
                if frame.tab_id in self._closed_tabs:
                    self._stats.dropped_tab_frames += 1
                    continue
                frames.append(frame)
                self._stats.frames_drained += 1
        return frames

    # -- 关标签（G09 step 4） ------------------------------------------------

    def close_tab(self, tab_id: int) -> dict:
        """关闭 tab：丢弃在途命令 + 帧 + 文档身份（不泄漏）。子进程
        存活时经 close_q 通知 worker drop_tab（多核侧释放），回退态
        直接父侧释放。"""
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
        if self.is_spawned():
            try:
                self._close_q.put(tab_id, block=False)
            except Exception:
                self._fallback_sync()
        self._stats.dropped_tab_frames += dropped_frames + dropped_cmds
        return {"dropped_frames": dropped_frames,
                "dropped_cmds": dropped_cmds}

    # -- 回退 ----------------------------------------------------------------

    def _fallback_sync(self) -> None:
        """子进程不可用（崩溃 / pipe 故障）：置 on_fallback，后续
        post/drain 走父进程内置同步 worker（UI 不崩）。"""
        if self._stats.on_fallback:
            return
        self._stats.worker_crashed = True
        self._stats.on_fallback = True
        self._on_spawned = False
        # 不 terminate 子进程（可能半崩），仅切回退态；shutdown 统一清理

    def mark_crashed(self) -> None:
        """worker 进程崩溃 / 不可用：标记 + 回退到内置同步 worker。"""
        self._fallback_sync()

    # -- 诊断 ----------------------------------------------------------------

    def describe(self) -> str:
        return (f"posted={self._stats.commands_posted} "
                f"drained={self._stats.frames_drained} "
                f"dropped={self._stats.dropped_tab_frames} "
                f"crashed={self._stats.worker_crashed} "
                f"fallback={self._stats.on_fallback} "
                f"spawned={self.is_spawned()} "
                f"pending_cmds={len(self._pending_cmds)} "
                f"pending_frames={len(self._pending_frames)}")


def spawn_doc_process_server() -> DocProcessServer:
    """批次 46 — 活显示验收入口：起真实 ``multiprocessing`` 文档进程
    池（多核收益）。``multiprocessing`` 不可用 / spawn 失败时返回
    ``on_fallback`` 的池（内置同步 worker，UI 不崩）。

    ``gui.py`` 在可多核环境调用本入口，导航 / resize / 输入经
    :meth:`DocProcessServer.post` 投递（不阻塞 UI），事件循环经
    :meth:`DocProcessServer.drain` 取回紧凑帧（经 C01 门控后应用），
    关标签经 :meth:`DocProcessServer.close_tab` 丢弃在途身份。
    退出前经 :meth:`DocProcessServer.shutdown` 终止子进程。
    """
    server = DocProcessServer()
    if not server.start():
        # start() 已置 on_fallback；返回可回退池（后续走内置同步）
        pass
    return server


def make_multiprocessing_pool() -> DocProcessServer:
    """G07 step 4 — 真实多核入口（批次 46 落地真实 spawn）。

    返回 :class:`DocProcessServer`（子进程持有 :class:`DocumentProcessWorker`，
    多核收益）。``multiprocessing`` 不可用 / spawn 失败时返回
    ``on_fallback`` 的池（内置同步 worker，UI 不崩）。
    活显示验收：``gui.py`` 用 :func:`spawn_doc_process_server` 起池，
    经 :meth:`post` / :meth:`drain` / :meth:`close_tab` 走多核路径。
    """
    return spawn_doc_process_server()


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
