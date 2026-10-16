"""toy-browser render probes + pixel diff vs Edge screenshots."""
import json, os, sys, struct, zlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from toy_browser.app import browser

CASES = json.load(open(os.path.join(os.path.dirname(__file__), 'render_cases.json'), encoding='utf-8'))
D = os.path.dirname(os.path.abspath(__file__))

def read_png_pixels(path, w, h):
    from PIL import Image
    with Image.open(path) as source:
        im = source.convert('RGB')
        assert im.size == (w, h)
        pixels = list(im.getdata())
    return [pixels[y*w:(y+1)*w] for y in range(h)]

W, H = 800, 600
results = {}
for name, html in CASES.items():
    bmp = browser.render_document(html, '', W, H)
    browser.write_png(bmp, os.path.join(D, f'toy_shot_{name}.png'))
    # load both
    tp = read_png_pixels(os.path.join(D, f'toy_shot_{name}.png'), W, H)
    ep = read_png_pixels(os.path.join(D, f'edge_shot_{name}.png'), W, H)
    n_white_like = 0; n_diff = 0; n_same = 0; total = W*H
    for y in range(H):
        for x in range(W):
            tr, tg, tb = tp[y][x]
            er, eg, eb = ep[y][x]
            d = abs(tr-er) + abs(tg-eg) + abs(tb-eb)
            if d <= 30:  # small diff tolerance
                n_same += 1
            else:
                n_diff += 1
            if er < 240 and eg < 240 and eb < 240:
                n_white_like += 1
    results[name] = {
        'diff_rate': round(n_diff/total*100, 2),
        'edge_nonwhite_pct': round(n_white_like/total*100, 2),
    }
    print(f"{name}: diff {results[name]['diff_rate']}% (edge non-white {results[name]['edge_nonwhite_pct']}%)")

with open(os.path.join(D, 'render_diffs.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, indent=1)
print('render_diffs.json written')
