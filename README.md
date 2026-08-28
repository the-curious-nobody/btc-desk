# BTC Desk

A single-page Bitcoin operating dashboard. Static files, no build step, no
frameworks. Every metric shows its current reading, and most carry their
history — a sparkline on the card, tap it for a full chart with 3M / 1Y / MAX
ranges and a crosshair readout.

```
btc-desk/
├── index.html            the dashboard (open it, or serve the folder)
├── pipeline.py           refreshes data/metrics.json (values + history)
├── manual_overrides.json weekly hand-edits; wins over everything
└── data/
    └── metrics.json      written by pipeline.py; read by the page
```

## Sections

| Section | Cards |
|---|---|
| Market (hero) | spot price + regime, hashrate, halving countdown, Fear & Greed, cycle composite |
| Trend | Mayer / 200DMA / momentum, 200-week MA + balanced price, ahr999 |
| Supply & Flows | exchange reserves, long-term holders, spot ETF flows, cost-basis structure (STH + URPD), stablecoins & SSR |
| Derivatives | perp funding, basis / DVOL / 25Δ risk reversal |
| Cycle | valuation oscillators (MVRV, NUPL, SOPR, Puell…), composite gauge, prediction-market 2026 range, attention & Coinbase premium |
| Macro | US M2 YoY, BTC correlations vs GLD / QQQ |
| Miner Economics | shutdown-price table with an electricity slider |
| Strategy / MSTR | mNAV & holdings, financing runway (from 8-K disclosures) |

## Two data layers

**Live (in the browser, no backend needed).** Fetched on load with graceful
fallback: price (CoinGecko → Binance), ~2.7y of daily closes (Binance klines →
CoinGecko 365d) from which the page computes the 200DMA, Mayer, momentum, the
bear-regime badge and ahr999 — plus their full history; weekly closes → live
200-week MA and its series; block height, halving countdown and 1y hashrate
(mempool.space); Fear & Greed with its full history since 2018 (alternative.me); stablecoin
market cap history and SSR (DeFiLlama + CoinGecko); funding with ~1y history
(Binance perp); DVOL 1y and nearest-future basis (Deribit).

**Cached (written by `pipeline.py`).** Everything the browser can't get for
free with CORS: ETF flows (Farside full table → daily + cumulative history),
the Polymarket → PAVA → PDF 2026 range, FRED M2 YoY series, GLD/QQQ
correlation windows + rolling 90d series, Google Trends, a Fear & Greed history fallback (used for the hero reading too if the live API is blocked), and the on-chain set
(MVRV family, reserves, LTH, STH cost basis, URPD) via a Glassnode key or your
weekly manual edits. The header badge flips `CACHE: SAMPLE → CACHE: LIVE` once
`data/metrics.json` exists. Until then the page renders stamped sample figures
for the cached layer.

## History

`metrics.json` carries `"history": { key: [["YYYY-MM-DD", value], …] }`.

- Series the pipeline can recompute in full (ETF flows, funding, 200WMA, SSR,
  M2, rolling correlations, Trends, Glassnode metrics with a key) are
  **replaced** each run.
- Metrics known only as a level (manual on-chain numbers, Polymarket
  probabilities, Coinbase premium) get **one snapshot per run day**, deduped —
  so charts of those build themselves from whatever you enter over the weeks.
  They record what the desk showed on each date; a manual value repeats until
  you edit it.
- `manual_overrides.json` may include its own `"history"` block, which wins.
  Use it to paste a series you exported elsewhere (add a `_src` note).

The browser merges cached history with the series it computes itself
(price, 200DMA overlay, Mayer, ahr999, 200WMA, F&G, hashrate, funding, DVOL,
stablecoins, SSR). Sparklines only appear when a real series exists — nothing
is interpolated or back-filled.

## Running the pipeline

```bash
pip install requests              # required
pip install pytrends yfinance     # optional (Trends, MSTR/STRC quotes)
export FRED_API_KEY=...           # optional (M2)
export GLASSNODE_API_KEY=...      # optional (on-chain history)
python3 pipeline.py               # writes data/metrics.json
```

Cron: hourly is fine — the script is cheap, and every run also appends to the
snapshot histories.

```
17 * * * * cd /path/to/btc-desk && /usr/bin/python3 pipeline.py >> pipeline.log 2>&1
```

Serve statically (`python3 -m http.server`, nginx, GitHub Pages, anything).
Opening `index.html` via `file://` also works; the cached layer then shows the
embedded sample until served over HTTP.

## Composite mapping

Cycle composite = equal-weight mean of 7 inputs normalized to 0–1, ×100:
MVRV-Z over −0.5→5, NUPL over −0.25→0.75, SOPR over 0.94→1.06, log-Puell over
0.3→4, supply-in-profit over 40→99%, Mayer over 0.6→2.4, Fear&Greed ÷100.
Bands: <20 capitulation, <40 accumulation, <60 neutral, <80 heated, ≥80
euphoria.

## Miner table

Shutdown price = daily electricity cost ÷ daily BTC mined at current network
hashrate, per rig at nameplate specs; fees excluded (real shutdown prices run
slightly lower). The electricity slider recomputes the whole table.

## Deploy (GitHub Pages + Actions)

The repo ships with `.github/workflows/update-data.yml`: an hourly job that
runs `pipeline.py` on GitHub's runners and commits `data/metrics.json`; every
commit redeploys Pages automatically. Setup: push this folder to a **public**
repo (Pages on the free plan requires public) → optionally add `FRED_API_KEY`
/ `GLASSNODE_API_KEY` under Settings → Secrets and variables → Actions → run
the workflow once from the Actions tab → Settings → Pages → “Deploy from a
branch” → `main` / root.

GitHub's US datacenter IPs are refused by some feeds (Binance returns 451
there; Farside's bot protection sometimes 403s). The pipeline logs each
failure and carries the previous cache forward, and visitors' browsers fetch
the Binance-backed metrics live regardless. For full pipeline coverage, run
`pipeline.py` on your own machine via cron and `git push` — Pages redeploys on
every push.

## Notes & limits

- Farside is an HTML scrape and will eventually break; the pipeline fails
  loudly and the previous cache (with its history) is carried forward.
- Binance endpoints are geo-restricted in some regions; the page falls back to
  CoinGecko (shorter history) automatically.
- The in-chat / sandboxed preview blocks network calls — live tiles show “—”
  there but work locally and deployed.
- US M2 stands in for global M2 and is labeled as such.
- Nothing here is investment advice.
