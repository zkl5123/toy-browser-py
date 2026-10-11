"""Stage 4 — display list + CPU rasterization into a bitmap (pure stdlib)."""
from __future__ import annotations


class Color:
    __slots__ = ("r", "g", "b", "a")

    def __init__(self, r: int, g: int, b: int, a: int = 255):
        self.r = r & 0xFF
        self.g = g & 0xFF
        self.b = b & 0xFF
        self.a = a & 0xFF

    @property
    def is_transparent(self) -> bool:
        return self.a == 0

    def to_u32(self) -> int:
        """Store as 0x00RRGGBB with alpha dropped (PPM has no alpha)."""
        return (self.r << 16) | (self.g << 8) | self.b

    def blend_over(self, dst: int) -> int:
        """Alpha-composite this colour (as 0xRRGGBB over dst 0xRRGGBB)."""
        if self.a == 0:
            return dst
        sa = self.a / 255.0
        dr = (dst >> 16) & 0xFF
        dg = (dst >> 8) & 0xFF
        db = dst & 0xFF
        sr = self.r
        sg = self.g
        sb = self.b
        mr = min(255, int(sr * sa + dr * (1.0 - sa)))
        mg = min(255, int(sg * sa + dg * (1.0 - sa)))
        mb = min(255, int(sb * sa + db * (1.0 - sa)))
        return (mr << 16) | (mg << 8) | mb

    def __repr__(self) -> str:  # pragma: no cover
        return f"Color({self.r},{self.g},{self.b},{self.a})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, Color)
            and (self.r, self.g, self.b, self.a) == (other.r, other.g, other.b, other.a)
        )


# Storage order: pixels are 0x00RRGGBB u32, row-major.
WHITE = 0xFFFFFF


class Bitmap:
    """An RGB pixel buffer (width x height, row-major)."""

    def __init__(self, width: int, height: int, fill: int = WHITE):
        self.width = width
        self.height = height
        self.pixels = [fill] * (width * height)

    def set_pixel(self, x: int, y: int, color: Color) -> None:
        x = int(x)
        y = int(y)
        if x < 0 or y < 0:
            return
        if x >= self.width or y >= self.height:
            return
        idx = y * self.width + x
        self.pixels[idx] = color.blend_over(self.pixels[idx])

    def pixel_at(self, x: int, y: int) -> tuple:
        """P33.R5 — return (r, g, b) for a single pixel (clamped to bounds)."""
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        v = self.pixels[y * self.width + x]
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)

    def fill_rect(self, x: float, y: float, w: float, h: float, color: Color) -> None:
        if color.is_transparent:
            return
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.width, int(x + w))
        y1 = min(self.height, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return
        rgb = color.to_u32()
        for py in range(y0, y1):
            base = py * self.width
            row = self.pixels
            for px in range(x0, x1):
                if color.a == 255:
                    row[base + px] = rgb
                else:
                    row[base + px] = color.blend_over(row[base + px])

    def save_ppm(self, path: str) -> None:
        with open(path, "wb") as f:
            header = f"P6\n{self.width} {self.height}\n255\n".encode()
            f.write(header)
            for px in self.pixels:
                f.write(bytes(((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF)))


def describe(bmp: Bitmap) -> str:
    nonwhite = sum(1 for p in bmp.pixels if p != WHITE)
    pct = 100.0 * nonwhite / max(1, len(bmp.pixels))
    return f"bitmap {bmp.width}x{bmp.height}  nonwhite={nonwhite} ({pct:.1f}%)"
