"""R7-A01 历史栈收敛 — 双栈字段是页内 HistoryCommitter 游标模型的只读投影。

Round-7 R7-A01：把 PageInstance 自持的双栈（back_stack / fwd_stack）
收敛到其页内 :class:`HistoryCommitter`（单一历史所有者），旧双栈字段
保留为游标投影，外部消费者（TabBrowser 主链 / session 持久化 /
tab_identity 快照 / about:history 页）按 list 读形态不变。

本批无 Tk 验证（headless 构造 PageInstance，直接驱动
``_commit_history`` / ``go_back`` / ``go_forward`` 并断言双栈投影
与游标语义一致，等价于旧双栈模型的 push/pop 时序）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui
from toy_browser.app.history_entry import HistoryCommitter

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


@_test
def test_page_owns_a_history_committer():
    """每个 PageInstance 持有自己的一份 HistoryCommitter（非浏览器级
    单例）；双栈字段初始为空投影。"""
    page = gui.PageInstance()
    assert isinstance(page._hist, HistoryCommitter), (
        "PageInstance must own a HistoryCommitter as the single "
        "history owner")
    # 两个页的 committer 互相独立（不串页）。
    page2 = gui.PageInstance()
    assert page._hist is not page2._hist, (
        "each PageInstance must own its own HistoryCommitter, "
        "not share a browser-level single instance")
    assert page.back_stack == [] and page.fwd_stack == []


@_test
def test_commit_projects_old_page_into_back_stack():
    """导航 about:blank → a → b：被离开的旧页依次落进 back_stack，
    前进栈被新导航清空（等价旧双栈 push 当前页 + clear fwd）。

    真实时序：``_commit_history`` 在 ``load_url`` 之前调用（此刻
    ``page.url`` 仍是被离开的旧页），随后 ``load_url`` 把
    ``page.url`` 推进到新目标。测试按该时序显式推进 ``page.url``。"""
    page = gui.PageInstance()
    page._commit_history("https://a")
    assert page.back_stack == ["about:blank"], (
        f"after first nav the old page must be backable, "
        f"got {page.back_stack}")
    page.url = "https://a"  # load_url 推进当前页
    page._commit_history("https://b")
    assert page.back_stack == ["about:blank", "https://a"], (
        f"back_stack must hold the page trail, got {page.back_stack}")
    assert page.fwd_stack == [], "new nav must clear the forward stack"
    page.url = "https://b"


@_test
def test_go_back_and_forward_move_cursor_and_reproject():
    """go_back / go_forward 经游标移动并重新投影双栈（等价旧 pop +
    push 语义）。"""
    page = gui.PageInstance()
    page._commit_history("https://a")
    page.url = "https://a"
    page._commit_history("https://b")
    page.url = "https://b"
    # 当前页 b；back: [about:blank, a]；fwd: []
    assert page.back_stack == ["about:blank", "https://a"]
    assert page.fwd_stack == []

    page.go_back()  # → a
    assert page.url == "https://a", f"go_back must land on a, got {page.url}"
    assert page.fwd_stack == ["https://b"], (
        f"the page we left becomes forwardable, got {page.fwd_stack}")
    assert page.back_stack == ["about:blank"], (
        f"back_stack shrinks to earlier trail, got {page.back_stack}")

    page.go_forward()  # → b
    assert page.url == "https://b", f"go_forward must land on b, got {page.url}"
    assert page.fwd_stack == []
    assert page.back_stack == ["about:blank", "https://a"]


@_test
def test_can_back_forward_track_cursor():
    """can_back / can_forward 反映游标位置（工具栏启停依据）。"""
    page = gui.PageInstance()
    # 初始 cursor=0（about:blank 是第 0 项）→ 无可后退项。
    assert page.can_back() is False, "at entry 0 there is nothing to back to"
    assert page.can_forward() is False
    page._commit_history("https://a")
    page.url = "https://a"
    page._commit_history("https://b")
    page.url = "https://b"
    assert page.can_back() is True
    assert page.can_forward() is False
    page.go_back()
    assert page.can_forward() is True


@_test
def test_about_history_page_sequence_uses_projection():
    """about:history 页拼接 back + [url] + fwd 的完整序列，投影保证
    当前页恰在中段、不重复。"""
    page = gui.PageInstance()
    page._commit_history("https://a")
    page.url = "https://a"
    page._commit_history("https://b")
    page.url = "https://b"
    seq = page.back_stack + [page.url] + page.fwd_stack
    assert seq == ["about:blank", "https://a", "https://b"], (
        f"history sequence must cover trail without dup, got {seq}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")
