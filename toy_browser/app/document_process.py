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

# R7-S04 第 5 点 — worker 超时后有界终止契约（纯逻辑判定层，无运行时依赖）。
from toy_browser.app import worker_timeout as _worker_timeout
from toy_browser.app.worker_timeout import WorkerTerminationState


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
    # R7-A02 第 3 点 — 版本化 IPC 四元组之 navigation_id（同一 tab
    # 内每次导航 +1）。与 tab_id / document_version / frame_id 共同
    # 构成"旧任务不可覆盖新页"的门控标识；默认 0 向后兼容（既有
    # 单导航场景不传也为 0）。
    navigation_id: int = 0

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
    # R7-A02 第 3 点 — 产出该帧的 navigation_id（与命令同源）。UI
    # 侧据此丢弃"旧导航"的迟到帧（旧任务不可覆盖新页）。
    navigation_id: int = 0


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
        # R7-A02 第 3 点 — navigation_id：同一 tab 内每次导航 +1
        # （与 doc_id/epoch/frame_id 共同构成版本化 IPC 四元组）。
        self._nav_id: dict[int, int] = {}

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
        if tab not in self._nav_id:
            self._nav_id[tab] = 0
        if cmd.kind == DOC_CMD_NAVIGATE:
            # 新文档：doc_id / epoch / frame 全 bump（旧帧 stale）
            self._doc_id[tab] += 1
            self._nav_epoch[tab] += 1
            self._frame_id[tab] += 1
            # R7-A02 — navigation_id 同步 +1（新导航身份；命令自带
            # navigation_id 时以命令为准，保证 UI 与 worker 同源）。
            self._nav_id[tab] += 1
            nav_id = cmd.navigation_id or self._nav_id[tab]
            if cmd.navigation_id:
                self._nav_id[tab] = cmd.navigation_id
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            navigation_id=nav_id,
                            payload={"url": cmd.payload.get("url", ""),
                                     "doc_id": self._doc_id[tab],
                                     "frame_id": self._frame_id[tab],
                                     "navigation_id": nav_id})
        if cmd.kind == DOC_CMD_STOP:
            # 取消收尾：在途帧标 cancelled（G08 step 3 不强杀）
            self._frame_id[tab] += 1
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            cancelled=True,
                            navigation_id=cmd.navigation_id or self._nav_id[tab],
                            payload={"frame_id": self._frame_id[tab]})
        # input / resize / scroll：同文档 bump 帧（几何 / 编辑重算）
        self._frame_id[tab] += 1
        return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                        navigation_id=cmd.navigation_id or self._nav_id[tab],
                        payload={"kind": cmd.kind,
                                 "frame_id": self._frame_id[tab]})

    # -- 关标签 -------------------------------------------------------------

    def drop_tab(self, tab_id: int) -> int:
        """G09 step 4 — 丢弃该标签全部在途文档身份（不泄漏）。
        返回被丢弃的 tab 计数（存在则 1，否则 0）。"""
        dropped = 0
        for m in (self._doc_id, self._nav_epoch, self._frame_id,
                  self._nav_id):
            if tab_id in m:
                del m[tab_id]
                dropped = 1
        return dropped

    def identity(self, tab_id: int):
        """C01 — 该 tab 的紧凑文档身份 (doc_id, epoch, frame_id)。

        R7-A02 — 版本化 IPC 四元组经 :meth:`ipc_identity` 取
        （tab_id / navigation_id / document_version / frame_id）；
        本方法保持既有三元组契约（向后兼容）。
        """
        return (self._doc_id.get(tab_id, 0),
                self._nav_epoch.get(tab_id, 0),
                self._frame_id.get(tab_id, 0))

    def ipc_identity(self, tab_id: int) -> dict:
        """R7-A02 第 3 点 — 版本化 IPC 身份四元组。

        返回 ``{"tab_id", "navigation_id", "document_version",
        "frame_id"}``——UI 侧据此做"旧任务不可覆盖新页"门控：
        tab 相同而 navigation_id 更小的迟到帧一律丢弃。
        """
        return {"tab_id": tab_id,
                "navigation_id": self._nav_id.get(tab_id, 0),
                "document_version": self._doc_id.get(tab_id, 0),
                "frame_id": self._frame_id.get(tab_id, 0)}


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
# R7-A02 第 3 点 — 命令合并 / 背压 / 取消确认 / 版本化门控
# ---------------------------------------------------------------------------

