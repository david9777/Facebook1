# Free setup — no proxy, just your Replit subscription

**The split:** Replit hosts the always-on dashboard and never scrapes. Your own
computer (residential IP, which Meta accepts) does the scraping and pushes results
up to the dashboard. No proxy, no extra bills.

```
  ┌─────────────────────┐         POST /ingest          ┌──────────────────────┐
  │   YOUR COMPUTER      │  ───────────────────────────► │   REPLIT (Reserved VM)│
  │  run_local.py        │     (ads.json bundle)         │   app.py serves the   │
  │  scrapes Ad Library  │                               │   dashboard, always-on│
  │  (residential IP)    │                               │   <name>.replit.app   │
  └─────────────────────┘                               └──────────────────────┘
```

The boss opens `<name>.replit.app` anytime. Data refreshes whenever your machine
runs the scraper. The dashboard shows sample data until your first push, so it's
never empty.

---

## Part 1 — Replit (one time, ~5 min)

1. Upload the project to a Python Repl (rename `dotreplit.txt` → `.replit`).
2. **Secrets** (lock icon): add just one —
   | Key | Value |
   |---|---|
   | `SYNC_TOKEN` | any random string (e.g. a UUID) |

   Do **not** set `PROXY`. With no proxy, the server won't try to scrape (it would
   just get blocked); it only serves and accepts your pushes.
3. **Deploy → Reserved VM**, smallest machine, run command `python3 app.py`,
   pick a subdomain → Deploy. You get `https://<name>.replit.app`.
4. Open it — the dashboard loads with sample data. 

That's the hosting done. It costs only your Replit Core subscription.

---

## Part 2 — Your computer (one time, ~5 min)

Put the same project folder on your machine and install the scraper:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Edit `config.json` to list the cases/keywords you want (one keyword per tort —
see the case list section below).

Test a run and push it live:

```bash
python run_local.py --url https://YOUR-APP.replit.app --token YOUR_SYNC_TOKEN
```

You should see `Scraped N ads.` then `Dashboard updated: {'ok': True, ...}`.
Refresh the Replit URL — it's now showing live data. Done.

(Tip: set `REPLIT_URL` and `SYNC_TOKEN` as environment variables once, then you
can just run `python run_local.py`.)

---

## Part 3 — Automate the refresh (optional)

The scraper only runs when your computer is on. Schedule it to run every few hours
while you work. Pick your OS:

**macOS / Linux** — `crontab -e`, add (runs every 3 hours):
```cron
0 */3 * * * cd /full/path/to/arbiter && REPLIT_URL=https://YOUR-APP.replit.app SYNC_TOKEN=YOUR_TOKEN /usr/bin/python3 run_local.py >> sync.log 2>&1
```

**Windows** — PowerShell (creates a task every 3 hours):
```powershell
schtasks /create /tn "ArbiterSync" /sc hourly /mo 3 /tr "cmd /c cd /d C:\path\to\arbiter && set REPLIT_URL=https://YOUR-APP.replit.app && set SYNC_TOKEN=YOUR_TOKEN && python run_local.py >> sync.log 2>&1"
```

Runs are skipped while the machine is asleep/off — that's fine; the next run
catches up the full library. Want true 24/7 auto-refresh with the machine off?
That's the only thing a paid proxy (or an always-on home device like a Raspberry
Pi) buys you. Everything else here is free.

---

## Case list — track everything you want

Coverage is just keywords in `config.json`. Each keyword surfaces *every* firm
advertising on that case. To add a brand-new case type with its own tag + colour:
1. add keyword(s) to `config.json`
2. add a rule to `CASE_RULES` in `enrich.py`
3. add a colour in `CASE_COLORS` in `dashboard.html`

Tell me the full list of torts you want and I'll pre-fill all three.

---

## Quick checks
- `https://<name>.replit.app/status` → `"ok": true` and a non-zero ad count after
  your first push.
- Local scrape returns 0 ads even on home internet? Meta may have changed its
  GraphQL shape — send me the symptom and it's a quick parser fix.
