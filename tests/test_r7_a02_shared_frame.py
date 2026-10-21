"""R7-A02 第 4 点 — 跨进程像素共享段判定接线探针（不冒充通过）。

Round-7 评估 R7-A02 遗留项：

    多核态（子进程存活）跨进程**像素**传递：需 mmap / posix 共享段
    + 子进程经 IPC 回报 doc_work 标记。

此前 :mod:`frame_buffer` 落地决策层（布局 + 父进程可见面校验）、
:mod:`document_process` 把 ``DocFrame.frame_spec`` + ``buffer`` 句柄
传到父进程，但**真实跨进程共享段执行**（worker 把 CPUImage 像素写
进共享段、父进程经句柄只读）一直标注"留活显示批次"。本探针补上
"可自动化的判定接线"——用 :class:`multiprocessing.shared_memory`
命名段做"父进程建段 → worker 按句柄写入像素 → 父进程只读逐像素
校验"的真 spawn 可判定通路。

依赖（缺 ``multiprocessing.shared_memory`` 即 :class:`SkipTest`，
不缩分母、不冒充通过）：

* :func:`multiprocessing.shared_memory`（命名段，跨进程安全）；
* :class:`CPUImage`（RGB8 连续 buffer，像素源）；
* 真 spawn 冒烟需 :mod:`multiprocessing` 可 spawn。

"不冒充通过"边界（本探针如实标注）：

* 本探针证明"父进程建命名共享段 → **子进程**按句柄把 CPUImage 像素
  写进去 → 父进程经同一句柄只读出并**逐字节校验一致**"——像素真的
  跨进程到了父进程，不是"有句柄就宣称已共享"；
* **不**证明"真实 mmap 权限细节 / GPU 量化计时 / 慢页 timeout 启用"
  ——这些仍留活显示批次，本探针不宣称完成。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.frame_shared import (
    SharedFrameSegment, create_shared_segment,
    write_frame_to_shared, read_frame_from_shared, verify_shared_frame,
    _spec_total_bytes,
)
from toy_browser.rendering.cpu_image import CPUImage

try:
    from multiprocessing import shared_memory as _shm_mod
except Exception:
    _shm_mod = None

try:
    import multiprocessing as _mp
except Exception:
    _mp = None

from skip_support import SkipTest  # noqa: E402

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _require_shared_memory():
    """跨进程共享段探针门控：缺 shared_memory 即 SkipTest。"""
    if _shm_mod is None:
        raise SkipTest("multiprocessing.shared_memory unavailable; "
                       "frame-shared probe gated")


def _make_img(w=4, h=3):
    """构造一张确定像素的 CPUImage（RGB8，逐像素可校验）。"""
    img = CPUImage(w, h)
    for y in range(h):
        for x in range(w):
            img.set_pixel(x, y, (x * 40) & 0xFF, (y * 80) & 0xFF, 200)
    return img


def _unlink_quiet(name):
    try:
        sm = _shm_mod.SharedMemory(name=name, create=False)
        sm.close()
        sm.unlink()
    except Exception:
        pass


# ----------------------------------------------------------------------
# 探针 1 — 命名段可创建 + 几何/大小判定（决策层，不 attach）
# ----------------------------------------------------------------------

@_test
def test_probe1_segment_create_and_geometry():
    """父进程命名段可创建；句柄 size 与 CPUImage.size 一致；大小
    非法 / 无段时 write/read/verify 不硬造。"""
    _require_shared_memory()
    img = _make_img(4, 3)
    name = "toy_fsm_p1"
    try:
        probe = create_shared_segment(name, img.size, width=4, height=3,
                                      create=True)
        assert probe.acquired is True, probe.describe()
        assert probe.handle.size == img.size, \
            f"句柄 size 须等于 CPUImage.size：{probe.handle.size} vs {img.size}"
        # 无段（未 attach）时 write/read/verify 全部 False / None（不硬造）。
        empty = SharedFrameSegment(probe.handle)
        assert write_frame_to_shared(img, empty) is False
        assert read_frame_from_shared(empty, 4, 3) is None
        assert verify_shared_frame(img, empty, 4, 3) is False
        # 大小非法 → acquired=False + reason 如实记录。
        bad = create_shared_segment("toy_fsm_bad", 0)
        assert bad.acquired is False and "size must be > 0" in bad.reason
    finally:
        _unlink_quiet(name)


# ----------------------------------------------------------------------
# 探针 2 — 父进程侧写入 + 读取 + 逐像素校验（同进程契约）
# ----------------------------------------------------------------------

@_test
def test_probe2_write_read_verify_roundtrip():
    """同一命名段：父进程写入 CPUImage 像素 → 只读出 → 逐字节校验
    一致（"像素真到了读取方"的契约表达，同进程先验证字节级正确）。"""
    _require_shared_memory()
    img = _make_img(4, 3)
    name = "toy_fsm_p2"
    sm = _shm_mod.SharedMemory(name=name, create=True, size=img.size)
    seg = SharedFrameSegment(
        create_shared_segment(name, img.size, width=4, height=3,
                              create=False).handle, sm)
    try:
        assert write_frame_to_shared(img, seg) is True
        back = read_frame_from_shared(seg, 4, 3)
        assert back is not None and len(back) == img.size
        assert verify_shared_frame(img, seg, 4, 3) is True, \
            "逐像素校验应一致"
        # 几何越段（按更大几何读）→ None（不硬造越界）。
        assert read_frame_from_shared(seg, 8, 8) is None
    finally:
        sm.close()
        _unlink_quiet(name)


# ----------------------------------------------------------------------
# 探针 3 — 真 spawn：子进程按句柄写入像素，父进程只读校验（核心突破）
# ----------------------------------------------------------------------

def _child_write_frame(name, size, w, h, buffer_id, result_q):
    """子进程侧：attach 同名共享段，写入确定像素，经 *result_q* 回报。

    独立顶层函数（可被 spawn 子进程 pickle）；不经
    :class:`DocumentProcessWorker`，只做"worker 把像素写进共享段"
    的最小可判定子集（真实 doc_work 写像素路径在活显示批次接）。
    """
    img = _make_img(w, h)
    try:
        sm = _shm_mod.SharedMemory(name=name, create=False)
    except Exception as exc:
        result_q.put({"ok": False, "reason": f"attach: {exc!r}"})
        return
    seg = SharedFrameSegment(
        create_shared_segment(name, size, width=w, height=h,
                              buffer_id=buffer_id, create=False).handle, sm)
    ok = write_frame_to_shared(img, seg, buffer_id=buffer_id)
    sm.close()
    result_q.put({"ok": ok})


@_test
def test_probe3_real_spawn_child_writes_parent_reads():
    """父进程建命名段 → 真 spawn 子进程 attach 同名段并写入 CPUImage
    像素 → 父进程经同一句柄只读出 + 逐字节校验一致（像素真的跨进程
    到了父进程）。缺 multiprocessing/不可 spawn 即 SkipTest。"""
    _require_shared_memory()
    if _mp is None:
        raise SkipTest("multiprocessing unavailable; spawn probe gated")
    img = _make_img(4, 3)
    name = "toy_fsm_p3"
    sm = _shm_mod.SharedMemory(name=name, create=True, size=img.size)
    try:
        ctx = _mp.get_context("spawn")
        result_q = ctx.Queue()
        proc = ctx.Process(
            target=_child_write_frame,
            args=(name, img.size, 4, 3, 7, result_q),
            daemon=True)
        proc.start()
        proc.join(timeout=30)
        rec = result_q.get(timeout=10) if proc.is_alive() else result_q.get(timeout=10)
        assert proc.exitcode == 0, f"子进程异常退出：{proc.exitcode}"
        assert rec.get("ok") is True, f"子进程写入应成功：{rec}"
        # 父进程侧只读出 + 逐字节校验（像素真跨进程到了父进程）。
        seg = SharedFrameSegment(
            create_shared_segment(name, img.size, width=4, height=3,
                                  buffer_id=7, create=False).handle, sm)
        assert verify_shared_frame(img, seg, 4, 3) is True, \
            "父进程只读出的像素应与子进程写入一致"
        back = read_frame_from_shared(seg, 4, 3)
        assert back is not None and bytes(back) == bytes(img.buffer)
    finally:
        sm.close()
        _unlink_quiet(name)


# ----------------------------------------------------------------------
# 探针 4 — buffer id 区分"哪一块像素"（每帧独立段，同句柄口径）
# ----------------------------------------------------------------------

@_test
def test_probe4_buffer_id_distinguishes_frames():
    """两块像素各占一个**独立**共享段、各自 buffer_id（与
    :meth:`DocumentProcessWorker.buffer_handle_for` 单调 id 同口径）：
    写入 A 段、写入 B 段，按各自几何 + 各自段逐字节校验互不串扰。"""
    _require_shared_memory()
    img_a = _make_img(4, 3)   # 36 字节
    img_b = _make_img(6, 2)   # 36 字节（不同像素内容）
    name_a = "toy_fsm_p4a"
    name_b = "toy_fsm_p4b"
    sm_a = _shm_mod.SharedMemory(name=name_a, create=True, size=img_a.size)
    sm_b = _shm_mod.SharedMemory(name=name_b, create=True, size=img_b.size)
    try:
        seg_a = SharedFrameSegment(
            create_shared_segment(name_a, img_a.size, width=4, height=3,
                                  buffer_id=1, create=False).handle, sm_a)
        seg_b = SharedFrameSegment(
            create_shared_segment(name_b, img_b.size, width=6, height=2,
                                  buffer_id=2, create=False).handle, sm_b)
        assert write_frame_to_shared(img_a, seg_a, buffer_id=1) is True
        assert write_frame_to_shared(img_b, seg_b, buffer_id=2) is True
        # 块身份可区分（同句柄口径：id / 几何 / 段大小各自独立）。
        assert seg_a.handle.buffer_id == 1 and seg_b.handle.buffer_id == 2
        assert seg_a.handle.width == 4 and seg_b.handle.width == 6
        # 各自段逐字节校验一致（互不串扰：A 段只含 A、B 段只含 B）。
        assert verify_shared_frame(img_a, seg_a, 4, 3) is True
        assert verify_shared_frame(img_b, seg_b, 6, 2) is True
        # 跨块校验必不一致（B 的像素写进 A 段 → 几何/内容不匹配）。
        assert verify_shared_frame(img_b, seg_a, 6, 2) is False
    finally:
        sm_a.close()
        _unlink_quiet(name_a)
        sm_b.close()
        _unlink_quiet(name_b)


# ----------------------------------------------------------------------
# 探针 5 — 损坏像素可检测（校验捕获不一致，不冒充"已一致"）
# ----------------------------------------------------------------------

@_test
def test_probe5_corrupt_pixel_detected():
    """写入像素后段被破坏（按不同 expected 校验）→ verify 返回
    False，不冒充"像素已一致"。"""
    _require_shared_memory()
    img_ok = _make_img(4, 3)
    img_diff = _make_img(4, 3)
    img_diff.set_pixel(0, 0, 9, 9, 9)   # 与 img_ok 在第 0 像素不同
    name = "toy_fsm_p5"
    sm = _shm_mod.SharedMemory(name=name, create=True, size=img_ok.size)
    seg = SharedFrameSegment(
        create_shared_segment(name, img_ok.size, width=4, height=3,
                              create=False).handle, sm)
    try:
        write_frame_to_shared(img_ok, seg)
        assert verify_shared_frame(img_ok, seg, 4, 3) is True
        # 用"不同"的 expected 校验 → 不一致被捕获（False，不冒充）。
        assert verify_shared_frame(img_diff, seg, 4, 3) is False
    finally:
        sm.close()
        _unlink_quiet(name)


# ----------------------------------------------------------------------
# 探针 6 — worker 句柄带 shared 段名 + 过父进程可见面契约
# ----------------------------------------------------------------------

@_test
def test_probe6_worker_handle_carries_shared_name():
    """R7-A02 第 4 点主链：``DocumentProcessWorker.buffer_handle_for``
    返回的句柄带 ``shared`` 段名（经 :func:`shared_segment_name` 生成），
    且过 :class:`FramePayloadContract` 校验（``shared`` 已入白名单）。"""
    _require_shared_memory()
    from toy_browser.app.document_process import DocumentProcessWorker
    from toy_browser.app.frame_buffer import FramePayloadContract
    from toy_browser.app import frame_shared as _fs
    w = DocumentProcessWorker()
    # 让 worker 有"最近位图"（经 _do_doc_work 真实渲染；缺 html 不触发，
    # 这里直接走 render_document_state 最小路径——用内联 HTML）。
    try:
        from toy_browser.app import document_renderer as _rend
        from toy_browser.app.document_state_full import DocumentState
        doc = DocumentState()
        w._doc_states[1] = doc
        bm = _rend.render_document_state(doc, "<p>x</p>",
                                         width=4, height=3)
        w._last_bitmap[1] = bm
        w._note_rendered(1)
    except Exception:
        # 渲染管线不可用时回退最小句柄契约验证（仍锁 shared 键存在）。
        w._last_bitmap[1] = type("_B", (), {"width": 4, "height": 3})()
        w._note_rendered(1)
    handle = w.buffer_handle_for(1)
    assert handle is not None, "渲染过应产生 buffer 句柄"
    assert "shared" in handle, f"句柄应带 shared 段名：{handle}"
    # shared 段名与 shared_segment_name 同口径（按 buffer id 单调）。
    expect_name = _fs.shared_segment_name(int(handle["id"]))
    assert handle["shared"] == expect_name, (handle["shared"], expect_name)
    # 过父进程可见面契约（shared 已入 _HANDLE_KEYS 白名单）。
    ok, reasons = FramePayloadContract().validate({"buffer": handle})
    assert ok, f"句柄应过契约：{reasons}"


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            if getattr(e, "__class__", None).__name__ == "SkipTest":
                print(f"SKIP {name}: {e}")
                skipped += 1
            else:
                print(f"ERROR {name}: {e!r}")
                failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)
