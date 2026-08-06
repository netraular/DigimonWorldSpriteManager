"""Assemble + bake every cropped sheet whose sprite count follows a known layout.

The DWDS rips are animated in blocks of three frames, laid out in reading order,
and the sprite count tells you which blocks a sheet carries:

    15 sprites   SW · SE · NW · NE · idle
    12 sprites   SW · SE · NW · NE           (no idle block — the baker holds the
                                              first SW pose while standing still)
     9 sprites   NW · SW · idle              (only the two left-facing views are
                                              drawn; SE mirrors SW and NE mirrors
                                              NW, which is what the animate view's
                                              mirror checkbox does by hand)

So the whole backlog of those three shapes can be assembled without opening the
editor once: each sheet gets the very spec the editor would have written (same
background, boxes, timing and creature defaults), then the same save + bake —
``specs/digimon/<NNN>.extract.json`` → ``output/digimon/<NNN>.png`` +
``<NNN>.json`` + ``pet_<NNN>.json``. A baked sheet is what the gallery counts as
done, so the sheets leave the "To animate" chip on their own.

Only sheets that are cropped, not marked "no sprites" and NOT already baked are
touched, which makes a run idempotent and keeps hand-authored work safe.

Backs both ``Scripts/animate_blocks.py`` and the gallery's Auto-assemble button
(``POST /api/auto-assemble``); no Flask import, like the rest of ``core/``.
"""
import json
import os

import config
from core import baker
from core import detect as D
from core import scaffold

BLOCK_SIZE = 3

# sprite count → the clip slot each block of three feeds, plus the facings that
# are baked as a horizontal flip of another (spec ``mirror``: dir → source dir).
LAYOUTS = {
    15: {"slots": [("walk", "down_left"), ("walk", "down_right"),
                   ("walk", "up_left"), ("walk", "up_right"), ("idle", None)],
         "mirror": {}},
    12: {"slots": [("walk", "down_left"), ("walk", "down_right"),
                   ("walk", "up_left"), ("walk", "up_right")],
         "mirror": {}},
    9:  {"slots": [("walk", "up_left"), ("walk", "down_left"), ("idle", None)],
         "mirror": {"down_right": "down_left", "up_right": "up_left"}},
}

DIR_LABEL = {"down_left": "SW", "down_right": "SE",
             "up_left": "NW", "up_right": "NE"}


def describe(count):
    """Human-readable block plan for a sprite count, e.g. "NW · SW · idle (+mirrors)"."""
    lay = LAYOUTS[count]
    parts = [DIR_LABEL.get(d, d) if c == "walk" else c for c, d in lay["slots"]]
    txt = " · ".join(parts)
    if lay["mirror"]:
        txt += " (+%s mirrored)" % ", ".join(DIR_LABEL.get(d, d) for d in lay["mirror"])
    return txt


def _specs_by_sheet():
    """{sheet_id: spec_id} for every spec already on disk."""
    out = {}
    if not os.path.isdir(config.SPEC_DIR):
        return out
    for name in sorted(os.listdir(config.SPEC_DIR)):
        if not name.endswith(".extract.json"):
            continue
        try:
            with open(os.path.join(config.SPEC_DIR, name), "r", encoding="utf-8") as f:
                spec = json.load(f)
            out[str(spec.get("sheet_id"))] = int(spec["id"])
        except Exception:  # noqa: BLE001 — a hand-edited spec must not stop a run
            continue
    return out


def _free_ids(taken):
    """Yield the lowest unused creature ids, so numbering stays dense."""
    n = 1
    while True:
        if n not in taken:
            yield n
        n += 1


