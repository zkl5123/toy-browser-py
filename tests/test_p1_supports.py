"""GPT P1-1 — @supports real property check (capability table).

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


import css


def test_p1_supports_display_grid():
    """display: grid — supported property → True."""
    assert css.supports_matches("display: grid") is True


def test_p1_supports_display_flex():
    """display: flex — supported property → True."""
    assert css.supports_matches("display: flex") is True


def test_p1_supports_position_absolute():
    """position: absolute — supported → True."""
    assert css.supports_matches("position: absolute") is True


def test_p1_supports_position_static():
    """position: static — supported → True."""
    assert css.supports_matches("position: static") is True


def test_p1_supports_position_fixed():
    """position: fixed — supported → True."""
    assert css.supports_matches("position: fixed") is True


def test_p1_supports_color_red():
    """color: red — supported property → True."""
    assert css.supports_matches("color: red") is True


def test_p1_supports_font_size():
    """font-size: 16px — supported property → True."""
    assert css.supports_matches("font-size: 16px") is True


def test_p1_supports_overflow_hidden():
    """overflow: hidden — supported property → True."""
    assert css.supports_matches("overflow: hidden") is True


def test_p1_supports_position_sticky_false():
    """position: sticky — unsupported value → False."""
    assert css.supports_matches("position: sticky") is False


def test_p1_supports_paren_sticky_false():
    """(position: sticky) — parenthesised form → False."""
    assert css.supports_matches("(position: sticky)") is False


def test_p1_supports_unknown_prop_false():
    """unknown-prop: 1px — unknown property → False."""
    assert css.supports_matches("unknown-prop: 1px") is False


def test_p1_supports_not_sticky_true():
    """not (position: sticky) — negation → True."""
    assert css.supports_matches("not (position: sticky)") is True


def test_p1_supports_and_combination():
    """display: grid and color: red → True."""
    assert css.supports_matches("display: grid and color: red") is True


def test_p1_supports_or_with_unsupported():
    """position: sticky or display: grid → True (OR)."""
    assert css.supports_matches("position: sticky or display: grid") is True


def test_p1_supports_and_with_unsupported():
    """position: sticky and display: grid → False (AND)."""
    assert css.supports_matches("position: sticky and display: grid") is False


def test_p1_supports_selector_always_true():
    """selector(:dir(ltr)) — toy always-True form."""
    assert css.supports_matches("selector(:dir(ltr))") is True


def test_p1_supports_font_tech_always_true():
    """font-tech(opentype) — toy always-True form."""
    assert css.supports_matches("font-tech(opentype)") is True


def test_p1_supports_empty_condition_true():
    """empty condition → True (vacuously satisfied)."""
    assert css.supports_matches("") is True


def test_p1_supports_blocks_integration():
    """@supports blocks: unsupported property excluded, supported included."""
    css_text = """
    @supports (position: sticky) { .a { position: sticky; } }
    @supports (display: grid)    { .b { display: grid; } }
    """
    rules = css.parse_supports_blocks(css_text)
    assert len(rules) == 2, f"expected 2 @supports blocks, got {len(rules)}"
    included = [sr.condition for sr in rules if css.supports_matches(sr.condition)]
    assert any("display" in c for c in included), "display: grid should be included"
    assert not any("sticky" in c for c in included), "position: sticky should be excluded"


TESTS = [
    ("p1_supports_display_grid", test_p1_supports_display_grid),
    ("p1_supports_display_flex", test_p1_supports_display_flex),
    ("p1_supports_position_absolute", test_p1_supports_position_absolute),
    ("p1_supports_position_static", test_p1_supports_position_static),
    ("p1_supports_position_fixed", test_p1_supports_position_fixed),
    ("p1_supports_color_red", test_p1_supports_color_red),
    ("p1_supports_font_size", test_p1_supports_font_size),
    ("p1_supports_overflow_hidden", test_p1_supports_overflow_hidden),
    ("p1_supports_position_sticky_false", test_p1_supports_position_sticky_false),
    ("p1_supports_paren_sticky_false", test_p1_supports_paren_sticky_false),
    ("p1_supports_unknown_prop_false", test_p1_supports_unknown_prop_false),
    ("p1_supports_not_sticky_true", test_p1_supports_not_sticky_true),
    ("p1_supports_and_combination", test_p1_supports_and_combination),
    ("p1_supports_or_with_unsupported", test_p1_supports_or_with_unsupported),
    ("p1_supports_and_with_unsupported", test_p1_supports_and_with_unsupported),
    ("p1_supports_selector_always_true", test_p1_supports_selector_always_true),
    ("p1_supports_font_tech_always_true", test_p1_supports_font_tech_always_true),
    ("p1_supports_empty_condition_true", test_p1_supports_empty_condition_true),
    ("p1_supports_blocks_integration", test_p1_supports_blocks_integration),
]
