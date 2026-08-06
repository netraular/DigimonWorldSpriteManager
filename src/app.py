"""Flask app: JSON API + static SPA for digimonSpriteManager.

All heavy lifting lives in ``core/`` (Pillow/numpy/scipy, no Flask import), so
the same logic backs both these endpoints and the ``Scripts/`` CLIs.
"""
import io
import json
import os
import threading

from flask import Flask, jsonify, request, send_file, send_from_directory
from PIL import Image

import config
from core import segmenter as S
from core import extractor as E
from core import autoassemble
from core import baker
from core import detect as D
from core import scaffold

_STATIC = os.path.join(os.path.dirname(__file__), "web", "static")

# Simple in-process progress state for the background downloader.
_dl_state = {"running": False, "done": 0, "total": 0, "fail": 0}
# …and for the bulk auto-assembler (the gallery's Auto-assemble button).
_aa_state = {"running": False, "phase": "", "done": 0, "total": 0, "fail": 0,
             "cropped": 0, "errors": []}


def create_app():
    app = Flask(__name__, static_folder=None)
    config.ensure_dirs()

    # ---- static SPA ----
    @app.get("/")
    def index():
        return send_from_directory(_STATIC, "index.html")

    # Deep links: the animate/crop editors have their own URLs so the browser
    # back/forward buttons move between gallery and per-sheet editor. Flask serves
    # the same SPA shell; the client reads the sheet id from the path on load.
    @app.get("/animate/<sid>")
    def index_sheet(sid):
        return send_from_directory(_STATIC, "index.html")

    @app.get("/crop")
    def crop():
        return send_from_directory(_STATIC, "crop.html")

    # The wall of baked creatures, all animating at once.
    @app.get("/preview")
    def preview():
        return send_from_directory(_STATIC, "preview.html")

    @app.get("/crop/<sid>")
    def crop_sheet(sid):
        return send_from_directory(_STATIC, "crop.html")

    @app.get("/static/<path:path>")
    def static_files(path):
        return send_from_directory(_STATIC, path)

    @app.get("/output/<path:path>")
    def output_files(path):
        return send_from_directory(os.path.join(config.ROOT, "output"), path)

    # ---- sheets ----
    @app.get("/api/sheets")
    def list_sheets():
        sheets = []
        for name in sorted(os.listdir(config.RAW_DIR)):
            if not name.endswith(".png"):
                continue
            sid = name[:-4]
            path = os.path.join(config.RAW_DIR, name)
            try:
                with Image.open(path) as im:
                    w, h = im.size
            except Exception:  # noqa: BLE001
                continue
            nnn = _spec_id_for(sid)
            st = _selection_status(sid)
            state = "deleted" if st["skip"] else ("done" if st["edited"] else "todo")
            sheets.append({
                "id": sid, "file": name, "w": w, "h": h,
                "has_boxes": st["has_boxes"], "count": st["count"],
                "edited": st["edited"], "skip": st["skip"],
                "reviewed": st["edited"] or st["skip"],
                "state": state,
                "has_spec": nnn is not None and os.path.exists(_spec_path(nnn)),
                "baked": nnn is not None and os.path.exists(os.path.join(config.OUT_DIR, f"{nnn}.png")),
            })
        sheets.sort(key=lambda s: int(s["id"]) if s["id"].isdigit() else 0)
        reviewed = sum(1 for s in sheets if s["reviewed"])
        counts = {
            "all": len(sheets),
            "todo": sum(1 for s in sheets if s["state"] == "todo"),
            "done": sum(1 for s in sheets if s["state"] == "done"),
            "deleted": sum(1 for s in sheets if s["state"] == "deleted"),
        }
        return jsonify({"sheets": sheets, "count": len(sheets),
                        "reviewed": reviewed, "counts": counts})

    @app.get("/api/sheets/<sid>/image")
    def sheet_image(sid):
        path = os.path.join(config.RAW_DIR, f"{_safe(sid)}.png")
        if not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        return send_file(path, mimetype="image/png")

    @app.get("/api/sheets/<sid>/thumb")
    def sheet_thumb(sid):
        """Downscaled, disk-cached preview for the gallery (crisp for pixel art)."""
        src = os.path.join(config.RAW_DIR, f"{_safe(sid)}.png")
        if not os.path.exists(src):
            return jsonify({"error": "not found"}), 404
        try:
            max_px = max(32, min(512, int(request.args.get("max", 240))))
        except (TypeError, ValueError):
            max_px = 240
        dst = os.path.join(config.THUMB_DIR, f"{_safe(sid)}.{max_px}.png")
        if not os.path.exists(dst) or os.path.getmtime(dst) < os.path.getmtime(src):
            with Image.open(src) as im:
                im = im.convert("RGBA")
                im.thumbnail((max_px, max_px), Image.NEAREST)
                im.save(dst)
        return send_file(dst, mimetype="image/png")

    @app.get("/api/sheets/<sid>/sprite/<box_id>")
    def sheet_sprite(sid, box_id):
        """Return ONE cropped sprite as a clean transparent PNG.

        Uses the very same extraction the baker runs (``extractor.extract_box``
        with the box cache's curated background), so what the animate view shows
        is exactly what gets baked. Read-only; nothing is written.
        """
        path = os.path.join(config.RAW_DIR, f"{_safe(sid)}.png")
        if not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        data = _boxes_for(sid)
        if data is None:
            return jsonify({"error": "not found"}), 404
        box = next((b for b in data.get("boxes", [])
                    if str(b.get("id")) == str(box_id)), None)
        if box is None:
            return jsonify({"error": "unknown box"}), 404
        arr = _load_arr(path)
        payload = data.get("background") or {}
        p = S.SegParams.merged(_seg_overrides(payload))
        bg = _bg_from_payload(payload, arr, p)
        img, _info = E.extract_box(arr, bg, box, p)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    @app.get("/api/sheets/<sid>/boxes")
    def get_boxes(sid):
        force = request.args.get("force") == "1"
        cache = os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json")
        if os.path.exists(cache) and not force:
            with open(cache, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        result = _detect(sid, None)
        if result is None:
            return jsonify({"error": "not found"}), 404
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(result, f)
        return jsonify(result)

    @app.post("/api/sheets/<sid>/boxes")
    def redetect_boxes(sid):
        body = request.get_json(silent=True) or {}
        overrides = body.get("params", body)
        result = _detect(sid, overrides)
        if result is None:
            return jsonify({"error": "not found"}), 404
        if body.get("save"):
            with open(os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json"), "w",
                      encoding="utf-8") as f:
                json.dump(result, f)
        return jsonify(result)

    @app.put("/api/sheets/<sid>/selection")
    def save_selection(sid):
        """Persist a manually-curated box set (+ bg) to the box cache."""
        body = request.get_json(silent=True) or {}
        boxes = body.get("boxes")
        if not isinstance(boxes, list):
            return jsonify({"error": "body.boxes must be a list"}), 400
        cache = os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json")
        existing = {}
        if os.path.exists(cache):
            with open(cache, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing["id"] = sid
        existing["boxes"] = boxes
        existing["edited"] = True
        existing["skip"] = bool(body.get("skip"))
        if body.get("background"):
            existing["background"] = body["background"]
        if body.get("image"):
            existing["image"] = body["image"]
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        return jsonify({"ok": True, "count": len(boxes), "skip": existing["skip"]})

    @app.post("/api/sheets/<sid>/state")
    def set_state(sid):
        """Move a sheet between todo / done / deleted without opening the editor.

        Used by the gallery for quick delete ("no sprites") and restore. Boxes are
        preserved as suggestions; only the review flags change.
        """
        body = request.get_json(silent=True) or {}
        state = body.get("state")
        if state not in ("todo", "done", "deleted"):
            return jsonify({"error": "state must be todo|done|deleted"}), 400
        cache = os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json")
        if state == "todo" and not os.path.exists(cache):
            return jsonify({"ok": True, "state": "todo"})  # already un-reviewed
        existing = {}
        if os.path.exists(cache):
            with open(cache, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing["id"] = sid
        if state == "deleted":
            existing["skip"] = True
        elif state == "done":
            existing["skip"] = False
            existing["edited"] = True
        else:  # todo
            existing["skip"] = False
            existing["edited"] = False
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        return jsonify({"ok": True, "state": state})

    # ---- specs ----
    @app.get("/api/specs/<nnn>")
    def get_spec(nnn):
        path = _spec_path(nnn)
        if not os.path.exists(path):
            return jsonify({"error": "not found"}), 404
        with open(path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))

    @app.put("/api/specs/<nnn>")
    def put_spec(nnn):
        spec = request.get_json(silent=True)
        if not isinstance(spec, dict):
            return jsonify({"error": "body must be a JSON object"}), 400
        err = _validate_spec(spec)
        if err:
            return jsonify({"error": err}), 400
        path = _spec_path(nnn)
        # A creature number belongs to ONE sheet. Saving over the spec of a
        # different sheet is how a whole creature silently disappeared (its sheet
        # popped back into the gallery as "ready"), so it takes ?force=1.
        owner = _spec_owner(path)
        if (owner is not None and str(owner) != str(spec.get("sheet_id"))
                and request.args.get("force") != "1"):
            return jsonify({"error": f"spec {int(nnn):03d} already belongs to sheet "
                                     f"{owner} — next free id is {_next_spec_id()}",
                            "conflict": {"id": int(nnn), "sheet_id": owner,
                                         "next_id": _next_spec_id()}}), 409
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)
            f.write("\n")
        return jsonify({"ok": True, "path": os.path.relpath(path, config.ROOT)})

    @app.get("/api/specs")
    def list_specs():
        """Every spec, plus which sheet owns it and the first free number.

        ``by_sheet`` saves the animate view a fetch per spec when it looks up the
        one belonging to a sheet, and ``next_id`` is what a NEW creature must be
        numbered — never the "1" an empty form would otherwise send.
        """
        out, by_sheet = [], {}
        for name in sorted(os.listdir(config.SPEC_DIR)):
            if not name.endswith(".extract.json"):
                continue
            nnn = name[:-len(".extract.json")]
            out.append(nnn)
            try:
                with open(os.path.join(config.SPEC_DIR, name), "r", encoding="utf-8") as f:
                    spec = json.load(f)
                by_sheet[str(spec.get("sheet_id"))] = nnn
            except Exception:  # noqa: BLE001
                continue
        return jsonify({"specs": out, "by_sheet": by_sheet,
                        "next_id": _next_spec_id()})

    @app.post("/api/specs/<nnn>/bake")
    def bake_spec(nnn):
        path = _spec_path(nnn)
        if not os.path.exists(path):
            return jsonify({"error": "spec not found"}), 404
        body = request.get_json(silent=True) or {}
        try:
            res = baker.bake(path)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 400
        if body.get("write_creature") and body.get("creature"):
            scaffold.write_creature(body["creature"], species="digimon")
            res["creature"] = os.path.join("output", "digimon", f"pet_{int(nnn):03d}.json")
        if body.get("stage_to_content"):
            dst = scaffold.stage_to_content(nnn, write_creature_node=bool(body.get("write_creature")))
            res["staged"] = {k: os.path.relpath(v, config.ROOT) for k, v in dst.items()}
        # make paths relative for the UI
        for k in ("png", "layout"):
            res[k] = os.path.relpath(res[k], config.ROOT)
        return jsonify(res)

    # ---- baked creatures (the /preview wall) ----
    @app.get("/api/baked")
    def list_baked():
        """Every baked creature, with what it takes to animate it in the browser.

        The layout's cells are ``{col,row}`` into a uniform grid, so the cell size
        is the sheet's size over cols/rows — the client needs no extra request per
        creature. Only the iso facings are sent: the cardinals the schema requires
        are aliases of those same rows (see ``core/baker.py``).
        """
        out = []
        for name in sorted(os.listdir(config.SPEC_DIR)):
            if not name.endswith(".extract.json"):
                continue
            nnn = name[:-len(".extract.json")]
            png = os.path.join(config.OUT_DIR, f"{nnn}.png")
            lay_path = os.path.join(config.OUT_DIR, f"{nnn}.json")
            if not (os.path.exists(png) and os.path.exists(lay_path)):
                continue
            try:
                with open(os.path.join(config.SPEC_DIR, name), "r", encoding="utf-8") as f:
                    spec = json.load(f)
                with open(lay_path, "r", encoding="utf-8") as f:
                    lay = json.load(f)
                with Image.open(png) as im:
                    w, h = im.size
            except Exception:  # noqa: BLE001 — a half-written bake must not 500 the wall
                continue
            cols, rows = int(lay.get("cols") or 1), int(lay.get("rows") or 1)
            out.append({
                "id": int(nnn), "sheet_id": str(spec.get("sheet_id")),
                "name": spec.get("creature_name") or "",
                "sprites": len(spec.get("boxes") or []),
                "png": f"/output/digimon/{nnn}.png",
                "cols": cols, "rows": rows,
                "cell_w": w // cols, "cell_h": h // rows,
                "tick_ms": lay.get("tick_ms", 33),
                "walk_durations": lay.get("walk_durations"),
                "idle_frame_ms": lay.get("idle_frame_ms"),
                "sleep_frame_ms": lay.get("sleep_frame_ms"),
                "walk": {d: lay["walk"][d] for d in baker.ISO_DIRS
                         if d in lay.get("walk", {})},
                "idle": (lay.get("idle") or {}).get("down_left")
                        or next(iter((lay.get("idle") or {}).values()), []),
                "sleep": lay.get("sleep") or [],
            })
        return jsonify({"creatures": out, "count": len(out)})

    # ---- block layouts ----
    @app.get("/api/layouts")
    def block_layouts():
        """The sprite-count → block plan table, so the animate editor can apply
        the very same assembly to a single sheet."""
        return jsonify({
            "block": autoassemble.BLOCK_SIZE,
            "layouts": {str(n): {"slots": lay["slots"], "mirror": lay["mirror"],
                                 "plan": autoassemble.describe(n)}
                        for n, lay in autoassemble.LAYOUTS.items()},
        })

    # ---- bulk auto-assembler ----
    @app.get("/api/auto-assemble")
    def auto_assemble_preview():
        """What one click would do: cropped sheets per sprite count, plus how many
        never-reviewed sheets a ``new`` run would still have to detect."""
        todo = autoassemble.candidates()
        groups = {}
        for c in todo:
            g = groups.setdefault(c["count"], {"count": c["count"], "sheets": 0,
                                               "plan": autoassemble.describe(c["count"])})
            g["sheets"] += 1
        return jsonify({"total": len(todo),
                        "groups": [groups[k] for k in sorted(groups)],
                        "new": len(autoassemble.unreviewed()),
                        "state": _aa_state})

    @app.get("/api/auto-assemble/status")
    def auto_assemble_status():
        return jsonify(_aa_state)

    @app.post("/api/auto-assemble")
    def start_auto_assemble():
        if _aa_state["running"]:
            return jsonify(_aa_state)
        body = request.get_json(silent=True) or {}
        counts = body.get("counts") or None
        threading.Thread(target=_run_auto_assemble,
                         args=(counts, bool(body.get("stage")), body.get("limit"),
                               bool(body.get("include_new"))),
                         daemon=True).start()
        return jsonify({"started": True})

    # ---- downloader ----
    @app.get("/api/download/status")
    def download_status():
        return jsonify(_dl_state)

    @app.post("/api/download")
    def start_download():
        if _dl_state["running"]:
            return jsonify(_dl_state)
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        threading.Thread(target=_run_download, args=(limit,), daemon=True).start()
        return jsonify({"started": True})

    return app


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _safe(sid):
    return "".join(c for c in str(sid) if c.isalnum())


# A tiny 1-entry cache so rendering a whole sheet's sprites doesn't re-decode the
# raw PNG once per sprite. Keyed by path+mtime; invalidated when the file changes.
_arr_cache = {"entry": None}


def _load_arr(path):
    # Key and array are swapped as ONE tuple: the palette fires a dozen concurrent
    # /sprite requests and the dev server is threaded, so publishing the key before
    # the array let another thread read a half-updated cache and crop a box out of
    # the previous sheet (or out of nothing). Worst case now, two threads decode
    # the same PNG — wasted work, never a wrong sprite.
    key = (path, os.path.getmtime(path))
    entry = _arr_cache["entry"]
    if entry is not None and entry[0] == key:
        return entry[1]
    arr = S.load_rgba(path)
    _arr_cache["entry"] = (key, arr)
    return arr


def _boxes_for(sid):
    """Return the cached box set for a sheet (auto-detecting if never cached)."""
    cache = os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json")
    if os.path.exists(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            pass
    return _detect(sid, None)


def _seg_overrides(payload):
    """Extraction params a box cache's background payload pins down.

    ``enclosed`` opts a single sheet out of keying the backdrop pixels its
    sprites wall in — the sheet keyed on black, whose outlines ARE the key.
    Absent (the normal case) leaves the SegParams default on. The animate view
    copies it into the spec's ``seg_params`` so a bake keys exactly what the
    palette showed.
    """
    return {"tol": (payload or {}).get("tolerance"),
            "bg_enclosed": (payload or {}).get("enclosed")}


def _bg_from_payload(payload, arr, p):
    """Rebuild an extractor background dict from a box-cache background payload.

    Mirrors ``baker.bake``: an explicit colour set (primary + extras) keys to
    transparent; ``alpha`` mode uses the image's own alpha; missing/legacy
    payloads fall back to auto-inference.
    """
    if payload and payload.get("mode") == "alpha":
        return {"mode": "alpha", "colors": [], "color": None, "has_alpha": True}
    colors = []
    for c in (payload or {}).get("colors") or []:
        try:
            colors.append(tuple(int(v) for v in c[:3]))
        except (TypeError, ValueError):
            continue
    if colors:
        return {"mode": "solid", "colors": colors, "color": colors[0],
                "has_alpha": False}
    return S.infer_background(arr, p)


def _selection_status(sid):
    """Read the box cache for a sheet's review status (cheap; small JSON)."""
    cache = os.path.join(config.BOX_DIR, f"{_safe(sid)}.boxes.json")
    out = {"has_boxes": False, "edited": False, "skip": False, "count": 0}
    if not os.path.exists(cache):
        return out
    out["has_boxes"] = True
    try:
        with open(cache, "r", encoding="utf-8") as f:
            d = json.load(f)
        out["edited"] = bool(d.get("edited"))
        out["skip"] = bool(d.get("skip"))
        out["count"] = len(d.get("boxes", []))
    except Exception:  # noqa: BLE001
        pass
    return out


def _spec_id_for(sheet_id):
    """Find the NNN of a spec whose sheet_id matches this raw sheet, if any."""
    if not os.path.isdir(config.SPEC_DIR):
        return None
    for name in os.listdir(config.SPEC_DIR):
        if not name.endswith(".extract.json"):
            continue
        try:
            with open(os.path.join(config.SPEC_DIR, name), "r", encoding="utf-8") as f:
                spec = json.load(f)
            if str(spec.get("sheet_id")) == str(sheet_id):
                return "%03d" % int(spec["id"])
        except Exception:  # noqa: BLE001
            continue
    return None


def _spec_path(nnn):
    return os.path.join(config.SPEC_DIR, f"{int(nnn):03d}.extract.json")


def _spec_owner(path):
    """The sheet id a spec file on disk belongs to (None if there is no file)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return str(json.load(f).get("sheet_id"))
    except Exception:  # noqa: BLE001 — unreadable spec: let the write through
        return None


def _next_spec_id():
    """Lowest creature number no spec has taken."""
    taken = set()
    for name in os.listdir(config.SPEC_DIR):
        if name.endswith(".extract.json"):
            try:
                taken.add(int(name[:-len(".extract.json")]))
            except ValueError:
                continue
    n = 1
    while n in taken:
        n += 1
    return n


# Detection lives in core/detect.py so the batch tools can crop a never-opened
# sheet exactly the way this endpoint would.
_detect = D.detect_sheet


def _validate_spec(spec):
    """Structural check for a SAVED spec (a draft recipe, not a bake).

    Deliberately permissive about which facings are filled: the animate view
    saves as you assemble, and the strict per-facing requirement (all four iso
    facings, or all four cardinals for a legacy top-down spec) belongs to the
    bake, which is where a half-filled spec would actually produce a bad sheet.
    """
    if "boxes" not in spec or "clips" not in spec:
        return "spec must have 'boxes' and 'clips'"
    box_ids = {b.get("id") for b in spec.get("boxes", [])}
    clips = spec.get("clips", {})
    walk = clips.get("walk", {}) or {}
    mirror = spec.get("mirror", {}) or {}
    if not any(walk.get(mirror.get(dr, dr)) for dr in set(walk) | set(mirror)):
        return "clips.walk needs at least one facing with frames"
    for dr, ids in walk.items():
        for bid in ids:
            if bid not in box_ids:
                return f"clip walk.{dr} references unknown box id {bid!r}"
    # idle and sleep are single non-directional clips (flat id lists); an idle
    # written by an older build is still a per-direction dict, so accept both.
    for clip in ("idle", "sleep"):
        frames = clips.get(clip) or []
        if isinstance(frames, dict):
            frames = [bid for ids in frames.values() for bid in ids]
        for bid in frames:
            if bid not in box_ids:
                return f"clip {clip} references unknown box id {bid!r}"
    return None


def _run_auto_assemble(counts, stage, limit, include_new=False):
    """Background worker for the Auto-assemble buttons (same code as the CLI)."""
    _aa_state.update(running=True, phase="scan" if include_new else "assemble",
                     done=0, total=0, fail=0, cropped=0, errors=[])

    def progress(done, total, fail, phase="assemble"):
        _aa_state.update(done=done, total=total, fail=fail, phase=phase)

    try:
        res = autoassemble.run(counts=counts, limit=limit, stage=stage,
                               include_new=include_new, validate=_validate_spec,
                               log=lambda *_: None, progress=progress)
        _aa_state.update(done=res["ok"], total=res["total"], fail=res["fail"],
                         cropped=len(res["cropped"]), phase="assemble",
                         errors=res["errors"][:20])
    finally:
        _aa_state["running"] = False


def _run_download(limit):
    from core import downloader
    _dl_state.update(running=True, done=0, fail=0)
    try:
        urls = downloader.derive_urls(log=lambda *_: None)
        _dl_state["total"] = len(urls)

        def log(msg):
            if "ok," in msg:
                return
            _dl_state["done"] += 1
        downloader.fetch_gallery(log=lambda *_: None)
        ok, fail = downloader.download_all(urls, log=lambda *_: None, limit=limit)
        _dl_state.update(done=ok, fail=fail)
    finally:
        _dl_state["running"] = False
