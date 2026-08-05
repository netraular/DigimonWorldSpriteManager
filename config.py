"""Central paths and defaults for digimonSpriteManager.

Imported by both the Flask app (`src/app.py`) and the GUI/HTTP-agnostic
core modules under `src/core/`, so there is a single source of truth for
where things live on disk.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# --- tool working directories --------------------------------------------
RAW_DIR = os.path.join(ROOT, "raw_sheets")          # downloaded input (gitignored)
SPEC_DIR = os.path.join(ROOT, "specs", "digimon")    # editable extraction specs
BOX_DIR = os.path.join(ROOT, "boxcache")             # cached auto-detect results
THUMB_DIR = os.path.join(ROOT, "thumbcache")         # cached gallery thumbnails
OUT_DIR = os.path.join(ROOT, "output", "digimon")    # baked deliverables

# --- staging into the hibitomo content-editor ----------------------------
# Copy-ready target so a bake can drop straight into the editor's dev tree.
CONTENT_ROOT = os.path.normpath(os.path.join(
    ROOT, "..", "hibitomo-content-editor",
    "local-content", "projects", "default",
    "shared", "services", "pet", "assets"))
GRAPHICS_SPECIES = os.path.join(CONTENT_ROOT, "graphics", "species", "digimon")
DATA_SPECIES = os.path.join(CONTENT_ROOT, "data", "digimon")

# --- The Spriters Resource -----------------------------------------------
GALLERY_URL = "https://www.spriters-resource.com/ds_dsi/dgmnworldds/"
BASE_URL = "https://www.spriters-resource.com"
# HTML pages sit behind Cloudflare and need a browser-like UA; the /media/
# CDN (the actual PNG bytes) does not, but sending the UA everywhere is safe.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# --- server ---------------------------------------------------------------
HOST = os.environ.get("DSM_HOST", "127.0.0.1")
PORT = int(os.environ.get("DSM_PORT", "5001"))


def ensure_dirs():
    """Create all tool working directories if they do not exist."""
    for d in (RAW_DIR, SPEC_DIR, BOX_DIR, THUMB_DIR, OUT_DIR):
        os.makedirs(d, exist_ok=True)
