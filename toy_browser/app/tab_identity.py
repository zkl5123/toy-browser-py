"""G09 — 稳定标签身份（tab_id 与列表位置解耦）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G09 step 1：

标签使用**稳定 tab_id**，不把列表位置（idx）作为长期身份。关闭
中间标签后需要重建映射，旧的回调（滚动条 command、滚轮绑定、
键盘焦点路由等以 idx 为 key 的闭包）不能操作到"别的标签"——
因为关闭中间标签后，后面的标签 idx 全部前移，若闭包仍持有旧 idx
就会写错页。

本模块提供：

* :class:`TabIdentityRegistry` — tab_id（递增整数）↔ idx 的映射；
  关闭某 idx 后**保留映射、失效旧 idx**（旧回调命中失效条目直接
  no-op），并为后续标签保留稳定 tab_id。
* 每标签可挂"每标签资源"（DocumentController、历史栈、滚动 / 焦点
  状态、加载任务，G09 step 2）——切换标签只改变前台呈现，不重建
  文档（资源以 tab_id 为 key 存于 registry）。
* 会话快照（G09 step 5）：``snapshot()`` / ``restore()`` 只保存
  **可恢复状态**（url / title / back / fwd / scroll / active），交给
  :class:`SessionStore` 原子写。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TabSnapshot:
    """G09 step 5 — 一个标签的可恢复状态（不含 DOM / 位图等大对象；
    恢复时重新加载 URL）。"""
    tab_id: int
    url: str
    title: str
    back_stack: list = field(default_factory=list)
    fwd_stack: list = field(default_factory=list)
    scroll: int = 0
    zoom: float = 1.0
    active: bool = False


class TabIdentityRegistry:
    """G09 step 1 — 稳定 tab_id ↔ 可变 idx 的映射。

    * ``allocate()`` — 新建标签时分配下一个稳定 tab_id（关闭中间
      标签后不复用旧 tab_id，避免旧资源 / 回调串页）。
    * ``idx_of(tab_id)`` / ``tab_id_at(idx)`` — 双向查询。
    * ``invalidate(idx)`` — 关闭 idx 时把该槽的 tab_id 标记失效；
      旧回调（持有旧 idx 的闭包）经 :meth:`valid_idx` 检查后 no-op。
    * ``compact()`` — 关闭中间标签后重排：剩余标签按现有顺序
      重新填 0..n-1 的 idx，tab_id 不变；返回 "旧 idx → 新 idx"
      映射供调用方重建 widget 绑定。
    """

    def __init__(self) -> None:
        self._next_tab_id = itertools.count(1)
        # idx -> tab_id（当前有效的槽位；compact 后重建）
        self._idx_to_tab: dict[int, int] = {}
        # tab_id -> 每标签资源（DocumentController / 历史 / 滚动 / 焦点 / 任务）
        self._resources: dict[int, dict] = {}
        # 失效的 tab_id（关闭后不再被复用）
        self._dead_tab_ids: set[int] = set()
        # 最近一次 invalidate 前捕获的旧 idx 快照 + 旧→新映射
        self._last_compact: dict = {}
        self._pre_close_pairs: list = []

    # ------------------------------------------------------------------
    # 分配 / 查询
    # ------------------------------------------------------------------
    def allocate(self) -> int:
        """G09 — 新建标签：分配稳定 tab_id，放在当前末尾。"""
        tab_id = next(self._next_tab_id)
        idx = len(self._idx_to_tab)
        self._idx_to_tab[idx] = tab_id
        self._resources[tab_id] = {}
        return tab_id

    def idx_of(self, tab_id: int) -> Optional[int]:
        """tab_id → 当前 idx；已关闭（死 tab_id）返回 None。"""
        if tab_id in self._dead_tab_ids:
            return None
        for idx, tid in self._idx_to_tab.items():
            if tid == tab_id:
                return idx
        return None

    def tab_id_at(self, idx: int) -> Optional[int]:
        return self._idx_to_tab.get(idx)

    def __len__(self) -> int:
        return len(self._idx_to_tab)

    def tab_ids(self) -> list[int]:
        """按 idx 顺序列出当前 tab_id。"""
        return [tid for _, tid in sorted(self._idx_to_tab.items())]

    # ------------------------------------------------------------------
    # 每标签资源（G09 step 2：切换只改前台，不重建文档）
    # ------------------------------------------------------------------
    def set_resource(self, tab_id: int, key: str, value) -> None:
        res = self._resources.get(tab_id)
        if res is not None:
            res[key] = value

    def get_resource(self, tab_id: int, key: str, default=None):
        res = self._resources.get(tab_id)
        if res is None:
            return default
        return res.get(key, default)

    def release_tab(self, tab_id: int) -> None:
        """G09 step 4 — 关闭标签：释放每标签资源 + 标记 tab_id 失效
        （旧资源不再被串页复用）。"""
        self._resources.pop(tab_id, None)
        self._dead_tab_ids.add(tab_id)

    # ------------------------------------------------------------------
    # 关闭 + 重建映射（G09 step 1）
    # ------------------------------------------------------------------
    def invalidate(self, idx: int) -> int:
        """关闭 idx：捕获"关闭前"的旧 idx 快照，从槽位移除并返回
        被关闭的 tab_id。

        关闭后旧 idx 已失效（后续 idx 全部前移），旧回调（持有旧 idx
        的闭包）通过 :meth:`compact` 返回的 *关闭前 → 关闭后* 映射重绑，
        或经 :meth:`valid_idx` 检查后 no-op（不操作别的标签）。"""
        # 捕获关闭前的 (idx, tab_id) 列表，供 compact() 出旧→新映射
        self._pre_close_pairs = sorted(self._idx_to_tab.items())
        tab_id = self._idx_to_tab.pop(idx, None)
        # 重排后续槽位（保持 tab_id 不变、idx 连续）
        self._reindex()
        if tab_id is not None:
            self.release_tab(tab_id)
            self._last_compact = self._build_compact_mapping()
        return tab_id if tab_id is not None else -1

    def _build_compact_mapping(self) -> dict:
        """由关闭前快照算 旧 idx → 新 idx（未变动的槽位映射到自身）。"""
        # 关闭前：[(idx, tab_id), ...] 按 idx 升序
        # 关闭后：tab_id 不变，按当前 idx 顺序
        current_tab_ids = [tid for _, tid in sorted(self._idx_to_tab.items())]
        mapping: dict[int, int] = {}
        for old_idx, tid in self._pre_close_pairs:
            if tid in self._dead_tab_ids:
                continue  # 被关闭的 tab 本身不映射
            if tid in current_tab_ids:
                mapping[old_idx] = current_tab_ids.index(tid)
        return mapping

    def _reindex(self) -> dict:
        """按现有顺序重建 0..n-1 的 idx 槽位；返回 旧idx→新idx。

        旧回调（持有旧 idx 的闭包）应在关闭后通过 :meth:`compact`
        返回的映射重绑，或直接 no-op（槽位已失效）。"""
        old_pairs = sorted(self._idx_to_tab.items())
        mapping: dict[int, int] = {}
        new: dict[int, int] = {}
        for new_idx, (_, tab_id) in enumerate(old_pairs):
            new[new_idx] = tab_id
        for old_idx, (_, tab_id) in enumerate(old_pairs):
            # 找 tab_id 的新 idx
            for nidx, tid in new.items():
                if tid == tab_id:
                    mapping[old_idx] = nidx
                    break
        self._idx_to_tab = new
        return mapping

    def compact(self) -> dict:
        """关闭中间标签后调用：返回最近一次 invalidate 捕获的
        "关闭前旧 idx → 关闭后新 idx" 映射，调用方据此重建
        widget / 闭包绑定；未变动的槽位映射到自身。

        没有进行过 invalidate 时返回空 dict（无映射需求）。"""
        return dict(self._last_compact)

    def valid_idx(self, idx: int) -> bool:
        """旧回调入口：该 idx 当前是否仍有效（否则 no-op，不操作
        别的标签）。"""
        return idx in self._idx_to_tab

    # ------------------------------------------------------------------
    # 会话快照（G09 step 5：仅保存可恢复状态）
    # ------------------------------------------------------------------
    def snapshot(self, active_tab_id: Optional[int]) -> list[TabSnapshot]:
        """当前所有标签的可恢复状态（不含 DOM / 位图 / 脚本 Host）。"""
        snaps: list[TabSnapshot] = []
        for idx, tid in sorted(self._idx_to_tab.items()):
            res = self._resources.get(tid, {})
            snaps.append(TabSnapshot(
                tab_id=tid,
                url=res.get("url", ""),
                title=res.get("title", ""),
                back_stack=list(res.get("back_stack", [])),
                fwd_stack=list(res.get("fwd_stack", [])),
                scroll=int(res.get("scroll", 0)),
                zoom=float(res.get("zoom", 1.0)),
                active=(tid == active_tab_id),
            ))
        return snaps

    @staticmethod
    def restore(snaps: list[TabSnapshot]) -> "TabIdentityRegistry":
        """从 :meth:`snapshot` 的列表重建 registry（tab_id 重新分配，
        顺序与 url / 历史 / 滚动保持）。"""
        reg = TabIdentityRegistry()
        for s in snaps:
            tid = reg.allocate()
            reg.set_resource(tid, "url", s.url)
            reg.set_resource(tid, "title", s.title)
            reg.set_resource(tid, "back_stack", list(s.back_stack))
            reg.set_resource(tid, "fwd_stack", list(s.fwd_stack))
            reg.set_resource(tid, "scroll", s.scroll)
            reg.set_resource(tid, "zoom", s.zoom)
        return reg
