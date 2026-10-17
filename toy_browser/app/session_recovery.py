"""批次 26 — G09 step 5 会话持久化损坏文件降级解析（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G09 step 5：

> 会话持久化仅保存可恢复状态（URL、标题、前后栈、滚动、活动标签、
> 书签），不保存 DOM / 位图 / 脚本 Host；损坏文件可降级打开。

G09 验收：

> … 恢复损坏数据。

已有模块（本模块**不重复**实现，只做损坏文件降级解析）：
- **G09 step 5**（:class:`toy_browser.app.session.HistoryStore` /
  :class:`toy_browser.app.session.BookmarkStore`，批次 1 / P32）已有
  原子写（写 ``.tmp`` 再 ``os.replace``，崩溃不损坏）+ 全有 / 全无
  读取（:func:`toy_browser.app.session._load_json_list` 损坏直接返回
  空，绝不 raise）。
- **G09 会话快照**（:class:`toy_browser.app.tab_identity.TabSnapshot`，
  批次 4；:class:`toy_browser.app.tab_session.TabSession`，批次 25）
  已定义"可恢复状态"的字段集合（url / title / back / fwd / scroll /
  active；不含 DOM / 位图 / 脚本 Host）。

本模块补**损坏文件降级解析**：把"全损 → 空"升级为"逐条抢救"
（partially-recoverable），G09 验收"恢复损坏数据"：

* :func:`recover_session` — 读一个（可能损坏的）会话 JSON 文件，
  按"逐条尽力解析"策略抢救可恢复条目：
  - 文件不存在 / 为空 → 空会话（降级，非错误）；
  - 整体可解析为 JSON 数组 → 逐条校验（只保留"可恢复状态"字段合法
    的条目，丢弃非法项 + 记入 ``skipped``）；
  - 整体解析失败（截断 / 乱码）→ 尝试**逐行**解析 NDJSON（JSON Lines）
    中可解析的行（原子写崩溃中途可能留下部分完好的行）；
  - 仍无法解析 → 全损降级为空会话 + ``degraded="full"``。
* :func:`recover_history` / :func:`recover_bookmarks` — 针对
  :class:`HistoryStore` / :class:`BookmarkStore` 的单条对象
  校验 + 逐条抢救（历史项 ``{"url","title","ts"}``；书签项
  ``{"url","title"}``），与 :func:`recover_session` 同策略。
* :class:`RecoveryReport` — 抢救审计：``recovered``（抢救成功条目）+
  ``skipped``（被丢弃的非法项及原因）+ ``degraded``（``"none"`` /
  ``"partial"`` / ``"full"``）+ ``source``（解析来源：``json`` /
  ``ndjson`` / ``none``）。诊断区可据此展示"从损坏文件抢救了多少 /
  丢了哪些"。

设计要点（与"原子写 + 全损降级"已有行为**兼容**）：
- 任何输入都**不 raise**（损坏文件不阻塞浏览器启动，G09 验收）。
- 降级后调用方可把 ``recovered`` 直接写回 store（修复损坏文件）；
  被丢弃项记入 ``skipped`` 供审计 / 日志。
- 只校验"可恢复状态"字段（url / title / back / fwd / scroll / zoom /
  active / ts）；**不**校验 / 恢复 DOM / 位图 / 脚本 Host（G09 step 5
  "仅保存可恢复状态"——它们本就不该出现在持久化文件里，出现则视为
  非法项丢弃）。
- 纯逻辑核心：不触碰 tkinter / 网络；文件读写仅用 stdlib ``json`` /
  ``os``，供 GUI / CLI / 测试共用同一降级策略。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# 降级级别 / 解析来源
# ---------------------------------------------------------------------------

DEGRADED_NONE = "none"      # 文件完好（整体可解析）
DEGRADED_PARTIAL = "partial"  # 部分损坏（逐条 / 逐行抢救）
DEGRADED_FULL = "full"      # 全损（无任何可恢复条目）

SOURCE_JSON = "json"        # 整体 JSON 数组解析成功
SOURCE_NDJSON = "ndjson"    # 逐行 NDJSON 抢救
SOURCE_NONE = "none"        # 无任何来源（空 / 缺失）


# ---------------------------------------------------------------------------
# 可恢复状态字段的合法化（G09 step 5：仅保存可恢复状态）
# ---------------------------------------------------------------------------

# 各可恢复字段 → 合法类型（None 表示"缺省可接受"，给默认值）
_FIELD_SPECS: dict[str, tuple] = {
    "tab_id": (int, float),
    "url": (str,),
    "title": (str,),
    "back_stack": (list,),
    "fwd_stack": (list,),
    "scroll": (int, float),
    "zoom": (int, float),
    "active": (bool,),
    "ts": (int, float),
    "name": (str,),
}


def _legal_value(key: str, value) -> bool:
    spec = _FIELD_SPECS.get(key)
    if spec is None:
        # 未列入可恢复状态的字段（DOM / 位图 / 脚本 Host 等）→ 非法
        return False
    # bool 是 int 子类，单独收紧：数值字段（int / float）不认 bool
    if isinstance(value, bool) and (int in spec or float in spec) \
            and key not in ("active",):
        return False
    return isinstance(value, spec)


def sanitize_entry(raw: dict) -> Optional[dict]:
    """把一条原始持久化条目**清洗**为只含合法可恢复字段的 dict。

    * 仅保留 :data:`_FIELD_SPECS` 中类型合法的键值；
    * 非 dict / 非法项 → 返回 None（调用方记入 ``skipped``）。
    * 缺省键不补默认值（保持"抢救到的原样"，由 store 侧补默认）。
    """
    if not isinstance(raw, dict):
        return None
    cleaned: dict = {}
    for k, v in raw.items():
        if _legal_value(k, v):
            cleaned[k] = v
    # 至少要有一个可恢复字段，否则视为非法（如 {"dom": ...} 全被丢）
    if not cleaned:
        return None
    return cleaned


def _entry_reason(raw) -> str:
    if raw is None:
        return "null"
    if not isinstance(raw, dict):
        return f"not-a-dict({type(raw).__name__})"
    bad = [k for k, v in raw.items() if not _legal_value(k, v)]
    if bad:
        return "illegal-fields:" + ",".join(sorted(bad))
    return "all-fields-illegal-or-empty"


# ---------------------------------------------------------------------------
# 抢救报告
# ---------------------------------------------------------------------------

@dataclass
class RecoveryReport:
    """G09 step 5 — 损坏文件降级解析的审计（诊断区可展示）。

    * ``recovered`` — 抢救成功的条目列表（已清洗为合法可恢复状态）。
    * ``skipped`` — 被丢弃的非法项（``{"raw": …, "reason": …}``，
      raw 截断展示）。
    * ``degraded`` — 降级级别（``none`` / ``partial`` / ``full``）。
    * ``source`` — 解析来源（``json`` / ``ndjson`` / ``none``）。
    * ``total_seen`` — 文件中实际见到的条目 / 行数（诊断用）。
    """
    recovered: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    degraded: str = DEGRADED_NONE
    source: str = SOURCE_NONE
    total_seen: int = 0

    def summary(self) -> str:
        return (f"recovered={len(self.recovered)} "
                f"skipped={len(self.skipped)} "
                f"degraded={self.degraded} "
                f"source={self.source} "
                f"seen={self.total_seen}")


# ---------------------------------------------------------------------------
# 核心降级解析
# ---------------------------------------------------------------------------

def _parse_whole_json(text: str) -> Optional[list]:
    """整体 JSON 数组解析；失败返回 None。"""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict):
        # 单个 dict 会话（如 TabSession.snapshot 序列化为 {"tabs":[...]}）
        # 不直接作条目列表；交给调用方提取。这里只认纯数组。
        return None
    return data if isinstance(data, list) else None


def _parse_ndjson(text: str) -> list[dict]:
    """NDJSON 抢救：先逐行，再括号配平扫描（应对未闭合大对象）。

    原子写（写 ``.tmp`` 再 ``os.replace``）在崩溃中途可能留下**部分
    完好的行**（甚至最后一个对象未闭合 ``{"url":"c"`` 缺 ``}``）；
    两层抢救比全损降级好：

    1. 逐行 ``json.loads``（标准 NDJSON，每行一个独立对象 / 数组）；
    2. 若逐行一条都救不回来，用**括号配平扫描**（:func:`_scan_
       balanced_objects`）抽出 *text* 里所有完整闭合的 ``{...}``
       对象再逐个解析（嵌套 / 字符串内花括号 / 转义都安全）。
    """
    out: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            val = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(val, list):
            out.extend(val)
        elif isinstance(val, dict):
            out.append(val)
    if out:
        return out
    # 第二层：括号配平扫描（整串是一个未闭合 / 跨行大对象）
    return _scan_balanced_objects(text)


def _scan_balanced_objects(text: str) -> list[dict]:
    """括号配平扫描：抽出 *text* 里所有**完整闭合**的 ``{...}`` JSON
    对象，逐个 ``json.loads``，返回解析成功的对象 / 数组展平列表。

    处理嵌套花括号（``{"a":{"b":1}}``）、字符串内的花括号与引号
    （``{"s":"}{"}``）及反斜杠转义。用于抢救"截断 JSON 里尚未损坏
    的完整对象"。"""
    objs: list = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                seg = text[start:i + 1]
                try:
                    val = json.loads(seg)
                except (json.JSONDecodeError, ValueError):
                    val = None
                if isinstance(val, dict):
                    objs.append(val)
                elif isinstance(val, list):
                    objs.extend(val)
                start = -1
    return objs


