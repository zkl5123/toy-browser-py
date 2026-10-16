"""G09 — 标签页与历史会话回归（round5 docs 批次 4）。

覆盖 G09 step 1（稳定 tab_id + 关闭重建映射 + 旧回调失效）与
step 5（会话持久化：仅保存可恢复状态、原子写、损坏降级）：

- TabIdentityRegistry：allocate / idx_of / tab_id_at / invalidate /
  compact（旧 idx → 新 idx 映射）/ valid_idx（旧回调 no-op）
- 每标签资源（DocumentController / 历史 / 滚动）以 tab_id 为 key
- SessionStore.save / load：往返一致、损坏 / 缺失 / 未知版本降级、
  单 tab 字段缺失逐字段降级、活动标签失效回退首标签
"""
from __future__ import annotations

import json
import os
import tempfile

from toy_browser.app.tab_identity import (
    TabIdentityRegistry, TabSnapshot,
)
from toy_browser.app.session_store import SessionStore


TESTS = []


# ----------------------------------------------------------------------
# G09 step 1 — 稳定 tab_id
# ----------------------------------------------------------------------
def test_g09_tab_ids_are_stable():
    """新建 / 关闭后 tab_id 不复用（关闭中间标签后旧 tab_id 死掉）。"""
    reg = TabIdentityRegistry()
    a = reg.allocate()
    b = reg.allocate()
    c = reg.allocate()
    assert reg.tab_ids() == [a, b, c]
    assert reg.idx_of(b) == 1
    # 关闭中间标签 b（idx 1）
    closed = reg.invalidate(1)
    assert closed == b
    assert reg.idx_of(b) is None          # b 已死
    assert reg.idx_of(a) == 0
    assert reg.idx_of(c) == 1            # c 前移到 idx 1
    assert reg.tab_id_at(1) == c
    # 新标签拿到全新 tab_id（不复用 b）
    d = reg.allocate()
    assert d not in (a, b, c)
    assert reg.idx_of(b) is None          # 旧的 tab_id 永不复活


def test_g09_compact_mapping_rebuilds_old_callbacks():
    """关闭中间标签后 compact() 给出 旧idx→新idx 映射，旧回调可重绑；
    未变动的槽位映射到自身。"""
    reg = TabIdentityRegistry()
    reg.allocate(); reg.allocate(); reg.allocate(); reg.allocate()
    reg.invalidate(1)  # 关第 2 个
    mapping = reg.compact()
    # 旧 idx 0 → 新 0；旧 idx 2 → 新 1；旧 idx 3 → 新 2
    assert mapping[0] == 0
    assert mapping[2] == 1
    assert mapping[3] == 2
    assert reg.tab_ids() == sorted(reg.tab_ids())
    assert len(reg) == 3


def test_g09_stale_idx_callback_noop():
    """旧回调（持有旧 idx）经 valid_idx 检查后 no-op，不操作别的标签。"""
    reg = TabIdentityRegistry()
    reg.allocate(); reg.allocate(); reg.allocate()
    reg.invalidate(0)  # 关第一个，idx 全部前移
    # 旧回调仍拿着 idx=0 / idx=2
    assert reg.valid_idx(2) is False  # 旧 idx 2 已不存在（现只有 0,1）
    assert reg.valid_idx(0) is True   # 新 idx 0 有效（原是第 2 个标签）


def test_g09_per_tab_resources_bound_to_tab_id():
    """每标签资源（DocumentController / 历史 / 滚动）以 tab_id 为 key：
    切换标签只改前台，不重建文档；关闭标签释放资源。"""
    reg = TabIdentityRegistry()
    a = reg.allocate()
    b = reg.allocate()
    reg.set_resource(a, "url", "https://a")
    reg.set_resource(a, "back_stack", ["https://a0"])
    reg.set_resource(a, "scroll", 120)
    reg.set_resource(b, "url", "https://b")
    # 切到 b 前台，a 的资源仍在（不重建）
    assert reg.get_resource(a, "url") == "https://a"
    assert reg.get_resource(b, "url") == "https://b"
    # 关闭 a：释放
    reg.invalidate(0)
    assert reg.get_resource(a, "url") is None
    assert reg.get_resource(b, "url") == "https://b"


def test_g09_snapshot_roundtrip():
    """snapshot() → restore() 保留 url / 历史 / 滚动 / active。"""
    reg = TabIdentityRegistry()
    a = reg.allocate(); b = reg.allocate()
    reg.set_resource(a, "url", "https://a"); reg.set_resource(a, "title", "A")
    reg.set_resource(a, "back_stack", ["https://a0"])
    reg.set_resource(a, "fwd_stack", ["https://a1"])
    reg.set_resource(a, "scroll", 64); reg.set_resource(a, "zoom", 1.25)
    reg.set_resource(b, "url", "https://b"); reg.set_resource(b, "title", "B")
    snaps = reg.snapshot(active_tab_id=a)
    assert len(snaps) == 2
    assert snaps[0].active and not snaps[1].active
    # 重新构造 registry
    reg2 = TabIdentityRegistry.restore(snaps)
    assert len(reg2) == 2
    tids = reg2.tab_ids()
    assert reg2.get_resource(tids[0], "url") == "https://a"
    assert reg2.get_resource(tids[0], "back_stack") == ["https://a0"]
    assert reg2.get_resource(tids[0], "fwd_stack") == ["https://a1"]
    assert reg2.get_resource(tids[0], "scroll") == 64
    assert reg2.get_resource(tids[0], "zoom") == 1.25
    assert reg2.get_resource(tids[1], "url") == "https://b"


# ----------------------------------------------------------------------
# G09 step 5 — 会话持久化（原子写 + 损坏降级）
# ----------------------------------------------------------------------
def _tmp_path(tmpdir, name):
    return os.path.join(tmpdir, name)


