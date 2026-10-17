"""B07 — 统一激活、表单提交与链接跳转纯逻辑核心回归。

依据 ``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B07。被测：``toy_browser/app/activation.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.activation import (
    ActivateDecision, FormField, ClickEvent,
    activate_element, parse_link_target, build_form_submission,
    TRIGGER_MOUSE, TRIGGER_ENTER, TRIGGER_SUBMIT, TRIGGER_REQUEST_SUBMIT,
    TARGET_CURRENT, TARGET_BLANK,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# B07 步骤 1：鼠标 / Enter / Space 同一默认动作
# ---------------------------------------------------------------------------

@_reg
def test_b07_link_mouse_navigate():
    """B07 步骤 1 — 链接鼠标点击 → 导航决策。"""
    d = activate_element("a", href="/s?wd=python", base="https://www.baidu.com/",
                         trigger=TRIGGER_MOUSE)
    assert d.action == "navigate"
    assert d.target == "https://www.baidu.com/s?wd=python", f"got {d.target}"
    assert d.url_kind == TARGET_CURRENT


@_reg
def test_b07_link_enter_same_as_mouse():
    """B07 步骤 1 — 链接 Enter 与鼠标进入同一默认动作（导航）。"""
    d = activate_element("a", href="https://target.com/", base="https://baidu.com/",
                         trigger=TRIGGER_ENTER)
    assert d.action == "navigate" and d.target == "https://target.com/"


@_reg
def test_b07_button_focus_no_default_nav():
    """B07 步骤 1 — 非提交 / 非链接控件 → 焦点动作（不导航 / 不提交）。"""
    d = activate_element("div", trigger=TRIGGER_MOUSE)
    assert d.action == "focus" and d.target == ""


# ---------------------------------------------------------------------------
# B07 步骤 1：可取消事件（preventDefault 阻断导航 / 提交）
# ---------------------------------------------------------------------------

@_reg
def test_b07_prevent_default_blocks_navigate():
    """B07 步骤 1 — click 被 preventDefault 时不导航。"""
    def on_click(ev: ClickEvent):
        ev.prevent_default()
    d = activate_element("a", href="https://x.com/", base="https://baidu.com/",
                         trigger=TRIGGER_MOUSE, on_click=on_click)
    assert d.prevented is True and d.action == "none", d.summary()


@_reg
def test_b07_prevent_default_blocks_submit():
    """B07 步骤 1 — 提交被 preventDefault 时不提交。"""
    def on_click(ev: ClickEvent):
        ev.prevent_default()
    fields = [FormField(name="wd", value="python", kind="text")]
    d = activate_element("input", fields=fields, form_id="s", action="/s",
                         base="https://www.baidu.com/", trigger=TRIGGER_MOUSE,
                         on_click=on_click)
    assert d.prevented is True and d.action == "none", d.summary()


# ---------------------------------------------------------------------------
# B07 步骤 2：form owner / successful controls / 禁用 / 未命名排除 / 重复字段 / UTF-8
# ---------------------------------------------------------------------------

@_reg
def test_b07_successful_controls_exclude_disabled_unnamed():
    """B07 步骤 2 — 禁用 / 未命名字段不计入提交。"""
    f_ok = FormField(name="wd", value="python", kind="text")
    f_dis = FormField(name="dbg", value="x", kind="text", disabled=True)
    f_unnamed = FormField(name="", value="y", kind="text")
    f_unchecked = FormField(name="ck", value="on", kind="checkbox", checked=False)
    assert f_ok.is_successful_control() is True
    assert f_dis.is_successful_control() is False, "disabled excluded"
    assert f_unnamed.is_successful_control() is False, "unnamed excluded"
    assert f_unchecked.is_successful_control() is False, "unchecked checkbox excluded"
    d = activate_element("input", fields=[f_ok, f_dis, f_unnamed, f_unchecked],
                         form_id="s", action="/s", base="https://www.baidu.com/",
                         trigger=TRIGGER_MOUSE)
    assert d.query == "wd=python", f"only successful control submitted: {d.query!r}"
    assert len(d.skipped_fields) == 3, f"3 fields skipped: {d.skipped_fields}"


@_reg
def test_b07_duplicate_field_names():
    """B07 步骤 2 — 重复字段名作为重复 k=v 对（不合并）。"""
    fields = [
        FormField(name="k", value="1", kind="checkbox", checked=True),
        FormField(name="k", value="2", kind="checkbox", checked=True),
    ]
    d = activate_element("input", fields=fields, form_id="f", action="/s",
                         base="https://x.com/", trigger=TRIGGER_MOUSE)
    assert d.query == "k=1&k=2", f"duplicate names emit repeated pairs: {d.query!r}"


@_reg
def test_b07_chinese_query_utf8():
    """B07 验收 — 中文查询 URL 编码（UTF-8）。"""
    fields = [FormField(name="wd", value="中文 查询", kind="text")]
    d = activate_element("input", fields=fields, form_id="s", action="/s",
                         base="https://www.baidu.com/", trigger=TRIGGER_MOUSE)
    import urllib.parse
    assert "wd=" in d.query
    decoded = urllib.parse.unquote(d.query.split("wd=", 1)[1])
    assert decoded == "中文 查询", f"chinese query must round-trip: {d.query!r}"


# ---------------------------------------------------------------------------
# B07 步骤 3：action="/s" / 相对 a href / 协议相对 / <base> 基址解析 + target/scheme
# ---------------------------------------------------------------------------

@_reg
def test_b07_relative_action_resolves_to_base():
    """B07 步骤 3 — action="/s" 相对文档基址解析。"""
    d = activate_element("input", fields=[FormField(name="wd", value="python")],
                         form_id="s", action="/s", base="https://www.baidu.com/",
                         trigger=TRIGGER_MOUSE)
    assert d.target.startswith("https://www.baidu.com/s"), d.summary()


@_reg
def test_b07_protocol_relative_href():
    """B07 步骤 3 — 协议相对 //host/… 保留 base 的 scheme。"""
    resolved, kind, ok = parse_link_target("//cdn.example.com/a.js",
                                           "https://site.com/page")
    assert resolved == "https://cdn.example.com/a.js", resolved
    assert kind == TARGET_CURRENT and ok is True


