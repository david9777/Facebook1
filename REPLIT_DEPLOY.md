# Deploying Arbiter on Replit (Core plan)

One **Reserved VM** runs a single process (`app.py`) that serves the dashboard
*and* scrapes the Ad Library every 3 hours. The dashboard shows sample data
immediately and switches to live data after the first successful scrape.

---

## Before you start — the one hard requirement

**You need a residential/mobile proxy.** Replit's Reserved VM has a datacenter
IP, and Meta's WAF blocks those from the Ad Library within a few requests.
Without a proxy the scraper will run, fail loud, and the dashboard will keep
showing sample data. With a proxy it pulls live ads. Sign up with any residential
proxy provider and grab a URL shaped like:

```
http://USERNAME:PASSWORD@gateway.yourprovider.com:7777
```

(Tell me your provider and I'll give you the exact gateway/rotation settings.)

---

## Steps

### 1. Get the files into a Repl
Create a new Python Repl, then upload all the project files (or push this folder
to GitHub and "Import from GitHub"). You should have:

```
app.py  scraper.py  enrich.py  dashboard.html
config.json  requirements.txt  .replit  replit.nix
sample_ads.json  make_sample.py
```

`.replit` and `replit.nix` are already configured — they pin Python 3.11, pull in
Nix Chromium (so Playwright runs without the GLIBC errors its bundled browser
hits on Replit), and set the run command to `python3 app.py`.

### 2. Add your Secrets
Open the **Secrets** tab (lock icon) and add:

| Key | Value | Required? |
|---|---|---|
| `PROXY` | your residential proxy URL | **Yes** — or you get sample data only |
| `SYNC_TOKEN` | any random string | Yes — protects the manual `/sync` button |
| `SYNC_INTERVAL_HOURS` | `3` | Optional (default 3) |

`CHROMIUM_PATH` is auto-detected from the Nix install — don't set it unless a run
log says Chromium wasn't found.

### 3. Pick the cases you want to track
Edit `config.json`. Two ways to add coverage:

- **`keywords`** — broad terms like `"camp lejeune"`, `"ozempic lawsuit"`. These
  surface *every* firm advertising on that case, including new entrants. This is
  how you "track all the cases" — add a keyword per tort.
- **`advertisers`** — specific firms by page ID, when you want one advertiser's
  full history. Open the firm in the Ad Library; the URL has `view_all_page_id=NNNN`
  — paste that number.

To add a brand-new case type end-to-end (so it gets its own tag + colour):
1. add keyword(s) to `config.json`,
2. add a rule to `CASE_RULES` in `enrich.py`,
3. add a colour for it in `CASE_COLORS` in `dashboard.html`.

### 4. Test in the workspace first
Hit **Run**. Watch the console:
- `[scheduler] scraping every 3.0h` and `[web] serving on 0.0.0.0:8080`
- ~20s later the first scrape fires. With a working proxy: `[sync] ok — N ads`.
  Without one: `[sync] FAILED — zero ads captured…` (expected — add the proxy).

Open the webview — the dashboard loads. Visit `/status` to see last-sync result
and `/healthz` for uptime.

### 5. Deploy as a Reserved VM
1. Click **Deploy** (top right) → choose **Reserved VM**.
2. Machine size: the smallest (0.25 vCPU / 1 GiB) is enough; bump RAM to 2 GiB if
   Chromium feels tight.
3. Build command: `pip install -r requirements.txt`
   Run command: `python3 app.py`
4. Pick a subdomain → **Deploy**. You'll get `https://<name>.replit.app`.
5. (Optional) add a custom domain in the deployment's Settings.

That URL is what you send the boss. It's always-on, refreshes itself every 3
hours, and you can force a refresh anytime:

```
curl -X POST "https://<name>.replit.app/sync?token=YOUR_SYNC_TOKEN"
```

---

## Verifying it's actually pulling live data
- `/status` shows `"ok": true` and a non-zero `ads` count.
- The dashboard's data source flips from sample to live (the "next sync"
  countdown reflects the real `next_sync_at`).
- If `/status` shows `ok: false` with a block/zero-ads error → it's the proxy.
  Confirm `PROXY` is set and the proxy has US residential IPs.

## Cost sketch (Core)
Reserved VM (smallest) runs ~$0.0000XX/sec ≈ a few dollars a month always-on,
plus your proxy plan (~$15–50/mo) and minimal outbound transfer. The scrape is
light; the proxy is the real line item.

## When it breaks
Meta reshapes its GraphQL periodically. The scraper **fails loud** (you'll see it
in `/status` and the deploy logs) instead of silently dropping cases. When that
happens the parser in `scraper.py` (`extract_ad_nodes` / `normalize_node`) needs a
small update — send me the new response shape and it's a quick fix.
