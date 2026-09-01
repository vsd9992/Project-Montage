"""Print-production preflight + PDF export (idea §19).

Preflight checks run against the rendered spread JPEGs (render_stage.py output) before
assembly, so problems are caught before a print-ready PDF is produced:

- canvas size matches the expected print dimensions exactly (catches a stale/mismatched
  render if layout_geometry.py's canvas size ever changes without re-rendering).
- effective DPI (pixels / inches) is at least MIN_DPI -- a hard-coded minimum since this
  project has one fixed canvas size for now (see layout_geometry.py's provisional note);
  each rendered spread is already produced at the canvas's native resolution, so this
  mainly guards against a corrupted/resized file rather than a real per-photo upsampling
  risk (crop_stage.py never upsamples above image_size).

PDF assembly is a thin wrapper over Pillow's multi-page JPEG-to-PDF save -- no bleed marks
or color management here, since the project doesn't have a real print-size/finishing
feature yet (flagged in layout_geometry.py as provisional).
"""

import argparse
import json
import os

from PIL import Image

from layout_geometry import DEFAULT_SIZE, get_geometry

MIN_DPI = 250  # below this, print quality is visibly soft; 300 is the design target


def preflight_check(image_path: str, geometry) -> dict:
    issues = []
    with Image.open(image_path) as img:
        w, h = img.size

    if (w, h) != (geometry.canvas_w, geometry.canvas_h):
        issues.append(
            f"size mismatch: {w}x{h}px, expected {geometry.canvas_w}x{geometry.canvas_h}px "
            f"(stale render vs. current --size selection / layout_geometry canvas size)"
        )

    dpi_w = w / geometry.width_in
    dpi_h = h / geometry.height_in
    if dpi_w < MIN_DPI or dpi_h < MIN_DPI:
        issues.append(f"low effective DPI: {dpi_w:.0f}x{dpi_h:.0f} (minimum {MIN_DPI})")

    return {"path": image_path, "size": [w, h], "ok": not issues, "issues": issues}


def run_preflight(rendered_dir: str, spreads_path: str, size: str = DEFAULT_SIZE) -> list[dict]:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = {s["spread"]: s for s in json.load(f)}
    geometry = get_geometry(size)

    results = []
    for n in sorted(spreads):
        spread = spreads[n]
        image_path = os.path.join(rendered_dir, f"spread_{n:03d}_{spread['layout']}.jpg")
        if not os.path.exists(image_path):
            results.append({"path": image_path, "ok": False, "issues": ["rendered file missing"]})
            continue
        results.append(preflight_check(image_path, geometry))
    return results


def export_pdf(rendered_dir: str, spreads_path: str, out_pdf: str, skip_failed: bool = False,
                size: str = DEFAULT_SIZE) -> None:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = {s["spread"]: s for s in json.load(f)}

    preflight = {r["path"]: r for r in run_preflight(rendered_dir, spreads_path, size)}
    failed = [r for r in preflight.values() if not r["ok"]]
    if failed and not skip_failed:
        print(f"Preflight failed for {len(failed)} spread(s):")
        for r in failed:
            print(f"  {r['path']}: {'; '.join(r['issues'])}")
        raise SystemExit("Preflight check failed. Fix the issues above, or pass --skip-failed to export anyway.")

    pages = []
    for n in sorted(spreads):
        spread = spreads[n]
        image_path = os.path.join(rendered_dir, f"spread_{n:03d}_{spread['layout']}.jpg")
        result = preflight.get(image_path)
        if result and not result["ok"]:
            print(f"Skipping spread {n} due to preflight issues: {'; '.join(result['issues'])}")
            continue
        pages.append(Image.open(image_path).convert("RGB"))

    if not pages:
        raise SystemExit("No spreads passed preflight; nothing to export.")

    os.makedirs(os.path.dirname(out_pdf) or ".", exist_ok=True)
    pages[0].save(out_pdf, "PDF", save_all=True, append_images=pages[1:], resolution=300.0)
    for p in pages:
        p.close()
    print(f"Exported {len(pages)} spreads to {out_pdf}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preflight-check and export rendered spreads to a PDF")
    parser.add_argument("--rendered-dir", default="exports/rendered_spreads")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--out", default="exports/album.pdf")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks and print a report, no PDF")
    parser.add_argument("--skip-failed", action="store_true", help="Export anyway, skipping spreads that fail preflight")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="Print size (see layout_geometry.PRINT_SIZES)")
    args = parser.parse_args()

    if args.preflight_only:
        results = run_preflight(args.rendered_dir, args.spreads, args.size)
        n_ok = sum(1 for r in results if r["ok"])
        print(f"Preflight: {n_ok}/{len(results)} spreads passed")
        for r in results:
            if not r["ok"]:
                print(f"  FAIL {r['path']}: {'; '.join(r['issues'])}")
    else:
        export_pdf(args.rendered_dir, args.spreads, args.out, args.skip_failed, args.size)
