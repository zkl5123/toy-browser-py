"""批次 28 — C05 step 5 按 Document 管理的带上限 LRU 缓存容器 回归。

被测：``toy_browser/app/style_cache_lru.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.style_cache_lru import (
    StyleCache, DocumentCacheManager,
    content_cache_key, _hashable, content_key_fingerprint,
    DEFAULT_CACHE_MAX,
)
from toy_browser.app.style_cache import StyleCacheKey

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# StyleCache（单文档 LRU）
# ---------------------------------------------------------------------------

@_reg
def test_style_cache_put_get():
    """put/get 基本读写（命中返回存值）。"""
    c = StyleCache(max_entries=4)
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None
    assert c.get("missing", "dflt") == "dflt"


@_reg
def test_style_cache_lru_eviction():
    """超上限 LRU 驱逐：最旧端被逐，evicted_count 累计。"""
    c = StyleCache(max_entries=2)
    c.put("a", 1); c.put("b", 2)
    c.put("c", 3)  # 驱逐 a（最旧）
    assert c.size() == 2
    assert c.evicted_count() == 1
    assert c.get("a") is None
    assert c.get("b") == 2 and c.get("c") == 3


@_reg
def test_style_cache_lru_recency():
    """LRU 顺序：get / put 命中都 move-to-end（不逐最近用的）。"""
    c = StyleCache(max_entries=2)
    c.put("a", 1); c.put("b", 2)
    c.get("a")        # a 变最新
    c.put("c", 3)     # 应驱逐 b（最旧），保留 a
    assert c.get("b") is None
    assert c.get("a") == 1 and c.get("c") == 3


@_reg
def test_style_cache_invalidate():
    """invalidate 单条失效：存在 → True，不存在 → False。"""
    c = StyleCache(max_entries=4)
    c.put("a", 1)
    assert c.invalidate("a") is True
    assert c.get("a") is None
    assert c.invalidate("a") is False  # 已无
    assert c.size() == 0


@_reg
def test_style_cache_clear():
    """clear 全清：返回清掉条目数 + 容量空。"""
    c = StyleCache(max_entries=8)
    for i in range(5):
        c.put(str(i), i)
    assert c.clear() == 5
    assert c.size() == 0
    assert c.clear() == 0  # 已空


@_reg
def test_style_cache_contains_len():
    """__contains__ / __len__ / keys。"""
    c = StyleCache(max_entries=8)
    c.put("x", 1); c.put("y", 2)
    assert "x" in c and "y" in c and "z" not in c
    assert len(c) == 2
    assert set(c.keys()) == {"x", "y"}


@_reg
def test_style_cache_default_max():
    """默认上限 4096（与 css.py _CANDIDATE_RULES_CACHE_MAX 一致）。"""
    c = StyleCache()
    assert c.max_entries == DEFAULT_CACHE_MAX == 4096


# ---------------------------------------------------------------------------
# DocumentCacheManager（按 document_id 管理）
# ---------------------------------------------------------------------------

@_reg
def test_manager_get_cache_isolated():
    """按 document_id 隔离：两个文档各持独立缓存，互不串页。"""
    mgr = DocumentCacheManager(max_entries_per_document=2)
    a = mgr.get_cache("doc-1")
    b = mgr.get_cache("doc-2")
    a.put("k", "va")
    b.put("k", "vb")
    assert a.get("k") == "va"
    assert b.get("k") == "vb"
    assert a is not b
    assert mgr.total_size() == 2


@_reg
def test_manager_get_cache_reuse():
    """get_cache 同 document_id 返回同一缓存实例（复用）。"""
    mgr = DocumentCacheManager()
    a1 = mgr.get_cache("d1")
    a1.put("x", 1)
    a2 = mgr.get_cache("d1")
    assert a1 is a2
    assert a2.get("x") == 1
    assert len(mgr) == 1


@_reg
def test_manager_drop_document():
    """drop_document 清掉该文档缓存（关标签释放资源）；返回清掉数。"""
    mgr = DocumentCacheManager(max_entries_per_document=4)
    a = mgr.get_cache("doc-1")
    for i in range(3):
        a.put(str(i), i)
    mgr.get_cache("doc-2").put("k", "v")
    assert mgr.drop_document("doc-1") == 3
    assert "doc-1" not in mgr
    assert mgr.get_cache("doc-2").get("k") == "v"
    # 重取 doc-1 是全新缓存（无残留旧条目）
    fresh = mgr.get_cache("doc-1")
    assert fresh.get("0") is None
    assert mgr.total_size() == 1


@_reg
def test_manager_drop_missing():
    """drop_document 不存在的 document_id → 0（无副作用）。"""
    mgr = DocumentCacheManager()
    assert mgr.drop_document("nope") == 0


@_reg
def test_manager_documents_order():
    """documents：LRU 顺序（头最久未用，尾最近用）。"""
    mgr = DocumentCacheManager()
    mgr.get_cache("d1")
    mgr.get_cache("d2")
    mgr.get_cache("d3")
    mgr.get_cache("d1")  # d1 变最新
    docs = mgr.documents()
    assert docs == ["d2", "d3", "d1"]


@_reg
def test_manager_total_evicted():
    """total_evicted：累计所有文档 LRU 驱逐次数。"""
    mgr = DocumentCacheManager(max_entries_per_document=2)
    a = mgr.get_cache("d1")
    for i in range(4):
        a.put(i, i)  # 上限 2，插入 4 个 → 驱逐 2 次
    assert mgr.total_evicted() == 2
    assert a.evicted_count() == 2


@_reg
def test_manager_drop_all():
    """drop_all 全清（导航 / 关全部标签）；返回清掉的总条目数。"""
    mgr = DocumentCacheManager(max_entries_per_document=8)
    for d in ("d1", "d2", "d3"):
        c = mgr.get_cache(d)
        for i in range(2):
            c.put(i, i)
    assert mgr.drop_all() == 6
    assert len(mgr) == 0
    assert mgr.total_size() == 0


@_reg
def test_manager_per_document_max():
    """每文档上限生效（d1 上限 1，d2 上限仍 1；不串）。"""
    mgr = DocumentCacheManager(max_entries_per_document=1)
    a = mgr.get_cache("d1")
    b = mgr.get_cache("d2")
    a.put("a", 1); a.put("b", 2)  # 驱逐 a
    b.put("x", 1)
    assert a.size() == 1 and a.get("a") is None
    assert b.size() == 1 and b.get("x") == 1
    assert mgr.total_size() == 2


# ---------------------------------------------------------------------------
# 内容缓存键（C05 step 5 "不依赖 Python id 识别身份"）
# ---------------------------------------------------------------------------

@_reg
def test_content_key_no_python_id():
    """content_cache_key 只含内容（tag/class/id/style_revision），不含
    Python 对象 id：节点删除后再分配新对象，同内容 → 同键（冷/热相同）。"""
    # 模拟"同一个 div 节点删除后重新创建"（两次构造，id 对象不同）
    k1 = content_cache_key("div", classes=("card",), element_id="c1",
                           style_revision=3)
    k2 = content_cache_key("div", classes=("card",), element_id="c1",
                           style_revision=3)
    assert k1 == k2
    assert _hashable(k1) == _hashable(k2)
    # 节点内容变了（class 变）→ 键不同（失效重算）
    k3 = content_cache_key("div", classes=("other",), element_id="c1",
                           style_revision=3)
    assert _hashable(k3) != _hashable(k1)


@_reg
def test_content_key_style_revision():
    """style 变（style_revision bump）→ 键不同（C05 验收"修改后无旧样式"）。"""
    k_old = content_cache_key("p", classes=(), style_revision=1)
    k_new = content_cache_key("p", classes=(), style_revision=2)
    assert _hashable(k_old) != _hashable(k_new)


@_reg
def test_content_key_identity_includes_id_flag():
    """identity_includes_id=False（css.py P34.5 标志）：键不含 id。"""
    k1 = content_cache_key("div", classes=("a",), element_id="x1",
                           identity_includes_id=False)
    k2 = content_cache_key("div", classes=("a",), element_id="x2",
                           identity_includes_id=False)
    # 不含 id → 相同内容（同 tag/class/style_revision）同键（高命中）
    assert _hashable(k1) == _hashable(k2)
    # 含 id 时不同 id 不同键
    c1 = content_cache_key("div", classes=("a",), element_id="x1",
                           identity_includes_id=True)
    c2 = content_cache_key("div", classes=("a",), element_id="x2",
                           identity_includes_id=True)
    assert _hashable(c1) != _hashable(c2)


@_reg
def test_content_key_fingerprint_stable():
    """content_key_fingerprint：同键同指纹、异键异指纹（确定性）。"""
    k1 = content_cache_key("div", ("a",), style_revision=1, element_id="x")
    k2 = content_cache_key("div", ("a",), style_revision=1, element_id="x")
    k3 = content_cache_key("span", ("a",), style_revision=1, element_id="x")
    f1 = content_key_fingerprint(k1)
    f2 = content_key_fingerprint(k2)
    f3 = content_key_fingerprint(k3)
    assert f1 == f2
    assert f1 != f3
    assert len(f1) == 16


@_reg
def test_style_cache_key_hashable():
    """StyleCacheKey（frozen dataclass）可直接作 StyleCache 键。"""
    c = StyleCache()
    k = StyleCacheKey(stylesheet_revision=2,
                      media=frozenset({"light"}))
    c.put(k, "matched-rules")
    assert c.get(k) == "matched-rules"
    k2 = StyleCacheKey(stylesheet_revision=3)
    assert k2 not in c


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
