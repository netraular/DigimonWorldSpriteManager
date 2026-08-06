#!/usr/bin/env python3
"""CLI: name the roster, give every baked sheet a creature node, and stage it.

    python3 Scripts/sync_roster.py                 # name + node + stage
    python3 Scripts/sync_roster.py --no-stage      # only fix up output/digimon/
    python3 Scripts/sync_roster.py --no-names      # skip the gallery naming pass
    python3 Scripts/sync_roster.py --rename        # re-apply titles over saved names

Unlike `bake_all.py` this never re-bakes: it works off whatever is already in
`output/digimon/`, so it is the cheap way to push the whole baked roster into the
content-editor's `species/digimon` folder.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from core import scaffold  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Sync the baked roster into the editor")
    ap.add_argument("--no-stage", action="store_true",
                    help="write creature nodes only; do not copy into the editor")
    ap.add_argument("--no-names", action="store_true",
                    help="do not fill creature names from the downloaded gallery")
    ap.add_argument("--rename", action="store_true",
                    help="overwrite names that are already saved in the specs")
    args = ap.parse_args()
    config.ensure_dirs()

    if not args.no_names:
        scaffold.apply_gallery_names(force=args.rename)
    scaffold.sync_roster(stage=not args.no_stage)


if __name__ == "__main__":
    main()