def test_g09_session_save_load_roundtrip():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "s.json")
    store = SessionStore(p)
    reg = TabIdentityRegistry()
    a = reg.allocate(); b = reg.allocate()
    reg.set_resource(a, "url", "https://a"); reg.set_resource(a, "title", "A")
    reg.set_resource(a, "back_stack", ["https://a0"]); reg.set_resource(a, "scroll", 32)
    reg.set_resource(b, "url", "https://b"); reg.set_resource(b, "title", "B")
    store.save(reg.snapshot(active_tab_id=b), active_tab_id=b)
    # 文件确实写出（原子：无 .tmp 残留）
    assert os.path.isfile(p)
    assert not os.path.exists(p + ".tmp")
    snaps, active = store.load()
    assert len(snaps) == 2
    assert active == b
    urls = [s.url for s in snaps]
    assert urls == ["https://a", "https://b"]
    by_url = {s.url: s for s in snaps}
    assert by_url["https://a"].back_stack == ["https://a0"]
    assert by_url["https://a"].scroll == 32


def test_g09_session_missing_file_degrades_to_empty():
    p = _tmp_path(tempfile.mkdtemp(), "none.json")
    store = SessionStore(p)
    snaps, active = store.load()
    assert snaps == [] and active is None
    assert not store.has_session()


def test_g09_session_corrupt_json_degrades_to_empty():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "corrupt.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ not valid json ][")
    store = SessionStore(p)
    snaps, active = store.load()
    assert snaps == [] and active is None


def test_g09_session_unknown_version_degrades_to_empty():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "ver.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 999, "tabs": []}, f)
    store = SessionStore(p)
    snaps, active = store.load()
    assert snaps == [] and active is None


def test_g09_session_bad_tabs_field_degrades_to_empty():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "badtabs.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "tabs": "not-a-list"}, f)
    store = SessionStore(p)
    snaps, active = store.load()
    assert snaps == []


def test_g09_session_single_tab_partial_fields_degrade_fieldwise():
    """单个 tab 字段缺失 / 类型错 → 逐字段默认值，不整份丢弃。"""
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "partial.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "active_tab_id": None, "tabs": [
            {"tab_id": 1, "url": "https://x", "back_stack": "oops",
             "scroll": "not-an-int"},
        ]}, f)
    store = SessionStore(p)
    snaps, active = store.load()
    assert len(snaps) == 1
    s = snaps[0]
    assert s.url == "https://x"
    assert s.back_stack == []      # "oops" 非 list → 默认 []
    assert s.scroll == 0            # 非 int → 默认 0
    assert s.title == ""


def test_g09_session_tab_without_url_skipped():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "nourl.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "active_tab_id": None, "tabs": [
            {"tab_id": 1, "url": ""},
            {"tab_id": 2, "url": "https://ok"},
        ]}, f)
    store = SessionStore(p)
    snaps, active = store.load()
    assert len(snaps) == 1
    assert snaps[0].url == "https://ok"


def test_g09_session_active_tab_missing_falls_back_to_first():
    """活动标签指向不存在的 tab_id → 降级到第一个标签。"""
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "active.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "active_tab_id": 999, "tabs": [
            {"tab_id": 1, "url": "https://one"},
        ]}, f)
    store = SessionStore(p)
    snaps, active = store.load()
    assert len(snaps) == 1
    assert active == 1


def test_g09_session_in_memory_only_skips_disk():
    d = tempfile.mkdtemp()
    p = _tmp_path(d, "mem.json")
    store = SessionStore(p, in_memory_only=True)
    reg = TabIdentityRegistry()
    a = reg.allocate(); reg.set_resource(a, "url", "https://a")
    store.save(reg.snapshot(active_tab_id=a), active_tab_id=a)
    # in_memory_only：不落盘
    assert not os.path.isfile(p)


TESTS.append(("test_g09_tab_ids_are_stable", test_g09_tab_ids_are_stable))
TESTS.append(("test_g09_compact_mapping_rebuilds_old_callbacks",
              test_g09_compact_mapping_rebuilds_old_callbacks))
TESTS.append(("test_g09_stale_idx_callback_noop",
              test_g09_stale_idx_callback_noop))
TESTS.append(("test_g09_per_tab_resources_bound_to_tab_id",
              test_g09_per_tab_resources_bound_to_tab_id))
TESTS.append(("test_g09_snapshot_roundtrip", test_g09_snapshot_roundtrip))
TESTS.append(("test_g09_session_save_load_roundtrip",
              test_g09_session_save_load_roundtrip))
TESTS.append(("test_g09_session_missing_file_degrades_to_empty",
              test_g09_session_missing_file_degrades_to_empty))
TESTS.append(("test_g09_session_corrupt_json_degrades_to_empty",
              test_g09_session_corrupt_json_degrades_to_empty))
TESTS.append(("test_g09_session_unknown_version_degrades_to_empty",
              test_g09_session_unknown_version_degrades_to_empty))
TESTS.append(("test_g09_session_bad_tabs_field_degrades_to_empty",
              test_g09_session_bad_tabs_field_degrades_to_empty))
TESTS.append(("test_g09_session_single_tab_partial_fields_degrade_fieldwise",
              test_g09_session_single_tab_partial_fields_degrade_fieldwise))
TESTS.append(("test_g09_session_tab_without_url_skipped",
              test_g09_session_tab_without_url_skipped))
TESTS.append(("test_g09_session_active_tab_missing_falls_back_to_first",
              test_g09_session_active_tab_missing_falls_back_to_first))
TESTS.append(("test_g09_session_in_memory_only_skips_disk",
              test_g09_session_in_memory_only_skips_disk))
