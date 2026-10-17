"""B08 — 按真实页面生命周期加载资源与调度脚本（纯逻辑核心）回归。

依据 ``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B08。被测：``toy_browser/app/document_context.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_context import (
    DocumentContext, ResourceRecord, ScriptScheduler,
    RES_CSS, RES_JS, RES_IMG, RES_FONT,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# B08 步骤 1：同一 DocumentContext 保存最终 URL / 基址 / 策略 / 任务版本 / 错误
# ---------------------------------------------------------------------------

@_reg
def test_b08_single_context_state():
    """B08 步骤 1 — 同一上下文保存最终 URL / 基址 / 策略 / Cookie / 任务版本。"""
    ctx = DocumentContext(final_url="https://www.baidu.com/",
                         base_url="https://www.baidu.com/",
                         policy={"csp": "default-src 'self'"},
                         cookies="session=abc")
    assert ctx.final_url == "https://www.baidu.com/"
    assert ctx.base_url == "https://www.baidu.com/"
    assert ctx.policy["csp"] == "default-src 'self'"
    assert ctx.cookies == "session=abc"
    assert ctx.task_version == 1


@_reg
def test_b08_task_version_stale_drop():
    """B08 步骤 1 / B10 — 任务版本单调，旧版本结果丢弃。"""
    ctx = DocumentContext(final_url="https://a.com/")
    v0 = ctx.task_version
    v1 = ctx.bump_task_version()
    assert v1 == v0 + 1
    assert ctx.is_stale_task(v0) is True, "old version must be stale"
    assert ctx.is_stale_task(v1) is False, "current version not stale"


# ---------------------------------------------------------------------------
# B08 步骤 2：资源三态（下载 200 / 成功解码 / 应用到页面）
# ---------------------------------------------------------------------------

@_reg
def test_b08_resource_three_states():
    """B08 步骤 2 — 下载 / 解码 / 应用三态分别可查（"33 个 200"≠渲染）。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.mark_downloaded("https://x.com/a.js", status_code=200,
                        size_bytes=100)
    ctx.mark_decoded("https://x.com/a.js")
    ctx.mark_applied("https://x.com/a.js")
    rec = ctx.resources["https://x.com/a.js"]
    assert rec.downloaded and rec.decoded and rec.applied
    assert rec.usable is True


@_reg
def test_b08_download200_not_usable():
    """B08 验收 — "下载 200"不等同于"应用到页面"（usable=False）。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.register_resource("https://x.com/pic.png", RES_IMG)
    ctx.mark_downloaded("https://x.com/pic.png", status_code=200)
    # 只下载、未解码未应用 → usable=False。
    rec = ctx.resources["https://x.com/pic.png"]
    assert rec.downloaded is True and rec.usable is False, "200 alone is not usable"


@_reg
def test_b08_resource_blocked_reason():
    """B08 步骤 4 — 缺口按失败降级，记录阻断原因（先显示能确定的内容）。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.mark_blocked("https://x.com/f.woff2", "font-face-unsupported")
    rec = ctx.resources["https://x.com/f.woff2"]
    assert rec.blocked_reason == "font-face-unsupported"
    assert rec.applied is False


