"""P3.4 / P11.1 — toy CSS transition engine.

Interpolates a property value over N frames between two states.
Supports color (r, g, b), scalar (width, opacity, …) and
transform (translate x/y) properties.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.rendering.paint import Color


@dataclass
class TransitionSpec:
    """A single transition: property name, start value, end value, duration in frames."""
    property: str  # "background", "opacity", "width", "height", "margin-left"
    start: object  # Color or float
    end: object  # Color or float
    frames: int  # number of interpolation frames

    def value_at(self, t: float):
        """Linear interpolation at t in [0, 1].

        P11.1 — extended to support transform (x, y) as a tuple
        ``("translate", x, y)`` in addition to the existing
        Color / scalar / Length cases.
        """
        if t <= 0.0:
            return self.start
        if t >= 1.0:
            return self.end
        # P11.1 — transform (translate x, y) tuples.
        if (isinstance(self.start, tuple) and isinstance(self.end, tuple)
                and len(self.start) == 3 and len(self.end) == 3
                and self.start[0] == "translate" and self.end[0] == "translate"):
            x = self.start[1] + (self.end[1] - self.start[1]) * t
            y = self.start[2] + (self.end[2] - self.start[2]) * t
            return ("translate", x, y)
        if isinstance(self.start, Color) and isinstance(self.end, Color):
            r = int(self.start.r + (self.end.r - self.start.r) * t)
            g = int(self.start.g + (self.end.g - self.start.g) * t)
            b = int(self.start.b + (self.end.b - self.start.b) * t)
            a = int(self.start.a + (self.end.a - self.start.a) * t)
            return Color(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0, min(255, a)))
        if isinstance(self.start, (int, float)) and isinstance(self.end, (int, float)):
            return self.start + (self.end - self.start) * t
        return self.start


class TransitionEngine:
    """Holds active transitions and produces per-frame style overrides."""

    def __init__(self) -> None:
        self.active: list[TransitionSpec] = []
        self.frame: int = 0

    def add(self, spec: TransitionSpec) -> None:
        self.active.append(spec)

    def is_running(self) -> bool:
        return any(spec.frames > 0 for spec in self.active)

    def advance(self) -> bool:
        """Advance one frame. Returns True if any transition is still running.

        P11.1 — a spec is considered running while ``frame < frames``;
        the moment ``frame`` reaches ``frames`` the spec is removed and
        the engine reports that no transitions remain.
        """
        if not self.active:
            return False
        self.frame += 1
        still = []
        for spec in self.active:
            if self.frame < spec.frames:
                still.append(spec)
        self.active = still
        return bool(self.active)

    def frame_overrides(self) -> dict:
        """Return {property: value} for the current frame (all active specs at the same t).

        P11.1 — multi-property support: ``add`` several specs with different
        property names and this method merges them into one dict so the
        caller can apply every override in a single cascade pass.
        """
        overrides: dict = {}
        for spec in self.active:
            t = min(1.0, self.frame / max(1, spec.frames))
            overrides[spec.property] = spec.value_at(t)
        return overrides

    def parse_transition_css(self, value: str, start_bg: Color, end_bg: Color) -> TransitionSpec:
        """Parse 'transition: background 1s' style CSS into a TransitionSpec (toy)."""
        # Toy: assume "property Ns" or "property Nframes"
        parts = value.strip().split()
        if not parts:
            return TransitionSpec("background", start_bg, end_bg, 3)
        prop = parts[0]
        frames = 3  # default
        if len(parts) > 1:
            dur = parts[1]
            if dur.endswith("s"):
                try:
                    frames = int(float(dur[:-1]) * 10)  # 1s ≈ 10 frames
                except ValueError:
                    frames = 3
            else:
                try:
                    frames = int(dur)
                except ValueError:
                    frames = 3
        return TransitionSpec(prop, start_bg, end_bg, max(1, frames))


    # P11.1 — multi-property convenience: create one spec per (prop, start,
    # end, frames) tuple and add them all in a single call.
    def add_multi(self, specs: list[TransitionSpec]) -> None:
        for s in specs:
            self.add(s)


def lerp_color(c1: Color, c2: Color, t: float) -> Color:
    """Linear interpolate two colors."""
    if t <= 0.0:
        return c1
    if t >= 1.0:
        return c2
    r = int(c1.r + (c2.r - c1.r) * t)
    g = int(c1.g + (c2.g - c1.g) * t)
    b = int(c1.b + (c2.b - c1.b) * t)
    a = int(c1.a + (c2.a - c1.a) * t)
    return Color(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)), max(0, min(255, a)))


def lerp_scalar(v1: float, v2: float, t: float) -> float:
    """Linear interpolate two scalars."""
    return v1 + (v2 - v1) * max(0.0, min(1.0, t))


def lerp_value(v1, v2, t: float):
    """P4.1 — interpolate between two CSS values (Color, Length, Keyword, or scalar).

    Used by the animation engine to interpolate keyframe declarations.
    """
    from toy_browser.style.css import Length, Keyword
    if isinstance(v1, Color) and isinstance(v2, Color):
        return lerp_color(v1, v2, t)
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        return lerp_scalar(v1, v2, t)
    if isinstance(v1, Length) and isinstance(v2, Length):
        r = lerp_scalar(v1.value, v2.value, t)
        return Length(r, v1.unit)
    if isinstance(v1, Keyword) and isinstance(v2, Keyword):
        # Toy: try to parse both as numbers; if both are numeric, lerp; else snap.
        try:
            f1 = float(v1.value)
            f2 = float(v2.value)
            return Keyword(str(lerp_scalar(f1, f2, t)))
        except (ValueError, TypeError):
            return v1 if t < 0.5 else v2
    # P12.1 — mix Keyword-with-numeric vs scalar: treat both as numeric.
    from toy_browser.style.css import Length, Keyword
    if isinstance(v1, Keyword) and isinstance(v2, (int, float)):
        try:
            f1 = float(v1.value)
        except (ValueError, TypeError):
            f1 = None
        if f1 is not None:
            return lerp_scalar(f1, v2, t)
        return v1 if t < 0.5 else v2
    if isinstance(v2, Keyword) and isinstance(v1, (int, float)):
        try:
            f2 = float(v2.value)
        except (ValueError, TypeError):
            f2 = None
        if f2 is not None:
            return lerp_scalar(v1, f2, t)
        return v1 if t < 0.5 else v2
    # P12.1 — Keyword vs Keyword where one or both are numeric.
    if isinstance(v1, Keyword) and isinstance(v2, Keyword):
        try:
            f1 = float(v1.value)
            f2 = float(v2.value)
            return lerp_scalar(f1, f2, t)
        except (ValueError, TypeError):
            return v1 if t < 0.5 else v2
    # P11.1 — transform tuples ("translate", x, y).
    if (isinstance(v1, tuple) and isinstance(v2, tuple)
            and len(v1) == 3 and len(v2) == 3
            and v1[0] == "translate" and v2[0] == "translate"):
        x = lerp_scalar(v1[1], v2[1], t)
        y = lerp_scalar(v1[2], v2[2], t)
        return ("translate", x, y)
    # Fallback: snap at the midpoint.
    return v1 if t < 0.5 else v2


def parse_transition_css_multi(value: str, start_vals: dict, end_vals: dict) -> list[TransitionSpec]:
    """P11.1 — parse a multi-property transition CSS value.

    Supports::

        transition: background 1s, opacity 1s, transform 1s

    For each comma-separated clause (``property duration``), returns a
    ``TransitionSpec`` using the corresponding start/end values from
    ``start_vals`` / ``end_vals``.  Unknown properties default to a 3-frame
    spec with the raw start/end values.
    """
    import re as _re

    specs: list[TransitionSpec] = []
    clauses = [c.strip() for c in value.split(",") if c.strip()]
    for clause in clauses:
        parts = clause.split()
        if not parts:
            continue
        prop = parts[0]
        frames = 3
        if len(parts) > 1:
            dur = parts[1]
            if dur.endswith("s"):
                try:
                    frames = int(float(dur[:-1]) * 10)
                except ValueError:
                    frames = 3
            else:
                try:
                    frames = int(dur)
                except ValueError:
                    frames = 3
        start_v = start_vals.get(prop, 0)
        end_v = end_vals.get(prop, 0)
        specs.append(TransitionSpec(prop, start_v, end_v, max(1, frames)))
    return specs
