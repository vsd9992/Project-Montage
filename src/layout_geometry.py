"""Shared spread geometry: canvas size + per-layout slot rectangles, in pixels.

Single source of truth for both crop_stage.py (needs each slot's target aspect ratio)
and render_stage.py (needs each slot's actual placement rectangle) so the two stay
consistent -- a crop computed for the wrong aspect ratio would misalign or force a
squash/stretch at render time.

Multi-print-size support (2026-09-01): geometry is now built for a chosen print size
rather than hardcoded to one canvas. `PRINT_SIZES` covers the MVP's 12x18-12x36 range
(idea §19) plus "custom". `SIZE` module-level constants below default to the original
12x36in @ 300 DPI choice so existing callers that only need the default size keep working
unchanged.
"""

DPI = 300
MARGIN_IN = 0.5
GUTTER_IN = 0.2

PRINT_SIZES = {
    "12x18": (18, 12),
    "12x24": (24, 12),
    "12x30": (30, 12),
    "12x36": (36, 12),
}
DEFAULT_SIZE = "12x36"


def _hero_full(canvas_w, canvas_h, margin):
    return {"hero": (margin, margin, canvas_w - 2 * margin, canvas_h - 2 * margin)}


def _duo(canvas_w, canvas_h, margin, gutter):
    avail_w = canvas_w - 2 * margin - gutter
    half_w = avail_w / 2
    h = canvas_h - 2 * margin
    return {
        "hero": (margin, margin, half_w, h),
        "support_1": (margin + half_w + gutter, margin, half_w, h),
    }


def _hero_plus_two(canvas_w, canvas_h, margin, gutter):
    avail_w = canvas_w - 2 * margin - gutter
    hero_w = avail_w * 0.65
    support_w = avail_w - hero_w
    full_h = canvas_h - 2 * margin
    avail_h = full_h - gutter
    support_h = avail_h / 2
    support_x = margin + hero_w + gutter
    return {
        "hero": (margin, margin, hero_w, full_h),
        "support_1": (support_x, margin, support_w, support_h),
        "support_2": (support_x, margin + support_h + gutter, support_w, support_h),
    }


def _hero_plus_three(canvas_w, canvas_h, margin, gutter):
    avail_w = canvas_w - 2 * margin - gutter
    hero_w = avail_w * 0.6
    support_w = avail_w - hero_w
    full_h = canvas_h - 2 * margin
    avail_h = full_h - 2 * gutter
    support_h = avail_h / 3
    support_x = margin + hero_w + gutter
    return {
        "hero": (margin, margin, hero_w, full_h),
        "support_1": (support_x, margin, support_w, support_h),
        "support_2": (support_x, margin + support_h + gutter, support_w, support_h),
        "support_3": (support_x, margin + 2 * (support_h + gutter), support_w, support_h),
    }


def _documentary_grid(canvas_w, canvas_h, margin, gutter):
    top_w = (canvas_w - 2 * margin - 2 * gutter) / 3
    bottom_w = (canvas_w - 2 * margin - gutter) / 2
    row_h = (canvas_h - 2 * margin - gutter) / 2
    bottom_y = margin + row_h + gutter
    return {
        "support_1": (margin, margin, top_w, row_h),
        "support_2": (margin + top_w + gutter, margin, top_w, row_h),
        "support_3": (margin + 2 * (top_w + gutter), margin, top_w, row_h),
        "support_4": (margin, bottom_y, bottom_w, row_h),
        "support_5": (margin + bottom_w + gutter, bottom_y, bottom_w, row_h),
    }


class Geometry:
    """Canvas + per-layout slot rectangles for one chosen print size."""

    def __init__(self, width_in: float, height_in: float, dpi: int = DPI,
                 margin_in: float = MARGIN_IN, gutter_in: float = GUTTER_IN):
        self.width_in = width_in
        self.height_in = height_in
        self.dpi = dpi
        self.canvas_w = round(width_in * dpi)
        self.canvas_h = round(height_in * dpi)
        self.margin = round(margin_in * dpi)
        self.gutter = round(gutter_in * dpi)

        w, h, m, g = self.canvas_w, self.canvas_h, self.margin, self.gutter
        self.layout_rects = {
            "hero": _hero_full(w, h, m),
            "duo": _duo(w, h, m, g),
            "hero_plus_two": _hero_plus_two(w, h, m, g),
            "hero_plus_three": _hero_plus_three(w, h, m, g),
            "documentary_grid": _documentary_grid(w, h, m, g),
        }

    def slot_aspect_ratios(self, layout: str) -> dict[str, float]:
        return {slot: rw / rh for slot, (rx, ry, rw, rh) in self.layout_rects[layout].items()}


def get_geometry(size: str = DEFAULT_SIZE) -> Geometry:
    if size not in PRINT_SIZES:
        raise ValueError(f"Unknown print size '{size}'. Available: {sorted(PRINT_SIZES)}")
    width_in, height_in = PRINT_SIZES[size]
    return Geometry(width_in, height_in)


# Backward-compatible module-level defaults (12x36in @ 300 DPI, the original single size).
_default = get_geometry(DEFAULT_SIZE)
CANVAS_W = _default.canvas_w
CANVAS_H = _default.canvas_h
MARGIN = _default.margin
GUTTER = _default.gutter
LAYOUT_RECTS = _default.layout_rects


def slot_aspect_ratios(layout: str) -> dict[str, float]:
    return _default.slot_aspect_ratios(layout)
