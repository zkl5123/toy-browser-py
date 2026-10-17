"""批次 24 — C04 表单提交端到端任务编排 + G09 统一历史提交路径（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md``
- **C04 步骤 4/5**："编辑状态使用 G03 的 value 和 selection；提交
  复用现有 serialize/validate/form_submit，不复制序列化规则"；
  "校验错误显示在对应控件或可访问提示中，失效输入保持用户内容与
  焦点"（批次 18 B07 落了 :func:`build_form_submission` 的纯逻辑
  提交 + 校验阻断；本批把提交接成**端到端任务**：提交 → 校验 →
  成功提交入历史 / 被阻断保留焦点与用户内容 + 取消提交不丢输入）。
- **G09 步骤 3**："统一地址栏、链接、书签和文件打开的历史提交路径，
  明确重定向只形成哪一个历史项"（批次 8 落了 :class:`HistoryCommitter`
  统一入口；本批把表单成功提交也接进同一历史入口，使"表单提交 = 一
  个历史项（携带最终 URL）"与地址栏 / 链接 / 书签 / 文件打开同语义，
  不产生历史分叉）。

与已有模块的关系（本模块**不重复**实现，只做端到端任务编排）：
- **B07**（:func:`toy_browser.app.activation.build_form_submission` /
  :class:`FormField`）已有表单提交纯逻辑（成功控件排除 + GET/POST
  查询 / body + required 校验阻断）；本模块的
  :class:`FormSubmissionTask` 调用它，不复制序列化规则（C04 步骤 4
  "提交复用现有 serialize/validate/form_submit"）。
- **G09**（:class:`toy_browser.app.history_entry.HistoryCommitter` /
  :class:`HistoryEntry`）已有统一历史提交入口（单一入口 + 游标 + 去
  重 + 重定向只形成一个历史项）；本模块把表单成功提交经
  ``commit`` 入历史（``source="form-submit"``，携带最终 URL），被阻
  断 / 被取消的提交**不入历史**（不新增历史项、不串页）。
- **C04 步骤 5**（"失效输入保持用户内容与焦点"）：校验被阻断时
  返回 :class:`FormTaskResult.blocked=True` + ``violations``，调用
  方据此把焦点移回第一个违规控件、保留已输入的 value（不清空）。
  :meth:`FormSubmissionTask.focus_field` 记录应聚焦的字段（稳定
  node_id，与 G11 键盘路由 / C01 稳定身份配合）。
- **取消提交**（C04 步骤 4 验收"取消提交"）：
  :meth:`FormSubmissionTask.cancel` 撤销一次被阻断 / 被 preventDefault
  的提交，保留用户输入与焦点（不清空 value、不切换页面）。

GUI 侧（Tk 真实表单控件 / 焦点画布滚动 / 提交后导航）需活显示环境；
本批为**纯逻辑核心**，不触碰 tkinter。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Callable

from toy_browser.app.activation import (
    FormField, build_form_submission, TRIGGER_SUBMIT,
    TRIGGER_REQUEST_SUBMIT,
)
from toy_browser.app.history_entry import (
    HistoryCommitter, HistoryEntry, SRC_ADDRESS, SRC_LINK,
    SRC_BOOKMARK, SRC_FILE,
)

# 表单提交的历史来源标签（G09 步骤 3：统一历史入口的来源标签之一）
SRC_FORM_SUBMIT = "form-submit"


# ---------------------------------------------------------------------------
# 表单提交任务结果（C04 步骤 4/5 验收 + G09 历史集成）
# ---------------------------------------------------------------------------

@dataclass
class FormTaskResult:
    """C04 — 一次表单提交任务的纯逻辑结果。

    - ``submitted`` — 是否真正提交了（成功入历史 / 发起导航）。
    - ``url`` / ``method`` / ``body`` — 提交目标与请求体
      （复用 :func:`build_form_submission` 的结果）。
    - ``blocked`` — 是否被校验阻断（required 违规 / preventDefault）；
      被阻断时 ``violations`` 列出违规字段，焦点应移回第一个违规
      控件、保留用户输入（C04 步骤 5）。
    - ``cancelled`` — 是否被调用方取消（:meth:`FormSubmissionTask.cancel`）。
    - ``history_committed`` — 是否已入历史（成功提交才入；被阻断 /
      被取消不入历史，避免历史分叉 / 串页）。
    - ``focus_field`` — 被阻断时应收回焦点的字段名（稳定 node_id
      由调用方经 C01 解析；None = 无需移焦点）。
    - ``user_content_preserved`` — 取消 / 被阻断后用户输入是否保留
      （C04 步骤 5 "失效输入保持用户内容"）。
    """
    submitted: bool = False
    url: str = ""
    method: str = "GET"
    body: str = ""
    blocked: bool = False
    cancelled: bool = False
    violations: list = field(default_factory=list)
    history_committed: bool = False
    focus_field: Optional[str] = None
    user_content_preserved: bool = True

    def summary(self) -> str:
        bits = [f"submitted={self.submitted}"]
        if self.blocked:
            bits.append(f"blocked({len(self.violations)} violations)")
        if self.cancelled:
            bits.append("cancelled")
        if self.history_committed:
            bits.append(f"history={self.url}")
        if self.focus_field:
            bits.append(f"focus={self.focus_field}")
        return " ".join(bits)


# ---------------------------------------------------------------------------
# 表单提交端到端任务（C04 步骤 4/5 + G09 历史集成）
# ---------------------------------------------------------------------------

class FormSubmissionTask:
    """C04 — 表单提交端到端任务编排（纯逻辑核心）。

    把"收集字段 → 校验 → 提交 → 入历史 / 被阻断保留焦点与内容"
    编排成一个可测试的流程，复用 B07 的 :func:`build_form_submission`
    （不复制序列化规则）与 G09 的 :class:`HistoryCommitter`
    （统一历史入口）。

    职责：
    - :meth:`run` — 执行一次提交：调 :func:`build_form_submission`；
      成功 → 经 ``committer.commit`` 入历史（``source=form-submit``，
      携带最终 URL）；被校验阻断 → 返回 :class:`FormTaskResult`
      的 ``blocked=True`` + ``violations`` + ``focus_field``（第一
      个违规字段），**不入历史**。
    - :meth:`cancel` — 取消一次被阻断 / 被 preventDefault 的提交：
      保留用户输入与焦点（``user_content_preserved=True``），不切换
      页面、不入历史（C04 步骤 4 验收"取消提交"）。
    - :meth:`focus_field` — 被阻断时应收回焦点的字段（稳定
      node_id，与 C01 稳定身份 / G11 键盘路由配合）。

    参数：
    - ``committer`` — 可选 :class:`HistoryCommitter`；提供时成功提交
      才入历史。不提供（None）则只构造提交结果、不操作历史
      （纯提交路径）。
    """

    def __init__(self, committer: Optional[HistoryCommitter] = None,
                 form_id: str = "",
                 base: str = "",
                 action: str = "",
                 method: str = "GET") -> None:
        self.committer = committer
        self.form_id = form_id
        self.base = base
        self.action = action
        self.method = method
        self._last_result: Optional[FormTaskResult] = None

    # -- 执行提交 -----------------------------------------------------------

    def run(self, fields: list[FormField],
            trigger: str = TRIGGER_REQUEST_SUBMIT,
            validate: bool = True) -> FormTaskResult:
        """执行一次表单提交任务。

        - 成功 → ``submitted=True`` + 经 committer 入历史（携带最终
          URL，``source=form-submit``）；
        - 被校验阻断（required 违规）→ ``blocked=True`` +
          ``violations`` + ``focus_field``（第一个违规字段），**不入
          历史**；保留用户输入（``user_content_preserved=True``）。

        *trigger* / *validate* 透传给 :func:`build_form_submission`
        （B07 步骤 4 的 submit / requestSubmit 差异：``TRIGGER_SUBMIT``
        跳校验，``TRIGGER_REQUEST_SUBMIT`` / Enter 走校验）。
        """
        submission = build_form_submission(
            fields, form_id=self.form_id, action=self.action,
            base=self.base, method=self.method,
            trigger=trigger, validate=validate)

        result = FormTaskResult(
            url=submission.get("url", ""),
            method=submission.get("method", "GET"),
            body=submission.get("body", ""),
        )

        if submission.get("blocked"):
            # 校验阻断：保留用户输入 + 焦点移回第一个违规字段（C04
            # 步骤 5 "失效输入保持用户内容与焦点"）。不入历史。
            result.blocked = True
            result.violations = list(submission.get("violations", []))
            result.focus_field = self._first_violation_field(
                submission.get("violations", []))
            result.user_content_preserved = True
            self._last_result = result
            return result

        # 成功提交
        result.submitted = True
        result.user_content_preserved = True
        if self.committer is not None and result.url:
            idx = self.committer.commit(
                result.url, source=SRC_FORM_SUBMIT,
                final_url=result.url, dedup=True)
            result.history_committed = True
        self._last_result = result
        return result

    # -- 取消提交（C04 步骤 4 验收"取消提交"） -----------------------------

    def cancel(self) -> FormTaskResult:
        """取消最近一次被阻断 / 被 preventDefault 的提交。

        - 保留用户输入与焦点（``user_content_preserved=True``）；
        - 不切换页面、不入历史（被取消的提交不产生历史项）；
        - 返回上一次任务的结果（``cancelled=True`` 置位）。

        若上一次任务是成功提交（已入历史），取消无意义——返回原结果
        （``cancelled`` 保持 False）。"""
        last = self._last_result
        if last is None:
            return FormTaskResult(cancelled=False,
                                  user_content_preserved=True)
        if last.submitted and last.history_committed:
            # 成功提交已入历史，不可取消（不串页、不回滚历史）
            last.cancelled = False
            return last
        last.cancelled = True
        last.submitted = False
        last.history_committed = False
        last.user_content_preserved = True
        return last

    # -- 焦点（C04 步骤 5）-------------------------------------------------

    def focus_field(self, result: Optional[FormTaskResult] = None) -> \
            Optional[str]:
        """被阻断时应收回焦点的字段名（稳定 node_id 由调用方经
        C01 解析；None = 无需移焦点）。"""
        r = result if result is not None else self._last_result
        if r is None:
            return None
        return r.focus_field

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _first_violation_field(violations: list) -> Optional[str]:
        for v in violations:
            if v.get("field"):
                return v["field"]
        return None

    # -- 诊断 ---------------------------------------------------------------

    def describe(self) -> str:
        last = self._last_result
        if last is None:
            return "no form task yet"
        return last.summary()


# ---------------------------------------------------------------------------
# G09 步骤 3 — 统一历史提交路径（表单提交 + 重定向语义）
# ---------------------------------------------------------------------------

def commit_form_navigation(committer: HistoryCommitter,
                           form_task: FormSubmissionTask,
                           fields: list[FormField],
                           final_url: Optional[str] = None,
                           trigger: str = TRIGGER_REQUEST_SUBMIT,
                           validate: bool = True) -> tuple[FormTaskResult,
                                                           Optional[HistoryEntry]]:
    """G09 步骤 3 — 把一次**成功的**表单提交接进统一历史入口。

    返回 ``(FormTaskResult, 入历史的 HistoryEntry | None)``：

    - 成功提交 → 经 :class:`HistoryCommitter.commit` 入一个历史项
      （``source=form-submit``，携带 *final_url*（默认取提交结果的
      URL）；重定向 / 同址去重语义与地址栏 / 链接 / 书签 / 文件打开
      一致——**一次导航 = 一个历史项**，中间跳转不重复入栈）。
    - 被校验阻断 / 被取消 → 不入历史（返回 entry=None），避免历史
      分叉 / 串页（G09 验收"不串页、不重复历史项"）。

    本函数是"表单提交路径"的 G09 统一入口封装；底层仍走
    :class:`HistoryCommitter` 的单一 :meth:`commit`（批次 8）。
    """
    result = form_task.run(fields, trigger=trigger, validate=validate)
    entry: Optional[HistoryEntry] = None
    if result.submitted and committer is not None:
        target = final_url if final_url else result.url
        if target:
            idx = committer.commit(
                target, source=SRC_FORM_SUBMIT,
                final_url=target, dedup=True)
            entry = committer.entries[idx]
    return result, entry


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    c = HistoryCommitter("http://example.com")
    task = FormSubmissionTask(committer=c, form_id="f1",
                              base="http://example.com",
                              action="/submit", method="GET")
    fields = [
        FormField(name="q", value="hello", kind="text"),
        FormField(name="page", value="1", kind="text"),
    ]
    res = task.run(fields)
    print(res.summary())
    print("history:", [e.url for e in c.entries])
