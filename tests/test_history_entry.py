"""G09 step 3 — 统一历史提交入口回归（round5 docs 批次 8）。

覆盖 toy_browser/app/history_entry.py HistoryCommitter：
- 一次新导航 = 恰好一个历史项（重定向只形成一个历史项，携带最终 URL）
- 同址去重（cursor 指向项 URL == 最终 URL → 不新增，原地更新）
- 前进/后退是游标移动（不新增项；移动后前进栈清空，无历史分叉）
- 各来源（address/link/bookmark/file）都接同一入口，source 标签区分
- can_back / can_forward / current（工具栏按钮启停依据）
"""
from __future__ import annotations

from toy_browser.app.history_entry import (
    HistoryCommitter, HistoryEntry,
    SRC_ADDRESS, SRC_LINK, SRC_BOOKMARK, SRC_FILE,
)


TESTS = []


def _comm():
    return HistoryCommitter(initial_url="about:blank")


def test_g09_commit_one_entry_per_navigation():
    """一次导航 = 一个历史项（cursor 0=blank，新导航 cursor=1）。"""
    c = _comm()
    i = c.commit("https://a", source=SRC_ADDRESS)
    assert i == 1
    assert c.current() == "https://a"
    assert len(c) == 2  # blank + a


def test_g09_redirect_single_entry_final_url():
    """重定向只形成一个历史项，携带最终 URL（中间跳转不入栈）。"""
    c = _comm()
    i = c.commit("https://a", source=SRC_ADDRESS,
                 final_url="https://a/2")
    ent = c.entries[i]
    assert ent.url == "https://a/2"      # 最终 URL 入栈
    assert ent.start_url == "https://a"  # 发起 URL 保留（诊断用）
    # 只新增一项（不是两个）
    assert len(c) == 2  # blank + a/2
    assert c.current() == "https://a/2"


def test_g09_redirect_no_final_uses_url():
    c = _comm()
    c.commit("https://x", source=SRC_ADDRESS)
    assert c.current() == "https://x"


def test_g09_dedup_same_url_no_new_entry():
    """同址重复提交去重：cursor 指向项 URL == 最终 URL → 不新增。"""
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    n = len(c)
    # 重定向/输入再次到 a（去重）
    i = c.commit("https://a", source=SRC_LINK,
                 final_url="https://a", dedup=True)
    assert i == 1
    assert len(c) == n  # 没新增
    # source 原地更新
    assert c.entries[i].source == SRC_LINK


def test_g09_dedup_disabled_appends():
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    i = c.commit("https://a", source=SRC_LINK, dedup=False)
    assert c.current() == "https://a"
    # dedup=False：强制新增同址项（语义：允许重复提交），但会清
    # 空前进栈。这里两次 a 之间无前进项，cursor 移到新项。
    assert i == 2
    assert c.can_forward() is False


def test_g09_back_forward_move_cursor_no_new_entry():
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    c.commit("https://b", source=SRC_LINK)
    n = len(c)
    assert c.can_forward() is False
    e = c.back()
    assert e is not None and e.url == "https://a"
    assert c.can_forward() is True
    assert len(c) == n  # back 不新增
    e2 = c.forward()
    assert e2 is not None and e2.url == "https://b"
    assert len(c) == n  # forward 不新增


def test_g09_back_at_top_returns_none():
    c = _comm()
    assert c.back() is None
    assert c.can_back() is False


def test_g09_forward_at_end_returns_none():
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    assert c.forward() is None
    assert c.can_forward() is False


def test_g09_new_navigation_clears_forward_stack():
    """新导航清空前进栈（无历史分叉）。"""
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    c.commit("https://b", source=SRC_LINK)
    c.back()  # → a
    assert c.can_forward() is True
    # 新导航 c（不同址，去重不影响）
    c.commit("https://c", source=SRC_ADDRESS)
    assert c.can_forward() is False  # 前进栈已清
    assert len(c) == 3  # blank + a + c


def test_g09_all_sources_share_one_entry():
    """各来源都接同一入口（source 标签区分）。"""
    c = HistoryCommitter(initial_url="about:blank")
    c.commit("file:///x.html", source=SRC_FILE)
    c.commit("https://bm", source=SRC_BOOKMARK)
    c.commit("https://link", source=SRC_LINK)
    assert c.entries[1].source == SRC_FILE
    assert c.entries[2].source == SRC_BOOKMARK
    assert c.entries[3].source == SRC_LINK


def test_g09_current_empty_when_no_history():
    c = HistoryCommitter()
    assert c.current() == ""
    assert c.can_back() is False and c.can_forward() is False


def test_g09_entry_is_historyentry():
    c = _comm()
    c.commit("https://a", source=SRC_ADDRESS)
    ent = c.entries[1]
    assert isinstance(ent, HistoryEntry)
    assert ent.start_url == "https://a"


TESTS.append(("test_g09_commit_one_entry_per_navigation",
              test_g09_commit_one_entry_per_navigation))
TESTS.append(("test_g09_redirect_single_entry_final_url",
              test_g09_redirect_single_entry_final_url))
TESTS.append(("test_g09_redirect_no_final_uses_url",
              test_g09_redirect_no_final_uses_url))
TESTS.append(("test_g09_dedup_same_url_no_new_entry",
              test_g09_dedup_same_url_no_new_entry))
TESTS.append(("test_g09_dedup_disabled_appends",
              test_g09_dedup_disabled_appends))
TESTS.append(("test_g09_back_forward_move_cursor_no_new_entry",
              test_g09_back_forward_move_cursor_no_new_entry))
TESTS.append(("test_g09_back_at_top_returns_none",
              test_g09_back_at_top_returns_none))
TESTS.append(("test_g09_forward_at_end_returns_none",
              test_g09_forward_at_end_returns_none))
TESTS.append(("test_g09_new_navigation_clears_forward_stack",
              test_g09_new_navigation_clears_forward_stack))
TESTS.append(("test_g09_all_sources_share_one_entry",
              test_g09_all_sources_share_one_entry))
TESTS.append(("test_g09_current_empty_when_no_history",
              test_g09_current_empty_when_no_history))
TESTS.append(("test_g09_entry_is_historyentry",
              test_g09_entry_is_historyentry))
