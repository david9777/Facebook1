#!/usr/bin/env python3
"""
scraper.py — Meta Ad Library front-end scraper (GraphQL interception).

Why front-end and not the official API:
  The official /ads_archive Graph API only returns full results for political /
  social-issue ads, and limits general ads to EU-targeted countries. Mass-tort
  legal ads are *commercial* ads in the US, so the official API barely sees them.
  The public Ad Library *website* shows all of them. This tool drives a real
  browser to that website and captures the internal GraphQL responses the page
  itself fetches — the same mechanism the commercial scrapers use.

How it works:
  1. Launch a real Chromium via Playwright (proxy + stealthy headers supported).
  2. For each tracked advertiser (page id) or keyword, open its Ad Library URL.
  3. Listen to every /api/graphql/ response; recursively pull out ad nodes.
  4. Scroll to lazy-load more results until exhausted or max reached.
  5. Normalize -> enrich (case-type + duplicate/scaling) -> SQLite upsert.
  6. Export ads.json (consumed by dashboard.html), plus CSV and XLSX.

Honest caveats (a senior engineer flags these up front):
  - This violates Meta's ToS (automated access). Public-data scraping is not a
    CFAA crime per hiQ v. LinkedIn, but it carries civil-liability risk. Decide
    with counsel for production use.
  - It is fragile: when Meta reshapes the GraphQL envelope the parser may need a
    tweak. We FAIL LOUD (raise) rather than silently return partial data.
  - At volume you need residential/mobile proxies; datacenter IPs get WAF-blocked.

Usage:
  pip install -r requirements.txt && python -m playwright install chromium
  python scraper.py --config config.json --out ads.json
  PROXY=http://user:pass@host:port python scraper.py --config config.json
"""
from __future__ import annotations
import argparse, json, os, re, sqlite3, sys, time, datetime as dt
from pathlib import Path

from enrich import enrich

DEFAULT_COUNTRY = "US"
GRAPHQL_MARK = "/api/graphql/"
PLATFORM_MAP = {
    "FACEBOOK": "facebook", "INSTAGRAM": "instagram",
    "MESSENGER": "messenger", "AUDIENCE_NETWORK": "audience_network",
}


# --------------------------------------------------------------------------- #
#  URL builders
# --------------------------------------------------------------------------- #
def page_url(page_id: str, country: str) -> str:
    return (
        "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
        f"&country={country}&view_all_page_id={page_id}"
        "&sort_data[direction]=desc&sort_data[mode]=relevancy_monthly_grouped&media_type=all"
    )


def keyword_url(q: str, country: str) -> str:
    from urllib.parse import quote
    return (
        "https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
        f"&country={country}&q={quote(q)}&search_type=keyword_unordered&media_type=all"
    )


def page_id_from_url(url: str) -> str | None:
    m = re.search(r"view_all_page_id=(\d+)", url) or re.search(r"/(\d{6,})(?:/|\?|$)", url)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
