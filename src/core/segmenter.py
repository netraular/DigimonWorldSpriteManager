"""Sprite-sheet auto-segmentation.

Given an arbitrary 2D sprite sheet (Digimon World DS rips: solid green / teal /
magenta background, or already-transparent), infer the background, build a
foreground mask, find connected components, filter + merge them into bounding
boxes, and sort the boxes in reading order. Optionally detect a regular grid.

Pipeline: load RGBA → infer_background → foreground_mask → detect_boxes
          (label → filter → merge → row-sort → grid-detect).

Pure numpy + scipy.ndimage; Pillow only for I/O (in load_rgba). No Flask.
"""
from dataclasses import dataclass, asdict, field

import numpy as np
from PIL import Image
from scipy import ndimage

# Canonical chroma-key backgrounds sprite rippers commonly use; the inferred
# modal background snaps to the nearest of these when close enough.
_CANONICAL = [
    (0, 255, 0),      # green screen
    (255, 0, 255),    # magenta
    (0, 182, 176),    # DWDS teal (observed)
    (0, 128, 128),    # teal variant (observed)
    (128, 3, 175),    # DWDS purple (observed)
]

# Share of a sheet that has to be fully clear before its alpha channel counts as
# a cut-out rather than a handful of stray soft pixels.
ALPHA_SHEET_FRAC = 0.05


@dataclass
class SegParams:
    alpha_thresh: int = 16          # opaque cutoff for pre-transparent input
    border_frac: float = 0.02       # bg sampling ring width fraction
    snap_dist: int = 40             # snap modal bg to a canonical color within this
    tol: int = 40                   # RGB Euclidean fg/bg cutoff
    use_hsv: bool = True            # chroma-key (hue) masking for saturated bg
    hue_tol: float = 0.05           # hue distance (0..1) counted as background
    sat_min: float = 0.30           # min saturation for a pixel to be "the bg hue"
    val_min: float = 0.15           # min value for a pixel to be "the bg hue"
    morph_px: int = 1               # opening/closing structuring element radius
    dilate_px: int = 1              # pre-label limb joining (0 disables)
    connectivity: int = 8           # 8 (default) or 4
    area_min: int = 20              # speck filter (filled px in ORIGINAL mask)
    wh_min: int = 4                 # min box side
    fill_min: float = 0.04          # drop grid lines / text (filled / bbox area)
    merge_gap: int = 2              # merge boxes whose inflated rects touch
    row_tol_frac: float = 0.5       # row-band tolerance = frac * median box height
    grid_spread: float = 0.18       # IQR/median cutoff: grid vs packed
    max_box_frac: float = 0.9       # flag/keep boxes up to this * sheet dim
    crop_pad: int = 2               # extraction padding (used by extractor)
    # secondary background: many rips place each sprite on a neutral "cell"
    # rectangle over the outer chroma key (e.g. gray cells on teal). Auto-detect
    # a flat, low-saturation, high-coverage colour and treat it as background too.
    auto_secondary_bg: bool = True
    sec_bg_frac: float = 0.10       # min image coverage to consider a 2nd bg
    sec_bg_sat: int = 46            # max (maxch-minch) to count a colour as neutral
    max_bg_colors: int = 3          # cap on total background colours
    extra_bg: list = field(default_factory=list)  # explicit (r,g,b) bg colours (eyedropper)
    # Explicit-only keying: key out ONLY the eyedropped colours — no auto-inferred
    # border background, no canonical snapping, no secondary. Whatever survives is
    # boxed as-is, so each sprite keeps its full coloured "cell" box; the sheet is
    # split purely by the general background colour(s) the operator picked.
    explicit_bg_only: bool = False
    # Uniform grid split: instead of tight per-component boxes, separate the
    # foreground by background gaps (empty rows, then empty columns within each
    # row) and emit one EQUALLY-SIZED, margined box per sprite (a "cell"). Every
    # cell is the size of the largest sprite's content bbox plus ``cell_margin`` on
    # each side, centred on the sprite — so each sprite lands in its own same-size
    # zone with breathing room. Meant to run over a selected work area.
    uniform_cells: bool = False
    cell_margin: int = 2            # breathing room (px) around the largest sprite
    # Per-box cell keying (extractor): a sprite's box is usually its flat "cell"
    # rectangle, whose colour is not part of the sheet background. Key whatever
    # flat colour dominates the box's own border ring so the sprite comes out
    # transparent without eyedropping every cell shade by hand.
    auto_cell_bg: bool = True
    # Key EVERY pixel of a background colour, not just the regions touching the
    # crop border: the gap an arm or a tail encloses is backdrop too, and a
    # border flood leaves it opaque. Turn off to keep the old flood (a sprite
    # whose own art uses the backdrop colour then keeps its interior intact).
    bg_enclosed: bool = True
    # Radius for that INTERIOR match, and it is deliberately not ``tol``. The
    # silhouette can afford a wide radius (a flood only ever spreads through
    # connected backdrop), but inside the sprite that same radius swallows the
    # art: a teal Digimon on a teal key is well within 40. 0 = the exact same
    # colour, which is what a flat DS rip's enclosed backdrop actually is.
    bg_enclosed_tol: int = 0
    cell_ring_px: int = 1           # width of the border ring sampled for the cell
    cell_bg_frac: float = 0.6       # share of that ring a colour must own
    cell_min_keep: float = 0.02     # abort if keying it leaves less than this opaque
    cell_min_solid: float = 0.8      # …or if it shatters the sprite into crumbs
    # Keep the operator's box as the extracted canvas instead of shrink-wrapping
    # each frame to its own pixels: the sprite's place INSIDE its cell is the
    # animation (a jump frame rides high), and trimming would flatten it.
    trim_content: bool = False

    @classmethod
    def merged(cls, overrides):
        """Fresh params with a dict of overrides applied (unknown keys ignored)."""
        p = cls()
        for k, v in (overrides or {}).items():
            if not hasattr(p, k) or v is None:
                continue
            cur = getattr(p, k)
            if isinstance(cur, list):
                setattr(p, k, list(v))
            elif isinstance(cur, bool):
                setattr(p, k, bool(v))
            else:
                setattr(p, k, type(cur)(v))
        return p


