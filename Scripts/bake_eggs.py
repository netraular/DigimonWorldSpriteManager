#!/usr/bin/env python3
"""CLI: bake the four attribute egg strips from the "Eggs" rip.

    python3 Scripts/bake_eggs.py              # bake + stage into the content-editor
    python3 Scripts/bake_eggs.py --no-stage   # bake into output/digimon/eggs/ only
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from core import eggs  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Bake the incubator egg strips")
    ap.add_argument("--no-stage", action="store_true",
                    help="bake into output/ only; do not copy into the editor")
    args = ap.parse_args()
    config.ensure_dirs()
    eggs.bake_eggs(stage=not args.no_stage)


if __name__ == "__main__":
    main()