def recover_json_entries(data, report: RecoveryReport,
                        source: str) -> None:
    """把一条 / 多条原始条目逐条清洗后填进 *report*。"""
    for raw in data:
        report.total_seen += 1
        cleaned = sanitize_entry(raw)
        if cleaned is not None:
            report.recovered.append(cleaned)
        else:
            report.skipped.append({"raw": _truncate(raw),
                                   "reason": _entry_reason(raw)})


def _truncate(obj, limit: int = 200):
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = repr(obj)
    if len(s) > limit:
        return s[:limit] + "…"
    return s


def _finalize(report: RecoveryReport, whole_ok: bool,
              ndjson_used: bool) -> None:
    """根据抢救结果定降级级别 + 来源。"""
    if whole_ok:
        report.source = SOURCE_JSON
        report.degraded = DEGRADED_NONE
    elif ndjson_used:
        report.source = SOURCE_NDJSON
        report.degraded = DEGRADED_PARTIAL
    else:
        report.source = SOURCE_NONE
        # 无任何可恢复条目 → 全损降级
        report.degraded = DEGRADED_FULL if report.total_seen == 0 \
            else DEGRADED_PARTIAL
    # 文件缺失 / 空：degraded 保持 none、source none（非损坏，正常空会话）


def recover_session_file(path: str) -> tuple[list[dict], RecoveryReport]:
    """G09 step 5 — 从（可能损坏的）会话 JSON 文件抢救可恢复条目。

    返回 ``(recovered_entries, report)``：

    - 文件不存在 / 为空 → ``([], report)``（降级，``degraded="none"``，
      非错误）。
    - 整体可解析为 JSON 数组 → 逐条校验（保留合法可恢复条目，非法项
      记 ``skipped``），``source="json"``；若有任何跳过 → 降级
      ``"partial"``。
    - 整体解析失败 → 逐行 NDJSON 抢救（保住崩溃中途留下的完好行），
      ``source="ndjson"``、``degraded="partial"``；无一行可解析 →
      ``degraded="full"``（全损降级空会话）。

    任何输入都不 raise（损坏文件不阻塞浏览器启动）。
    """
    report = RecoveryReport()
    if not os.path.isfile(path):
        _finalize(report, whole_ok=True, ndjson_used=False)
        return [], report
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        _finalize(report, whole_ok=False, ndjson_used=False)
        return [], report
    if not text.strip():
        _finalize(report, whole_ok=True, ndjson_used=False)
        return [], report

    whole = _parse_whole_json(text)
    if whole is not None:
        report.source = SOURCE_JSON
        # 整体是数组：若有跳过的非法项则降级 partial
        for raw in whole:
            report.total_seen += 1
            cleaned = sanitize_entry(raw)
            if cleaned is not None:
                report.recovered.append(cleaned)
            else:
                report.skipped.append(
                    {"raw": _truncate(raw), "reason": _entry_reason(raw)})
        if report.skipped:
            report.degraded = DEGRADED_PARTIAL
        else:
            report.degraded = DEGRADED_NONE
        return list(report.recovered), report

    # 整体解析失败 → NDJSON 逐行抢救
    rows = _parse_ndjson(text)
    if rows:
        report.source = SOURCE_NDJSON
        recover_json_entries(rows, report, SOURCE_NDJSON)
        report.degraded = DEGRADED_PARTIAL
        return list(report.recovered), report

    # 全损：无任何可恢复条目
    _finalize(report, whole_ok=False, ndjson_used=False)
    return [], report


