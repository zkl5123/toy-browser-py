from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
"""Final P0/P1 regression probe (retest report §layout + §js + §csp)."""
from toy_browser.document import dom; from toy_browser.style import css; from toy_browser.layout import layout; from toy_browser.scripting import js
from toy_browser.network import net

def find_box(root, id_):
    """Find a layout box by element id."""
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element() and bx.dom.attr('id') == id_:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None

def find_tag_box(root, tag):
    stack = [root]
    while stack:
        bx = stack.pop()
        if bx.dom is not None and bx.dom.is_element() and (bx.dom.tag or '').lower() == tag:
            return bx
        for c in reversed(bx.children):
            stack.append(c)
    return None

def find_dom_node(root, id_):
    """Find a DOM node by id (Node, not layout box)."""
    stack = [root]
    while stack:
        n = stack.pop()
        if n.is_element() and n.attr('id') == id_:
            return n
        for c in n.children:
            stack.append(c)
    return None

def lay(html, w=300.0, h=400.0):
    r = dom.parse(html)
    s = css.apply(r, '')
    return layout.layout(s, w, h)

ok = 0
bad = 0
def check(label, got, want, tol=0.6):
    global ok, bad
    good = abs(got - want) <= tol
    mark = 'OK  ' if good else 'FAIL'
    if good:
        ok += 1
    else:
        bad += 1
    print(f'{mark} {label}: got {got} want {want}')

# --- 1. flex row-reverse center ---
# 3x(40px content + 8px margin each side) = mb 56 each.
# free = 300 - 3*56 = 132. center x_off = 66.
# a (DOM-first, rightmost in reverse): mb_left = 66 + 56+56 + ... 
# suffix: a.mb_left = x_off + mb_b + mb_c + 2*step(0) = 66+112=178 (mb edge)
# a.content_x = a.mb_left + margin_l = 178+8 = 186
# b.mb_left = 66+56 = 122; b.content_x = 122+8 = 130
# c.mb_left = 66; c.content_x = 66+8 = 74
lr = lay(
    '<div id=f style="display:flex;flex-direction:row-reverse;justify-content:center;width:300px;height:200px">'
    '<div id=a style="width:40px;margin:8px"></div>'
    '<div id=b style="width:40px;margin:8px"></div>'
    '<div id=c style="width:40px;margin:8px"></div></div>')
a, b, c = find_box(lr, 'a'), find_box(lr, 'b'), find_box(lr, 'c')
check('row-rev-center a.x (content-box)', a.rect.x, 186)
check('row-rev-center b.x', b.rect.x, 130)
check('row-rev-center c.x', c.rect.x, 74)

# --- 2. flex row-reverse end (no margins) ---
# 3x40, total=120, free=180. reverse end packs left: c=0, b=40, a=80.
lr = lay(
    '<div id=f style="display:flex;flex-direction:row-reverse;justify-content:flex-end;width:300px;height:200px">'
    '<div id=a style="width:40px"></div><div id=b style="width:40px"></div>'
    '<div id=c style="width:40px"></div></div>')
a, b, c = find_box(lr, 'a'), find_box(lr, 'b'), find_box(lr, 'c')
check('row-rev-end a.x', a.rect.x, 80)
check('row-rev-end b.x', b.rect.x, 40)
check('row-rev-end c.x', c.rect.x, 0)

# --- 3. gap ---
lr = lay(
    '<div id=f style="display:flex;gap:10px;width:300px;height:100px">'
    '<div id=a style="width:50px"></div><div id=b style="width:50px"></div>'
    '<div id=c style="width:50px"></div></div>')
a, b, c = find_box(lr, 'a'), find_box(lr, 'b'), find_box(lr, 'c')
check('gap b.x', b.rect.x, 60)
check('gap c.x', c.rect.x, 120)

# --- 4. single item space-around / evenly ---
lr = lay(
    '<div id=f style="display:flex;justify-content:space-around;width:300px;height:100px">'
    '<div id=a style="width:40px"></div></div>')
check('space-around single x', find_box(lr, 'a').rect.x, 130)

# --- 5. column center ---
lr = lay(
    '<div id=f style="display:flex;flex-direction:column;justify-content:center;width:300px;height:300px">'
    '<div id=a style="height:30px"></div><div id=b style="height:40px"></div>'
    '<div id=c style="height:50px"></div></div>', 300.0, 300.0)
a, b, c = find_box(lr, 'a'), find_box(lr, 'b'), find_box(lr, 'c')
check('col-center a.y', a.rect.y, 90)
check('col-center b.y', b.rect.y, 120)
check('col-center c.y', c.rect.y, 160)

# --- 6. column margin ---
lr = lay(
    '<div id=f style="display:flex;flex-direction:column;width:300px;height:300px">'
    '<div id=a style="height:30px;margin:4px"></div></div>', 300.0, 300.0)
check('col-margin4 a.y', find_box(lr, 'a').rect.y, 4)

# --- 7. grid 1fr 2fr + gap ---
lr = lay(
    '<div id=g style="display:grid;grid-template-columns:1fr 2fr;gap:10px;width:300px;height:200px">'
    '<div id=c1></div><div id=c2></div><div id=c3></div><div id=c4></div></div>')
