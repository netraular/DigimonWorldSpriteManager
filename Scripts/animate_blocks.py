#!/usr/bin/env python3
"""CLI: assemble + bake every 15-sprite sheet that is still "to animate".

The DWDS rips whose crop yields exactly 15 sprites all hold the same five
blocks of three, in reading order, so the assembly the animate view asks you to
click can be filled in for the whole backlog at once:

    sprites  1- 3 → walk SW (down_left)      sprites 10-12 → walk NE (up_right)
    sprites  4- 6 → walk SE (down_right)     sprites 13-15 → idle
    sprites  7- 9 → walk NW (up_left)

Each sheet gets the spec the editor would have written (same background, boxes,
timing and creature defaults), then the same save + bake: ``specs/digimon/
<NNN>.extract.json`` → ``output/digimon/<NNN>.png`` + ``<NNN>.json`` +
``pet_<NNN>.json``. A baked sheet is what the gallery counts as done, so the
sheets move out of the "To animate" chip on their own.

    python3 Scripts/animate_15.py --dry-run   # list what would be assembled
    python3 Scripts/animate_15.py             # assemble + bake them
    python3 Scripts/animate_15.py --stage     # …and copy into the content-editor
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from app import _validate_spec  # noqa: E402
from core import baker  # noqa: E402
from core import scaffold  # noqa: E402

# Frame block → clip slot, in the order the palette lists the sprites.
BLOCKS = [("walk", "down_left"), ("walk", "down_right"),
          ("walk", "up_left"), ("walk", "up_right"), ("idle", None)]
BLOCK_SIZE = 3
EXPECTED = len(BLOCKS) * BLOCK_SIZE


def _specs_by_sheet():
    """{sheet_id: spec_id} for every spec already on disk."""
    out = {}
    if not os.path.isdir(config.SPEC_DIR):
        return out
    for name in sorted(os.listdir(config.SPEC_DIR)):
        if not name.endswith(".extract.json"):
            continue
        with open(os.path.join(config.SPEC_DIR, name), "r", encoding="utf-8") as f:
            spec = json.load(f)
        out[str(spec.get("sheet_id"))] = int(spec["id"])
    return out


def _free_ids(taken):
    """Yield the lowest unused creature ids, so numbering stays dense."""
    n = 1
    while True:
        if n not in taken:
            yield n
        n += 1


def _candidates(specs_by_sheet):
    """Cropped, unbaked sheets holding exactly EXPECTED sprites."""
    out = []
    for name in sorted(os.listdir(config.BOX_DIR)):
        if not name.endswith(".boxes.json"):
            continue
        sheet_id = name[:-len(".boxes.json")]
        with open(os.path.join(config.BOX_DIR, name), "r", encoding="utf-8") as f:
            cache = json.load(f)
        if not cache.get("edited") or cache.get("skip"):
            continue          # never reviewed, or marked "no sprites"
        if len(cache.get("boxes") or []) != EXPECTED:
            continue
        nnn = specs_by_sheet.get(sheet_id)
        if nnn is not None and os.path.exists(os.path.join(config.OUT_DIR, "%03d.png" % nnn)):
            continue          # already baked = already done
        out.append((sheet_id, cache, nnn))
    out.sort(key=lambda c: int(c[0]) if c[0].isdigit() else 0)
    return out


def build_spec(sheet_id, cache, spec_id):
    """The spec the animate view would have written for this sheet."""
    bg = cache.get("background") or {}
    colors = bg.get("colors") or []
    ids = [b["id"] for b in cache["boxes"]]
    walk, idle = {}, []
    for i, (clip, direction) in enumerate(BLOCKS):
        frames = ids[i * BLOCK_SIZE:(i + 1) * BLOCK_SIZE]
        if clip == "walk":
            walk[direction] = frames
        else:
            idle = frames
    seg = dict(cache.get("params") or {})
    seg["tol"] = bg.get("tolerance", seg.get("tol"))
    seg["extra_bg"] = colors[1:]
    if bg.get("enclosed") is not None:
        seg["bg_enclosed"] = bg["enclosed"]
    return {
        "spec_version": 1, "species": "digimon", "id": spec_id,
        "source": f"raw_sheets/{sheet_id}.png", "sheet_id": sheet_id,
        "background": bg.get("color"), "background_tolerance": bg.get("tolerance"),
        "background_mode": bg.get("mode"), "seg_params": seg,
        "trim": True, "export_scale": 1, "pad": 1,
        "boxes": [{"id": b["id"], "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]}
                  for b in cache["boxes"]],
        "clips": {"walk": walk, "idle": idle, "sleep": []}, "mirror": {},
        "diagonals": True, "anchor": "bottom_center", "walk_style": "stride",
        "tick_ms": 33, "walk_durations": [6], "idle_frame_ms": 300,
        "sleep_frame_ms": 400, "notes": "",
        "creature_name": "", "creature_type": "Data",
        "creature_color": "0x8899AA", "creature_stage": 1,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="list, write nothing")
    ap.add_argument("--limit", type=int, help="only the first N sheets")
    ap.add_argument("--stage", action="store_true",
                    help="also copy each baked asset into the content-editor tree")
    args = ap.parse_args()
    config.ensure_dirs()

    specs_by_sheet = _specs_by_sheet()
    todo = _candidates(specs_by_sheet)
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} sheets to animate ({EXPECTED} sprites each)")

    ids = _free_ids(set(specs_by_sheet.values()))
    ok = fail = 0
    for sheet_id, cache, nnn in todo:
        # A sheet that already has a draft spec keeps its number.
        spec_id = nnn if nnn is not None else next(ids)
        spec = build_spec(sheet_id, cache, spec_id)
        err = _validate_spec(spec)
        if err:
            print(f"  ! {sheet_id}: {err}")
            fail += 1
            continue
        if args.dry_run:
            print(f"  {sheet_id} → {spec_id:03d}  " +
                  "  ".join(f"{d or 'idle'}={','.join(f)}" for (c, d), f in
                            zip(BLOCKS, [spec['clips']['walk'].get(d) or spec['clips']['idle']
                                         for c, d in BLOCKS])))
            ok += 1
            continue
        path = os.path.join(config.SPEC_DIR, f"{spec_id:03d}.extract.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)
            f.write("\n")
        try:
            res = baker.bake(path, log=lambda *_: None)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {sheet_id} ({spec_id:03d}): bake failed — {exc}")
            fail += 1
            continue
        scaffold.write_creature({"id": spec_id, "type": "Data",
                                 "color": "0x8899AA", "stage": 1}, species="digimon")
        if args.stage:
            scaffold.stage_to_content("%03d" % spec_id, write_creature_node=True)
        print(f"  {sheet_id} → {spec_id:03d}: {res['cols']}x{res['rows']} cells")
        ok += 1
    verb = "would assemble" if args.dry_run else "baked"
    print(f"{verb} {ok}, failed {fail}")


if __name__ == "__main__":
    main()