@dataclass
class Box:
    id: str
    x: int
    y: int
    w: int
    h: int
    area: int
    fill: float
    row_band: int = -1
    empty: bool = False

    def as_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------
# I/O + background
# --------------------------------------------------------------------------
def load_rgba(path):
    """Open an image as an (H, W, 4) uint8 RGBA numpy array."""
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def _snap(color, p):
    """Snap an (r,g,b) to the nearest canonical bg within snap_dist, else keep."""
    c = np.array(color, dtype=np.int32)
    best, bd = color, p.snap_dist + 1
    for cand in _CANONICAL:
        d = float(np.sqrt(np.sum((c - np.array(cand)) ** 2)))
        if d < bd:
            best, bd = cand, d
    return tuple(int(v) for v in best)


def _modal_color(rgb):
    """Return (color, frac) for the dominant quantized color of an (N,3) array."""
    q = (rgb >> 3)
    keys = q[:, 0] * 4096 + q[:, 1] * 64 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    i = counts.argmax()
    mask = keys == vals[i]
    color = tuple(int(v) for v in np.round(rgb[mask].mean(axis=0)))
    return color, counts[i] / len(keys)


def _explicit_colors(p):
    """Normalise ``extra_bg`` into a flat list of (r,g,b) tuples.

    Accepts a single flat triple ``[r,g,b]`` or a list of triples; the operator's
    eyedropper picks (background and any stubborn cell colours) feed in here.
    """
    src = p.extra_bg
    if not src:
        return []
    seq = src if isinstance(src[0], (list, tuple)) else [src]
    out = []
    for c in seq:
        try:
            out.append(tuple(int(v) for v in c[:3]))
        except (TypeError, ValueError):
            continue
    return out


