#!/usr/bin/env python3
"""
run_local.py — the free path. Run this ON YOUR OWN COMPUTER.

Your home/office internet is a residential IP, which Meta's Ad Library accepts —
so the scrape works here even though it won't from Replit's datacenter IP. This
script scrapes locally, then pushes the result up to your Replit dashboard's
/ingest endpoint. Replit just hosts the always-on dashboard; your machine feeds it.

One-time setup on your computer:
    pip install -r requirements.txt
    python -m playwright install chromium     # local Chromium is fine here

Run it (point at your deployed Replit app):
    python run_local.py --url https://YOUR-APP.replit.app --token YOUR_SYNC_TOKEN

Or set env vars and just `python run_local.py`:
    REPLIT_URL=https://YOUR-APP.replit.app  SYNC_TOKEN=YOUR_SYNC_TOKEN
"""
from __future__ import annotations
import argparse, json, os, sys, urllib.request, urllib.parse
from pathlib import Path

import scraper  # local module

HERE = Path(__file__).parent
OUT = HERE / "ads.json"


def push(url: str, token: str, bundle: dict):
    endpoint = url.rstrip("/") + "/ingest?token=" + urllib.parse.quote(token)
    body = json.dumps(bundle).encode()
    req = urllib.request.Request(endpoint, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser(description="Scrape locally, push to Replit dashboard")
    ap.add_argument("--url", default=os.environ.get("REPLIT_URL", ""), help="https://YOUR-APP.replit.app")
    ap.add_argument("--token", default=os.environ.get("SYNC_TOKEN", ""), help="same SYNC_TOKEN as the Replit secret")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--no-push", action="store_true", help="scrape only, don't upload")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}

    print("Scraping locally (residential IP)…")
    scraper.run(cfg, str(OUT))          # writes ads.json + csv + xlsx, keeps history in arbiter.db
    bundle = json.loads(OUT.read_text())
    print(f"Scraped {len(bundle.get('ads', []))} ads.")

    if args.no_push:
        print("--no-push set; left result in", OUT)
        return
    if not args.url or not args.token:
        sys.exit("Set --url and --token (or REPLIT_URL / SYNC_TOKEN env) to push to Replit.")

    print(f"Pushing to {args.url} …")
    try:
        res = push(args.url, args.token, bundle)
        print("Dashboard updated:", res)
    except Exception as e:
        sys.exit(f"Upload failed: {e}\n(Check the URL, that the app is deployed, and the token matches.)")


if __name__ == "__main__":
    import urllib.parse  # noqa: needed by push()
    main()
