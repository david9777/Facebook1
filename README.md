# Arbiter — Mass-Tort Ad Intelligence Desk

Tracks mass-tort / "arbitration" plaintiff-acquisition ad campaigns across Meta
(Facebook, Instagram, Messenger, Audience Network) by scraping the **public Ad
Library front-end**, then enriching the raw ads into a clean intelligence desk:
case-type tagging, duplicate/scaling detection, analyst ratings, and one-click
CSV/XLSX export.

Two pieces:

| File | What it is |
|---|---|
| `dashboard.html` | The desk. Self-contained — open it in any browser, zero setup. Ships with sample data so it demos instantly; auto-loads live `ads.json` when served next to one. |
| `scraper.py` | The engine. Pulls live ads from the Meta Ad Library and writes `ads.json` + `ads.csv` + `ads.xlsx`. |

---

## Why scrape the front-end instead of the official API

The official `ads_archive` Graph API only returns full results for **political /
social-issue** ads, and restricts general ads to **EU-targeted countries**.
Mass-tort legal ads in the US are *commercial* ads — so the official API barely
sees them. That's the coverage gap you hit.

The public Ad Library **website** shows all of them. The site loads ad data from
an internal `/api/graphql/` endpoint. `scraper.py` drives a real browser to the
Ad Library, intercepts those GraphQL responses, and parses them — the same
mechanism the commercial scrapers use. No login, no token.

---

## Quick start (demo — no scraping)

Just open `dashboard.html`. It renders the sample dataset so you can present
immediately. Nothing else required.

## Live data

```bash
pip install -r requirements.txt
python -m playwright install chromium

# 1. Edit config.json — add advertiser page IDs and/or keywords
# 2. Run the scrape
python scraper.py --config config.json --out ads.json

# 3. Serve the dashboard next to ads.json so it loads live data
python -m http.server 8000
# open http://localhost:8000/dashboard.html
```

`scraper.py` writes `ads.json` (dashboard), `ads.csv`, `ads.xlsx`, and keeps a
running history in `arbiter.db` (SQLite) so `first_seen` / `last_seen` and your
star ratings survive across syncs.

### Finding an advertiser's page ID
Open the advertiser in the Ad Library. The URL contains `view_all_page_id=NNNN`.
Paste that number — or the whole URL — into `advertisers` in `config.json`.
Keywords need no setup and automatically surface **new** firms the moment they
start advertising on a case.

---

## Auto-refresh every 3 hours

The original site refreshes every 3h. Match it with cron:

```cron
0 */3 * * * cd /path/to/arbiter && /usr/bin/python3 scraper.py --config config.json --out ads.json >> sync.log 2>&1
```

The dashboard's "next sync" countdown reads `next_sync_at` from `ads.json`, so it
stays honest about freshness.

---

## What gets captured per ad

`advertiser`, `company`, `ad_copy`, `cta_text`, `landing_url`, `media_type`,
`platforms`, `start` / `end`, `status` (active/inactive), `snapshot_url` — plus
the enrichment the API can't give you:

- **Case type** — `enrich.py` tags each ad with its mass-tort campaign by keyword
  rules. Extend `CASE_RULES` to add cases; chip colours live in `dashboard.html`.
- **Scaling signal** — near-identical creatives are grouped and counted by
  *concurrent active* variants. 3+ live copies of one creative = budget behind it.

---

## Honest engineering caveats

- **Terms of Service.** Automated access violates Meta's ToS. Scraping *public*
  data is not a CFAA crime (per *hiQ v. LinkedIn*), but it carries civil-liability
  risk. For production use, clear it with counsel.
- **Fragility.** When Meta reshapes the GraphQL envelope, the parser may need a
  tweak. The scraper **fails loud** (exits non-zero) rather than silently
  returning half your cases — so a broken sync is obvious, not invisible.
- **Proxies.** Datacenter IPs get WAF-blocked quickly. At any real volume use
  residential/mobile proxies: `PROXY=http://user:pass@host:port python scraper.py`.
  Set `headless: false` in config to watch a run and debug blocks.
- **Be polite.** `scroll_pause_seconds` and `target_delay_seconds` throttle the
  crawl. Don't drop them to zero.

---

## Files

```
dashboard.html   self-contained desk (sample data baked in; loads ads.json if present)
scraper.py       Playwright GraphQL-interception scraper -> ads.json/csv/xlsx + SQLite
enrich.py        case-type classification + duplicate/scaling detection (unit-tested)
config.json      advertisers, keywords, country, proxy, throttle
requirements.txt  playwright, openpyxl
make_sample.py   regenerates the demo dataset (sample_ads.json)
```
