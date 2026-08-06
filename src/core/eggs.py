"""Bake the incubator egg strips the content-editor draws per attribute.

A pet hatches from an egg whose art is derived, not stored: the editor and the
firmware both resolve ``species/<species>/eggs/egg_<type>.png`` from the
creature's ``type``. For digimon that type is the Reference-Book attribute
(Data / Vaccine / Virus / Free), so the species needs exactly four strips.

Digimon World DS ships its eggs on one rip (sheet ``48315``, "Eggs"): an 18x6
grid of 32px cells over a teal grid, where each **group of three columns** is one
design's 3-frame pulse (the egg's shading swells and settles). The editor wants a
4-frame horizontal strip like the Pokémon ones (128x32), so a design is laid out
as frames 0-1-2-1: the pulse read forwards then back, which loops seamlessly.

Keying is a plain exact-colour match (the grid teal plus the cell's own flat
backdrop) rather than ``core.extractor``: these eggs have no anti-aliased rim,
and the extractor's de-fringe pass would eat their one-pixel black outline.
"""
import os
import shutil

import numpy as np
from PIL import Image

import config

SHEET_ID = "48315"

# Grid geometry of the rip: 32px cells, 2px teal rule between them, and an extra
# 2px gutter between one design (3 columns) and the next.
CELL = 32
_RULE = 2
_COL_PITCH = CELL + _RULE            # 34, within a design
_GROUP_PITCH = 3 * _COL_PITCH + _RULE  # 104, design to design
_ORIGIN = 2

# attribute -> (row, design) on the sheet. Colour-coded the way the Reference
# Book reads: Data blue, Vaccine green, Virus purple, Free multi-coloured.
DESIGNS = {
    "data": (3, 2),
    "vaccine": (2, 2),
    "virus": (5, 2),
    "free": (7, 4),
}

# Which of the design's 3 source frames each strip frame shows (pulse, then back).
FRAME_ORDER = (0, 1, 2, 1)


def _cell_rect(row, design, frame):
    """Pixel rect of one 32x32 cell: design ``design``, frame ``frame``, row ``row``."""
    x = _ORIGIN + design * _GROUP_PITCH + frame * _COL_PITCH
    y = _ORIGIN + row * _COL_PITCH
    return x, y


def _keyed(cell):
    """Return ``cell`` (H,W,4 uint8) with its flat backdrop turned transparent.

    Keys the exact colour of every pixel that owns the cell's border ring — the
    green (or grey/pink) backdrop the rip parks the egg on — so a design whose
    body shares the *family* of that hue keeps its own shades.
    """
    rgb = cell[..., :3]
    ring = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    colors, counts = np.unique(ring.reshape(-1, 3), axis=0, return_counts=True)
    mask = np.zeros(cell.shape[:2], dtype=bool)
    for color, count in zip(colors, counts):
        if count < 4:  # a stray outline pixel touching the edge, not a backdrop
            continue
        mask |= np.all(rgb == color, axis=2)
    out = cell.copy()
    out[..., 3] = np.where(mask, 0, 255)
    return out


def build_strip(sheet, attribute):
    """Compose one attribute's 128x32 four-frame egg strip from the rip."""
    row, design = DESIGNS[attribute]
    strip = Image.new("RGBA", (CELL * len(FRAME_ORDER), CELL), (0, 0, 0, 0))
    for slot, frame in enumerate(FRAME_ORDER):
        x, y = _cell_rect(row, design, frame)
        cell = np.array(sheet.crop((x, y, x + CELL, y + CELL)).convert("RGBA"))
        strip.paste(Image.fromarray(_keyed(cell)), (slot * CELL, 0))
    return strip


def bake_eggs(out_dir=None, raw_dir=None, stage=True, log=print):
    """Write ``eggs/egg_<attribute>.png`` for all four attributes.

    Bakes into ``output/digimon/eggs/`` and, unless ``stage`` is off, copies the
    strips into the content-editor's ``graphics/species/digimon/eggs/``.
    Returns the list of baked paths.
    """
    out_dir = out_dir or config.OUT_DIR
    raw_dir = raw_dir or config.RAW_DIR
    src = os.path.join(raw_dir, f"{SHEET_ID}.png")
    if not os.path.exists(src):
        raise FileNotFoundError(
            f"egg sheet {SHEET_ID}.png is not in {raw_dir} — "
            "run Scripts/download_sheets.py first")
    sheet = Image.open(src).convert("RGBA")
    egg_dir = os.path.join(out_dir, "eggs")
    os.makedirs(egg_dir, exist_ok=True)
    staged_dir = os.path.join(config.GRAPHICS_SPECIES, "eggs")
    if stage:
        os.makedirs(staged_dir, exist_ok=True)

    paths = []
    for attribute in sorted(DESIGNS):
        strip = build_strip(sheet, attribute)
        path = os.path.join(egg_dir, f"egg_{attribute}.png")
        strip.save(path)
        paths.append(path)
        if stage:
            shutil.copy2(path, os.path.join(staged_dir, f"egg_{attribute}.png"))
    log(f"baked {len(paths)} egg strips" + (f" → {staged_dir}" if stage else ""))
    return paths
