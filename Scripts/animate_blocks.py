#!/usr/bin/env python3
"""CLI: assemble + bake every "to animate" sheet with a known block layout.

The DWDS rips animate in blocks of three, so the sprite count says which facings
a sheet carries (see ``core/autoassemble.py``):

    15 sprites → SW · SE · NW · NE · idle
    12 sprites → SW · SE · NW · NE
     9 sprites → SW · NW · idle, with SE/NE baked as the mirror of SW/NW

Each sheet gets the spec the editor would have written, then the same save +
bake, so it leaves the gallery's "To animate" chip on its own. Sheets already
baked are skipped, so re-running is safe.

    python3 Scripts/animate_blocks.py --dry-run       # list what would be assembled
    python3 Scripts/animate_blocks.py                 # assemble + bake them
    python3 Scripts/animate_blocks.py --counts 9      # only the 9-sprite sheets
    python3 Scripts/animate_blocks.py --stage         # …and copy into the content-editor

The gallery's "Auto-assemble" button runs exactly this over 15/12/9.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

from app import _validate_spec  # noqa: E402
from core import autoassemble  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list, write nothing")
    ap.add_argument("--limit", type=int, help="only the first N sheets")
    ap.add_argument("--counts", type=int, nargs="+", choices=sorted(autoassemble.LAYOUTS),
                    help="only sheets with these sprite counts (default: all known)")
    ap.add_argument("--stage", action="store_true",
                    help="also copy each baked asset into the content-editor tree")
    args = ap.parse_args()

    counts = args.counts or sorted(autoassemble.LAYOUTS)
    for n in counts:
        print(f"  {n:>2} sprites → {autoassemble.describe(n)}")
    print(f"{len(autoassemble.candidates(counts))} sheets to animate")

    res = autoassemble.run(counts=counts, limit=args.limit, stage=args.stage,
                           dry_run=args.dry_run, validate=_validate_spec)
    if args.dry_run:
        for it in res["items"]:
            print(f"  {it['sheet_id']} → {it['id']:03d}  {it['count']} sprites: {it['plan']}")
    verb = "would assemble" if args.dry_run else "baked"
    print(f"{verb} {res['ok']}, failed {res['fail']}")


if __name__ == "__main__":
    main()
