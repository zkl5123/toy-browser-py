"""R7-S03 第 5/6 点 — StorageService 分区、配额与生命周期（纯逻辑）。

修复前 ``web_api.Storage`` 是**每个 ``js.Host`` 一份**的裸 dict：

* 宿主每份文档一个 ⇒ 同源导航之间 localStorage **不保留**（违反第 5
  点"同源导航保留"）；
* 没有任何按 origin / tab / profile 的分区概念（跨源隔离只是"碰巧"，
  因为根本没共享）；
* 配额靠 ``_used_bytes()`` **每次 setItem 全量重算**（O(n)），第 6
  点要求增量；
* 内部 dict 是公开属性 ``_store``，页面拿到对象即可直接读写（S01 要
  封掉的宿主直触）。

本文件锁 ``scripting/storage_service.py`` 的契约：local 按
profile+origin、session 按 tab+origin、opaque 每文档独立且不跨导航、
增量配额、失败原子化、清理与 LRU 驱逐、内部字典不可直触。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.scripting import storage_service as ss

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 静态层 — 源码契约
# ---------------------------------------------------------------------------

@_reg
def test_static_internal_dict_is_private():
    """第 6 点 + S01：不得有可直接触达的内部 dict 属性。"""
    src = inspect.getsource(ss.StorageArea)
    assert "__store" in src, "内部字典必须私有（名字改写）"
    assert "self.__store" in src, "内部字典必须私有（名字改写）"
    assert "self._store" not in src, \
        "不得保留单下划线这种可直触的内部 dict 属性"
    assert "def snapshot" in src, "需要一次性读取时只给副本"


@_reg
def test_static_quota_is_incremental():
    """第 6 点：setItem 不得再全量重算已用字节。"""
    src = inspect.getsource(ss.StorageArea.set_item)
    assert "_byte_len" in src
    assert "self.__used" in src, "已用字节必须增量维护"
    for banned in ("sum(", "for k, v in", "_used_bytes"):
        assert banned not in src, f"setItem 不得再全量重算（{banned}）"


@_reg
def test_static_set_item_is_atomic():
    """第 6 点：失败原子化 —— 超配额时先抛异常，不写、不半改。"""
    src = inspect.getsource(ss.StorageArea.set_item)
    idx_raise = src.find("raise QuotaExceededError")
    idx_write = src.find("self.__store[")
    assert idx_raise > 0 and idx_write > idx_raise, \
        "必须先判定配额再写入（超配额时不得留下半截状态）"


@_reg
def test_static_service_is_pure():
    src = inspect.getsource(ss)
    for banned in ("import os", "open(", "time.time", "import sqlite3"):
        assert banned not in src, f"纯逻辑层不得出现 {banned}"


# ---------------------------------------------------------------------------
# origin 归一化 / opaque 判定（第 5 点）
# ---------------------------------------------------------------------------

@_reg
def test_normalize_origin():
    assert ss.normalize_origin("https://A.test/") == "https://A.test"
    assert ss.normalize_origin("") == ""
    assert ss.normalize_origin("null") == ""
    assert ss.normalize_origin(None) == ""


@_reg
def test_opaque_origins():
    for o in ("", "null", "data:text/html,x", "blob:https://a.test/1",
              "about:blank", "file:///tmp/x.html"):
        assert ss.is_opaque_origin(o), f"{o} 必须是 opaque"


@_reg
def test_tu_origins_are_not_opaque():
    for o in ("https://a.test", "http://127.0.0.1:8000"):
        assert not ss.is_opaque_origin(o), f"{o} 是真实 origin"


# ---------------------------------------------------------------------------
# 分区（第 5 点）
# ---------------------------------------------------------------------------

@_reg
def test_local_partition_is_profile_plus_origin():
    svc = ss.StorageService()
    a1 = svc.local_area("https://a.test")
    a2 = svc.local_area("https://a.test")
    assert a1 is a2, "同源同 profile 必须共享同一份 localStorage"
    b = svc.local_area("https://b.test")
    assert b is not a1, "跨源必须隔离"
    c = svc.local_area("https://a.test", profile_id="p2")
    assert c is not a1, "不同 profile 必须隔离"


@_reg
def test_session_partition_is_tab_plus_origin():
    svc = ss.StorageService()
    t1 = svc.session_area("https://a.test", tab_id="tab1")
    t2 = svc.session_area("https://a.test", tab_id="tab2")
    assert t1 is not t2, "sessionStorage 按标签隔离"
    assert svc.session_area("https://a.test", tab_id="tab1") is t1


@_reg
def test_local_survives_same_origin_navigation():
    """第 5 点验收：同源导航保留（旧实现每份文档一份，丢了）。"""
    svc = ss.StorageService()
    # 导航 1
    local, _sess = svc.areas_for_document("https://a.test", tab_id="tab1")
    local.set_item("k", "v")
    # 导航 2（新的"文档"，同一 origin 同一 tab）
    local2, sess2 = svc.areas_for_document("https://a.test", tab_id="tab1")
    assert local2 is local
    assert local2.get_item("k") == "v", "同源导航必须保留 localStorage"
    # sessionStorage 同一 tab 内也保留
    _, sess1 = svc.areas_for_document("https://a.test", tab_id="tab1")
    sess1.set_item("s", "1")
    _, sess2b = svc.areas_for_document("https://a.test", tab_id="tab1")
    assert sess2b.get_item("s") == "1"


@_reg
def test_cross_origin_isolated():
    svc = ss.StorageService()
    a, _ = svc.areas_for_document("https://a.test", tab_id="t")
    a.set_item("k", "a-value")
    b, _ = svc.areas_for_document("https://b.test", tab_id="t")
    assert b.get_item("k") is None, "跨源不得读到另一个 origin 的数据"
    b.set_item("k", "b-value")
    assert a.get_item("k") == "a-value", "互不影响"


@_reg
def test_opaque_origin_is_per_document():
    """第 5 点：opaque origin 每文档独立，且**不**跨导航保留。"""
    svc = ss.StorageService()
    a, _ = svc.areas_for_document("data:text/html,x", opaque_token="doc1")
    a.set_item("k", "v")
    b, _ = svc.areas_for_document("data:text/html,x", opaque_token="doc2")
    assert b.get_item("k") is None, "opaque 每文档独立"
    c, _ = svc.areas_for_document("", opaque_token="doc3")
    assert c.get_item("k") is None, "空 origin 也是 opaque，不得共享"
    assert "opaque#" in str(a.storage_key.origin)


@_reg
def test_opaque_cleared_on_navigation():
    svc = ss.StorageService()
    a, _ = svc.areas_for_document("about:blank", opaque_token="d1")
    a.set_item("k", "v")
    n = svc.clear_opaque()
    assert n >= 1
    again, _ = svc.areas_for_document("about:blank", opaque_token="d1")
    assert again.get_item("k") is None, "opaque 不跨导航保留"


# ---------------------------------------------------------------------------
# 配额：增量 + 原子化（第 6 点）
# ---------------------------------------------------------------------------

@_reg
def test_used_bytes_is_incremental_and_exact():
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"))
    area.set_item("a", "12")
    assert area.used_bytes == 3, "key 1 字节 + value 2 字节"
    area.set_item("a", "1234")
    assert area.used_bytes == 5, "覆盖时按增量（不是累加）"
    area.remove_item("a")
    assert area.used_bytes == 0
    area.set_item("中文", "值")
    assert area.used_bytes == len("中文".encode()) + len("值".encode())


@_reg
def test_quota_exceeded_is_atomic():
    """失败原子化：超配额的 setItem 不得留下任何痕迹。"""
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"), quota=10)
    area.set_item("k", "12345")          # 6 bytes
    assert area.used_bytes == 6
    try:
        area.set_item("k2", "xxxxxxx")    # 8 → 14 > 10
        raise AssertionError("超配额必须抛 QuotaExceededError")
    except ss.QuotaExceededError:
        pass
    # 关键：没有任何半截状态。
    assert area.get_item("k2") is None
    assert area.used_bytes == 6, "配额失败必须保持原状"
    assert area.length == 1
    # 覆盖成更大的值也要原子失败。
    try:
        area.set_item("k", "x" * 20)
        raise AssertionError("覆盖超配额也必须抛")
    except ss.QuotaExceededError:
        pass
    assert area.get_item("k") == "12345", "失败不得破坏旧值"


@_reg
def test_overwrite_that_fits_is_allowed():
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"), quota=10)
    area.set_item("k", "12345")
    area.set_item("k", "1234567")   # 8 bytes ≤ 10
    assert area.used_bytes == 8 and area.get_item("k") == "1234567"


@_reg
def test_clear_resets_usage():
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"))
    area.set_item("a", "1")
    area.set_item("b", "2")
    area.clear()
    assert area.used_bytes == 0 and area.length == 0
    assert area.get_item("a") is None


@_reg
def test_key_and_length():
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"))
    assert area.length == 0 and area.key(0) is None
    area.set_item("x", "1")
    area.set_item("y", "2")
    assert area.length == 2
    assert {area.key(0), area.key(1)} == {"x", "y"}
    assert area.key(5) is None and area.key(-1) is None


@_reg
def test_snapshot_is_a_copy():
    """S01：交出去的是副本，改它不影响内部状态。"""
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"))
    area.set_item("k", "v")
    snap = area.snapshot()
    snap["k"] = "hacked"
    snap["new"] = "x"
    assert area.get_item("k") == "v"
    assert area.length == 1


@_reg
def test_no_public_internal_dict_attribute():
    """页面（或任何调用方）拿不到内部 dict。"""
    area = ss.StorageArea(ss.StorageKey("local", "https://a.test"))
    area.set_item("k", "v")
    for name in ("_store", "store", "__store", "_used", "used"):
        assert not hasattr(area, name), f"不得暴露内部属性 {name}"
    # S01：JS 侧连属性名都读不到（白名单只放行规范成员）。
    from toy_browser.scripting import js as _js
    for name in ("_store", "__store", "_StorageArea__store", "used_bytes"):
        assert _js.host_object_read(area, name) is _js._UNDEFINED, \
            f"JS 侧不得读到 {name}"


# ---------------------------------------------------------------------------
# 清理与驱逐（第 6 点）
# ---------------------------------------------------------------------------

@_reg
def test_clear_origin_and_tab_and_profile():
    svc = ss.StorageService()
    svc.local_area("https://a.test", profile_id="p1").set_item("k", "v")
    svc.session_area("https://a.test", tab_id="t1").set_item("s", "v")
    svc.local_area("https://b.test", profile_id="p1").set_item("k", "v")
    assert svc.clear_origin("https://a.test") == 2, "local + session 都清"
    assert svc.area_count == 1
    svc.session_area("https://b.test", tab_id="t9").set_item("s", "v")
    assert svc.clear_tab("t9") == 1
    assert svc.clear_profile("p1") == 1
    assert svc.area_count == 0


@_reg
def test_clear_tab_does_not_touch_local():
    svc = ss.StorageService()
    svc.local_area("https://a.test", profile_id="p").set_item("k", "v")
    svc.session_area("https://a.test", tab_id="t").set_item("s", "v")
    svc.clear_tab("t")
    assert svc.local_area("https://a.test", profile_id="p").get_item("k") == "v"


@_reg
def test_global_cap_triggers_lru_eviction():
    svc = ss.StorageService(max_total_bytes=20, quota_per_area=100)
    a = svc.local_area("https://a.test")
    b = svc.local_area("https://b.test")
    a.set_item("k", "1" * 8)     # 9 bytes
    b.set_item("k", "1" * 8)     # 9 bytes → total 18
    a.get_item("k")              # a 更新 → b 变成最久未访问
    c = svc.local_area("https://c.test")
    c.set_item("k", "1" * 8)     # 27 > 20 → 驱逐 LRU
    assert svc.total_bytes <= 20, f"全局上限必须生效：{svc.total_bytes}"
    assert b.storage_key not in svc._StorageService__areas or \
        svc.local_area("https://b.test").get_item("k") is None, \
        "被驱逐的必须是最久未访问的那个（b）"


@_reg
def test_evict_keeps_recent_when_asked():
    svc = ss.StorageService(max_total_bytes=10, quota_per_area=100)
    a = svc.local_area("https://a.test")
    a.set_item("k", "1" * 20)
    assert svc.evict_lru(keep=1) == 0, "keep=1 时不得驱逐仅有的分区"


@_reg
def test_describe_is_observable():
    svc = ss.StorageService()
    svc.local_area("https://a.test").set_item("k", "v")
    txt = svc.describe()
    assert "StorageService(1 areas" in txt and "https://a.test" in txt


@_reg
def test_quota_default_is_5mib():
    assert ss.QUOTA_BYTES_PER_ORIGIN == 5 * 1024 * 1024
    assert ss.StorageService().quota_per_area == ss.QUOTA_BYTES_PER_ORIGIN


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
