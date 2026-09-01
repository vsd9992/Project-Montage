"""Manual storyboard reshuffling (Phase 1 review gap #2, idea §11 "the user can drag to
reorder"). No drag-and-drop UI exists yet -- that belongs to Phase 3's desktop spread
editor (project-plan.md) -- but the underlying reorder operation the user needs today
(change which order spreads appear in the album) is provided here as a CLI: give it an
explicit new ordering of existing spread numbers, and it renumbers `spreads.json` and
keeps `crops.json` in sync (both files key their entries by spread number), so a re-render
using the existing crops/faces produces the reordered album without recomputing anything.

Only full-spread reordering is supported (move a whole hero/duo/etc. spread earlier or
later); reordering photos *within* a spread's slots is not covered here -- that's a
smaller, layout-grammar-level edit better done by hand-editing spreads.json's slot
assignments directly, since it can also change which layout template fits.
"""

import argparse
import json


def renumber(spreads: list[dict], new_order: list[int]) -> list[dict]:
    """`new_order` is a permutation of the existing `spread` numbers, in the desired new
    sequence. Returns a new list with `spread` fields reassigned 1..N in that sequence."""
    by_number = {s["spread"]: s for s in spreads}
    if set(new_order) != set(by_number):
        missing = set(by_number) - set(new_order)
        extra = set(new_order) - set(by_number)
        raise ValueError(
            f"new_order must be a permutation of all existing spread numbers. "
            f"missing={sorted(missing)} unexpected={sorted(extra)}"
        )
    result = []
    for new_number, old_number in enumerate(new_order, start=1):
        spread = dict(by_number[old_number])
        spread["spread"] = new_number
        result.append(spread)
    return result


def apply_reorder(spreads_path: str, crops_path: str, new_order: list[int],
                   spreads_out: str, crops_out: str) -> None:
    with open(spreads_path, encoding="utf-8") as f:
        spreads = json.load(f)
    reordered_spreads = renumber(spreads, new_order)

    with open(spreads_out, "w", encoding="utf-8") as f:
        json.dump(reordered_spreads, f, indent=2)
    print(f"Wrote {len(reordered_spreads)} reordered spreads to {spreads_out}")

    if crops_path:
        with open(crops_path, encoding="utf-8") as f:
            crops = json.load(f)
        reordered_crops = renumber(crops, new_order)
        with open(crops_out, "w", encoding="utf-8") as f:
            json.dump(reordered_crops, f, indent=2)
        print(f"Wrote {len(reordered_crops)} reordered crop entries to {crops_out}")


def move_spread(spread_numbers: list[int], spread: int, to_position: int) -> list[int]:
    """Convenience: move one spread number to a 1-indexed position, shifting the rest."""
    order = [n for n in spread_numbers if n != spread]
    order.insert(to_position - 1, spread)
    return order


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reorder spreads (manual storyboard reshuffling)")
    parser.add_argument("--spreads", default="exports/spreads.json")
    parser.add_argument("--crops", default="exports/crops.json")
    parser.add_argument("--spreads-out", default=None, help="Default: overwrite --spreads")
    parser.add_argument("--crops-out", default=None, help="Default: overwrite --crops")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--order", help="Comma-separated full permutation of spread numbers, e.g. 1,3,2,4")
    group.add_argument("--move", nargs=2, type=int, metavar=("SPREAD", "TO_POSITION"),
                        help="Move one spread number to a 1-indexed position")
    args = parser.parse_args()

    with open(args.spreads, encoding="utf-8") as f:
        current_numbers = [s["spread"] for s in json.load(f)]

    if args.order:
        new_order = [int(x) for x in args.order.split(",")]
    else:
        spread, to_position = args.move
        new_order = move_spread(current_numbers, spread, to_position)

    apply_reorder(
        args.spreads, args.crops, new_order,
        args.spreads_out or args.spreads, args.crops_out or args.crops,
    )
