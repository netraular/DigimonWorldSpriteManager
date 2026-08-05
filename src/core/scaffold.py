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