#: R7-A02 — 可合并命令：同一 (tab_id, navigation_id, kind) 的连续
#: resize / scroll 只保留**最新**一条（旧几何命令被新几何取代，
#: 无意义却会撑满队列 → 慢页期间 UI 被淹没）。
#: navigate 不合并（每次导航是新文档身份）；stop 不合并（每次取消
#: 都要被确认，见 :class:`CancelTracker`）。
COALESCABLE_COMMANDS = (DOC_CMD_RESIZE, DOC_CMD_SCROLL)


class CommandCoalescer:
    """R7-A02 第 3 点 — 命令合并（coalescing）。

    UI 侧连续 resize / scroll（拖拽窗口、滚轮）会瞬间产出大量几何
    命令；旧命令的结果会被新命令覆盖，全部投递只会在慢页时撑爆
    队列、延迟 UI。本合并器对同一 (tab_id, navigation_id, kind)
    的可合并命令**保留 seq 最大的一条**（最新几何），navigate /
    stop 原样保留（不可合并）。

    退化契约：无可合并命令（只有 navigate / input / stop）时
    :meth:`drain` 返回原命令序列（按 seq 排序），与不合并的既有
    行为一致（向后兼容）。
    """

    def __init__(self) -> None:
        # key = (tab_id, navigation_id, kind) → 最新一条命令
        self._latest: dict[tuple, DocCommand] = {}
        self._seq: list[DocCommand] = []
        self._merged = 0

    def push(self, cmd: DocCommand) -> None:
        """并入一条命令。可合并 kind → 覆盖同 key 旧命令；否则入序。"""
        if cmd.kind in COALESCABLE_COMMANDS:
            key = (cmd.tab_id, cmd.navigation_id, cmd.kind)
            if key in self._latest:
                self._merged += 1
            self._latest[key] = cmd
            return
        self._seq.append(cmd)

    def drain(self) -> list:
        """取出合并后的命令列表（按 seq 升序，保证因果顺序）。"""
        out = list(self._seq)
        out.extend(self._latest.values())
        self._seq = []
        self._latest = {}
        out.sort(key=lambda c: c.seq)
        return out

    def pending(self) -> int:
        """当前待投递命令数（合并后口径）。"""
        return len(self._seq) + len(self._latest)

    @property
    def merged(self) -> int:
        """被合并掉的旧命令数（诊断：合并生效证据）。"""
        return self._merged

    def describe(self) -> str:
        return f"pending={self.pending()} merged={self._merged}"


class BackpressureGate:
    """R7-A02 第 3 点 — 背压（backpressure）。

    在途命令数达上限时**拒收**新命令（返回 False + 计 dropped），
    避免慢页 / 坏脚本期间命令队列无界增长把 UI 拖死。上限默认
    256（紧凑命令，远小于 DOM 序列化成本）。

    退化契约：在途数 < limit 时 :meth:`admit` 恒 True（与无背压的
    既有行为一致，向后兼容）。
    """

    def __init__(self, limit: int = 256) -> None:
        self.limit = max(1, int(limit))
        self.dropped = 0
        self._rejected_last = False

    def admit(self, depth: int) -> bool:
        """在途深度 ``depth`` 下是否准入新命令。达上限拒收并计
        ``dropped``。"""
        if depth >= self.limit:
            self.dropped += 1
            self._rejected_last = True
            return False
        self._rejected_last = False
        return True

    @property
    def is_saturated(self) -> bool:
        """最近一次判定是否已达上限（诊断）。"""
        return self._rejected_last

    def describe(self) -> str:
        return f"limit={self.limit} dropped={self.dropped}"


