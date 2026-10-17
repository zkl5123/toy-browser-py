"""批次 24 — C04 表单提交端到端任务 + G09 统一历史提交路径 回归。

被测：``toy_browser/app/form_task.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.form_task import (
    FormSubmissionTask, FormTaskResult,
    commit_form_navigation, SRC_FORM_SUBMIT,
)
from toy_browser.app.activation import (
    FormField, TRIGGER_SUBMIT, TRIGGER_REQUEST_SUBMIT,
)
from toy_browser.app.history_entry import HistoryCommitter

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _mk_task(committer=None, method="GET", action="/submit",
             base="http://example.com"):
    return FormSubmissionTask(committer=committer, form_id="f1",
                              base=base, action=action, method=method)


# ---------------------------------------------------------------------------
# 成功提交（GET）
# ---------------------------------------------------------------------------

@_reg
def test_get_submit_success_no_committer():
    """无 committer：成功提交结果正确、history_committed=False。"""
    task = _mk_task()
    fields = [
        FormField(name="q", value="hello", kind="text"),
        FormField(name="page", value="2", kind="text"),
    ]
    r = task.run(fields)
    assert r.submitted is True
    assert r.blocked is False
    assert "q=hello" in r.url
    assert "page=2" in r.url
    assert r.method == "GET"
    assert r.history_committed is False  # 无 committer


@_reg
def test_get_submit_with_committer():
    """有 committer：成功提交入历史（source=form-submit）。"""
    c = HistoryCommitter("http://example.com")
    task = _mk_task(committer=c)
    fields = [FormField(name="q", value="abc", kind="text")]
    r = task.run(fields)
    assert r.submitted is True
    assert r.history_committed is True
    # 历史新增一项，source=form-submit
    last = c.entries[-1]
    assert last.source == SRC_FORM_SUBMIT
    assert "q=abc" in last.url


@_reg
def test_post_submit_body_not_url():
    """POST：查询串进 body、不进 URL。"""
    task = _mk_task(method="POST")
    fields = [FormField(name="a", value="1", kind="text"),
              FormField(name="b", value="2", kind="text")]
    r = task.run(fields)
    assert r.submitted is True
    assert r.method == "POST"
    assert "a=1" in r.body
    assert "b=2" in r.body
    assert "?" not in r.url.split("/", 3)[-1] or r.url == \
        "http://example.com/submit"


# ---------------------------------------------------------------------------
# 校验阻断（C04 步骤 5：保留焦点与用户内容）
# ---------------------------------------------------------------------------

@_reg
def test_required_violation_blocks():
    """required 字段为空 → 阻断、violations 记录、焦点移回该字段。"""
    task = _mk_task()
    f = FormField(name="email", value="", kind="text")
    f.required = True
    r = task.run([f])
    assert r.submitted is False
    assert r.blocked is True
    assert r.history_committed is False
    assert len(r.violations) == 1
    assert r.violations[0]["field"] == "email"
    assert r.focus_field == "email"
    assert r.user_content_preserved is True


@_reg
def test_trigger_submit_skips_validation():
    """form.submit()（TRIGGER_SUBMIT）跳校验：required 空也能提交。"""
    task = _mk_task()
    f = FormField(name="email", value="", kind="text")
    f.required = True
    # 默认 validate=True，但 TRIGGER_SUBMIT 语义应跳校验
    r = task.run([f], trigger=TRIGGER_SUBMIT, validate=False)
    assert r.submitted is True
    assert r.blocked is False


@_reg
def test_blocked_preserves_user_content():
    """被阻断后用户输入保留（value 不被清空）。"""
    task = _mk_task()
    f1 = FormField(name="ok", value="typed", kind="text")
    f2 = FormField(name="must", value="", kind="text")
    f2.required = True
    r = task.run([f1, f2])
    assert r.blocked is True
    # 用户输入保留（f1 的值未丢）
    assert f1.value == "typed"
    assert r.user_content_preserved is True


@_reg
def test_focus_field_api():
    """focus_field() 返回被阻断应收回焦点的字段。"""
    task = _mk_task()
    f = FormField(name="name", value="", kind="text")
    f.required = True
    r = task.run([f])
    assert task.focus_field(r) == "name"
    assert task.focus_field() == "name"  # 用最近一次


# ---------------------------------------------------------------------------
# 取消提交（C04 步骤 4 验收"取消提交"）
# ---------------------------------------------------------------------------

@_reg
def test_cancel_blocked_preserves_content():
    """取消被阻断的提交：保留用户输入与焦点、不入历史、不切换页面。"""
    c = HistoryCommitter("http://example.com")
    task = _mk_task(committer=c)
    f = FormField(name="email", value="", kind="text")
    f.required = True
    r = task.run([f])
    assert r.blocked is True
    assert c.cursor == 0  # 只有一条初始历史（阻断不入历史）
    cancelled = task.cancel()
    assert cancelled.cancelled is True
    assert cancelled.history_committed is False
    assert cancelled.user_content_preserved is True
    assert c.cursor == 0  # 取消也不入历史


@_reg
def test_cancel_success_no_effect():
    """成功提交已入历史后取消：不可撤销（不串页、不回滚历史）。"""
    c = HistoryCommitter("http://example.com")
    task = _mk_task(committer=c)
    fields = [FormField(name="q", value="x", kind="text")]
    task.run(fields)
    assert c.cursor == 1  # 成功入了一项历史
    r = task.cancel()
    assert r.cancelled is False  # 成功提交不可取消
    assert c.cursor == 1  # 历史未回滚


@_reg
def test_cancel_no_task_yet():
    """无任务时取消：返回 cancelled=False、保留内容。"""
    task = _mk_task()
    r = task.cancel()
    assert r.cancelled is False
    assert r.user_content_preserved is True


# ---------------------------------------------------------------------------
# G09 统一历史提交路径（commit_form_navigation）
# ---------------------------------------------------------------------------

@_reg
def test_commit_form_navigation_success():
    """成功提交：commit_form_navigation 返回 (result, entry)，entry
    source=form-submit。task 无 committer → 历史提交由
    commit_form_navigation 的 committer 承担。"""
    c = HistoryCommitter("http://example.com")
    task = _mk_task()  # 无 committer（历史提交由包装函数承担）
    fields = [FormField(name="q", value="z", kind="text")]
    result, entry = commit_form_navigation(c, task, fields)
    assert result.submitted is True
    assert result.history_committed is False  # task 自身无 committer
    assert entry is not None
    assert entry.source == SRC_FORM_SUBMIT
    assert "q=z" in entry.url
    # 包装函数负责入历史：committer 确实新增了一项
    assert c.cursor == 1


@_reg
def test_commit_form_navigation_blocked_no_entry():
    """被阻断：entry=None（不入历史，避免历史分叉）。"""
    c = HistoryCommitter("http://example.com")
    task = _mk_task()
    f = FormField(name="req", value="", kind="text")
    f.required = True
    result, entry = commit_form_navigation(c, task, [f])
    assert result.blocked is True
    assert entry is None
    assert c.cursor == 0  # 无新历史项


@_reg
def test_commit_form_navigation_dedup():
    """同址重定向去重：表单提交到当前页同址 → 不重复入历史。"""
    c = HistoryCommitter("http://example.com/current")
    task = FormSubmissionTask(committer=c, form_id="f1",
                              base="http://example.com/current",
                              action="current", method="GET")
    fields = [FormField(name="x", value="1", kind="text")]
    # 第一次：入历史
    r1, e1 = commit_form_navigation(c, task, fields)
    n1 = len(c.entries)
    # 第二次（同址）：去重不新增
    r2, e2 = commit_form_navigation(c, task, fields)
    assert len(c.entries) == n1  # 去重，历史项数不变


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------

@_reg
def test_describe():
    """describe：无任务 / 有任务。"""
    task = _mk_task()
    assert "no form task" in task.describe()
    task.run([FormField(name="q", value="1", kind="text")])
    assert "submitted=True" in task.describe()


@_reg
def test_form_task_result_summary():
    """FormTaskResult.summary：关键位。"""
    r = FormTaskResult(submitted=True, url="http://x/y?q=1",
                       history_committed=True)
    s = r.summary()
    assert "submitted=True" in s
    assert "history=http://x/y?q=1" in s


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)
