"""R7-A02 第 6 点 — 文档工作模式 + 能力声明真实性校验（纯逻辑）。

Round-7 实施方案 R7-A02「把实际页面工作移出 UI 线程」第 6 点：

> 在工作进程未完全接通前保留显式单进程调试模式，但**默认模式的
> 性能/安全声明必须真实**。

审计 R7-A02 证据（本模块要回答的原话）：

> "现有 worker 存在且 spawn，但默认导航仍同步，worker handle 主要
> 返回 URL/版本状态；**不能据名称宣称已多核渲染**。"

问题本质：代码里有 ``worker`` / ``spawn`` / ``DocumentProcess*``
这些**名词**，很容易被当成"已多核渲染"的证据。但真正的判据是
**文档工作（解析 / 样式 / 布局 / 绘制）在哪执行**：

* 默认导航 :meth:`gui._commit_navigation` 仍**同步**调用
  ``page.load_url()`` → 文档工作在 UI 主线程 → **单进程**。
* 既有 :class:`DocumentProcessWorker` 处理的是**紧凑身份计数**
  （doc_id / nav_epoch / frame_id / navigation_id），**不**执行
  真实解析 / 样式 / 布局 / 绘制 → 即使真实 spawn 了子进程，也
  **不是**"已多核渲染"，只是"IPC 通了"。

本模块把这三档**显式化**，并给"声明"（claim）加**真实性校验**：

* :class:`DocWorkMode` — 三态（SINGLE_DEBUG / WORKER_IPC /
  WORKER_FULL），默认 :attr:`DocWorkMode.SINGLE_DEBUG`。
* :func:`detect_doc_work_mode` — 按**真实接线**探测当前模式
  （"默认导航是否同步" + "worker 是否执行真实文档工作" +
  "是否真实 spawn"），不靠名字猜。
* :class:`CapabilityClaim` — 一条性能 / 安全声明 + :meth:`is_truthful`
  校验：声明要求的模式 ≤ 实际模式才为真。**越级声明（如
  WORKER_IPC 下声明"已多核渲染"）判为不真实**并给出原因。
* :class:`SingleProcessDebugMode` — **显式**单进程调试模式开关
  （审计"保留显式单进程调试模式"），可观测、可断言，而不是
  隐式的"spawn 失败所以回退"。

与既有模块的关系（**不重复实现**）：
- :class:`shell_state.BackendInfo` 解决"backend 是不是真进程"
  （process / fallback / cpu）——**进程维度**。
- 本模块解决"文档工作在不在 worker 里做"——**工作位置维度**。
  两者正交：真进程 + 只做 IPC ≠ 多核渲染（本模块的 WORKER_IPC）。

设计边界（"不冒充通过"）：
- 本模块**只做模式判定与声明校验**（可测决策层），不实现真实
  worker 文档工作（第 2 点，留活批次）。
- 默认模式如实为 ``SINGLE_DEBUG``；在真实 worker 文档工作接通
  并经实测前，:func:`detect_doc_work_mode` 不得返回 WORKER_FULL。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# R7-A02 第 6 点 — 文档工作模式（三态）
# ---------------------------------------------------------------------------

#: 单进程调试：文档工作（解析 / 样式 / 布局 / 绘制）在 UI 主线程
#: **同步**执行。既有默认导航 :meth:`gui._commit_navigation` 即此态
#: （同步调 ``page.load_url``）。审计要求"保留显式单进程调试模式"。
MODE_SINGLE_DEBUG = "single_debug"

#: worker IPC：子进程真实 spawn 且 IPC 通，但 worker **只处理紧凑
#: 身份 / 命令**（doc_id / nav_epoch / frame_id / navigation_id），
#: **不**执行真实解析 / 样式 / 布局 / 绘制。即"管道通了，活还在
#: 主线程"——**不是**多核渲染（审计原话：不能据名称宣称已多核渲染）。
MODE_WORKER_IPC = "worker_ipc"

#: worker 全量：worker 持有 DocumentSession，执行真实网络等待 /
#: 解析 / JS / 样式 / 布局 / 绘制；UI 只提交命令、处理输入、呈现
#: 结果。这是 R7-A02 的真正目标态，**当前未接通**（第 2 点）。
MODE_WORKER_FULL = "worker_full"

VALID_DOC_WORK_MODES = (MODE_SINGLE_DEBUG, MODE_WORKER_IPC,
                        MODE_WORKER_FULL)

#: 模式序（用于声明校验：声明要求模式 ≤ 实际模式才真实）。
_MODE_RANK = {
    MODE_SINGLE_DEBUG: 0,
    MODE_WORKER_IPC: 1,
    MODE_WORKER_FULL: 2,
}

#: 各模式的真实能力（声明校验的依据；不夸大）。
_MODE_CAPABILITIES = {
    MODE_SINGLE_DEBUG: {
        "multi_core_rendering": False,   # 活在主线程
        "worker_isolation": False,       # 无进程隔离
        "ui_nonblocking_navigation": False,  # 导航同步阻塞
        "frame_ipc": False,
    },
    MODE_WORKER_IPC: {
        "multi_core_rendering": False,   # 关键：IPC 通 ≠ 多核渲染
        "worker_isolation": True,        # 子进程隔离（身份 / IPC）
        "ui_nonblocking_navigation": False,  # 默认导航仍同步
        "frame_ipc": True,
    },
    MODE_WORKER_FULL: {
        "multi_core_rendering": True,
        "worker_isolation": True,
        "ui_nonblocking_navigation": True,
        "frame_ipc": True,
    },
}


@dataclass
class DocWorkMode:
    """R7-A02 第 6 点 — 当前文档工作模式（含判定依据，可断言）。

    * ``mode`` — :data:`MODE_SINGLE_DEBUG` / :data:`MODE_WORKER_IPC`
      / :data:`MODE_WORKER_FULL`。
    * ``navigation_is_sync`` — 默认导航入口是否**同步**执行文档
      工作（真 = 活还在 UI 主线程）。
    * ``worker_does_doc_work`` — worker 是否执行**真实**解析 / 样式
      / 布局 / 绘制（真 = 活真的在 worker）。
    * ``worker_spawned`` — 子进程是否真实 spawn 且存活。
    * ``reason`` — 判定依据（人类可读，写进诊断区）。
    """

    mode: str = MODE_SINGLE_DEBUG
    navigation_is_sync: bool = True
    worker_does_doc_work: bool = False
    worker_spawned: bool = False
    reason: str = ""

    @property
    def rank(self) -> int:
        return _MODE_RANK.get(self.mode, 0)

    @property
    def is_single_debug(self) -> bool:
        return self.mode == MODE_SINGLE_DEBUG

    @property
    def is_worker_full(self) -> bool:
        """真正目标态（worker 执行真实文档工作）。当前默认 False。"""
        return self.mode == MODE_WORKER_FULL

    def capabilities(self) -> dict:
        """该模式的真实能力表（声明校验依据，不夸大）。"""
        return dict(_MODE_CAPABILITIES.get(self.mode,
                                           _MODE_CAPABILITIES[MODE_SINGLE_DEBUG]))

    def supports(self, capability: str) -> bool:
        """该模式是否真实具备 ``capability``（未知能力按 False，
        保守——不承认没定义过的声明）。"""
        return bool(self.capabilities().get(capability, False))

    def describe(self) -> str:
        base = (f"mode={self.mode} nav_sync={self.navigation_is_sync} "
                f"worker_doc_work={self.worker_does_doc_work} "
                f"spawned={self.worker_spawned}")
        return base + (f" reason={self.reason}" if self.reason else "")


def detect_doc_work_mode(navigation_is_sync: bool = True,
                         worker_does_doc_work: bool = False,
                         worker_spawned: bool = False,
                         reason: str = "") -> DocWorkMode:
    """R7-A02 第 6 点 — 按**真实接线**探测文档工作模式（不靠名字猜）。

    判定规则（严格，防"据名称宣称"）：

    * ``worker_does_doc_work`` 为真 → :data:`MODE_WORKER_FULL`
      （活真在 worker；这是唯一能支撑"多核渲染"的态）。
    * 否则若 ``worker_spawned`` 为真 → :data:`MODE_WORKER_IPC`
      （管道通了，但 worker 只做紧凑 IPC / 身份计数）。
    * 否则 → :data:`MODE_SINGLE_DEBUG`（默认：导航同步，活在主线程）。

    ``navigation_is_sync`` 单独记录（不改变档位，但写进
    :attr:`DocWorkMode.reason` 与诊断——"默认导航仍同步"是审计
    点名的证据，必须可见）。

    默认调用 ``detect_doc_work_mode()`` 返回 SINGLE_DEBUG——如实
    反映"默认导航仍同步、worker 不做真实文档工作"的现状。
    """
    if worker_does_doc_work:
        mode = MODE_WORKER_FULL
        if not reason:
            reason = "worker 执行真实解析/样式/布局/绘制"
    elif worker_spawned:
        mode = MODE_WORKER_IPC
        if not reason:
            reason = ("子进程已 spawn 且 IPC 通，但 worker 只处理紧凑"
                      "身份/命令，不执行真实文档工作（非多核渲染）")
    else:
        mode = MODE_SINGLE_DEBUG
        if not reason:
            reason = "默认导航同步执行，文档工作在 UI 主线程"
    return DocWorkMode(mode=mode,
                       navigation_is_sync=navigation_is_sync,
                       worker_does_doc_work=worker_does_doc_work,
                       worker_spawned=worker_spawned,
                       reason=reason)


# ---------------------------------------------------------------------------
# R7-A02 第 6 点 — 能力声明真实性校验
# ---------------------------------------------------------------------------

@dataclass
class CapabilityClaim:
    """R7-A02 第 6 点 — 一条性能 / 安全声明 + 真实性校验。

    * ``name`` — 声明名（如 ``"multi-core rendering"``）。
    * ``capability`` — 对应 :data:`_MODE_CAPABILITIES` 的能力键
      （``multi_core_rendering`` / ``worker_isolation`` /
      ``ui_nonblocking_navigation`` / ``frame_ipc``）。
    * ``mode`` — 声明时所处的实际模式。

    :meth:`is_truthful` 用**能力表**校验（不是比档位）：声明的
    capability 在当前模式下是否为真。越级声明（WORKER_IPC 下宣称
    "已多核渲染"）返回 False 并给出原因——这正是审计"不能据名称
    宣称已多核渲染"的可测闸门。
    """

    name: str = ""
    capability: str = ""
    mode: str = MODE_SINGLE_DEBUG

    def is_truthful(self) -> bool:
        """该声明在当前模式下是否真实（不夸大）。"""
        caps = _MODE_CAPABILITIES.get(self.mode,
                                      _MODE_CAPABILITIES[MODE_SINGLE_DEBUG])
        return bool(caps.get(self.capability, False))

    def untruthful_reason(self) -> str:
        """不真实时的原因串（真实时空串）。"""
        if self.is_truthful():
            return ""
        if self.mode not in _MODE_CAPABILITIES:
            return f"未知模式 {self.mode!r}，声明不可判定（按不真实处理）"
        if self.capability not in _MODE_CAPABILITIES[self.mode]:
            return f"声明能力 {self.capability!r} 未在模式 {self.mode!r} 定义（按不真实处理）"
        if self.capability == "multi_core_rendering":
            return (f"模式 {self.mode!r} 下文档工作不在 worker 执行，"
                    "不可宣称多核渲染（审计 R7-A02：不能据名称宣称）")
        return f"模式 {self.mode!r} 不支持能力 {self.capability!r}"

    def describe(self) -> str:
        ok = self.is_truthful()
        tag = "OK" if ok else "UNTRUTHFUL"
        base = f"claim={self.name!r} capability={self.capability!r} mode={self.mode!r} [{tag}]"
        if not ok:
            base += " " + self.untruthful_reason()
        return base


#: 常见"容易越级"的声明（审计点名场景），供回归直接断言。
CLAIM_MULTI_CORE_RENDERING = "multi-core rendering"


def claim_multi_core_rendering(mode: DocWorkMode) -> CapabilityClaim:
    """审计点名场景：宣称"已多核渲染"。

    在 SINGLE_DEBUG / WORKER_IPC 下**均不真实**——只有 worker 真的
    执行文档工作（WORKER_FULL）才成立。当前默认模式如实为
    SINGLE_DEBUG，故本声明默认 UNTRUTHFUL（这正是本模块的意义）。
    """
    return CapabilityClaim(name=CLAIM_MULTI_CORE_RENDERING,
                           capability="multi_core_rendering",
                           mode=mode.mode)


# ---------------------------------------------------------------------------
# R7-A02 第 6 点 — 显式单进程调试模式（不是隐式回退）
# ---------------------------------------------------------------------------

@dataclass
class SingleProcessDebugMode:
    """R7-A02 第 6 点 — **显式**单进程调试模式（审计要求"保留"）。

    与"spawn 失败所以回退"的区别：回退是**被动**的（意外），
    本模式是**主动 opt-in**的（开发者显式打开，用于单步调试文档
    工作）。必须可观测、可断言：

    * :attr:`enabled` — 是否显式开启。
    * :attr:`reason` — 开启理由（写进诊断，避免"忘了关"）。
    * :attr:`was_auto_fallback` — 是否由 spawn 失败**自动**进入
      （真 = 被动回退，需在诊断里与主动调试区分）。

    :meth:`effective_mode` 把显式调试 / 自动回退统一折算为
    :class:`DocWorkMode`——显式开启时**强制** SINGLE_DEBUG（即使
    worker 已 spawn），保证"调试态下活一定在主线程"。
    """

    enabled: bool = False
    reason: str = ""
    was_auto_fallback: bool = False

    def enable(self, reason: str = "developer opt-in") -> None:
        """显式开启单进程调试（主动）。"""
        self.enabled = True
        self.reason = reason

    def disable(self) -> None:
        """关闭显式调试（回到按真实接线探测的模式）。"""
        self.enabled = False
        self.reason = ""

    def mark_auto_fallback(self, reason: str = "spawn failed") -> None:
        """标记**被动**回退（spawn 失败 / worker 崩溃），与主动调试
        区分（诊断里必须能分辨这两种"单进程"）。"""
        self.was_auto_fallback = True
        self.reason = reason

    @property
    def is_debug_single_process(self) -> bool:
        """当前是否处于单进程态（显式调试 **或** 被动回退）。"""
        return self.enabled or self.was_auto_fallback

    def effective_mode(self, detected: DocWorkMode) -> DocWorkMode:
        """折算生效模式：显式调试 → 强制 SINGLE_DEBUG（活在主线程，
        便于单步）；否则用 ``detected``（按真实接线探测的结果）。"""
        if self.enabled:
            return DocWorkMode(
                mode=MODE_SINGLE_DEBUG,
                navigation_is_sync=True,
                worker_does_doc_work=False,
                worker_spawned=detected.worker_spawned,
                reason=f"显式单进程调试模式（{self.reason or 'opt-in'}）",
            )
        if self.was_auto_fallback:
            # 被动回退：档位退到 SINGLE_DEBUG（worker 不可用），但
            # reason 标明是"回退"而非"主动调试"。
            return DocWorkMode(
                mode=MODE_SINGLE_DEBUG,
                navigation_is_sync=True,
                worker_does_doc_work=False,
                worker_spawned=False,
                reason=f"worker 不可用自动回退（{self.reason or 'spawn failed'}）",
            )
        return detected

    def describe(self) -> str:
        if self.enabled:
            return f"single-process-debug(active, reason={self.reason!r})"
        if self.was_auto_fallback:
            return f"auto-fallback(reason={self.reason!r})"
        return "single-process-debug(off)"
