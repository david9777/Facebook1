# START HERE — Arbiter, end to end

Everything in one place: how to control what gets scraped, how to deploy, how to
run it for free, and how to use it day to day.

---

## 1. What controls what gets scraped — `config.json`

This file is the whole control panel. Open it and edit these:

```json
{
  "country": "US",                     // which country's ad delivery to pull
  "max_ads_per_target": 300,           // cap per keyword/advertiser (raise for more depth)
  "keywords": [ ... ],                 // broad searches — the main lever
  "advertisers": [ ]                   // specific firms by page ID — optional
}
```

### `keywords` — the main lever (catches everyone)
Each keyword is an Ad Library search. It pulls **every advertiser** running ads
that match — so it surfaces new firms the moment they enter a case. This is how
you "track all the cases": one keyword per tort.

```json
"keywords": ["camp lejeune", "roundup lawsuit", "ozempic lawsuit"]
```

Add a line for any new case. Remove lines for cases you don't care about. Quote
each entry, comma between them, no comma after the last one.

### `advertisers` — track a specific firm's whole library
When you want one firm's complete history (not just keyword hits), add its page
ID. To find it: open that advertiser in the Ad Library
(`facebook.com/ads/library`), look at the URL for `view_all_page_id=NNNNN`, and
paste either the number or the whole URL:

```json
"advertisers": ["123456789012345", "https://www.facebook.com/ads/library/?...view_all_page_id=999..."]
```

Leave it `[]` if you only want keyword tracking.

### Other knobs
- `country` — `"US"`, `"CA"`, `"GB"`, etc.
- `max_ads_per_target` — higher = deeper history per target, slower scrape.
- `scroll_pause_seconds` / `target_delay_seconds` — politeness throttles; leave as is.

After editing, save the file. That's it — the next scrape uses the new settings.

---

## 2. Deploy the dashboard to Replit (one time, ~5 min)

1. Create a **Python Repl**. Upload all the project files. Rename
   `dotreplit.txt` → `.replit` (Replit hides dotfiles in the uploader).
2. Open **Secrets** (lock icon) and add one secret:
   - `SYNC_TOKEN` = any random string (make one up, e.g. a long password). You'll
     reuse this exact value on your computer in step 3.
   - Do **not** set `PROXY`. (No proxy = the server won't try to scrape from its
     blocked datacenter IP; it just hosts and receives your data.)
3. Click **Deploy** → choose **Reserved VM** → smallest machine →
   run command `python3 app.py` → pick a subdomain → **Deploy**.
4. You now have `https://YOUR-APP.replit.app`. Open it — the dashboard loads with
   sample data. **That URL is what you send the boss.** It's always on.

Hosting is done. It costs only your Replit Core subscription.

---

## 3. Set up the scraper on your computer (one time, ~5 min)

Your home internet is a residential IP, which the Ad Library accepts — so the
scrape runs here for free. Replit just receives the results.

In the project folder on your computer:
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

(If `pip`/`python` aren't found, install Python 3.11+ from python.org first.)

---

## 4. Run it — scrape locally, push to Replit

```bash
python run_local.py --url https://YOUR-APP.replit.app --token YOUR_SYNC_TOKEN
```

- `--url` = your Replit app URL from step 2.
- `--token` = the **same** `SYNC_TOKEN` you set in Replit Secrets.

You'll see `Scraped N ads.` then `Dashboard updated`. Refresh the Replit URL —
live data. (Add `--no-push` to scrape without uploading while you test.)

### Make it refresh automatically every 3 hours
Your machine must be awake when it runs.

**macOS / Linux** — `crontab -e`, add:
```
0 */3 * * * cd /full/path/to/arbiter && python3 run_local.py --url https://YOUR-APP.replit.app --token YOUR_SYNC_TOKEN >> arbiter.log 2>&1
```

**Windows** — Task Scheduler → Create Basic Task → trigger Daily, then in the
task's Triggers set "Repeat every 3 hours" → Action "Start a program":
- Program: `python`
- Arguments: `run_local.py --url https://YOUR-APP.replit.app --token YOUR_SYNC_TOKEN`
- Start in: the full path to the project folder.

Got an always-on machine (old laptop, Raspberry Pi)? Put the cron job there for
effectively 24/7 freshness.

---

## 5. Using the dashboard day to day

- **Top KPIs** — tracked advertisers, active ads, new in 24h, case types, and
  *scaling creatives* (the brass tile: creatives a firm is running many copies of
  right now — where the budget is).
- **Case mix bar** — share of active ads by tort. Click a bar to filter to it.
- **Advertisers rail (left)** — click a firm to filter to its ads. Add a firm to
  track by pasting its Ad Library URL (it's picked up on the next scrape).
- **Filters** — case type, platform, status (active/inactive), and "Scaling only"
  to see just the creatives with budget behind them.
- **Ads table** — click any row for the full creative, copy, landing page, dates,
  and platforms. Star-rate ads (1–3) to mark the ones worth watching; ratings
  survive across syncs.
- **Search box** (top) — filter by ad copy or advertiser instantly.
- **Export CSV / XLSX** — exports exactly what's currently filtered.
- **Force a refresh now** (instead of waiting for the schedule): run
  `python run_local.py --url ... --token ...` again, or
  `curl -X POST "https://YOUR-APP.replit.app/sync?token=YOUR_SYNC_TOKEN"` only if
  you enabled server-side scraping.
- **Check freshness/health**: visit `https://YOUR-APP.replit.app/status`.

---

## 6. Adding a brand-new case type (full support)

Say a new tort shows up. To give it its own tag + color end to end:

1. **`config.json`** — add a keyword: `"new tort keyword"`.
2. **`enrich.py`** — add a rule to `CASE_RULES`:
   `("New Tort Name", "newtort", [r"keyword", r"another phrase"]),`
3. **`dashboard.html`** — add a color in `CASE_COLORS`:
   `"New Tort Name":"#hexcolor",`

Re-run `run_local.py`. New case is tracked, tagged, and colored.

(Tell me the torts you want and I'll do all three edits for you.)

---

## 7. If something's off

| Symptom | Cause / fix |
|---|---|
| Dashboard shows sample data only | No successful push yet. Run `run_local.py` and check it prints `Dashboard updated`. |
| `Scraped 0 ads` locally | Meta blocked even your home IP (rare) or copy didn't match. Try fewer keywords, rerun. |
| `Upload failed` | URL or token wrong. Token must match the Replit `SYNC_TOKEN` exactly. |
| `/status` shows `ok: false` | Read its `error`. Zero-ads = blocked; check you're running locally, not on Replit. |
| Scrape returns nothing on Replit directly | Expected — datacenter IP. Use the home runner (steps 3–4). |
| `chromium` not found locally | Run `python -m playwright install chromium` again. |

---

## TL;DR
1. Edit `config.json` keywords → controls what's scraped.
2. Deploy `app.py` to Replit Reserved VM, set `SYNC_TOKEN` → hosts the dashboard.
3. On your computer: `pip install -r requirements.txt`, `playwright install chromium`.
4. `python run_local.py --url <replit-url> --token <token>` → live data.
5. Cron it every 3h. Send the boss the `.replit.app` URL.
