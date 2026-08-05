"""Build + serialize the hibitomo explicit ``SpriteLayout`` JSON.

Mirrors ``packages/schema/src/pets.ts`` (SpriteLayoutSchema, ``.strict()``):
only schema keys are emitted, the four cardinals are always present in ``walk``,
cells are ``{col,row}``, and serialization is compact (one cell object per array
entry on a single line) with 2-space indent + trailing newline, matching the
hand-authored ``pokemon/001.json``.
"""
import json

# Direction order + which are cardinal vs diagonal (verbatim from pets.ts).
CARDINALS = ["down", "left", "right", "up"]
DIAGONALS = ["down_right", "up_right", "up_left", "down_left"]
ALL_DIRS = CARDINALS + DIAGONALS


def build_layout(cols, rows, walk, idle=None, sleep=None, *,
                 diagonals=False, walk_style="stride", tick_ms=33,
                 walk_durations=None, idle_durations=None, sleep_durations=None,
                 idle_frame_ms=None, sleep_frame_ms=None, description=None):
    """Assemble a SpriteLayout dict.

    ``walk`` / ``idle`` are dicts direction -> list[{col,row}]. Cardinals are
    required in ``walk``. Diagonal keys are emitted only when ``diagonals`` is
    True and present. ``idle`` defaults each direction to its first walk cell.
    (The digimon baker passes the SAME cells for every direction: idle is one
    non-directional clip there, since the pet faces the camera while standing.)
    """
    dirs = ALL_DIRS if diagonals else CARDINALS
    layout = {"style": "explicit", "cols": int(cols), "rows": int(rows),
              "walk_style": walk_style, "tick_ms": int(tick_ms)}
    if idle_frame_ms is not None:
        layout["idle_frame_ms"] = int(idle_frame_ms)
    if sleep and sleep_frame_ms is not None:
        layout["sleep_frame_ms"] = int(sleep_frame_ms)

    def cells(d):
        return [{"col": int(c["col"]), "row": int(c["row"])} for c in d]

    layout["walk"] = {}
    for dr in dirs:
        if dr in walk and walk[dr]:
            layout["walk"][dr] = cells(walk[dr])
        elif dr in CARDINALS:
            raise ValueError(f"walk.{dr} is required but empty")

    idle = idle or {}
    layout["idle"] = {}
    for dr in dirs:
        if dr in idle and idle[dr]:
            layout["idle"][dr] = cells(idle[dr])
        elif dr in layout["walk"]:
            layout["idle"][dr] = [layout["walk"][dr][0]]

    if sleep:
        layout["sleep"] = cells(sleep)

    # Per-frame durations are indexed by frame N and shared across directions, so
    # they must match the walk/idle frame count. Auto-fit (pad with the last
    # value, or truncate) so a single typed value expands to every frame.
    def _fit(durs, n):
        durs = [int(x) for x in durs]
        if not n:
            return durs
        if len(durs) < n:
            durs = durs + [durs[-1]] * (n - len(durs))
        return durs[:n]

    if walk_durations:
        n = max((len(v) for v in layout["walk"].values()), default=0)
        layout["walk_durations"] = _fit(walk_durations, n)
    if idle_durations:
        n = max((len(v) for v in layout["idle"].values()), default=0)
        layout["idle_durations"] = _fit(idle_durations, n)
    if sleep and sleep_durations:
        layout["sleep_durations"] = _fit(sleep_durations, len(layout["sleep"]))
    if description:
        layout["description"] = description
    return layout


def _cell_line(cell):
    return '{ "col": %d, "row": %d }' % (cell["col"], cell["row"])


def dumps_layout(layout):
    """Serialize compactly: each cell array on one line, matching 001.json."""
    # We hand-render the walk/idle/sleep cell arrays so each cell is inline and
    # the whole direction sits on a single line, then json-dump the scalars.
    lines = ["{"]
    scalar_order = ["style", "cols", "rows", "walk_style", "tick_ms",
                    "idle_frame_ms", "sleep_frame_ms"]
    body = []
    for k in scalar_order:
        if k in layout:
            body.append(f'  {json.dumps(k)}: {json.dumps(layout[k])}')

    def block(key):
        d = layout[key]
        inner = []
        for dr, arr in d.items():
            cells = ", ".join(_cell_line(c) for c in arr)
            inner.append(f'    {json.dumps(dr)}: [{cells}]')
        return f'  {json.dumps(key)}: {{\n' + ",\n".join(inner) + "\n  }"

    if "walk" in layout:
        body.append(block("walk"))
    if "idle" in layout:
        body.append(block("idle"))
    if "sleep" in layout:
        cells = ", ".join(_cell_line(c) for c in layout["sleep"])
        body.append(f'  {json.dumps("sleep")}: [{cells}]')
    for k in ["walk_durations", "idle_durations", "sleep_durations"]:
        if k in layout:
            body.append(f'  {json.dumps(k)}: {json.dumps(layout[k])}')
    if "description" in layout:
        body.append(f'  {json.dumps("description")}: {json.dumps(layout["description"])}')

    lines.append(",\n".join(body))
    lines.append("}")
    return "\n".join(lines) + "\n"


def validate_layout(layout):
    """Lightweight structural check mirroring SpriteLayoutSchema.strict().

    Returns a list of error strings (empty = valid).
    """
    errs = []
    if layout.get("style") != "explicit":
        errs.append("style must be 'explicit'")
    for k in ("cols", "rows"):
        if not isinstance(layout.get(k), int) or layout[k] <= 0:
            errs.append(f"{k} must be a positive int")
    walk = layout.get("walk", {})
    for dr in CARDINALS:
        cellz = walk.get(dr)
        if not cellz:
            errs.append(f"walk.{dr} is required and non-empty")
    allowed_dirs = set(ALL_DIRS)
    for key in ("walk", "idle"):
        for dr, arr in layout.get(key, {}).items():
            if dr not in allowed_dirs:
                errs.append(f"{key}.{dr} is not a valid direction")
            for c in arr or []:
                if not (isinstance(c.get("col"), int) and c["col"] >= 0
                        and isinstance(c.get("row"), int) and c["row"] >= 0):
                    errs.append(f"{key}.{dr} has an invalid cell {c}")
    # duration lengths (warn-level, still reported)
    for dkey, ckey in (("walk_durations", "walk"),):
        if dkey in layout:
            n = len(next(iter(layout[ckey].values())))
            if len(layout[dkey]) != n:
                errs.append(f"{dkey} length {len(layout[dkey])} != {n} walk frames")
    return errs