def sheet_uses_alpha(arr, p):
    """Is this sheet genuinely CUT OUT, rather than merely carrying an alpha channel?

    A stray semi-transparent pixel or two is not a cut-out; a rip whose sprites
    sit on transparency has a large share of the sheet fully clear. The same
    test decides ``infer_background``'s alpha mode and, in ``extractor``,
    whether the sheet's own alpha is a fact to be honoured whatever a stored
    background payload claims.
    """
    alpha = arr[:, :, 3]
    return bool(alpha.min() < 250) and float((alpha < p.alpha_thresh).mean()) > ALPHA_SHEET_FRAC


def infer_background(arr, p):
    """Infer the sheet background colour(s).

    Returns {'mode': 'alpha'|'solid', 'colors': [(r,g,b), …], 'color': first,
    'has_alpha': bool}. Alpha mode wins when the image genuinely uses
    transparency; otherwise the border modal colour is the primary background,
    plus (optionally) a secondary flat/neutral colour that covers a large area —
    the "cell" rectangles many rips place behind each sprite — plus any explicit
    ``extra_bg`` colours supplied by the operator (eyedropper). Explicit colours
    are honoured in alpha mode too, so an eyedropped colour still keys out on an
    already-transparent sheet.
    """
    explicit = _explicit_colors(p)
    has_alpha = bool(arr[:, :, 3].min() < 250)
    cut_out = sheet_uses_alpha(arr, p)

    # Explicit-only: the operator's picks ARE the background, nothing else — but
    # transparency is background whatever anyone picks. On a genuinely transparent
    # sheet, dropping alpha to key a colour instead leaves every anti-aliased
    # fringe pixel (transparent, but not the picked RGB) standing as foreground,
    # and those faint pixels bridge sprites that the eye sees clearly apart, so the
    # components come out fused. Alpha mode keys the picks on top of transparency.
    if p.explicit_bg_only:
        if cut_out:
            return {"mode": "alpha", "colors": explicit,
                    "color": explicit[0] if explicit else None, "has_alpha": True}
        return {"mode": "solid", "colors": explicit,
                "color": explicit[0] if explicit else None, "has_alpha": has_alpha}

    if cut_out:
        return {"mode": "alpha", "colors": explicit,
                "color": explicit[0] if explicit else None, "has_alpha": True}

    h, w = arr.shape[:2]
    ring = max(2, round(p.border_frac * min(h, w)))
    border = np.concatenate([
        arr[:ring].reshape(-1, 4), arr[-ring:].reshape(-1, 4),
        arr[:, :ring].reshape(-1, 4), arr[:, -ring:].reshape(-1, 4),
    ])[:, :3].astype(np.int32)
    primary, frac = _modal_color(border)
    primary = _snap(primary, p)
    colors = [primary]

    if p.auto_secondary_bg:
        # Look at pixels that are NOT the primary bg; the most common flat,
        # neutral (low-saturation) colour covering a big share is a 2nd bg.
        full = arr[:, :, :3].reshape(-1, 3).astype(np.int32)
        prim = np.array(primary)
        not_prim = np.sqrt(np.sum((full - prim) ** 2, axis=-1)) > p.tol
        rest = full[not_prim]
        if len(rest) > 0:
            cand, cfrac = _modal_color(rest)
            cov = cfrac * (len(rest) / len(full))  # coverage of the whole image
            neutral = (max(cand) - min(cand)) <= p.sec_bg_sat
            if cov >= p.sec_bg_frac and neutral and len(colors) < p.max_bg_colors:
                colors.append(_snap(cand, p))

    for c in explicit:
        if c not in colors and len(colors) < p.max_bg_colors:
            colors.append(c)

    return {"mode": "solid", "colors": colors, "color": colors[0],
            "has_alpha": has_alpha, "border_frac": round(float(frac), 3)}


