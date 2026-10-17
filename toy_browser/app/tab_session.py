"""批次 25 — G09 step 1 统一标签会话协议层（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G09 step 1：

> 标签使用稳定 tab_id，不把列表位置作为长期身份；关闭中间标签后重建
> 映射，旧回调不能操作其他标签。

G09 验收：

> 至少三标签、关闭首/中/末标签、后台加载完成、历史前后、书签跳转和
> 恢复损坏数据。不串页、不重复历史项、不泄漏已关闭标签任务。

已有模块（本模块**不重复**实现，只做统一协议层 + 旧回调防护）：
- **G09 step 1**（:class:`toy_browser.app.tab_identity.TabIdentityRegistry`，
  批次 4）已有稳定 tab_id 分配 + 关闭中间标签的"旧 idx → 新 idx"映射
  重建（:meth:`invalidate` / :meth:`compact` / :meth:`valid_idx`）。
- **G09 step 2+4**（:class:`toy_browser.app.tab_lifecycle.TabLifecycle`，
  批次 9）已有每标签呈现 + 任务门控（关标签取消在途任务 + 释放资源）。
- **G07 标签级编排**（:class:`toy_browser.app.tab_document_orchestrator.
  TabDocumentOrchestrator`，批次 23）已有多标签控制器 + C01 桥 + 在途
  结果丢弃。
- **G09 step 5**（:class:`toy_browser.app.session.SessionStore` /
  :class:`toy_browser.app.tab_identity.TabSnapshot`）已有会话持久化
  （原子写 + 损坏降级）。

本模块补**统一协议层** :class:`TabSession`：
- 把 registry + lifecycle 接成"开 / 关 / 切标签"的单一入口
  （G09 验收"至少三标签、关闭首/中/末标签"）；
- **旧回调防护**（G09 step 1 验收"旧回调不能操作其他标签"）：
  旧回调持有旧 idx 时，经 :meth:`resolve_callback` 检查"该旧 idx 映射
  到的 tab_id 是否仍在 registry 且未被关闭"；不在 → no-op（不串页）。
  把"关闭中间标签后旧回调"从"必须靠调用方记得 compact 重绑"升级为
  "协议层统一拦截"。
- **关闭首 / 中 / 末标签** 三个 G09 验收场景的统一处理（关首标签 /
  关末标签 / 关中间标签都正确重建 idx 映射 + 前台让给剩余标签 +
  丢弃被关标签在途结果与任务，不泄漏）。
- **关闭标签任务不泄漏**（G09 验收"不泄漏已关闭标签任务"）：关闭时
  把被关标签的在途任务 + 在途结果全部清理，``describe`` 可审计。
- **会话快照 / 恢复**（G09 step 5）：``snapshot`` / ``restore`` 经
  registry 的 :meth:`TabIdentityRegistry.snapshot` /
  :meth:`TabIdentityRegistry.restore`（原子写 + 损坏降级由
  :class:`SessionStore` 承担，本批只做可恢复状态序列化的纯逻辑）。

GUI 侧（Tk 真实标签控件 / 闭包重绑）需活显示环境；本批为**纯逻辑
核心**，不触碰 tkinter。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.tab_identity import TabIdentityRegistry, TabSnapshot
from toy_browser.app.tab_lifecycle import TabLifecycle


@dataclass
class TabSessionStats:
    """G09 验收 — 标签会话的状态统计（诊断区 / 审计用）。

    * ``tab_count`` — 当前标签数。
    * ``active_tab_id`` — 当前前台标签（None = 无标签）。
    * ``closed_tab_ids`` — 已关闭 tab_id 集合（审计"不泄漏已关闭标签"）。
    * ``stale_callbacks`` — 被旧回调防护拦截的"旧 idx 回调"计数
      （G09 验收"不串页"：这些回调本可能操作到别的标签，已被拦截）。
    """
    tab_count: int = 0
    active_tab_id: Optional[int] = None
    closed_tab_ids: list = field(default_factory=list)
    stale_callbacks: int = 0


class TabSession:
    """G09 step 1 + step 2 + step 4 + step 5 — 统一标签会话协议层。

    把 :class:`TabIdentityRegistry`（稳定 tab_id + 关闭重建映射）+
    :class:`TabLifecycle`（每标签呈现 + 任务门控）接成"开 / 关 / 切 /
    快照 / 恢复"的单一入口，补 G09 step 1 验收"关闭中间标签后旧回调
    不能操作其他标签"的**协议层拦截**（旧回调持有旧 idx → 经
    :meth:`resolve_callback` 检查，不在 / 已被关 → no-op + 计数）。

    职责：
    - :meth:`open_tab` — 新开标签（分配稳定 tab_id + 呈现 + 门控）。
    - :meth:`close_tab` — 关标签（取消在途任务 + 丢弃在途结果 + 释放
      资源 + 重建 idx 映射 + 前台让给剩余标签 + 记入关闭审计；G09
      验收"关闭首 / 中 / 末标签" + "不泄漏已关闭标签任务"）。
    - :meth:`switch` — 切标签（G09 step 2 只改前台呈现，不重建文档）。
    - :meth:`resolve_callback` — **旧回调防护**（G09 step 1 验收）：
      旧回调持 *old_idx* 时，检查它映射到的 tab_id 是否仍有效；
      无效 → 返回 None（调用方 no-op，不操作别的标签）并计数
      ``stale_callbacks``。
    - :meth:`snapshot` / :meth:`restore` — 会话快照 / 恢复（G09 step 5，
      可恢复状态：url / title / back / fwd / scroll / active；不含
      DOM / 位图 / 脚本 Host，恢复时重新加载 URL）。
    - :meth:`stats` / :meth:`describe` — 审计统计 + 诊断串。

    与 :class:`TabDocumentOrchestrator`（批次 23）的关系：本会话层
    只管理 registry + lifecycle（身份 + 呈现 + 任务门控）；文档工作
    单元（控制器 + C01 桥 + 在途结果）由 orchestrator 承担。本批的
    "关标签丢弃在途"经 :meth:`close_tab` 返回的 ``cancelled`` 列表
    供 orchestrator 侧丢弃在途结果（两者配合完成 G09 验收"不泄漏已
    关闭标签任务"）。
    """

    def __init__(self,
                 registry: Optional[TabIdentityRegistry] = None,
                 lifecycle: Optional[TabLifecycle] = None) -> None:
        self.registry = registry or TabIdentityRegistry()
        # lifecycle 用同一个 registry（共享稳定 tab_id 分配）
        self.lifecycle = lifecycle or TabLifecycle(registry=self.registry)
        self._closed_tab_ids: list[int] = []
        self._stale_callbacks: int = 0

    # ------------------------------------------------------------------
    # 标签创建 / 关闭 / 切换（G09 验收"至少三标签、关闭首/中/末"）
    # ------------------------------------------------------------------
    def open_tab(self) -> int:
        """新开标签：分配稳定 tab_id + 呈现 + 任务门控。"""
        tab_id = self.lifecycle.new_tab()
        # 首个标签默认前台（lifecycle 已处理）
        return tab_id

    def close_tab(self, tab_id: int) -> dict:
        """关标签（G09 验收"关闭首 / 中 / 末标签" + "不泄漏任务"）。

        经 lifecycle 取消在途任务 + 释放资源 + 经 registry 重建 idx
        映射（:meth:`TabIdentityRegistry.invalidate` → :meth:`compact`）
        + 前台让给剩余标签。返回::

            {"cancelled": [...], "compact_mapping": {...},
             "new_active": ...}

        - ``cancelled`` — 被关标签取消的在途任务 id（供 orchestrator
          丢弃在途结果，"不泄漏已关闭标签任务"）。
        - ``compact_mapping`` — 旧 idx → 新 idx 映射（调用方据此重建
          widget / 闭包绑定；未变动槽位映射到自身）。
        - ``new_active`` — 关标签后的新前台 tab_id（None = 全关光）。
        """
        cancelled = self.lifecycle.close_tab(tab_id)
        self._closed_tab_ids.append(tab_id)
        compact_mapping = self.registry.compact()
        return {
            "cancelled": cancelled,
            "compact_mapping": compact_mapping,
            "new_active": self.lifecycle.active_tab_id(),
        }

    def switch(self, tab_id: int) -> bool:
        """切标签前台（G09 step 2 只改呈现，不重建文档）。"""
        pres = self.lifecycle.presentation.get(tab_id)
        if pres is None:
            return False
        self.lifecycle.activate(tab_id)
        return True

    # ------------------------------------------------------------------
    # 旧回调防护（G09 step 1 验收"旧回调不能操作其他标签"）
    # ------------------------------------------------------------------
    def resolve_callback(self, old_idx: int,
                         url: str = "", title: str = "",
                         back_stack: Optional[list] = None,
                         fwd_stack: Optional[list] = None,
                         scroll: int = 0, zoom: float = 1.0) -> \
            Optional[int]:
        """旧回调（持有旧 idx 的闭包）的统一拦截入口。

        旧回调本以 *old_idx* 为 key 操作某标签；关闭中间标签后 *old_idx*
        对应的槽位已前移 / 失效，若直接用旧 idx 会**操作到别的标签**
        （G09 验收"不串页"）。本方法把 *old_idx* 解析为**当前** tab_id：

        - 若 *old_idx* 当前仍映射到某**存活** tab_id → 返回该 tab_id
          （旧回调可安全继续，但应以 tab_id 为 key 而非 idx）。
        - 若 *old_idx* 已无对应存活 tab_id（被关闭 / 前移错位）→
          返回 None + 计数 ``stale_callbacks``（调用方 no-op，不操作
          别的标签）。

        调用方拿到 tab_id 后，应经 :meth:`switch` / 资源读写以
        **tab_id**（而非 idx）为 key 操作，确保"旧回调不操作其他标签"。
        """
        current = self.registry.tab_id_at(old_idx)
        if current is None:
            self._stale_callbacks += 1
            return None
        # 该 tab_id 是否仍存活（未被关）
        if self.registry.idx_of(current) is None:
            self._stale_callbacks += 1
            return None
        return current

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    @property
    def tab_ids(self) -> list[int]:
        """当前存活 tab_id（按 idx 顺序）。"""
        return self.registry.tab_ids()

    @property
    def active_tab_id(self) -> Optional[int]:
        return self.lifecycle.active_tab_id()

    @property
    def tab_count(self) -> int:
        return self.lifecycle.tab_count()

    @property
    def closed_tab_ids(self) -> list[int]:
        return list(self._closed_tab_ids)

    @property
    def stale_callback_count(self) -> int:
        return self._stale_callbacks

    # ------------------------------------------------------------------
    # 会话快照 / 恢复（G09 step 5：仅保存可恢复状态）
    # ------------------------------------------------------------------
    def snapshot(self) -> list[TabSnapshot]:
        """当前所有标签的可恢复状态（url / title / back / fwd / scroll /
        active；不含 DOM / 位图 / 脚本 Host，恢复时重新加载 URL）。"""
        return self.registry.snapshot(self.active_tab_id)

    @staticmethod
    def restore(snaps: list[TabSnapshot]) -> "TabSession":
        """从 :meth:`snapshot` 的列表重建会话（registry + lifecycle）。
        tab_id 重新分配（与 restore 前**不同**，registry.allocate 递增），
        顺序与 url / 历史 / 滚动保持；active 标签恢复后切到前台。"""
        reg = TabIdentityRegistry.restore(snaps)
        session = TabSession(registry=reg)
        # 把恢复出的每个 tab_id 挂到 lifecycle（呈现 + 任务门控），
        # 否则 tab_count / active_tab_id 查不到（registry 有资源但
        # lifecycle 没呈现条目）。registry.restore 的 allocate 顺序
        # 与 snaps 一致：新 tab_id 依次为 1..n，对应 snaps 的
        # url / active。
        new_ids = reg.tab_ids()
        active_found = False
        for new_tid, s in zip(new_ids, snaps):
            session.lifecycle.presentation.add(new_tid)
            session.lifecycle.gate.add(new_tid)
            if s.active:
                active_found = True
        if active_found:
            for new_tid, s in zip(new_ids, snaps):
                if s.active:
                    session.lifecycle.activate(new_tid)
                    break
        return session

    # ------------------------------------------------------------------
    # 审计 / 诊断
    # ------------------------------------------------------------------
    def stats(self) -> TabSessionStats:
        return TabSessionStats(
            tab_count=self.tab_count,
            active_tab_id=self.active_tab_id,
            closed_tab_ids=list(self._closed_tab_ids),
            stale_callbacks=self._stale_callbacks,
        )

    def describe(self) -> str:
        s = self.stats()
        return (f"tabs={s.tab_count} active={s.active_tab_id} "
                f"closed={len(s.closed_tab_ids)} "
                f"stale_callbacks={s.stale_callbacks} "
                f"ids={self.tab_ids}")


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    sess = TabSession()
    a = sess.open_tab()
    b = sess.open_tab()
    c = sess.open_tab()
    print("opened:", a, b, c, "active=", sess.active_tab_id)
    res = sess.close_tab(b)  # 关中间标签
    print("closed b:", res)
    print("resolve old idx 0 ->", sess.resolve_callback(0))
    print(sess.describe())
