"""B01 + B05 统一导航控制器 + location 导航语义（纯逻辑核心）回归。

依据：``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B01（统一导航入口）+ B05（location / 文档 URL 导航语义）。
被测模块：``toy_browser/app/navigation.py``。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.navigation import (
    NavigationController, NavRequest, NavBudget, Location,
    SRC_LAUNCH, SRC_ADDRESS, SRC_SCRIPT, SRC_REDIRECT,
)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# B01 — 统一导航入口（NavRequest / NavigationController）
# ---------------------------------------------------------------------------

@_reg
def test_b01_launch_https():
    """B01 步骤 2 — 启动 HTTPS 经统一入口（kind=web）。"""
    c = NavigationController()
    req = c.launch("https://www.baidu.com/")
    out = c.dispatch(req)
    assert req.kind() == "web", f"launch kind should be web, got {req.kind()}"
    assert out.ok and out.committed
    assert c.current_url == "https://www.baidu.com/"
    assert c.address_text == "https://www.baidu.com/"


@_reg
def test_b01_local_file_same_entry():
    """B01 步骤 1/2 — 本地文件与 http 经同一 dispatch 入口。"""
    c = NavigationController()
    req = c.launch("D:/Desktop/demo.html")
    out = c.dispatch(req)
    assert out.ok
    assert c.current_url == "D:/Desktop/demo.html"


@_reg
def test_b01_about_page():
    """B01 — about 页经同一入口分派（kind=about）。"""
    c = NavigationController()
    req = c.launch("about:blank")
    out = c.dispatch(req)
    assert req.kind() == "about"
    assert out.ok and c.current_url == "about:blank"


@_reg
def test_b01_seq_monotonic_drop_stale():
    """B01/B10 — 导航序号单调；旧 seq 结果被丢弃。"""
    c = NavigationController()
    r1 = c.dispatch(c.launch("https://a.com/"))
    r2 = c.dispatch(c.launch("https://b.com/"))
    assert r2.seq == r1.seq + 1, f"seq must be monotonic ({r1.seq} -> {r2.seq})"
    assert c.drop_stale(r1.seq) is True, "stale seq must be dropped"
    assert c.drop_stale(r2.seq) is False, "active seq must not be dropped"


@_reg
def test_b01_address_bar_url():
    """B01 步骤 3 — 地址栏输入绝对 URL 原样导航。"""
    c = NavigationController(initial_url="about:blank")
    req = c.classify_address_input("https://www.baidu.com/s?wd=python")
    assert req.error is None
    assert req.target == "https://www.baidu.com/s?wd=python"
    assert req.source == SRC_ADDRESS


@_reg
def test_b01_address_bar_relative():
    """B01 步骤 3 — 相对 URL 基于当前文档基址解析。"""
    c = NavigationController(initial_url="https://x.com/a/b.html")
    req = c.classify_address_input("c.html")
    assert req.target == "https://x.com/a/c.html", f"relative should resolve to base, got {req.target}"
    assert req.error is None


@_reg
def test_b01_address_bar_plain_text_search():
    """B01 步骤 3 — 普通文本 → 搜索引擎补全（独立策略钩子，不当本地文件）。"""
    c = NavigationController(initial_url="about:blank")
    # 未提供搜索策略：返回 error（不当本地文件、不当 URL 丢失）。
    req = c.classify_address_input("python browser")
    assert req.error == "plain-text-no-search-policy", f"plain text without policy must be flagged, got {req.error}"
    # 提供搜索策略钩子：补全为搜索 URL。
    req2 = c.classify_address_input("python browser",
                                   search_url_for=lambda t: "https://www.baidu.com/s?wd=" + t)
    assert req2.error is None
    assert req2.target == "https://www.baidu.com/s?wd=python browser"


@_reg
def test_b01_success_updates_history():
    """B01 步骤 4 — 成功提交更新地址栏 / 当前 URL / 历史。"""
    c = NavigationController()
    c.dispatch(c.launch("https://a.com/"))
    c.dispatch(c.launch("https://b.com/"))
    assert c.address_text == "https://b.com/"
    assert c.current_url == "https://b.com/"
    assert c.can_back()
    assert c.back() == "https://a.com/"
    assert c.can_forward()
    assert c.forward() == "https://b.com/"


@_reg
def test_b01_failure_keeps_target_and_error():
    """B01 步骤 4 — 请求失败保留目标与错误类型（不静默改界面文本）。"""
    c = NavigationController(initial_url="https://ok.com/")
    req = NavRequest(target="https://fail.com/", source=SRC_ADDRESS, error="dns-error")
    out = c.dispatch(req)
    assert c.last_error() == "dns-error", f"failure must keep error type, got {c.last_error()}"
    # 失败不改当前 URL（保留请求目标在 request 上）。
    assert c.current_url == "https://ok.com/"
    assert req.target == "https://fail.com/"


# ---------------------------------------------------------------------------
# B05 — Location 导航语义
# ---------------------------------------------------------------------------

@_reg
def test_b05_location_reads_committed_doc():
    """B05 步骤 1 — location 读取当前已提交文档 URL（统一初始化来源）。"""
    c = NavigationController(initial_url="https://baidu.com/")
    loc = Location(doc_url=c.current_url, controller=c)
    assert loc.href == "https://baidu.com/"
    assert loc.hostname == "baidu.com"
    assert loc.protocol == "https:"
    d = loc.as_dict()
    # 兼容 web_api.make_location 的键契约。
    for k in ("href", "protocol", "host", "hostname", "port", "pathname", "search", "hash", "origin"):
        assert k in d, f"as_dict must keep key {k}"


@_reg
def test_b05_location_assign_new_history():
    """B05 步骤 2/3 — assign 新建历史项（转成导航任务，不同步递归加载）。"""
    c = NavigationController(initial_url="https://a.com/")
    loc = Location(doc_url=c.current_url, controller=c)
    req = loc.assign("https://b.com/")
    assert req.source == SRC_SCRIPT and req.replace is False
    assert c.current_url == "https://b.com/"
    assert c.can_back() and c.back() == "https://a.com/"


@_reg
def test_b05_location_replace_history():
    """B05 步骤 3 — replace 替换当前历史项（百度 227 字节页语义）。"""
    c = NavigationController(initial_url="https://a.com/")
    c.dispatch(NavRequest(target="https://b.com/", source=SRC_SCRIPT))
    loc = Location(doc_url=c.current_url, controller=c)
    req = loc.replace("https://c.com/")
    assert req.replace is True
    # replace 不新建历史项：back 应直接到 a.com（b 被替换）。
    assert c.current_url == "https://c.com/"
    assert c.can_back() and c.back() == "https://a.com/"


@_reg
def test_b05_location_href_assign():
    """B05 步骤 2 — href 赋值 = assign 语义。"""
    c = NavigationController(initial_url="https://a.com/")
    loc = Location(doc_url=c.current_url, controller=c)
    req = loc.set_href("https://z.com/")
    assert req.replace is False
    assert c.current_url == "https://z.com/"


@_reg
def test_b05_location_reload():
    """B05 步骤 2 — reload 转成导航任务（替换当前项）。"""
    c = NavigationController(initial_url="https://a.com/")
    loc = Location(doc_url=c.current_url, controller=c)
    req = loc.reload()
    assert req.replace is True and req.target == "https://a.com/"


@_reg
def test_b05_same_doc_hash():
    """B05 步骤 3 — 同文档 hash 导航与跨文档加载分开。"""
    c = NavigationController(initial_url="https://x.com/page?k=1")
    loc = Location(doc_url=c.current_url, controller=c)
    assert loc.is_same_doc("#sec2", ignore_fragment=True) is True, "hash-only is same doc"
    assert loc.is_same_doc("https://y.com/", ignore_fragment=True) is False, "cross-host is not same doc"
    assert loc.is_same_doc("other.html", ignore_fragment=True) is False, "cross-path is not same doc"


@_reg
def test_b05_relative_resolve():
    """B05 步骤 3 — 相对目标用 stdlib URL 解析器基于当前文档解析。"""
    c = NavigationController(initial_url="https://x.com/a/b.html")
    loc = Location(doc_url=c.current_url, controller=c)
    assert loc.resolve_target("c.html") == "https://x.com/a/c.html"
    assert loc.resolve_target("/root.html") == "https://x.com/root.html"
    assert loc.resolve_target("../up.html") == "https://x.com/up.html"
    assert loc.resolve_target("https://abs.com/") == "https://abs.com/"


@_reg
def test_b05_baidu_227_byte_script_page():
    """B05 验收 — 227 字节脚本页 location.replace 产生可追踪导航任务。

    （百度首页只返回 location.replace(location.href.replace('https://','http://'))
    的 227 字节页；location.replace 必须转成 replace 历史项的导航任务，
    不能无声停在原 URL。）
    """
    c = NavigationController(initial_url="https://www.baidu.com/")
    loc = Location(doc_url=c.current_url, controller=c)
    # 模拟脚本 location.replace(location.href.replace("https://", "http://"))。
    target = loc.href.replace("https://", "http://")
    req = loc.replace(target)
    assert req.replace is True
    assert req.target == "http://www.baidu.com/", f"replace target should downgrade scheme, got {req.target}"
    assert c.current_url == "http://www.baidu.com/"


@_reg
def test_b05_nav_budget_loop_guard():
    """B05 步骤 4 — 循环跳转超预算被拒绝（nav-budget-exhausted）。"""
    c = NavigationController(initial_url="https://a.com/")
    budget = NavBudget(max_hops=3)
    req = NavRequest(target="https://a.com/", source=SRC_REDIRECT)
    for i in range(4):
        req = c.record_hop(req, budget)
    assert req.error == "nav-budget-exhausted", f"loop beyond budget must be rejected, got {req.error}"
    assert c.last_error() == "nav-budget-exhausted"


@_reg
def test_b05_budget_cancel():
    """B05 步骤 4 — 取消标记使预算立即耗尽。"""
    c = NavigationController(initial_url="https://a.com/")
    budget = NavBudget(max_hops=10, cancelled=True)
    req = c.record_hop(NavRequest(target="https://b.com/", source=SRC_REDIRECT), budget)
    assert req.error == "nav-budget-exhausted"


# ---------------------------------------------------------------------------

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
