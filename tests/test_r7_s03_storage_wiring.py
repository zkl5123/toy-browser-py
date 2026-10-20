"""R7-S03 第 5/6 点 — Storage 接线验收（真实 ``js.Host`` + JS 执行）。

纯逻辑测试（``test_r7_s03_storage_service.py``）证明分区与配额**算得
对**；本文件证明它**真的接进了**宿主：通过真实 ``js.Host`` 与页面
脚本验证

1. 同源的两个文档共享 localStorage（"同源导航保留"——旧实现每个
   Host 一份裸 dict，导航一次就丢）；
2. 跨源隔离；
3. sessionStorage 按标签隔离；
4. opaque origin（无归属文档）每文档独立；
5. 配额失败原子化（JS 侧抛错后旧值完好）；
6. JS 侧读不到宿主内部状态（S01 白名单）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.document import dom
from toy_browser.scripting import js
from toy_browser.scripting import web_api
from toy_browser.scripting import storage_service as ss

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _host(html="<html><body></body></html>", origin="", tab_id="",
          profile_id="default"):
    host = js.Host(dom.parse(html), lambda s: None)
    host.doc_origin = origin
    host.doc_url = origin
    host.tab_id = tab_id
    host.profile_id = profile_id
    # 重新注册一次，让新的 origin / tab 生效（register 已在 Host
    # 构造时跑过一遍，那时这些属性还没设）。
    web_api.register_web_api(host)
    return host


def _run(host, code):
    logs = []
    host.log = logs.append
    script = dom.parse(f"<html><body><script>{code}</script></body></html>")
    h2 = js.Host(script, logs.append)
    for attr in ("doc_origin", "doc_url", "tab_id", "profile_id",
                 "storage_service", "_storage_opaque_token"):
        if hasattr(host, attr):
            setattr(h2, attr, getattr(host, attr))
    web_api.register_web_api(h2)
    h2.execute_all()
    return logs


def _fresh_service():
    """每个用例一份干净的分区表（互不污染）。"""
    svc = ss.StorageService()
    return svc


# ---------------------------------------------------------------------------
# 静态层 — 接线确实存在
# ---------------------------------------------------------------------------

@_reg
def test_static_register_uses_storage_service():
    src = inspect.getsource(web_api.register_web_api)
    assert "storage_service" in src, "必须向宿主的 StorageService 要分区"
    assert "areas_for_document" in src
    assert "Storage()" not in src, "不得再每个 Host 造一个裸 dict"


@_reg
def test_static_storage_is_a_facade():
    src = inspect.getsource(web_api.Storage)
    assert "__area" in src, "Storage 只是分区的外观，不自己存数据"
    assert "_store: dict" not in src, "不得再持有裸 dict 字段"


@_reg
def test_static_quota_error_is_shared():
    assert web_api.QuotaExceededError is ss.QuotaExceededError


# ---------------------------------------------------------------------------
# 第 5 点 — 分区
# ---------------------------------------------------------------------------

@_reg
def test_same_origin_shares_localstorage():
    """同源导航保留：第二个文档看得到第一个写的值。"""
    svc = _fresh_service()
    h1 = _host(origin="https://a.test", tab_id="t1")
    h1.storage_service = svc
    web_api.register_web_api(h1)
    h1.globals["localStorage"].setItem("k", "v1")
    h2 = _host(origin="https://a.test", tab_id="t1")
    h2.storage_service = svc
    web_api.register_web_api(h2)
    assert h2.globals["localStorage"].getItem("k") == "v1", \
        "同源文档必须共享 localStorage（旧实现会丢）"


@_reg
def test_cross_origin_isolated():
    svc = _fresh_service()
    h1 = _host(origin="https://a.test", tab_id="t1")
    h1.storage_service = svc
    web_api.register_web_api(h1)
    h1.globals["localStorage"].setItem("k", "a")
    h2 = _host(origin="https://b.test", tab_id="t1")
    h2.storage_service = svc
    web_api.register_web_api(h2)
    assert h2.globals["localStorage"].getItem("k") is None, \
        "跨源不得读到另一个 origin 的数据"


@_reg
def test_session_storage_is_per_tab():
    svc = _fresh_service()
    a = _host(origin="https://a.test", tab_id="tab1")
    a.storage_service = svc
    web_api.register_web_api(a)
    a.globals["sessionStorage"].setItem("s", "1")
    b = _host(origin="https://a.test", tab_id="tab1")
    b.storage_service = svc
    web_api.register_web_api(b)
    assert b.globals["sessionStorage"].getItem("s") == "1", \
        "同标签同源共享 sessionStorage"
    c = _host(origin="https://a.test", tab_id="tab2")
    c.storage_service = svc
    web_api.register_web_api(c)
    assert c.globals["sessionStorage"].getItem("s") is None, \
        "换标签必须换分区"


@_reg
def test_local_survives_tab_switch_session_does_not():
    """local 跨标签共享，session 不共享（规范语义）。"""
    svc = _fresh_service()
    a = _host(origin="https://a.test", tab_id="tab1")
    a.storage_service = svc
    web_api.register_web_api(a)
    a.globals["localStorage"].setItem("L", "1")
    a.globals["sessionStorage"].setItem("S", "1")
    b = _host(origin="https://a.test", tab_id="tab2")
    b.storage_service = svc
    web_api.register_web_api(b)
    assert b.globals["localStorage"].getItem("L") == "1"
    assert b.globals["sessionStorage"].getItem("S") is None


@_reg
def test_opaque_origin_is_per_document():
    """无归属文档（opaque）之间**不**共享。"""
    svc = _fresh_service()
    h1 = _host(origin="about:blank")
    h1.storage_service = svc
    web_api.register_web_api(h1)
    h1.globals["localStorage"].setItem("k", "v")
    h2 = _host(origin="about:blank")
    h2.storage_service = svc
    web_api.register_web_api(h2)
    assert h2.globals["localStorage"].getItem("k") is None, \
        "opaque origin 不得共享存储"


@_reg
def test_default_service_is_shared_across_hosts():
    """默认（宿主进程共享）服务：两个都没显式设 service 的 Host，
    只要 origin / tab 相同就共享。"""
    ss.set_default_storage_service(_fresh_service())
    try:
        a = _host(origin="https://shared.test", tab_id="t")
        b = _host(origin="https://shared.test", tab_id="t")
        a.globals["localStorage"].setItem("k", "v")
        assert b.globals["localStorage"].getItem("k") == "v"
    finally:
        ss.set_default_storage_service(None)


# ---------------------------------------------------------------------------
# 第 6 点 — 配额失败原子化（JS 侧可见）
# ---------------------------------------------------------------------------

@_reg
def test_js_quota_failure_is_atomic():
    """JS 里 setItem 超配额抛错后，旧值必须完好。"""
    svc = _fresh_service()
    host = _host(origin="https://q.test", tab_id="t")
    host.storage_service = svc
    web_api.register_web_api(host)
    ls = host.globals["localStorage"]
    area = ls._area
    area.set_item("k", "keep")
    try:
        area.set_item("big", "x" * (ss.QUOTA_BYTES_PER_ORIGIN + 1))
        raise AssertionError("超配额必须抛 QuotaExceededError")
    except ss.QuotaExceededError:
        pass
    assert area.get_item("k") == "keep", "失败不得破坏已有数据"
    assert area.used_bytes < 100


@_reg
def test_js_script_quota_error_does_not_corrupt():
    """页面脚本触发配额错误后，之前写的值仍然可读。"""
    svc = _fresh_service()
    host = _host(origin="https://q2.test", tab_id="t")
    host.storage_service = svc
    web_api.register_web_api(host)
    logs = _run(host,
                "localStorage.setItem('a', 'keep');"
                "try { localStorage.setItem('big', 'x'.repeat(6*1024*1024)); }"
                "catch (e) { console.log('err:' + e.name); }"
                "console.log('val:' + localStorage.getItem('a'));")
    joined = "\n".join(str(x) for x in logs)
    assert "val:keep" in joined, f"配额失败后旧值必须还在：{joined}"


# ---------------------------------------------------------------------------
# S01 — JS 侧读不到宿主内部
# ---------------------------------------------------------------------------

@_reg
def test_js_cannot_reach_internal_state():
    svc = _fresh_service()
    host = _host(origin="https://s.test", tab_id="t")
    host.storage_service = svc
    web_api.register_web_api(host)
    logs = _run(host,
                "console.log('s:' + (typeof localStorage._store));"
                "console.log('d:' + (typeof localStorage.__store));"
                "console.log('a:' + (typeof localStorage._area));"
                "console.log('c:' + (typeof localStorage.__class__));")
    joined = "\n".join(str(x) for x in logs)
    for key in ("s:", "d:", "a:", "c:"):
        assert f"{key}undefined" in joined, \
            f"JS 不得看到宿主内部状态 {key}：{joined}"


@_reg
def test_js_surface_still_works():
    """规范表面不能被改坏。"""
    svc = _fresh_service()
    host = _host(origin="https://ok.test", tab_id="t")
    host.storage_service = svc
    web_api.register_web_api(host)
    logs = _run(host,
                "localStorage.setItem('x', '1');"
                "localStorage.setItem('y', '2');"
                "console.log('l:' + localStorage.length);"
                "console.log('k0:' + localStorage.key(0));"
                "console.log('g:' + localStorage.getItem('y'));"
                "localStorage.removeItem('x');"
                "console.log('after:' + localStorage.length);")
    joined = "\n".join(str(x) for x in logs)
    assert "l:2" in joined, joined
    assert "k0:x" in joined, joined
    assert "g:2" in joined, joined
    assert "after:1" in joined, joined


@_reg
def test_session_and_local_are_distinct_objects():
    host = _host(origin="https://d.test", tab_id="t")
    local = host.globals["localStorage"]
    sess = host.globals["sessionStorage"]
    local.setItem("k", "L")
    sess.setItem("k", "S")
    assert local.getItem("k") == "L"
    assert sess.getItem("k") == "S"
    assert local._area is not sess._area


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