c1, c2 = find_box(lr, 'c1'), find_box(lr, 'c2')
check('grid c1.w', round(c1.rect.w, 1), 96.7)
check('grid c2.w', round(c2.rect.w, 1), 193.3)

# --- 8. table auto-width (shrink-to-fit content-box) ---
lr = lay(
    '<table style="border-collapse:collapse"><tr>'
    '<td style="padding:4px;border:1px solid black">a</td>'
    '<td style="padding:4px;border:1px solid black">b</td></tr>'
    '<tr><td style="padding:4px;border:1px solid black">c</td>'
    '<td style="padding:4px;border:1px solid black">d</td></tr></table>', 300.0, 200.0)
t = find_tag_box(lr, 'table')
# col ≈ 8.9(natural) + 8(pad) − 1(halved border, collapse) ≈ 15.9 each;
# content w ≈ 31.8 (P33.R2-2 D3: collapse halves the shared border pair,
# and rect.w now stores the content width = total − full outer pair).
check('table w shrink-fit', round(t.rect.w, 1), 31.8, tol=2.0)

# --- 9. column-reverse adjacency ---
lr = lay(
    '<div id=f style="display:flex;flex-direction:column-reverse;width:100px;height:200px">'
    '<div id=a style="height:30px"></div><div id=b style="height:50px"></div></div>',
    100.0, 200.0)
a, b = find_box(lr, 'a'), find_box(lr, 'b')
check('col-rev b.y', b.rect.y, 120)
check('col-rev a.y', a.rect.y, 170)
check('col-rev b_bottom==a.y', b.rect.y + b.rect.h, a.rect.y)

# --- 10. column gap ---
lr = lay(
    '<div id=f style="display:flex;flex-direction:column;gap:10px;width:300px;height:300px">'
    '<div id=a style="height:30px"></div><div id=b style="height:40px"></div>'
    '<div id=c style="height:50px"></div></div>', 300.0, 300.0)
a, b, c = find_box(lr, 'a'), find_box(lr, 'b'), find_box(lr, 'c')
check('col-gap a.y', a.rect.y, 0)
check('col-gap b.y', b.rect.y, 40)
check('col-gap c.y', c.rect.y, 90)

# --- 11. JS object identity (=== on two distinct dicts) ---
tree = dom.parse('<div id="o">init</div>')
host = js.Host(tree, lambda s: None)
host.execute_all(external_scripts=[('/t.js', '''
var obj = {x: 1}; var obj2 = {x: 1};
document.getElementById("o").textContent = (obj === obj2) ? "FALSE" : "CORRECT";''')])
node = find_dom_node(tree, 'o')
check('js === dict identity', 1 if node.text_content() == 'CORRECT' else 0, 1, tol=0)

# --- 12. removeChild + getElementById returns null ---
tree = dom.parse('<div id="root"><div id="child">HERE</div></div>')
host = js.Host(tree, lambda s: None)
host.execute_all(external_scripts=[('/t2.js', '''
var root = document.getElementById("root");
root.removeChild(document.getElementById("child"));
var after = document.getElementById("child");
root.textContent = after === null ? "NULL" : "FOUND";''')])
node = find_dom_node(tree, 'root')
check('removeChild -> getElementById null', 1 if node.text_content() == 'NULL' else 0, 1, tol=0)

# --- 13. const reassignment catchable in try/catch ---
tree = dom.parse('<div id="res3">INIT</div>')
host = js.Host(tree, lambda s: None)
host.execute_all(external_scripts=[('/t3.js', '''
try { const x = 1; x = 2; } catch(e) {
  document.getElementById("res3").textContent = "CAUGHT"; }''')])
node = find_dom_node(tree, 'res3')
check('const reassign catch', 1 if node.text_content() == 'CAUGHT' else 0, 1, tol=0)

# --- 14. CSP script-src 'none' blocks inline script ---
tree = dom.parse('<div id="o4">SAFE</div><script>document.getElementById("o4").textContent="XSS";</script>')
d = net.parse_csp_directives("script-src 'none'")
host = js.Host(tree, lambda s: None)
host.execute_all(csp_directives=d)
node = find_dom_node(tree, 'o4')
check('CSP script-src none blocks inline', 1 if node.text_content() == 'SAFE' else 0, 1, tol=0)

# --- 15. CSP style-src 'self': same-origin allows, cross-origin blocks ---
d = net.parse_csp_directives("style-src 'self'")
same = not net._csp_blocks(d.get('style-src', []), 'https://example.com/s.css', doc_origin='https://example.com/')
cross = net._csp_blocks(d.get('style-src', []), 'https://cdn.other.com/s.css', doc_origin='https://example.com/')
check("CSP 'self' same-origin allowed", 1 if same else 0, 1, tol=0)
check("CSP 'self' cross-origin blocked", 1 if cross else 0, 1, tol=0)

# --- 16. external script executes ---
tree = dom.parse('<div id="o5">BEFORE</div>')
host = js.Host(tree, lambda s: None)
host.execute_all(external_scripts=[('/ext.js', 'document.getElementById("o5").textContent="EXECUTED";')])
node = find_dom_node(tree, 'o5')
check('external script executed', 1 if node.text_content() == 'EXECUTED' else 0, 1, tol=0)

print(f'\n{ok} ok, {bad} fail')
