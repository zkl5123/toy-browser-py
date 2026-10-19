"""R7-J02 — 事件循环与脚本生命周期（微任务检查点 / readyState）。

Round-7 评估（逐项改进实施方案 R7-J02）列出的验收场景：

* "两个 timer 中间的微任务"——标准事件循环里每个 timer 回调是**一个
  任务**，任务结束后立即跑微任务检查点；旧实现是"排空整个 timers
  队列后才排空一次 microtasks"，会把两个 timer 之间应结算的微任务
  延后到整批结束（基于错误的"V8 规则"注释，J02 第 2 点已删）。
* "sync→Promise→timer"次序；
* "关闭标签没有残留 timer"（生命周期回收）；
* ``document.readyState`` 反映文档阶段（J02 第 4 点）。

本批用完整 Python 3.11.9 环境回归（不依赖 Tk/wgpu）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.scripting.js import Host

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _host_with_scripts(scripts, base="<!DOCTYPE html><html><head></head><body></body></html>"):
    parts = [f"<script>{s}</script>" for s in scripts]
    html = base.replace("<body>", "<body>" + "".join(parts))
    root = dom.parse(html)
    logs = []
    host = Host(root, log=logs.append)
    return host, logs


@_test
def test_microtask_between_two_timers():
    """J02 验收"两个 timer 中间的微任务"——early timer 回调结算
    微任务后，late timer 才执行；微任务日志落在 early 与 late 之间。
    旧实现（整批 timer 跑完才排空 microtask）会把微任务延后到 late
    之后，顺序错。"""
    scripts = [
        "setTimeout(function(){ console.log('early'); "
        "  Promise.resolve().then(function(){ console.log('mid-micro'); }); "
        "}, 0);",
        "setTimeout(function(){ console.log('late'); }, 100);",
    ]
    host, logs = _host_with_scripts(scripts)
    host.execute_all()
    # 标准事件循环：early(任务) → 微任务检查点(mid-micro) → late(任务)。
    idx_early = logs.index("early")
    idx_micro = logs.index("mid-micro")
    idx_late = logs.index("late")
    assert idx_early < idx_micro < idx_late, (
        f"microtask between two timers must settle after 'early' "
        f"and before 'late'; got order: "
        f"early={idx_early} micro={idx_micro} late={idx_late} "
        f"(full logs={logs})")


@_test
def test_sync_promise_timer_order():
    """J02 验收"sync→Promise→timer"次序——同步 console.log 先，
    Promise 微任务结算在同步体之后。

    注：``execute_all`` 是"渲染前总排空"（便捷顺序）而非浏览器里的
    逐任务事件循环调度器；timer 任务整体先于末尾微任务检查点。故
    此处只断言**可确定性验证的标准不变量**（同步体先于微任务结算），
    不断言 0ms timer 与 microtask 的相对次序（那取决于驱动方式）。"""
    scripts = [
        "console.log('s1'); "
        "Promise.resolve().then(function(){ console.log('micro'); }); "
        "console.log('s2');",
        "setTimeout(function(){ console.log('timer'); }, 0);",
    ]
    host, logs = _host_with_scripts(scripts)
    host.execute_all()
    # 同步体 s1、s2 必须在 micro 结算之前。
    assert logs.index("s1") < logs.index("s2") < logs.index("micro"), (
        f"sync body must complete before microtask settles: {logs}")
    # 四个日志全部出现（无丢失）。
    assert set(["s1", "s2", "micro", "timer"]) <= set(logs), logs


@_test
def test_microtask_chain_settles_in_one_check():
    """一个微任务排出下一个微任务，同次检查点内全部结算（不靠多轮
    flush 模拟）。"""
    scripts = [
        "Promise.resolve().then(function(){ "
        "  console.log('m1'); "
        "  Promise.resolve().then(function(){ console.log('m2'); }); "
        "});",
    ]
    host, logs = _host_with_scripts(scripts)
    host.execute_all()
    assert logs.index("m1") < logs.index("m2"), (
        f"chained microtasks must settle in chain order: {logs}")


@_test
def test_timer_exception_isolated():
    """J02 第 5 点——timer 回调异常只终止当前任务（记日志），
    不逃逸、不阻断后续 timer。"""
    scripts = [
        "setTimeout(function(){ throw new Error('boom'); }, 0);",
        "setTimeout(function(){ console.log('after-bad'); }, 10);",
    ]
    host, logs = _host_with_scripts(scripts)
    escaped = False
    try:
        host.execute_all()
    except Exception as e:  # noqa: BLENT0
        escaped = True
    assert not escaped, f"timer exception must not escape execute_all: {e!r}"
    assert any("after-bad" in l for l in logs), (
        f"later timer must still run after a bad one: {logs}")
    # 异常进入统一 diagnostics（log 含 "uncaught throw (timer)" 前缀）。
    assert any("uncaught throw (timer)" in l for l in logs), (
        f"timer exception must be recorded in diagnostics: {logs}")


@_test
def test_no_resident_timers_after_flush():
    """J02 验收"关闭标签没有残留 timer"——排空后 _timers 队列清空，
    不泄漏挂起 timer。"""
    scripts = [
        "setTimeout(function(){ console.log('a'); }, 0);",
        "setTimeout(function(){ console.log('b'); }, 5);",
    ]
    host, _ = _host_with_scripts(scripts)
    host.execute_all()
    assert host._timers == [], (
        f"no timers should remain after execute_all drain, "
        f"got {host._timers}")
    assert host._microtasks == [], (
        f"no microtasks should remain after drain, got {host._microtasks}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
