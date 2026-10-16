"""p34r4_perf.py — P34.4 stage-timing benchmark (per-round).

Run: py -3.11 p34r4_perf.py
"""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[2]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import gc
import time

from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout
from toy_browser.rendering import paint_engine


def _make_doc(n: int) -> str:
    """Build an n-node test document (mix of divs, spans, CJK text)."""
    items = []
    for i in range(n):
        if i % 50 == 0:
            items.append(f'<div id="d{i}"><h{i % 100}>节点 {i} 标题</h{i % 100}></div>')
        elif i % 50 == 1:
            items.append(
                f'<p id="p{i}"><span style="color:#3333ff">段落 {i}</span> '
                f"包含中文文本 {'你好世界'.ljust(8, '字')} 用于光栅化测试。</p>"
            )
        elif i % 50 in (2, 3):
            items.append(f'<ul><li>列表项 {i}-{0}</li><li>列表项 {i}-1</li></ul>')
        else:
            items.append(f'<div class="c{i % 7}" id="c{i}">内容 {i}</div>')
    head = (
        "<!DOCTYPE html><html><head>"
        "<style>"
        "body { margin: 0; font-family: sans-serif; font-size: 14px; }"
        "div, p, ul, li, h1 { margin: 2px 0; padding: 1px 0; }"
        "h1 { font-size: 16px; } .c0 { color: red; }"
        ".c1 { background: yellow; } .c2 { border: 1px solid gray; }"
        "</style></head><body>" + "".join(items) + "</body></html>"
    )
    return head


def run(n: int, viewport: tuple = (1200, 800)) -> dict:
    html = _make_doc(n)
    gc.collect()
    t0 = time.perf_counter()
    root = dom.parse(html)
    t_parse = time.perf_counter() - t0
    gc.collect()
    t0 = time.perf_counter()
    styled = css.apply(root, "")
    t_css = time.perf_counter() - t0
    gc.collect()
    t0 = time.perf_counter()
    tree = layout.layout(styled, 1200, 800)
    t_layout = time.perf_counter() - t0
    gc.collect()
    t0 = time.perf_counter()
    dl = paint_engine.emit_commands(tree)
    t_emit = time.perf_counter() - t0
    gc.collect()
    vw, vh = viewport
    t0 = time.perf_counter()
    bmp = paint_engine.rasterize(dl, vw, vh)
    t_rast = time.perf_counter() - t0
    total = t_parse + t_css + t_layout + t_emit + t_rast
    n_text = sum(1 for c in dl.commands if c.kind == "text")
    return {
        "n_nodes": n,
        "parse_ms": t_parse * 1000,
        "css_ms": t_css * 1000,
        "layout_ms": t_layout * 1000,
        "emit_ms": t_emit * 1000,
        "rasterize_ms": t_rast * 1000,
        "total_ms": total * 1000,
        "display_cmds": len(dl.commands),
        "text_cmds": n_text,
    }


def main():
    for n in (1000, 3000, 10000):
        r = run(n)
        print(f"--- {n}-node document ---")
        for k in ("parse_ms", "css_ms", "layout_ms", "emit_ms", "rasterize_ms", "total_ms"):
            print(f"  {k.replace('_ms', ' (ms)'):<14} {r[k]:>10.1f}")
        print(f"  display list:   {r['display_cmds']} commands "
              f"({r['text_cmds']} text)")


if __name__ == "__main__":
    main()
