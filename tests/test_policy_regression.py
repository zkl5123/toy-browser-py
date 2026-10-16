"""C10 — 策略 API 与事件循环扩大回归（纯逻辑核心）回归
（round5 docs 批次 14）。

覆盖 toy_browser/app/policy_regression.py：
- policy_matrix：DocumentPolicy 一致性矩阵（各入口同策略 / 不一致
  定位字段 + 偏离入口）
- check_nonce：合法 / 不匹配 / 缺失 / 区分大小写
- resolve_script_url：相对 / 绝对 / 协议相对 / data / 空 + 去重
- classify_fetch：正文/JSON、失败拒绝、重定向、超时、取消、凭据、
  CORS 允许/拒绝（"拒绝路径不得误当成功"）
- ObserverGuard：批量记录 / 截断 / 触发次数上限 / disconnect /
  回调中不立即再触发（防无限触发）
- ResizeObserverGuard：尺寸不变不触发 / 布局变化触发 / 循环保护
- EventLoopScheduler：嵌套 Promise 微任务排空、定时器内微任务、
  重复定时器取消、rAF 顺序、排空错误记录
"""
from __future__ import annotations

from toy_browser.app.policy_regression import (
    PolicyConsistencyCase, policy_matrix,
    check_nonce, resolve_script_url, dedupe_scripts,
    classify_fetch,
    ObserverGuard, ResizeObserverGuard,
    EventLoopScheduler,
)


TESTS = []


# ----------------------------------------------------------------------
# policy_matrix — C10 步骤 1 一致性矩阵
# ----------------------------------------------------------------------
def _case(entry, final_url="https://x/a", base="https://x/",
          origin="https://x", csp="default-src 'self'",
          mode="cors"):
    return PolicyConsistencyCase(entry, final_url, base, origin, csp, mode)


def test_matrix_consistent():
    """各入口同一策略 → consistent=True。"""
    cases = [_case("cli"), _case("gui"), _case("refresh"),
             _case("history_back")]
    m = policy_matrix(cases)
    assert m["consistent"] is True
    assert m["mismatches"] == []
    assert m["expected"]["final_url"] == "https://x/a"


def test_matrix_detects_mismatch():
    """某入口 origin 偏离 → consistent=False，定位到 origin 字段 +
    偏离入口。"""
    cases = [_case("cli"), _case("gui", origin="https://evil")]
    m = policy_matrix(cases)
    assert m["consistent"] is False
    assert m["fields"]["origin"] is False
    assert m["fields"]["final_url"] is True   # final_url 仍一致
    assert ("origin",
            [("cli", "https://x"), ("gui", "https://evil")]) in \
        m["mismatches"]


def test_matrix_empty():
    m = policy_matrix([])
    assert m["consistent"] is True
    assert m["fields"] == {}


# ----------------------------------------------------------------------
# check_nonce — C10 步骤 2
# ----------------------------------------------------------------------
def test_nonce_ok_and_mismatch():
    assert check_nonce("abc", "abc")["allowed"] is True
    assert check_nonce("abc", "xyz")["allowed"] is False
    assert check_nonce("abc", "xyz")["reason"] == "nonce_mismatch"


def test_nonce_missing_and_absent():
    # 策略未声明 nonce → 允许（无需 nonce）
    assert check_nonce(None, None)["reason"] == "no_nonce"
    # 声明了但脚本没带 → 拒绝
    r = check_nonce("abc", None)
    assert r["allowed"] is False and r["reason"] == "nonce_missing"


def test_nonce_case_sensitivity():
    """默认区分大小写；case_insensitive 时不区分。"""
    assert check_nonce("ABC", "abc")["allowed"] is False      # 敏感
    assert check_nonce("ABC", "abc", case_insensitive=True)["allowed"] is True


# ----------------------------------------------------------------------
# resolve_script_url — C10 步骤 2
# ----------------------------------------------------------------------
def test_script_url_kinds():
    base = "https://host/dir/page.html"
    assert resolve_script_url("https://cdn/x.js", base)["kind"] == "absolute"
    assert resolve_script_url("//cdn/x.js", base)["kind"] == "protocol_relative"
    # 协议相对：补 base 的 scheme（https: + //cdn/x.js）
    assert resolve_script_url("//cdn/x.js", "https://h/p.html")["url"] == \
        "https://cdn/x.js"
    assert resolve_script_url("data:text/javascript,alert(1)", base)["kind"] == "data"
    assert resolve_script_url("rel/x.js", base)["kind"] == "relative"
    # 相对解析：base 目录（去最后一段 page.html → dir/）+ src
    assert resolve_script_url("rel/x.js", base)["url"] == \
        "https://host/dir/rel/x.js"
    assert resolve_script_url("", base)["kind"] == "empty"


