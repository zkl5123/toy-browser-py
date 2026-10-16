"""DOM parsing + hit-testing — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


from toy_browser.app import browser
from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.scripting import js
from toy_browser.typography import text
from toy_browser.rendering import paint
from toy_browser.rendering.paint import Bitmap, Color
import sys

# Shared helpers (defined in test.py, needed by multiple modules)
from test_helpers import (
    _a11y_roles,
    _find_a11y_role,
    _find_box,
    _find_box_by_id,
    _find_box_by_id_or_tag,
    _find_box_by_tag,
    _greens,
    _p20_bitmap,
    _p20_first_valid_outline,
    _p20_ink,
    _p20_metrics,
    _p21_start_server,
    _p22_start_server,
    _p29_js_logs,
    _p30_doc_html,
    _p30_host,
    _p30_qsel_all,
    _p32_cleanup,
    _p32_tmp,
    _pixel_diff,
    _reds,
    _rmtree_quiet,
    find2_children,
)

TESTS: list = []

def test_dom_parse():
    root = dom.parse("<div id='a' class='c'>hi<span>there</span></div>")
    div = root.children[0]
    assert div.attr("id") == "a"
    assert div.has_class("c")
    assert div.text_content() == "hithere"
    assert root.find_by_id("a") is div
    assert div.query_selector_all(".c")[0] is div
    assert div.query_selector_all("#a")[0] is div



TESTS.append(("test_dom_parse", test_dom_parse))
def test_dom_implicit_close():
    root = dom.parse("<p>one</p><p>two</p>")
    assert len(root.children) == 2
    assert root.children[0].tag == "p"
    assert root.children[1].tag == "p"



TESTS.append(("test_dom_implicit_close", test_dom_implicit_close))
def test_hit_test():
    root = dom.parse('<button id="b">click</button>')
    styled = css.apply(root, "button { margin: 0; padding: 4px; }")
    layout_root = layout.layout(styled, 400.0)
    hit = layout.hit_test(layout_root, 5.0, 5.0)
    assert hit is not None
    assert hit.dom is not None and hit.dom.attr("id") == "b", (
        f"hit-test should find #b, got dom={hit.dom}"
    )



TESTS.append(("test_hit_test", test_hit_test))