def _rgb_to_hsv(rgb):
    """Vectorized RGB→HSV for an (N,3) or (H,W,3) uint8 array. Returns floats 0..1."""
    a = rgb.astype(np.float32) / 255.0
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = np.max(a, axis=-1)
    mn = np.min(a, axis=-1)
    df = mx - mn
    h = np.zeros_like(mx)
    nz = df > 1e-6
    # hue per dominant channel
    rm = nz & (mx == r)
    gm = nz & (mx == g)
    bm = nz & (mx == b)
    h[rm] = ((g[rm] - b[rm]) / df[rm]) % 6
    h[gm] = ((b[gm] - r[gm]) / df[gm]) + 2
    h[bm] = ((r[bm] - g[bm]) / df[bm]) + 4
    h = h / 6.0
    s = np.where(mx > 1e-6, df / np.maximum(mx, 1e-6), 0.0)
    v = mx
    return np.stack([h, s, v], axis=-1)


def _bg_colors(bg):
    """Normalize a bg dict to a list of (r,g,b) colours."""
    if bg.get("colors"):
        return [tuple(c) for c in bg["colors"]]
    return [tuple(bg["color"])] if bg.get("color") else []


def foreground_mask(arr, bg, p):
    """Boolean (H, W) mask: True = sprite pixel, False = any background colour."""
    if bg["mode"] == "alpha":
        mask = arr[:, :, 3] > p.alpha_thresh
        # Honour explicit picks even on transparent sheets: a cell colour picked
        # by the operator is keyed out of the opaque foreground too.
        if bg.get("colors"):
            rgb = arr[:, :, :3].astype(np.int32)
            for c in _bg_colors(bg):
                bgc = np.array(c, dtype=np.int32)
                mask &= np.sqrt(np.sum((rgb - bgc) ** 2, axis=-1)) > p.tol
    else:
        cols = _bg_colors(bg)
        if not cols:
            # Nothing picked yet on a solid sheet — nothing to separate.
            return np.zeros(arr.shape[:2], dtype=bool)
        rgb = arr[:, :, :3].astype(np.int32)
        is_bg = np.zeros(arr.shape[:2], dtype=bool)
        hsv = _rgb_to_hsv(arr[:, :, :3]) if p.use_hsv else None
        for c in cols:
            bgc = np.array(c, dtype=np.int32)
            dist = np.sqrt(np.sum((rgb - bgc) ** 2, axis=-1))
            near = dist <= p.tol
            # For a saturated chroma-key colour, also cut anti-aliased fringe by hue.
            bg_hsv = _rgb_to_hsv(bgc.reshape(1, 3))[0]
            if hsv is not None and bg_hsv[1] >= p.sat_min:
                dh = np.abs(hsv[..., 0] - bg_hsv[0])
                dh = np.minimum(dh, 1.0 - dh)
                near = near | ((dh < p.hue_tol) & (hsv[..., 1] > p.sat_min) & (hsv[..., 2] > p.val_min))
            is_bg |= near
        mask = ~is_bg
    # Clean speckle: opening removes lone pixels, closing fills 1px holes.
    if p.morph_px > 0:
        st = ndimage.generate_binary_structure(2, 2)
        mask = ndimage.binary_opening(mask, structure=st, iterations=p.morph_px)
        mask = ndimage.binary_closing(mask, structure=st, iterations=p.morph_px)
    return mask


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def _merge_boxes(rects, gap):
    """Union-find merge of rects whose ``gap``-inflated bounds overlap.

    ``rects`` = list of (x, y, w, h). Iterates to a fixed point. Returns merged
    list of (x, y, w, h).
    """
    def overlap(a, b):
        ax0, ay0, ax1, ay1 = a[0] - gap, a[1] - gap, a[0] + a[2] + gap, a[1] + a[3] + gap
        bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

    boxes = list(rects)
    changed = True
    while changed:
        changed = False
        out = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            x0, y0 = boxes[i][0], boxes[i][1]
            x1, y1 = x0 + boxes[i][2], y0 + boxes[i][3]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                if overlap((x0, y0, x1 - x0, y1 - y0), boxes[j]):
                    x0 = min(x0, boxes[j][0]); y0 = min(y0, boxes[j][1])
                    x1 = max(x1, boxes[j][0] + boxes[j][2])
                    y1 = max(y1, boxes[j][1] + boxes[j][3])
                    used[j] = True
                    changed = True
            out.append((x0, y0, x1 - x0, y1 - y0))
            used[i] = True
        boxes = out
    return boxes


