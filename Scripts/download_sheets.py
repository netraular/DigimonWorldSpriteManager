#!/usr/bin/env python3
"""CLI: download all Digimon World DS sprite sheets into raw_sheets/.

    python3 Scripts/download_sheets.py             # fetch gallery + all sheets
    python3 Scripts/download_sheets.py --limit 5   # only 5 new (quick sample)
    python3 Scripts/download_sheets.py --urls-only  # just refresh sheet_urls.txt
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "src"))

import config  # noqa: E402
from core import downloader  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Download DWDS sprite sheets")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap the number of NEW downloads (quick sample)")
    ap.add_argument("--no-skip", action="store_true",
                    help="re-download sheets even if they already exist")
    ap.add_argument("--urls-only", action="store_true",
                    help="only fetch the gallery + write sheet_urls.txt")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="seconds to sleep between downloads")
    args = ap.parse_args()

    config.ensure_dirs()
    downloader.fetch_gallery()
    urls = downloader.derive_urls()
    if args.urls_only:
        return
    downloader.download_all(urls, skip_existing=not args.no_skip,
                            delay=args.delay, limit=args.limit)


if __name__ == "__main__":
    main()
