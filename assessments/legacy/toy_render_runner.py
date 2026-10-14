"""toy-browser render probes + pixel diff vs Edge screenshots."""
import json, os, sys, struct, zlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import browser

CASES = json.load(open(os.path.join(os.path.dirname(__file__), 'render_cases.json'), encoding='utf-8'))
D = os.path.dirname(os.path.abspath(__file__))

def read_png_pixels(path, w, h):
    """Minimal PNG reader (8-bit RGB or RGBA, no filters other than None/sub)."""
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos = 8
    idat = b''
    bit_depth = color_type = None
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos+4])[0]
        typ = data[pos+4:pos+8]
        body = data[pos+8:pos+8+ln]
        if typ == b'IHDR':
            w2, h2, bit_depth, color_type = struct.unpack('>IIBB', body[:10])
        elif typ == b'IDAT':
            idat += body
        pos += 12 + ln
        if typ == b'IEND':
            break
    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    stride = w * channels
    out = [[0]*w for _ in range(h)]
    prev = bytearray(stride)
    i = 0
    for y in range(h):
        ftype = raw[i]; i += 1
        line = bytearray(raw[i:i+stride]); i += stride
        if ftype == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x-channels]) & 0xFF
        elif ftype == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif ftype == 3:
            for x in range(stride):
                left = line[x-channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif ftype == 4:
            for x in range(stride):
                left = line[x-channels] if x >= channels else 0
                up = prev[x]
                up_left = prev[x-channels] if x >= channels else 0
                p = left + up - up_left
                pa, pb, pc = abs(p-left), abs(p-up_left), abs(p-up)
                pr = left if (pa <= pb and pa <= pc) else (up if pb <= pc else up_left)
                line[x] = (line[x] + pr) & 0xFF
        prev = line
        for x in range(w):
            out[y][x] = (line[x*channels], line[x*channels+1], line[x*channels+2])
    return out

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
