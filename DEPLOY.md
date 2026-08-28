# Deploying BTC Desk

One-time setup, ~10 minutes. End state: your dashboard at
`https://YOURNAME.github.io/btc-desk/`, refreshing its own data every hour
with nothing left running on your machine.

How it works: GitHub **Pages** serves the static site; a GitHub **Action**
(already included at `.github/workflows/update-data.yml`) runs `pipeline.py`
hourly and commits the refreshed `data/metrics.json`; every commit redeploys
Pages automatically.

---

## Step 0 — Prerequisites

- A GitHub account (free) — github.com
- Git on your machine. Check with `git --version` in Terminal; on macOS,
  accept the prompt to install the command-line tools if asked.

## Step 1 — Unzip

Unzip `btc-desk.zip` anywhere (e.g. your home folder). Everything below
happens inside the unzipped `btc-desk` folder. The hidden `.github` folder is
already inside — press ⌘⇧. in Finder if you want to see it.

## Step 2 — Turn the folder into a git repo

```bash
cd ~/btc-desk          # or wherever you unzipped it
git init -b main
git add .
git commit -m "BTC Desk"
```

`git status` should now say "nothing to commit, working tree clean".

## Step 3 — Create the GitHub repo and push

1. Go to **github.com/new** → Repository name: `btc-desk` → visibility:
   **Public** (Pages on the free plan requires a public repo; nothing in
   these files is sensitive — API keys go into Secrets in Step 4, never into
   the repo) → do **not** add a README → Create repository.
2. Back in Terminal:

```bash
git remote add origin https://github.com/YOURNAME/btc-desk.git
git push -u origin main
```

Git walks you through browser sign-in the first time.

### No-Terminal alternative to Steps 2–3 (browser only)

You can skip git entirely and upload through github.com — with two traps:

1. Create the repo as in Step 3.1, then on the empty-repo page click
   **"uploading an existing file"**.
2. In Finder, open the unzipped folder, press **⌘⇧.** to reveal hidden
   files, select **everything inside the folder** (including `.github`) and
   drag it into the upload box → Commit. Drag the *contents*, never the
   folder itself — a nested `btc-desk/` root breaks the workflow and the
   Pages URL.
3. **Verify** `.github/workflows/update-data.yml` exists in the repo —
   folder drags sometimes drop dotfiles silently. If it's missing:
   **Add file → Create new file**, type `.github/workflows/update-data.yml`
   as the name (the slashes create the folders), paste the file's contents,
   Commit. `.gitignore` and `data/.gitkeep` are safe to omit.

Everything from Step 4 on is identical, and later edits can be made directly
on github.com. Only the local-cron option below requires git on your machine.

## Step 4 — Add API keys (optional, skip freely)

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `FRED_API_KEY` — free key from fred.stlouisfed.org; enables the M2 chart.
- `GLASSNODE_API_KEY` — if you have one; enables on-chain history.

Without them, those feeds are simply logged as skipped.

## Step 5 — Seed the data

Repo → **Actions** tab → enable workflows if prompted → select
**update data** → **Run workflow**. It finishes in about a minute and pushes
a commit named `data: 2026-…` — that's your first `data/metrics.json`. From
now on it re-runs itself every hour.

## Step 6 — Turn on Pages

Repo → **Settings → Pages** → Source: **Deploy from a branch** → Branch
`main`, folder `/ (root)` → Save. About a minute later the page shows your
live URL.

## Step 7 — Verify

Open the URL. Within a few seconds: price fills in and updates every 15s,
sparklines fade in, and the header badge reads **CACHE: LIVE**. If it still
says SAMPLE, hard-refresh (⌘⇧R) — Pages caches for a few minutes.

## Step 8 — Custom domain (optional)

Settings → Pages → Custom domain → enter it → at your DNS provider add a
CNAME record pointing to `YOURNAME.github.io` → once the check passes, tick
**Enforce HTTPS**.

---

## Updating the site later

- **Weekly manual numbers** (on-chain values, MSTR 8-K figures): edit
  `manual_overrides.json` — easiest is directly on github.com (open the file
  → pencil icon → commit). The next hourly run bakes your edits into
  `metrics.json`, and each edit also becomes a dated point in that metric's
  history chart. To apply immediately, run the **update data** workflow
  manually after committing.
- **Code changes** (index.html, pipeline.py): commit and push; Pages
  redeploys on every push.

## Known caveats (by design, already handled)

- **GitHub's runners have US datacenter IPs.** Binance returns 451 from the
  US and Farside's bot protection sometimes 403s. Check any run's log: each
  feed reports `[pipeline] … failed` individually and everything else still
  updates. Visitors' browsers fetch the Binance-backed metrics live
  regardless, so the dashboard stays complete.
- **Full pipeline coverage instead:** run it from your own machine (a
  Canadian residential IP passes everything) with this cron entry
  (`crontab -e`):

```
17 * * * * cd $HOME/btc-desk && python3 pipeline.py && git add data/metrics.json && (git diff --cached --quiet || (git commit -qm data && git push -q)) >> pipeline.log 2>&1
```

  Runs are skipped while the machine sleeps; the GitHub Action covers those
  hours, and whichever pushed last wins.
- **Schedule drift:** GitHub may run the hourly job a few minutes late, and
  pauses schedules on repos inactive for ~60 days — the job's own commits
  count as activity, so it keeps itself alive.

## Quick throwaway preview (no pipeline)

Drag the folder onto **app.netlify.com/drop** for an instant URL. Nothing
runs the pipeline there, so the cached layer stays on sample figures — fine
for showing someone, not for daily use.