def test_dedupe_scripts():
    out = dedupe_scripts(["a.js", "b.js", "a.js", "c.js", "b.js"])
    assert out == ["a.js", "b.js", "c.js"]


# ----------------------------------------------------------------------
# classify_fetch — C10 步骤 3（拒绝路径不得误当成功）
# ----------------------------------------------------------------------
def test_fetch_success_text_and_json():
    o = classify_fetch(200, body="hello")
    assert o.ok is True and o.success is True and o.category == "text"
    assert o.body == "hello"
    oj = classify_fetch(200, json_payload={"a": 1})
    assert oj.category == "json" and oj.json == {"a": 1}
    assert oj.success is True


def test_fetch_redirect_still_success():
    o = classify_fetch(301, body="", redirected=True)
    # 3xx 重定向：成功（body 记录），category=redirected
    assert o.ok is True and o.category == "redirected"
    assert o.success is True


def test_fetch_http_error_rejected():
    """4xx/5xx → 拒绝（不得误当成功）。"""
    o = classify_fetch(404)
    assert o.ok is False and o.success is False
    assert o.category == "failed" and o.status == 404
    o5 = classify_fetch(500)
    assert o5.ok is False and o5.success is False


def test_fetch_timeout_cancelled_cors():
    assert classify_fetch(0, timed_out=True).category == "timeout"
    assert classify_fetch(0, timed_out=True).success is False
    assert classify_fetch(0, cancelled=True).category == "cancelled"
    assert classify_fetch(0, cors_denied=True).category == "cors_denied"
    assert classify_fetch(0, cors_denied=True).success is False


def test_fetch_credentials_annotated():
    o = classify_fetch(200, body="ok", credentials="include")
    assert o.ok is True and "include" in o.reason


# ----------------------------------------------------------------------
# ObserverGuard — C10 步骤 4 防无限触发
# ----------------------------------------------------------------------
def test_observer_batch_drain():
    """批量记录 + drain（"批量记录"）。"""
    g = ObserverGuard(max_per_batch=2)
    for i in range(5):
        g.record(i)
    batch1 = g.drain()
    assert batch1 == [0, 1]           # 最多 max_per_batch
    batch2 = g.drain()
    assert batch2 == [2, 3]
    batch3 = g.drain()
    assert batch3 == [4]


def test_observer_fire_limit():
    """触发次数上限（循环保护，"观察器不能无限触发"）。"""
    g = ObserverGuard(max_fires=2)
    fired = 0
    for _ in range(10):
        if g.fire():
            g.end_callback()
            fired += 1
        else:
            break
    assert fired == 2          # 超过 max_fires 后不再触发


def test_observer_in_callback_no_immediate_refire():
    """回调中继续修改 → 不立即再触发（排下一批，避免无限递归）。"""
    g = ObserverGuard(max_fires=10)
    assert g.fire() is True       # 进入回调
    assert g.fire() is False      # 回调中再 fire → 拒绝（排下一批）
    g.end_callback()
    assert g.fire() is True       # 回调结束后可再触发


def test_observer_disconnect_stops():
    g = ObserverGuard()
    g.disconnect()
    g.record(1)
    assert g.drain() == []        # 断开后不记录 / 不触发
    assert g.fire() is False


# ----------------------------------------------------------------------
# ResizeObserverGuard — C10 步骤 4
# ----------------------------------------------------------------------
def test_resize_unchanged_no_trigger():
    """尺寸不变 → 不触发（"尺寸不变"）。"""
    ro = ResizeObserverGuard()
    assert ro.observe("k", (100, 200)) is True    # 首次（从无到有）
    assert ro.observe("k", (100, 200)) is False   # 不变 → 不触发


def test_resize_changed_triggers():
    """布局变化 → 触发一次（"布局变化"）。"""
    ro = ResizeObserverGuard()
    ro.observe("k", (100, 200))
    assert ro.observe("k", (300, 400)) is True


def test_resize_loop_guard():
    """循环保护：单轮对同一 key 触发次数超上限 → 拒绝。"""
    ro = ResizeObserverGuard(max_per_round=2)
    assert ro.observe("k", (1, 1)) is True
    assert ro.observe("k", (2, 2)) is True
    assert ro.observe("k", (3, 3)) is False   # 本轮已 2 次 → 拒绝
    ro.reset_round()
    assert ro.observe("k", (4, 4)) is True     # 新轮次恢复


# ----------------------------------------------------------------------
# EventLoopScheduler — C10 步骤 5 事件循环排空顺序
# ----------------------------------------------------------------------
def test_scheduler_microtasks_drain_after_task():
    """宏任务后排空微任务（嵌套 Promise 微任务 FIFO）。"""
    sched = EventLoopScheduler()
    order = []
    # 宏任务 T1；T1 里追加一个微任务（嵌套 Promise）
    t1 = sched.schedule("task", lambda: order.append("T1"))
    mid = sched.schedule("microtask", lambda: order.append("M1"))
    sched.drain()
    # M1 在 T1 后排空
    assert order.index("T1") < order.index("M1")


