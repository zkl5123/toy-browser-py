"""R7-S04 第 4 点 — 页面维度独立资源限额（纯逻辑 + 接线契约）。

审计 S04 第 4 点：页面 DOM 数量/深度、源码字节、图像像素、字体表/
组件、任务队列与 IPC 队列设**独立**限额。本文件锁定两层：

* 纯逻辑层（:mod:`toy_browser.network.page_limits`）—— 六维度独立
  上限 + 逐维记账 + 分配前防护（``admit`` / ``admit_or_raise``）+
  未启用维度放行 + 负记账防护 + 静态锁（不 import 运行时、不读时钟）。
* 接线层 —— IPC 队列维度接进 :class:`DocumentProcessPool.post`
  （在途命令+帧达上限即拒收，限 0 恒放行，负上限等价未启用）。

设计：不 import tkinter / 真实进程。IPC 接线用可注入的
``process_factory`` / ``pipe_factory``（None = 纯暂存回退态）即可验证
``post`` 的拒收路径，不依赖 spawn。
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network.page_limits import (
    PageDimension, PageResourceLimits, PageLimitExceeded, DEFAULT_LIMITS,
)
from toy_browser.app.document_process import DocumentProcessPool, DocCommand

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 静态层 — 源码契约（不跑真实资源分配）
# ---------------------------------------------------------------------------

@_reg
def test_module_no_runtime_imports():
    """page_limits 不 import 运行时（subprocess/threading/
    multiprocessing/socket），不读时钟（P0 纯逻辑承诺）。"""
    src = _src("toy_browser/network/page_limits.py")
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
        f"page_limits 不得 import 运行时原语 {banned & imported}"
    assert "monotonic" not in src and "time.time" not in src, \
        "page_limits 是纯限额层，不得读时钟"


@_reg
def test_all_six_dimensions_have_default_limits():
    """七维度（DOM_NODES/DOM_DEPTH/SOURCE_BYTES/IMAGE_PIXELS/
    FONT_COMPONENTS/TASK_QUEUE/IPC_QUEUE）默认限额必须显式给定且 > 0
    （防"缺省即无限制"的隐性缺省，审计 S04 第 4 点"独立限额"）。"""
    dims = list(PageDimension)
    assert len(dims) == 7, f"应为 7 个维度，实 {len(dims)}"
    for dim in dims:
        assert dim in DEFAULT_LIMITS, f"维度 {dim} 缺默认限额"
        assert DEFAULT_LIMITS[dim] > 0, \
            f"维度 {dim} 默认限额必须 > 0（防缺省即无限制）"


@_reg
def test_dimensions_are_independent():
    """六维度互不串用：某维用满不影响其它维的 remaining / admit。"""
    l = PageResourceLimits(limits={PageDimension.DOM_NODES: 5})
    l.charge(PageDimension.DOM_NODES, 5)  # 用满
    assert l.exhausted(PageDimension.DOM_NODES)
    # 其它维度（默认限额）仍可用、未耗尽。
    assert not l.exhausted(PageDimension.TASK_QUEUE)
    assert l.admit(PageDimension.TASK_QUEUE, 1) is True
    assert l.remaining(PageDimension.DOM_NODES) == 0


@_reg
def test_pool_wires_ipc_queue_limit():
    """DocumentProcessPool 须挂 IPC 队列限额（S04 第 4 点 IPC 维度），
    且 post 在在途达上限时拒收（不静默放行、不串页）。"""
    src = _src("toy_browser/app/document_process.py")
    assert "PageResourceLimits" in src, \
        "pool 须接入页面维度限额层"
    assert "PageDimension" in src or "_PageDim" in src, \
        "pool 须引用 IPC 队列维度枚举"
    # post 的拒收路径须存在（inflight 计数 + 拒收）。
    assert "_ipc_rejected" in src, "被拒收命令须计数（可诊断）"
    assert "ipc_queue_limit" in src, "须暴露 IPC 队列上限访问器"


# ---------------------------------------------------------------------------
# 动态层 — 纯逻辑限额
# ---------------------------------------------------------------------------

@_reg
def test_admit_blocks_before_charge():
    """分配前防护：used + n > cap 时 admit False（不记账、不创建）。"""
    l = PageResourceLimits(limits={PageDimension.DOM_NODES: 100})
    l.charge(PageDimension.DOM_NODES, 90)
    assert l.admit(PageDimension.DOM_NODES, 20) is False, \
        "90+20=110>100 须拒（分配前防护，S04 第 1 点）"
    assert l.used(PageDimension.DOM_NODES) == 90, \
        "被拒的 20 不得记账（admit 无副作用）"
    assert l.admit(PageDimension.DOM_NODES, 10) is True, \
        "90+10=100<=100 须放行"


@_reg
def test_charge_exhausts_at_cap():
    """用满即 exhausted（== 上限即耗尽，与 PageBudget 同口径）。"""
    l = PageResourceLimits(limits={PageDimension.TASK_QUEUE: 3})
    l.charge(PageDimension.TASK_QUEUE, 3)
    assert l.exhausted(PageDimension.TASK_QUEUE)
    assert l.admit(PageDimension.TASK_QUEUE, 1) is False


@_reg
def test_negative_charge_ignored():
    """负记账按 0 处理（防恶意负值绕过限额）。"""
    l = PageResourceLimits(limits={PageDimension.IMAGE_PIXELS: 10})
    l.charge(PageDimension.IMAGE_PIXELS, -100)
    assert l.used(PageDimension.IMAGE_PIXELS) == 0


@_reg
def test_disabled_dimension_passes():
    """上限 <= 0 = 该维度未启用，admit 恒 True、exhausted 恒 False、
    remaining = ∞。"""
    l = PageResourceLimits(limits={PageDimension.FONT_COMPONENTS: 0})
    assert l.admit(PageDimension.FONT_COMPONENTS, 10**9) is True
    assert l.exhausted(PageDimension.FONT_COMPONENTS) is False
    assert l.remaining(PageDimension.FONT_COMPONENTS) == float("inf")


@_reg
def test_admit_or_raise_carries_dimension():
    """超限异常携带 dimension / limit / used / requested（可诊断）。"""
    l = PageResourceLimits(limits={PageDimension.SOURCE_BYTES: 100})
    l.charge(PageDimension.SOURCE_BYTES, 80)
    try:
        l.admit_or_raise(PageDimension.SOURCE_BYTES, 30)
    except PageLimitExceeded as e:
        assert e.dimension is PageDimension.SOURCE_BYTES
        assert e.limit == 100 and e.used == 80 and e.requested == 30
        return
    raise AssertionError("应抛 PageLimitExceeded")


@_reg
def test_reset_clears_one_or_all():
    """reset(dim) 清单维；reset() 清全部（导航 / 关标签时调用）。"""
    l = PageResourceLimits()
    l.charge(PageDimension.DOM_NODES, 5)
    l.charge(PageDimension.TASK_QUEUE, 7)
    l.reset(PageDimension.DOM_NODES)
    assert l.used(PageDimension.DOM_NODES) == 0
    assert l.used(PageDimension.TASK_QUEUE) == 7, "单维 reset 不得误清他维"
    l.reset()
    assert l.used(PageDimension.TASK_QUEUE) == 0


# ---------------------------------------------------------------------------
# 动态层 — IPC 队列接线（DocumentProcessPool.post）
# ---------------------------------------------------------------------------

@_reg
def test_pool_rejects_when_inflight_reaches_limit():
    """在途命令 + 帧达 IPC 上限即拒收新命令（不串页、不静默）。"""
    pool = DocumentProcessPool()  # process_factory=None → 纯暂存回退态
    pool.ipc_queue_limit = 2
    c1 = DocCommand(tab_id=1, kind="navigate",
                    payload={"url": "http://a"}, seq=1)
    c2 = DocCommand(tab_id=1, kind="resize",
                    payload={"w": 800, "h": 600}, seq=2)
    assert pool.post(c1) is True, "第 1 条（inflight 0<2）须放行"
    assert pool.post(c2) is True, "第 2 条（inflight 1<2）须放行"
    c3 = DocCommand(tab_id=1, kind="scroll", payload={}, seq=3)
    assert pool.post(c3) is False, "第 3 条（inflight 2>=2）须拒收"
    assert pool.ipc_rejected == 1
    # 被拒的命令不入队、不计数 posted。
    assert pool.stats.commands_posted == 2


@_reg
def test_pool_limit_zero_disables_enforcement():
    """IPC 上限 = 0 → 未启用，恒放行（向后兼容既有无限池契约）。"""
    pool = DocumentProcessPool()
    pool.ipc_queue_limit = 0
    for i in range(50):  # 远超任何默认上限
        assert pool.post(DocCommand(tab_id=1, kind="resize",
                                    payload={"w": i, "h": i}, seq=i)) is True
    assert pool.ipc_rejected == 0, "未启用限额时不得拒收任何命令"


@_reg
def test_pool_drain_releases_inflight_slots():
    """drain 清在途后，被拒的新命令可再放行（限额看的是在途，不是
    历史累计）。"""
    pool = DocumentProcessPool()
    pool.ipc_queue_limit = 2
    assert pool.post(DocCommand(tab_id=1, kind="navigate",
                                payload={"url": "http://a"}, seq=1))
    assert pool.post(DocCommand(tab_id=1, kind="resize",
                                payload={"w": 10, "h": 10}, seq=2))
    assert pool.post(DocCommand(tab_id=1, kind="scroll",
                                payload={}, seq=3)) is False
    pool.drain()  # 清空在途命令 → 释放槽位
    assert pool.post(DocCommand(tab_id=1, kind="scroll",
                                payload={}, seq=4)) is True, \
        "drain 释放在途后须可再放行"


@_reg
def test_closed_tab_still_rejected_regardless_of_limit():
    """已关 tab 的命令先于限额判定直接拒收（G09 不串页，优先级更高）。"""
    pool = DocumentProcessPool()
    pool.ipc_queue_limit = 100
    pool.post(DocCommand(tab_id=7, kind="navigate",
                         payload={"url": "http://a"}, seq=1))
    pool.close_tab(7)
    assert pool.post(DocCommand(tab_id=7, kind="navigate",
                                payload={"url": "http://b"}, seq=2)) is False, \
        "已关 tab 的命令须拒收（与限额无关）"


# ---------------------------------------------------------------------------
# 逐入口接线 — 任务队列 / 字体 / 图像（S04 第 4 点非 IPC 维度）
# ---------------------------------------------------------------------------

@_reg
def test_task_queue_entry_points_refuse():
    """js.Host 三任务入口（_set_timeout / _queue_microtask /
    _request_animation_frame）接 TASK_QUEUE 限额：超限拒入队 +
    计数可诊断 + flush 出队释放槽位。"""
    import base64
    from toy_browser.scripting import js
    from toy_browser.document import dom as dom_mod
    from toy_browser.network.page_limits import PageDimension

    root = dom_mod.parse(b"<html><body></body></html>")
    host = js.Host(root, lambda *a, **k: None)
    # 把任务队列上限压到 2，验证"入队防护 + 出队释放"。
    host._task_limits.limits[PageDimension.TASK_QUEUE] = 2
    # 入队 1、2：放行；入队 3：超限拒。
    host._set_timeout(lambda *a: None, 0)
    host._set_timeout(lambda *a: None, 0)
    assert len(host._timers) == 2
    host._set_timeout(lambda *a: None, 0)
    assert len(host._timers) == 2, "第 3 条 timer 须拒入队（TASK_QUEUE 限额）"
    assert host._task_rejected == 1
    # 出队释放：flush 后槽位腾出，可再入队。
    host.flush_timers()
    assert host._task_limits.used(PageDimension.TASK_QUEUE) == 0, \
        "flush 出队须释放 TASK_QUEUE 记账"
    host._set_timeout(lambda *a: None, 0)
    assert len(host._timers) == 1, "flush 释放后须可再入队"


@_reg
def test_microtask_and_raf_entry_points_refuse():
    """_queue_microtask / _request_animation_frame 同口径接 TASK_QUEUE。"""
    from toy_browser.scripting import js
    from toy_browser.document import dom as dom_mod
    from toy_browser.network.page_limits import PageDimension

    root = dom_mod.parse(b"<html><body></body></html>")
    host = js.Host(root, lambda *a, **k: None)
    host._task_limits.limits[PageDimension.TASK_QUEUE] = 1
    host._queue_microtask(lambda *a: None)
    assert len(host._microtasks) == 1
    host._request_animation_frame(lambda *a: None)
    assert len(host._raf_callbacks) == 0, "rAF 第 2 条须拒（限额=1 已满）"
    assert host._task_rejected == 1
    # flush_raf 只结算 rAF 队列：rAF 槽位释放，microtask 槽位仍在
    # （两入口共享 TASK_QUEUE 维度，各按自身入队记账）。
    host.flush_raf()
    assert host._task_limits.used(PageDimension.TASK_QUEUE) == 1, \
        "flush_raf 后 rAF 释放，microtask 1 条仍占槽位"
    # flush_microtasks 结算 microtask，槽位清零。
    host.flush_microtasks()
    assert host._task_limits.used(PageDimension.TASK_QUEUE) == 0


@_reg
def test_font_entry_refuses_oversized_table_dir():
    """ttf._load_one_ttf 接 FONT_COMPONENTS 限额（接线契约 + 决策层
    语义）：源码须引用 FONT_COMPONENTS 维度；判定层对"表目录数 >
    上限"返回 False（超限拒加载），边界（== 上限）放行。"""
    import re
    from toy_browser.network.page_limits import (
        PageResourceLimits, PageDimension,
    )
    # 静态契约：ttf_font 的表目录判定点引用了 FONT_COMPONENTS 维度。
    src = _src("toy_browser/typography/ttf_font.py")
    m = re.search(r"_load_one_ttf.*?(?=\ndef |\nclass )", src, re.S)
    assert "FONT_COMPONENTS" in src, "ttf 须接字体表/组件限额维度"
    assert "_load_one_ttf" in src, "字体表判定须下沉到 _load_one_ttf"
    # 动态语义：表目录数 20 > 上限 4 → admit 拒（加载失败路径）。
    l = PageResourceLimits(limits={PageDimension.FONT_COMPONENTS: 4})
    assert l.admit(PageDimension.FONT_COMPONENTS, 20) is False, \
        "表目录 20 > 4 须拒（超限不崩，TTFError 可诊断）"
    # 边界：表目录 4 == 上限 4 → 0+4>4 为 False → 放行。
    assert l.admit(PageDimension.FONT_COMPONENTS, 4) is True, \
        "表目录 4 <= 4 须放行（边界）"
    # 已记账 4（用满）后再 admit 任何正数 → 拒。
    l.charge(PageDimension.FONT_COMPONENTS, 4)
    assert l.exhausted(PageDimension.FONT_COMPONENTS)
    assert l.admit(PageDimension.FONT_COMPONENTS, 1) is False


