"""批次 26 — G09 step 5 会话持久化损坏文件降级解析 回归。

被测：``toy_browser/app/session_recovery.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.session_recovery import (
    recover_session, recover_session_file,
    recover_history, recover_bookmarks,
    sanitize_entry, RecoveryReport,
    DEGRADED_NONE, DEGRADED_PARTIAL, DEGRADED_FULL,
    SOURCE_JSON, SOURCE_NDJSON, SOURCE_NONE,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 单条清洗（可恢复状态字段合法化）
# ---------------------------------------------------------------------------

@_reg
def test_sanitize_entry_valid():
    """合法可恢复条目 → 保留（字段合法）。"""
    e = {"tab_id": 1, "url": "http://a", "title": "A",
         "back_stack": ["http://0"], "fwd_stack": [],
         "scroll": 120, "zoom": 1.0, "active": True}
    c = sanitize_entry(e)
    assert c == e


@_reg
def test_sanitize_entry_drops_illegal_fields():
    """含非法字段（DOM / 位图 / 脚本 Host）→ 丢弃这些字段，保留合法项。"""
    c = sanitize_entry({"url": "http://a", "dom": "<tree>",
                        "bitmap": b"\x89PNG", "script_host": {}})
    assert c is not None
    assert c == {"url": "http://a"}  # 只有 url 合法


@_reg
def test_sanitize_entry_all_illegal():
    """全字段非法 → None（视为非法项）。"""
    c = sanitize_entry({"dom": "<tree>", "bitmap": b"x"})
    assert c is None


@_reg
def test_sanitize_entry_not_dict():
    """非 dict → None。"""
    assert sanitize_entry("http://a") is None
    assert sanitize_entry(42) is None
    assert sanitize_entry(None) is None


@_reg
def test_sanitize_entry_bool_not_number():
    """bool 不算 int/float（tab_id / scroll 等数值字段收紧）。"""
    c = sanitize_entry({"tab_id": True, "url": "http://a"})
    assert c is not None
    assert "tab_id" not in c  # True 不被当 int 收下
    assert c == {"url": "http://a"}


# ---------------------------------------------------------------------------
# recover_session（已解析 / 待解析负载）
# ---------------------------------------------------------------------------

@_reg
def test_recover_session_valid_json_list():
    """合法 JSON 数组 → 全保留、degraded=none、source=json。"""
    entries, rep = recover_session(
        '[{"tab_id":1,"url":"http://a"},'
        '{"tab_id":2,"url":"http://b","active":true}]')
    assert len(entries) == 2
    assert rep.degraded == DEGRADED_NONE
    assert rep.source == SOURCE_JSON
    assert rep.skipped == []


@_reg
def test_recover_session_mixed_drops_illegal():
    """混合合法 / 非法项 → 保留合法、跳过非法、degraded=partial。"""
    entries, rep = recover_session(
        '[{"url":"http://a"},'
        '{"dom":"<tree>"},'
        '{"url":"http://b","scroll":40}]')
    assert len(entries) == 2
    assert rep.degraded == DEGRADED_PARTIAL
    assert len(rep.skipped) == 1
    assert "dom" in rep.skipped[0]["raw"] or "dom" in str(
        rep.skipped[0]["raw"])


@_reg
def test_recover_session_ndjson_truncated():
    """截断 JSON（数组没闭合）→ NDJSON 逐行抢救、degraded=partial。"""
    text = '[{"url":"http://a"},\n{"url":"http://b"},\n{"url":"http://c"'
    entries, rep = recover_session(text)
    # 三行都可逐行解析（最后一行单独是合法对象）
    assert len(entries) >= 2
    assert rep.source == SOURCE_NDJSON
    assert rep.degraded == DEGRADED_PARTIAL


@_reg
def test_recover_session_full_corrupt():
    """全损（非 JSON 乱码）→ 空会话、degraded=full、source=none。"""
    entries, rep = recover_session("!!! this is not json @@@")
    assert entries == []
    assert rep.degraded == DEGRADED_FULL
    assert rep.source == SOURCE_NONE


@_reg
def test_recover_session_empty_string():
    """空串 → 空会话（降级非错误）、degraded=none。"""
    entries, rep = recover_session("")
    assert entries == []
    assert rep.degraded == DEGRADED_NONE


@_reg
def test_recover_session_dict_tabs_form():
    """dict 形态 {"tabs":[...]}（TabSession.snapshot 序列化）→ 取 tabs。"""
    payload = {"tabs": [{"url": "http://a"}, {"url": "http://b"}]}
    entries, rep = recover_session(payload)
    assert len(entries) == 2
    assert rep.source == SOURCE_JSON


@_reg
def test_recover_session_single_dict():
    """单 dict（非 tabs 形态）→ 当一条清洗。"""
    entries, rep = recover_session({"url": "http://x", "active": True})
    assert len(entries) == 1
    assert entries[0]["url"] == "http://x"


@_reg
def test_recover_session_illegal_payload():
    """非法 payload（None / int）→ 全损降级空会话。"""
    for bad in (None, 42, object()):
        entries, rep = recover_session(bad)
        assert entries == []
        assert rep.degraded == DEGRADED_FULL


# ---------------------------------------------------------------------------
# recover_session_file（真实文件降级）
# ---------------------------------------------------------------------------

def _write_tmp(content: str) -> str:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


@_reg
def test_recover_file_valid():
    """文件完好 JSON 数组 → 全保留、degraded=none。"""
    path = _write_tmp('[{"url":"http://a"},{"url":"http://b"}]')
    try:
        entries, rep = recover_session_file(path)
        assert len(entries) == 2
        assert rep.degraded == DEGRADED_NONE
        assert rep.source == SOURCE_JSON
    finally:
        os.remove(path)


@_reg
def test_recover_file_missing():
    """文件不存在 → 空会话（降级非错误）。"""
    entries, rep = recover_session_file(
        os.path.join(os.path.dirname(__file__), "no_such_xyz.json"))
    assert entries == []
    assert rep.degraded == DEGRADED_NONE


@_reg
def test_recover_file_truncated_ndjson():
    """截断文件（NDJSON 部分完好行）→ 逐行抢救。"""
    path = _write_tmp('{"url":"http://a"}\n{"url":"http://b"}\n{"url":"http')
    try:
        entries, rep = recover_session_file(path)
        assert len(entries) == 2
        assert rep.source == SOURCE_NDJSON
        assert rep.degraded == DEGRADED_PARTIAL
    finally:
        os.remove(path)


@_reg
def test_recover_file_full_corrupt():
    """全损文件（纯乱码）→ 空会话、degraded=full。"""
    path = _write_tmp("\x00\xff\xfe not json at all")
    try:
        entries, rep = recover_session_file(path)
        assert entries == []
        assert rep.degraded == DEGRADED_FULL
    finally:
        os.remove(path)


@_reg
def test_recover_file_empty():
    """空文件 → 空会话、degraded=none。"""
    path = _write_tmp("")
    try:
        entries, rep = recover_session_file(path)
        assert entries == []
        assert rep.degraded == DEGRADED_NONE
    finally:
        os.remove(path)


# ---------------------------------------------------------------------------
# recover_history / recover_bookmarks（单条对象抢救）
# ---------------------------------------------------------------------------

@_reg
def test_recover_history_valid():
    """合法历史项 → 保留 + 补齐 title / ts 缺省。"""
    entries, rep = recover_history([
        {"url": "http://a", "title": "A", "ts": 100.0},
        {"url": "http://b"},  # 缺 title / ts → 补齐
    ])
    assert len(entries) == 2
    assert entries[1]["title"] == ""
    assert entries[1]["ts"] == 0.0
    assert rep.degraded == DEGRADED_NONE


@_reg
def test_recover_history_drops_no_url():
    """无 url 的历史项 → 丢弃 + 记 skipped。"""
    entries, rep = recover_history([{"url": "http://a"},
                                     {"title": "no url"},
                                     {"dom": "x"}])
    assert len(entries) == 1
    assert rep.degraded == DEGRADED_PARTIAL
    assert len(rep.skipped) == 2


@_reg
def test_recover_bookmarks_valid():
    """合法书签项 → 保留 + 缺省 title 取 url。"""
    entries, rep = recover_bookmarks([
        {"url": "http://a", "title": "A"},
        {"url": "http://b"},  # 缺 title → 取 url
    ])
    assert len(entries) == 2
    assert entries[1]["title"] == "http://b"
    assert rep.degraded == DEGRADED_NONE


@_reg
def test_recover_bookmarks_drops_illegal():
    """非法书签项 → 丢弃。"""
    entries, rep = recover_bookmarks([{"url": "http://a"},
                                       {"dom": "x"},
                                       {"bitmap": b"y"}])
    assert len(entries) == 1
    assert rep.degraded == DEGRADED_PARTIAL


# ---------------------------------------------------------------------------
# 报告 / 诊断
# ---------------------------------------------------------------------------

@_reg
def test_report_summary():
    """RecoveryReport.summary：关键位。"""
    rep = RecoveryReport(
        recovered=[{"url": "a"}, {"url": "b"}],
        skipped=[{"raw": "x", "reason": "no-url"}],
        degraded=DEGRADED_PARTIAL, source=SOURCE_NDJSON, total_seen=3)
    s = rep.summary()
    assert "recovered=2" in s
    assert "skipped=1" in s
    assert "degraded=partial" in s
    assert "source=ndjson" in s


@_reg
def test_no_raise_on_any_input():
    """任何输入都不 raise（损坏不阻塞浏览器启动，G09 验收）。"""
    for bad in (None, "", "{}", "[", "[{]", b"\x00\x01", 0, -1,
                "[1,2,3]", "[{\"dom\":1}]"):
        e1, r1 = recover_session(bad)
        e2, r2 = recover_history(bad)
        e3, r3 = recover_bookmarks(bad)
        assert isinstance(e1, list)
        assert isinstance(e2, list)
        assert isinstance(e3, list)


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
