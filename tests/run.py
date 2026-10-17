"""End-to-end tests for toy-browser-py (pure stdlib, no test framework).

GPT P1-2 — test.py is now a thin orchestrator. Individual test
functions live in module files:

  test_dom.py       DOM parsing + hit-testing
  test_css.py       CSS cascade + selectors + @media + @supports + calc
  test_js.py        JS engine tests
  test_layout.py    Layout: flex, grid, table, position, float, overflow
  test_paint.py     Paint: PNG, transitions, keyframes, opacity, filters
  test_net.py       Network: HTTP, cache, TLS, cookies, CORS/CSP
  test_browser.py   Browser-level: render_document, a11y, links, iframe
  test_helpers.py   Shared helpers (_reds, _pixel_diff, _find_box, …)
  test_r1.py … test_r5.py, test_p0_js_budget.py, test_p0_policy.py,
  test_p1_supports.py, test_p2.py, test_p3.py, test_p34*.py, test_mem.py,
  test_web_api.py   — pre-existing module test files

Run:  python test.py
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "tools" / "benchmarks"))
sys.path.insert(0, str(_ROOT / "tests"))
# Fixtures use project-relative paths (fonts/, demo.html).
os.chdir(_ROOT)


# GPT P1-2 — import test lists from module files.
from test_dom import TESTS as _DOM_TESTS
from test_css import TESTS as _CSS_TESTS
from test_js import TESTS as _JS_TESTS
from test_layout import TESTS as _LAYOUT_TESTS
from test_paint import TESTS as _PAINT_TESTS
from test_net import TESTS as _NET_TESTS
from test_browser import TESTS as _BROWSER_TESTS
from test_p2 import TESTS as _P2_TESTS
from test_p3 import TESTS as _P3_TESTS
from test_r1 import TESTS as _R1_TESTS
from test_r2 import TESTS as _R2_TESTS
from test_r3 import TESTS as _R3_TESTS
from test_r4 import TESTS as _R4_TESTS
from test_r4_regression import TESTS as _R4_REGRESSION_TESTS
from test_r4_perf import TESTS as _R4_PERF_TESTS
from test_r5 import TESTS as _R5_TESTS
from test_mem import TESTS as _MEM_TESTS
from test_web_api import TESTS as _WEB_API_TESTS
from test_p34 import TESTS as _P34_TESTS
from test_p34_r2 import TESTS as _P34_R2_TESTS
from test_p34_r3 import TESTS as _P34_R3_TESTS
from test_p34_r4 import TESTS as _P34_R4_TESTS
from test_p34_r5 import TESTS as _P34_R5_TESTS
from test_p34_r6 import TESTS as _P34_R6_TESTS
from test_p0_js_budget import TESTS as _P0_JS_BUDGET_TESTS
from test_p0_policy import TESTS as _P0_POLICY_TESTS
from test_p1_supports import TESTS as _P1_SUPPORTS_TESTS
from test_gui_scroll import TESTS as _GUI_SCROLL_TESTS
from test_gui_activation import TESTS as _GUI_ACTIVATION_TESTS
from test_focus_routing import TESTS as _FOCUS_ROUTING_TESTS
from test_editor import TESTS as _EDITOR_TESTS
from test_viewport import TESTS as _VIEWPORT_TESTS
from test_cpu_image import TESTS as _CPU_IMAGE_TESTS
from test_navigation_state import TESTS as _NAVIGATION_STATE_TESTS
from test_document_controller import TESTS as _DOCUMENT_CONTROLLER_TESTS
from test_tab_identity import TESTS as _TAB_IDENTITY_TESTS
from test_transport import TESTS as _TRANSPORT_TESTS
from test_diagnostics import TESTS as _DIAGNOSTICS_TESTS
from test_document_state_full import TESTS as _DOCUMENT_STATE_FULL_TESTS
from test_accessibility import TESTS as _ACCESSIBILITY_TESTS
from test_history_entry import TESTS as _HISTORY_ENTRY_TESTS
from test_tab_lifecycle import TESTS as _TAB_LIFECYCLE_TESTS
from test_document_renderer import TESTS as _DOCUMENT_RENDERER_TESTS
from test_form_control import TESTS as _FORM_CONTROL_TESTS
from test_diag_panel import TESTS as _DIAG_PANEL_TESTS
from test_style_cache import TESTS as _STYLE_CACHE_TESTS
from test_layout_incremental import TESTS as _LAYOUT_INCREMENTAL_TESTS
from test_display_list import TESTS as _DISPLAY_LIST_TESTS
from test_dirty_region import TESTS as _DIRTY_REGION_TESTS
from test_png_export import TESTS as _PNG_EXPORT_TESTS
from test_policy_regression import TESTS as _POLICY_REGRESSION_TESTS
from test_cpu_image_formats import TESTS as _CPU_IMAGE_FORMATS_TESTS
from test_inline_layout import TESTS as _INLINE_LAYOUT_TESTS

def main():
    tests = []
    tests.extend(_DOM_TESTS)
    tests.extend(_CSS_TESTS)
    tests.extend(_JS_TESTS)
    tests.extend(_LAYOUT_TESTS)
    tests.extend(_PAINT_TESTS)
    tests.extend(_NET_TESTS)
    tests.extend(_BROWSER_TESTS)
    tests.extend(_P2_TESTS)
    tests.extend(_P3_TESTS)
    tests.extend(_R1_TESTS)
    tests.extend(_R2_TESTS)
    tests.extend(_R3_TESTS)
    tests.extend(_R4_TESTS)
    tests.extend(_R4_REGRESSION_TESTS)
    tests.extend(_R4_PERF_TESTS)
    tests.extend(_R5_TESTS)
    tests.extend(_MEM_TESTS)
    tests.extend(_WEB_API_TESTS)
    tests.extend(_P34_TESTS)
    tests.extend(_P34_R2_TESTS)
    tests.extend(_P34_R3_TESTS)
    tests.extend(_P34_R4_TESTS)
    tests.extend(_P34_R5_TESTS)
    tests.extend(_P34_R6_TESTS)
    tests.extend(_P0_JS_BUDGET_TESTS)
    tests.extend(_P0_POLICY_TESTS)
    tests.extend(_P1_SUPPORTS_TESTS)
    tests.extend(_GUI_SCROLL_TESTS)
    tests.extend(_GUI_ACTIVATION_TESTS)
    tests.extend(_FOCUS_ROUTING_TESTS)
    tests.extend(_EDITOR_TESTS)
    tests.extend(_VIEWPORT_TESTS)
    tests.extend(_CPU_IMAGE_TESTS)
    tests.extend(_NAVIGATION_STATE_TESTS)
    tests.extend(_DOCUMENT_CONTROLLER_TESTS)
    tests.extend(_TAB_IDENTITY_TESTS)
    tests.extend(_TRANSPORT_TESTS)
    tests.extend(_DIAGNOSTICS_TESTS)
    tests.extend(_DOCUMENT_STATE_FULL_TESTS)
    tests.extend(_ACCESSIBILITY_TESTS)
    tests.extend(_HISTORY_ENTRY_TESTS)
    tests.extend(_TAB_LIFECYCLE_TESTS)
    tests.extend(_DOCUMENT_RENDERER_TESTS)
    tests.extend(_FORM_CONTROL_TESTS)
    tests.extend(_DIAG_PANEL_TESTS)
    tests.extend(_STYLE_CACHE_TESTS)
    tests.extend(_LAYOUT_INCREMENTAL_TESTS)
    tests.extend(_DISPLAY_LIST_TESTS)
    tests.extend(_DIRTY_REGION_TESTS)
    tests.extend(_PNG_EXPORT_TESTS)
    tests.extend(_POLICY_REGRESSION_TESTS)
    tests.extend(_CPU_IMAGE_FORMATS_TESTS)
    tests.extend(_INLINE_LAYOUT_TESTS)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  ok  {name}")
        except Exception as e:
            failed.append((name, e))
            import traceback
            print(f" FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print(f"FAILED: {[(n, str(e)) for n, e in failed]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
