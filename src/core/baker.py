"""Bake an extraction spec into a unified sheet PNG + explicit SpriteLayout.

A single placement pass drives BOTH the composited PNG and the layout
``{col,row}`` cells, so they can never drift out of sync.

These sheets are assembled for the ISOMETRIC apps, whose walker asks for the
diagonal direction keys (``PetWalker::walker_dir_from_delta_iso`` → 4=SE, 5=NE,
6=NW, 7=SW). So an *iso* spec authors only those four facings; the four cardinals
the schema requires are emitted as ALIASES pointing at the same rows (no extra
pixels), using the equivalence a single grid step has in both projections:
``right≡SE, up≡NE, left≡NW, down≡SW``. The layout then declares 8 directions, an
iso consumer gets four distinct facings instead of the nearest-cardinal fallback,
and a top-down consumer still gets a sensible pose per direction.

Emitted grid (matching hibitomo ``pokemon/001.json``):
  rows 0..k-1  : walk, one row per direction that has frames (iso facings for an
                 iso spec, cardinals for a legacy top-down one)
  next row     : idle — ONE shared row (non-directional: the pet faces the camera
                 while standing still), defaults to the front walk[0] pose. Every
                 direction's ``idle`` in the layout points at that same row, so
                 the emitted JSON stays the per-direction shape the schema and the
                 firmware expect.
  last row     : sleep (non-directional), if the spec has sleep frames
Every cell is the same size; frames are anchor-aligned (bottom-center default)
and left-packed from column 0. Trailing cells in short rows stay transparent
and are never referenced by the layout. Frames are magnified by the spec's
``export_scale`` (2 by default, see ``DEFAULT_EXPORT_SCALE``) before placement.
"""
import json
import os

import numpy as np
from PIL import Image

import config
from core import segmenter as S
from core import extractor as E
from core import layout as L


