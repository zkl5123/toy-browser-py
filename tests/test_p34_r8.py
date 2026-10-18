"""B05 — location navigation bridge regression tests.

Round-5 assessment (百度真实网页访问评估与改进步骤 §B05): the live
``location`` global used to be a bare dict from
``web_api.make_location`` — ``location.replace(...)`` (baidu's 227-byte
redirect shim shape) merely *wrote a dict key* and produced no
navigation task, so the page rendered the redirect shim as a final
document (blank screen).  This batch upgrades the sandbox's live
``location`` to :class:`web_api.ToyLocation`, a navigation-capable
object that reuses :class:`app.navigation.Location`'s stdlib URL
parsing so that ``replace`` / ``assign`` / ``href =`` / ``reload``
each emit a :class:`app.navigation.NavRequest` on
``host._pending_nav`` for the render pipeline to follow (with a loop
guard against the classic ``https ↔ http`` replace loop).

Tests (no real network I/O — the navigation task is asserted on the
host; the pipeline's follow logic is exercised separately with a
local ``http.server`` fixture in B05-specific cases).
"""

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

from toy_browser.document import dom
from toy_browser.scripting.js import Host
from toy_browser.scripting import web_api

_failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)


def _host_with_script(code, doc_url=""):
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
            f'<body><script>{code}</script></body></html>')
    root = dom.parse(html)
    host = Host(root, log=lambda _m: None)
    if doc_url:
        host.doc_url = doc_url
    host.execute_scripts_in_order({})
    return host


# --- 1. ToyLocation is the live location global ---------------------------
def t1_live_location_is_toy_location():
    host = _host_with_script('console.log("x");')
    loc = host.globals["location"]
    check("live location global is a web_api.ToyLocation",
          isinstance(loc, web_api.ToyLocation),
          f"type={type(loc)}")


# --- 2. readable parts expose the same keys as the legacy dict ----------
def t2_location_parts_match_legacy_keys():
    host = _host_with_script('console.log("x");', "https://example.com/p?x=1#h")
    loc = host.globals["location"]
    # Legacy-dict key coverage: every part make_location exposes is
    # readable on the ToyLocation object via attribute access.
    d = web_api.make_location("https://example.com/p?x=1#h")
    for k in ("href", "protocol", "host", "hostname", "port",
              "pathname", "search", "hash", "origin"):
        check(f"location.{k} matches make_location", getattr(loc, k) == d[k],
              f"got {getattr(loc, k)!r} vs {d[k]!r}")
    # Note: with a *non-default* port, navigation.Location's origin
    # (spec origin includes a non-default port) intentionally differs
    # from the legacy make_location dict projection; that is a
    # pre-existing navigation-module semantic, not a B05 regression.


# --- 2b. non-default port: parts are readable; origin carries the port ---
def t2b_location_non_default_port_readable():
    host = _host_with_script('console.log("x");',
                             "https://example.com:8443/p?x=1#h")
    loc = host.globals["location"]
    check("non-default port: port part readable",
          loc.port == "8443", f"got {loc.port!r}")
    check("non-default port: host part readable",
          loc.host == "example.com:8443", f"got {loc.host!r}")
    check("non-default port: origin carries the port (spec)",
          loc.origin == "https://example.com:8443",
          f"got {loc.origin!r}")


# --- 3. location.replace (baidu https->http shape) emits a task ---------
def t3_replace_baidu_shape():
    code = 'location.replace(location.href.replace("https://","http://"));'
    host = _host_with_script(code, "https://www.baidu.com/")
    pending = host._pending_nav
    check("replace() emits one navigation task", len(pending) == 1,
          f"got {len(pending)}")
    if pending:
        check("replace target is the http URL (baidu shape)",
              pending[0].target == "http://www.baidu.com/",
              f"got {pending[0].target!r}")
        check("replace is a replace-history navigation",
              pending[0].replace is True, f"replace={pending[0].replace}")
        check("replace source is script",
              pending[0].source == "script", f"source={pending[0].source}")


# --- 4. relative assign resolves against the document base -------------
def t4_assign_relative_resolves():
    host = _host_with_script('location.assign("page2.html");',
                             "https://example.com/dir/")
    pending = host._pending_nav
    check("assign() emits one navigation task", len(pending) == 1)
    if pending:
        check("relative assign resolves against doc base",
              pending[0].target == "https://example.com/dir/page2.html",
              f"got {pending[0].target!r}")
        check("assign is a new-history navigation",
              pending[0].replace is False, f"replace={pending[0].replace}")


# --- 5. href assignment is assign semantics -----------------------------
def t5_href_assign_semantics():
    host = _host_with_script('location.href = "/q?wd=py";',
                             "https://example.com/")
    pending = host._pending_nav
    check("href = … emits one navigation task", len(pending) == 1)
    if pending:
        check("href = resolves the relative path",
              pending[0].target == "https://example.com/q?wd=py",
              f"got {pending[0].target!r}")
        check("href = is a new-history (assign) navigation",
              pending[0].replace is False, f"replace={pending[0].replace}")


# --- 6. reload targets the current URL and is a replace -----------------
def t6_reload_is_replace_of_self():
    host = _host_with_script('location.reload();', "https://example.com/a")
    pending = host._pending_nav
    check("reload() emits one navigation task", len(pending) == 1)
    if pending:
        check("reload targets the current URL",
              pending[0].target == "https://example.com/a",
              f"got {pending[0].target!r}")
        check("reload is a replace-history navigation",
              pending[0].replace is True, f"replace={pending[0].replace}")


# --- 7. a clean page emits no navigation task ---------------------------
def t7_clean_page_no_pending_nav():
    host = _host_with_script('console.log("no nav");', "https://example.com/")
    check("clean page emits zero navigation tasks",
          len(host._pending_nav) == 0, f"got {host._pending_nav}")


# --- 8. absolute replace targets pass through unmodified ---------------
def t8_absolute_replace_passthrough():
    host = _host_with_script(
        'location.replace("https://other.example.com/next");',
        "https://example.com/")
    pending = host._pending_nav
    check("absolute replace passes through the target",
          len(pending) == 1 and pending[0].target == "https://other.example.com/next",
          f"got {pending}")


def main():
    t1_live_location_is_toy_location()
    t2_location_parts_match_legacy_keys()
    t2b_location_non_default_port_readable()
    t3_replace_baidu_shape()
    t4_assign_relative_resolves()
    t5_href_assign_semantics()
    t6_reload_is_replace_of_self()
    t7_clean_page_no_pending_nav()
    t8_absolute_replace_passthrough()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P34.8 (B05 location navigation bridge) tests passed.")


if __name__ == "__main__":
    main()


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("t1_live_location_is_toy_location", t1_live_location_is_toy_location))
TESTS.append(("t2_location_parts_match_legacy_keys", t2_location_parts_match_legacy_keys))
TESTS.append(("t2b_location_non_default_port_readable", t2b_location_non_default_port_readable))
TESTS.append(("t3_replace_baidu_shape", t3_replace_baidu_shape))
TESTS.append(("t4_assign_relative_resolves", t4_assign_relative_resolves))
TESTS.append(("t5_href_assign_semantics", t5_href_assign_semantics))
TESTS.append(("t6_reload_is_replace_of_self", t6_reload_is_replace_of_self))
TESTS.append(("t7_clean_page_no_pending_nav", t7_clean_page_no_pending_nav))
TESTS.append(("t8_absolute_replace_passthrough", t8_absolute_replace_passthrough))
