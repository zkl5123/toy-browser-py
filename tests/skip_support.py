"""Shared test-support helpers for the regression suite.

R7-Q01 (round-7 assessment, 逐项改进实施方案 §2): the runner must not
count an environment-dependent early ``return`` as a functional PASS.
Test modules that cannot run in the current environment (no tkinter,
no real font fixture, …) raise :class:`SkipTest` instead; ``run.py``
records them as an explicit SKIP result with the reason, keeps them in
the denominator, and never lets them masquerade as passes.

Usage inside a ``tests/test_*.py`` module::

    from skip_support import SkipTest

    @_reg
    def test_needs_tk():
        if not _tk_available():
            raise SkipTest("tkinter not available in this environment")
        ...
"""
from __future__ import annotations


class SkipTest(Exception):
    """Raised by a test that cannot run in the current environment.

    The runner maps this to a SKIP result (with ``str(reason)`` kept as
    the skip reason) instead of a PASS.
    """

    def __init__(self, reason: str = ""):
        super().__init__(reason)
        self.reason = reason


def skip(reason: str = "") -> None:
    """Convenience helper: unconditionally skip with *reason*."""
    raise SkipTest(reason)


def require_tk() -> None:
    """Raise :class:`SkipTest` when tkinter is unavailable.

    R7-Q01 — GUI-driving tests must distinguish "the environment has no
    Tk" (SKIP) from "the wiring is broken" (FAIL).  Call at the top of
    any test that needs a real ``tkinter``.
    """
    try:
        import tkinter  # noqa: F401
    except Exception as e:  # pragma: no cover - environment dependent
        raise SkipTest(f"tkinter not available: {e}")


def require_wgpu() -> None:
    """Raise :class:`SkipTest` when wgpu is unavailable."""
    try:
        import wgpu  # noqa: F401
    except Exception as e:  # pragma: no cover - environment dependent
        raise SkipTest(f"wgpu not available: {e}")
