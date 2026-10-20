"""R7-A02 第 4 点 — 连续帧 buffer 布局契约 + 父进程可见面校验（纯逻辑）。

审计 A02 第 4 点：帧用连续 buffer 或受控共享内存，**不**序列化整棵
Python 对象图；父进程不接触 worker 内部 Host。

被测：
- ``toy_browser/app/frame_buffer.py``（:class:`FrameBufferSpec`
  连续 buffer 布局 / :class:`FramePayloadContract` 父进程可见面校验）
- ``toy_browser/app/document_process.py`` 接线
  （:class:`DocProcessServer` 挂 :class:`FramePayloadContract`、
  :meth:`_frame_parent_visible`、:meth:`drain` 拒收越面帧；
  :class:`DocumentProcessWorker.frame_spec_for` 几何 → 布局）

设计：静态层锁源码契约（白名单键 / buffer 句柄键 / 不 import
运行时 / DocFrame 带 frame_spec 字段 / drain 挂校验）；动态层验证
布局自洽（rgb8/rgba8 字节数、stride padding、越界拒）+ 可见面
（有限摘要放行、Host / 大列表 / 超限字符串 / 非法句柄键拒收、
None 句柄合法）+ 接线（DocProcessServer 拒收越面帧、worker 几何
→ spec、无几何 → None）。

不冒充通过：本批只做**布局描述 + 可见面校验**决策层，不创建
共享内存、不跨进程传像素（真实 mmap / posix 共享段留活显示 / GPU
批次）。
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.frame_buffer import (
    FrameBufferSpec, FramePayloadContract,
    _WHITELIST_KEYS, _HANDLE_KEYS,
)
from toy_browser.app import document_process as dp

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 静态层 — 源码契约（不跑真实共享内存）
# ---------------------------------------------------------------------------

@_reg
def test_module_no_runtime_imports():
    """frame_buffer 不 import 运行时（subprocess/threading/
    multiprocessing/socket），不读时钟（P0 纯逻辑承诺）。"""
    src = _src("toy_browser/app/frame_buffer.py")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imported.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    banned = {"subprocess", "threading", "multiprocessing",
              "socket", "time"}
    assert not (imported & banned), \
        f"frame_buffer 不得 import 运行时原语 {banned & imported}"
    # 不读时钟：源码里不得出现 time.monotonic / time.time 调用。
    assert "monotonic" not in src and "time.time" not in src, \
        "frame_buffer 是纯布局 / 校验层，不得读时钟"


@_reg
def test_whitelist_keys_are_finite():
    """父进程可见面白名单必须是有限集合（不得开放任意键）。"""
    # 白名单必须封闭：恰好这 9 个键（摘要 8 + buffer 句柄 1）。
    assert _WHITELIST_KEYS == frozenset({
        "ok", "cancelled", "kind", "url", "frame_id", "doc_id",
        "navigation_id", "error_category", "buffer"}), \
        "可见面白名单须封闭（防 worker 偷塞对象图）"
    # buffer 句柄只描述像素引用，不得携带像素数据本身。
    assert _HANDLE_KEYS == frozenset({
        "id", "size", "format", "width", "height"}), \
        "buffer 句柄键须封闭（只描述位置/大小/格式，不传像素）"
    assert "pixels" not in _HANDLE_KEYS and "data" not in _HANDLE_KEYS


@_reg
def test_docframe_carries_frame_spec():
    """DocFrame 须带可选 frame_spec 字段（连续 buffer 布局，None =
    无像素帧），且默认 None（向后兼容既有紧凑 dict 帧）。"""
    f = dp.DocFrame(tab_id=1, seq=2)
    assert hasattr(f, "frame_spec") and f.frame_spec is None, \
        "DocFrame.frame_spec 须默认 None（无像素帧）"
    spec = FrameBufferSpec(format="rgba8", width=2, height=2)
    f2 = dp.DocFrame(tab_id=1, seq=2, frame_spec=spec)
    assert f2.frame_spec is spec, "DocFrame 须可携带 FrameBufferSpec"


@_reg
def test_drain_wires_parent_visible_check():
    """DocProcessServer.drain 须挂父进程可见面校验（_frame_parent
    _visible），越面帧拒收（不静默放行）。"""
    src = _src("toy_browser/app/document_process.py")
    assert "_frame_parent_visible" in src, \
        "drain 须挂父进程可见面校验"
    # 三处帧判定（pending / spawned / fallback）都须过校验：
    assert src.count("_frame_parent_visible(") >= 4, \
        "pending / spawned / fallback 三条 drain 路径都须过可见面校验"
    # 校验失败须拒收（dropped_tab_frames 计数 + continue）。
    assert "self._stats.dropped_tab_frames += 1" in src


# ---------------------------------------------------------------------------
# 动态层 — 布局契约
# ---------------------------------------------------------------------------

@_reg
def test_rgb8_rgba8_total_bytes():
    """rgb8 / rgba8 总字节数 = stride × height（stride 默认 w×bpp）。"""
    s = FrameBufferSpec(format="rgb8", width=4, height=3)
    assert s.bpp == 3 and s.row_bytes == 12
    assert s.total_bytes() == 36, f"rgb8 4x3 应 36 字节，实 {s.total_bytes()}"
    a = FrameBufferSpec(format="rgba8", width=3, height=2)
    assert a.bpp == 4 and a.row_bytes == 12
    assert a.total_bytes() == 24


@_reg
def test_stride_padding_layout_ok():
    """带行 padding 的 stride（>= w×bpp）合法；越界 stride（< w×bpp）
    拒（layout_ok False）。"""
    ok = FrameBufferSpec(format="rgb8", width=4, height=2, stride=16)
    assert ok.layout_ok(), "padding stride 16 >= w×bpp 12 应合法"
    bad = FrameBufferSpec(format="rgb8", width=4, height=2, stride=5)
    assert not bad.layout_ok(), "stride 5 < w×bpp 12 越界须拒"


@_reg
def test_unknown_format_rejected():
    """未知像素格式在构造时即拒（不静默回退到默认 bpp）。"""
    try:
        FrameBufferSpec(format="bgra8", width=1, height=1)
    except ValueError:
        return
    raise AssertionError("未知格式 bgra8 应在构造时拒收")


@_reg
def test_zero_dimension_zero_bytes():
    """0 尺寸帧 total_bytes = 0（layout_ok 仍 True，几何自洽）。"""
    s = FrameBufferSpec(format="rgb8", width=0, height=5)
    assert s.total_bytes() == 0 and s.layout_ok()


# ---------------------------------------------------------------------------
# 动态层 — 父进程可见面校验
# ---------------------------------------------------------------------------

@_reg
def test_finite_summary_admitted():
    """有限摘要 + 合法 buffer 句柄 → 放行。"""
    c = FramePayloadContract()
    payload = {
        "ok": True, "cancelled": False, "kind": "resize",
        "url": "http://127.0.0.1", "frame_id": 7, "doc_id": 3,
        "navigation_id": 2, "error_category": "timeout",
        "buffer": {"id": 1, "size": 4096, "format": "rgb8",
                   "width": 32, "height": 32},
    }
    ok, reasons = c.validate(payload)
    assert ok and reasons == [], f"合法摘要须放行，拒因 {reasons}"


@_reg
def test_host_leak_rejected():
    """worker 把 Host / 对象图塞进 payload → 拒（审计 A02 第 4 点
    核心：父进程不接触 worker 内部 Host）。"""
    c = FramePayloadContract()
    host_obj = object()
    ok, reasons = c.validate({"host": host_obj})
    assert not ok, "Host 对象图须拒收"
    assert any("host" in r for r in reasons)
    # 大列表（对象图）同样拒。
    ok2, _ = c.validate({"dom": [1, 2, 3]})
    assert not ok2, "DOM 列表须拒收"


@_reg
def test_oversized_string_rejected():
    """超限字符串（> 128）拒收——父进程只接收有限摘要。"""
    c = FramePayloadContract()
    ok, reasons = c.validate({"url": "x" * 200})
    assert not ok, "200 字符 url 超限须拒"
    assert any("128" in r for r in reasons)
    # 恰好 128 字符应放行（边界）。
    ok2, _ = c.validate({"url": "x" * 128})
    assert ok2, "128 字符 url 应放行（边界含）"


@_reg
def test_buffer_handle_rejects_pixel_data():
    """buffer 句柄只描述像素引用，携带像素数据本身（pixels / data）
    → 拒。"""
    c = FramePayloadContract()
    ok, reasons = c.validate(
        {"buffer": {"pixels": bytearray(1000), "size": 4096}})
    assert not ok, "句柄携带像素数据须拒"
    assert any("pixels" in r for r in reasons)


@_reg
def test_buffer_none_admitted():
    """buffer 键缺省 None（本帧无像素 buffer，如纯导航 / stop 帧）
    → 放行。"""
    c = FramePayloadContract()
    ok, _ = c.validate({"buffer": None})
    assert ok, "None 句柄须放行"


@_reg
def test_buffer_handle_type_violations():
    """buffer 句柄内各键类型 / 范围违规逐个拒。"""
    c = FramePayloadContract()
    # size 超上限
    ok, _ = c.validate({"buffer": {"size": 10 ** 9}})
    assert not ok, "size 超 64MiB 上限须拒"
    # format 非法
    ok2, _ = c.validate({"buffer": {"format": "bgra8"}})
    assert not ok2, "非法 format 须拒"
    # width 负数
    ok3, _ = c.validate({"buffer": {"width": -1}})
    assert not ok3, "负 width 须拒"


# ---------------------------------------------------------------------------
# 动态层 — document_process 接线
# ---------------------------------------------------------------------------

@_reg
def test_worker_frame_spec_from_geometry():
    """worker.frame_spec_for：几何 payload → rgb8 spec；无几何 →
    None（navigate / stop 帧不携带像素 buffer）。"""
    w = dp.DocumentProcessWorker()
    s = w.frame_spec_for({"w": 10, "h": 20})
    assert s is not None and s.format == "rgb8" \
        and s.width == 10 and s.height == 20
    assert s.total_bytes() == 600, "rgb8 10x20 = 600 字节"
    # width/height 键名同样认。
    s2 = w.frame_spec_for({"width": 3, "height": 4})
    assert s2.width == 3 and s2.height == 4
    # 无几何（navigate 帧只带 url）→ None。
    assert w.frame_spec_for({"url": "http://a"}) is None
    # 几何非法（非数值）→ None（不崩）。
    assert w.frame_spec_for({"w": "x", "h": "y"}) is None


@_reg
def test_server_rejects_oversized_payload_in_drain():
    """DocProcessServer 父进程可见面校验：越面帧拒收（不计入
    frames_drained），合法帧放行。"""
    s = dp.DocProcessServer()
    # 合法帧：parent_visible True。
    good = dp.DocFrame(tab_id=1, seq=1, payload={"ok": True,
                                                   "frame_id": 1})
    assert s._frame_parent_visible(good) is True
    # Host 泄漏帧：parent_visible False。
    leak = dp.DocFrame(tab_id=1, seq=2, payload={"host": object()})
    assert s._frame_parent_visible(leak) is False
    # 注入越面帧进 pending → drain 拒收（dropped 计数 +1，不 drained）。
    s._pending_frames = [leak]
    frames = s.drain()
    assert not any(f is leak for f in frames), \
        "越面帧不得进入 drain 结果"
    assert s._stats.dropped_tab_frames >= 1
    assert s._stats.frames_drained == 0, "越面帧不得计入 drained"


@_reg
def test_server_adopts_legal_frame_in_drain():
    """合法帧经 drain 放行（计入 frames_drained，可见面校验不破坏
    既有 S01 有限 IPC 校验）。

    resize 命令的 payload 是 {w,h}，但 worker 产出的帧 payload 是
    {"kind","frame_id"}（紧凑摘要，经 S01 有限 IPC 校验 + A02 可见面
    校验都放行）——帧不携带原始几何，几何经 frame_spec_for 从命令
    payload 还原为 buffer 布局（审计"帧用连续 buffer，不序列化整棵
    对象图"）。"""
    s = dp.DocProcessServer()
    cmd = dp.DocCommand(tab_id=1, kind=dp.DOC_CMD_RESIZE,
                        payload={"w": 8, "h": 9}, seq=5)
    s.post(cmd)
    frames = s.drain()
    assert len(frames) == 1, f"合法 resize 帧应放行，实 {len(frames)}"
    f = frames[0]
    assert f.seq == 5
    # 帧 payload 是紧凑摘要（kind/frame_id），不含原始 w/h（几何在
    # frame_spec，不塞进 payload 对象图）。
    assert "kind" in f.payload and f.payload["kind"] == "resize"
    assert s._frame_parent_visible(f), "合法帧须过父进程可见面校验"
    assert s._stats.frames_drained == 1
    # 几何 buffer 布局从命令 payload 还原（rgb8 8x9 = 216 字节）。
    spec = dp.DocumentProcessWorker.frame_spec_for(cmd.payload)
    assert spec is not None and spec.total_bytes() == 8 * 9 * 3


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
