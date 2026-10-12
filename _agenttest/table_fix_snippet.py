"""table shrink-to-fit: measure each cell's text at natural width, compute col_max."""
import layout
import text as text_mod

def _table_content_width(bx, row, containing_w):
    """P0 (agent report) — natural cell content width: measure the cell's
    text at its font size (no wrap) and add padding + border.  Used for
    auto-width tables (no explicit width) to shrink-to-fit like a real
    browser.  Returns a list of per-column content widths for *row*."""
    cols = []
    for c in row.children:
        if not (c.is_table_cell or c.box_type in (layout.BoxType.BLOCK, layout.BoxType.TEXT, layout.BoxType.INLINE)):
            continue
        # measure the cell's own text directly (cell.text_content holds the
        # direct text; descendant element text is rare in a toy table).
        t = (c.text_content or "")
        if c.children:
            parts = []
            for gc in c.children:
                if gc.box_type == layout.BoxType.TEXT and gc.text_content:
                    parts.append(gc.text_content)
            t = " ".join(parts) if parts else t
        natural = text_mod.measure(t, c.font_px) if t else 0.0
        col_w = natural + c.padding.left + c.padding.right + c.border.left + c.border.right
        cols.append(col_w)
    return cols