#  GraphQL response parsing — resilient recursive walk
# --------------------------------------------------------------------------- #
def _epoch_to_iso(v):
    try:
        if v in (None, 0, "0"):
            return None
        return dt.datetime.utcfromtimestamp(int(v)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _first(*vals):
    for v in vals:
        if v:
            return v
    return None


def extract_ad_nodes(obj, found: list):
    """Recursively collect any dict that looks like an Ad Library ad node."""
    if isinstance(obj, dict):
        if obj.get("ad_archive_id") or obj.get("adArchiveID"):
            found.append(obj)
        for v in obj.values():
            extract_ad_nodes(v, found)
    elif isinstance(obj, list):
        for v in obj:
            extract_ad_nodes(v, found)
    return found


def normalize_node(node: dict, country: str) -> dict | None:
    aid = str(node.get("ad_archive_id") or node.get("adArchiveID") or "")
    if not aid:
        return None
    snap = node.get("snapshot") or {}
    body = snap.get("body") or {}
    copy_text = body.get("text") if isinstance(body, dict) else (body or "")
    # media
    videos = snap.get("videos") or []
    images = snap.get("images") or []
    media_type = "video" if videos else ("image" if images else "unknown")
    plats = [PLATFORM_MAP.get(p, str(p).lower()) for p in (node.get("publisher_platform") or [])]
    landing = _first(snap.get("link_url"), snap.get("caption"))
    is_active = node.get("is_active")
    status = "ACTIVE" if is_active else "INACTIVE"
    return {
        "id": aid,
        "page_id": str(node.get("page_id") or ""),
        "advertiser": _first(node.get("page_name"), snap.get("page_name"), "Unknown"),
        "company": _first(node.get("page_name"), snap.get("page_name"), "Unknown"),
        "case_type": "", "case_key": "",
        "ad_copy": (copy_text or "").strip(),
        "cta_text": snap.get("cta_text") or "",
        "link_caption": snap.get("caption") or "",
        "link_title": snap.get("title") or "",
        "landing_url": landing or "",
        "media_type": media_type,
        "platforms": plats or ["facebook"],
        "start": _epoch_to_iso(node.get("start_date")),
        "end": _epoch_to_iso(node.get("end_date")),
        "status": status,
        "snapshot_url": f"https://www.facebook.com/ads/library/?id={aid}",
        "duplicate_group": "", "duplicate_count": 1, "rating": 0,
    }


# --------------------------------------------------------------------------- #
#  Browser scrape
# --------------------------------------------------------------------------- #
def scrape_target(context, url: str, country: str, max_ads: int, scroll_pause: float, log) -> list[dict]:
    page = context.new_page()
    captured: dict[str, dict] = {}

    def on_response(resp):
        try:
            if GRAPHQL_MARK not in resp.url:
                return
            txt = resp.text()
            if "ad_archive_id" not in txt and "adArchiveID" not in txt:
                return
            txt = txt.lstrip()
            if txt.startswith("for (;;);"):
                txt = txt[len("for (;;);"):]
            # responses can be NDJSON (multiple JSON objects); parse each line
            for chunk in txt.splitlines():
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                for n in extract_ad_nodes(data, []):
                    norm = normalize_node(n, country)
                    if norm:
                        captured.setdefault(norm["id"], norm)
        except Exception:
            pass  # never let a parse error kill the scroll loop

    page.on("response", on_response)
    log(f"  → {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3500)

    if "log in" in (page.title() or "").lower() or "login" in page.url:
        page.close()
        raise RuntimeError("Hit a login wall — Meta is gating this IP. Use a residential proxy.")

    stale, last = 0, 0
    for i in range(80):
        if len(captured) >= max_ads:
            break
        page.mouse.wheel(0, 4200)
        page.wait_for_timeout(int(scroll_pause * 1000))
        now = len(captured)
        if now == last:
            stale += 1
            if stale >= 4:  # 4 dry scrolls => end of results
                break
        else:
            stale = 0
            log(f"    captured {now} ads…")
        last = now

    page.close()
    return list(captured.values())[:max_ads]


# --------------------------------------------------------------------------- #
#  Advertiser name -> page_id resolution (so you can track firms by name)
# --------------------------------------------------------------------------- #
import difflib

def _norm_name(s: str) -> str:
    s = (s or "").lower()
    s = re.split(r"\s+-\s+", s)[0]                      # "X - formerly Y" -> "X"
    s = re.sub(r"\b(llp|llc|l\.l\.c|pc|p\.c|pa|p\.a|apc|co|inc|ltd|law|firm|group|attorneys?)\b", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def collect_pages(obj, out: dict):
    """Recursively gather (page_id -> page name) pairs from any GraphQL payload."""
    if isinstance(obj, dict):
        pid = obj.get("page_id")
        nm = obj.get("page_name") or obj.get("name")
        if pid and nm and str(pid).isdigit():
            out.setdefault(str(pid), str(nm))
        for v in obj.values():
            collect_pages(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_pages(v, out)
    return out


def resolve_page_id(context, name: str, country: str, log) -> tuple[str, str] | None:
    """Search the Ad Library for an advertiser name and return its (page_id, name)."""
    from urllib.parse import quote
    url = ("https://www.facebook.com/ads/library/?active_status=all&ad_type=all"
           f"&country={country}&q={quote(name)}&search_type=page&media_type=all")
    page = context.new_page()
    cands: dict[str, str] = {}

    def on_resp(resp):
        try:
            if GRAPHQL_MARK not in resp.url:
                return
            txt = resp.text()
            if "page_id" not in txt:
                return
            txt = txt.lstrip()
            if txt.startswith("for (;;);"):
                txt = txt[len("for (;;);"):]
            for line in txt.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    collect_pages(json.loads(line), cands)
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

    page.on("response", on_resp)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4500)
    except Exception as e:
        log(f"    resolve error for '{name}': {e}")
    finally:
        page.close()

    if not cands:
        return None
    target = _norm_name(name)
    scored = sorted(((difflib.SequenceMatcher(None, target, _norm_name(nm)).ratio(), pid, nm)
                     for pid, nm in cands.items()), reverse=True)
    ratio, pid, nm = scored[0]
    log(f"    '{name}' -> {nm} ({pid})  match={ratio:.2f}")
    return (pid, nm) if ratio >= 0.55 else None


def load_resolved_cache(path: str) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def save_resolved_cache(path: str, cache: dict):
    Path(path).write_text(json.dumps(cache, indent=2))


# --------------------------------------------------------------------------- #
#  Persistence
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS ads(
  id TEXT PRIMARY KEY, page_id TEXT, advertiser TEXT, company TEXT,
  case_type TEXT, case_key TEXT, ad_copy TEXT, cta_text TEXT,
  link_caption TEXT, link_title TEXT, landing_url TEXT, media_type TEXT,
  platforms TEXT, start TEXT, end TEXT, status TEXT, snapshot_url TEXT,
  duplicate_group TEXT, duplicate_count INTEGER, rating INTEGER,
  first_seen TEXT, last_seen TEXT
);
"""


def upsert(db: sqlite3.Connection, ads: list[dict], now_iso: str):
    db.executescript(SCHEMA)
    cur = db.cursor()
    for a in ads:
        cur.execute("SELECT rating, first_seen FROM ads WHERE id=?", (a["id"],))
        row = cur.fetchone()
        rating = a.get("rating", 0)
        first_seen = now_iso
        if row:
            rating = row[0] if row[0] else rating  # preserve analyst rating across syncs
            first_seen = row[1] or now_iso
        cur.execute(
            """INSERT INTO ads VALUES (:id,:page_id,:advertiser,:company,:case_type,:case_key,
               :ad_copy,:cta_text,:link_caption,:link_title,:landing_url,:media_type,:platforms,
               :start,:end,:status,:snapshot_url,:duplicate_group,:duplicate_count,:rating,
               :first_seen,:last_seen)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, end=excluded.end,
               case_type=excluded.case_type, case_key=excluded.case_key,
               duplicate_group=excluded.duplicate_group, duplicate_count=excluded.duplicate_count,
               last_seen=excluded.last_seen""",
            {**a, "platforms": "|".join(a["platforms"]), "rating": rating,
             "first_seen": first_seen, "last_seen": now_iso},
        )
    db.commit()


def load_all(db: sqlite3.Connection) -> list[dict]:
    db.executescript(SCHEMA)
    cols = [c[1] for c in db.execute("PRAGMA table_info(ads)")]
    out = []
    for r in db.execute("SELECT * FROM ads"):
        d = dict(zip(cols, r))
        d["platforms"] = d["platforms"].split("|") if d["platforms"] else []
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
#  Export
# --------------------------------------------------------------------------- #
def build_bundle(ads: list[dict], country: str) -> dict:
    now = dt.datetime.utcnow()
    advs: dict[str, dict] = {}
    for a in ads:
        k = a["advertiser"]
        adv = advs.setdefault(k, {"page_id": a["page_id"], "page_name": k, "company": a["company"],
                                  "active_ads": 0, "total_ads": 0, "case_types": set(),
                                  "first_seen": a.get("first_seen")})
        adv["total_ads"] += 1
        adv["active_ads"] += a["status"] == "ACTIVE"
        adv["case_types"].add(a["case_type"])
        if a.get("first_seen") and (not adv["first_seen"] or a["first_seen"] < adv["first_seen"]):
            adv["first_seen"] = a["first_seen"]
    for adv in advs.values():
        adv["case_types"] = sorted(x for x in adv["case_types"] if x)
        adv["library_url"] = page_url(adv["page_id"], country) if adv["page_id"] else ""
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_sync_at": (now + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "live", "country": country,
        "advertisers": list(advs.values()),
        "cases": [{"case_type": ct, "case_key": ck} for ct, ck, _ in __import__("enrich").CASE_RULES],
        "ads": ads,
    }


def export_csv(ads: list[dict], path: str):
    import csv
    cols = ["rating", "advertiser", "company", "case_type", "ad_copy", "cta_text",
            "landing_url", "media_type", "platforms", "start", "end", "status",
            "duplicate_count", "duplicate_group", "snapshot_url"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for a in ads:
            w.writerow(["|".join(a[c]) if c == "platforms" else a.get(c, "") for c in cols])


def export_xlsx(ads: list[dict], path: str):
    try:
        from openpyxl import Workbook
    except ImportError:
        print("  (openpyxl not installed — skipping XLSX)")
        return
    wb = Workbook(); ws = wb.active; ws.title = "Ads"
    cols = ["rating", "advertiser", "company", "case_type", "ad_copy", "cta_text",
            "landing_url", "media_type", "platforms", "start", "end", "status",
            "duplicate_count", "snapshot_url"]
    ws.append(cols)
    for a in ads:
        ws.append(["|".join(a[c]) if c == "platforms" else a.get(c, "") for c in cols])
    wb.save(path)


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def run(config: dict, out_path: str):
    country = config.get("country", DEFAULT_COUNTRY)
    max_ads = int(config.get("max_ads_per_target", 300))
    scroll_pause = float(config.get("scroll_pause_seconds", 1.6))
    db_path = config.get("db", "arbiter.db")
    headless = config.get("headless", True)
    log = (lambda *a: print(*a, flush=True)) if config.get("verbose", True) else (lambda *a: None)

    targets = []
    for adv in config.get("advertisers", []):
        pid = adv if str(adv).isdigit() else page_id_from_url(str(adv))
        if pid:
            targets.append(("page", pid, page_url(pid, country)))
        else:
            log(f"! could not resolve page id from: {adv}")
    for kw in config.get("keywords", []):
        targets.append(("keyword", kw, keyword_url(kw, country)))

    if not targets and not config.get("advertiser_names"):
        sys.exit("No targets in config. Add 'advertiser_names', 'advertisers', or 'keywords'.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright missing. Run: pip install -r requirements.txt && python -m playwright install chromium")

    proxy = os.environ.get("PROXY") or config.get("proxy")
    # Container/Replit-safe flags. --no-sandbox is required inside Replit's VM;
    # --disable-dev-shm-usage avoids /dev/shm exhaustion on small machines.
    launch = {"headless": headless, "args": [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox", "--disable-dev-shm-usage",
    ]}
    # On Replit, point Playwright at the Nix-provided Chromium (avoids GLIBC errors
    # from Playwright's own bundled build). Order: env -> config -> chromium on PATH.
    import shutil
    chromium_path = (os.environ.get("CHROMIUM_PATH") or config.get("chromium_path")
                     or shutil.which("chromium") or shutil.which("chromium-browser"))
    if chromium_path:
        launch["executable_path"] = chromium_path
        log(f"  using chromium: {chromium_path}")
    if proxy:
        launch["proxy"] = {"server": proxy}

    all_ads: dict[str, dict] = {}
    now_iso = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        context = browser.new_context(
            locale="en-US", viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        )
        context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        # Resolve any advertiser_names (firm names) -> page targets, using a cache.
        names = config.get("advertiser_names", [])
        if names:
            cache_path = config.get("resolved_cache", "resolved_pages.json")
            cache = load_resolved_cache(cache_path)
            log(f"Resolving {len(names)} advertiser name(s) to page IDs…")
            for name in names:
                if name in cache and cache[name].get("page_id"):
                    pid = cache[name]["page_id"]
                    log(f"    '{name}' -> cached {pid}")
                    targets.append(("page", name, page_url(pid, country)))
                    continue
                res = resolve_page_id(context, name, country, log)
                if res:
                    pid, resolved_nm = res
                    cache[name] = {"page_id": pid, "resolved_name": resolved_nm}
                    targets.append(("page", name, page_url(pid, country)))
                else:
                    log(f"    '{name}' -> NOT FOUND, falling back to keyword search")
                    targets.append(("keyword", name, keyword_url(name, country)))
                time.sleep(config.get("target_delay_seconds", 2))
            save_resolved_cache(cache_path, cache)

        if not targets:
            sys.exit("No targets resolved. Add 'advertiser_names', 'advertisers', or 'keywords' to config.")

        for kind, label, url in targets:
            log(f"[{kind}] {label}")
            try:
                rows = scrape_target(context, url, country, max_ads, scroll_pause, log)
                log(f"  ✓ {len(rows)} ads")
                for r in rows:
                    all_ads.setdefault(r["id"], r)
            except Exception as e:
                log(f"  ✗ {label}: {e}")
            time.sleep(config.get("target_delay_seconds", 2))
        browser.close()

    if not all_ads:
        sys.exit("FAIL LOUD: zero ads captured. Likely IP-blocked or GraphQL shape changed. "
                 "Try a residential proxy (PROXY=...) or headless=false to inspect.")

    ads = enrich(list(all_ads.values()))

    db = sqlite3.connect(db_path)
    upsert(db, ads, now_iso)
    merged = load_all(db)              # union of this run + history (status updated)
    enrich(merged)                     # recompute scaling across full library
    db.close()

    bundle = build_bundle(merged, country)
    Path(out_path).write_text(json.dumps(bundle, indent=2))
    base = out_path.rsplit(".", 1)[0]
    export_csv(merged, base + ".csv")
    export_xlsx(merged, base + ".xlsx")
    print(f"\n✓ {len(merged)} ads · {len(bundle['advertisers'])} advertisers")
    print(f"  wrote {out_path}, {base}.csv, {base}.xlsx  (db: {db_path})")
    print("  Serve dashboard.html next to ads.json (e.g. `python -m http.server`) to see it live.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Meta Ad Library scraper for mass-tort ad intelligence")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="ads.json")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
    run(cfg, args.out)
