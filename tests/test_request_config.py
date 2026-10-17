"""B02 — 服务器响应差异可观察的兼容性（纯逻辑核心）回归。

依据 ``assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md``
第 6 节 B02。被测：``toy_browser/app/request_config.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.request_config import (
    RequestConfig, PageDiagnostics,
    UAP_DEFAULT, UAP_COMPAT,
    DIAG_FULL_PAGE, DIAG_LEGITIMATE_EMPTY, DIAG_SCRIPT_REDIRECT,
    DIAG_NO_VISIBLE_CONTENT,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# B02 步骤 1：集中管理 User-Agent / Accept / Accept-Language / Cookie / 超时 / 重定向
# ---------------------------------------------------------------------------

@_reg
def test_b02_config_centralized():
    """B02 步骤 1 — 配置对象集中管理标识（文档及子资源共用）。"""
    cfg = RequestConfig(ua=UAP_DEFAULT, timeout=30.0, max_redirects=10,
                       cookie_policy="send")
    assert cfg.ua == UAP_DEFAULT
    assert cfg.timeout == 30.0
    assert cfg.max_redirects == 10
    assert cfg.cookie_policy == "send"


@_reg
def test_b02_subresource_shares_config():
    """B02 步骤 1 — 子资源继承文档配置（避免硬编码不一致标识）。"""
    cfg = RequestConfig(ua=UAP_COMPAT, cookie_policy="send",
                        accept_language="zh-CN,zh;q=0.9")
    sub = cfg.for_subresource()
    # 子资源继承 ua / cookie_policy / accept_language，accept 放宽为 */*。
    assert sub.ua == UAP_COMPAT
    assert sub.accept == "*/*"
    assert sub.cookie_policy == "send"
    assert sub.accept_language == "zh-CN,zh;q=0.9"
    assert sub.request_id.endswith(".sub")


# ---------------------------------------------------------------------------
# B02 步骤 2：兼容标识（显式选择用于诊断，界面/日志记录实际选择）
# ---------------------------------------------------------------------------

@_reg
def test_b02_compat_ua_pick():
    """B02 步骤 2 — 显式选择兼容标识（诊断用，非引擎兼容修复）。"""
    cfg = RequestConfig.with_ua(UAP_COMPAT)
    assert cfg.ua == UAP_COMPAT
    assert "compat" in cfg.ua_value, "日志记录实际选择"
    # 非法标识拒绝。
    try:
        RequestConfig.with_ua("bogus")
        raise AssertionError("should reject unknown ua")
    except ValueError:
        pass


@_reg
def test_b02_describe_records_actual():
    """B02 步骤 2 — describe() 记录实际选择（界面 / 日志）。"""
    cfg = RequestConfig.with_ua(UAP_COMPAT, cookie_policy="none")
    d = cfg.describe()
    assert "ua=compat" in d
    assert "cookie=none" in d
    assert "timeout=30.0s" in d
    assert "max-redirects=10" in d


# ---------------------------------------------------------------------------
# B02 步骤 3：主文档诊断（区分 传输完成 / 文档已提交 / 首屏已绘制 / 脚本错误）
# ---------------------------------------------------------------------------

@_reg
def test_b02_four_state_flags():
    """B02 步骤 3 — 区分"传输完成""文档已提交""首屏已绘制""脚本错误"。"""
    d = PageDiagnostics(status_code=200, final_url="https://x.com/",
                       content_type="text/html", decoded_size=5000,
                       request_id="req-1")
    d.add_nav_hop("https://x.com/")
    d.mark_transport_done()
    d.mark_doc_committed()
    d.mark_first_screen()
    d.mark_script_error()
    exp = d.explain()
    assert "transport-done" in exp
    assert "doc-committed" in exp
    assert "first-screen" in exp
    assert "script-error" in exp
    assert "req-1" in exp


@_reg
def test_b02_nav_chain_recorded():
    """B02 步骤 3 — 记录导航链（主文档状态 / 最终 URL / Content-Type / 大小）。"""
    d = PageDiagnostics(status_code=200, final_url="https://a.com/",
                       content_type="text/html", decoded_size=1234,
                       request_id="r")
    d.add_nav_hop("https://start.com/")
    d.add_nav_hop("https://a.com/")
    exp = d.explain()
    assert "start.com" in exp and "a.com" in exp
    assert "status=200" in exp
    assert "text/html" in exp
    assert "size=1234" in exp


# ---------------------------------------------------------------------------
# B02 步骤 4：小正文 / 可见内容诊断（不单凭正文短小判失败）
# ---------------------------------------------------------------------------

@_reg
def test_b02_full_page_diagnosis():
    """B02 步骤 4 — 有可见内容 → full-page（完整页交由正常引擎处理）。"""
    d = PageDiagnostics(status_code=200, decoded_size=8000)
    d.mark_doc_committed()
    d.mark_visible_content(True)
    assert d.diagnose_small_body() == DIAG_FULL_PAGE
    assert d.diagnosis == DIAG_FULL_PAGE


@_reg
def test_b02_legitimate_empty_not_failure():
    """B02 步骤 4 — 空文档本身也可能是合法页面（不判失败）。"""
    d = PageDiagnostics(status_code=200, decoded_size=0)
    # 200 + 极小 + 无可见内容 + 文档未提交（不调 mark_doc_committed）
    # → legitimate-empty（合法空页）。
    d.mark_visible_content(False)
    assert d.diagnose_small_body() == DIAG_LEGITIMATE_EMPTY
    assert d.is_legitimate_empty() is True


@_reg
def test_b02_script_redirect_diagnosis():
    """B02 步骤 4 — 200 + 极小脚本跳转页（脚本错误 → script-redirect）。"""
    d = PageDiagnostics(status_code=200, decoded_size=200)
    d.mark_doc_committed()
    d.mark_visible_content(False)
    d.mark_script_error()
    assert d.diagnose_small_body() == DIAG_SCRIPT_REDIRECT
    assert d.needs_script_execution() is True


@_reg
def test_b02_no_visible_content_needs_script():
    """B02 步骤 4 — 无可见内容但需脚本执行才能显示（不是"页面失败"）。"""
    d = PageDiagnostics(status_code=200, decoded_size=500)
    d.mark_doc_committed()
    d.mark_visible_content(False)
    # 无脚本错误 → no-visible-content（需脚本执行）。
    assert d.diagnose_small_body() == DIAG_NO_VISIBLE_CONTENT
    assert d.needs_script_execution() is True


@_reg
def test_b02_explain_complete():
    """B02 验收 — 能解释"当前收到哪一种页面"。"""
    d = PageDiagnostics(status_code=200, final_url="https://x.com/",
                       content_type="text/html", decoded_size=60,
                       request_id="rid")
    d.mark_doc_committed()
    d.mark_visible_content(False)
    exp = d.explain()
    assert "diagnosis=" in exp
    assert "status=200" in exp
    assert "rid" in exp
    # 完整页仍需交由正常引擎（本模块不修改请求，B02 验收）。
    # 不存在的"关证书 / 降级 HTTPS"开关（验收）。
    assert not hasattr(d, "disable_tls")
    assert not hasattr(RequestConfig(), "force_http")


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