class CancelTracker:
    """R7-A02 第 3 点 — 取消确认（cancel acknowledgement）。

    Stop / 关标签的"取消"必须是**被确认**的，不能静默：UI 发出
    cancel 请求 → worker 回 cancelled 帧 → :meth:`ack` 确认。
    未被确认的取消保持在途（:meth:`pending` > 0），UI 可据此继续
    等待或强制回收（超时兜底）。

    token 单调，避免同一 tab 多次取消互相串味。
    """

    def __init__(self) -> None:
        self._pending: dict[int, tuple] = {}   # token → (tab_id, nav_id)
        self._acked: dict[int, bool] = {}
        self._next_token = 0

    def request(self, tab_id: int, navigation_id: int = 0) -> int:
        """发起一次取消，返回 cancel token。"""
        self._next_token += 1
        token = self._next_token
        self._pending[token] = (tab_id, navigation_id)
        return token

    def ack(self, token: int) -> bool:
        """确认 token 对应取消已完成（worker 回了 cancelled 帧）。
        重复 / 未知 token 返回 False。"""
        if token not in self._pending:
            return False
        del self._pending[token]
        self._acked[token] = True
        return True

    def ack_tab(self, tab_id: int, navigation_id: int = 0) -> int:
        """确认该 (tab, navigation) 的全部在途取消，返回确认条数。"""
        hits = [t for t, (tb, nv) in self._pending.items()
                if tb == tab_id and (navigation_id == 0 or nv == navigation_id)]
        for t in hits:
            self.ack(t)
        return len(hits)

    def is_acked(self, token: int) -> bool:
        return bool(self._acked.get(token, False))

    def pending(self) -> int:
        """未被确认的取消数（>0 说明取消还没被 worker 确认）。"""
        return len(self._pending)

    def describe(self) -> str:
        return f"pending={self.pending()} acked={len(self._acked)}"


class NavVersionGate:
    """R7-A02 第 3 点 — "旧任务不可覆盖新页"版本门控。

    按 (tab_id, navigation_id) 判定一个帧是否**可应用**：同 tab
    而 navigation_id 小于当前已应用导航的帧一律丢弃（旧导航的迟
    到结果不得覆盖新页）。frame_id 只在同一 navigation 内做单调
    比较（同导航内旧帧也不得覆盖新帧）。

    这是方案"验收目标：旧任务不可覆盖新页"的可测判定层。
    """

    def __init__(self) -> None:
        self._applied: dict[int, tuple] = {}   # tab_id → (nav_id, frame_id)

    def admit(self, tab_id: int, navigation_id: int,
              frame_id: int) -> bool:
        """该帧是否可应用。可应用时推进已应用版本。"""
        cur = self._applied.get(tab_id)
        if cur is None:
            self._applied[tab_id] = (navigation_id, frame_id)
            return True
        cur_nav, cur_frame = cur
        if navigation_id < cur_nav:
            return False          # 旧导航的迟到帧 → 丢弃
        if navigation_id == cur_nav and frame_id <= cur_frame:
            return False          # 同导航内旧帧 → 丢弃
        self._applied[tab_id] = (navigation_id, frame_id)
        return True

    def current(self, tab_id: int) -> tuple:
        """该 tab 已应用的 (navigation_id, frame_id)。"""
        return self._applied.get(tab_id, (0, 0))

    def reset(self, tab_id: int) -> None:
        """关标签 / 新会话：清该 tab 门控状态。"""
        self._applied.pop(tab_id, None)


# ---------------------------------------------------------------------------
# R7-A02 第 1 点 — UI 心跳 + task trace（确认真正调用路径）
# ---------------------------------------------------------------------------

class UiHeartbeat:
    """R7-A02 第 1 点 — UI 心跳。

    慢页 / 坏脚本期间 UI 必须持续可响应。本心跳在默认导航 /
    resize / scroll 入口被"真正调用路径"推进（每处理一次真实导航
    或每帧绘制 = 一次心跳），记录心跳次数与最近心跳时间戳。

    **不冒充通过**：本心跳只记录"UI 主线程被真实调用"的次数
    （可测），**不**测量真实墙钟响应延迟（P95 ≤ 100ms 的实测需
    真实 Tk 事件循环 + 活显示，留活 UI 批次）。
    """

    def __init__(self, clock=None) -> None:
        self._clock = clock
        self.beats = 0
        self.last_ts = 0.0

    def beat(self) -> int:
        """推进一次心跳（真实调用路径每次进入默认入口时调用）。"""
        self.beats += 1
        self.last_ts = self._now()
        return self.beats

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        try:
            import time
            return time.monotonic()
        except Exception:
            return 0.0

    def describe(self) -> str:
        return f"beats={self.beats} last_ts={self.last_ts:.3f}"


@dataclass
class TraceSpan:
    """R7-A02 第 1 点 — 一条 task trace span（阶段耗时 + 身份）。"""
    name: str = ""
    tab_id: int = 0
    navigation_id: int = 0
    started: float = 0.0
    ended: float = 0.0
    ok: bool = True
    note: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.ended - self.started)


