"""R7-J02 第3点 — parser-blocking / defer / async / module 分类验证。

对应 ``assessments/20261018-round7/docs/逐项改进实施方案.md`` §R7-J02 第3点：
"parser-blocking 脚本与增量 DOM 解析协调；defer、async、module 明确分类；
未实现 module loader 时不把 module 当普通 classic 执行。"

被测：``toy_browser/app/document_context.py`` 的
:class:`ScriptScheduler` / :class:`DocumentContext` module/defer 分类。
纯逻辑，无 Tk / 无网络。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.document_context import (
    DocumentContext, ScriptScheduler,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 1. module 脚本始终 deferred（即使无 defer 属性），不排到组 0（classic）
# ---------------------------------------------------------------------------

@_reg
def test_module_script_not_classic():
    """module 脚本（is_module=True, defer=False）必须排在 defer 组（组 2），
    不能与普通 parser-blocking classic（组 0）混淆。"""
    sched = ScriptScheduler()
    sched.add("classic.js")                    # 组 0（parser-blocking classic）
    sched.add("mod.js", is_module=True)       # 组 2（module 始终 deferred）
    order = sched.execution_order()
    # classic 必须排在 module 之前
    assert order.index("classic.js") < order.index("mod.js"), \
        f"module must not be treated as classic; order={order}"
    # mod.js 必须是 is_module 标记的
    assert sched.scripts_by_url()["mod.js"].is_module, \
        "mod.js must carry is_module flag"


@_reg
def test_module_scripts_helper_returns_only_modules():
    """ScriptScheduler.module_scripts() 只列出 is_module=True 的脚本。"""
    sched = ScriptScheduler()
    sched.add("a.js")                          # classic
    sched.add("b.js", is_module=True)
    sched.add("c.js", defer=True)              # defer（非 module）
    mods = sched.module_scripts()
    assert mods == ["b.js"], f"module_scripts should return only b.js, got {mods}"


@_reg
def test_module_script_in_deferred_list():
    """deferred_scripts() 必须包含 module 脚本和 defer=True 脚本。"""
    sched = ScriptScheduler()
    sched.add("classic.js")
    sched.add("mod.js", is_module=True)
    sched.add("deferred.js", defer=True)
    deferred = sched.deferred_scripts()
    assert "mod.js" in deferred, "module must appear in deferred_scripts"
    assert "deferred.js" in deferred, "defer must appear in deferred_scripts"
    assert "classic.js" not in deferred, "classic must NOT appear in deferred_scripts"


# ---------------------------------------------------------------------------
# 2. module + defer 双重标记（真实 HTML 中 module 可带 defer，但行为等价）
# ---------------------------------------------------------------------------

@_reg
def test_module_with_defer_flag_stays_group2():
    """module 带 defer=True 仍属组 2（不重复排序、不打乱相对顺序）。"""
    sched = ScriptScheduler()
    sched.add("reg.js")                         # 组 0
    sched.add("mod_deferred.js", is_module=True, defer=True)  # 组 2
    order = sched.execution_order()
    assert order.index("reg.js") < order.index("mod_deferred.js")
    assert sched.scripts_by_url()["mod_deferred.js"].is_module is True
    assert sched.scripts_by_url()["mod_deferred.js"].defer is True


# ---------------------------------------------------------------------------
# 3. disabled module 脚本不参与执行
# ---------------------------------------------------------------------------

@_reg
def test_disabled_module_not_in_module_scripts():
    """enabled=False 的 module 脚本不出现在 module_scripts()。"""
    sched = ScriptScheduler()
    sched.add("mod_ok.js", is_module=True)
    sched.add("mod_blocked.js", is_module=True, enabled=False)
    mods = sched.module_scripts()
    assert mods == ["mod_ok.js"], f"disabled module must be excluded; got {mods}"


# ---------------------------------------------------------------------------
# 4. DocumentContext 层的 module 便捷方法
# ---------------------------------------------------------------------------

@_reg
def test_context_module_scripts():
    """DocumentContext.module_scripts() 委托给 scheduler。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.register_script("classic.js")
    ctx.register_script("mod1.js", is_module=True)
    ctx.register_script("mod2.js", is_module=True)
    mods = ctx.module_scripts()
    assert mods == ["mod1.js", "mod2.js"], f"got {mods}"


@_reg
def test_context_deferred_scripts():
    """DocumentContext.deferred_scripts() 列出 defer=True 或 is_module=True。"""
    ctx = DocumentContext(final_url="https://x.com/")
    ctx.register_script("c.js")
    ctx.register_script("d.js", defer=True)
    ctx.register_script("m.js", is_module=True)
    deferred = ctx.deferred_scripts()
    assert "d.js" in deferred and "m.js" in deferred, f"got {deferred}"
    assert "c.js" not in deferred, "classic must not be in deferred"


# ---------------------------------------------------------------------------
# 5. 完整四类混合排序（parser-blocking → async → defer/module → doc_ready）
# ---------------------------------------------------------------------------

@_reg
def test_mixed_four_groups_ordering():
    """四类脚本的相对顺序符合 R7-J02 第3点分类契约。"""
    sched = ScriptScheduler()
    sched.add("pb.js")                         # parser-blocking classic（组 0）
    sched.add("async.js", async_flag=True)     # async（组 1）
    sched.add("defer.js", defer=True)          # defer（组 2）
    sched.add("mod.js", is_module=True)        # module（组 2，同组按文档序）
    sched.add("dcl.js", doc_ready=True)        # DOMContentLoaded 后（组 3）
    order = sched.execution_order()
    # 组 0 < 组 1 < 组 2 < 组 3
    assert order.index("pb.js") < order.index("async.js")
    assert order.index("async.js") < order.index("defer.js")
    assert order.index("defer.js") < order.index("dcl.js")
    assert order.index("mod.js") < order.index("dcl.js")
    # defer 与 module 同组，按文档序：defer.js 先于 mod.js
    assert order.index("defer.js") < order.index("mod.js")


@_reg
def test_group2_module_not_before_async():
    """module（组 2）不能在 async（组 1）之前执行。"""
    sched = ScriptScheduler()
    sched.add("mod.js", is_module=True)
    sched.add("fast_async.js", async_flag=True)
    order = sched.execution_order()
    assert order.index("fast_async.js") < order.index("mod.js"), \
        f"async must precede module/defer; got {order}"


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