def candidates(counts=None, rebake=False):
    """Cropped, unbaked sheets whose sprite count has a layout.

    Returns ``[{sheet_id, count, cache, spec_id}]`` in sheet order; ``spec_id`` is
    the number an existing draft spec already claimed, else ``None``.
    ``rebake`` also returns the sheets already baked, so a layout fix can be
    replayed over them (it overwrites their spec).
    """
    counts = set(counts or LAYOUTS)
    specs_by_sheet = _specs_by_sheet()
    out = []
    for name in sorted(os.listdir(config.BOX_DIR)):
        if not name.endswith(".boxes.json"):
            continue
        sheet_id = name[:-len(".boxes.json")]
        try:
            with open(os.path.join(config.BOX_DIR, name), "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:  # noqa: BLE001
            continue
        if not cache.get("edited") or cache.get("skip"):
            continue          # never reviewed, or marked "no sprites"
        n = len(cache.get("boxes") or [])
        if n not in counts:
            continue
        nnn = specs_by_sheet.get(sheet_id)
        if (not rebake and nnn is not None
                and os.path.exists(os.path.join(config.OUT_DIR, "%03d.png" % nnn))):
            continue          # already baked = already done
        out.append({"sheet_id": sheet_id, "count": n, "cache": cache, "spec_id": nnn})
    out.sort(key=lambda c: int(c["sheet_id"]) if c["sheet_id"].isdigit() else 0)
    # Hand out the free numbers only now, so they follow sheet order.
    ids = _free_ids(set(specs_by_sheet.values()))
    for c in out:
        if c["spec_id"] is None:
            c["spec_id"] = next(ids)
    return out


def unreviewed():
    """Raw sheets nobody has cropped yet — neither saved nor marked "no sprites".

    Cheap (it only stats the box cache): the sprite count of a new sheet is not
    known until ``scan_new`` actually detects it.
    """
    out = []
    for name in sorted(os.listdir(config.RAW_DIR)):
        if not name.endswith(".png"):
            continue
        sheet_id = name[:-4]
        cache = os.path.join(config.BOX_DIR, f"{sheet_id}.boxes.json")
        if os.path.exists(cache):
            try:
                with open(cache, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("edited") or d.get("skip"):
                    continue      # already reviewed in /crop
            except Exception:  # noqa: BLE001
                pass
        out.append(sheet_id)
    out.sort(key=lambda s: int(s) if s.isdigit() else 0)
    return out


def scan_new(counts=None, dry_run=False, log=print, progress=None):
    """Auto-crop the never-reviewed sheets whose detection lands on a known count.

    Runs /crop's own one-click Auto (corner colour → separate → keep the modal
    sprite size, ``detect.auto_crop``), and when it finds 15/12/9 sprites saves
    the boxes as a reviewed crop — which is what promotes the sheet to
    ``candidates()`` below, so the very same click goes on to bake it. A sheet
    whose detection lands on any other count is left untouched in the To-do
    chip: an unusual layout is exactly the case a human should look at.

    Returns ``[{sheet_id, count}]`` for the sheets it cropped.
    """
    counts = set(counts or LAYOUTS)
    todo = unreviewed()
    picked = []
    for i, sheet_id in enumerate(todo):
        if progress:
            progress(i, len(todo), 0, "scan")
        try:
            res = D.auto_crop(sheet_id)
        except Exception as exc:  # noqa: BLE001 — a broken PNG must not stop the run
            log(f"  ! {sheet_id}: detect failed — {exc}")
            continue
        if res is None:
            continue
        n = len(res.get("boxes") or [])
        if n not in counts:
            continue
        picked.append({"sheet_id": sheet_id, "count": n})
        log(f"  {sheet_id}: {n} sprites → cropped")
        if dry_run:
            continue
        res["edited"] = True
        res["skip"] = False
        with open(os.path.join(config.BOX_DIR, f"{sheet_id}.boxes.json"), "w",
                  encoding="utf-8") as f:
            json.dump(res, f)
    if progress:
        progress(len(todo), len(todo), 0, "scan")
    return picked


def build_spec(sheet_id, cache, spec_id):
    """The spec the animate view would have written for this sheet."""
    boxes = cache["boxes"]
    lay = LAYOUTS[len(boxes)]
    bg = cache.get("background") or {}
    colors = bg.get("colors") or []
    ids = [b["id"] for b in boxes]
    walk, idle = {}, []
    for i, (clip, direction) in enumerate(lay["slots"]):
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
                  for b in boxes],
        "clips": {"walk": walk, "idle": idle, "sleep": []},
        "mirror": dict(lay["mirror"]),
        "diagonals": True, "anchor": "bottom_center", "walk_style": "stride",
        "tick_ms": 33, "walk_durations": [6], "idle_frame_ms": 300,
        "sleep_frame_ms": 400, "notes": "",
        "creature_name": "", "creature_type": "Data",
        "creature_color": "0x8899AA", "creature_stage": 1,
    }


