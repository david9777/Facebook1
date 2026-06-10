#!/usr/bin/env python3
"""
app.py — the always-on box for Replit (Reserved VM).

Does two jobs in one process:
  1. Serves dashboard.html at /  and the live data at /ads.json
  2. Runs scraper.py every SYNC_INTERVAL_HOURS (default 3) on a background
     scheduler, writing ads.json next to itself so the dashboard picks it up.

Endpoints:
  GET  /                -> dashboard
  GET  /ads.json        -> latest scraped data (falls back to sample_ads.json)
  GET  /healthz         -> {"ok": true, ...} for uptime checks
  POST /sync?token=XXX  -> trigger a scrape now (needs SYNC_TOKEN secret)
  GET  /status          -> last sync time, ad count, next run

Replit setup:
  - Set Secrets: PROXY (residential proxy URL), SYNC_TOKEN (any string),
    CHROMIUM_PATH (path to Nix chromium — see REPLIT_DEPLOY.md).
  - Deploy as a Reserved VM. Run command: python3 app.py
"""
from __future__ import annotations
import json, os, threading, datetime as dt, traceback
from pathlib import Path
from flask import Flask, send_file, jsonify, request, abort

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    BackgroundScheduler = None

HERE = Path(__file__).parent
ADS = HERE / "ads.json"
SAMPLE = HERE / "sample_ads.json"
DASH = HERE / "dashboard.html"
CONFIG = HERE / "config.json"

SYNC_INTERVAL_HOURS = float(os.environ.get("SYNC_INTERVAL_HOURS", "3"))
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))
# Free path: scrape from your home machine (residential IP) and push via /upload.
# Set SCRAPE_ON_SERVER=true only if you have a proxy and want Replit to scrape itself.
SCRAPE_ON_SERVER = os.environ.get("SCRAPE_ON_SERVER", "false").lower() == "true"

app = Flask(__name__)
_lock = threading.Lock()
_last = {"at": None, "ok": None, "ads": None, "error": None}


def do_sync():
    """Run one scrape. Safe to call from scheduler or HTTP. Never raises."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "a sync is already running"}
    try:
        import scraper
        cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        scraper.run(cfg, str(ADS))
        n = len(json.loads(ADS.read_text()).get("ads", [])) if ADS.exists() else 0
        _last.update(at=dt.datetime.utcnow().isoformat() + "Z", ok=True, ads=n, error=None)
        print(f"[sync] ok — {n} ads", flush=True)
        return {"ok": True, "ads": n}
    except SystemExit as e:           # scraper.run() exits loud on zero ads / block
        _last.update(at=dt.datetime.utcnow().isoformat() + "Z", ok=False, error=str(e))
        print(f"[sync] FAILED — {e}", flush=True)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        _last.update(at=dt.datetime.utcnow().isoformat() + "Z", ok=False, error=str(e))
        print("[sync] ERROR\n" + traceback.format_exc(), flush=True)
        return {"ok": False, "error": str(e)}
    finally:
        _lock.release()


@app.get("/")
def index():
    return send_file(DASH)


@app.get("/ads.json")
def ads():
    path = ADS if ADS.exists() else SAMPLE
    return send_file(path, mimetype="application/json")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, time=dt.datetime.utcnow().isoformat() + "Z")


@app.get("/status")
def status():
    return jsonify(last_sync=_last, interval_hours=SYNC_INTERVAL_HOURS,
                   has_live_data=ADS.exists())


@app.post("/sync")
def sync():
    if not SYNC_TOKEN or request.args.get("token") != SYNC_TOKEN:
        abort(403)
    # run in a thread so the HTTP request returns immediately
    threading.Thread(target=do_sync, daemon=True).start()
    return jsonify(started=True)


@app.post("/ingest")
def ingest():
    """Accept an ads.json bundle pushed from a local/residential scraper.
    This is the free path: the box that scrapes is your own machine (residential
    IP), and it POSTs results here so Replit just hosts the dashboard."""
    if not SYNC_TOKEN or request.args.get("token") != SYNC_TOKEN:
        abort(403)
    data = request.get_json(silent=True)
    if not data or "ads" not in data:
        return jsonify(ok=False, error="body must be the ads.json bundle (with an 'ads' list)"), 400
    ADS.write_text(json.dumps(data))
    _last.update(at=dt.datetime.utcnow().isoformat() + "Z", ok=True,
                 ads=len(data["ads"]), error=None)
    print(f"[ingest] received {len(data['ads'])} ads", flush=True)
    return jsonify(ok=True, ads=len(data["ads"]))


def start_scheduler():
    if BackgroundScheduler is None:
        print("[scheduler] apscheduler not installed — skipping auto-sync", flush=True)
        return
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(do_sync, "interval", hours=SYNC_INTERVAL_HOURS,
                  next_run_time=dt.datetime.now() + dt.timedelta(seconds=20),
                  id="scrape", max_instances=1, coalesce=True)
    sched.start()
    print(f"[scheduler] scraping every {SYNC_INTERVAL_HOURS}h", flush=True)


if __name__ == "__main__":
    # Server-side scraping only makes sense with a residential PROXY (datacenter
    # IPs get blocked). Without one, run the free path: serve the dashboard and
    # accept data pushed to /ingest from a local scraper (run_local.py).
    server_scrape = (os.environ.get("SCRAPE_ON_SERVER", "").lower() in ("1", "true", "yes")
                     or bool(os.environ.get("PROXY")))
    if server_scrape:
        start_scheduler()
    else:
        print("[scheduler] server-side scraping OFF (no PROXY set).", flush=True)
        print("[scheduler] Push data with run_local.py -> POST /ingest from your own machine.", flush=True)
    print(f"[web] serving on 0.0.0.0:{PORT}", flush=True)
    app.run(host="0.0.0.0", port=PORT)