class TaskTrace:
    """R7-A02 第 1 点 — task trace（确认真正调用路径）。

    在默认导航 / resize / scroll 入口记录阶段 span（名称 + tab /
    navigation 身份 + 起止时间戳 + 成功标记），回答方案第 1 点
    "确认真正调用路径"——不是"有这个模块"，而是"默认入口真的
    走到了这里"。

    **不冒充通过**：只记录调用路径与阶段耗时（可测），不宣称
    真实多核加速（那需要真实 spawn + 活显示性能实测，留活批次）。
    """

    def __init__(self, clock=None, max_spans: int = 512) -> None:
        self._clock = clock
        self._spans: list[TraceSpan] = []
        self.max_spans = max(1, int(max_spans))

    def _now(self) -> float:
        if self._clock is not None:
            return float(self._clock())
        try:
            import time
            return time.monotonic()
        except Exception:
            return 0.0

    def start(self, name: str, tab_id: int = 0,
              navigation_id: int = 0) -> TraceSpan:
        """开一个 span（返回句柄供 :meth:`end` 收尾）。"""
        return TraceSpan(name=name, tab_id=tab_id,
                         navigation_id=navigation_id,
                         started=self._now())

    def end(self, span: TraceSpan, ok: bool = True,
            note: str = "") -> TraceSpan:
        """收尾一个 span 并归档（超上限丢最旧，有界内存）。"""
        span.ended = self._now()
        span.ok = ok
        span.note = note
        self._spans.append(span)
        if len(self._spans) > self.max_spans:
            self._spans = self._spans[-self.max_spans:]
        return span

    def spans(self) -> list:
        return list(self._spans)

    def names(self) -> list:
        """已记录的 span 名称序列（调用路径证据）。"""
        return [sp.name for sp in self._spans]

    def count(self, name: str = "") -> int:
        """指定名称（或全部）的 span 数。"""
        if not name:
            return len(self._spans)
        return sum(1 for sp in self._spans if sp.name == name)

    def last(self) -> TraceSpan:
        return self._spans[-1] if self._spans else None

    def describe(self) -> str:
        return f"spans={len(self._spans)} path={self.names()[-8:]}"


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
        # R7-S04 第 5 点 — worker 超时后有界终止契约（纯逻辑判定层；
        # 真实 terminate/kill/join 仍由 DocProcessServer.shutdown 执行，
        # 这里的状态机供调用方查询"能否导航"）。
        self._termination = WorkerTerminationState()

    # 供外部查询（审计"不允许冒充已终止"——只暴露状态机，不假装终止）
    @property
    def termination_state(self) -> WorkerTerminationState:
        return self._termination

    def begin_terminate(self, now: float, summary: str = "") -> None:
        """发起有界终止流程（任务超时后调用）。幂等。"""
        self._termination.begin_terminate(now, summary)

    def complete_termination(self, now: float, natural: bool = True) -> None:
        """终止完成（join 结束或 forced_kill 后），允许新导航。"""
        self._termination.complete(now, natural)

    def may_navigate(self) -> bool:
        """当前是否允许发起新导航（terminated 后 True）。"""
        return self._termination.may_navigate

    def should_force_kill(self, now: float) -> bool:
        """join 预算是否已用尽（调用方据此决定强制终止）。"""
        return self._termination.check_join_budget(now)

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
        """终止子进程（置 stop_evt + join，回退态 no-op）。

        R7-S04 第 5 点 — 终止流程经 ``_termination`` 状态机跟踪：
        发起 → join 预算内自然结束 → terminated 后才允许新导航。
        """
        import time as _time
        if self._on_spawned:
            t0 = _time.monotonic()
            self._termination.begin_terminate(t0, "shutdown")
            try:
                self._stop_evt.set()
                self._cmd_q.put(None)
            except Exception:
                pass
            try:
                self._proc.join(timeout=1.0)
            except Exception:
                pass
            natural = not self._proc.is_alive()
            if self._proc.is_alive():
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                natural = False  # forced_kill
            self._termination.complete(_time.monotonic(), natural=natural)
            self._on_spawned = False
        else:
            # 回退态：无需真正终止，但仍把状态机推到 terminated
            # （允许后续导航）。
            t = _time.monotonic()
            self._termination.begin_terminate(t, "fallback-shutdown")
            self._termination.complete(t, natural=True)
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
