#!/usr/bin/env python3
"""CLI: bake every extraction spec into output/ (and optionally stage).

    python3 Scripts/bake_all.py               # bake all specs → output/digimon/
    python3 Scripts/bake_all.py --stage       # also copy into the content-editor
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from core import baker  # noqa: E402
from core import scaffold  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Bake all extraction specs")
    ap.add_argument("--stage", action="store_true",
                    help="also copy baked assets into the content-editor tree")
    args = ap.parse_args()
    config.ensure_dirs()

    ok, fail = baker.bake_all()
    if args.stage and ok:
        scaffold.ensure_species("digimon")
        for name in sorted(os.listdir(config.OUT_DIR)):
            if name.endswith(".png"):
                sid = name[:-4]
                scaffold.stage_to_content(sid, write_creature_node=True)
        print(f"staged {ok} creatures into {config.CONTENT_ROOT}")


if __name__ == "__main__":
    main()
