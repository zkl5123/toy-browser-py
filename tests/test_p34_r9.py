"""B01 — unified launch-argument navigation regression tests.

Round-5 assessment (百度真实网页访问评估与改进步骤 §B01 "统一导航
控制器"): the live ``TabBrowser.__init__`` accepted a launch argument
(``initial_path``) but only routed *local files* (``os.path.isfile``)
through navigation — an HTTPS launch argument (e.g.
``py toy_browser https://www.baidu.com``) fell into the ``else``
branch and was silently discarded, leaving the first tab on
``about:blank``.  That broke the "all navigation goes through one
controller" invariant for the *launch* entry point: address-bar /
link / form navigation already flow through
:class:`app.b_series_wiring.BGuiWiring` (a
:class:`app.navigation.NavigationController` surface), but the launch
arg did not.

This batch adds :func:`b_series_wiring.resolve_initial_route` — a
pure-logic router that decides, for a launch argument, whether it is
a *local file*, a *URL form* (http/https/about/file/bare host), or
*plain text* (fall back to ``about:blank``).  ``TabBrowser.__init__``
now consults it so launch navigation shares the same semantics as the
unified controller, without touching the existing local-file render
path.

Tests cover the pure-logic router (no real file system, no tkinter)
plus the ``navigation._is_url_like`` URL-form predicate it relies on.
"""

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

from toy_browser.app.navigation import _is_url_like
from toy_browser.app.b_series_wiring import resolve_initial_route, InitialRoute

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)


def _local_only(path):
    """Fake is_local_file: true only for this specific path."""
    return path == "D:\\proj\\page.html"


# --- 1. URL-form predicate recognises http/https/about/file/bare host ---
def t1_is_url_like_recognises_url_forms():
    for s in ("http://www.baidu.com", "https://example.com/p",
              "about:blank", "file:///c/x.html", "localhost:8000",
              "example.com", "www.baidu.com", "localhost:8000/app"):
        check(f"_is_url_like({s!r}) is a URL form", _is_url_like(s),
              f"got False")


# --- 2. plain text (with spaces) is NOT a URL form ---------------------
def t2_plain_text_not_url_form():
    for s in ("hello world", "foo bar", "", "   ", "search baidu"):
        check(f"_is_url_like({s!r}) is not a URL form", not _is_url_like(s),
              f"got True")


# --- 3. launch routing: local file wins over URL form ------------------
def t3_local_file_route():
    r = resolve_initial_route("D:\\proj\\page.html", _local_only)
    check("existing local file routes to local_file",
          r.route == "local_file" and r.target == "D:\\proj\\page.html",
          f"got {r}")


# --- 4. launch routing: http(s) URL goes through the controller --------
def t4_http_url_route():
    r = resolve_initial_route("https://www.baidu.com/s?wd=python",
                              _local_only)
    check("https launch arg routes to url (unified controller)",
          r.route == "url" and r.target == "https://www.baidu.com/s?wd=python",
          f"got {r}")


# --- 5. launch routing: about:blank launch arg is a URL, not blank -----
def t5_about_route_is_url():
    r = resolve_initial_route("about:blank", _local_only)
    check("about:blank launch arg routes to url",
          r.route == "url" and r.target == "about:blank", f"got {r}")


# --- 6. launch routing: bare host (localhost) is a URL form ------------
def t6_bare_host_route():
    r = resolve_initial_route("localhost:8000/app", _local_only)
    check("bare host launch arg routes to url",
          r.route == "url" and r.target == "localhost:8000/app", f"got {r}")


# --- 7. launch routing: empty / whitespace / plain text -> blank -------
def t7_plain_text_falls_back_to_blank():
    for s in ("", "   ", "hello world", "foo bar"):
        r = resolve_initial_route(s, _local_only)
        check(f"launch arg {s!r} routes to blank", r.route == "blank",
              f"got {r}")


# --- 8. local-file check runs before URL-form check (precedence) ------
def t8_local_file_precedence():
    # A path that is BOTH a local file (fake) and would also be URL-ish
    # must win as local_file — the router checks is_local_file first.
    r = resolve_initial_route("D:\\proj\\page.html", _local_only)
    check("local-file check has precedence over URL-form",
          r.route == "local_file", f"got {r}")
    check("router returns an InitialRoute dataclass",
          isinstance(r, InitialRoute), f"type={type(r)}")


# --- 9. non-matching path (not a local file) that is URL-form -> url --
def t9_nonlocal_urlish_path():
    # "example.com" is not a local file here; it is a URL form.
    r = resolve_initial_route("example.com", _local_only)
    check("non-local URL-form path routes to url", r.route == "url",
          f"got {r}")


def main():
    t1_is_url_like_recognises_url_forms()
    t2_plain_text_not_url_form()
    t3_local_file_route()
    t4_http_url_route()
    t5_about_route_is_url()
    t6_bare_host_route()
    t7_plain_text_falls_back_to_blank()
    t8_local_file_precedence()
    t9_nonlocal_urlish_path()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P34.9 (B01 unified launch-arg navigation) tests passed.")


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("t1_is_url_like_recognises_url_forms", t1_is_url_like_recognises_url_forms))
TESTS.append(("t2_plain_text_not_url_form", t2_plain_text_not_url_form))
TESTS.append(("t3_local_file_route", t3_local_file_route))
TESTS.append(("t4_http_url_route", t4_http_url_route))
TESTS.append(("t5_about_route_is_url", t5_about_route_is_url))
TESTS.append(("t6_bare_host_route", t6_bare_host_route))
TESTS.append(("t7_plain_text_falls_back_to_blank", t7_plain_text_falls_back_to_blank))
TESTS.append(("t8_local_file_precedence", t8_local_file_precedence))
TESTS.append(("t9_nonlocal_urlish_path", t9_nonlocal_urlish_path))
