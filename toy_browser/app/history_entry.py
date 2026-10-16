"""G09 step 3 — 统一历史提交入口（纯逻辑核心）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G09 step 3：

> 统一地址栏、链接、书签和文件打开的历史提交路径，明确重定向只
> 形成哪一个历史项。

本模块把**所有**导航来源（地址栏 Go / 链接点击 / 书签跳转 / 文件
打开 / 前进后退）的历史提交收敛到 :class:`HistoryCommitter` 的
**唯一入口** :meth:`HistoryCommitter.commit`，并明确重定向规则：

* **一次导航 = 恰好一个历史项**，携带 *最终* URL（重定向链走完
  之后落到的 URL）。中间 3xx 跳转**不**产生独立历史项（G09 step 3
  "明确重定向只形成哪一个历史项"）。
* 前进 / 后退是**移动游标**（:meth:`HistoryCommitter.move`），
  不新增历史项；移动后前进栈清空（标准浏览器语义，避免历史分叉）。
* 同一来源反复提交不会重复插入（去重）。

与 G08 / C01 的关系：本入口只负责**历史栈的形态**（哪个 URL 入栈、
顺序、游标位置）；每个历史项携带的 C01 版本 / 导航 epoch 由调用方
（``DocumentController.navigate``）负责。GUI / CLI / 测试都接同一
入口，保证"不串页、不重复历史项"（G09 验收）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 导航来源（历史项的 source 标签；诊断区 / 审计可据此区分）
SRC_ADDRESS = "address"        # 地址栏 Go
SRC_LINK = "link"              # 链接点击
SRC_BOOKMARK = "bookmark"     # 书签跳转
SRC_FILE = "file"             # 文件打开
SRC_BACK = "back"             # 后退（游标移动，非新增）
SRC_FORWARD = "forward"       # 前进（游标移动，非新增）


@dataclass
class HistoryEntry:
    """一个历史项。

    * ``url`` — 该导航项的 URL（**最终** URL：重定向链走完落到的
      地址；中间跳转不出现在历史里）。
    * ``source`` — 来源标签（见 ``SRC_*`` 常量；back/forward 的
      游标移动用 ``SRC_BACK`` / ``SRC_FORWARD``）。
    * ``start_url`` — 请求发起时的 URL（重定向前）；非重定向时
      等于 *url*。携带它便于诊断区记录重定向链，但**不**为每个
      中间跳转建历史项。
    """
    url: str
    source: str
    start_url: str = ""

    def __post_init__(self) -> None:
        if not self.start_url:
            self.start_url = self.url


class HistoryCommitter:
    """G09 step 3 — 统一历史提交入口（单一入口 + 游标 + 去重）。

    状态（游标模型，等价于 PageInstance 的 back_stack / fwd_stack，
    但用"entries + cursor"表达，语义更清晰、便于统一来源提交）：

    * ``entries`` — 完整历史序列（entries[0] 是首项）。
    * ``cursor`` — 当前游标索引（指向"当前页面"在 entries 里的
      位置）；首项时 cursor=0。

    操作：

    * :meth:`commit` — 一次**新导航**（地址栏 / 链接 / 书签 / 文件
      打开）：把 *url*（重定向后最终 URL）作为**一个**历史项追加到
      cursor 之后，清空其后的前进栈；同址去重。返回该历史项的索引。
    * :meth:`back` / :meth:`forward` — 游标移动（不新增项）；到头
      时返回 None。
    * :meth:`current` — 当前页面的 URL。
    * :meth:`can_back` / :meth:`can_forward` — 工具栏按钮启停依据。
    """

    def __init__(self, initial_url: str = "") -> None:
        self._entries: list[HistoryEntry] = []
        self._cursor: int = -1
        if initial_url:
            self._entries.append(HistoryEntry(initial_url, SRC_ADDRESS,
                                              initial_url))
            self._cursor = 0

    # ------------------------------------------------------------------
    # 统一提交入口（G09 step 3 单一入口）
    # ------------------------------------------------------------------
    def commit(self, url: str, source: str = SRC_ADDRESS,
               final_url: Optional[str] = None,
               dedup: bool = True) -> int:
        """提交**一次新导航**到一个历史项。

        * *url* — 请求发起的 URL（重定向前）。
        * *final_url* — 重定向链走完落到的最终 URL；None 表示本次
          无重定向（*final_url* = *url*）。**只**为最终 URL 建一个
          历史项（G09 step 3：重定向只形成一个历史项）。
        * *source* — 来源标签。
        * *dedup* — 若 cursor 当前指向的项 URL 与本次最终 URL 相同
          （例如重定向回到当前页 / 同址重复提交），不新增历史项，
          只原地更新 source / 最终 URL（返回现有索引）。

        返回该历史项在 ``entries`` 里的索引。
        """
        final = final_url if final_url else url
        # 同址去重：当前项 URL 已等于最终 URL → 不新增
        if dedup and self._cursor >= 0 and \
                self._entries[self._cursor].url == final:
            ent = self._entries[self._cursor]
            ent.url = final
            ent.source = source
            ent.start_url = url
            return self._cursor
        # 新导航：清空 cursor 之后的前进栈（防止历史分叉）
        del self._entries[self._cursor + 1:]
        self._entries.append(HistoryEntry(final, source, url))
        self._cursor += 1
        return self._cursor

    # ------------------------------------------------------------------
    # 游标移动（不新增项）
    # ------------------------------------------------------------------
    def back(self) -> Optional[HistoryEntry]:
        if self._cursor > 0:
            self._cursor -= 1
            return self._entries[self._cursor]
        return None

    def forward(self) -> Optional[HistoryEntry]:
        if self._cursor < len(self._entries) - 1:
            self._cursor += 1
            return self._entries[self._cursor]
        return None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def current(self) -> str:
        """当前页面的 URL（无历史 → 空串）。"""
        if 0 <= self._cursor < len(self._entries):
            return self._entries[self._cursor].url
        return ""

    def can_back(self) -> bool:
        return self._cursor > 0

    def can_forward(self) -> bool:
        return self._cursor < len(self._entries) - 1

    @property
    def entries(self) -> list[HistoryEntry]:
        """只读视图：完整历史序列（诊断区 / 会话持久化用）。"""
        return list(self._entries)

    @property
    def cursor(self) -> int:
        return self._cursor

    def __len__(self) -> int:
        return len(self._entries)
