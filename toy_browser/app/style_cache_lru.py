"""批次 28 — C05 step 5 按 Document 管理的带上限 LRU 缓存容器（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C05 step 5：

> 所有长期缓存有上限并按 Document 管理；节点释放后不依赖可复用的
> Python id 单独识别身份。

C05 验收：

> 动态 class、祖先选择器、继承、resize 媒体规则及节点删除后再分配。
> 冷/热结果相同；修改后无旧样式；记录真实命中率和分段耗时。

批次 11（:mod:`toy_browser.app.style_cache`，C05 step 1–4）已落地
**缓存键 / 失效范围 / 命中率统计 / 缓存预算**（`StyleCacheKey` /
`InvalidateScope` / `HitRateStats` / `document_cache_budget`），并声明
了"按 Document 管理 + 上限 4096"的预算约定；本批补**容器层**——把
"按 document_id 管理 + 带上限 LRU + 节点释放后不靠 Python id 识别身份"
这件事固化成可测试的纯逻辑：

* :class:`StyleCache` — 单个 Document 的样式匹配缓存实例（带上限
  LRU）：``get`` / ``put`` / ``invalidate`` / ``clear`` / ``size`` /
  ``evicted_count``。键是**显式内容键**（:class:`toy_browser.app.
  style_cache.StyleCacheKey` 或任意 hashable），**不是** Python 对象
  的 ``id()``（C05 step 5 "节点释放后不依赖可复用的 Python id 单独
  识别身份"）——被释放的节点若再分配新对象，不会误命中旧对象残留的
  缓存项。
* :class:`DocumentCacheManager` — 按 ``document_id`` 管理多个
  :class:`StyleCache`（C05 step 5 "按 Document 管理"）：``get_cache``
  取 / 建该文档的缓存（带上限），``drop_document`` 在文档销毁 / 关标签
  时清掉该文档的缓存（复用批次 23/25 关标签路径的约定），
  ``total_size`` / ``drop_all`` 全局诊断。
* :func:`content_cache_key` — 把"节点内容标识"（tag + class + id +
  style 修订）归一成 hashable 键，供 css.py 的 candidate-rule 缓存
  （``_candidate_rules_cache``）按**内容**而非 ``id(对象)`` 寻址
  （C05 验收"节点删除后再分配"：同 tag/class 的节点删除后再分配，
  键相同 → 复用命中；id 变了也不影响）。

设计要点（与 css.py 现有缓存**兼容不重叠**）：
- css.py 的 ``_candidate_rules_cache`` 仍持有实际缓存实例（模块级
  dict + ``clear_caches``）；本模块提供"该怎么键、该怎么管、上限多少、
  命中率多少"的**容器 + 决策层**，css.py 在活显示批次把 candidate-rule
  缓存迁到 :class:`DocumentCacheManager` 时**直接调用**，不另写一套。
- LRU 用 ``collections.OrderedDict``（stdlib）：``put`` / ``get`` 命中
  都 move-to-end，超上限从最旧端驱逐（``evicted_count`` 累计供诊断）。
- 键必须 hashable；传入 ``StyleCacheKey``（frozen dataclass）天然
  hashable；传入普通 dict 会报错（明确"不要传可变键"）。

纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib ``collections``。
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

# 与 css.py _CANDIDATE_RULES_CACHE_MAX 一致（批次 11 document_cache_budget 默认）
DEFAULT_CACHE_MAX = 4096


# ----------------------------------------------------------------------
# 内容缓存键（C05 step 5 "节点释放后不依赖可复用 Python id 识别身份"）
# ----------------------------------------------------------------------

def content_cache_key(tag: str, classes: tuple = (), style_revision: int = 0,
                      element_id: Optional[str] = None,
                      identity_includes_id: bool = True) -> dict:
    """C05 step 5 — 把节点**内容标识**归一成可哈希缓存键（dict 返回前
    由调用方转 :class:`toy_browser.app.style_cache.StyleCacheKey` 或
    直接作为 ``StyleCache`` 的键值对键）。

    * *tag* — 元素标签（``"div"`` / ``"p"`` / ...）。
    * *classes* — 类名序列（tuple，序不变；``()`` 表示无类）。
    * *style_revision* — 该节点内联 style 的修订号（style 变则 bump；
      与批次 24 G09 ``HistoryCommitter`` 同款"一次改动一个修订"语义）。
    * *element_id* — 元素 id（可选）。
    * *identity_includes_id* — 对应 css.py ``_identity_includes_id``
      标志（P34.5）：False 时键**不含** id（常见 tag/class 驱动规则集
      里，含 id 会让"每节点唯一 id"100% 未命中缓存，css.py 因此有
      该开关）。

    返回 ``{"tag":…, "classes":…, "style_revision":…, "id":…}``（dict，
    本身不可哈希；调用方应 ``json`` 序列化或转 :class:`StyleCacheKey`
    后用，:func:`_hashable` 提供便捷哈希）。**绝不**用 ``id(对象)``——
    节点删除后再分配新对象 ``id`` 会变，但内容键不变（C05 验收"节点
    删除后再分配"冷/热结果相同）。"""
    key: dict = {"tag": tag, "classes": tuple(classes)}
    if identity_includes_id:
        key["id"] = element_id or ""
        key["style_revision"] = int(style_revision)
    else:
        # 不含 id 时仍含 style_revision（style 变要重算）
        key["style_revision"] = int(style_revision)
    return key