def _load_spec(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        return json.load(f)


# The four iso facings, in the order the animate view lists (and bakes) them:
# the two front views first, then the two back ones.
ISO_DIRS = ["down_left", "down_right", "up_left", "up_right"]
ISO_LABEL = {"down_right": "SE", "up_right": "NE", "up_left": "NW",
             "down_left": "SW"}
# The cardinal each iso facing stands in for: one grid step reads as this
# direction top-down and as that facing in the 2:1 iso projection (+gx = right =
# SE, -gy = up = NE, -gx = left = NW, +gy = down = SW). Used to alias the
# schema-required cardinals onto the iso rows.
ISO_TO_CARDINAL = {"down_right": "right", "up_right": "up",
                   "up_left": "left", "down_left": "down"}

# Digimon World DS draws its overworld sprites at roughly half the size of the
# Pokémon Mystery Dungeon rips the sibling tool exports, and hibitomo renders a
# sheet cell 1:1 on screen — so a 1x digimon shows up tiny next to a pokémon.
# PMDSpriteManager magnifies by 2 on export (`firmware_exporter.DEFAULT_SCALE`);
# match it here so both rosters land in the content-editor at the same apparent
# size. Nearest-neighbour, so the pixel art stays crisp.
DEFAULT_EXPORT_SCALE = 2


def _is_iso(spec):
    """True when the spec authors the iso facings (frames or a mirror on any)."""
    walk = spec.get("clips", {}).get("walk", {}) or {}
    mirror = spec.get("mirror", {}) or {}
    return any(walk.get(dr) or dr in mirror for dr in ISO_DIRS)


def _emitted_dirs(spec):
    """Directions to walk when planning rows — iso facings in slot order, so the
    baked sheet's row order matches what the animate view shows."""
    if spec.get("diagonals") or _is_iso(spec):
        return L.CARDINALS + ISO_DIRS
    return L.CARDINALS


def _resolve_clip_dir(spec, clip, direction):
    """Return (list_of_box_ids, hflip) for a (clip, direction), honoring mirror.

    ``mirror`` maps a direction to the one it is horizontally flipped from, e.g.
    ``{"right": "left"}`` bakes ``right`` from ``left``'s frames, flipped.
    """
    mirror = spec.get("mirror", {})
    if direction in mirror:
        src = mirror[direction]
        ids = spec.get("clips", {}).get(clip, {}).get(src, [])
        return list(ids), True
    ids = spec.get("clips", {}).get(clip, {}).get(direction, [])
    return list(ids), False


def _resolve_idle(spec):
    """Return (list_of_box_ids, hflip) for the ONE non-directional idle clip.

    The pet always faces the camera while standing still, so ``clips.idle`` is a
    flat frame list. A spec written by an older build carries the per-direction
    dict instead — collapse it to the front (``down``) facing, or to the first
    non-empty direction. An empty idle falls back to the first front-facing walk
    pose there is: SW, then SE for an iso spec, then the top-down ``down``.
    """
    idle = spec.get("clips", {}).get("idle")
    if isinstance(idle, dict):  # legacy per-direction idle
        ids = list(idle.get("down") or next((v for v in idle.values() if v), []))
        flip = False
    else:
        ids, flip = list(idle or []), False
    if not ids:
        for cand in ("down_left", "down_right", "down"):
            ids, flip = _resolve_clip_dir(spec, "walk", cand)
            if ids:
                break
        ids = ids[:1]
    return ids, flip


def bake(spec_path, out_dir=None, log=print):
    """Bake one spec. Returns a result dict (paths, geometry, layout preview)."""
    out_dir = out_dir or config.OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    spec = _load_spec(spec_path)
    sid = "%03d" % int(spec["id"])

    src_png = spec["source"]
    if not os.path.isabs(src_png):
        src_png = os.path.join(config.ROOT, src_png)
    arr = S.load_rgba(src_png)

    p = S.SegParams.merged(spec.get("seg_params"))
    if "background_tolerance" in spec:
        p.tol = int(spec["background_tolerance"])
    if spec.get("background_mode") == "alpha":
        bg = {"mode": "alpha", "colors": [], "color": None, "has_alpha": True}
    elif spec.get("background"):
        # Reconstruct the exact colour set the operator curated: the primary
        # background plus every extra_bg colour (e.g. the per-cell gray). If we
        # only keyed the primary, sprites would keep their cell backdrop.
        # ``has_alpha`` comes off the sheet rather than the spec: a spec written
        # before alpha keying existed says "solid" about a transparent rip.
        colors = []
        prim = _parse_color(spec.get("background"))
        if prim:
            colors.append(prim)
        for c in (p.extra_bg or []):
            t = _parse_color(c) if isinstance(c, str) else tuple(int(v) for v in c[:3])
            if t and t not in colors:
                colors.append(t)
        bg = {"mode": "solid", "colors": colors,
              "color": colors[0] if colors else None,
              "has_alpha": S.sheet_uses_alpha(arr, p)}
    else:
        bg = S.infer_background(arr, p)

    boxes_by_id = {b["id"]: b for b in spec.get("boxes", [])}
    export_scale = int(spec.get("export_scale") or DEFAULT_EXPORT_SCALE)
    pad = int(spec.get("pad", 1))
    anchor = spec.get("anchor", "bottom_center")
    iso = _is_iso(spec)
    dirs = _emitted_dirs(spec)

    # --- 1: gather every referenced frame as (clip, dir, frame_idx, id, hflip)
    plan = []    # ordered rows: dict(clip, dir|None, frame_ids, hflip)
    for dr in dirs:
        ids, flip = _resolve_clip_dir(spec, "walk", dr)
        if ids:
            plan.append({"clip": "walk", "dir": dr, "ids": ids, "flip": flip})
    authored = {row["dir"] for row in plan}
    # An iso spec must carry all four iso facings (mirroring one from its
    # horizontal twin counts); the cardinals are aliased from them below. A
    # legacy top-down spec still has to carry the four cardinals itself.
    required = ISO_DIRS if iso else L.CARDINALS
    missing = [dr for dr in required if dr not in authored]
    if missing:
        pretty = ", ".join(f"{ISO_LABEL[d]} ({d})" if d in ISO_LABEL else d
                           for d in missing)
        raise ValueError(f"walk has no frames for {pretty} (spec {sid})")
    idle_ids, idle_flip = _resolve_idle(spec)
    if idle_ids:
        plan.append({"clip": "idle", "dir": None, "ids": idle_ids, "flip": idle_flip})
    sleep_ids = spec.get("clips", {}).get("sleep", [])
    if sleep_ids:
        plan.append({"clip": "sleep", "dir": None, "ids": list(sleep_ids), "flip": False})

    # --- 2: extract every unique box once (cache), record content size
    cache = {}

    def get_img(box_id):
        if box_id not in cache:
            box = boxes_by_id.get(box_id)
            if box is None:
                raise ValueError(f"unknown box id {box_id!r} in spec {sid}")
            img, info = E.extract_box(arr, bg, box, p)
            if export_scale != 1:
                img = img.resize((img.width * export_scale, img.height * export_scale),
                                 Image.NEAREST)
            cache[box_id] = img
        return cache[box_id]

    # collect all imgs to size the uniform cell
    max_w = max_h = 1
    for row in plan:
        for bid in row["ids"]:
            im = get_img(bid)
            max_w = max(max_w, im.width)
            max_h = max(max_h, im.height)
    cell_w = max_w + 2 * pad
    cell_h = max_h + 2 * pad

    # --- 3: grid dims
    rows = len(plan)
    cols = max((len(r["ids"]) for r in plan), default=1)
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 0))

    # --- 4: composite + build layout cell maps
    walk_cells, idle_cells = {}, {}
    sleep_cells = []
    for r_idx, row in enumerate(plan):
        cellrow = []
        for c_idx, bid in enumerate(row["ids"]):
            im = get_img(bid)
            if row["flip"]:
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            _paste_anchored(sheet, im, c_idx, r_idx, cell_w, cell_h, pad, anchor)
            cellrow.append({"col": c_idx, "row": r_idx})
        if row["clip"] == "walk":
            walk_cells[row["dir"]] = cellrow
        elif row["clip"] == "idle":
            # One shared row: point every emitted direction at it, so the layout
            # keeps its per-direction shape while the pet holds the same
            # camera-facing pose no matter which way it last walked.
            idle_cells = {dr: cellrow for dr in dirs}
        else:
            sleep_cells = cellrow

    # The schema requires the four cardinals in `walk`, and an iso spec authors
    # none of them: point each at the row of the facing it is the top-down
    # reading of (same cells, no extra pixels). A spec that DID author a cardinal
    # keeps its own row.
    if iso:
        for iso_dir, card in ISO_TO_CARDINAL.items():
            if not walk_cells.get(card) and walk_cells.get(iso_dir):
                walk_cells[card] = walk_cells[iso_dir]

    png_path = os.path.join(out_dir, f"{sid}.png")
    sheet.save(png_path)

    # --- 5: build + validate + write layout
    walk_durations = spec.get("walk_durations")
    lay = L.build_layout(
        cols, rows, walk_cells, idle_cells, sleep_cells or None,
        diagonals=bool(spec.get("diagonals")) or iso,
        walk_style=spec.get("walk_style", "stride"),
        tick_ms=spec.get("tick_ms", 33),
        walk_durations=walk_durations,
        idle_durations=spec.get("idle_durations"),
        sleep_durations=spec.get("sleep_durations"),
        idle_frame_ms=spec.get("idle_frame_ms"),
        sleep_frame_ms=spec.get("sleep_frame_ms"),
        description=spec.get("notes") or None,
    )
    errs = L.validate_layout(lay)
    if errs:
        for e in errs:
            log(f"  ! layout warning ({sid}): {e}")
    layout_path = os.path.join(out_dir, f"{sid}.json")
    with open(layout_path, "w", encoding="utf-8") as f:
        f.write(L.dumps_layout(lay))

    log(f"baked {sid}: {cols}x{rows} cells ({cell_w}x{cell_h}px) → {png_path}")
    return {"ok": True, "id": int(spec["id"]), "png": png_path,
            "layout": layout_path, "cols": cols, "rows": rows,
            "cell_w": cell_w, "cell_h": cell_h, "layout_preview": lay,
            "warnings": errs}


