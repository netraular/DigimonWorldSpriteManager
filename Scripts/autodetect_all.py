#!/usr/bin/env python3
"""CLI: run auto-detection over every raw sheet and warm the box cache.

    python3 Scripts/autodetect_all.py            # cache all missing
    python3 Scripts/autodetect_all.py --force    # recompute everything
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from core import segmenter as S  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Warm the auto-detect box cache")
    ap.add_argument("--force", action="store_true", help="recompute even if cached")
    args = ap.parse_args()
    config.ensure_dirs()

    sheets = [f for f in sorted(os.listdir(config.RAW_DIR)) if f.endswith(".png")]
    ok = 0
    for name in sheets:
        sid = name[:-4]
        cache = os.path.join(config.BOX_DIR, f"{sid}.boxes.json")
        if os.path.exists(cache) and not args.force:
            continue
        arr = S.load_rgba(os.path.join(config.RAW_DIR, name))
        boxes, meta = S.detect_boxes(arr)
        payload = {
            "id": sid, "image": meta["image"],
            "background": meta["background"], "grid": meta["grid"],
            "boxes": [b.as_dict() for b in boxes], "params": meta["params"],
        }
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        ok += 1
        print(f"{sid}: {len(boxes)} boxes")
    print(f"done: cached {ok} sheets ({len(sheets)} total)")


if __name__ == "__main__":
    main()
