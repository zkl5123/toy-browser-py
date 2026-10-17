"""B03 纯逻辑核心 — 脚本异常隔离与统一错误分类。

实施文档 B03（"隔离脚本失败，保留文档和明确错误来源"）的纯逻辑层：
* 区分脚本内部可捕获 throw 与跨脚本边界的未捕获异常；
* 建立统一错误记录（脚本 URL/索引、错误类别、位置和导航 ID）；
* 单个脚本失败后继续后续独立脚本；
* 保持 DOM、已成功加载的 CSS 和资源可用于渲染；
  仅因一个第三方或统计脚本失败时，不替换整页为网络错误页。

GUI / CLI 分开处理 NetworkError、PolicyError、ScriptError 和 RenderError；
状态栏允许"已显示，部分脚本失败"，诊断区展示详情，
CLI 返回可检查的结构化结果。

纯逻辑 / 可离线测试；不依赖 Tk、不依赖真实网络。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ----------------------------------------------------------------------
# 错误类别
# ----------------------------------------------------------------------
class ErrorCategory(str, Enum):
    """B03 步骤 4 — 四类错误，GUI 分开处理。"""
    NETWORK = "network"      # 主文档 / 资源下载失败（连接、超时、DNS）
    POLICY = "policy"        # CSP / nonce / 同源策略拒绝
    SCRIPT = "script"        # JS 解析 / 运行 / 未捕获 throw
    RENDER = "render"        # 布局 / 绘制 / 光栅化阶段异常


class ScriptPhase(str, Enum):
    """B03 步骤 2 — 脚本执行边界（哪个阶段失败）。"""
    FETCH = "fetch"          # 外部脚本下载 / 解码
    PARSE = "parse"          # JS 词法 / 语法解析
    RUNTIME = "runtime"      # 求值 / 调用
    EVENT = "event"         # 事件处理器 / 定时器回调


@dataclass
class ScriptErrorRecord:
    """B03 步骤 1 — 统一错误记录（含脚本 URL/索引、类别、位置、导航 ID）。

    * ``script_id``：外部脚本的 URL；内联脚本用 ``"<inline>#N"``（N 是
      文档内 script 元素序号，从 0 起）。
    * ``category``：SCRIPT 之外的类别（NETWORK / POLICY / RENDER）
      通常 ``script_id`` 为资源 URL 或 None。
    * ``position``：词法 / 语法位置（行 / 列 / 偏移），运行期错误
      可为 None。
    * ``nav_id``：关联的导航 ID（C01 文档版本），供跨标签 / 过期丢弃。
    * ``message``：人类可读错误摘要。
    * ``caught``：脚本内部 try/catch 捕获的 throw 为 True（可恢复）；
      跨脚本边界未捕获为 False（该脚本中止，后续独立脚本继续）。
    """
    nav_id: int
    script_id: str
    category: ErrorCategory
    phase: ScriptPhase
    message: str
    position: Optional[dict] = None   # {"line":, "col":, "offset":}
    caught: bool = False
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        """一行摘要（状态栏 / CLI 输出）。"""
        loc = ""
        if self.position:
            loc = f" @L{self.position.get('line', '?')}"
        return (f"[{self.category.value}][{self.phase.value}] "
                f"{self.script_id}{loc}: {self.message}")


# ----------------------------------------------------------------------
# 页面级错误聚合器
# ----------------------------------------------------------------------
class PageErrorAggregator:
    """B03 步骤 3/4/5 — 按导航 ID 聚合脚本错误。

    * ``record``：登记一条错误（脚本边界 / 资源 / 渲染阶段）。
    * ``continues_after_failure``：判断后续独立脚本是否仍应执行
      （"单个脚本失败后继续后续独立脚本"）—— 只有 SCRIPT + 未捕获
      才中止 *该脚本*，后续独立脚本照常。
    * ``page_is_usable``：判断整页是否仍可渲染（"仅因一个第三方或
      统计脚本失败时，不替换整页为网络错误页"）—— 只要主文档已
      提交且 DOM 可遍历，即使存在脚本失败页仍可用（部分降级）。
    * ``status_line``：状态栏文案（"已显示，部分脚本失败" vs "完整"）。
    * ``cli_result``：CLI 可检查的结构化结果（dict）。
    """

    def __init__(self, nav_id: int):
        self.nav_id = nav_id
        self.records: list[ScriptErrorRecord] = []
        self.doc_committed: bool = False      # 主文档已提交（C01）
        self.dom_usable: bool = True          # DOM 可遍历（渲染前置）
        self.render_ok: bool = True           # 布局 / 绘制未崩溃

    # ---- 登记 ----
    def record(self, rec: ScriptErrorRecord) -> None:
        self.records.append(rec)
        # 渲染阶段崩溃 → 页面不可用
        if rec.category == ErrorCategory.RENDER:
            self.render_ok = False

    def record_uncaught_script(self, script_id: str,
                               message: str,
                               phase: ScriptPhase = ScriptPhase.RUNTIME,
                               position: Optional[dict] = None,
                               caught: bool = False) -> ScriptErrorRecord:
        """便捷：登记一条未捕获（或捕获）脚本错误。"""
        rec = ScriptErrorRecord(
            nav_id=self.nav_id,
            script_id=script_id,
            category=ErrorCategory.SCRIPT,
            phase=phase,
            message=message,
            position=position,
            caught=caught,
        )
        self.record(rec)
        return rec

    def mark_doc_committed(self, dom_usable: bool = True) -> None:
        self.doc_committed = True
        self.dom_usable = dom_usable

    # ---- 判定 ----
    def continues_after_failure(self, next_script_id: str) -> bool:
        """B03 步骤 2 — 下一个独立脚本是否仍应执行。

        单脚本失败只中止 *该脚本*；后续独立脚本（不同 script_id）
        照常。同一 script_id 的失败意味着该脚本已中止，不应重跑。"""
        last = self.records[-1] if self.records else None
        if last is None:
            return True
        if last.category != ErrorCategory.SCRIPT:
            return True
        if last.caught:
            # 内部 try/catch 捕获 → 该脚本可能还有后续（同一脚本 ID）
            # 但 B03 语义：跨脚本边界才隔离；同脚本内捕获不影响后续脚本。
            return True
        # 未捕获 SCRIPT 错误：只中止 *该* 脚本；不同 script_id 继续。
        return last.script_id != next_script_id

    def page_is_usable(self) -> bool:
        """B03 步骤 3 — 整页是否仍可渲染（不替换为网络错误页）。"""
        return (self.doc_committed and self.dom_usable
                and self.render_ok)

    def has_uncaught_script_errors(self) -> bool:
        return any(
            r.category == ErrorCategory.SCRIPT and not r.caught
            for r in self.records
        )

    # ---- 输出 ----
    def status_line(self) -> str:
        """B03 步骤 4 — 状态栏文案。"""
        if not self.page_is_usable():
            if not self.doc_committed:
                return "文档未提交"
            if not self.dom_usable:
                return "DOM 不可用"
            return "渲染失败"
        if self.has_uncaught_script_errors():
            n = sum(1 for r in self.records
                    if r.category == ErrorCategory.SCRIPT and not r.caught)
            return f"已显示，{n} 个脚本失败"
        return "已显示"

    def cli_result(self) -> dict:
        """B03 步骤 4 — CLI 可检查的结构化结果。"""
        return {
            "nav_id": self.nav_id,
            "page_usable": self.page_is_usable(),
            "status": self.status_line(),
            "errors": [
                {
                    "script_id": r.script_id,
                    "category": r.category.value,
                    "phase": r.phase.value,
                    "message": r.message,
                    "position": r.position,
                    "caught": r.caught,
                }
                for r in self.records
            ],
        }