def _hashable(key: dict) -> tuple:
    """把一个 :func:`content_cache_key` 结果（dict）转成可哈希 tuple。"""
    return (key.get("tag"),
            tuple(key.get("classes", ())),
            key.get("id"),
            key.get("style_revision", 0))


def content_key_fingerprint(key: dict, limit: int = 16) -> str:
    """键的内容指纹（前 *limit* 位十六进制）：诊断 / 日志展示用。

    确定性：同键同指纹（不依赖 ``id`` / 内存地址）。"""
    raw = json_dumps_stable(key)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:limit]


def json_dumps_stable(obj) -> str:
    """稳定 JSON 序列化（键排序 + 紧凑），供指纹 / 落盘对照。"""
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


# ----------------------------------------------------------------------
# 单 Document 缓存（带上限 LRU）
# ----------------------------------------------------------------------

@dataclass
class StyleCache:
    """C05 step 5 — 单个 Document 的样式匹配缓存实例（带上限 LRU）。

    * ``max_entries`` — 上限（默认 4096，与 css.py
      ``_CANDIDATE_RULES_CACHE_MAX`` 一致；C05 step 5 "所有长期缓存
      有上限"）。
    * :meth:`get` / :meth:`put` — 读 / 写（命中或 put 都 move-to-end，
      LRU 语义）。
    * :meth:`invalidate` — 按键失效（单条）。
    * :meth:`clear` — 全清（文档销毁 / 导航）。
    * :meth:`size` / :meth:`evicted_count` — 诊断。

    键是**显式内容键**（hashable），不是 ``id(对象)``（C05 step 5）。
    """
    max_entries: int = DEFAULT_CACHE_MAX

    def __post_init__(self) -> None:
        self._data: "OrderedDict" = OrderedDict()
        self._evicted = 0

    # ---- 读 / 写 -----------------------------------------------------
    def get(self, key, default=None):
        """读（命中 move-to-end；未命中返回 *default*）。"""
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return default

    def put(self, key, value) -> None:
        """写（更新现有键 move-to-end；新增超上限则从最旧端驱逐）。"""
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        # 超上限：从最旧端驱逐（LRU 最旧 = 序列头）
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)
            self._evicted += 1

    # ---- 失效 --------------------------------------------------------
    def invalidate(self, key) -> bool:
        """按键失效一条；返回是否真的删了（False = 键不在）。"""
        if key in self._data:
            del self._data[key]
            return True
        return False

    def clear(self) -> int:
        """全清；返回清掉的条目数。"""
        n = len(self._data)
        self._data.clear()
        return n

    # ---- 诊断 --------------------------------------------------------
    def size(self) -> int:
        return len(self._data)

    def evicted_count(self) -> int:
        """LRU 累计驱逐次数（"上限有没有被打到"诊断）。"""
        return self._evicted

    def keys(self) -> list:
        """当前键（LRU 顺序：头最旧、尾最新）。"""
        return list(self._data.keys())

    def __contains__(self, key) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)