def recover_session(payload) -> tuple[list[dict], RecoveryReport]:
    """G09 step 5 — 从已解析 / 待解析的会话**负载**抢救可恢复条目。

    *payload* 可能是：
    - ``str``（JSON 文本）：先整体解析，失败再 NDJSON 逐行；
    - ``list``：逐条清洗；
    - ``dict``：若是 ``{"tabs": [...]}``（TabSession.snapshot 序列化
      形态）→ 取 ``tabs`` 列表；否则单 dict 当一条清洗。

    返回 ``(recovered_entries, report)``，语义同
    :func:`recover_session_file`（不抛异常，损坏降级不阻塞启动）。
    """
    report = RecoveryReport()
    if isinstance(payload, str):
        text = payload
        if not text.strip():
            _finalize(report, whole_ok=True, ndjson_used=False)
            return [], report
        whole = _parse_whole_json(text)
        if whole is not None:
            report.source = SOURCE_JSON
            recover_json_entries(whole, report, SOURCE_JSON)
            if report.skipped:
                report.degraded = DEGRADED_PARTIAL
            else:
                report.degraded = DEGRADED_NONE
            return list(report.recovered), report
        rows = _parse_ndjson(text)
        if rows:
            report.source = SOURCE_NDJSON
            recover_json_entries(rows, report, SOURCE_NDJSON)
            report.degraded = DEGRADED_PARTIAL
            return list(report.recovered), report
        _finalize(report, whole_ok=False, ndjson_used=False)
        return [], report

    if isinstance(payload, list):
        report.source = SOURCE_JSON
        recover_json_entries(payload, report, SOURCE_JSON)
        report.degraded = (DEGRADED_PARTIAL if report.skipped
                           else DEGRADED_NONE)
        return list(report.recovered), report

    if isinstance(payload, dict):
        report.source = SOURCE_JSON
        tabs = payload.get("tabs")
        if isinstance(tabs, list):
            recover_json_entries(tabs, report, SOURCE_JSON)
            report.degraded = (DEGRADED_PARTIAL if report.skipped
                               else DEGRADED_NONE)
            return list(report.recovered), report
        # 单 dict 当一条
        report.total_seen = 1
        cleaned = sanitize_entry(payload)
        if cleaned is not None:
            report.recovered.append(cleaned)
        else:
            report.skipped.append(
                {"raw": _truncate(payload),
                 "reason": _entry_reason(payload)})
        report.degraded = (DEGRADED_PARTIAL if report.skipped
                           else DEGRADED_NONE)
        return list(report.recovered), report

    # 非法 payload（None / int …）→ 全损降级
    _finalize(report, whole_ok=False, ndjson_used=False)
    return [], report