@_reg
def test_b08_download_failed_records_error():
    """B08 步骤 2 — 下载失败记录错误类别（B03 错误分类对齐）。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.mark_downloaded("https://x.com/404.js", status_code=404, ok=False)
    assert any("download-failed" in e for e in ctx.errors)


# ---------------------------------------------------------------------------
# B08 步骤 3：脚本调度最小一致模型（async / defer / DOMContentLoaded / load）
# ---------------------------------------------------------------------------

@_reg
def test_b08_script_order_document_order():
    """B08 步骤 3 — 普通脚本按文档序执行。"""
    sched = ScriptScheduler()
    sched.add("1.js", position="head")
    sched.add("2.js", position="head")
    sched.add("3.js", position="body")
    assert sched.execution_order() == ["1.js", "2.js", "3.js"]


@_reg
def test_b08_defer_after_regular_before_domready():
    """B08 步骤 3 — defer 脚本在普通脚本之后、DOMContentLoaded 之前。"""
    sched = ScriptScheduler()
    sched.add("reg1.js")
    sched.add("defer1.js", defer=True)
    sched.add("reg2.js")
    order = sched.execution_order()
    assert order.index("reg1.js") < order.index("defer1.js"), "defer after regular"
    assert order.index("reg2.js") < order.index("defer1.js"), "defer after all regular"


@_reg
def test_b08_async_ready_insert():
    """B08 步骤 3 — async 脚本就绪即执行（不阻塞后续，按就绪序）。"""
    sched = ScriptScheduler()
    sched.add("a.js")
    sched.add("async1.js", async_flag=True)
    sched.add("b.js")
    order = sched.execution_order()
    # async 组在普通组之后（就绪序），但在 defer / doc_ready 之前。
    assert order.index("a.js") < order.index("async1.js")
    assert order.index("b.js") < order.index("async1.js")


@_reg
def test_b08_disabled_script_not_executed():
    """B08 步骤 3 — 被策略 / CSP 禁用的脚本不执行。"""
    sched = ScriptScheduler()
    sched.add("good.js")
    sched.add("bad.js", enabled=False)
    assert sched.execution_order() == ["good.js"]


@_reg
def test_b08_domready_gate():
    """B08 步骤 3 — DOMContentLoaded 门控（onDOMContentLoaded 脚本组 3）。"""
    sched = ScriptScheduler()
    sched.add("normal.js")
    sched.add("onload.js", doc_ready=True)
    order = sched.execution_order()
    assert order.index("normal.js") < order.index("onload.js"), "doc_ready last"
    assert sched.dom_content_loaded() is False
    sched.mark_dom_content_loaded()
    assert sched.dom_content_loaded() is True


@_reg
def test_b08_context_script_schedule():
    """B08 步骤 3 — DocumentContext 集成脚本调度 + 生命周期门控。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.register_script("s1.js")
    ctx.register_script("s2.js", defer=True)
    ctx.register_script("s3.js", doc_ready=True)
    assert ctx.script_execution_order() == ["s1.js", "s2.js", "s3.js"]
    ctx.fire_dom_content_loaded()
    ctx.fire_load()
    assert ctx.scheduler.load_fired() is True


# ---------------------------------------------------------------------------
# B08 步骤 5：资源完成度 / 可见性可解释（验收）
# ---------------------------------------------------------------------------

@_reg
def test_b08_resource_completeness_counts():
    """B08 验收 — 网络成功数与页面使用结果分别可查。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.mark_downloaded("https://x.com/a.css", RES_CSS, 200)
    ctx.mark_decoded("https://x.com/a.css")
    ctx.mark_applied("https://x.com/a.css")
    ctx.mark_downloaded("https://x.com/b.js", RES_JS, 200)
    # b.js 只下载，未应用。
    comp = ctx.resource_completeness()
    assert comp["total"] == 2
    assert comp["downloaded"] == 2
    assert comp["decoded"] == 1
    assert comp["applied"] == 1
    assert comp["usable"] == 1
    assert comp["blocked"] == 0


@_reg
def test_b08_visibility_report_explains_missing():
    """B08 验收 — 能解释首页为何仍隐藏或缺失内容（不冒充功能兼容率）。"""
    ctx = DocumentContext(final_url="https://www.baidu.com/")
    ctx.mark_downloaded("https://www.baidu.com/logo.png", RES_IMG, 200)
    ctx.mark_downloaded("https://www.baidu.com/search.js", RES_JS, 200)
    # 两个都只下载，未应用 → 可见性报告列出缺失原因。
    report = ctx.visibility_report()
    assert "missing-content" in report
    assert "search.js" in report


@_reg
def test_b08_usage_report_combined():
    """B08 步骤 5 — 资源完成度 + 脚本错误 + 可见性合并视图。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.mark_downloaded("https://x.com/a.js", status_code=200)
    ctx.mark_blocked("https://x.com/b.png", "image-decode-failed")
    rep = ctx.usage_report()
    assert rep["total"] == 2
    assert rep["blocked"] == 1
    assert "b.png" in rep["visibility"]
    assert isinstance(rep["script_errors"], list)


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
