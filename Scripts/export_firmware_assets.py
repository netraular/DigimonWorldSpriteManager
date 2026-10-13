#!/usr/bin/env python3
"""Export local bakes as split clips and portraits for current hibitomo firmware."""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config
from core.firmware_exporter import DEFAULT_ID_OFFSET, export_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="new package directory; must not exist")
    parser.add_argument("--source", default=config.OUT_DIR,
                        help="directory containing existing baked PNGs and JSON")
    parser.add_argument("--id-offset", type=int, default=DEFAULT_ID_OFFSET,
                        help="offset added to source IDs (default: 1000)")
    args = parser.parse_args()
    manifest = export_assets(args.source, args.destination, args.id_offset)
    print(json.dumps(manifest["summary"], indent=2))
    print(f"Exported to {os.path.abspath(args.destination)}")
    generic = [c["source_id"] for c in manifest["creatures"]
               if c["name"].startswith("Digimon ")]
    if generic:
        print(f"WARNING: generic source names retained for IDs: {generic}")
    print("Asset package only: no game catalogue or firmware changes included.")


if __name__ == "__main__":
    main()
