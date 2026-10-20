# R7-S01 第 5 点 — 有限 IPC 消息 schema 与校验（纯逻辑层）
#
# 审计 S01 第 5 点："结合 A02 增加独立执行进程和有限 IPC；操作系统
# 沙箱/权限收敛单独验收，不能把 spawn 写成安全沙箱"。
#
# 本模块提供**有限消息 schema 的校验**，让 worker ↔ UI 的 IPC 通道
# 有明确的契约：
#
#   * 字段类型枚举（tab_id / seq / navigation_id 必须 int；kind 必须
#     取自固定枚举；payload 只允许有限标量，不允许嵌套宿主对象）；
#   * 大小上限（payload 的 dict 键数 / 字符串长度 / 数值深度有界，
#     防超大消息打穿父进程）；
#   * 回传结果只允许**有限摘要**（error_category 取枚举、payload
#     值必须是可 pickle 标量或有限 dict/list），不允许把宿主内部
#     对象（Python class 实例、函数、文件句柄）跨进程传回。
#
# 契约（测试锁定）：
#   * 不 import multiprocessing / pickle / subprocess / signal；
#   * 校验失败返回明确的错误字符串（调用方据此拒收，不静默放行）；
#   * 所有判定纯逻辑、无 I/O、无时钟。
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 允许的 DocCommand kind（与 document_process.DOC_CMD_* 对齐）。
VALID_CMD_KINDS = frozenset({
    "navigate", "input", "resize", "scroll", "stop",
})

# 允许的 DocFrame error_category（与现有代码一致）。
VALID_ERROR_CATEGORIES = frozenset({
    "", "dns", "connect", "timeout", "bad_url", "http", "script",
})

# 单个字符串值的最大字节数（防超大字符串打穿父进程）。
_MAX_STR_BYTES = 4096

# payload dict 的最大键数（防超大 dict 打穿父进程）。
_MAX_DICT_KEYS = 64

# 嵌套深度的硬上限（防超大嵌套结构）。
_MAX_DEPTH = 4

# 数值（int/float）的绝对值上限（防超大数）。
_MAX_ABS_NUM = 1 << 48


class _Reject:
    """校验失败结果。"""
    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason


@dataclass
class MessageBudget:
    """一条 IPC 消息的大小预算（调用方决定上限，本类只做判定）。

    三个维度独立：字符串总字节 / dict 键数 / 嵌套深度。任一超
    即拒绝，不静默截断（截断会改变语义，应当让调用方拒收整条
    消息）。
    """
    max_str_bytes: int = _MAX_STR_BYTES
    max_dict_keys: int = _MAX_DICT_KEYS
    max_depth: int = _MAX_DEPTH

    def check_value(self, v: Any, depth: int = 0) -> "str | _Reject | None":
        """校验一个标量/容器值是否可跨进程传（返回 None=合法，
        返回 :class:`_Reject`=非法，带 reason）。"""
        if depth > self.max_depth:
            return _Reject(f"nested depth {depth} > budget {self.max_depth}")
        if v is None or isinstance(v, (bool, int, float)):
            # 数值绝对值上限（int/float）。bool 是 int 子类但单独放行。
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if abs(v) > _MAX_ABS_NUM:
                    return _Reject(f"numeric value out of budget: {v}")
            return None
        if isinstance(v, str):
            if len(v.encode("utf-8", errors="replace")) > self.max_str_bytes:
                return _Reject(
                    f"string too long ({len(v)} chars > "
                    f"{self.max_str_bytes} byte budget)")
            return None
        if isinstance(v, (list, tuple)):
            if len(v) > self.max_dict_keys:
                return _Reject(f"list/tuple too long ({len(v)} > "
                                f"{self.max_dict_keys})")
            for item in v:
                r = self.check_value(item, depth + 1)
                if r is not None:
                    return r
            return None
        if isinstance(v, dict):
            if len(v) > self.max_dict_keys:
                return _Reject(f"dict too large ({len(v)} > "
                                f"{self.max_dict_keys} keys)")
            for k, val in v.items():
                if not isinstance(k, str):
                    return _Reject(f"dict key must be str, got {type(k).__name__}")
                r = self.check_value(val, depth + 1)
                if r is not None:
                    return r
            return None
        # 不允许：函数 / class 实例 / 文件句柄 / 其他宿主对象。
        return _Reject(
            f"value of type {type(v).__name__} not allowed in IPC payload")


