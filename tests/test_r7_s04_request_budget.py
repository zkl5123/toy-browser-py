"""R7-S04 第 3 点 — 网络请求的 deadline / 取消 / 聚合预算（纯逻辑）。

审计 S04 第 3 点："deadline、取消与连接关闭放进共同 finally；
超限连接不归还可复用池"。本文件锁定**决策层**的契约，不跑真实
socket（那是接线批的事）：

* :class:`Deadline`：注入单调时钟，``expired`` / ``remaining`` 纯
  判定，``budget <= 0`` 等价"未启用"（remaining 无穷）；
* :class:`CancellationScope`：粘性取消——取消后 :meth:`check` 永远
  抛 :class:`RequestCancelled`，不可撤销；原因被截断为有限摘要；
* :class:`PageBudget`：原始/解压字节分别累计，任一超限即
  ``exhausted``，剩余请求全部拒绝；记账失败请求不计（先成功后记账）；
* 静态锁定：模块不 import socket / http.client / threading。
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network import request_budget as rb
from toy_browser.network.request_budget import (
    Deadline, CancellationScope, PageBudget, RequestCancelled,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# -- Deadline --------------------------------------------------------------

@_reg
def test_deadline_injected_clock_is_pure():
    d = Deadline(monotonic=100.0, budget=10.0)
    assert d.remaining(105.0) == 5.0
    assert abs(d.remaining(109.999) - 0.001) < 1e-9
    assert not d.expired(109.0)
    assert d.expired(110.0)
    assert d.expired(111.0)


@_reg
def test_deadline_expired_at_boundary():
    d = Deadline(monotonic=0.0, budget=5.0)
    assert d.remaining(5.0) == 0.0
    assert d.expired(5.0), "boundary 即过期（<=0 剩余）"


@_reg
def test_deadline_disabled_when_budget_nonpositive():
    d = Deadline(monotonic=0.0, budget=0.0)
    assert d.remaining(0.0) == float("inf")
    assert not d.expired(0.0)
    d2 = Deadline(monotonic=0.0, budget=-1.0)
    assert d2.remaining(999.0) == float("inf")


@_reg
def test_deadline_remaining_clamped_to_zero():
    d = Deadline(monotonic=0.0, budget=5.0)
    assert d.remaining(100.0) == 0.0, "过期后不得报负数剩余"


# -- CancellationScope -------------------------------------------------------

@_reg
def test_cancel_is_sticky_and_raises_forever():
    s = CancellationScope()
    s.cancel()
    for _ in range(3):
        try:
            s.check()
            raise AssertionError("取消后 check 必须永远抛错")
        except RequestCancelled:
            pass


@_reg
def test_not_cancelled_does_not_raise():
    s = CancellationScope()
    s.check()  # no-op
    assert not s.cancelled


@_reg
def test_cancel_reason_truncated_to_bounded_summary():
    s = CancellationScope()
    s.cancel("x" * 10000)
    assert len(s.reason) == 128, "取消原因必须是有限摘要（防注入超长）"
    assert s.reason == "x" * 128


@_reg
def test_second_cancel_keeps_first_reason():
    s = CancellationScope()
    s.cancel("first")
    s.cancel("second")
    assert s.reason == "first", "粘性取消：首次原因不可被覆盖"


# -- PageBudget --------------------------------------------------------------

@_reg
def test_budget_tracks_raw_and_decoded_separately():
    b = PageBudget(raw=100, decoded=100)
    b.charge(60, 30)
    assert b.raw_used == 60 and b.decoded_used == 30
    assert b.remaining_for(40, 0)   # 60+40 = 100 恰好不超
    assert not b.remaining_for(41, 0)
    assert not b.exhausted


@_reg
def test_budget_exhausted_blocks_remaining_requests():
    b = PageBudget(raw=100, decoded=100)
    b.charge(100, 0)
    assert b.exhausted
    assert not b.remaining_for(1, 0), "已耗尽的预算拒绝任何剩余请求"


@_reg
def test_budget_decoded_dimension_blocks_ghb():
    """解压炸弹：原始字节很小，但解压字节顶到解码维度。"""
    b = PageBudget(raw=10_000, decoded=500)
    assert b.remaining_for(100, 400)   # 原始维度放得下
    b.charge(100, 400)
    assert not b.exhausted             # 400/500，未用满
    b.charge(0, 100)                   # 解压 500/500，用满
    assert b.exhausted                 # >= 上限即耗尽
    assert not b.remaining_for(0, 1)   # 再要 1 字节解压会超


@_reg
def test_budget_negative_charge_ignored():
    b = PageBudget(raw=10, decoded=10)
    b.charge(-5, -5)
    assert b.raw_used == 0 and b.decoded_used == 0


# -- 静态锁定 ----------------------------------------------------------------

@_reg
def test_module_is_pure_logic_no_runtime_imports():
    src = inspect.getsource(rb)
    for banned in ("import socket", "http.client", "import threading",
                   "ssl", "subprocess"):
        assert banned not in src, f"request_budget 不得 import {banned}"
    # 时钟只允许在 Deadline.now 里出现一次
    assert src.count("time.monotonic()") == 1


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