def run(counts=None, limit=None, stage=False, dry_run=False, include_new=False,
        validate=None, log=print, progress=None, rebake=False):
    """Assemble + bake every candidate. Returns a summary dict.

    ``include_new`` first runs ``scan_new``, so a batch of freshly downloaded
    sheets goes from raw PNG to baked creature in one call. ``validate`` is the
    app's spec check (passed in so ``core`` keeps its no-Flask rule);
    ``progress(done, total, fail, phase)`` is called as work advances so a caller
    can drive a progress bar.
    """
    config.ensure_dirs()
    cropped = scan_new(counts, dry_run=dry_run, log=log,
                       progress=progress) if include_new else []
    todo = candidates(counts, rebake=rebake)
    if dry_run and include_new:
        # Nothing was written, so the sheets scan_new picked are not candidates
        # yet — report them as what the run would take on.
        known = {c["sheet_id"] for c in todo}
        todo += [{"sheet_id": c["sheet_id"], "count": c["count"], "cache": None,
                  "spec_id": None} for c in cropped if c["sheet_id"] not in known]
    if limit:
        todo = todo[:limit]
    total = len(todo)
    done = fail = 0
    items, errors = [], []
    if progress:
        progress(done, total, fail, "assemble")
    for c in todo:
        sheet_id, spec_id = c["sheet_id"], c["spec_id"]
        if c["cache"] is None:  # dry run, sheet not cropped yet — plan only
            items.append({"sheet_id": sheet_id, "id": None, "count": c["count"],
                          "plan": describe(c["count"]) + " (new)"})
            done += 1
            continue
        spec = build_spec(sheet_id, c["cache"], spec_id)
        err = validate(spec) if validate else None
        if not err and dry_run:
            items.append({"sheet_id": sheet_id, "id": spec_id, "count": c["count"],
                          "plan": describe(c["count"])})
            done += 1
        elif not err:
            path = os.path.join(config.SPEC_DIR, f"{spec_id:03d}.extract.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2)
                f.write("\n")
            try:
                res = baker.bake(path, log=lambda *_: None)
            except Exception as exc:  # noqa: BLE001
                err = f"bake failed — {exc}"
            else:
                scaffold.write_creature({"id": spec_id, "type": "Data",
                                         "color": "0x8899AA", "stage": 1},
                                        species="digimon")
                if stage:
                    scaffold.stage_to_content("%03d" % spec_id, write_creature_node=True)
                items.append({"sheet_id": sheet_id, "id": spec_id, "count": c["count"],
                              "cols": res["cols"], "rows": res["rows"]})
                done += 1
                log(f"  {sheet_id} → {spec_id:03d}: {res['cols']}x{res['rows']} cells "
                    f"({c['count']} sprites)")
        if err:
            fail += 1
            errors.append({"sheet_id": sheet_id, "error": err})
            log(f"  ! {sheet_id}: {err}")
        if progress:
            progress(done + fail, total, fail, "assemble")
    return {"total": total, "ok": done, "fail": fail, "items": items,
            "errors": errors, "cropped": cropped}