def validate_command(tab_id: int, kind: str, payload: dict,
                     seq: int = 0, navigation_id: int = 0,
                     budget: MessageBudget | None = None
                     ) -> "str | None":
    """校验一条 DocCommand（返回 None=合法，字符串=拒绝原因）。

    检查顺序（先便宜后贵）：
    1. 字段类型（tab_id / seq / navigation_id 必须 int，bool 不混入）；
    2. kind 必须在枚举内；
    3. payload 经 budget 校验（标量/有限容器，无嵌套宿主对象）。
    """
    budget = budget or MessageBudget()
    if isinstance(tab_id, bool) or not isinstance(tab_id, int):
        return f"tab_id must be int (got {type(tab_id).__name__})"
    if isinstance(seq, bool) or not isinstance(seq, int):
        return f"seq must be int (got {type(seq).__name__})"
    if isinstance(navigation_id, bool) or not isinstance(navigation_id, int):
        return f"navigation_id must be int (got {type(navigation_id).__name__})"
    if tab_id < 0:
        return f"tab_id must be >= 0 (got {tab_id})"
    if seq < 0:
        return f"seq must be >= 0 (got {seq})"
    if navigation_id < 0:
        return f"navigation_id must be >= 0 (got {navigation_id})"
    if kind not in VALID_CMD_KINDS:
        return f"unknown command kind: {kind!r} (valid: {sorted(VALID_CMD_KINDS)})"
    if not isinstance(payload, dict):
        return f"payload must be dict (got {type(payload).__name__})"
    r = budget.check_value(payload)
    if r is not None:
        return f"payload rejected: {r.reason}"
    return None


def validate_frame(tab_id: int, seq: int, ok: bool,
                   error_category: str, payload: dict,
                   navigation_id: int = 0,
                   budget: MessageBudget | None = None
                   ) -> "str | None":
    """校验一条 DocFrame（返回 None=合法，字符串=拒绝原因）。

    审计第 5 点：worker 回传结果只能带**有限摘要**——
    * error_category 必须在枚举内；
    * payload 值必须是可 pickle 标量/有限容器（不允许宿主对象）；
    * 字符串字段不超长。
    """
    budget = budget or MessageBudget()
    if isinstance(tab_id, bool) or not isinstance(tab_id, int):
        return f"tab_id must be int (got {type(tab_id).__name__})"
    if tab_id < 0:
        return f"tab_id must be >= 0 (got {tab_id})"
    if isinstance(seq, bool) or not isinstance(seq, int):
        return f"seq must be int (got {type(seq).__name__})"
    if seq < 0:
        return f"seq must be >= 0 (got {seq})"
    if isinstance(navigation_id, bool) or not isinstance(navigation_id, int):
        return f"navigation_id must be int (got {type(navigation_id).__name__})"
    if navigation_id < 0:
        return f"navigation_id must be >= 0 (got {navigation_id})"
    if not isinstance(ok, bool):
        return f"ok must be bool (got {type(ok).__name__})"
    if error_category not in VALID_ERROR_CATEGORIES:
        return (f"unknown error_category: {error_category!r} "
                f"(valid: {sorted(VALID_ERROR_CATEGORIES)})")
    if not isinstance(payload, dict):
        return f"payload must be dict (got {type(payload).__name__})"
    r = budget.check_value(payload)
    if r is not None:
        return f"payload rejected: {r.reason}"
    return None


__all__ = [
    "VALID_CMD_KINDS",
    "VALID_ERROR_CATEGORIES",
    "MessageBudget",
    "validate_command",
    "validate_frame",
]
