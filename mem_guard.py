"""P33.R1 — memory governor for low-spec hosts.

Hard cap: process must not exceed 6 GB of Python-heap memory.  The
TTF parser already enforces a 12 MiB font-file cap and a 4096-entry
advance array; this module adds a *runtime* watchdog that tracks
Python's own heap growth (via ``tracemalloc``) and refuses to load
additional font faces when the process is near the cap.

Design
------
- A single module-level ``tracemalloc`` domain is started lazily on
  the first :func:`can_load_face` call.
- :func:`heap_mb` returns the current process heap in MiB.
- :func:`can_load_face(face_bytes, max_mb=6000) -> bool` is the
  per-face gate used by :mod:`ttf_font`.  A face whose file is
  larger than ``MAX_FACE_MB`` is rejected; a face whose parse would
  push the process past the cap is also rejected (the caller falls
  back to the bitmap font).

The 6 GB cap is chosen for the project's target hardware.  The
governor is pure stdlib and adds no external dependency.
"""
from __future__ import annotations

import threading

# 6 GB hard cap (user requirement: never exceed 8 GB; 6 GB is the
# working ceiling that leaves 2 GB headroom for the OS + Tk + other
# processes on the target host).
MEMORY_CAP_MB = 6000

# Per-face parse gate: a single TTF/TTC file may be at most this many
# MiB before we refuse to parse it.  12 MiB covers simhei (9.7 MB)
# and rejects the 17-20 MB simsun / msyh collections on low-spec hosts.
MAX_FACE_MB = 12

_lock = threading.Lock()
_started = False


def _ensure_started() -> None:
    """Lazily start the tracemalloc domain (thread-safe, idempotent).

    ``tracemalloc.start(n)`` starts a *new domain* (domain id ``n``)
    by default.  We want the default domain (id 0) so that
    :func:`get_traced_memory` (which returns ``(current, peak)``)
    works.  ``start()`` with no argument is the idempotent call for
    the default domain.
    """
    global _started
    with _lock:
        if _started:
            return
        try:
            import tracemalloc
            tracemalloc.start()  # default domain, idempotent.
        except ImportError:
            pass
        _started = True


def heap_mb() -> float:
    """Current process heap in MiB (0.0 when tracemalloc is off)."""
    _ensure_started()
    import tracemalloc
    cur, _peak = tracemalloc.get_traced_memory()
    return cur / (1024 * 1024)


def peak_mb() -> float:
    """Peak process heap in MiB since tracemalloc was started."""
    _ensure_started()
    import tracemalloc
    _cur, peak = tracemalloc.get_traced_memory()
    return peak / (1024 * 1024)


def check_budget(max_mb: int = MEMORY_CAP_MB) -> bool:
    """True when the process heap is under *max_mb* MiB.

    Callers use this as a *soft* pre-flight check: when it returns
    False the renderer skips loading additional font faces and the
    caller falls back to the bitmap font.
    """
    return heap_mb() < max_mb


def can_load_face(face_bytes: int, max_mb: int = MEMORY_CAP_MB) -> bool:
    """Per-face gate: the face file fits the byte budget AND the
    process is still under the heap cap.

    ``face_bytes`` is the on-disk size of the TTF/TTC file.  The
    face is *loaded* into Python objects (dicts, lists of tuples)
    that typically occupy 2-4x the file size in memory, so we
    allow a face when ``heap + face_bytes * 3 < max_mb``.
    """
    if face_bytes > MAX_FACE_MB * 1024 * 1024:
        return False
    est_growth = face_bytes * 3
    cur_bytes = int(heap_mb() * 1024 * 1024)
    return (cur_bytes + est_growth) < max_mb * 1024 * 1024


def process_rss_mb() -> float:
    """Best-effort process RSS in MiB.  Returns the tracemalloc
    heap as a lower-bound proxy (no psutil dependency).
    """
    return heap_mb()


def enforce_cap(max_mb: int = MEMORY_CAP_MB) -> bool:
    """One-shot gate used by the TTF loader: True when the face may
    be parsed, False when the process is already at the cap.

    The caller (``ttf_font.load_face_best_cjk`` /
    ``ttf_font.load_ttc``) wraps its parse call with this check so
    a low-spec host does not OOM on a 17 MB CJK collection.
    """
    return check_budget(max_mb)
