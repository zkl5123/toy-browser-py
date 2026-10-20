"""R7-S03 第 5/6 点 — Storage 分区、配额与生命周期（纯逻辑层）。

方案原文：

* 第 5 点：``StorageService`` 在**宿主侧**按 ``profile+origin``
  （localStorage）或 ``tab+origin``（sessionStorage）管理；opaque
  origin 策略明确，同源导航保留、跨源隔离。
* 第 6 点：配额**增量计算**、失败**原子化**、清理和驱逐；通过 S01 的
  封装**禁止直接触达内部 dict**。

仓库现状
--------
``web_api.Storage`` 是**每个 ``js.Host`` 一份**的裸 dict：

* 宿主是每份文档新建一个 ⇒ 同源导航之间 ``localStorage`` **不保留**
  （违反第 5 点"同源导航保留"），而且不同源的页面各自一份（这条
  碰巧隔离了，但不是因为分区，而是因为根本没共享）；
* 配额靠 ``_used_bytes()`` **每次 setItem 全量重算**（O(n) 扫一遍
  全部键值——第 6 点要求增量）；
* 内部 dict 就叫 ``_store``，是**公开属性**，页面只要拿到对象就能
  直接读写（S01 要封掉的正是这类宿主直触）。

本模块提供
----------
* :class:`StorageArea` —— 一个分区（= 一个 origin 的一种 storage）：
  增量维护已用字节、**失败原子化**（超配额时**先算后写**，绝不留下
  半截状态）、内部字典是**私有**（名字改写 + 只暴露规范方法与
  ``snapshot()`` 副本）。
* :class:`StorageService` —— 宿主侧的分区表：local 按
  ``(profile, origin)``、session 按 ``(tab, origin)``；opaque origin
  （``""`` / ``null`` / ``data:`` / ``blob:`` / ``about:`` / ``file:``）
  按 **per-document token** 独立且**不跨导航保留**；提供按 origin /
  tab / profile 的清理与 LRU 驱逐，以及全局字节上限。

覆盖范围与"不冒充通过"
----------------------
纯逻辑：不落盘（磁盘持久化、QuotaManager 的用量上报、跨进程共享
**未实现**）；不读时钟（LRU 用单调递增的访问序号，不读系统时钟）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = [
    "QUOTA_BYTES_PER_ORIGIN", "DEFAULT_MAX_TOTAL_BYTES",
    "LOCAL", "SESSION", "OPAQUE_SCHEMES",
    "StorageKey", "StorageArea", "StorageService",
    "is_opaque_origin", "normalize_origin",
]


#: Web Storage 规范的单源配额（5 MiB）。
QUOTA_BYTES_PER_ORIGIN = 5 * 1024 * 1024

#: 玩具实现的**全局**上限：所有分区加起来不得超过（LRU 驱逐触发点）。
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024

LOCAL = "local"
SESSION = "session"

#: 视为 opaque origin 的 scheme（无法参与同源判定的来源）。
OPAQUE_SCHEMES = frozenset({"data", "blob", "about", "file"})


class QuotaExceededError(Exception):
    """JS 侧 ``QuotaExceededError``（Web Storage 规范）。"""


# ---------------------------------------------------------------------------
# 宿主侧共享的 StorageService（第 5 点：分区的唯一持有者）
# ---------------------------------------------------------------------------

_DEFAULT_SERVICE: "StorageService | None" = None


def default_storage_service() -> "StorageService":
    """宿主机进程共享的那个 :class:`StorageService`（懒建单例）。

    所有 ``js.Host`` 共用它 ⇒ 同源（同 profile / 同 tab）的文档共享
    同一份分区，跨源互不看到（第 5 点）。
    """
    global _DEFAULT_SERVICE
    if _DEFAULT_SERVICE is None:
        _DEFAULT_SERVICE = StorageService()
    return _DEFAULT_SERVICE


def set_default_storage_service(svc: "StorageService | None") -> None:
    """替换（或置空）默认服务；测试用。"""
    global _DEFAULT_SERVICE
    _DEFAULT_SERVICE = svc


# ---------------------------------------------------------------------------
# origin 归一化与 opaque 判定
# ---------------------------------------------------------------------------

def normalize_origin(origin: str) -> str:
    """把 origin 归一化成小写、去尾斜杠的形式；无法解析则原样返回。"""
    s = str(origin or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low in ("null", "opaque"):
        return ""
    if low.startswith("file:"):
        return ""
    return s.rstrip("/")


def is_opaque_origin(origin: str) -> bool:
    """第 5 点 — 是否为 opaque origin。

    ``""`` / ``null`` / ``data:`` / ``blob:`` / ``about:`` / ``file:``
    都是 opaque：它们**没有**稳定的 origin，因此不能按 origin 共享
    存储——每个文档实例一份，且不跨导航保留。
    """
    n = normalize_origin(origin)
    if not n:
        return True
    scheme = n.split(":", 1)[0].lower() if ":" in n else ""
    return scheme in OPAQUE_SCHEMES


# ---------------------------------------------------------------------------
# StorageKey
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StorageKey:
    """一个分区的身份。

    * ``local``   → ``(kind, profile_id, origin)``（tab_id 不参与：
      localStorage 跨标签共享）；
    * ``session`` → ``(kind, tab_id, origin)``（换标签即换分区）；
    * opaque      → origin 用 ``opaque#<token>`` 表达（token 是文档
      实例标识），因此**既不跨源也不跨导航**共享。
    """
    kind: str
    origin: str
    profile_id: str = "default"
    tab_id: str = ""

    def scope(self) -> str:
        """分区作用域（审计用）：local 看 profile，session 看 tab。"""
        if self.kind == SESSION:
            return self.tab_id or "tab:unknown"
        return self.profile_id or "profile:default"

    def __str__(self) -> str:
        return f"{self.kind}@{self.scope()}/{self.origin or 'opaque'}"


# ---------------------------------------------------------------------------
# StorageArea — 一个分区
# ---------------------------------------------------------------------------

def _byte_len(s: str) -> int:
    return len(str(s).encode("utf-8"))


class StorageArea:
    """第 5/6 点 — 一个 origin 的一种 storage（local 或 session）。

    只暴露 Web Storage 规范的方法与只读属性；内部字典是**私有**的
    （名字改写 ``__store``，Python 层无法用 ``area._store`` 触达），
    需要一次性读取时用 :meth:`snapshot`（返回**副本**）。

    配额是**增量**维护的（``__used``），setItem 不再 O(n) 重算。
    """

    # 注意：``key`` 是 Web Storage 规范的方法名，不能同时当属性用，
    # 分区身份存在 ``_key`` 上并通过 :attr:`storage_key` 暴露。
    __slots__ = ("_key", "quota", "_StorageArea__store", "_StorageArea__used",
                 "_seq", "_on_change")

    def __init__(self, key: "StorageKey",
                 quota: int = QUOTA_BYTES_PER_ORIGIN,
                 on_change=None) -> None:
        self._key = key
        self.quota = int(quota)
        self.__store: dict = {}
        self.__used = 0
        self._seq = 0            # LRU：最后一次访问的序号（不读时钟）
        self._on_change = on_change

    # -- 只读视图 -----------------------------------------------------
    @property
    def storage_key(self) -> "StorageKey":
        """分区身份（``key`` 是规范的方法名，身份属性叫 ``storage_key``）。"""
        return self._key

    @property
    def used_bytes(self) -> int:
        return self.__used

    @property
    def length(self) -> int:
        return len(self.__store)

    @property
    def last_access(self) -> int:
        return self._seq

    def snapshot(self) -> dict:
        """内部键值对的**副本**（S01：不交出内部 dict 本身）。"""
        return dict(self.__store)

    # -- 规范方法 -----------------------------------------------------
    def get_item(self, key: str) -> Optional[str]:
        self._touch()
        v = self.__store.get(str(key))
        return None if v is None else str(v)

    def set_item(self, key: str, value: str) -> None:
        """第 6 点 — 失败原子化：先算增量，超配额**直接抛**，绝不
        留下半截状态（旧实现是先改 dict 再检查）。"""
        k, v = str(key), str(value)
        new_bytes = _byte_len(k) + _byte_len(v)
        old = self.__store.get(k)
        delta = new_bytes - (_byte_len(k) + _byte_len(old) if old is not None
                             else 0)
        projected = self.__used + delta
        if projected > self.quota:
            raise QuotaExceededError(
                f"{self.storage_key.kind}Storage quota exceeded "
                f"({projected} bytes > {self.quota}) for {self.storage_key}")
        self.__store[k] = v
        self.__used = projected
        self._touch()
        self._notify(delta)

    def remove_item(self, key: str) -> None:
        k = str(key)
        old = self.__store.pop(k, None)
        if old is not None:
            self.__used -= _byte_len(k) + _byte_len(old)
            if self.__used < 0:  # 防御：增量不该为负
                self.__used = 0
            self._touch()
            self._notify(-(_byte_len(k) + _byte_len(old)))

    def clear(self) -> None:
        if not self.__store:
            return
        delta = -self.__used
        self.__store.clear()
        self.__used = 0
        self._touch()
        self._notify(delta)

    def key(self, index: int) -> Optional[str]:
        self._touch()
        try:
            i = int(index)
        except (TypeError, ValueError):
            return None
        keys = list(self.__store.keys())
        return keys[i] if 0 <= i < len(keys) else None

    def keys(self) -> list:
        self._touch()
        return list(self.__store.keys())

    # -- 内部 ---------------------------------------------------------
    def _touch(self) -> None:
        self._seq += 1

    def _notify(self, delta: int) -> None:
        if self._on_change is not None:
            try:
                self._on_change(self, delta)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (f"StorageArea({self.storage_key}, {self.length} keys, "
                f"{self.__used}/{self.quota} bytes)")


# ---------------------------------------------------------------------------
# StorageService — 宿主侧的分区表
# ---------------------------------------------------------------------------

class StorageService:
    """第 5 点 — 分区的**唯一**持有者（宿主侧）。

    * ``local``   → ``(profile_id, origin)``：跨标签、跨同源导航保留；
    * ``session`` → ``(tab_id, origin)``：换标签即换分区，关标签即清；
    * opaque origin → ``opaque#<token>``：每文档一份，**不**跨导航保留。

    第 6 点 — 清理与驱逐：``clear_origin`` / ``clear_tab`` /
    ``clear_profile`` / ``clear_all``；全局字节超限时按 **LRU**（访问
    序号最小者）驱逐，驱逐不掉才让写入失败。
    """

    def __init__(self, max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                 quota_per_area: int = QUOTA_BYTES_PER_ORIGIN) -> None:
        self.max_total_bytes = int(max_total_bytes)
        self.quota_per_area = int(quota_per_area)
        self.__areas: dict = {}
        self.__counter = 0

    # -- 分区获取 -----------------------------------------------------
    def local_area(self, origin: str, profile_id: str = "default") -> StorageArea:
        return self._area(LOCAL, origin, profile_id=profile_id)

    def session_area(self, origin: str, tab_id: str = "") -> StorageArea:
        return self._area(SESSION, origin, tab_id=tab_id)

    def _area(self, kind: str, origin: str, profile_id: str = "default",
              tab_id: str = "", opaque_token: str = "") -> StorageArea:
        norm = normalize_origin(origin)
        if is_opaque_origin(norm):
            # 第 5 点 — opaque：用 per-document token 隔离，且不跨导航
            # 保留（调用方换 token 就是新的一份）。
            norm = "opaque#" + (str(opaque_token) or self._new_token())
        # 第 5 点 — localStorage 的作用域是 profile（**不**含 tab），
        # sessionStorage 的作用域是 tab（**不**含 profile）。把不参与
        # 的那个字段清掉，否则同一分区会被拆成多份（换标签就丢
        # localStorage 是错的）。
        key = StorageKey(
            kind=kind, origin=norm,
            profile_id=(profile_id or "default") if kind != SESSION else "",
            tab_id=(tab_id or "") if kind != LOCAL else "",
        )
        area = self.__areas.get(key)
        if area is None:
            area = StorageArea(key, self.quota_per_area,
                               on_change=self._on_area_change)
            self.__areas[key] = area
        area._touch()
        return area

    def _new_token(self) -> str:
        self.__counter += 1
        return f"doc{self.__counter}"

    # -- 便捷入口（给宿主：一次拿到 local + session） -----------------
    def areas_for_document(self, origin: str, profile_id: str = "default",
                           tab_id: str = "", opaque_token: str = ""
                           ) -> "tuple[StorageArea, StorageArea]":
        """返回 ``(localStorage, sessionStorage)`` 两个分区。"""
        return (self._area(LOCAL, origin, profile_id=profile_id,
                           tab_id=tab_id, opaque_token=opaque_token),
                self._area(SESSION, origin, profile_id=profile_id,
                           tab_id=tab_id, opaque_token=opaque_token))

    # -- 清理 / 驱逐（第 6 点） ---------------------------------------
    @property
    def total_bytes(self) -> int:
        return sum(a.used_bytes for a in self.__areas.values())

    @property
    def area_count(self) -> int:
        return len(self.__areas)

    def _on_area_change(self, _area: StorageArea, _delta: int) -> None:
        if self.total_bytes > self.max_total_bytes:
            self.evict_lru()

    def evict_lru(self, keep: int = 0) -> int:
        """按 LRU 驱逐最久未访问的分区，直到总量回到上限内。

        返回被驱逐的分区数。``keep`` 保留最近访问的前 N 个不动。
        """
        removed = 0
        while self.total_bytes > self.max_total_bytes:
            areas = sorted(self.__areas.values(),
                           key=lambda a: a.last_access)
            if len(areas) <= keep or not areas:
                break
            victim = areas[0]
            self.__areas.pop(victim.storage_key, None)
            removed += 1
            if not self.__areas:
                break
        return removed

    def clear_origin(self, origin: str) -> int:
        """清理某个 origin 的**全部**分区（local + session）。"""
        norm = normalize_origin(origin)
        hit = [k for k in self.__areas if k.origin == norm]
        for k in hit:
            self.__areas.pop(k, None)
        return len(hit)

    def clear_tab(self, tab_id: str) -> int:
        """关标签：清掉该 tab 的 session 分区（local 不动）。"""
        hit = [k for k in self.__areas
               if k.kind == SESSION and k.tab_id == tab_id]
        for k in hit:
            self.__areas.pop(k, None)
        return len(hit)

    def clear_profile(self, profile_id: str) -> int:
        """清掉某个 profile 的 local 分区（session 不动）。"""
        hit = [k for k in self.__areas
               if k.kind == LOCAL and k.profile_id == profile_id]
        for k in hit:
            self.__areas.pop(k, None)
        return len(hit)

    def clear_opaque(self) -> int:
        """清掉所有 opaque 分区（opaque 不跨导航保留，导航后调用）。"""
        hit = [k for k in self.__areas if k.origin.startswith("opaque#")]
        for k in hit:
            self.__areas.pop(k, None)
        return len(hit)

    def clear_all(self) -> int:
        n = len(self.__areas)
        self.__areas.clear()
        return n

    # -- 可观测 -------------------------------------------------------
    def describe(self) -> str:
        parts = []
        for k, a in sorted(self.__areas.items(),
                           key=lambda kv: str(kv[0])):
            parts.append(f"{k}={a.length}k/{a.used_bytes}B")
        return (f"StorageService({len(self.__areas)} areas, "
                f"{self.total_bytes}/{self.max_total_bytes} bytes: "
                + ", ".join(parts) + ")")

    def __repr__(self) -> str:
        return self.describe()
