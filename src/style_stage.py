"""Design styles (idea §13): "style and layout should be separate." Layout grammar
(spread_stage.py/layout_geometry.py) decides slot geometry; a style here only decides how
each spread *looks* -- background, photo border/mat, and caption typography -- without
touching slot rectangles, so an already-computed crop (crop_stage.py) never needs
recomputing when the style changes.

Only a subset of idea §13's named styles are implemented for now (the ones expressible as
background/border/typography choices with the current single-canvas-size renderer);
culturally-specific decorative elements (Traditional Indian) and true per-style layout
density changes (Documentary "more images per spread") are deferred -- they need either
asset/ornament support or per-style layout grammar, neither of which exists yet.
"""

import os

_WINDOWS_FONT_DIR = r"C:\Windows\Fonts"


def _font_path(*candidates: str) -> str | None:
    for name in candidates:
        path = os.path.join(_WINDOWS_FONT_DIR, name)
        if os.path.exists(path):
            return path
    return None


STYLES = {
    "modern_minimal": {
        "background_color": (255, 255, 255),
        "mat_color": None,
        "mat_width": 0,
        "caption_color": (60, 60, 60),
        "caption_font": _font_path("segoeui.ttf", "arial.ttf"),
        "caption_size": 42,
        "caption_align": "left",
    },
    "luxury_wedding": {
        "background_color": (247, 244, 239),
        "mat_color": (255, 255, 255),
        "mat_width": 18,
        "caption_color": (110, 90, 60),
        "caption_font": _font_path("georgia.ttf", "times.ttf"),
        "caption_size": 54,
        "caption_align": "center",
    },
    "editorial": {
        "background_color": (255, 255, 255),
        "mat_color": None,
        "mat_width": 0,
        "caption_color": (20, 20, 20),
        "caption_font": _font_path("arialbd.ttf", "arial.ttf"),
        "caption_size": 60,
        "caption_align": "left",
    },
    "documentary": {
        "background_color": (18, 18, 18),
        "mat_color": (18, 18, 18),
        "mat_width": 6,
        "caption_color": (230, 230, 230),
        "caption_font": _font_path("consola.ttf", "cour.ttf", "arial.ttf"),
        "caption_size": 36,
        "caption_align": "left",
    },
}

DEFAULT_STYLE = "modern_minimal"


def get_style(name: str) -> dict:
    if name not in STYLES:
        raise ValueError(f"Unknown style '{name}'. Available: {sorted(STYLES)}")
    return STYLES[name]
