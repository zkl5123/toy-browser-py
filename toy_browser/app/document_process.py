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
# R7-S01 第 5 点 — 有限 IPC 消息 schema 校验（纯逻辑判定层，无运行时依赖）。
from toy_browser.scripting.script_ipc import (
    validate_command, validate_frame, MessageBudget as _IPC_Budget,
)
# R7-A02 第 4 点 — 连续帧 buffer 布局契约 + 父进程可见面校验（纯逻辑）。
from toy_browser.app.frame_buffer import FrameBufferSpec, FramePayloadContract
# R7-S04 第 4 点 — 页面维度独立资源限额（纯逻辑判定层，IPC 队列维度）。
from toy_browser.network.page_limits import (
    PageResourceLimits, PageDimension as _PageDim,
)


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
    # R7-A02 第 4 点 — 该帧像素的**连续 buffer 布局**（可选；None =
    # 本帧不携带像素 buffer，如纯导航 / stop 帧）。父进程据
    # :class:`FrameBufferSpec` 申请 / 校验共享段大小，**不**逐像素
    # 复制、不接触 worker 内部 Host。真实跨进程共享段（mmap /
    # posix 共享）的执行路径留活显示批次，本字段只描述布局。
    frame_spec: Optional[FrameBufferSpec] = None


# ---------------------------------------------------------------------------
# G07 step 4 — 文档进程 worker（进程内长期持有文档身份）
# ---------------------------------------------------------------------------

#: R7-A02 第 5 点 — 错误摘要最大长度（与 ``FramePayloadContract`` 的
#: ``_MAX_STR_LEN`` 同口径：父进程只接收有限摘要，防 worker 把大
#: 文本 / 对象 repr 塞进帧）。
_MAX_ERROR_SUMMARY = 128


