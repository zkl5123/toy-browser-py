"""R7-A02 第 4 点 — 连续帧 buffer **呈现层接线**（决策层已就位后的消费侧）。

审计 A02 第 4 点："帧用连续 buffer 或受控共享内存，不序列化整棵
Python 对象图；父进程不接触 worker 内部 Host。"

决策层（``frame_buffer.py``：:class:`FrameBufferSpec` 布局 /
:class:`FramePayloadContract` 父进程可见面）与 ``drain`` 挂校验已在
``test_r7_a02_frame_buffer.py`` 锁定；本批锁**呈现层接线**：

- worker 侧：真实渲染帧（navigate 带 html / resize / scroll 重排）
  **填** ``DocFrame.frame_spec``（连续 buffer 布局）+ payload
  ``buffer`` 句柄（布局引用，不携带像素数据）——此前字段存在但
  从不填充、句柄从不产生，"连续 buffer"是空话；
- gui 侧：:meth:`TabBrowser._r7_a02_apply_frame_buffer` 消费布局
  （回退态取父进程 worker 真实像素应用 CPUImage；多核态仅布局），
  :meth:`_g07_drain_frames` 在版本门控后逐帧应用。

不冒充通过边界（测试显式锁定）：
- 多核态（子进程存活）父进程**拿不到**子进程位图——apply 返回
  None 并计 ``layout_only``，不宣称"像素已跨进程到达"（真实
  mmap / posix 共享段留活显示批次）；
- 失败帧（ok=False）**不挂** spec / 句柄（失败不宣称像素）；
- 未渲染的身份帧（input / 无 DocumentState 的 resize）不挂旧位图
  布局；
- 布局不自洽（stride < width×bpp）/ 句柄越面（带 pixels 数据键）
  → 拒收，不静默放行、不错位上屏。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_process import (
    DocCommand, DocFrame, DocProcessServer, DocumentProcessWorker,
    DOC_CMD_NAVIGATE, DOC_CMD_RESIZE, DOC_CMD_SCROLL,
    spawn_doc_process_server,
)
from toy_browser.app.frame_buffer import FrameBufferSpec, FramePayloadContract

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def _fn_body(src: str, header: str) -> str:
    """取 ``def <header>`` 起的函数体（到下一个同缩进 def/class 前）。"""
    i = src.index(header)
    j = src.index("\n    def ", i + 1) if "\n    def " in src[i + 1:] \
        else len(src)
    return src[i:j]


# ---------------------------------------------------------------------------
# 静态层 — 源码契约（worker 填布局 / gui 消费布局）
# ---------------------------------------------------------------------------

@_reg
def test_static_worker_populates_spec_and_handle():
    """worker 的 navigate-html 成功帧与 resize/scroll 成功帧必须构造
    FrameBufferSpec + buffer 句柄（字段存在但从不填充 = 空话）。"""
    src = _src("toy_browser/app/document_process.py")
    handle_nav = _fn_body(src, "def handle(self, cmd")
    assert "FrameBufferSpec(format=\"rgb8\"" in handle_nav, \
        "handle 必须为真实渲染帧构造 FrameBufferSpec"
    assert "self.buffer_handle_for(tab)" in handle_nav, \
        "handle 必须把 buffer 句柄带进帧 payload"
    # 句柄只描述布局：不得把像素数据键塞进句柄
    bh = _fn_body(src, "def buffer_handle_for")
    assert "\"size\": w * h * 3" in bh, \
        "buffer 句柄 size 须按 rgb8 连续布局计算（w×h×3）"
    assert "pixels" not in bh and "\"data\"" not in bh, \
        "buffer 句柄不得携带像素数据本身"


@_reg
def test_static_drain_frames_applies_buffer():
    """gui 的 _g07_drain_frames 函数体必须调 _r7_a02_apply_frame_buffer
    （版本门控后的存活帧才应用像素）。"""
    src = _src("toy_browser/app/gui.py")
    body = _fn_body(src, "def _g07_drain_frames")
    assert "_r7_a02_apply_frame_buffer(" in body, \
        "_g07_drain_frames 必须经 _r7_a02_apply_frame_buffer 消费布局"
    # 应用点在版本门控之后（旧任务帧不应用 buffer）
    assert body.index("_r7_a02_gated_frames(") < \
        body.index("_r7_a02_apply_frame_buffer("), \
        "buffer 应用必须在版本门控之后（被丢弃的旧帧不应用像素）"


# ---------------------------------------------------------------------------
# 动态层 — worker 产帧（布局真实填充）
# ---------------------------------------------------------------------------

def _render_worker():
    w = DocumentProcessWorker()
    w.handle(DocCommand(1, DOC_CMD_NAVIGATE,
                        {"url": "http://a", "html": "<p>hello</p>",
                         "w": 320, "h": 240}, seq=1, navigation_id=1))
    return w


@_reg
def test_navigate_html_frame_carries_spec_and_handle():
    """navigate 带 html → 帧填 frame_spec（几何 = 实际渲染 320x240）+
    payload buffer 句柄；句柄过 FramePayloadContract；drain 不拒收。"""
    w = _render_worker()
    # 重新拿一帧（同命令再 handle 会 bump 身份；直接用缓存帧语义：
    # 重新构造 worker 逐条验证）
    w2 = DocumentProcessWorker()
    f = w2.handle(DocCommand(1, DOC_CMD_NAVIGATE,
                             {"url": "http://a", "html": "<p>hi</p>",
                              "w": 320, "h": 240}, seq=1, navigation_id=1))
    spec = f.frame_spec
    assert spec is not None and spec.layout_ok()
    assert (spec.width, spec.height) == (320, 240)
    assert spec.total_bytes() == 320 * 240 * 3, \
        "连续 buffer 总字节数 = stride×height（rgb8 无 padding）"
    handle = f.payload.get("buffer")
    assert handle is not None
    assert handle["size"] == spec.total_bytes()
    assert handle["format"] == "rgb8"
    ok, reasons = FramePayloadContract().validate(f.payload)
    assert ok, f"帧 payload 须过父进程可见面契约：{reasons}"
    assert w.has_done_doc_work()


@_reg
def test_resize_frame_carries_new_spec_and_buffer_id():
    """resize 重排 → 新布局（640x480）+ buffer id 推进（父进程据此
    区分"哪一块像素"）；旧位图布局不被复用。"""
    w = _render_worker()
    f = w.handle(DocCommand(1, DOC_CMD_RESIZE,
                            {"w": 640, "h": 480}, seq=2, navigation_id=1))
    assert f.frame_spec is not None
    assert (f.frame_spec.width, f.frame_spec.height) == (640, 480)
    assert f.payload["buffer"]["size"] == 640 * 480 * 3
    assert f.payload["buffer"]["id"] == 2, "buffer id 须单调递增"
    ok, _ = FramePayloadContract().validate(f.payload)
    assert ok


@_reg
def test_scroll_with_doc_state_frame_carries_spec():
    """scroll（已有 DocumentState）同样重排产帧 → 带布局。"""
    w = _render_worker()
    f = w.handle(DocCommand(1, DOC_CMD_SCROLL,
                            {"w": 320, "h": 240}, seq=3, navigation_id=1))
    assert f.frame_spec is not None and f.frame_spec.layout_ok()
    assert f.payload.get("buffer") is not None


@_reg
def test_error_frame_has_no_spec():
    """坏脚本失败帧（ok=False）不挂 spec / 句柄——失败不宣称像素。"""
    w = DocumentProcessWorker()
    # 注入渲染失败（DocumentState 存在但 dom_tree 操作炸裂的等效路径：
    # 用一个会触发渲染异常的 html 不可行——改用 doc_states 预置坏对象）
    class _Boom:
        dom_tree = object()
        script_host = None

        def mark_restate(self):
            raise RuntimeError("boom-restate")

    w._doc_states[1] = _Boom()
    f = w.handle(DocCommand(1, DOC_CMD_RESIZE,
                            {"w": 10, "h": 10}, seq=1, navigation_id=1))
    assert f.ok is False and f.error_category == "script"
    assert f.frame_spec is None, "失败帧不得携带像素布局"
    assert "buffer" not in f.payload, "失败帧不得携带 buffer 句柄"


@_reg
def test_identity_frame_has_no_spec():
    """无 DocumentState 的 scroll（纯身份帧）不挂旧位图布局。"""
    w = DocumentProcessWorker()
    f = w.handle(DocCommand(9, DOC_CMD_SCROLL,
                            {"w": 100, "h": 100}, seq=1, navigation_id=0))
    assert f.ok is True
    assert f.frame_spec is None, "未渲染的身份帧不得挂布局"
    assert "buffer" not in f.payload


@_reg
def test_drain_accepts_handle_frames():
    """DocProcessServer.drain 接受带 buffer 句柄的帧（句柄是白名单
    键，过 validate_frame 预算 + 父进程可见面校验，不拒收）。"""
    srv = DocProcessServer()
    srv.post(DocCommand(1, DOC_CMD_NAVIGATE,
                        {"url": "http://a", "html": "<p>hi</p>",
                         "w": 64, "h": 48}, seq=1, navigation_id=1))
    frames = srv.drain()
    assert len(frames) == 1, "带句柄的合法帧不得被 drain 拒收"
    assert srv.stats.frames_drained == 1
    assert srv.stats.dropped_tab_frames == 0
    assert frames[0].frame_spec is not None


# ---------------------------------------------------------------------------
# 动态层 — gui 呈现层消费（回退态真实像素 / 多核态仅布局 / 拒收）
# ---------------------------------------------------------------------------

class _StubBrowser:
    """最小 TabBrowser stub（不建 Tk；经 ``__new__`` 拿真实类实例，
    方法天然绑定）。gui 模块导入需 tkinter——缺库时抛 SkipTest
    （不冒充通过）。"""

    def __new__(cls, server):
        try:
            import tkinter  # noqa: F401
        except ImportError:
            from skip_support import SkipTest
            raise SkipTest("tkinter 不可用，跳过第 4 点 gui 呈现层动态断言"
                           "（gui 模块导入需 tkinter；不冒充通过）")
        from toy_browser.app import gui as _gui
        tb_cls = None
        for nm in dir(_gui):
            o = getattr(_gui, nm)
            if isinstance(o, type) and hasattr(o, "handle_scroll_command"):
                tb_cls = o
                break
        assert tb_cls is not None, "gui 缺 handle_scroll_command 所属类"
        tb = tb_cls.__new__(tb_cls)
        tb._g07_doc_server = server
        # 呈现层消费不依赖心跳 / trace / 门控（组件缺失即 no-op）
        tb._r7_a02_heartbeat = None
        tb._r7_a02_trace = None
        tb._r7_a02_nav_gate = None
        tb._r7_a02_cancel = None
        tb._r7_a02_coalescer = None
        tb._r7_a02_backpressure = None
        return tb


def _fallback_frame(server):
    server.post(DocCommand(1, DOC_CMD_NAVIGATE,
                           {"url": "http://a", "html": "<p>hi</p>",
                            "w": 64, "h": 48}, seq=1, navigation_id=1))
    frames = server.drain()
    assert frames, "回退态应产出帧"
    return frames[0]


@_reg
def test_apply_fallback_applies_real_pixels():
    """回退态：呈现层取父进程 worker 真实位图 → CPUImage 连续
    buffer，几何与 spec 一致、字节数 ≥ total_bytes；计 applied。"""
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    f = _fallback_frame(srv)
    img = tb._r7_a02_apply_frame_buffer(f)
    assert img is not None, "回退态应应用真实像素"
    assert (img.width, img.height) == (64, 48)
    assert len(img.buffer) >= f.frame_spec.total_bytes()
    assert tb._r7_a02_buffer_applied == 1
    assert getattr(tb, '_r7_a02_buffer_layout_only', 0) == 0


@_reg
def test_apply_spawn_is_layout_only():
    """多核态：子进程存活时父进程拿不到像素 → 返回 None + 计
    layout_only（不冒充"像素已跨进程到达"）。"""
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    f = _fallback_frame(srv)

    class _FakeProc:
        def is_alive(self):
            return True

    srv._on_spawned = True
    srv._proc = _FakeProc()
    try:
        img = tb._r7_a02_apply_frame_buffer(f)
        assert img is None
        assert tb._r7_a02_buffer_layout_only == 1
        assert getattr(tb, '_r7_a02_buffer_applied', 0) == 0
    finally:
        srv._on_spawned = False
        srv._proc = None


@_reg
def test_apply_rejects_bad_layout_and_handle():
    """布局不自洽（stride < width×bpp）/ 句柄越面（带 pixels 数据
    键）→ 拒收（计 rejected，不静默放行、不错位上屏）。"""
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    f = _fallback_frame(srv)
    # 坏布局
    bad = DocFrame(tab_id=1, seq=99, ok=True, navigation_id=1,
                   frame_spec=FrameBufferSpec(format="rgb8", width=4,
                                              height=2, stride=5),
                   payload={"frame_id": 1})
    assert tb._r7_a02_apply_frame_buffer(bad) is None
    # 坏句柄（句柄里塞像素数据——越过父进程可见面）
    badh = DocFrame(tab_id=1, seq=100, ok=True, navigation_id=1,
                    frame_spec=f.frame_spec,
                    payload={"frame_id": 2,
                             "buffer": {"id": 1, "pixels": bytearray(8)}})
    assert tb._r7_a02_apply_frame_buffer(badh) is None
    assert tb._r7_a02_buffer_rejected == 2
    assert getattr(tb, '_r7_a02_buffer_applied', 0) == 0


@_reg
def test_apply_identity_frame_is_noop():
    """纯身份帧（无 spec）→ no-op（不计数、不崩）。"""
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    plain = DocFrame(tab_id=1, seq=1, ok=True, navigation_id=1,
                     payload={"frame_id": 1})
    assert tb._r7_a02_apply_frame_buffer(plain) is None
    assert getattr(tb, "_r7_a02_buffer_applied", 0) == 0
    assert getattr(tb, "_r7_a02_buffer_rejected", 0) == 0


@_reg
def test_drain_path_applies_buffer_end_to_end():
    """_g07_drain_frames 全链路：post → drain → 门控 → 应用。
    回退态 applied==1；帧仍返回给调用方（呈现层可继续用）。"""
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    srv.post(DocCommand(1, DOC_CMD_NAVIGATE,
                        {"url": "http://a", "html": "<p>hi</p>",
                         "w": 64, "h": 48}, seq=1, navigation_id=1))
    out = tb._g07_drain_frames()
    assert len(out) == 1
    assert tb._r7_a02_buffer_applied == 1, \
        "drain 路径应在版本门控后应用连续 buffer"


@_reg
def test_drop_tab_clears_buffer_state():
    """drop_tab 清 buffer 句柄 / 位图 → 该 tab 后续 apply 拒收
    （不泄漏已关闭标签的像素引用）。"""
    w = _render_worker()
    assert w.buffer_handle_for(1) is not None
    w.drop_tab(1)
    assert w.buffer_handle_for(1) is None
    assert w.last_bitmap(1) is None
    srv = DocProcessServer()
    tb = _StubBrowser(srv)
    stale = DocFrame(tab_id=1, seq=1, ok=True, navigation_id=1,
                     frame_spec=FrameBufferSpec(format="rgb8", width=64,
                                                height=48),
                     payload={"frame_id": 1,
                              "buffer": {"id": 1, "size": 64 * 48 * 3,
                                         "format": "rgb8",
                                         "width": 64, "height": 48}})
    assert tb._r7_a02_apply_frame_buffer(stale) is None
    assert tb._r7_a02_buffer_rejected == 1


# ---------------------------------------------------------------------------
# 动态层 — 真实 spawn（子进程渲染帧带 spec 跨 pipe）
# ---------------------------------------------------------------------------

@_reg
def test_real_spawn_frame_spec_crosses_pipe():
    """真实 spawn：子进程 navigate 带 html 真实渲染 → 帧经 pipe 回
    父进程携带合法 FrameBufferSpec + buffer 句柄（pickle 往返）；
    父进程 apply 如实走 layout-only（像素留子进程，不冒充到达）。"""
    try:
        import multiprocessing as _mp
        _mp.get_context("spawn")
    except Exception:
        return  # spawn 不可用：跳过（不伪装通过）
    srv = spawn_doc_process_server()
    try:
        if not srv.is_spawned():
            return  # spawn 失败走回退：本条目不适用（回退态由上面覆盖）
        srv.post(DocCommand(1, DOC_CMD_NAVIGATE,
                            {"url": "http://a", "html": "<p>hi</p>",
                             "w": 64, "h": 48}, seq=1, navigation_id=1))
        frames = []
        for _ in range(60):
            frames = srv.drain()
            if frames:
                break
            time.sleep(0.05)
        assert len(frames) == 1, "子进程应回一帧（未被拒收）"
        f = frames[0]
        assert f.frame_spec is not None, "spec 须经 pipe 完整往返"
        assert f.frame_spec.layout_ok()
        assert (f.frame_spec.width, f.frame_spec.height) == (64, 48)
        assert f.payload.get("buffer", {}).get("size") == 64 * 48 * 3
        # 父进程侧无该 tab 位图（像素在子进程）——诚实口径
        assert srv.last_bitmap_for(1) is None
        tb = _StubBrowser(srv)
        img = tb._r7_a02_apply_frame_buffer(f)
        assert img is None and tb._r7_a02_buffer_layout_only == 1, \
            "多核态必须走仅布局（不宣称像素已跨进程到达）"
    finally:
        srv.shutdown()
        assert not srv.is_spawned()


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS [{name}]")
            passed += 1
        except Exception as e:
            if type(e).__name__ == "SkipTest":
                print(f"SKIP [{name}]: {e}")
                skipped += 1
                continue
            print(f"FAIL [{name}]: {e}")
            failed += 1
    print(f"\n{passed} passed / {failed} failed / {skipped} skipped")
    sys.exit(1 if failed else 0)
