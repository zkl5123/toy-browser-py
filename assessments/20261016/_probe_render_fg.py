import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
import browser, paint

# R4 stage 5 — measure the 4 render_* cases' foreground (non-white)
# coverage in the toy rasterizer and compare against the Edge
# screenshots' non-white share (from render_diffs.json's
# edge_nonwhite_pct).  The plan's acceptance for stage 5 is that the
# render_basic foreground-union difference drops from its 81% legacy
# value; we report the toy/edge non-white shares so the progress is
# visible even without PIL.

RENDER = {
    "render_basic":
        "<html><head><style>*{box-sizing:border-box} body{margin:0;font-family:Arial,sans-serif;font-size:16px} h1{color:#c00;font-size:28px} .box{width:240px;height:80px;background:#eef;border:2px solid #039;margin:12px;padding:8px} p{line-height:1.5} table{border-collapse:collapse} td{border:1px solid #999;padding:6px}</style></head><body><h1>Hello World</h1><div class='box'>A box with text and <b>bold</b>, <i>italic</i> and <span style='color:#090'>green</span>.</div><p>Some paragraph text with a <a href='#'>link</a> and more words that wrap around the container width because they continue on and on.</p><table><tr><td>one</td><td>two</td></tr><tr><td>three</td><td>four</td></tr></table></body></html>",
    "render_cjk":
        "<html><head><style>body{margin:0;font-size:16px}</style></head><body><h1>浏览器测试</h1><p>这是中文文本，用于测试中日韩字符的渲染与换行行为。</p><div style='width:200px;border:1px solid #ccc'>中文短句会逐字断行：浏览器引擎的测试页面。</div></body></html>",
    "render_emoji_symbols":
        "<html><head><style>body{margin:0;font-size:16px}</style></head><body><p>Math: ∑∫√∞≠≤≥ π Ω ❤ ★ ☺ → ∂</p><p>Digits: ٠١٢٣٤ ①②③</p></body></html>",
    "render_forms":
        "<html><head><style>*{box-sizing:border-box} body{margin:0;font-size:14px} form{border:1px solid #ccc;padding:12px;width:320px} label{display:block;margin:6px 0}</style></head><body><form><label>Name <input type='text' value='John'></label><label>Email <input type='email' value='j@x.com'></label><label>Choose <select><option>A</option><option selected>B</option></select></label><button type='button'>Click me</button></form></body></html>",
}

EDGE_NONWHITE = json.loads(
    Path(__file__).resolve().parent.joinpath('render_diffs.json').read_text(encoding='utf-8'))

print(f"{'case':20s} {'toy%':>6s} {'edge%':>6s}  (non-white foreground share)")
for name, html in RENDER.items():
    bmp = browser.render_document(html, '', 800, 600)
    w, h = bmp.width, bmp.height
    fg = 0
    for y in range(h):
        base = y * w
        for x in range(w):
            v = bmp.pixels[base + x]
            r_, g_, b_ = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
            if not (r_ > 245 and g_ > 245 and b_ > 245):
                fg += 1
    toy_pct = 100.0 * fg / (w * h)
    edge_pct = EDGE_NONWHITE[name]['edge_nonwhite_pct']
    print(f"{name:20s} {toy_pct:6.2f} {edge_pct:6.2f}")
