"""Write hibitomo species/creature JSON and stage bakes into the content-editor.

Mirrors the on-disk shapes the content-editor expects:
  data/<species>/_species.json      SpeciesConfigSchema
  data/<species>/pet_<NNN>.json      CreatureSchema
  graphics/species/<species>/<NNN>.png + <NNN>.json   sprite + SpriteLayout
"""
import json
import os
import shutil

import config
from core import downloader

# Digimon World DS lines run deeper than Pokémon (rookie→champion→ultimate→…).
# Default to a 6-phase cadence across the 7-day lifespan; editable afterwards.
_DIGIMON_PHASES = [
    {"day": 1, "label": "Fresh"},
    {"day": 2, "label": "In-Training"},
    {"day": 3, "label": "Rookie"},
    {"day": 4, "label": "Champion"},
    {"day": 5, "label": "Ultimate"},
    {"day": 6, "label": "Mega"},
]


def ensure_species(species="digimon", data_dir=None):
    """Create ``data/<species>/_species.json`` if absent. Returns its path."""
    data_dir = data_dir or config.DATA_SPECIES
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "_species.json")
    if not os.path.exists(path):
        cfg = {"species": species, "phases": _DIGIMON_PHASES}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
    return path


def write_creature(creature, species="digimon", out_dir=None):
    """Write a ``pet_<NNN>.json`` CreatureSchema node. Returns its path.

    ``creature`` = dict with id, name, type, color, stage, and optional
    evolutions. ``sprite`` and ``species`` are filled in if missing.
    """
    out_dir = out_dir or config.OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    cid = int(creature["id"])
    node = {
        "id": cid,
        "name": creature.get("name", f"Digimon {cid:03d}"),
        "sprite": creature.get("sprite", f"{species}/{cid:03d}.png"),
        "type": creature.get("type", "Data"),
        "color": creature.get("color", "0x8899AA"),
        "stage": int(creature.get("stage", 1)),
        "species": species,
        "evolutions": creature.get("evolutions", []),
    }
    path = os.path.join(out_dir, f"pet_{cid:03d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(node, f, indent=2)
        f.write("\n")
    return path


def _read_json(path):
    """Load a JSON file, or return ``None`` when it is absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def apply_gallery_names(force=False, spec_dir=None, log=print):
    """Fill each spec's ``creature_name`` from the gallery's sheet title.

    The rips are anonymous PNGs; the only record of *which* Digimon a sheet is,
    is the title The Spriters Resource gives it. The name is written into the
    spec (the committed, editable source) rather than into the baked node, so a
    re-bake keeps it. Already-named specs are left alone unless ``force``.

    Returns ``(named, unnamed)`` counts.
    """
    spec_dir = spec_dir or config.SPEC_DIR
    names = downloader.sheet_names()
    if not names:
        log("no gallery.html in raw_sheets/ — run Scripts/download_sheets.py first")
        return 0, 0
    named = unnamed = 0
    for fname in sorted(os.listdir(spec_dir)):
        if not fname.endswith(".extract.json"):
            continue
        path = os.path.join(spec_dir, fname)
        spec = _read_json(path)
        if spec is None:
            continue
        title = names.get(str(spec.get("sheet_id")))
        if not title:
            unnamed += 1
            continue
        if spec.get("creature_name") and not force:
            continue
        if spec.get("creature_name") == title:
            continue
        spec["creature_name"] = title
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        named += 1
    log(f"named {named} specs; {unnamed} sheets have no gallery title")
    return named, unnamed


def creature_from_spec(spec, existing=None, species="digimon"):
    """Build a creature node from a spec's ``creature_*`` fields.

    ``existing`` (a previously written node) wins for the authored fields the
    spec has no better answer for — type, color, stage and the whole evolution
    graph — so re-running never flattens work done in the content-editor's
    codex. The name follows the spec, which is where the gallery title lands.
    """
    cid = int(spec["id"])
    prev = existing or {}
    name = (spec.get("creature_name") or "").strip()
    if not name:
        name = prev.get("name") or f"Digimon {cid:03d}"
    return {
        "id": cid,
        "name": name,
        "sprite": f"{species}/{cid:03d}.png",
        "type": prev.get("type") or spec.get("creature_type") or "Data",
        "color": prev.get("color") or spec.get("creature_color") or "0x8899AA",
        "stage": int(prev.get("stage") or spec.get("creature_stage") or 1),
        "evolutions": prev.get("evolutions", []),
    }


def sync_roster(out_dir=None, spec_dir=None, species="digimon", stage=True, log=print):
    """Give every baked sheet a creature node, then (optionally) stage it.

    A sheet baked from the bulk assembler or from the animate view without the
    creature form filled in has art but no node, so the codex cannot show it.
    This walks the baked PNGs, writes the missing/refreshed nodes from their
    specs and copies sheet + layout + node into the content-editor tree.

    Returns ``(nodes, staged, orphans)`` — orphans being baked PNGs with no spec.
    """
    out_dir = out_dir or config.OUT_DIR
    spec_dir = spec_dir or config.SPEC_DIR
    nodes = staged = 0
    orphans = []
    if stage:
        ensure_species(species)
    for fname in sorted(os.listdir(out_dir)):
        if not fname.endswith(".png") or not fname[:-4].isdigit():
            continue
        sid3 = fname[:-4]
        spec = _read_json(os.path.join(spec_dir, f"{sid3}.extract.json"))
        if spec is None:
            orphans.append(sid3)
            continue
        existing = _read_json(os.path.join(out_dir, f"pet_{sid3}.json"))
        write_creature(creature_from_spec(spec, existing, species),
                       species=species, out_dir=out_dir)
        nodes += 1
        if stage:
            stage_to_content(sid3, out_dir=out_dir, species=species)
            staged += 1
    log(f"{nodes} creature nodes; {staged} staged into {config.CONTENT_ROOT}")
    if orphans:
        log(f"  ! {len(orphans)} baked sheets have no spec: {', '.join(orphans)}")
    return nodes, staged, orphans


def stage_to_content(sid, out_dir=None, species="digimon", write_creature_node=True):
    """Copy baked ``<NNN>.png`` + ``<NNN>.json`` (+ creature) into the editor tree.

    Returns a dict of destination paths. Ensures ``_species.json`` exists.
    """
    out_dir = out_dir or config.OUT_DIR
    sid3 = "%03d" % int(sid)
    os.makedirs(config.GRAPHICS_SPECIES, exist_ok=True)
    os.makedirs(config.DATA_SPECIES, exist_ok=True)
    ensure_species(species)

    dst = {}
    for ext in ("png", "json"):
        src = os.path.join(out_dir, f"{sid3}.{ext}")
        if os.path.exists(src):
            d = os.path.join(config.GRAPHICS_SPECIES, f"{sid3}.{ext}")
            shutil.copy2(src, d)
            dst[ext] = d
    if write_creature_node:
        src = os.path.join(out_dir, f"pet_{sid3}.json")
        if os.path.exists(src):
            d = os.path.join(config.DATA_SPECIES, f"pet_{sid3}.json")
            shutil.copy2(src, d)
            dst["creature"] = d
    return dst