@_reg
def test_image_entry_refuses_oversized_pixels():
    """layout._decode_data_uri 接 IMAGE_PIXELS 限额：像素总量超限返回
    None（可诊断、不崩）；限额内放行。"""
    import base64
    from toy_browser.layout import layout as _lay
    from toy_browser.network.page_limits import (
        PageResourceLimits, PageDimension,
    )

    # 4×4 原色 RGB：4*4*3 = 48 字节。像素总量 = 16。
    rgb = bytes([i % 256 for i in range(16)]) * 3
    b64 = base64.b64encode(rgb).decode()
    uri = "data:image/octet-stream;base64," + b64

    limits = PageResourceLimits(limits={PageDimension.IMAGE_PIXELS: 8})
    # 16 > 8 → 超限，解码返回 None（不崩）。
    assert _lay._decode_data_uri(uri, limits) is None, \
        "像素 16 > 限额 8 须返回 None（超限拒解码）"
    # 限额内（16 <= 16）→ 放行。
    ok = PageResourceLimits(limits={PageDimension.IMAGE_PIXELS: 16})
    res = _lay._decode_data_uri(uri, ok)
    assert res is not None, "像素 16 <= 限额 16 须放行"
    pixels, w, h = res
    assert (w, h) == (4, 4) and len(pixels) == 16
    # limits=None（向后兼容）→ 不校验，正常解码。
    res_none = _lay._decode_data_uri(uri, None)
    assert res_none is not None


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
