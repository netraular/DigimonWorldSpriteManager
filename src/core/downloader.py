"""Download Digimon World DS sprite sheets from The Spriters Resource.

Mechanics (verified against the live site, 2026):

* The gallery HTML sits behind Cloudflare and rejects non-browser agents
  (HTTP 403 for WebFetch / bare ``requests``), but a plain ``curl`` with a
  browser User-Agent gets HTTP 200. So we shell out to ``curl``.
* The gallery lists every sheet's icon as ``/media/asset_icons/<bucket>/<id>.png``.
  The full-resolution sheet lives at the parallel path
  ``/media/assets/<bucket>/<id>.png`` — same bucket, same id. We therefore
  derive all download URLs straight from the gallery HTML; no need to fetch
  each per-asset page.
* The ``/media/`` CDN serves the PNG bytes without Cloudflare friction, but we
  send the UA everywhere anyway (harmless).

Everything here is stdlib + ``subprocess`` (curl). No third-party deps.
"""
import os
import re
import subprocess
import time

import config

_ICON_RE = re.compile(r"asset_icons/(\d+)/(\d+)\.png")


def _curl(url, out_path=None, retries=3, delay=1.0):
    """Fetch ``url`` with a browser UA. Returns (ok, http_code, size).

    When ``out_path`` is given the body is written there; otherwise the body
    is returned as the third element instead of a size.
    """
    for attempt in range(retries):
        cmd = ["curl", "-sS", "-A", config.UA, "--fail", url]
        if out_path:
            cmd += ["-o", out_path, "-w", "%{http_code} %{size_download}"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                time.sleep(delay)
                continue
            if res.returncode == 0 and res.stdout:
                code, _, size = res.stdout.partition(" ")
                return True, code, int(size or 0)
        else:
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                time.sleep(delay)
                continue
            if res.returncode == 0:
                return True, "200", res.stdout
        time.sleep(delay * (attempt + 1))
    return False, "ERR", 0 if out_path else b""


def fetch_gallery(log=print):
    """Download the gallery HTML to ``raw_sheets/gallery.html``. Returns path."""
    os.makedirs(config.RAW_DIR, exist_ok=True)
    dest = os.path.join(config.RAW_DIR, "gallery.html")
    ok, code, size = _curl(config.GALLERY_URL, dest)
    if not ok:
        raise RuntimeError(f"gallery fetch failed (HTTP {code})")
    log(f"gallery: HTTP {code}, {size} bytes → {dest}")
    return dest


def derive_urls(gallery_path=None, log=print):
    """Parse the gallery HTML for (id, bucket) pairs and build sheet URLs.

    Writes ``raw_sheets/sheet_urls.txt`` (one ``<id> <url>`` per line) and
    returns a list of ``(sheet_id, url)`` tuples, sorted numerically by id.
    """
    gallery_path = gallery_path or os.path.join(config.RAW_DIR, "gallery.html")
    with open(gallery_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    seen = {}
    for bucket, sid in _ICON_RE.findall(html):
        seen[sid] = bucket  # dedupe; last wins (bucket is stable per id)
    pairs = sorted(seen.items(), key=lambda kv: int(kv[0]))
    urls = [(sid, f"{config.BASE_URL}/media/assets/{bucket}/{sid}.png")
            for sid, bucket in pairs]
    out = os.path.join(config.RAW_DIR, "sheet_urls.txt")
    with open(out, "w", encoding="utf-8") as f:
        for sid, url in urls:
            f.write(f"{sid} {url}\n")
    log(f"derived {len(urls)} sheet URLs → {out}")
    return urls


def download_all(urls=None, log=print, skip_existing=True, delay=0.3, limit=None):
    """Download every sheet PNG to ``raw_sheets/<id>.png``.

    Returns ``(ok, fail)`` counts. ``limit`` caps the number of NEW downloads
    (handy for a quick sample run). Politely sleeps ``delay`` s between fetches.
    """
    if urls is None:
        urls = derive_urls(log=log)
    os.makedirs(config.RAW_DIR, exist_ok=True)
    ok = fail = new = 0
    for sid, url in urls:
        dest = os.path.join(config.RAW_DIR, f"{sid}.png")
        if skip_existing and os.path.exists(dest) and os.path.getsize(dest) > 0:
            ok += 1
            continue
        if limit is not None and new >= limit:
            break
        got, code, size = _curl(url, dest)
        new += 1
        if got and size > 0:
            ok += 1
            log(f"[{ok + fail}/{len(urls)}] {sid}.png  {size} B")
        else:
            fail += 1
            log(f"[{ok + fail}/{len(urls)}] {sid}  FAILED (HTTP {code})")
            # remove any zero-byte stub so a re-run retries it
            if os.path.exists(dest) and os.path.getsize(dest) == 0:
                os.remove(dest)
        time.sleep(delay)
    log(f"done: {ok} ok, {fail} failed")
    return ok, fail