# ----------------------------------------------------------------------
# 按 document_id 管理的缓存容器（C05 step 5 "按 Document 管理"）
# ----------------------------------------------------------------------

@dataclass
class DocumentCacheManager:
    """C05 step 5 — 按 ``document_id`` 管理多个 :class:`StyleCache`。

    * *max_entries_per_document* — 每文档缓存上限（默认 4096，与
      css.py ``_CANDIDATE_RULES_CACHE_MAX`` 一致）。
    * :meth:`get_cache` — 取 / 建该文档缓存（带该上限的 LRU）。
    * :meth:`drop_document` — 文档销毁 / 关标签时清掉该文档缓存
      （复用批次 23/25 关标签"释放资源"约定；返回清掉的条目数）。
    * :meth:`total_size` / :meth:`drop_all` — 全局诊断 / 全清。

    多标签场景下每个标签（Document）各持一个 :class:`StyleCache`
    （上限各 4096），互不串页（G09 验收"不串页"同语义）；关标签
    （批次 23/25）时 ``drop_document`` 释放该文档缓存。"""
    max_entries_per_document: int = DEFAULT_CACHE_MAX

    def __post_init__(self) -> None:
        self._caches: "OrderedDict" = OrderedDict()

    def get_cache(self, document_id) -> StyleCache:
        """取 / 建 *document_id* 对应的缓存（带上限 LRU）。

        取到现有则 move-to-end（最常用文档保活）；新建则带上限。"""
        cache = self._caches.get(document_id)
        if cache is None:
            cache = StyleCache(max_entries=self.max_entries_per_document)
            self._caches[document_id] = cache
        else:
            self._caches.move_to_end(document_id)
        return cache

    def drop_document(self, document_id) -> int:
        """文档销毁 / 关标签：清掉该文档缓存；返回清掉的条目数。

        （C05 step 5 "按 Document 管理" + 批次 23/25 关标签释放
        资源约定；节点释放后不靠 Python id 识别身份——整文档缓存随
        文档销毁丢弃，节点级内容键在缓存内已无残留。）"""
        cache = self._caches.pop(document_id, None)
        if cache is None:
            return 0
        return cache.clear()

    def has_document(self, document_id) -> bool:
        return document_id in self._caches

    def documents(self) -> list:
        """当前持有缓存的 document_id（LRU 顺序：头最久未用）。"""
        return list(self._caches.keys())

    def total_size(self) -> int:
        """所有文档缓存条目总数（全局诊断）。"""
        return sum(c.size() for c in self._caches.values())

    def total_evicted(self) -> int:
        """所有文档缓存累计 LRU 驱逐次数。"""
        return sum(c.evicted_count() for c in self._caches.values())

    def drop_all(self) -> int:
        """全清（导航 / 关全部标签 / 进程退出）；返回清掉的总条目数。"""
        n = self.total_size()
        self._caches.clear()
        return n

    def __contains__(self, document_id) -> bool:
        return document_id in self._caches

    def __len__(self) -> int:
        return len(self._caches)


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys
    sys.path.insert(0, ".")
    from toy_browser.app.style_cache import StyleCacheKey

    mgr = DocumentCacheManager(max_entries_per_document=3)
    doc_a = mgr.get_cache("doc-1")
    doc_b = mgr.get_cache("doc-2")
    k = StyleCacheKey(stylesheet_revision=1,
                      media=frozenset({"light"}))
    for i in range(5):
        doc_a.put((k, i), f"v{i}")
    print("doc_a size:", doc_a.size(), "evicted:", doc_a.evicted_count())
    print("total_size:", mgr.total_size())
    print("drop doc-1:", mgr.drop_document("doc-1"))
    print("total_size after drop:", mgr.total_size())
    print("mgr docs:", mgr.documents())
