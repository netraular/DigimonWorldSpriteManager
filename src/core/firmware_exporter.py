"""Export existing Digimon bakes into the current firmware's asset tree."""
import copy
import json
import shutil
from pathlib import Path

from PIL import Image

from core.layout import CARDINALS, DIAGONALS, validate_layout


DEFAULT_ID_OFFSET = 1000
MAX_ROWS = 20
MAX_CLIP_NAME = 24
FRAME_CAPS = {"walk": 4, "idle": 4, "sleep": 8}


def _read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _cells(document, clip):
    table = document.get(clip, [] if clip == "sleep" else {})
    if clip == "sleep":
        return [("sleep", table)]
    return [(direction, table[direction])
            for direction in DIAGONALS + CARDINALS if direction in table]


def _validate_source(path):
    document = _read_json(path.with_suffix(".json"))
    node = _read_json(path.parent / f"pet_{path.stem}.json")
    errors = validate_layout(document)
    if errors:
        raise ValueError(f"{path.stem}: invalid layout: {'; '.join(errors)}")
    if document.get("sheet_split"):
        raise ValueError(f"{path.stem}: expected an unsplit source sheet")
    if document["rows"] > MAX_ROWS:
        raise ValueError(f"{path.stem}: exceeds firmware's {MAX_ROWS}-row limit")
    with Image.open(path) as image:
        image.load()
        width, height = image.size
    if width % document["cols"] or height % document["rows"]:
        raise ValueError(f"{path.stem}: sheet dimensions do not match its grid")
    for clip, cap in FRAME_CAPS.items():
        for direction, cells in _cells(document, clip):
            if len(cells) > cap:
                raise ValueError(f"{path.stem}: {clip}.{direction} exceeds {cap} frames")
            for cell in cells:
                if not (0 <= cell["col"] < document["cols"]
                        and 0 <= cell["row"] < document["rows"]):
                    raise ValueError(f"{path.stem}: {clip}.{direction} outside grid")
    if node["id"] != int(path.stem):
        raise ValueError(f"{path.stem}: creature node ID does not match sheet")
    if not document.get("idle", {}).get("down"):
        raise ValueError(f"{path.stem}: down idle frame required for portraits")
    return document, node


def _row_clips(document):
    names = [None] * document["rows"]
    columns = [0] * document["rows"]
    for clip in FRAME_CAPS:
        for direction, cells in _cells(document, clip):
            for cell in cells:
                row = cell["row"]
                columns[row] = max(columns[row], cell["col"] + 1)
                if names[row] is None:
                    name = (f"walk_{direction}" if clip == "walk" else clip)
                    if name in names:
                        name = f"r{row:02d}"
                    names[row] = name
    if names[0] is None:
        raise ValueError("row 0 must have a clip for the firmware's split probe")
    if any(name and len(name) >= MAX_CLIP_NAME for name in names):
        raise ValueError("clip name exceeds firmware limit")
    return names, columns


def _portrait(frame, size):
    bounds = frame.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("cannot create a portrait from an empty idle frame")
    body = frame.crop(bounds)
    scale = min(size / body.width, size / body.height)
    width = max(1, min(size, round(body.width * scale)))
    height = max(1, min(size, round(body.height * scale)))
    body = body.resize((width, height), Image.Resampling.NEAREST)
    bounds = body.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError("idle frame has no visible pixels after portrait scaling")
    body = body.crop(bounds)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.alpha_composite(body, ((size - body.width) // 2, size - body.height))
    return result


def export_assets(source_dir, destination, id_offset=DEFAULT_ID_OFFSET, *, legacy_shared=False):
    """Create a new package; never overwrite an existing directory or source."""
    source_dir = Path(source_dir).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if destination.is_relative_to(source_dir):
        raise ValueError("destination must be outside the source directory")
    if not isinstance(id_offset, int) or id_offset < 0:
        raise ValueError("ID offset must be a non-negative integer")
    sheets = sorted(path for path in source_dir.glob("*.png")
                    if path.stem.isdigit())
    if not sheets:
        raise ValueError(f"no baked sheets in {source_dir}")

    sources = []
    ids = set()
    for path in sheets:
        source_id = int(path.stem)
        asset_id = source_id + id_offset
        if not 152 <= asset_id <= 65535 or asset_id in ids:
            raise ValueError(f"{path.stem}: ID {asset_id} collides or is out of range")
        ids.add(asset_id)
        document, node = _validate_source(path)
        names, columns = _row_clips(document)
        sources.append((path, asset_id, document, node, names, columns))

    runtime_root = destination / "shared" if legacy_shared else destination
    graphics = runtime_root / "services/pet/assets/graphics/species/digimon"
    portraits = runtime_root / "services/creatures/assets/graphics/portraits"
    manifest = {
        "version": 1,
        "species": "digimon",
        "runtime_root": "shared" if legacy_shared else ".",
        "id_offset": id_offset,
        "catalogue_included": False,
        "notes": [
            "IDs are unique against the current Gen-1 roster, not reserved globally.",
            "Original node metadata is informational, not a firmware catalogue.",
            "Portraits are derived from idle sprites, not expression portraits.",
            "Missing sleep clips use the firmware's idle fallback.",
            "No new art is downloaded or generated; existing local pixels are reused.",
        ],
        "creatures": [],
    }
    graphics.mkdir(parents=True)
    max_clip_bytes = 0
    clip_count = 0
    for path, asset_id, document, node, names, columns in sources:
        stem = f"{asset_id:03d}"
        output_layout = copy.deepcopy(document)
        output_layout["sheet_split"] = "clips"
        output_layout["clip_files"] = names
        clip_dir = graphics / stem
        clip_dir.mkdir()
        with Image.open(path) as image:
            image = image.convert("RGBA")
            cell_w = image.width // document["cols"]
            cell_h = image.height // document["rows"]
            for row, name in enumerate(names):
                if name is None:
                    continue
                strip = image.crop((0, row * cell_h,
                                    columns[row] * cell_w, (row + 1) * cell_h))
                strip.save(clip_dir / f"{name}.png")
                max_clip_bytes = max(max_clip_bytes, strip.width * strip.height * 4)
                clip_count += 1
            idle = document["idle"]["down"][0]
            frame = image.crop((idle["col"] * cell_w, idle["row"] * cell_h,
                                (idle["col"] + 1) * cell_w,
                                (idle["row"] + 1) * cell_h))
            for kind, size in (("small", 32), ("full", 64)):
                directory = portraits / kind
                directory.mkdir(parents=True, exist_ok=True)
                _portrait(frame, size).save(directory / f"{stem}.png")
        shutil.copyfile(path, graphics / f"{stem}.png")
        _write_json(graphics / f"{stem}.json", output_layout)
        manifest["creatures"].append({
            "source_id": int(path.stem),
            "asset_id": asset_id,
            "name": node["name"],
            "sprite_rel": f"graphics/species/digimon/{stem}.png",
            "portrait_small": f"assets/graphics/portraits/small/{stem}.png",
            "portrait_full": f"assets/graphics/portraits/full/{stem}.png",
            "source_metadata": node,
        })
    egg_dir = source_dir / "eggs"
    eggs = sorted(egg_dir.glob("egg_*.png"))
    if eggs:
        output_eggs = graphics / "eggs"
        output_eggs.mkdir()
        for egg in eggs:
            shutil.copyfile(egg, output_eggs / egg.name)
    manifest["summary"] = {
        "creatures": len(sources),
        "clips": clip_count,
        "portraits": len(sources) * 2,
        "eggs": len(eggs),
        "max_clip_argb8888_bytes": max_clip_bytes,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest
