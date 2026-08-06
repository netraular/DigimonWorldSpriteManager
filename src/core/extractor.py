"""Clean per-box sprite extraction.

Given a chosen bounding box on a sheet, crop it, key out the background to
transparent alpha (EVERY pixel of a background colour, enclosed ones included —
the gap between an arm and the body is backdrop and has to read through; see
``bg_enclosed``), de-fringe the anti-aliased rim, and hand back the box-sized
frame plus where its content landed inside it.

Rips usually park each sprite on its own flat "cell" rectangle over the outer
chroma key (grey/green/blue cells on teal). The crop view keeps those cell boxes
on purpose — they are what makes every sprite the same size — so the cell colour
is NOT part of the sheet background and would ride along into the sprite. Hence
``_cell_color``: whatever flat colour dominates a box's own border ring is keyed
out too, per box, so sprites come out transparent without anybody eyedropping
each cell shade by hand.
"""
import collections

import numpy as np
from PIL import Image
from scipy import ndimage

from core import segmenter as S


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _phantom_color(arr, p):
    """The RGB the sheet's fully-transparent pixels carry, or ``None``.

    A transparent pixel has no colour: its RGB is whatever the encoder left
    under it, which on these rips is always black. That value is not a
    background colour anybody could have meant to pick, and keying it takes
    every OPAQUE pixel the artist drew in it — on a black-under-alpha rip, every
    outline, and with it the wing or the tail the outline was holding together.
    /crop's eyedropper already refuses to pick a transparent pixel, but sheets
    keyed before that guard existed still carry the colour in their box cache
    (46749, 48330, 48332), so it is dropped here too — at the one place the
    palette and the baker share, which is what heals the old caches and the
    specs baked from them without touching either.
    """
    if not S.sheet_uses_alpha(arr, p):
        return None
    px = arr[:, :, :3][arr[:, :, 3] < p.alpha_thresh].astype(np.int32)
    if not len(px):
        return None
    packed = (px[:, 0] << 16) | (px[:, 1] << 8) | px[:, 2]
    vals, counts = np.unique(packed, return_counts=True)
    top = int(vals[counts.argmax()])
    return ((top >> 16) & 255, (top >> 8) & 255, top & 255)


def _ring_mask(shape, rect, width=1):
    """Boolean mask of the ``width``-px frame of ``rect`` inside a (h, w) crop.

    The frame follows the OPERATOR'S box, not the padded crop: the pad lies in the
    sheet background, so a ring taken from the crop's own edge would be all
    background and say nothing about what the sprite sits on.
    """
    m = np.zeros(shape, dtype=bool)
    x, y, w, h = rect
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(shape[1], x + w), min(shape[0], y + h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return m
    d = max(1, int(width))
    m[y0:min(y0 + d, y1), x0:x1] = True
    m[max(y0, y1 - d):y1, x0:x1] = True
    m[y0:y1, x0:min(x0 + d, x1)] = True
    m[y0:y1, max(x0, x1 - d):x1] = True
    return m


def _defringe(alpha):
    """Erode the opaque mask by 1px to shave the chroma-key rim."""
    opaque = alpha > 0
    if not opaque.any():
        return alpha
    eroded = ndimage.binary_erosion(opaque, iterations=1, border_value=1)
    return np.where(eroded, alpha, 0).astype(np.uint8)


def _largest_share(mask):
    """Share of the opaque pixels that belong to their biggest blob (1.0 = one piece)."""
    if not mask.any():
        return 0.0
    lbl, n = ndimage.label(mask, structure=np.ones((3, 3), int))
    if n <= 1:
        return 1.0
    sizes = ndimage.sum(mask, lbl, range(1, n + 1))
    return float(sizes.max() / sizes.sum())


def _keeps_sprite(before, after, p):
    """Did keying the cell colour peel a backdrop, or eat the sprite?

    Removing a cell leaves the sprite whole; keying a colour the sprite is MADE of
    shatters it into crumbs (a white Digimon on a white cell). So the survivor has
    to stay essentially one piece — or at least be no more broken up than it
    already was on sheets whose sprites genuinely have detached parts.
    """
    op = after > 0
    if op.mean() < p.cell_min_keep:
        return False
    share = _largest_share(op)
    return share >= p.cell_min_solid or share >= _largest_share(before > 0) - 0.05


def _cell_color(rgb, already_bg, rect, p):
    """The flat colour a box sits on and the share of its FULL border ring it
    owns, or (None, 0.0).

    Looks only at the box's border ring, ignoring pixels the sheet background
    already keys out. A colour has to dominate that ring (``cell_bg_frac``) to
    count — on a tight box the ring is mostly sprite, and no single colour gets
    close, so nothing is keyed.

    The share comes back measured over the WHOLE ring, sheet-background pixels
    included, because that is the part the dominance test above cannot see:
    where the sheet background reaches the box's own border, only a handful of
    ring pixels are left to vote and the sprite's outline can carry them. Such a
    "cell" is harmless to flood — it never touches the crop border, so the flood
    finds nothing — but keying every pixel of it would eat the outline, so the
    caller uses the share to tell a real frame from that accident.
    """
    ring = _ring_mask(rgb.shape[:2], rect, p.cell_ring_px)
    px = rgb[ring & ~already_bg]
    if len(px) < 8:
        return None, 0.0
    color, n = collections.Counter(map(tuple, px)).most_common(1)[0]
    if (n / len(px)) < p.cell_bg_frac:
        return None, 0.0
    return color, n / max(int(ring.sum()), 1)


def extract_box(arr, bg, box, p):
    """Extract one box to a clean RGBA PIL image.

    ``arr``  : (H, W, 4) uint8 sheet.
    ``bg``   : background dict from segmenter.infer_background.
    ``box``  : object/dict with x, y, w, h (sheet pixel coords).
    ``p``    : SegParams (uses tol, crop_pad).

    Returns (PIL.Image RGBA, info) where info = {orig_box, content_box, offset}.
    ``offset`` is the returned image's top-left relative to the box's top-left,
    and ``content_box`` the opaque content's bounds in sheet coordinates.

    By default the image keeps the operator's BOX as its canvas (``trim_content``
    off). That geometry is the animation: a jump frame sits high inside its cell
    and a landing frame sits low, so trimming each frame to its own content would
    glue every pose to the same baseline and flatten the jump — the baker anchors
    what it is handed and has no idea a frame was airborne.
    """
    H, W = arr.shape[:2]
    bx = box["x"] if isinstance(box, dict) else box.x
    by = box["y"] if isinstance(box, dict) else box.y
    bw = box["w"] if isinstance(box, dict) else box.w
    bh = box["h"] if isinstance(box, dict) else box.h

    pad = p.crop_pad
    x0 = _clamp(bx - pad, 0, W)
    y0 = _clamp(by - pad, 0, H)
    x1 = _clamp(bx + bw + pad, 0, W)
    y1 = _clamp(by + bh + pad, 0, H)
    sub = arr[y0:y1, x0:x1].copy()
    sh, sw = sub.shape[:2]

    rgb = sub[:, :, :3].astype(np.int32)
    # What the sheet itself already declares transparent. An alpha-keyed rip
    # brings its own mask and names no colours — but "has an alpha channel" does
    # NOT mean "is cut out": plenty of these sheets are transparent between the
    # cells and flat-coloured inside them, so the per-box cell keying below has
    # to run for them too, or every sprite keeps its cell (48582 and 49 more).
    #
    # Whether the sheet is cut out is read off the PIXELS, never off ``mode``: a
    # box cache written before alpha keying existed labels a 70%-transparent rip
    # "solid", and taking it at its word forces every clear pixel opaque and
    # hands the sprite back on a black slab.
    cut_out = S.sheet_uses_alpha(arr, p)
    if bg["mode"] == "alpha":
        sheet_alpha = sub[:, :, 3].copy()
        colors = []
    else:
        sheet_alpha = sub[:, :, 3].copy() if cut_out else np.full(sub.shape[:2], 255, np.uint8)
        colors = bg.get("colors") or ([bg["color"]] if bg.get("color") else [])
        # Never key the colour transparency reads as — see ``_phantom_color``.
        phantom = _phantom_color(arr, p) if cut_out else None
        if phantom is not None:
            colors = [c for c in colors
                      if np.linalg.norm(np.array(c, float) - phantom) > p.tol]

    def matching(cols, tol=None):
        """Pixels within ``tol`` (default ``p.tol``) of any of ``cols``."""
        like = np.zeros(sub.shape[:2], dtype=bool)
        radius = p.tol if tol is None else tol
        for c in cols:
            bgc = np.array(c, dtype=np.int32)
            like |= np.sqrt(np.sum((rgb - bgc) ** 2, axis=-1)) <= radius
        return like

    def flooded(like):
        """Only the regions of ``like`` CONNECTED to the crop border."""
        lbl, n = ndimage.label(like)
        border_labels = set()
        if n:
            border_labels.update(np.unique(lbl[0, :]))
            border_labels.update(np.unique(lbl[-1, :]))
            border_labels.update(np.unique(lbl[:, 0]))
            border_labels.update(np.unique(lbl[:, -1]))
            border_labels.discard(0)
        return np.isin(lbl, list(border_labels)) if border_labels else np.zeros_like(like)

    # Backdrop-coloured pixels wherever they sit, the ones the sprite walls in
    # included: the gap an arm or a tail encloses is backdrop and has to read
    # through. Only the SHEET colours qualify (they are what the operator
    # eyedropped in /crop), and only at ``bg_enclosed_tol`` — see the param.
    enclosed = (matching(colors, p.bg_enclosed_tol) if p.bg_enclosed
                else np.zeros(sub.shape[:2], dtype=bool))

    def keyed(cols, enc=None):
        """Alpha for a set of background colours: the border flood, plus every
        pixel of ``enc`` (defaults to the sheet-colour enclosed mask), over
        whatever the sheet's own alpha already keeps."""
        like = matching(cols)
        enc = enclosed if enc is None else enc
        return np.where(flooded(like) | enc, 0, sheet_alpha).astype(np.uint8), like

    alpha, bg_like = keyed(colors)
    if enclosed.any():
        # Same survival test the cell keying gets: drop the enclosed pixels
        # rather than hand back a shattered sprite. It catches the gross case
        # only — on a sheet keyed on a colour the artist DREW with (a black
        # key over black outlines), the holes are indistinguishable from the
        # art by any measure of mass or connectivity, and that sheet has to
        # opt out by hand (``bg_enclosed``). Clearing the mask here also
        # keeps it out of the cell candidate below, which closes over it.
        flood_only = np.where(flooded(bg_like), 0, sheet_alpha).astype(np.uint8)
        if not _keeps_sprite(_defringe(flood_only), _defringe(alpha), p):
            enclosed = np.zeros_like(enclosed)
            alpha = flood_only
    if p.auto_cell_bg:
        # The sheet background rarely reaches inside a cell box, so key the
        # cell's own colour as well. Kept only if a sprite actually survives:
        # if the "cell" turned out to be the sprite, we drop the whole idea.
        # Pixels the sheet's own alpha already drops are not part of the ring
        # vote — on an alpha-keyed rip they are the gap BETWEEN the cells, and
        # whatever RGB hides under them would otherwise elect itself "the cell".
        cell, ring_share = _cell_color(rgb, bg_like | (sheet_alpha == 0),
                                       (bx - x0, by - y0, bw, bh), p)
        if cell is not None:
            cols = list(colors) + [cell]
            # Two candidate keyings, best first.
            #
            # The cell is backdrop exactly as the sheet colour is, so where it
            # frames the box (``ring_share``) EVERY pixel of it goes, the ones
            # the sprite walls off included: the patch inside the curl of a
            # tail, between the legs, under an arm. Those are not connected to
            # the crop border, so a flood leaves them opaque — that is the
            # backdrop that used to survive inside finished sprites.
            #
            # Where it does not frame the box the colour is a bad guess rather
            # than a cell: the sheet background already reaches the box's
            # border, so the ring vote came down to a few sprite pixels and
            # elected the outline. Flooding it is a harmless no-op (it touches
            # nothing at the crop border) but keying every pixel of it would
            # dissolve the sprite, hence flood-only there — and no cell keying
            # at all if even that fails to keep a sprite.
            cands = []
            if p.bg_enclosed and ring_share >= p.cell_bg_frac:
                cands.append(enclosed | matching([cell], p.bg_enclosed_tol))
            cands.append(enclosed)
            # Judge the DE-FRINGED masks: the 1px erosion below is what turns a
            # thin bridge into a break, so comparing raw masks would wave through
            # a keying that only falls apart at the very last step.
            base = _defringe(alpha)
            for enc in cands:
                cand, _ = keyed(cols, enc)
                if _keeps_sprite(base, _defringe(cand), p):
                    alpha = cand
                    break

    # De-fringe: erode the opaque mask by 1px to shave the chroma-key rim.
    alpha = _defringe(alpha)

    out = sub.copy()
    out[:, :, 3] = alpha

    if p.trim_content:
        canvas, cvx, cvy = out, x0, y0            # bbox measured on the padded crop
    else:
        # Keep the operator's box as the canvas. The pad only ever existed to give
        # the border flood a ring of background to start from, so it is dropped
        # again here — every frame of a sheet comes back the same size, holding the
        # sprite exactly where it sits inside its cell.
        rx, ry = bx - x0, by - y0
        canvas, cvx, cvy = out[ry:ry + bh, rx:rx + bw], bx, by

    # Content bounds of whatever we are actually returning, in sheet coordinates.
    ys, xs = np.where(canvas[:, :, 3] > 0)
    empty = len(ys) == 0
    if empty:
        content_box = [bx, by, 0, 0]
    else:
        cy0, cy1 = int(ys.min()), int(ys.max()) + 1
        cx0, cx1 = int(xs.min()), int(xs.max()) + 1
        content_box = [cvx + cx0, cvy + cy0, cx1 - cx0, cy1 - cy0]

    if p.trim_content:
        if empty:
            # nothing survived — return a 1x1 transparent pixel
            img = Image.fromarray(np.zeros((1, 1, 4), np.uint8), "RGBA")
            return img, {"orig_box": [bx, by, bw, bh], "content_box": content_box,
                         "offset": [0, 0], "empty": True}
        img = Image.fromarray(canvas[cy0:cy1, cx0:cx1], "RGBA")
        off = [content_box[0] - bx, content_box[1] - by]
    else:
        img = Image.fromarray(canvas, "RGBA")
        off = [0, 0]

    return img, {"orig_box": [bx, by, bw, bh], "content_box": content_box,
                 "offset": off, "empty": empty}