def _limited_error_summary(exc: BaseException) -> str:
    """R7-A02 第 5 点 — 把异常转成**有限摘要**（类别 + 截断消息）。

    坏脚本 / 渲染失败的诊断只以有限摘要跨进程/跨层传递（审计
    "日志只记录有限摘要"同口径）；不把 traceback / 宿主对象图带出
    worker。
    """
    name = type(exc).__name__ or "Error"
    msg = str(exc).strip() or "(no message)"
    if len(msg) > 64:
        msg = msg[:64] + "..."
    return f"script:{name}:{msg}"[:_MAX_ERROR_SUMMARY]


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
        # R7-A02 第 2 点 — 按 tab 持有**真实文档工作容器**
        # （:class:`DocumentState` + 渲染管线产物）。navigate 时解析
        # + JS + 样式 + 布局 + 光栅化全在 worker 内执行（真实文档
        # 工作，不再只回身份计数）；resize/scroll 走重排（只 bump
        # layout，不重跑脚本）。``_doc_states`` 持有每个 tab 的
        # DocumentState（DOM / Host / 资源 / 版本长期留在 worker 进程，
        # G07 step 4"DOM 长期留该进程"）；``_last_bitmap`` 持有最近
        # 渲染的 :class:`Bitmap`（紧凑，可跨进程传像素句柄 / 尺寸）。
        # 默认 None（纯身份模式，向后兼容既有协议回归）；调用方按
        # 需在 navigate / resize 时填 payload 里的 html / css / 几何
        # 才会触发真实文档工作（见 :meth:`handle` 的
        # ``_do_doc_work`` 分支）。
        self._doc_states: dict[int, object] = {}
        self._last_bitmap: dict[int, object] = {}
        # R7-A02 第 4 点 — 连续帧 buffer 句柄：每次成功渲染分配一个
        # 单调递增的 buffer id（父进程据此区分"哪一块像素"），句柄
        # 只描述布局（id/size/format/width/height，见
        # :meth:`buffer_handle_for`），**不**携带像素数据本身——像素
        # 留在 worker（回退态父进程可经 :meth:`last_bitmap` 取；多核态
        # 跨进程共享段留活显示批次）。
        self._buffer_seq: int = 0
        self._last_buffer_id: dict[int, int] = {}
        # R7-A02 第 5 点 — 每 tab 最近一次文档工作错误摘要（坏脚本 /
        # 渲染失败）：错误**不传播**出 worker（传播会杀死回退态的
        # 父进程或子进程循环 → 整池挂 → 连累其他标签），转成该 tab
        # 的错误帧（ok=False + error_category），其他 tab 不受影响。
        self._last_doc_error: dict[int, str] = {}
        # R7-A02 第 6 点 — 标记本 worker 是否执行过**真实**文档工作
        # （解析 / 样式 / 布局 / 绘制）。:func:`detect_doc_work_mode`
        # 据此把模式判为 WORKER_FULL（而非据名称误判）；未执行过
        # 真实文档工作时保持 WORKER_IPC。
        self._has_done_doc_work: bool = False

    # -- R7-A02 第 5 点 — 坏脚本隔离（单 tab 失败不连累其他标签） -------

    def doc_error_for(self, tab: int):
        """该 tab 最近一次文档工作错误摘要（有限长字符串；无错误
        返回 None）。**不冒充通过**：只记录"这个 tab 失败了"，
        不宣称错误原因已被完整诊断。"""
        return self._last_doc_error.get(tab)

    def _do_doc_work(self, tab: int, cmd: "DocCommand",
                     payload: dict):
        """R7-A02 第 2 点 — 在 worker 内执行真实文档工作（解析 / JS /
        样式 / 布局 / 绘制），复用 :func:`document_renderer.render_
        document_state` 管线（不重复造轮子，C01 唯一修改入口）。

        * ``navigate`` → 新文档：解析 + 脚本初始化（``new_document``，
          脚本初始化计数 +1）+ 样式 / 布局 / 光栅化。
        * ``resize`` / ``scroll`` → 同文档重排：复用已有 DOM / Host，
          只 bump ``layout_revision`` + 重解样式 / 布局 / 光栅化
          （``render_document_state_restate``，不重建 Host、不重跑
          脚本——验收"重排后点击监听仍在"）。

        仅在 payload 带 html / css 或 tab 已有 DocumentState（navigate
        时经 html 解析）才真正执行；否则保持既有纯身份契约（向后
        兼容既有协议回归——不发 html 的 navigate 仍只回身份计数）。

        **不冒充通过**：本路径是真实 CPU 工作（解析 / 样式 / 布局 /
        绘制），但跨进程的**像素共享段**（mmap / posix）仍由
        :meth:`frame_spec_for` 描述布局，真实共享段执行留活显示
        批次；本批只落地"文档工作在 worker 进程执行"的判定与
        可测子集，不把"已共享像素"当通过。

        R7-A02 第 5 点 — 坏脚本 / 渲染失败**不传播**：异常在本方法
        内兜住，转成有限摘要（截断 + 类别）返回（None = 成功）。
        传播会杀死回退态的父进程或子进程循环（→ 整池挂 → 连累
        其他标签），那是审计点名的"坏脚本影响其他标签"。
        """
        from toy_browser.app import document_renderer as _rend
        html = payload.get("html", "")
        css = payload.get("css", "")
        width = int(payload.get("w", payload.get("width", 800)) or 800)
        height = int(payload.get("h", payload.get("height", 600)) or 600)

        try:
            if cmd.kind == DOC_CMD_NAVIGATE:
                doc = self._doc_states.get(tab)
                if doc is None:
                    from toy_browser.app.document_state_full import DocumentState
                    doc = DocumentState()
                    self._doc_states[tab] = doc
                # 网络等待（真实 navigate 时由调用方先 fetch 再经 payload
                # 传 html；本层只做"拿到 html 后的解析 / 样式 / 布局 /
                # 绘制"——网络等待在活显示批次接，本批标注"未实现"）。
                if html:
                    bm = _rend.render_document_state(
                        doc, html, css_text=css, width=width, height=height,
                        base_url=payload.get("url", ""))
                    self._last_bitmap[tab] = bm
                    self._has_done_doc_work = True
                    self._note_rendered(tab)
            elif cmd.kind in (DOC_CMD_RESIZE, DOC_CMD_SCROLL):
                doc = self._doc_states.get(tab)
                if doc is not None and doc.dom_tree is not None:
                    bm = _rend.render_document_state_restate(
                        doc, css_text=css, width=width, height=height)
                    self._last_bitmap[tab] = bm
                    self._has_done_doc_work = True
                    self._note_rendered(tab)
        except Exception as e:
            # 坏脚本 / 渲染失败 → 单 tab 错误摘要（不传播、不杀进程）
            summary = _limited_error_summary(e)
            self._last_doc_error[tab] = summary
            return summary
        self._last_doc_error.pop(tab, None)
        return None

    def doc_state_for(self, tab: int):
        """R7-A02 第 2 点 — 该 tab 持有的 :class:`DocumentState`
        （真实文档工作容器；未执行过真实文档工作返回 None）。"""
        return self._doc_states.get(tab)

    def last_bitmap(self, tab: int):
        """R7-A02 第 2 点 — 该 tab 最近渲染的 :class:`Bitmap`
        （紧凑像素 / 尺寸，跨进程呈现层消费；未渲染返回 None）。"""
        return self._last_bitmap.get(tab)

    def _note_rendered(self, tab: int) -> None:
        """R7-A02 第 4 点 — 一次成功渲染：分配单调递增 buffer id。

        父进程经帧 payload 的 ``buffer`` 句柄引用"哪一块像素"；
        句柄的 size/format/width/height 由 :meth:`buffer_handle_for`
        按实际渲染几何算出（布局描述，不携带像素数据）。
        """
        self._buffer_seq += 1
        self._last_buffer_id[tab] = self._buffer_seq

    def buffer_handle_for(self, tab: int):
        """R7-A02 第 4 点 — 该 tab 最近渲染帧的 **buffer 句柄**（布局
        描述，:class:`FramePayloadContract` 白名单内的有限 dict：
        ``{id, size, format, width, height}``）。

        * ``size`` = :meth:`FrameBufferSpec.total_bytes`
          （``width × height × 3``，rgb8）——父进程据此申请 / 校验
          buffer 大小，**不**逐像素复制；
        * 句柄**不**携带像素数据本身（像素留 worker；回退态父进程经
          :meth:`last_bitmap` 取，多核态跨进程共享段留活显示批次）；
        * 未渲染过返回 None（该 tab 无像素帧）。

        **不冒充通过**：多核态（子进程存活）下父进程拿到句柄也只能
        描述布局——像素是否已到父进程由呈现层按
        :meth:`DocProcessServer.is_spawned` 如实区分，不把"有句柄"
        当"已共享像素"。
        """
        bm = self._last_bitmap.get(tab)
        if bm is None:
            return None
        w, h = int(bm.width), int(bm.height)
        return {"id": int(self._last_buffer_id.get(tab, 0)),
                "size": w * h * 3,
                "format": "rgb8",
                "width": w,
                "height": h}

    def has_done_doc_work(self) -> bool:
        """R7-A02 第 6 点 — 本 worker 是否执行过**真实**文档工作
        （解析 / 样式 / 布局 / 绘制）。:func:`detect_doc_work_mode`
        据此判 WORKER_FULL（而非 WORKER_IPC）；未执行过则保持
        WORKER_IPC（不据名称宣称多核渲染）。"""
        return self._has_done_doc_work

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
            # R7-A02 第 2 点 — 真实文档工作：navigate 时 payload 带
            # html（解析 / JS / 样式 / 布局 / 绘制）在 worker 内执行。
            # 不破坏既有身份契约：身份计数照旧；真实文档工作后帧
            # payload 多带渲染几何（w/h），父进程经 frame_spec_for
            # 取像素布局。无 html（网络等待留活显示批次）时只做
            # 身份 bump，不发渲染帧。
            if cmd.payload.get("html"):
                _err = self._do_doc_work(tab, cmd, cmd.payload)
                # R7-A02 第 5 点 — 坏脚本隔离：渲染失败 → 该 tab 的
                # 错误帧（ok=False + error_category=script + 有限
                # 摘要），**不**传播（传播会杀回退态父进程 / 子进程
                # 循环，连累其他标签）。payload 只带白名单键 +
                # error_category 摘要（<=128，过父进程可见面校验）。
                if _err is not None:
                    return DocFrame(tab_id=tab, seq=cmd.seq, ok=False,
                                    error_category="script",
                                    navigation_id=nav_id,
                                    payload={
                                        "url": cmd.payload.get("url", ""),
                                        "kind": cmd.kind,
                                        "doc_id": self._doc_id[tab],
                                        "frame_id": self._frame_id[tab],
                                        "navigation_id": nav_id,
                                        "error_category": _err})
                # R7-A02 第 2/4 点 — 真实文档工作帧：payload 带白名单键
                # （kind/url/frame_id/doc_id/navigation_id）+ **buffer
                # 句柄**（布局引用，不携带像素数据）；帧挂
                # :class:`FrameBufferSpec`（连续 buffer 布局描述）——
                # 父进程据此申请 / 校验 buffer 大小、经句柄引用像素，
                # **不**逐像素复制、不接触 worker 内部 Host。几何 w/h
                # 仍不入帧 payload 顶层（非白名单）——布局走
                # frame_spec / buffer 句柄两个专用通道。
                _p = {"url": cmd.payload.get("url", ""),
                      "kind": cmd.kind,
                      "doc_id": self._doc_id[tab],
                      "frame_id": self._frame_id[tab],
                      "navigation_id": nav_id}
                _spec = None
                bm = self._last_bitmap.get(tab)
                if bm is not None:
                    _spec = FrameBufferSpec(format="rgb8",
                                            width=int(bm.width),
                                            height=int(bm.height))
                    _p["buffer"] = self.buffer_handle_for(tab)
                return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                                navigation_id=nav_id,
                                frame_spec=_spec, payload=_p)
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            navigation_id=nav_id,
                            payload={"url": cmd.payload.get("url", ""),
                                     "doc_id": self._doc_id[tab],
                                     "frame_id": self._frame_id[tab],
                                     "navigation_id": nav_id})
        # R7-A02 第 2 点 — 真实文档工作：tab 已有 DocumentState
        # （resize / scroll 重排，不重建 Host、不重跑脚本）时在 worker
        # 内执行。navigate 的 html 路径已在上面处理。
        _rendered = False
        if tab in self._doc_states and cmd.kind in (DOC_CMD_RESIZE,
                                                    DOC_CMD_SCROLL):
            # R7-A02 第 4 点 — 用 buffer id 是否推进判定"本次命令
            # 是否真实产出新像素"（只给本命令渲染出的帧挂布局）。
            _bid0 = self._last_buffer_id.get(tab, 0)
            _err = self._do_doc_work(tab, cmd, cmd.payload)
            _rendered = self._last_buffer_id.get(tab, 0) != _bid0
            # R7-A02 第 5 点 — 重排失败同样转该 tab 错误帧（不传播）
            if _err is not None:
                self._frame_id[tab] += 1
                return DocFrame(tab_id=tab, seq=cmd.seq, ok=False,
                                error_category="script",
                                navigation_id=cmd.navigation_id
                                or self._nav_id[tab],
                                payload={"frame_id": self._frame_id[tab],
                                         "error_category": _err})
        if cmd.kind == DOC_CMD_STOP:
            # 取消收尾：在途帧标 cancelled（G08 step 3 不强杀）
            self._frame_id[tab] += 1
            return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                            cancelled=True,
                            navigation_id=cmd.navigation_id or self._nav_id[tab],
                            payload={"frame_id": self._frame_id[tab]})
        # input / resize / scroll：同文档 bump 帧（几何 / 编辑重算）
        self._frame_id[tab] += 1
        _p = {"kind": cmd.kind,
              "frame_id": self._frame_id[tab]}
        # R7-A02 第 4 点 — 本命令真实渲染过 → 帧带连续 buffer 布局
        # （frame_spec + payload buffer 句柄）；未渲染（input / 无
        # DocumentState 的 resize）→ 纯身份帧（不挂旧位图的布局）。
        _spec = None
        if _rendered:
            bm = self._last_bitmap.get(tab)
            if bm is not None:
                _spec = FrameBufferSpec(format="rgb8",
                                        width=int(bm.width),
                                        height=int(bm.height))
                _p["buffer"] = self.buffer_handle_for(tab)
        return DocFrame(tab_id=tab, seq=cmd.seq, ok=True,
                        navigation_id=cmd.navigation_id or self._nav_id[tab],
                        frame_spec=_spec, payload=_p)

    # -- 关标签 -------------------------------------------------------------

    def drop_tab(self, tab_id: int) -> int:
        """G09 step 4 — 丢弃该标签全部在途文档身份（不泄漏）。
        返回被丢弃的 tab 计数（存在则 1，否则 0）。"""
        dropped = 0
        for m in (self._doc_id, self._nav_epoch, self._frame_id,
                  self._nav_id, self._doc_states, self._last_bitmap,
                  self._last_doc_error, self._last_buffer_id):
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

    # -- R7-A02 第 4 点 — 帧像素的连续 buffer 布局 -------------------------

    @staticmethod
    def frame_spec_for(payload: dict) -> Optional["FrameBufferSpec"]:
        """由紧凑 payload 里的几何（``w``/``h`` 或
        ``width``/``height``）描述一帧像素的**连续 buffer 布局**。

        R7-A02 第 4 点：帧用连续 buffer（对齐 :class:`CPUImage` 的
        ``bytearray``），父进程据 :class:`FrameBufferSpec` 申请 /
        校验共享段大小，**不**逐像素复制、不接触 worker 内部
        Host。本方法只做**布局描述**（纯逻辑）——真实跨进程共享
        段（mmap / posix）的执行路径留活显示批次，不在此冒充
        "已经共享了"。

        只认**几何键**（``w``/``h``/``width``/``height``）；payload
        缺几何（navigate / stop / input 帧，如只带 url / kind /
        frame_id）→ 返回 ``None``（本帧不携带像素 buffer）。
        rgb8 缺省（与 :class:`CPUImage` 参考实现同口径）。
        """
        w = payload.get("w", payload.get("width", 0))
        h = payload.get("h", payload.get("height", 0))
        # 只认几何键；navigate / stop / input 帧不带几何 → 无像素
        # buffer（None）。frame_id / doc_id / kind / url 等非几何键
        # 不触发 buffer（这些是 C01 身份摘要，不是像素几何）。
        if "w" not in payload and "width" not in payload:
            return None
        try:
            w, h = int(w), int(h)
        except (TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return FrameBufferSpec(format="rgb8", width=w, height=h)


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
        # R7-S04 第 4 点 — IPC 队列独立限额（纯逻辑判定层）：在途
        # 命令 + 在途帧的合计超过上限即拒收新命令（防恶意 / 失控
        # 页面把 IPC 队列撑爆打穿父进程）。真实截断行为由 post 的
        # 拒收分支保证；这里只给判定与记账。
        self._ipc_limits = PageResourceLimits()
        self._ipc_rejected = 0

    @property
    def ipc_queue_limit(self) -> int:
        """R7-S04 第 4 点 — IPC 队列当前在途上限（0 = 未启用）。"""
        return self._ipc_limits.limit(_PageDim.IPC_QUEUE)

    @ipc_queue_limit.setter
    def ipc_queue_limit(self, value: int) -> None:
        self._ipc_limits.limits[_PageDim.IPC_QUEUE] = int(value)

    @property
    def ipc_rejected(self) -> int:
        """R7-S04 第 4 点 — 因 IPC 队列限额被拒收的命令数（诊断）。"""
        return self._ipc_rejected
        # R7-S04 第 4 点 — 页面维度独立资源限额（IPC 队列维度）：
        # 在途命令数超过上限即拒收（防恶意 / 失控页面把 IPC 队列
        # 撑爆打穿父进程）。纯逻辑判定层，真实截断行为由调用点保证。
        self._page_limits = PageResourceLimits()
        self._stats._ipc_rejected = 0

    def __init_subclass__(cls, **kw):  # pragma: no cover - doc only
        pass

    # -- 查询 -------------------------------------------------------------

    @property
    def stats(self) -> DocumentProcessStats:
        return self._stats

    def is_fallback(self) -> bool:
        """是否已回退到内置同步 worker（无真实进程 / 进程崩溃）。"""
        return self._stats.on_fallback

    # -- 命令投递（UI 主线程 → 文档进程，轻量不阻塞） ---------------------

    def inflight(self) -> int:
        """R7-A02 第 3 点 — 当前在途量（在途命令 + 在途帧）。

        供 :class:`BackpressureGate` 取背压深度：慢页 / 坏脚本期间
        队列无界增长时 UI 侧据此**拒收**新命令（不把 UI 拖死）。
        公开方法而非让调用方读私有字段（IPC 面不泄漏内部结构）。
        """
        return len(self._pending_cmds) + len(self._pending_frames)

    def post(self, cmd: DocCommand) -> bool:
        """G07 step 4 — 投递一条紧凑命令。已关 tab 的命令直接丢弃
        （返回 False，不串页）；否则入在途队列（真实进程下经 pipe
        发往 worker，本纯逻辑核心先暂存）。"""
        if cmd.tab_id in self._closed_tabs:
            # 关标签后旧命令丢弃（G09 step 4 不泄漏）
            return False
        # R7-S04 第 4 点 — IPC 队列独立限额（分配前防护）：在途命令
        # + 在途帧的合计已达上限则拒收新命令（防恶意 / 失控页面把
        # IPC 队列撑爆打穿父进程）。未启用（上限 0）时恒放行。
        _inflight = len(self._pending_cmds) + len(self._pending_frames)
        if not self._ipc_limits.admit(_PageDim.IPC_QUEUE, 1) \
                or (self._ipc_limits.limit(_PageDim.IPC_QUEUE) > 0
                    and _inflight >= self._ipc_limits.limit(_PageDim.IPC_QUEUE)):
            self._ipc_rejected += 1
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

    def abandon_tab(self, tab_id: int) -> int:
        """标签销毁：该 tab 的在途取消**作废**（不再等确认）。

        语义与 :meth:`ack_tab` **不同**，不得混用：

        * ``ack_tab`` — worker 回了 cancelled 帧，取消**被确认完成**；
        * ``abandon_tab`` — 标签已销毁（在途命令 / 帧 / 文档身份
          全部丢弃），取消请求本身**已无意义**，不是"取消完成了"。

        按"不冒充通过"，关标签不得用 ``ack_tab`` 冒充"取消已被
        worker 确认"——那是假的确认。返回作废条数。
        """
        hits = [t for t, (tb, _nv) in self._pending.items() if tb == tab_id]
        for t in hits:
            del self._pending[t]
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
                # R7-A02 第 5 点 — 单条命令的意外异常**不得杀死子进程**：
                # 旧行为是回 {"error": ...} 后 break（子进程退出 → 整池
                # 挂 → 连累其他标签）。现在转成该 tab 的错误帧继续循环
                # （坏脚本 / 坏命令只影响自己那个标签）。
                try:
                    result_q.put(DocFrame(
                        tab_id=cmd.tab_id, seq=cmd.seq, ok=False,
                        error_category="script",
                        navigation_id=cmd.navigation_id,
                        payload={"error_category":
                                 _limited_error_summary(e)}))
                except Exception:
                    # 帧都构造不出（极端情况）才回退错误摘要，仍不退出。
                    result_q.put({"error": repr(e)})
                continue
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
        # R7-A02 第 4 点 — 父进程可见面校验：worker 传回的帧 payload
        # 只允许有限摘要 + buffer 句柄，不得携带 Host / 对象图。
        self._frame_contract = FramePayloadContract()

    def _frame_parent_visible(self, frame: DocFrame) -> bool:
        """R7-A02 第 4 点 — 该帧对父进程是否可见（payload 有限性）。

        经 :meth:`FramePayloadContract.validate` 校验：合法 → True；
        违规（worker 试图把 Host / 对象图 / 超限摘要塞进 payload）
        → False（调用方拒收，不计入 frames_drained）。"""
        ok, _reasons = self._frame_contract.validate(frame.payload)
        return ok

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
                # R7-A02 第 5 点 — terminate 是**异步**发信号：发完立即
                # 查 is_alive() 可能仍为 True（进程还没死透），"关闭后
                # 无遗留子进程"就不可测。补一次有界 join，让回收有界
                # 且可断言。
                try:
                    self._proc.join(timeout=0.5)
                except Exception:
                    pass
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

    def has_done_doc_work(self) -> bool:
        """R7-A02 第 2 点 — worker 是否执行过**真实**文档工作。

        读**父进程内置同步 worker**（:attr:`_worker`）的
        :meth:`DocumentProcessWorker.has_done_doc_work` 标记——
        回退态 / 子进程未 spawn 时，真实解析 / 样式 / 布局 / 绘制
        就发生在这个父进程 worker 里，父进程可直接观测。

        **不冒充通过**（保守口径）：子进程存活（多核态）时，真实
        文档工作在**子进程**的独立 ``DocumentProcessWorker`` 里执行，
        父进程**无法直接观测**子进程是否做了真实文档工作（需子进程
        经 IPC 回报 ``doc_work`` 标记——见开放问题"多核态判
        WORKER_FULL 需 IPC 协议扩展 + 活显示实测"）。本方法在子进程
        存活时**不**代子进程返回 True（父进程侧标记未翻转则
        False），调用点据此判 WORKER_IPC 而非 WORKER_FULL——不据
        "子进程 spawn 了"宣称已多核渲染。"""
        return self._worker.has_done_doc_work()

    def last_bitmap_for(self, tab_id: int):
        """R7-A02 第 4 点 — 该 tab 最近渲染的位图（呈现层取真实像素）。

        读**父进程内置同步 worker**（回退态真实文档工作发生在此，
        父进程同进程可直接取像素）。**不冒充通过**：子进程存活
        （多核态）时位图在子进程里，本方法返回 None——父进程不
        代子进程"变"出像素；跨进程像素传递（共享内存）留活显示
        批次，多核态呈现层据此走"仅布局"路径（见
        :meth:`buffer_handle_for` 与 gui
        :meth:`_r7_a02_apply_frame_buffer`）。"""
        return self._worker.last_bitmap(tab_id)

    def buffer_handle_for(self, tab_id: int):
        """R7-A02 第 4 点 — 该 tab 最近渲染帧的 buffer 句柄（布局描述：
        ``{id, size, format, width, height}``，不携带像素数据）。

        与 :meth:`last_bitmap_for` 同源（父进程内置同步 worker）；
        子进程存活时同样返回 None（父进程无子进程侧句柄表——
        多核态帧自带句柄随 pipe 传回，呈现层直接用帧上的）。"""
        return self._worker.buffer_handle_for(tab_id)

    # -- 命令投递（UI 主线程 → 文档进程，轻量不阻塞） ------------------------

    def inflight(self) -> int:
        """R7-A02 第 3 点 — 当前在途量（在途命令 + 在途帧，含子进程
        存活时待经 pipe 发出的命令）。

        供 :class:`BackpressureGate` 取背压深度——慢页 / 坏脚本期间
        UI 侧据此拒收新命令（队列无界增长会拖死 UI）。"""
        return len(self._pending_cmds) + len(self._pending_frames)

    def post(self, cmd: DocCommand) -> bool:
        """投递紧凑命令。已关 tab 直接丢弃（False）；子进程存活时
        经 pipe 发往 worker（多核），否则回退到父进程内置同步暂存。

        R7-S01 第 5 点 — 投递前做有限 IPC 校验（消息 schema）：
        字段类型 / kind 枚举 / payload 有限性，任一失败拒收（False），
        不静默放行。"""
        if cmd.tab_id in self._closed_tabs:
            return False
        # R7-S01 第 5 点 — 有限 IPC 校验（拒绝非法消息，不静默放行）。
        _r = validate_command(
            tab_id=cmd.tab_id, kind=cmd.kind, payload=cmd.payload,
            seq=cmd.seq, navigation_id=cmd.navigation_id)
        if _r is not None:
            return False  # 拒收：不计数、不入队、不回 pipe。
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
        （G09 step 4）。

        R7-S01 第 5 点 — 所有帧（父侧暂存 + 经 pipe 收回的）都过
        有限 IPC 校验，非法帧拒收（不计入 frames_drained）。

        R7-A02 第 4 点 — 每帧经父进程可见面校验（``FramePayload
        Contract.validate``）：worker 传回 payload 只允许有限摘要 +
        buffer 句柄引用，不得携带 Host / 对象图。违规帧拒收
        （不计入 frames_drained，审计"父进程不接触 worker 内部
        Host"）。"""
        _validated: list[DocFrame] = []
        for _pf in self._pending_frames:
            _fr = validate_frame(
                _pf.tab_id, _pf.seq, _pf.ok,
                _pf.error_category, _pf.payload, _pf.navigation_id)
            # R7-A02 第 4 点 — 父进程可见面校验（有限摘要 + buffer
            # 句柄，不得含 Host / 对象图）。
            _parent_ok = self._frame_parent_visible(_pf)
            if _fr is None and _parent_ok \
                    and _pf.tab_id not in self._closed_tabs:
                _validated.append(_pf)
            else:
                self._stats.dropped_tab_frames += 1
        frames = _validated
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
                    # R7-S01 第 5 点 — 经 pipe 收回的帧也过有限 IPC
                    # 校验（防 worker 回传宿主对象/非法类别）。
                    _fr = validate_frame(
                        item.tab_id, item.seq, item.ok,
                        item.error_category, item.payload,
                        item.navigation_id)
                    # R7-A02 第 4 点 — 父进程可见面校验。
                    _parent_ok = self._frame_parent_visible(item)
                    if _fr is not None or not _parent_ok:
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
                # R7-S01 第 5 点 — worker 产出的帧也须过有限 IPC 校验
                # （回传结果只允许有限摘要，不允许宿主对象）。
                _fr = validate_frame(
                    frame.tab_id, frame.seq, frame.ok,
                    frame.error_category, frame.payload,
                    frame.navigation_id)
                # R7-A02 第 4 点 — 父进程可见面校验（有限摘要 +
                # buffer 句柄，不得含 Host / 对象图）。
                _parent_ok = self._frame_parent_visible(frame)
                if _fr is not None or not _parent_ok:
                    # 非法帧 / 越面帧拒收，不计入 drained（不静默放行）。
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
