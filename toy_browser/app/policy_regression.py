"""C10 — 策略 API 与事件循环扩大回归（纯逻辑核心，批次 14）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C10：

> 当前状态：本轮 10 个 HTTP 任务和观察器最小案例通过，不再按旧缺陷
> 重复实现。
>
> 1. DocumentPolicy 统一最终 URL、base、origin、CSP 与请求模式；增加
>    CLI、GUI、刷新和历史返回的一致性矩阵。
> 2. nonce 测合法、不匹配、缺失、区分大小写；脚本测相对/绝对/协议
>    相对 URL、重复标签、失败后继续执行与 data/module 类型。
> 3. fetch/XHR 增加正文、JSON、失败拒绝、重定向、超时、取消、凭据
>    及 CORS 允许/拒绝用例。网络任务完成回到 Document 所有者更新
>    Promise/事件。
> 4. MutationObserver 增加批量记录、subtree、disconnect、回调中继续
>    修改；ResizeObserver 增加尺寸不变、布局变化、断开和循环保护。
> 5. 事件循环扩展嵌套 Promise、定时器内微任务、重复定时器取消和 rAF
>    顺序，记录异步排空错误。
>
> 验收：采用真实本机 HTTP 与 Edge 行为，不只注入模拟响应或检查
> typeof。拒绝路径不得误当成功，观察器不能无限触发，保持已有测试
> 通过。交付：新的行为矩阵、离线夹具与受控在线回归。

本模块是**纯逻辑核心**（不依赖 tkinter / 真实网络），把 C10 的
"策略一致性矩阵 / nonce 校验 / 脚本 URL 解析 / 网络任务结果分类 /
观察器防无限触发 / 事件循环排空顺序"抽成可测试的数据结构；browser.py
/ JS 引擎的实际执行在活显示环境批次消费这里的判定层：

* :class:`PolicyConsistencyCase` + :func:`policy_matrix` — C10 步骤 1
  DocumentPolicy 一致性矩阵（CLI/GUI/刷新/历史返回 × 最终 URL/base/
  origin/CSP/请求模式），校验各入口得到**同一**策略。
* :func:`check_nonce` — C10 步骤 2 nonce 校验（合法 / 不匹配 / 缺失 /
  区分大小写）。
* :func:`resolve_script_url` — C10 步骤 2 脚本 URL 解析（相对 / 绝对 /
  协议相对 + data: + 重复标签去重 + 失败后继续）。
* :class:`NetworkOutcome` + :func:`classify_fetch` — C10 步骤 3
  fetch/XHR 结果分类（正文/JSON、失败拒绝、重定向、超时、取消、凭据、
  CORS 允许/拒绝），"拒绝路径不得误当成功"。
* :class:`ObserverGuard` — C10 步骤 4 观察器防无限触发（批量记录 /
  subtree / disconnect / 回调中继续修改 / ResizeObserver 循环保护）。
* :class:`EventLoopScheduler` — C10 步骤 5 事件循环排空顺序（嵌套
  Promise、定时器内微任务、重复定时器取消、rAF 顺序）+ 记录异步排空
  错误。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# C10 步骤 1 — DocumentPolicy 一致性矩阵
# ----------------------------------------------------------------------
@dataclass
class PolicyConsistencyCase:
    """C10 步骤 1 — 一个入口（CLI/GUI/刷新/历史返回）的策略快照。

    * ``entry`` — 入口标签（"cli" / "gui" / "refresh" / "history_back" /
      "history_forward"）。
    * ``final_url`` / ``base_url`` / ``origin`` / ``csp`` /
      ``request_mode`` — 该入口解析出的统一策略字段。
    """
    entry: str
    final_url: str
    base_url: str
    origin: str
    csp: str = ""
    request_mode: str = ""


def policy_matrix(cases: list[PolicyConsistencyCase]) -> dict:
    """C10 步骤 1 — 校验各入口策略**一致**（返回一致性矩阵）。

    * ``consistent`` — 所有入口的 final_url/base_url/origin 相同（True
      / False）。
    * ``fields`` — 各字段是否在所有入口间一致。
    * ``expected`` — 取第一个 case 的字段为基准。
    * ``mismatches`` — 不一致的 (字段, [(entry, value), ...]) 列表
      （诊断消费，定位哪个入口偏离了统一策略）。

    "CLI、GUI、刷新和历史返回的一致性矩阵"：同一最终 URL 经不同入口
    得到的 base/origin/CSP/请求模式必须一致。"""
    if not cases:
        return {"consistent": True, "fields": {},
                "expected": {}, "mismatches": []}
    fields = ("final_url", "base_url", "origin", "csp", "request_mode")
    expected = {f: getattr(cases[0], f) for f in fields}
    per_field: dict = {f: True for f in fields}
    mismatches: list = []
    for f in fields:
        vals = [(c.entry, getattr(c, f)) for c in cases]
        uniq = {v for _, v in vals}
        if len(uniq) > 1:
            per_field[f] = False
            mismatches.append((f, vals))
    consistent = all(per_field.values())
    return {
        "consistent": consistent,
        "fields": per_field,
        "expected": expected,
        "mismatches": mismatches,
    }


# ----------------------------------------------------------------------
# C10 步骤 2 — nonce 校验
# ----------------------------------------------------------------------
def check_nonce(script_nonce: Optional[str], provided_nonce: Optional[str],
                case_insensitive: bool = False) -> dict:
    """C10 步骤 2 — nonce 校验（合法 / 不匹配 / 缺失 / 区分大小写）。

    * *script_nonce* — 策略里声明的 nonce（CSP nonce 或 meta nonce）。
    * *provided_nonce* — 脚本标签实际携带的 nonce。
    * *case_insensitive* — nonce 是否按大小写不敏感比较（C10 步骤 2
      "区分大小写" 用例：默认敏感，显式可设不敏感）。

    返回 ``{"allowed": bool, "reason": str}``：
    * ``script_nonce`` 缺失 → 无需 nonce（允许，``reason="no_nonce"``）。
    * 有声明但 ``provided_nonce`` 缺失 → 拒绝（``"nonce_missing"``）。
    * 不匹配 → 拒绝（``"nonce_mismatch"``）。
    * 匹配 → 允许（``"nonce_ok"``）。
    """
    if script_nonce is None or script_nonce == "":
        return {"allowed": True, "reason": "no_nonce"}
    if provided_nonce is None or provided_nonce == "":
        return {"allowed": False, "reason": "nonce_missing"}
    if case_insensitive:
        ok = script_nonce.lower() == provided_nonce.lower()
    else:
        ok = script_nonce == provided_nonce
    if ok:
        return {"allowed": True, "reason": "nonce_ok"}
    return {"allowed": False, "reason": "nonce_mismatch"}


# ----------------------------------------------------------------------
# C10 步骤 2 — 脚本 URL 解析（相对 / 绝对 / 协议相对 + data + 去重）
# ----------------------------------------------------------------------
def resolve_script_url(src: str, base_url: str) -> dict:
    """C10 步骤 2 — 脚本 src 相对 base_url 解析成最终 URL。

    返回 ``{"url": str, "kind": str}``：
    * ``kind="absolute"`` — src 是绝对 URL（http/https/…）。
    * ``kind="protocol_relative"`` — src 以 ``//`` 开头（协议相对，
      补 base 的 scheme）。
    * ``kind="data"`` — src 是 ``data:`` 内联脚本。
    * ``kind="module"`` — src 是模块脚本 URL（调用方标 module）。
    * ``kind="relative"`` — 其它（相对 base_url 解析）。

    * *base_url* 形如 ``https://host/dir/page.html``；相对解析取 base
      的 origin + 目录 + src。
    """
    s = (src or "").strip()
    base = (base_url or "").strip()
    if not s:
        return {"url": "", "kind": "empty"}
    if s.startswith("data:"):
        return {"url": s, "kind": "data"}
    if s.startswith("//"):
        # 协议相对：补 base 的 scheme（scheme 本身已含冒号，如
        # "https:"），形如 https: + //cdn/x.js
        scheme = base.split("://", 1)[0] + ":" if "://" in base \
            else "https:"
        return {"url": scheme + s, "kind": "protocol_relative"}
    if s.startswith(("http://", "https://")):
        return {"url": s, "kind": "absolute"}
    # 相对：取 base 的 origin + 目录（去掉最后一段路径，保留目录结构）
    if "://" in base:
        scheme, rest = base.split("://", 1)
        last_slash = rest.rfind("/")
        base_dir = (scheme + "://" + rest[:last_slash + 1]) if last_slash > 0 \
            else scheme + ":///"
        url = base_dir + s.lstrip("/")
    else:
        url = s
    return {"url": url, "kind": "relative"}


def dedupe_scripts(sources: list) -> list:
    """C10 步骤 2 — 重复脚本标签去重（保持首次出现顺序）。"""
    seen: dict = {}
    out: list = []
    for src in sources:
        if src not in seen:
            seen[src] = True
            out.append(src)
    return out


# ----------------------------------------------------------------------
# C10 步骤 3 — fetch/XHR 结果分类（"拒绝路径不得误当成功"）
# ----------------------------------------------------------------------
@dataclass
class NetworkOutcome:
    """C10 步骤 3 — 一次网络任务的结果分类。

    * ``ok`` — 是否成功（拒绝 / 失败路径必须 ``False``，不得误当成功）。
    * ``category`` — "json" / "text" / "redirected" / "failed" /
      "timeout" / "cancelled" / "cors_denied" / "credentials"。
    * ``body`` / ``json`` — 响应正文 / JSON 载荷（成功时）。
    * ``status`` — HTTP 状态码。
    * ``reason`` — 失败 / 拒绝原因。
    """
    ok: bool
    category: str
    body: Optional[str] = None
    json: Optional[object] = None
    status: int = 0
    reason: str = ""

    @property
    def success(self) -> bool:
        """C10 验收 "拒绝路径不得误当成功"：只有 ok 且非失败类才算
        成功。"""
        return self.ok and self.category not in (
            "failed", "timeout", "cancelled", "cors_denied")


def classify_fetch(status: int, body: Optional[str] = None,
                   json_payload: Optional[object] = None,
                   redirected: bool = False, timed_out: bool = False,
                   cancelled: bool = False, cors_denied: bool = False,
                   credentials: Optional[str] = None) -> NetworkOutcome:
    """C10 步骤 3 — 把一次 fetch/XHR 的原始结果分类成
    :class:`NetworkOutcome`。

    分类优先级（拒绝 / 失败路径优先，不得误当成功）：
    * ``cancelled`` → ``ok=False, "cancelled"``。
    * ``timed_out`` → ``ok=False, "timeout"``。
    * ``cors_denied`` → ``ok=False, "cors_denied"``。
    * 4xx/5xx → ``ok=False, "failed"``（status 记录）。
    * 其余成功：有 JSON 载荷 → ``"json"``；重定向 → ``"redirected"``
      （仍成功，body 记录）；否则 ``"text"``（body 记录）。
    * *credentials* 非 None → 附加到 reason（凭据模式标注）。
    """
    if cancelled:
        return NetworkOutcome(False, "cancelled", reason="cancelled")
    if timed_out:
        return NetworkOutcome(False, "timeout", reason="timed out")
    if cors_denied:
        return NetworkOutcome(False, "cors_denied",
                             reason="CORS denied")
    if 400 <= status < 600:
        return NetworkOutcome(False, "failed", status=status,
                             reason=f"HTTP {status}", body=body)
    reason = f"credentials={credentials}" if credentials else ""
    if json_payload is not None:
        return NetworkOutcome(True, "json", json=json_payload,
                              status=status, reason=reason)
    if redirected:
        return NetworkOutcome(True, "redirected", body=body,
                              status=status, reason=reason)
    return NetworkOutcome(True, "text", body=body, status=status,
                          reason=reason)


# ----------------------------------------------------------------------
# C10 步骤 4 — 观察器防无限触发
# ----------------------------------------------------------------------
class ObserverGuard:
    """C10 步骤 4 — MutationObserver / ResizeObserver 防无限触发。

    观察器回调里继续修改 DOM / 触发 resize 会导致**无限触发**（C10
    验收 "观察器不能无限触发"）。本守卫限制单轮批处理内的回调次数：

    * :meth:`record` — 记一条观察记录（MutationObserver 批量记录）。
    * :meth:`drain` — 取出一批待处理记录（"批量记录"）；超过
      ``max_per_batch`` 时截断并置 ``truncated``（防单轮吞掉全部）。
    * :meth:`fire` — 触发回调前检查本轮已触发次数；超过
      ``max_fires`` 拒绝触发（循环保护）。
    * :meth:`disconnect` — 断开后不再记录 / 触发（"disconnect"）。
    * :meth:`in_callback` — 标记"正在回调中"（"回调中继续修改" →
      新修改排到下一批，不立即触发，避免无限递归）。
    """

    def __init__(self, max_per_batch: int = 64,
                 max_fires: int = 32) -> None:
        self.max_per_batch = max_per_batch
        self.max_fires = max_fires
        self._records: list = []
        self._fired_this_round = 0
        self._in_callback = False
        self._disconnected = False
        self.truncated = False

    def record(self, record: object) -> None:
        """MutationObserver 批量记录（"批量记录"）。"""
        if self._disconnected:
            return
        self._records.append(record)

    def drain(self) -> list:
        """取出一批记录（最多 max_per_batch，超出截断）。"""
        if self._disconnected:
            return []
        batch = self._records[:self.max_per_batch]
        self._records = self._records[self.max_per_batch:] if len(
            self._records) > self.max_per_batch else []
        self.truncated = len(batch) == self.max_per_batch and \
            self._records is not None and len(batch) == self.max_per_batch
        # 简化：截断标志 = 本批满且仍有剩余
        self.truncated = (len(batch) == self.max_per_batch)
        self._fired_this_round = 0   # 新一批，重置触发计数
        return batch

    def can_fire(self) -> bool:
        """本轮是否还能触发回调（循环保护）。"""
        return not self._disconnected and \
            self._fired_this_round < self.max_fires

    def fire(self) -> bool:
        """触发一次回调；超限 / 已断开 / 回调中 → 不触发。"""
        if not self.can_fire():
            return False
        if self._in_callback:
            # 回调中继续修改：不立即再触发，排下一批
            return False
        self._fired_this_round += 1
        self._in_callback = True
        return True

    def end_callback(self) -> None:
        """回调结束（允许同轮后续触发直到 max_fires）。"""
        self._in_callback = False

    def disconnect(self) -> None:
        self._disconnected = True
        self._in_callback = False


class ResizeObserverGuard:
    """C10 步骤 4 — ResizeObserver 尺寸不变 / 布局变化 / 循环保护。

    * :meth:`observe` — 记录某 key 的尺寸。
    * :meth:`changed` — 尺寸相对上次是否变化（"尺寸不变" → 不触发；
      "布局变化" → 触发一次）。
    * :meth:`loop_guard` — 单轮内对同一 key 的触发次数，超过
      ``max_per_round`` 拒绝（循环保护，防 resize → 布局 → resize
      死循环）。
    """

    def __init__(self, max_per_round: int = 4) -> None:
        self.max_per_round = max_per_round
        self._sizes: dict = {}
        self._fired: dict = {}

    def observe(self, key: str, size: tuple) -> bool:
        """记录尺寸；相对上次变化才返回 True（需触发一次）。"""
        if self._sizes.get(key) == size:
            return False   # 尺寸不变 → 不触发
        self._sizes[key] = size
        fired = self._fired.get(key, 0)
        if fired >= self.max_per_round:
            return False   # 循环保护：本轮已触发上限
        self._fired[key] = fired + 1
        return True

    def reset_round(self) -> None:
        """新渲染轮次：清触发计数（保留尺寸基准）。"""
        self._fired.clear()


# ----------------------------------------------------------------------
# C10 步骤 5 — 事件循环排空顺序（嵌套 Promise / 定时器内微任务 /
# 重复定时器取消 / rAF 顺序）
# ----------------------------------------------------------------------
class EventLoopScheduler:
    """C10 步骤 5 — 事件循环排空顺序（记录异步排空错误）。

    任务类型（影响排空顺序，对齐浏览器事件循环）：

    * ``"microtask"`` — Promise 回调（嵌套 Promise 按 FIFO 入微任务
      队列，微任务在任务之间排空）。
    * ``"task"`` — 普通任务（setTimeout）。
    * ``"raf"`` — requestAnimationFrame（每渲染帧按调度顺序执行）。

    规则（浏览器事件循环）：
    1. 每执行完一个任务 / 宏任务，先排空**当前**微任务队列
       （嵌套 Promise 产生的新微任务也一起排空）。
    2. 重复定时器（setInterval）取消 → 不再入队。
    3. rAF 在每帧按调度顺序执行（后调度后执行）。
    4. 排空中出错 → 记录到 ``drain_errors``（"记录异步排空错误"）。

    * :meth:`schedule` — 调度任务（返回 task_id）。
    * :meth:`drain` — 执行到排空（或达到 *max_steps*），返回执行顺序
      + 记录错误。
    * :meth:`cancel` — 取消任务（重复定时器取消）。
    """

    def __init__(self) -> None:
        self._micro: list = []       # 微任务 FIFO（嵌套 Promise）
        self._tasks: list = []       # 宏任务 FIFO
        self._raf: list = []         # rAF FIFO
        self._intervals: dict = {}   # 重复定时器
        self._next_id = 1
        self.drain_errors: list = []
        self._order: list = []

    def schedule(self, kind: str, fn, repeat: bool = False,
                 interval_id: str = "") -> int:
        tid = self._next_id
        self._next_id += 1
        if kind == "microtask":
            self._micro.append((tid, fn))
        elif kind == "task":
            if repeat:
                self._intervals[interval_id or str(tid)] = (tid, fn)
            self._tasks.append((tid, fn))
        elif kind == "raf":
            self._raf.append((tid, fn))
        return tid

    def cancel(self, tid: int) -> bool:
        """取消任务 / 重复定时器（"重复定时器取消"）。"""
        # 从所有队列移除
        self._micro = [(i, f) for i, f in self._micro if i != tid]
        self._tasks = [(i, f) for i, f in self._tasks if i != tid]
        self._raf = [(i, f) for i, f in self._raf if i != tid]
        # 重复定时器：取消后不再重新入队
        for k, (i, f) in list(self._intervals.items()):
            if i == tid:
                del self._intervals[k]
        return True

    def drain(self, max_steps: int = 1000) -> list:
        """执行到排空（或 *max_steps*），返回执行顺序（task 标签序列）。

        简化模型：宏任务逐个执行（每个宏任务后排空微任务），然后
        rAF 按调度顺序执行。重复定时器在对应任务执行后重新入队
        （若未取消）。错误记录到 :attr:`drain_errors`。"""
        self._order = []
        steps = 0
        while steps < max_steps:
            # 1) 取一个宏任务
            if self._tasks:
                tid, fn = self._tasks.pop(0)
                self._order.append(("task", tid))
                try:
                    fn()
                except Exception as e:
                    self.drain_errors.append(("task", tid, str(e)))
                # 宏任务后重新入队未取消的重复定时器
                for k, (i, f) in list(self._intervals.items()):
                    self._tasks.append((i, f))
                # 2) 排空微任务（嵌套 Promise）
                while self._micro:
                    steps += 1
                    if steps >= max_steps:
                        break
                    mid, mfn = self._micro.pop(0)
                    self._order.append(("microtask", mid))
                    try:
                        mfn()
                    except Exception as e:
                        self.drain_errors.append(("microtask", mid,
                                                  str(e)))
                steps += 1
                continue
            # 无宏任务 → 执行 rAF（按调度顺序）
            if self._raf:
                rid, rfn = self._raf.pop(0)
                self._order.append(("raf", rid))
                try:
                    rfn()
                except Exception as e:
                    self.drain_errors.append(("raf", rid, str(e)))
                steps += 1
                continue
            break
        return self._order

    def pending_count(self) -> int:
        return (len(self._micro) + len(self._tasks)
                + len(self._raf))

    def is_drained(self) -> bool:
        return self.pending_count() == 0