def _paste_anchored(sheet, im, col, row, cell_w, cell_h, pad, anchor):
    """Paste ``im`` into grid cell (col,row), aligned per ``anchor``."""
    cx = col * cell_w
    cy = row * cell_h
    inner_w = cell_w - 2 * pad
    inner_h = cell_h - 2 * pad
    if anchor == "bottom_center":
        ox = cx + pad + (inner_w - im.width) // 2
        oy = cy + pad + (inner_h - im.height)
    elif anchor == "center":
        ox = cx + pad + (inner_w - im.width) // 2
        oy = cy + pad + (inner_h - im.height) // 2
    else:  # top_left
        ox = cx + pad
        oy = cy + pad
    sheet.alpha_composite(im, (ox, oy))


def _parse_color(v):
    if isinstance(v, (list, tuple)):
        return tuple(int(x) for x in v[:3])
    if isinstance(v, str):
        s = v.lower().replace("0x", "").replace("#", "")
        if len(s) == 6:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    return None


def bake_all(spec_dir=None, out_dir=None, log=print, stage=False):
    """Bake every ``*.extract.json`` in ``spec_dir``. Returns (ok, fail)."""
    spec_dir = spec_dir or config.SPEC_DIR
    out_dir = out_dir or config.OUT_DIR
    ok = fail = 0
    for name in sorted(os.listdir(spec_dir)):
        if not name.endswith(".extract.json"):
            continue
        try:
            bake(os.path.join(spec_dir, name), out_dir, log=log)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            log(f"  ! bake failed for {name}: {exc}")
    log(f"bake_all: {ok} ok, {fail} failed")
    return ok, fail
