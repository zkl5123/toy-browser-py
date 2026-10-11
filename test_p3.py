"""P33.P3 — @font-face parsing + SVG/video/audio placeholder rendering."""
import dom
import css
import layout
import browser
import paint_engine


# ------------------------------------------------------------------
# @font-face
# ------------------------------------------------------------------

def test_p3_font_face_parse_basic():
    """P33.P3 — a simple @font-face block parses to a FontFace with
    family, src, weight, and style."""
    css_text = """
    @font-face {
        font-family: 'MyFont';
        src: url('fonts/myfont.woff2') format('woff2');
        font-weight: 400;
        font-style: normal;
    }
    """
    faces = css.parse_font_faces(css_text)
    assert len(faces) == 1, f"expected 1 font face, got {len(faces)}"
    ff = faces[0]
    assert ff.family == "MyFont"
    assert "woff2" in ff.src or "myfont" in ff.url
    assert ff.weight == "400"
    assert ff.style == "normal"


def test_p3_font_face_multiple():
    """P33.P3 — two @font-face blocks parse to two FontFace objects."""
    css_text = """
    @font-face {
        font-family: 'A';
        src: local('Arial');
    }
    @font-face {
        font-family: 'B';
        src: url('b.ttf') format('truetype');
        font-weight: 700;
    }
    """
    faces = css.parse_font_faces(css_text)
    assert len(faces) == 2
    assert faces[0].family == "A"
    assert faces[0].is_local
    assert faces[1].family == "B"
    assert faces[1].weight == "700"
    assert not faces[1].is_local
    assert faces[1].url == "b.ttf"


def test_p3_font_face_empty_css():
    """P33.P3 — CSS without @font-face returns an empty list."""
    assert css.parse_font_faces("div { color: red; }") == []


def test_p3_font_face_unicode_range():
    """P33.P3 — the unicode-range declaration is captured."""
    css_text = """
    @font-face {
        font-family: 'CJK';
        src: local('SimHei');
        unicode-range: U+4E00-9FFF;
    }
    """
    faces = css.parse_font_faces(css_text)
    assert len(faces) == 1
    assert faces[0].unicode_range == "U+4E00-9FFF"


# ------------------------------------------------------------------
# SVG placeholder rendering
# ------------------------------------------------------------------

def _find_tag_box(root, tag_name):
    """Find the first LayoutBox whose DOM node has the given tag name."""
    def _walk(bx):
        if bx.dom is not None and bx.dom.is_element() and bx.dom.tag == tag_name:
            return bx
        for c in bx.children:
            r = _walk(c)
            if r is not None:
                return r
        return None
    return _walk(root)


def test_p3_svg_default_size():
    """P33.P3 — an <svg> element with no width/height attrs defaults to
    300×150 (same as the browser's intrinsic size)."""
    html = '<html><body><svg id="s"></svg></body></html>'
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    styled = css.apply(tree, author_css, ua_css=ua_css)
    root = layout.layout(styled, 400.0, 300.0)
    sv = _find_tag_box(root, "svg")
    assert sv is not None, "svg box must exist"
    assert sv.rect.w == 300.0, f"default svg width should be 300, got {sv.rect.w}"
    assert sv.rect.h == 150.0, f"default svg height should be 150, got {sv.rect.h}"


def test_p3_svg_size_from_attrs():
    """P33.P3 — <svg width="200" height="100"> produces a 200×100 box."""
    html = '<html><body><svg width="200" height="100"></svg></body></html>'
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    styled = css.apply(tree, author_css, ua_css=ua_css)
    root = layout.layout(styled, 400.0, 300.0)
    sv = _find_tag_box(root, "svg")
    assert sv is not None
    assert sv.rect.w == 200.0, f"got {sv.rect.w}"
    assert sv.rect.h == 100.0, f"got {sv.rect.h}"


def test_p3_svg_paint_placeholder():
    """P33.P3 — an <svg> element renders a grey placeholder rectangle
    in the bitmap (no actual SVG content is parsed)."""
    html = '<html><body><svg width="100" height="80"></svg></body></html>'
    bmp = browser.render_document(html, "", 400, 300)
    # The SVG placeholder is a light-grey (230,230,230) fill.
    grey = 0xE6E6E6
    found = any(p == grey for p in bmp.pixels)
    assert found, "svg placeholder grey (#e6e6e6) must appear in the bitmap"


def test_p3_video_default_size():
    """P33.P3 — a <video> element with no attrs defaults to 300×150."""
    html = '<html><body><video id="v"></video></body></html>'
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    styled = css.apply(tree, author_css, ua_css=ua_css)
    root = layout.layout(styled, 400.0, 300.0)
    v = _find_tag_box(root, "video")
    assert v is not None
    assert v.rect.w == 300.0, f"default video width should be 300, got {v.rect.w}"
    assert v.rect.h == 150.0, f"default video height should be 150, got {v.rect.h}"


def test_p3_audio_default_size():
    """P33.P3 — an <audio> element with no attrs defaults to 300×150
    (the toy treats audio the same as video for sizing)."""
    html = '<html><body><audio id="a"></audio></body></html>'
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    styled = css.apply(tree, author_css, ua_css=ua_css)
    root = layout.layout(styled, 400.0, 300.0)
    a = _find_tag_box(root, "audio")
    assert a is not None
    assert a.rect.w == 300.0, f"default audio width should be 300, got {a.rect.w}"


def test_p3_svg_css_size_override():
    """P33.P3 — CSS width/height on <svg> overrides the attribute
    values (the box is sized from CSS)."""
    html = ('<html><body>'
            '<style>svg { width: 80px; height: 60px; }</style>'
            '<svg width="300" height="150"></svg>'
            '</body></html>')
    tree = dom.parse(html)
    author_css, ua_css = browser._full_css(tree, "")
    styled = css.apply(tree, author_css, ua_css=ua_css)
    root = layout.layout(styled, 400.0, 300.0)
    sv = _find_tag_box(root, "svg")
    assert sv is not None
    assert sv.rect.w == 80.0, f"CSS width should override attr, got {sv.rect.w}"
    assert sv.rect.h == 60.0, f"CSS height should override attr, got {sv.rect.h}"


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_p3_font_face_parse_basic", test_p3_font_face_parse_basic))
TESTS.append(("test_p3_font_face_multiple", test_p3_font_face_multiple))
TESTS.append(("test_p3_font_face_empty_css", test_p3_font_face_empty_css))
TESTS.append(("test_p3_font_face_unicode_range", test_p3_font_face_unicode_range))
TESTS.append(("test_p3_svg_default_size", test_p3_svg_default_size))
TESTS.append(("test_p3_svg_size_from_attrs", test_p3_svg_size_from_attrs))
TESTS.append(("test_p3_svg_paint_placeholder", test_p3_svg_paint_placeholder))
TESTS.append(("test_p3_video_default_size", test_p3_video_default_size))
TESTS.append(("test_p3_audio_default_size", test_p3_audio_default_size))
TESTS.append(("test_p3_svg_css_size_override", test_p3_svg_css_size_override))
