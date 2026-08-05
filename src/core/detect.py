"""Auto-detection of a sheet's sprite boxes, in the shape the box cache stores.

Lifted out of ``app.py`` so the batch tools can detect a never-opened sheet
without importing Flask: the /crop editor, ``POST /api/sheets/<id>/boxes`` and
``core/autoassemble.py`` all run this exact code, so a sheet auto-processed in
bulk is cropped the same way opening it in /crop would have cropped it.
"""
import os

import config
from core import segmenter as S


def safe(sid):
    return "".join(c for c in str(sid) if c.isalnum())


def bg_payload(bg, p):
    """Background block of the box cache: every keyed colour, hex + rgb + tol."""
    colors = bg.get("colors") or ([bg["color"]] if bg.get("color") else [])
    return {
        "mode": bg["mode"],
        "colors": [list(c) for c in colors],
        "hex": ["0x%02X%02X%02X" % tuple(c) for c in colors],
        "color": ("0x%02X%02X%02X" % tuple(colors[0])) if colors else None,
        "rgb": list(colors[0]) if colors else None,
        "tolerance": p.tol,
        "has_alpha": bg.get("has_alpha", False),
    }


# ---- the /crop editor's one-click auto-detect (its "A" action), in Python ----
# Same two steps, same constants, so a sheet cropped in bulk is cropped exactly
# the way clicking Auto in /crop would have cropped it:
#   1. the sheet's corner colour IS the background (rips key the whole sheet with
#      one flat colour) — eyedrop it and separate on that colour ALONE;
#   2. the most repeated box size is "the sprite size" (every sprite sits in an
#      identically-sized cell), so anything else — credits, labels, specks — goes.
AUTO_MIN_SIDE = 16   # a sprite is at least this many px on each side
AUTO_TOL = 1         # px of jitter tolerated around the modal size
# Literal keying: only the picked colour goes, no hue expansion, no gap bridging,
# no min-size filter — so each sprite keeps its full coloured cell box.
AUTO_PARAMS = {"explicit_bg_only": True, "use_hsv": False, "tol": 8,
               "merge_gap": 0, "area_min": 0, "auto_secondary_bg": False,
               "morph_px": 0, "dilate_px": 0, "max_bg_colors": 8}


def corner_background(arr):
    """The colour of the sheet's corners (the most repeated one), or None if they
    are transparent — an alpha-keyed sheet needs no pick."""
    h, w = arr.shape[0], arr.shape[1]
    tally = {}
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        px = arr[y, x]
        if arr.shape[2] > 3 and int(px[3]) <= 16:
            continue
        key = tuple(int(v) for v in px[:3])
        tally[key] = tally.get(key, 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda kv: kv[1])[0]


def modal_size(boxes):
    """The size shared by the most boxes (within AUTO_TOL), ties to the bigger."""
    cands = [b for b in boxes if b["w"] >= AUTO_MIN_SIDE and b["h"] >= AUTO_MIN_SIDE]
    best = None
    for c in cands:
        n = sum(1 for b in cands
                if abs(b["w"] - c["w"]) <= AUTO_TOL and abs(b["h"] - c["h"]) <= AUTO_TOL)
        if best is None or n > best[2] or (n == best[2] and c["w"] * c["h"] > best[0] * best[1]):
            best = (c["w"], c["h"], n)
    return best


def auto_crop(sid):
    """Detect one sheet the way /crop's Auto button does. ``None`` if no sheet."""
    path = os.path.join(config.RAW_DIR, f"{safe(sid)}.png")
    if not os.path.exists(path):
        return None
    rgb = corner_background(S.load_rgba(path))
    overrides = dict(AUTO_PARAMS)
    overrides["extra_bg"] = [list(rgb)] if rgb else []
    res = detect_sheet(sid, overrides)
    if res is None:
        return None
    size = modal_size(res["boxes"])
    if size:
        res["boxes"] = [b for b in res["boxes"]
                        if abs(b["w"] - size[0]) <= AUTO_TOL and abs(b["h"] - size[1]) <= AUTO_TOL]
    return res


def detect_sheet(sid, overrides=None):
    """Detect the sprite boxes of one raw sheet. ``None`` if there is no such sheet.

    An optional ``region`` rectangle in ``overrides`` limits detection (and
    background inference) to the meaningful part of a sheet, so credit text or
    labels outside it are ignored. Boxes are offset back into full-image
    coordinates.
    """
    path = os.path.join(config.RAW_DIR, f"{safe(sid)}.png")
    if not os.path.exists(path):
        return None
    arr = S.load_rgba(path)
    full_h, full_w = int(arr.shape[0]), int(arr.shape[1])

    region = None
    if isinstance(overrides, dict):
        region = overrides.pop("region", None)
    p = S.SegParams.merged(overrides)

    ox, oy = 0, 0
    sub = arr
    if region:
        try:
            rx, ry, rw, rh = (int(round(float(v))) for v in region)
        except (TypeError, ValueError):
            rx = ry = rw = rh = 0
        if rw > 0 and rh > 0:
            rx = max(0, min(rx, full_w - 1)); ry = max(0, min(ry, full_h - 1))
            rw = max(1, min(rw, full_w - rx)); rh = max(1, min(rh, full_h - ry))
            ox, oy = rx, ry
            sub = arr[ry:ry + rh, rx:rx + rw]

    boxes, meta = S.detect_boxes(sub, p)
    box_dicts = []
    for b in boxes:
        d = b.as_dict(); d["x"] += ox; d["y"] += oy
        box_dicts.append(d)
    return {
        "id": sid,
        "image": {"w": full_w, "h": full_h},
        "background": bg_payload(meta["background"], p),
        "grid": meta["grid"],
        "boxes": box_dicts,
        "params": meta["params"],
        "region": [ox, oy, int(sub.shape[1]), int(sub.shape[0])] if region else None,
    }