@_reg
def test_b07_target_blank_recorded_not_opened():
    """B07 步骤 3 — target=_blank 记录为新标签（不静默丢弃）。"""
    d = activate_element("a", href="https://x.com/", base="https://baidu.com/",
                         target_attr="_blank", trigger=TRIGGER_MOUSE)
    assert d.url_kind == TARGET_BLANK, f"target=_blank must be flagged: {d.url_kind}"
    assert d.action == "navigate"


@_reg
def test_b07_unsupported_scheme_flagged():
    """B07 步骤 3 — 不支持的 scheme（javascript:/mailto:）明确标记。"""
    _, kind, ok = parse_link_target("javascript:alert(1)", "https://baidu.com/")
    assert ok is False, "javascript: scheme must be flagged unsupported"
    _, _, ok2 = parse_link_target("mailto:a@b.com", "https://baidu.com/")
    assert ok2 is False


# ---------------------------------------------------------------------------
# B07 步骤 4：submit / requestSubmit 与默认 Enter 提交差异
# ---------------------------------------------------------------------------

@_reg
def test_b07_submit_skips_validation():
    """B07 步骤 4 — form.submit() 跳过校验（必填空值不阻断）。"""
    fields = [FormField(name="req", value="", kind="text")]
    fields[0].required = True
    res = build_form_submission(fields, form_id="s", action="/s",
                                base="https://www.baidu.com/",
                                method="GET", trigger=TRIGGER_SUBMIT,
                                validate=False)
    assert res["ok"] is True and res["blocked"] is False, res


@_reg
def test_b07_request_submit_runs_validation():
    """B07 步骤 4 — requestSubmit 走验证（必填空值阻断提交）。"""
    fields = [FormField(name="req", value="", kind="text")]
    fields[0].required = True
    res = build_form_submission(fields, form_id="s", action="/s",
                                base="https://www.baidu.com/",
                                method="GET", trigger=TRIGGER_REQUEST_SUBMIT,
                                validate=True)
    assert res["ok"] is False and res["blocked"] is True
    assert res["violations"][0]["field"] == "req"


@_reg
def test_b07_enter_default_submits_form():
    """B07 步骤 4 — 表单内默认 Enter 提交（trigger=enter 的 form 目标）。"""
    fields = [FormField(name="wd", value="python", kind="text")]
    d = activate_element("form", fields=fields, form_id="s", action="/s",
                         base="https://www.baidu.com/", trigger=TRIGGER_ENTER)
    assert d.action == "submit" and d.query == "wd=python", d.summary()


@_reg
def test_b07_post_body_not_in_url():
    """B07 步骤 2 — POST 依 enctype 构建请求（查询串进 body 而非 URL）。"""
    fields = [FormField(name="wd", value="python", kind="text")]
    res = build_form_submission(fields, form_id="s", action="/submit",
                                base="https://www.baidu.com/", method="POST")
    assert res["method"] == "POST"
    assert "wd=python" in res["body"], f"POST body carries fields: {res['body']!r}"
    assert "wd=" not in res["url"].split("?")[-1] or "?" not in res["url"], res["url"]


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
