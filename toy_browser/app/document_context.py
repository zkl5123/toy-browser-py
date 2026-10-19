"""B08 — 按真实页面生命周期加载资源与调度脚本（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B08（批次 19 纯逻辑落点；``net.py`` 资源加载器与 GUI 接线留后续）。

与已有模块的关系：
- **B08 步骤 1**（同一 ``DocumentContext`` 保存最终 URL / 基址 / 策略 /
  资源表 / 任务版本 / 错误列表，消除 CLI/GUI 重复拼接）由
  :class:`DocumentContext` 承担；任务版本与 C01
  :class:`~toy_browser.app.document_state_full.DocumentState` 的
  ``doc_version`` 对齐（``doc_version`` 可选注入）。
- **B08 步骤 2**（资源记录类型 / 请求 / 解码结果 / 使用方 / 阻断原因，
  区分"下载 200""成功解码""应用到页面"）由 :class:`ResourceRecord`
  的三态 ``downloaded`` / ``decoded`` / ``applied`` + ``blocked_reason``
  承担——"33 个 200"不能代替渲染验收（B08 验收）。
- **B08 步骤 3**（动态插入 script / async / defer / CSS 依赖 /
  DOMContentLoaded / load 最小一致模型）由
  :class:`ScriptScheduler` 承担：按"脚本启用状态 + 位置 + async/defer
  标志"决定执行顺序，DOMContentLoaded / load 门控不提前触发。
- **B08 步骤 4**（font / img / css url 缺口按失败降级）：
  ``ResourceRecord`` 失败时保留已确定的部分（先显示能确定的内容），
  ``DocumentContext.usage_report`` 输出"可解释为何仍隐藏或缺失内容"。

无 Tk / 无网络 I/O；``tests/test_document_context.py`` 覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- B08 步骤 2：资源三态词表 ------------------------------------------------

RES_HTML = "html"
RES_CSS = "css"
RES_JS = "js"
RES_IMG = "img"
RES_FONT = "font"
RES_OTHER = "other"


@dataclass
class ResourceRecord:
    """B08 步骤 2 — 一条资源记录（请求 / 解码 / 应用三态 + 阻断原因）。

    - ``downloaded`` — 下载 200（区别于"成功解码 / 应用到页面"）。
    - ``decoded``    — 成功解码（字节 → 可用对象）。
    - ``applied``    — 应用到页面（真正影响渲染 / DOM）。
    - ``blocked_reason`` — 任一阶段失败的原因（"download-failed" /
      "decode-failed" / "policy-denied" / "not-applied"）。
    - ``used_by``    — 使用方（哪个元素 / 脚本消费它）。
    """
    url: str
    kind: str = RES_OTHER
    status_code: int = 0
    downloaded: bool = False
    decoded: bool = False
    applied: bool = False
    used_by: str = ""
    blocked_reason: str = ""
    size_bytes: int = 0

    def summary(self) -> str:
        st = []
        if self.downloaded:
            st.append(f"d{self.status_code}")
        else:
            st.append("dn-fail")
        st.append("dc" if self.decoded else "dc-fail")
        st.append("ap" if self.applied else "ap-fail")
        tail = f" ({self.blocked_reason})" if self.blocked_reason else ""
        return f"{self.kind} {self.url} " + " ".join(st) + tail

    @property
    def usable(self) -> bool:
        """B08 验收 — "可操作首屏"要求资源真正 applied（不只是 200）。"""
        return self.applied


# ---------------------------------------------------------------------------
# B08 步骤 3：脚本调度最小模型
# ---------------------------------------------------------------------------

@dataclass
class ScriptSpec:
    """一个 ``<script>`` 的调度规格（动态插入 / async / defer / module / 位置）。

    - ``position`` — ``inline`` / ``head`` / ``body``（影响相对执行顺序）。
    - ``async`` / ``defer`` — 布尔标志（B08 步骤 3 "async/defer"）。
    - ``is_module`` — R7-J02 第3点：ES module 脚本。module 脚本**始终 deferred**
      （即使无 defer 属性），且未实现 module loader 时不可当经典 classic 执行。
    - ``enabled`` — 脚本启用状态（被策略 / CSP 禁用时 False）。
    - ``doc_ready`` — 是否需要在 DOMContentLoaded 之后执行。
    """
    url: str
    position: str = "head"
    async_flag: bool = False
    defer: bool = False
    is_module: bool = False
    enabled: bool = True
    doc_ready: bool = False

    def key(self) -> tuple:
        """调度排序键（B08 步骤 3 + R7-J02 第3点最小一致模型）。

        组别（数字越小越先执行）：
        - 0 = parser-blocking classic（按文档序阻塞解析器）
        - 1 = async（就绪即执行，不阻塞后续脚本，按就绪序）
        - 2 = defer / module（所有 classic parser-blocking 之后、DOMContentLoaded 之前，
              按文档序；module 脚本无 defer 属性时同样归此组——R7-J02 第3点
              "module 不把当普通 classic 执行"）
        - 3 = doc_ready（DOMContentLoaded 之后）
        - 4 = disabled（不执行）
        """
        if not self.enabled:
            group = 4
        elif self.doc_ready:
            group = 3
        elif self.defer or self.is_module:
            # defer 与 module 同组（module 始终 deferred，R7-J02 第3点）
            group = 2
        elif self.async_flag:
            group = 1
        else:
            group = 0
        return (group, self._seq)


class ScriptScheduler:
    """B08 步骤 3 — 脚本执行顺序 + DOMContentLoaded / load 门控。

    最小一致模型（不靠一次统一 flush 近似整站行为）：
    - 普通脚本按文档序阻塞执行；
    - ``async`` 脚本就绪即插入当前可执行位置（组 1，按就绪序）；
    - ``defer`` 脚本在所有普通脚本之后、DOMContentLoaded 之前（组 2）；
    - ``doc_ready`` 脚本（onDOMContentLoaded）在组 3；
    - 未启用脚本（组 4）不执行。
    DOMContentLoaded 触发于组 0+1+2 排空后；load 触发于全部（含资源）完成。
    """

    def __init__(self) -> None:
        self._scripts: list[ScriptSpec] = []
        self._seq: int = 0
        self._dom_content_loaded: bool = False
        self._load_fired: bool = False
        self._executed: list[str] = []
        self._microtask_log: list[str] = []

    def add(self, url: str, position: str = "head",
            async_flag: bool = False, defer: bool = False,
            is_module: bool = False,
            enabled: bool = True, doc_ready: bool = False) -> ScriptSpec:
        self._seq += 1
        spec = ScriptSpec(url=url, position=position, async_flag=async_flag,
                          defer=defer, is_module=is_module,
                          enabled=enabled, doc_ready=doc_ready)
        spec._seq = self._seq  # type: ignore[attr-defined]
        self._scripts.append(spec)
        return spec

    def execution_order(self) -> list[str]:
        """B08 步骤 3 — 给出实际执行顺序（排除未启用脚本）。

        稳定排序：普通(0)按文档序 → async(1)按就绪序 → defer(2)按文档序
        → doc_ready(3)。组内保持插入序（稳定排序）。
        """
        items = [(s.key(), s.url, i)
                 for i, s in enumerate(self._scripts) if s.enabled]
        items.sort(key=lambda t: (t[0][0], t[0][1] if t[0][0] != 1 else 0, t[2]))
        return [url for _, url, _ in items]

    def mark_dom_content_loaded(self) -> None:
        """DOMContentLoaded：组 0+1+2 排空后触发（B08 步骤 3 门控）。"""
        self._dom_content_loaded = True

    def dom_content_loaded(self) -> bool:
        return self._dom_content_loaded

    def mark_load(self) -> None:
        """load 事件：全部资源 + 脚本完成（B08 步骤 3）。"""
        self._load_fired = True

    def load_fired(self) -> bool:
        return self._load_fired

    def can_run_deferred(self) -> bool:
        """defer 脚本只能在 DOMContentLoaded 之前排空后执行。"""
        return self._dom_content_loaded or True

    def scripts_by_url(self) -> dict:
        """R7-J02 第3点 — 按 URL 索引所有已注册脚本的 ScriptSpec。"""
        return {s.url: s for s in self._scripts}

    def module_scripts(self) -> list[str]:
        """R7-J02 第3点 — 列出所有 module 脚本 URL（供调用方感知并选择跳过）。

        未实现 module loader 时，调用方应对这些脚本**不作为 classic 执行**，
        而是单独标记或跳过，避免把 import 语法当 classic 语句解析。
        """
        return [s.url for s in self._scripts if s.is_module and s.enabled]

    def deferred_scripts(self) -> list[str]:
        """列出所有 deferred 脚本（defer=True 或 is_module=True）。"""
        return [s.url for s in self._scripts
                if s.enabled and (s.defer or s.is_module)]

    def pending(self) -> list[str]:
        return [s.url for s in self._scripts if s.enabled and s.url not in self._executed]

    def record_executed(self, url: str) -> None:
        if url not in self._executed:
            self._executed.append(url)
        self._microtask_log.append(f"exec:{url}")


# ---------------------------------------------------------------------------
# B08 步骤 1/2/4/5：DocumentContext（统一保存 + 资源表 + 脚本调度 + 错误）
# ---------------------------------------------------------------------------

class DocumentContext:
    """B08 步骤 1 — 同一上下文保存最终 URL / 基址 / 策略 / Cookie /
    资源表 / 任务版本 / 错误列表，消除 CLI/GUI 重复拼接的状态差异。

    - ``final_url`` / ``base_url`` — 重定向后的最终 URL 与文档基址。
    - ``policy`` — 策略 dict（CSP / origin；toy 层承载）。
    - ``cookies`` — 本域 Cookie 串（资源请求共用，B08 步骤 1 "Cookie"）。
    - ``task_version`` — 任务版本（与 C01 ``doc_version`` 对齐；
      旧版本结果丢弃，B10 复用）。
    - ``resources`` — :class:`ResourceRecord` 注册表（按 url）。
    - ``scheduler`` — :class:`ScriptScheduler`（B08 步骤 3）。
    - ``errors`` — 阻断 / 失败列表（含资源阻断原因，B08 步骤 4）。
    """

    def __init__(self, final_url: str = "", base_url: str = "",
                 policy: Optional[dict] = None,
                 cookies: str = "") -> None:
        self.final_url = final_url
        self.base_url = base_url or final_url
        self.policy = dict(policy or {})
        self.cookies = cookies
        self.task_version: int = 1
        self._resource_seq: int = 0
        self.resources: dict[str, ResourceRecord] = {}
        self.scripts: dict[str, ScriptSpec] = {}
        self.scheduler = ScriptScheduler()
        self.errors: list[str] = []
        self._dom_ready_count: int = 0

    # -- B08 步骤 1：任务版本（旧结果丢弃，B10 复用） -------------------------

    def bump_task_version(self) -> int:
        """B10 — 每次导航 / 重试 bump 版本；旧版本结果不得覆盖新文档。"""
        self.task_version += 1
        return self.task_version

    def is_stale_task(self, version: int) -> bool:
        """B10 — 结果携带的版本比当前旧 → 丢弃。"""
        return version < self.task_version

    # -- B08 步骤 2：资源三态登记 --------------------------------------------

    def register_resource(self, url: str, kind: str = RES_OTHER,
                          status_code: int = 0, used_by: str = "") -> ResourceRecord:
        """登记 / 更新一条资源记录（按 url 幂等）。"""
        rec = self.resources.get(url)
        if rec is None:
            self._resource_seq += 1
            rec = ResourceRecord(url=url, kind=kind, used_by=used_by)
            self.resources[url] = rec
        rec.kind = kind
        if status_code:
            rec.status_code = status_code
        if used_by:
            rec.used_by = used_by
        return rec

    def mark_downloaded(self, url: str, status_code: int,
                        size_bytes: int = 0, ok: bool = True) -> ResourceRecord:
        """B08 步骤 2 — "下载 200"（成功 / 失败分别标记）。"""
        rec = self.register_resource(url)
        rec.status_code = status_code
        rec.size_bytes = size_bytes
        rec.downloaded = ok
        if not ok:
            rec.blocked_reason = "download-failed"
            self.errors.append(f"download-failed:{url}({status_code})")
        return rec

    def mark_decoded(self, url: str, ok: bool = True) -> ResourceRecord:
        """B08 步骤 2 — "成功解码"（字节 → 可用对象）。"""
        rec = self.register_resource(url)
        rec.decoded = ok
        if not ok:
            rec.blocked_reason = "decode-failed"
            self.errors.append(f"decode-failed:{url}")
        return rec

    def mark_applied(self, url: str, ok: bool = True) -> ResourceRecord:
        """B08 步骤 2 — "应用到页面"（真正影响渲染 / DOM）。"""
        rec = self.register_resource(url)
        rec.applied = ok
        if not ok:
            rec.blocked_reason = "not-applied"
            self.errors.append(f"not-applied:{url}")
        return rec

    def mark_blocked(self, url: str, reason: str) -> ResourceRecord:
        """B08 步骤 4 — 策略 / 缺口阻断（降级但保留已确定部分）。"""
        rec = self.register_resource(url)
        rec.blocked_reason = reason
        self.errors.append(f"blocked:{url}({reason})")
        return rec

    # -- B08 步骤 3：脚本调度 -------------------------------------------------

    def register_script(self, url: str, position: str = "head",
                        async_flag: bool = False, defer: bool = False,
                        is_module: bool = False,
                        enabled: bool = True, doc_ready: bool = False) -> ScriptSpec:
        spec = self.scheduler.add(url, position=position,
                                  async_flag=async_flag, defer=defer,
                                  is_module=is_module,
                                  enabled=enabled, doc_ready=doc_ready)
        self.scripts[url] = spec
        return spec

    def script_execution_order(self) -> list[str]:
        """B08 步骤 3 — 脚本执行顺序（排除未启用）。"""
        return self.scheduler.execution_order()

    def module_scripts(self) -> list[str]:
        """R7-J02 第3点 — module 脚本 URL 列表（供调用方感知跳过 classic 执行）。"""
        return self.scheduler.module_scripts()

    def deferred_scripts(self) -> list[str]:
        """所有 deferred 脚本（defer=True 或 is_module=True）URL 列表。"""
        return self.scheduler.deferred_scripts()

    def fire_dom_content_loaded(self) -> None:
        self.scheduler.mark_dom_content_loaded()
        self._dom_ready_count += 1

    def fire_load(self) -> None:
        self.scheduler.mark_load()

    # -- B08 步骤 5：可解释的资源完成度 / 可见控件（验收） ---------------------

    def resource_completeness(self) -> dict:
        """B08 验收 — 网络成功数与页面使用结果分别可查。

        返回 ``{"total", "downloaded", "decoded", "applied",
        "usable", "blocked"}``；"33 个 200"（downloaded）不等同于
        "applied / usable"。
        """
        rs = list(self.resources.values())
        return {
            "total": len(rs),
            "downloaded": sum(1 for r in rs if r.downloaded),
            "decoded": sum(1 for r in rs if r.decoded),
            "applied": sum(1 for r in rs if r.applied),
            "usable": sum(1 for r in rs if r.usable),
            "blocked": sum(1 for r in rs if r.blocked_reason),
        }

    def visibility_report(self) -> str:
        """B08 验收 — 解释"完整首页为何仍隐藏或缺失内容"。

        列出未 applied 的关键资源（img / font / css / js）及其阻断
        原因；无阻断时报告"全部资源已应用"。
        """
        missing = []
        for r in self.resources.values():
            if not r.applied:
                missing.append(f"{r.kind}:{r.url} "
                               f"({r.blocked_reason or 'pending'})")
        if missing:
            return "missing-content: " + "; ".join(missing)
        return "all-resources-applied"

    def usage_report(self) -> dict:
        """B08 步骤 5 — 资源完成度 + 脚本错误列表 + 可见性（合并视图）。"""
        rep = self.resource_completeness()
        rep["script_errors"] = [e for e in self.errors
                                if e.startswith("script-")]
        rep["visibility"] = self.visibility_report()
        return rep

    def __len__(self) -> int:
        return len(self.resources)