# ---------------------------------------------------------------------------
# 单条对象抢救（HistoryStore / BookmarkStore 用）
# ---------------------------------------------------------------------------

def recover_history(data) -> tuple[list[dict], RecoveryReport]:
    """抢救历史项（``{"url","title","ts"}``）：非法项丢弃，合法项
    保留并补齐 ``ts`` 缺省。"""
    report = RecoveryReport()
    entries = data if isinstance(data, list) else [data]
    for raw in entries:
        report.total_seen += 1
        cleaned = sanitize_entry(raw)
        if cleaned is None or "url" not in cleaned:
            report.skipped.append(
                {"raw": _truncate(raw),
                 "reason": "no-url-or-illegal" + (
                     " " + _entry_reason(raw) if cleaned is not None
                     else "")})
            continue
        cleaned.setdefault("title", "")
        cleaned.setdefault("ts", 0.0)
        report.recovered.append(cleaned)
    report.degraded = (DEGRADED_PARTIAL if report.skipped
                       else DEGRADED_NONE)
    return list(report.recovered), report


def recover_bookmarks(data) -> tuple[list[dict], RecoveryReport]:
    """抢救书签项（``{"url","title"}``）：非法项丢弃，合法项保留并
    补齐 ``title`` 缺省（缺省取 url，与 :meth:`BookmarkStore.add` 一致）。"""
    report = RecoveryReport()
    entries = data if isinstance(data, list) else [data]
    for raw in entries:
        report.total_seen += 1
        cleaned = sanitize_entry(raw)
        if cleaned is None or "url" not in cleaned:
            report.skipped.append(
                {"raw": _truncate(raw),
                 "reason": "no-url-or-illegal"})
            continue
        if "title" not in cleaned:
            cleaned["title"] = cleaned["url"]
        report.recovered.append(cleaned)
    report.degraded = (DEGRADED_PARTIAL if report.skipped
                       else DEGRADED_NONE)
    return list(report.recovered), report


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    entries, rep = recover_session(
        '[{"tab_id":1,"url":"http://a","back_stack":["http://0"]}, '
        '{"url":"http://b","active":true}, '
        '{"dom":"<tree>"}]')
    print("recovered:", entries)
    print("report:", rep.summary())
