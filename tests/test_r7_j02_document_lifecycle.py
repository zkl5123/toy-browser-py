"""R7-J02 第 4 点 — document 生命周期（readyState / DOMContentLoaded / load）。

Round-7 评估（逐项改进实施方案 R7-J02 第 4 点）：

* ``document.readyState`` 反映文档阶段：脚本执行中 ``"loading"``，
  解析 + 脚本排空后 ``"interactive"``，全部排空后 ``"complete"``；
* ``document.addEventListener("DOMContentLoaded", cb)`` 在状态推进到
  interactive 时结算；``"load"`` 在 complete 时结算；
* 监听器注册晚于事件触发（脚本在 load 后注册）也应可见——toy 采
  "注册即结算"语义，便于脚本在生命周期任意点观察状态。

本批完整 Python 3.11.9 环境回归（不依赖 Tk/wgpu）。
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


def _run(html):
    root = dom.parse(html)
    logs = []
    host = Host(root, log=logs.append)
    host.execute_all()
    return host, logs


@_test
def test_ready_state_progresses_to_complete():
    """execute_all 后 readyState 从 loading 推进到 complete"""
    html = ('<html><head></head><body>'
            '<script>console.log(document.readyState);</script>'
            '</body></html>')
    host, logs = _run(html)
    # 脚本执行中（execute_all 内）readyState 是 loading；排空后 complete。
    assert "loading" in logs, f"script must see readyState=loading during execution: {logs}"
    assert host._document_ready_state == "complete", (
        f"execute_all must end at complete, got {host._document_ready_state}")


@_test
def test_domcontentloaded_fires_at_interactive():
    """DOMContentLoaded 监听器在状态到 interactive 时结算（此时
    readyState 尚未 complete）"""
    html = ('<html><head></head><body>'
            '<script>document.addEventListener("DOMContentLoaded", '
            'function(){ console.log("dc:" + document.readyState); });'
            '</script></body></html>')
    host, logs = _run(html)
    assert "dc:interactive" in logs, (
        f"DOMContentLoaded must settle while readyState=interactive, got {logs}")
    assert "DOMContentLoaded" in host._document_fired


@_test
def test_load_fires_at_complete():
    """load 监听器在状态到 complete 时结算"""
    html = ('<html><head></head><body>'
            '<script>document.addEventListener("load", '
            'function(){ console.log("load:" + document.readyState); });'
            '</script></body></html>')
    host, logs = _run(html)
    assert "load:complete" in logs, (
        f"load must settle when readyState=complete, got {logs}")
    assert "load" in host._document_fired


@_test
def test_document_event_order():
    """DOMContentLoaded 先于 load（HTML 生命周期次序）"""
    html = ('<html><head></head><body>'
            '<script>'
            'document.addEventListener("DOMContentLoaded", function(){ console.log("D"); });'
            'document.addEventListener("load", function(){ console.log("L"); });'
            '</script></body></html>')
    host, logs = _run(html)
    assert logs.index("D") < logs.index("L"), (
        f"DOMContentLoaded must precede load, got {logs}")


@_test
def test_late_registration_settles():
    """J02 toy 语义——脚本内若在某状态后注册已触发的事件监听器，
    注册时立即结算（可见性优先）"""
    html = ('<html><head></head><body>'
            '<script>'
            # 注册一个 load 监听器；execute_all 排空后 load 已 complete，
            # 监听器在注册时（脚本体内）即结算。
            'document.addEventListener("load", function(){ console.log("late"); });'
            '</script></body></html>')
    host, logs = _run(html)
    # 脚本体内执行时 readyState 仍 loading，load 尚未触发；监听器入队。
    # execute_all 排空后触发 load（"late" 日志在脚本体之后）。
    assert "late" in logs, f"late-registered load handler must settle: {logs}"


@_test
def test_document_event_exception_isolated():
    """J02 第 5 点 — document 事件监听器异常只终止当前监听器，
    不阻断后续（多个监听器同事件）"""
    html = ('<html><head></head><body>'
            '<script>'
            'document.addEventListener("load", function(){ throw new Error("bad"); });'
            'document.addEventListener("load", function(){ console.log("good"); });'
            '</script></body></html>')
    host, logs = _run(html)
    assert "good" in logs, (
        f"second load handler must run after a bad one: {logs}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
