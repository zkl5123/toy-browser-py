"""GPT P0-4 — CORS/CSP enforcement in the request flow.

GPT P1-2 — restructured as a proper test module: all checks are wrapped
in test_* functions so they can be imported and run by test.py's
orchestrator without executing at module import time.
"""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import dom
import net


def test_p0_same_origin_no_policy_allowed():
    """Same-origin resource with no CSP → allowed."""
    resp = net.HttpResponse(status=200, status_text="OK",
                            headers={"Content-Type": "text/css"}, body=b"")
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://example.com/style.css") is True


def test_p0_cross_origin_no_acao_blocked():
    """Cross-origin resource without ACAO → blocked (CORS)."""
    resp = net.HttpResponse(status=200, status_text="OK",
                            headers={"Content-Type": "text/css"}, body=b"")
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://cdn.example.org/style.css") is False


def test_p0_cross_origin_with_acao_allowed():
    """Cross-origin resource WITH matching ACAO → allowed."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Access-Control-Allow-Origin": "https://example.com"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://cdn.example.org/style.css") is True


def test_p0_cross_origin_acao_star_allowed():
    """Cross-origin resource with ACAO: * → allowed."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Access-Control-Allow-Origin": "*"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://cdn.example.org/style.css") is True


def test_p0_csp_style_src_self_blocks_cross_origin():
    """CSP style-src 'self' blocks cross-origin stylesheets."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Content-Security-Policy": "style-src 'self'"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "style",
        "https://cdn.example.org/style.css") is False


def test_p0_csp_style_src_self_allows_same_origin():
    """CSP style-src 'self' allows same-origin stylesheets."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Content-Security-Policy": "style-src 'self'"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://example.com/style.css") is True


def test_p0_no_csp_allows_all():
    """No CSP header → all allowed (CSP check is a no-op)."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Access-Control-Allow-Origin": "*"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "style", "https://cdn.example.org/style.css") is True


def test_p0_blank_origin_skips_cors():
    """Blank requesting_origin → CORS check skipped (legacy)."""
    resp = net.HttpResponse(status=200, status_text="OK",
                            headers={"Content-Type": "text/css"}, body=b"")
    assert resp.is_policy_allowed(
        "", "style", "https://cdn.example.org/style.css") is True


def test_p0_blank_kind_skips_csp():
    """Blank resource_kind → CSP check skipped."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "text/css",
                 "Content-Security-Policy": "style-src 'self'"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "", "https://example.com/style.css") is True


def test_p0_csp_img_src_blocks_cross_origin_image():
    """CSP img-src 'self' blocks cross-origin images."""
    resp = net.HttpResponse(
        status=200, status_text="OK",
        headers={"Content-Type": "image/png",
                 "Content-Security-Policy": "img-src 'self'"},
        body=b"",
    )
    assert resp.is_policy_allowed(
        "https://example.com", "img", "https://evil.com/img.png") is False


def test_p0_csp_blocks_helper_same_origin():
    """_csp_blocks helper: 'self' permits same-origin."""
    assert net._csp_blocks(["'self'"], "https://example.com/s.css") is False


def test_p0_csp_blocks_helper_none():
    """_csp_blocks helper: 'none' blocks everything."""
    assert net._csp_blocks(["'none'"], "https://example.com/s.css") is True


def test_p0_csp_blocks_helper_exact_match():
    """_csp_blocks helper: exact URL match permits."""
    assert net._csp_blocks(
        ["https://example.com/s.css"], "https://example.com/s.css") is False


def test_p0_csp_blocks_helper_no_match_blocked():
    """_csp_blocks helper: no matching token → blocked."""
    assert net._csp_blocks(
        ["https://other.com/s.css"], "https://example.com/s.css") is True


def test_p0_load_subresources_enforce_policy_false():
    """load_subresources enforce_policy=False → legacy behaviour."""
    root = dom.parse(
        '<html><body>'
        '<link rel="stylesheet" href="https://cdn.example.org/style.css">'
        '<img src="https://cdn.example.org/img.png">'
        '</body></html>')
    result = net.load_subresources(root, "https://example.com/",
                                    enforce_policy=False)
    assert "stylesheet" in result and "image" in result


def test_p0_load_subresources_enforce_policy_true():
    """load_subresources enforce_policy=True (default) runs the check."""
    root = dom.parse(
        '<html><body>'
        '<link rel="stylesheet" href="https://cdn.example.org/style.css">'
        '<img src="https://cdn.example.org/img.png">'
        '</body></html>')
    # No real HTTP client → fetch_resource returns None for every spec.
    # The policy check runs but has no response to gate.
    result = net.load_subresources(root, "https://example.com/")
    assert "stylesheet" in result and "image" in result


TESTS = [
    ("p0_same_origin_no_policy_allowed", test_p0_same_origin_no_policy_allowed),
    ("p0_cross_origin_no_acao_blocked", test_p0_cross_origin_no_acao_blocked),
    ("p0_cross_origin_with_acao_allowed", test_p0_cross_origin_with_acao_allowed),
    ("p0_cross_origin_acao_star_allowed", test_p0_cross_origin_acao_star_allowed),
    ("p0_csp_style_src_self_blocks_cross_origin", test_p0_csp_style_src_self_blocks_cross_origin),
    ("p0_csp_style_src_self_allows_same_origin", test_p0_csp_style_src_self_allows_same_origin),
    ("p0_no_csp_allows_all", test_p0_no_csp_allows_all),
    ("p0_blank_origin_skips_cors", test_p0_blank_origin_skips_cors),
    ("p0_blank_kind_skips_csp", test_p0_blank_kind_skips_csp),
    ("p0_csp_img_src_blocks_cross_origin_image", test_p0_csp_img_src_blocks_cross_origin_image),
    ("p0_csp_blocks_helper_same_origin", test_p0_csp_blocks_helper_same_origin),
    ("p0_csp_blocks_helper_none", test_p0_csp_blocks_helper_none),
    ("p0_csp_blocks_helper_exact_match", test_p0_csp_blocks_helper_exact_match),
    ("p0_csp_blocks_helper_no_match_blocked", test_p0_csp_blocks_helper_no_match_blocked),
    ("p0_load_subresources_enforce_policy_false", test_p0_load_subresources_enforce_policy_false),
    ("p0_load_subresources_enforce_policy_true", test_p0_load_subresources_enforce_policy_true),
]