def _row_sort(rects, row_tol):
    """Cluster rects into horizontal bands (reading order), tag with row_band.

    A box joins a band only if its center-y is within ``row_tol`` of the band's
    *anchor* (the center-y of the band's first, top-most member). The anchor is
    frozen, so a band never grows unbounded and chain-merges whole columns —
    the failure mode of expanding-window banding on densely packed sheets.

    Returns list of (rect, row_band) sorted top-to-bottom then left-to-right.
    """
    if not rects:
        return []
    items = sorted(rects, key=lambda r: (r[1] + r[3] / 2.0))  # by center-y
    bands = []  # each: {anchor_cy, items:[]}
    for r in items:
        cy = r[1] + r[3] / 2.0
        placed = False
        for band in bands:
            if abs(cy - band["anchor_cy"]) <= row_tol:
                band["items"].append(r)
                placed = True
                break
        if not placed:
            bands.append({"anchor_cy": cy, "items": [r]})
    bands.sort(key=lambda b: b["anchor_cy"])
    out = []
    for bi, band in enumerate(bands):
        for r in sorted(band["items"], key=lambda r: r[0]):
            out.append((r, bi))
    return out


def detect_grid(rects, W, H, p):
    """Report whether the boxes form a regular grid (low size spread)."""
    if len(rects) < 4:
        return {"detected": False}
    ws = np.array([r[2] for r in rects], dtype=np.float32)
    hs = np.array([r[3] for r in rects], dtype=np.float32)

    def spread(v):
        med = np.median(v)
        if med <= 0:
            return 1.0
        iqr = np.percentile(v, 75) - np.percentile(v, 25)
        return float(iqr / med)

    if spread(ws) > p.grid_spread or spread(hs) > p.grid_spread:
        return {"detected": False}
    xs = np.array(sorted({r[0] for r in rects}))
    ys = np.array(sorted({r[1] for r in rects}))
    pitch_x = int(np.median(np.diff(xs))) if len(xs) > 1 else int(np.median(ws))
    pitch_y = int(np.median(np.diff(ys))) if len(ys) > 1 else int(np.median(hs))
    return {"detected": True, "pitch_x": max(1, pitch_x), "pitch_y": max(1, pitch_y),
            "origin_x": int(xs.min()), "origin_y": int(ys.min())}


