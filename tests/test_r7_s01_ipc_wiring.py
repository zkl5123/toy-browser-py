"""R7-S01 第 5 点接线 — DocProcessServer 的有限 IPC 校验生效。

锁定 DocProcessServer 的行为：
* 非法 kind 的 DocCommand 经 :meth:`post` 被拒收（返回 False，不入队）；
* 负 tab_id 的 DocCommand 被拒收；
* 宿主对象作 payload 的 DocCommand 被拒收；
* 合法命令正常投递（True，入队）；
* worker 产出的非法帧经 :meth:`drain` 被丢弃（不计 frames_drained）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import document_process as dp
from toy_browser.app.document_process import DocCommand, DocProcessServer

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


@_reg
def test_valid_command_posted():
    s = dp.DocProcessServer()
    cmd = DocCommand(tab_id=1, kind="navigate",
                     payload={"url": "http://a.test/"}, seq=1)
    assert s.post(cmd) is True, "合法命令必须正常投递"
    assert s.stats.commands_posted == 1


@_reg
def test_unknown_kind_rejected():
    s = dp.DocProcessServer()
    cmd = DocCommand(tab_id=1, kind="evil_kind", payload={}, seq=1)
    assert s.post(cmd) is False, "未知 kind 必须拒收"
    assert s.stats.commands_posted == 0, "拒收的命令不计入 posted"


@_reg
def test_negative_tab_id_rejected():
    s = dp.DocProcessServer()
    cmd = DocCommand(tab_id=-1, kind="navigate", payload={}, seq=1)
    assert s.post(cmd) is False, "负 tab_id 必须拒收"


@_reg
def test_bool_tab_id_rejected():
    s = dp.DocProcessServer()
    cmd = DocCommand(tab_id=True, kind="navigate", payload={}, seq=1)
    assert s.post(cmd) is False, "bool 不是合法 tab_id（bool 是 int 子类须单独排除）"


@_reg
def test_host_object_payload_rejected():
    s = dp.DocProcessServer()

    class _Host:
        pass

    cmd = DocCommand(tab_id=1, kind="navigate",
                     payload={"x": _Host()}, seq=1)
    assert s.post(cmd) is False, "宿主对象作 payload 必须拒收"


@_reg
def test_huge_payload_string_rejected():
    s = dp.DocProcessServer()
    # 10000 字符超过默认 4096 字节预算。
    cmd = DocCommand(tab_id=1, kind="navigate",
                     payload={"url": "x" * 10000}, seq=1)
    assert s.post(cmd) is False, "超大字符串 payload 必须拒收"


@_reg
def test_valid_command_kind_all_accepted():
    """所有合法 kind 都必须通过校验。"""
    s = dp.DocProcessServer()
    for kind in ("navigate", "input", "resize", "scroll", "stop"):
        cmd = DocCommand(tab_id=1, kind=kind, payload={}, seq=1)
        assert s.post(cmd) is True, f"kind={kind!r} 必须被接受"
    assert s.stats.commands_posted == 5


@_reg
def test_closed_tab_still_rejected_before_validation():
    s = dp.DocProcessServer()
    s.close_tab(99)
    cmd = DocCommand(tab_id=99, kind="navigate", payload={}, seq=1)
    assert s.post(cmd) is False, "已关 tab 的命令在任何校验前就丢弃"


@_reg
def test_drain_drops_invalid_frames_from_worker():
    """worker 产出的帧若含非法 error_category，drain 必须丢弃。

    用一个伪造的 DocFrame（绕过 worker 正常路径）验证 drain 的
    校验逻辑会拒收。"""
    s = dp.DocProcessServer()
    # 手动往 _pending_frames 塞一条非法帧（绕过 worker 正常路径，
    # 模拟"worker 回传了非法 error_category"的场景）。
    s._pending_frames.append(dp.DocFrame(
        tab_id=1, seq=1, ok=False, error_category="bogus_category",
        payload={}, navigation_id=0))
    frames = s.drain()
    assert len(frames) == 0, "非法 error_category 的帧必须被 drain 丢弃"
    assert s.stats.dropped_tab_frames >= 1


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")
