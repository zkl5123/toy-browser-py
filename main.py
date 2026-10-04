"""toy-browser-py — a from-scratch browser engine in pure-stdlib Python.

CLI:
  python main.py <input.html> [out.ppm] [--width W] [--height H]
                   [--event id] [--click x,y] [--css file.css]
                   [--a11y a11y.json] [--links links.txt] [--png]
                   [--animate NAME] [--anim-frames N] [--anim-out DIR]

Renders an HTML document through the full "URL to pixels" pipeline and
writes a PPM (or PNG) image.  Optionally emits an accessibility tree
JSON and/or a link list.
"""
from __future__ import annotations

import argparse
import os
import sys

import browser
import dom as dom_mod


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="toy-browser (Python, zero-dep)")
    p.add_argument("input", help="path to an HTML file or an http(s) URL")
    p.add_argument("output", nargs="?", default="out.ppm", help="output PPM/PNG path")
    p.add_argument("--width", type=int, default=800)
    p.add_argument("--height", type=int, default=600)
    p.add_argument("--event", help="simulate a click on the element with this id")
    p.add_argument("--click", help="coordinate click 'x,y' (layout hit test)")
    p.add_argument("--css", help="additional CSS file to append")
    p.add_argument("--list-timers", action="store_true",
                   help="also drain the toy setTimeout FIFO queue")
    # P2 flags
    p.add_argument("--a11y", metavar="FILE",
                   help="write accessibility tree JSON to FILE")
    p.add_argument("--links", metavar="FILE",
                   help="write all <a href> URLs to FILE (one per line)")
    p.add_argument("--png", action="store_true",
                   help="output PNG instead of PPM (pure-stdlib encoder)")
    # P4.1 — @keyframes animation
    p.add_argument("--animate", metavar="NAME",
                   help="render a named @keyframes animation (element id via --event)")
    p.add_argument("--anim-frames", type=int, default=10,
                   help="number of frames for --animate (default 10)")
    p.add_argument("--anim-out", metavar="DIR",
                   help="directory to write animation frames to (P4.1)")
    return p.parse_args(argv)


def _render_error_and_exit(html: str, args) -> None:
    """P21.3 — render the unified error-page HTML and write the image,
    so a network failure still produces a viewable PPM/PNG."""
    import paint, paint_engine, layout, css, dom
    bmp = paint.Bitmap(args.width, args.height)
    dl = paint_engine.emit_commands(
        layout.build_box(css.apply(dom.parse(html), ""), 0.0),
    )
    out = paint_engine.rasterize(dl, args.width, args.height)
    if args.png:
        browser.write_png(out, args.output)
    else:
        browser.write_ppm(out, args.output)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    # Read the input document.
    if os.path.exists(args.input):
        with open(args.input, encoding="utf-8", errors="replace") as f:
            html = f.read()
        css_text = ""
    elif args.input.lower().startswith(("http://", "https://")):
        # P21 — URL input through the new network stack.
        import net
        try:
            html, _resp = net.fetch_html(args.input, timeout=10.0)
            css_text = ""
        except net.FetchError as e:
            print(f"network error: {e}", file=sys.stderr)
            # Unified error page (P21.3) so the user still gets an image.
            html = net.error_page_html(
                f"Network error: {e}",
                f"Loading {args.input} failed.",
            )
            css_text = ""
            _render_error_and_exit(html, args)
            return 3
        except Exception as e:
            print(f"connection error: {e}", file=sys.stderr)
            html = net.error_page_html("Connection error", str(e))
            css_text = ""
            _render_error_and_exit(html, args)
            return 3
    else:
        # P21 — unknown input → try as URL through the network stack.
        import net
        try:
            html, _resp = net.fetch_html(args.input, timeout=10.0)
            css_text = ""
        except Exception as e:
            print(f"cannot load {args.input!r}: {e}", file=sys.stderr)
            return 2
    css_text = ""
    if args.css and os.path.exists(args.css):
        with open(args.css, encoding="utf-8", errors="replace") as f:
            css_text = f.read()

    # P2.2 — collect links before rendering (DOM is mutated by scripts).
    tree = dom_mod.parse(html)
    links = browser.collect_links(tree)
    if args.links:
        browser.write_links(links, args.links)
        print(f"wrote {args.links}  ({len(links)} links)")

    # P2.1 — accessibility tree.
    if args.a11y:
        a11y = browser.collect_accessibility_tree(tree)
        browser.write_a11y_json(a11y, args.a11y)
        print(f"wrote {args.a11y}  ({len(a11y)} a11y nodes)")

    # Render.
    if args.event:
        bmp = browser.render_document_with_event(
            html, css_text, args.width, args.height, args.event
        )
    elif args.click:
        try:
            xs, ys = args.click.split(",", 1)
            x, y = float(xs), float(ys)
        except ValueError:
            print(f"--click expects 'x,y', got {args.click!r}", file=sys.stderr)
            return 2
        bmp = browser.render_document_with_click(
            html, css_text, args.width, args.height, x, y
        )
    else:
        bmp = browser.render_document(html, css_text, args.width, args.height)

    # P2.3 — output PNG if requested.
    if args.png:
        browser.write_png(bmp, args.output)
    else:
        browser.write_ppm(bmp, args.output)
    nonwhite = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    ext = ".png" if args.png else ".ppm"
    print(f"wrote {args.output}  ({bmp.width}x{bmp.height}, {nonwhite} non-white px)")

    # P4.1 — @keyframes animation: render N+1 frames to a directory.
    if args.animate:
        if not args.event:
            print("--animate requires --event <element-id>", file=sys.stderr)
            return 2
        out_dir = args.anim_out or "anim_frames"
        os.makedirs(out_dir, exist_ok=True)
        frames = browser.animate_keyframes(
            html, css_text, args.width, args.height,
            args.event, args.animate, total_frames=args.anim_frames,
        )
        for i, fr in enumerate(frames):
            ext = ".png" if args.png else ".ppm"
            fpath = os.path.join(out_dir, f"frame_{i:03d}{ext}")
            if args.png:
                browser.write_png(fr, fpath)
            else:
                browser.write_ppm(fr, fpath)
        print(f"wrote {len(frames)} frames to {out_dir}/")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