def test_scheduler_raf_order():
    """rAF 按调度顺序执行（后调度后执行）。"""
    sched = EventLoopScheduler()
    seq = []
    a = sched.schedule("raf", lambda: seq.append("A"))
    b = sched.schedule("raf", lambda: seq.append("B"))
    sched.drain()
    assert seq == ["A", "B"]


def test_scheduler_repeat_timer_cancel():
    """重复定时器取消 → 不再重新入队。"""
    sched = EventLoopScheduler()
    count = []
    def tick():
        count.append(1)
        if len(count) >= 1:
            sched.cancel(tid)   # 第一次执行后取消
    tid = sched.schedule("task", tick, repeat=True,
                         interval_id="iv1")
    sched.drain(max_steps=20)
    assert len(count) == 1       # 只执行一次（取消后不再入队）


def test_scheduler_drain_records_errors():
    """排空中出错 → 记录到 drain_errors（"记录异步排空错误"）。"""
    sched = EventLoopScheduler()

    def boom():
        raise RuntimeError("async drain error")
    sched.schedule("task", boom)
    sched.drain()
    assert len(sched.drain_errors) == 1
    assert "async drain error" in sched.drain_errors[0][2]


def test_scheduler_nested_microtask_from_microtask():
    """微任务里追加微任务（嵌套 Promise）也一起排空。"""
    sched = EventLoopScheduler()
    order = []
    sched.schedule("task", lambda: order.append("T"))
    # 微任务 M1 执行时追加 M2（嵌套）
    def m1():
        order.append("M1")
        sched.schedule("microtask", lambda: order.append("M2"))
    sched.schedule("microtask", m1)
    sched.drain()
    assert order == ["T", "M1", "M2"]   # 宏任务后微任务全部排空


def test_scheduler_is_drained():
    sched = EventLoopScheduler()
    assert sched.is_drained() is True
    sched.schedule("task", lambda: None)
    assert sched.is_drained() is False
    sched.drain()
    assert sched.is_drained() is True


TESTS.append(("test_matrix_consistent", test_matrix_consistent))
TESTS.append(("test_matrix_detects_mismatch",
              test_matrix_detects_mismatch))
TESTS.append(("test_matrix_empty", test_matrix_empty))
TESTS.append(("test_nonce_ok_and_mismatch",
              test_nonce_ok_and_mismatch))
TESTS.append(("test_nonce_missing_and_absent",
              test_nonce_missing_and_absent))
TESTS.append(("test_nonce_case_sensitivity",
              test_nonce_case_sensitivity))
TESTS.append(("test_script_url_kinds", test_script_url_kinds))
TESTS.append(("test_dedupe_scripts", test_dedupe_scripts))
TESTS.append(("test_fetch_success_text_and_json",
              test_fetch_success_text_and_json))
TESTS.append(("test_fetch_redirect_still_success",
              test_fetch_redirect_still_success))
TESTS.append(("test_fetch_http_error_rejected",
              test_fetch_http_error_rejected))
TESTS.append(("test_fetch_timeout_cancelled_cors",
              test_fetch_timeout_cancelled_cors))
TESTS.append(("test_fetch_credentials_annotated",
              test_fetch_credentials_annotated))
TESTS.append(("test_observer_batch_drain", test_observer_batch_drain))
TESTS.append(("test_observer_fire_limit", test_observer_fire_limit))
TESTS.append(("test_observer_in_callback_no_immediate_refire",
              test_observer_in_callback_no_immediate_refire))
TESTS.append(("test_observer_disconnect_stops",
              test_observer_disconnect_stops))
TESTS.append(("test_resize_unchanged_no_trigger",
              test_resize_unchanged_no_trigger))
TESTS.append(("test_resize_changed_triggers",
              test_resize_changed_triggers))
TESTS.append(("test_resize_loop_guard", test_resize_loop_guard))
TESTS.append(("test_scheduler_microtasks_drain_after_task",
              test_scheduler_microtasks_drain_after_task))
TESTS.append(("test_scheduler_raf_order", test_scheduler_raf_order))
TESTS.append(("test_scheduler_repeat_timer_cancel",
              test_scheduler_repeat_timer_cancel))
TESTS.append(("test_scheduler_drain_records_errors",
              test_scheduler_drain_records_errors))
TESTS.append(("test_scheduler_nested_microtask_from_microtask",
              test_scheduler_nested_microtask_from_microtask))
TESTS.append(("test_scheduler_is_drained", test_scheduler_is_drained))
