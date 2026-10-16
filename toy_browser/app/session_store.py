"""G09 step 5 — 会话持久化：可恢复标签快照 + 原子写 + 损坏降级。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G09：

5. 会话持久化**仅保存可恢复状态**（url / title / back / fwd /
   scroll / zoom / active），用 :mod:`toy_browser.app.session`
   的原子写（``<path>.tmp`` + ``os.replace``，崩溃中途不会写坏
   文件）；损坏文件可降级打开（不抛异常，回退到空会话或
   上一份有效快照）。

文件格式：单个 JSON 对象 ``{"version": 1, "active_tab_id": int |
null, "tabs": [TabSnapshot...]}``。每个 Tab 字段缺失 / 类型错误
时逐字段降级（默认值），整个文件不可解析时降级为空会话（一个
about:blank）。
"""

from __future__ import annotations

import json
import os
from typing import Optional

from toy_browser.app.tab_identity import TabSnapshot

DEFAULT_SESSION_PATH = ".toy_session.json"
_SESSION_VERSION = 1


def _load_json_obj(path: str) -> Optional[dict]:
    """读一个 JSON 对象；任何失败返回 None（降级，不抛）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _as_str(v, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _as_str_list(v) -> list:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str)]
    return []


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default: float = 1.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class SessionStore:
    """G09 step 5 — 会话快照的持久化（原子写 + 损坏降级）。

    * :meth:`save` — 把一份 :class:`TabSnapshot` 列表写入磁盘
      （``<path>.tmp`` + ``os.replace``；``in_memory_only`` 跳过）。
    * :meth:`load` — 读回一份 (snapshots, active_tab_id)；损坏 /
      缺失时降级为空会话。
    * :meth:`has_session` — 磁盘上是否存在可读会话。
    """

    def __init__(self, path: str = DEFAULT_SESSION_PATH,
                 in_memory_only: bool = False) -> None:
        self.path = path
        self.in_memory_only = in_memory_only

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def save(self, snaps: list[TabSnapshot],
             active_tab_id: Optional[int]) -> None:
        """G09 — 会话持久化仅保存可恢复状态（快照列表 + 活动标签）。"""
        if self.in_memory_only:
            return
        data = {
            "version": _SESSION_VERSION,
            "active_tab_id": active_tab_id,
            "tabs": [
                {
                    "tab_id": s.tab_id,
                    "url": s.url,
                    "title": s.title,
                    "back_stack": list(s.back_stack),
                    "fwd_stack": list(s.fwd_stack),
                    "scroll": s.scroll,
                    "zoom": s.zoom,
                    "active": s.active,
                }
                for s in snaps
            ],
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    # 加载（损坏可降级）
    # ------------------------------------------------------------------
    def load(self) -> tuple[list[TabSnapshot], Optional[int]]:
        """读回 (snapshots, active_tab_id)。

        降级策略（G09 step 5：损坏文件可降级打开）：

        * 文件不存在 / 非对象 / 非预期版本 → 空会话。
        * ``tabs`` 缺失 / 非 list → 空会话。
        * 单个 tab 字段缺失 / 类型错 → 逐字段默认值，不整份丢弃。
        * 活动标签指向已不存在的 tab_id → 降级为第一个标签（或
          None，空会话时）。
        """
        raw = None if self.in_memory_only else _load_json_obj(self.path)
        if raw is None:
            return [], None
        if raw.get("version", _SESSION_VERSION) != _SESSION_VERSION:
            # 未知版本：保守降级为空会话（不猜结构）
            return [], None
        active = raw.get("active_tab_id")
        active_tab_id = _as_int(active) if active is not None else None
        tabs_raw = raw.get("tabs")
        if not isinstance(tabs_raw, list):
            return [], None
        snaps: list[TabSnapshot] = []
        valid_ids: set[int] = set()
        for t in tabs_raw:
            if not isinstance(t, dict):
                continue
            tid = _as_int(t.get("tab_id"), 0)
            url = _as_str(t.get("url"))
            if not url:
                # 无 url 的快照不可恢复：跳过
                continue
            snap = TabSnapshot(
                tab_id=tid,
                url=url,
                title=_as_str(t.get("title")),
                back_stack=_as_str_list(t.get("back_stack")),
                fwd_stack=_as_str_list(t.get("fwd_stack")),
                scroll=_as_int(t.get("scroll")),
                zoom=_as_float(t.get("zoom")),
                active=bool(t.get("active", False)),
            )
            snaps.append(snap)
            valid_ids.add(tid)
        if active_tab_id is not None and active_tab_id not in valid_ids:
            active_tab_id = valid_ids.pop() if valid_ids else None
        return snaps, active_tab_id

    def has_session(self) -> bool:
        return bool(self.load()[0])