def _runs(flags):
    """Return [(start, stop), …] index runs where a boolean 1-D array is True."""
    spans = []
    n = len(flags)
    i = 0
    while i < n:
        if flags[i]:
            j = i + 1
            while j < n and flags[j]:
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def split_uniform_cells(mask, margin, min_side):
    """Separate the foreground in ``mask`` into uniform, margined grid cells.

    Rows are found from horizontal background gaps (rows with no foreground);
    within each row, columns from vertical gaps — so a sprite's detached limbs
    (same row+column band) stay together while neighbours (separated by a clear
    background row/column) split apart. Each occupied cell's tight content bbox is
    measured, then every sprite is emitted as a box the size of the LARGEST
    content bbox + ``margin`` on each side, centred on its own content. Result:
    one equally-sized zone per sprite, each with breathing room.

    Returns a list of (x, y, w, h) in ``mask`` coordinates.
    """
    H, W = mask.shape
    if not mask.any():
        return []
    content = []  # tight content bboxes (x, y, w, h)
    for (r0, r1) in _runs(mask.any(axis=1)):
        band = mask[r0:r1, :]
        for (c0, c1) in _runs(band.any(axis=0)):
            cell = band[:, c0:c1]
            ys, xs = np.where(cell)
            if len(ys) == 0:
                continue
            cx0 = c0 + int(xs.min())
            cy0 = r0 + int(ys.min())
            cw = int(xs.max()) - int(xs.min()) + 1
            ch = int(ys.max()) - int(ys.min()) + 1
            if cw < min_side or ch < min_side:
                continue
            content.append((cx0, cy0, cw, ch))
    if not content:
        return []
    cell_w = min(W, max(c[2] for c in content) + 2 * margin)
    cell_h = min(H, max(c[3] for c in content) + 2 * margin)
    boxes = []
    for (cx0, cy0, cw, ch) in content:
        ccx = cx0 + cw / 2.0
        ccy = cy0 + ch / 2.0
        bx = int(round(ccx - cell_w / 2.0))
        by = int(round(ccy - cell_h / 2.0))
        bx = max(0, min(bx, W - cell_w))
        by = max(0, min(by, H - cell_h))
        boxes.append((bx, by, int(cell_w), int(cell_h)))
    return boxes


def detect_boxes(arr, p=None):
    """Full segmentation. Returns (list[Box] in reading order, meta dict)."""
    p = p or SegParams()
    H, W = arr.shape[:2]
    bg = infer_background(arr, p)
    mask = foreground_mask(arr, bg, p)

    if p.uniform_cells:
        # Grid split: uniform, margined cells by background gaps (no components,
        # no merge, no size/fill filters — the gap structure IS the separation).
        rects = split_uniform_cells(mask, p.cell_margin, max(1, p.wh_min))
    else:
        # Dilation only groups disconnected limbs for labelling; areas/bboxes are
        # computed from the ORIGINAL mask so a dilated halo never inflates them.
        label_src = mask
        if p.dilate_px > 0:
            st = ndimage.generate_binary_structure(2, 2)
            label_src = ndimage.binary_dilation(mask, structure=st, iterations=p.dilate_px)

        structure = np.ones((3, 3), int) if p.connectivity == 8 else None
        labels, n = ndimage.label(label_src, structure=structure)
        slices = ndimage.find_objects(labels)

        max_w = p.max_box_frac * W
        max_h = p.max_box_frac * H
        rects = []
        for sl in slices:
            if sl is None:
                continue
            ys, xs = sl
            x0, y0 = xs.start, ys.start
            w, h = xs.stop - xs.start, ys.stop - ys.start
            if w < p.wh_min or h < p.wh_min:
                continue
            if w > max_w or h > max_h:
                continue
            filled = int(mask[sl].sum())  # original mask, not dilated
            if filled < p.area_min:
                continue
            if filled / float(w * h) < p.fill_min:
                continue
            rects.append((x0, y0, w, h))

        if p.merge_gap > 0:
            rects = _merge_boxes(rects, p.merge_gap)

    med_h = float(np.median([r[3] for r in rects])) if rects else 0.0
    row_tol = max(2.0, p.row_tol_frac * med_h)
    ordered = _row_sort(rects, row_tol)

    boxes = []
    for i, (r, band) in enumerate(ordered):
        x0, y0, w, h = r
        filled = int(mask[y0:y0 + h, x0:x0 + w].sum())
        boxes.append(Box(id=f"b{i}", x=int(x0), y=int(y0), w=int(w), h=int(h),
                         area=filled, fill=round(filled / float(w * h), 3),
                         row_band=int(band)))

    meta = {
        "image": {"w": int(W), "h": int(H)},
        "background": bg,
        "grid": detect_grid([(b.x, b.y, b.w, b.h) for b in boxes], W, H, p),
        "params": asdict(p),
    }
    return boxes, meta
