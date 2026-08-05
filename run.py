#!/usr/bin/env python3
"""Entry point for digimonSpriteManager.

Injects ``src/`` onto ``sys.path`` (so core modules import by top-level
package name, e.g. ``from core.segmenter import ...``) and starts the Flask
app. Mirrors the thin-launcher convention of the sibling PMDSpriteManager.

    python3 run.py            # serve the web tool on http://127.0.0.1:5001
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import config  # noqa: E402
from app import create_app  # noqa: E402


def main():
    config.ensure_dirs()
    app = create_app()
    print(f"digimonSpriteManager → http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    main()
