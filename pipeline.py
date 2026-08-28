#!/usr/bin/env python3
"""
BTC Desk — cache pipeline
Writes data/metrics.json for index.html: current values + history series.

Layers
------
FREE, fully automated here:
  * ETF net flows        — Farside (HTML scrape; fragile by nature, fails loudly)
                           full daily table -> history + cumulative
  * Funding rate history — Binance USDS-M public API (30d avg, streak, daily series)
  * Polymarket PDF       — gamma API -> survival function -> PAVA -> discrete PDF
                           (method per Fed FEDS 2026-010, Diercks/Katz/Wright)
  * Stablecoin SSR       — DeFiLlama charts + CoinGecko mcap (level + series)
  * 200-week MA          — Binance weekly klines (level + series)
  * Correlations         — BTC vs GLD/QQQ via stooq.com CSVs (windows + rolling 90d)
  * M2 YoY               — FRED (free FRED_API_KEY; US M2 as proxy, labeled)
  * Fear & Greed         — alternative.me full history (fallback for the live layer)
  * Google Trends        — pytrends if installed (level + 12m weekly series)

VENDOR-GATED (GLASSNODE_API_KEY) or MANUAL weekly via manual_overrides.json:
  * exchange reserves & netflow, LTH supply / net position change,
    MVRV / MVRV-Z / NUPL / SOPR / Puell / supply-in-profit,
    STH cost basis, URPD clusters, balanced price, Coinbase premium.
  With a key, ~400 days of history are pulled per metric.
  Without one, each run APPENDS today's manual value to history, so the
  dashboard's charts build themselves from your weekly edits.

STRATEGY / MSTR:
  * quotes via yfinance if installed; 8-K facts are manual_overrides
    (source: SEC EDGAR — self-binding disclosures).

History
-------
metrics.json carries {"history": {key: [["YYYY-MM-DD", value], ...]}}.
Keys match the dashboard: price-side series are computed in the browser;
this file supplies etf_flow, funding, wma200, wma200x, ssr, stables(opt),
m2_yoy, corr_gld, corr_qqq, gtrends, fng, and the on-chain keys
(mvrv, mvrv_z, nupl, sopr, puell, reserves, lth, sth_cb, cb_prem,
poly_p100, poly_p50). Series are replaced when recomputed in full and
appended (one point per day, deduped) when only a level is known.
manual_overrides.json may include its own "history" block, which wins.

Usage
-----
  pip install requests            # required
  pip install pytrends yfinance   # optional
  export FRED_API_KEY=...         # optional
  export GLASSNODE_API_KEY=...    # optional
  python3 pipeline.py             # writes data/metrics.json

Cron suggestion: hourly. Cheap; every run also grows the history file.
"""
from __future__ import annotations
import json, math, os, re, sys, datetime as dt
from pathlib import Path

import requests

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "metrics.json"
OVERRIDES = ROOT / "manual_overrides.json"
UA = {"User-Agent": "btc-desk/2.0 (personal research dashboard)"}
TIMEOUT = 25
TODAY = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
GENESIS = dt.date(2009, 1, 3)


def redact(s) -> str:
    """Strip API keys from anything we print. GitHub masks exact secret values
    in Actions logs, but public-repo logs should never depend on one layer:
    request errors embed the full URL, and both FRED and Glassnode carry the
    key as a query parameter."""
    return re.sub(r"(api_key=)[^&\s'\"]+", r"\1REDACTED", str(s))


def log(msg: str) -> None:
    print(f"[pipeline] {redact(msg)}", file=sys.stderr)


def get(url: str, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ----------------------------------------------------------------- ETF flows
def etf_flows() -> tuple[dict, dict] | None:
    """Scrape Farside's daily flow table (full history). HTML scraping is
    inherently fragile: on failure we return None and the previous cache,
    including its history, is carried forward."""
    try:
        html = get("https://farside.co.uk/btc/").text
        rows = re.findall(
            r"<tr[^>]*>\s*<td[^>]*>(\d{1,2}\s+\w{3}\s+\d{4})</td>(.*?)</tr>",
            html, re.S)
        daily = []
        for date, cells in rows:
            # capture the optional "(" per cell so negatives (parenthesized) are
            # signed by their own cell, not by a lookalike elsewhere in the row
            nums = re.findall(r"<td[^>]*>\s*(\()?\s*(-?[\d,]+\.?\d*)\s*\)?\s*</td>", cells)
            if not nums:
                continue
            paren, raw = nums[-1]
            total = float(raw.replace(",", ""))
            if paren:
                total = -abs(total)
            try:
                iso = dt.datetime.strptime(date, "%d %b %Y").strftime("%Y-%m-%d")
            except ValueError:
                continue
            daily.append((iso, total * 1e6))  # table is in US$m
        if not daily:
            return None
        daily.sort()
        vals = [v for _, v in daily]
        last = vals[-1]
        five = sum(vals[-5:])
        streak, sign = 0, math.copysign(1, vals[-1] or 1)
        for v in reversed(vals):
            if v == 0 or math.copysign(1, v) != sign:
                break
            streak += 1
        scalars = {"etf_flow_last_usd": last, "etf_flow_5d_usd": five,
                   "etf_streak_days": int(streak * sign)}
        # only claim "cumulative since launch" when the table reaches back to
        # the Jan 2024 launch; a partial table must not masquerade as the total
        if daily[0][0] <= "2024-01-16":
            scalars["etf_cum_usd"] = sum(vals)
        else:
            log(f"farside table partial (starts {daily[0][0]}) — cumulative not updated")
        return scalars, {"etf_flow": [[d, v] for d, v in daily]}
    except Exception as e:
        log(f"farside failed: {e}")
        return None


# ------------------------------------------------------------------- funding
def funding() -> tuple[dict, dict] | None:
    """Binance perp funding history -> last/30d avg/streak + daily ann. series."""
    try:
        j = get("https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "limit": 1000}).json()
        pts = [(int(x["fundingTime"]), float(x["fundingRate"])) for x in j]
        if not pts:
            return None
        pts.sort()
        rates = [r for _, r in pts]
        last_ann = rates[-1] * 3 * 365 * 100
        avg30 = sum(rates[-90:]) / min(90, len(rates)) * 3 * 365 * 100
        streak = 0
        for r in reversed(rates):
            if r < 0:
                streak += 1
            else:
                break
        by_day: dict[str, list[float]] = {}
        for t, r in pts:
            d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            by_day.setdefault(d, []).append(r)
        series = [[d, sum(v) / len(v) * 3 * 365 * 100] for d, v in sorted(by_day.items())]
        return ({"funding_last_ann_pct": last_ann,
                 "funding_30d_ann_pct": avg30,
                 "funding_neg_streak_days": streak // 3},
                {"funding": series})
    except Exception as e:
        log(f"binance funding failed: {e}")
        return None


# ------------------------------------------------- Polymarket -> PDF (PAVA)
def pava_isotonic_decreasing(y: list[float]) -> list[float]:
    """Pool-adjacent-violators for a non-increasing sequence."""
    vals = [[v, 1.0] for v in y]
    i = 0
    while i < len(vals) - 1:
        if vals[i][0] < vals[i + 1][0] - 1e-12:
            v = (vals[i][0] * vals[i][1] + vals[i + 1][0] * vals[i + 1][1]) / (vals[i][1] + vals[i + 1][1])
            vals[i] = [v, vals[i][1] + vals[i + 1][1]]
            del vals[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for v, w in vals:
        out += [v] * int(w)
    return out


def polymarket() -> dict | None:
    """Fetch 'what price will bitcoin hit before 2027' markets, build the
    market-implied survival function, monotonize with PAVA, difference to a PDF.
    Method follows Fed FEDS 2026-010 (public code: jdkatz21/Prediction_Markets_Public)."""
    try:
        j = get("https://gamma-api.polymarket.com/events",
                params={"slug": "what-price-will-bitcoin-hit-before-2027"}).json()
        if not j:
            return None
        markets = j[0].get("markets", [])
        ups, downs, vol = [], [], 0.0
        for m in markets:
            q = (m.get("question") or "")
            vol += float(m.get("volumeNum") or 0)
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            if not prices:
                continue
            yes = float(prices[0])
            k = re.search(r"\$([\d,.]+)\s*([KkMm]?)", q)
            if not k:
                continue
            level = float(k.group(1).replace(",", ""))
            level *= {"k": 1e3, "m": 1e6}.get(k.group(2).lower(), 1)
            if re.search(r"reach|hit|above|≥", q, re.I):
                ups.append((level, yes))
            elif re.search(r"dip|fall|below|≤", q, re.I):
                downs.append((level, yes))
        ups.sort()
        if len(ups) < 4:
            return None
        levels = [l for l, _ in ups]
        surv = pava_isotonic_decreasing([p for _, p in ups])
        pdf = [max(0.0, surv[i] - surv[i + 1]) for i in range(len(surv) - 1)]
        tot = sum(pdf) or 1.0
        pdf = [p / tot for p in pdf]
        mode = levels[max(range(len(pdf)), key=lambda i: pdf[i])]
        cum, p10, p90 = 0.0, levels[0], levels[-1]
        for i, p in enumerate(pdf):
            c0, cum = cum, cum + p
            if c0 < 0.10 <= cum:
                p10 = levels[i]
            if c0 < 0.90 <= cum:
                p90 = levels[i]
        def at(level_list, target):
            best = min(level_list, key=lambda lp: abs(lp[0] - target), default=None)
            return best[1] if best and abs(best[0] - target) < target * 0.02 else None
        out = {"poly_mode_usd": mode, "poly_p10_usd": p10, "poly_p90_usd": p90,
               "poly_volume_usd": vol}
        up100 = at(ups, 100_000)
        dn50 = at(sorted(downs), 50_000)
        if up100 is not None:
            out["poly_p_ge_100k"] = up100
        if dn50 is not None:
            out["poly_p_le_50k"] = dn50
        return out
    except Exception as e:
        log(f"polymarket failed: {e}")
        return None


# ------------------------------------------------------------ SSR / stables
def ssr() -> tuple[dict, dict] | None:
    """SSR level + one-year series from DeFiLlama charts and CoinGecko mcap."""
    try:
        charts = get("https://stablecoins.llama.fi/stablecoincharts/all").json()
        stab = {}
        for x in charts:
            v = (x.get("totalCirculatingUSD") or {}).get("peggedUSD")
            if v:
                stab[int(x["date"]) // 86400] = v
        cg = get("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
                 params={"vs_currency": "usd", "days": 365, "interval": "daily"}).json()
        series = []
        for ms, cap in cg.get("market_caps", []):
            day = int(ms) // 1000 // 86400
            st = stab.get(day) or stab.get(day - 1)
            if st:
                iso = dt.datetime.fromtimestamp(day * 86400, dt.timezone.utc).strftime("%Y-%m-%d")
                series.append([iso, cap / st])
        if not series:
            return None
        stables = [[dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc)
                    .strftime("%Y-%m-%d"), v] for d, v in sorted(stab.items())]
        return {"ssr": series[-1][1]}, {"ssr": series, "stables": stables[-2600:]}
    except Exception as e:
        log(f"ssr failed: {e}")
        return None


# ------------------------------------------------------- 200WMA (full hist)
def wma200() -> tuple[dict, dict] | None:
    try:
        j = get("https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1w", "limit": 1000}).json()
        wk = [(int(k[0]), float(k[4])) for k in j]
        if len(wk) < 210:
            return None
        cs = [0.0]
        for _, c in wk:
            cs.append(cs[-1] + c)
        wS, xS = [], []
        for i in range(199, len(wk)):
            m = (cs[i + 1] - cs[i - 199]) / 200
            iso = dt.datetime.fromtimestamp(wk[i][0] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            wS.append([iso, m])
            xS.append([iso, wk[i][1] / m])
        return {"wma200_usd": wS[-1][1]}, {"wma200": wS, "wma200x": xS}
    except Exception as e:
        log(f"wma200 failed: {e}")
        return None


# --------------------------------------------------------------- correlations
def correlations() -> tuple[dict, dict] | None:
    """Pearson corr of daily log returns, BTC vs GLD / QQQ: fixed windows for
    the table + rolling 90-day series for the charts."""
    try:
        def stooq(sym):
            txt = get(f"https://stooq.com/q/d/l/?s={sym}&i=d").text.strip().splitlines()[1:]
            out = {}
            for line in txt:
                p = line.split(",")
                if len(p) >= 5 and p[4]:
                    out[p[0]] = float(p[4])
            return out
        gld, qqq = stooq("gld.us"), stooq("qqq.us")
        j = get("https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}).json()
        btc = {dt.datetime.fromtimestamp(k[0] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"):
               float(k[4]) for k in j}

        def corr(a, b):
            n = len(a)
            if n < 10:
                return None
            ma, mb = sum(a) / n, sum(b) / n
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            va = math.sqrt(sum((x - ma) ** 2 for x in a))
            vb = math.sqrt(sum((y - mb) ** 2 for y in b))
            return cov / (va * vb) if va and vb else None

        table, hist = {}, {}
        for name, px in (("gld", gld), ("qqq", qqq)):
            ds = sorted(set(btc) & set(px))[-560:]
            rb = [math.log(btc[ds[i + 1]] / btc[ds[i]]) for i in range(len(ds) - 1)]
            ra = [math.log(px[ds[i + 1]] / px[ds[i]]) for i in range(len(ds) - 1)]
            table[name] = {}
            for label, days in (("d30", 30), ("d90", 90), ("d180", 180), ("d365", 365)):
                c = corr(rb[-days:], ra[-days:])
                if c is not None:
                    table[name][label] = round(c, 2)
            roll = []
            for i in range(90, len(rb) + 1):
                c = corr(rb[i - 90:i], ra[i - 90:i])
                if c is not None:
                    roll.append([ds[i], round(c, 3)])
            hist["corr_" + name] = roll
        return {"corr": table}, hist
    except Exception as e:
        log(f"correlations failed: {e}")
        return None


# --------------------------------------------------------------------- FRED
def m2() -> tuple[dict, dict] | None:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        return None
    try:
        j = get("https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": "M2SL", "api_key": key, "file_type": "json",
                        "sort_order": "asc", "observation_start": "2010-01-01"}).json()
        obs = [(o["date"], float(o["value"])) for o in j["observations"] if o["value"] != "."]
        if len(obs) < 13:
            return None
        series = [[obs[i][0], (obs[i][1] / obs[i - 12][1] - 1) * 100]
                  for i in range(12, len(obs))]
        return {"m2_yoy_pct": series[-1][1]}, {"m2_yoy": series}
    except Exception as e:
        log(f"fred failed: {e}")
        return None


# ------------------------------------------------------------ Fear & Greed
def fng() -> tuple[dict, dict] | None:
    """Full Fear & Greed history (alternative.me, daily since 2018-02).
    Cached fallback: the browser fetches this live, but if that call is
    blocked the dashboard falls back to this series for both the chart
    and the hero reading."""
    try:
        j = get("https://api.alternative.me/fng/",
                params={"limit": 0, "format": "json"}).json()
        by_day = {}
        for x in j.get("data") or []:
            iso = dt.datetime.fromtimestamp(int(x["timestamp"]), dt.timezone.utc) \
                    .strftime("%Y-%m-%d")
            by_day[iso] = int(x["value"])
        if not by_day:
            return None
        return {}, {"fng": [[d, v] for d, v in sorted(by_day.items())]}
    except Exception as e:
        log(f"fng failed: {e}")
        return None


# ------------------------------------------------------------ Google Trends
def gtrends() -> tuple[dict, dict] | None:
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=0)
        pt.build_payload(["bitcoin"], timeframe="today 12-m")
        df = pt.interest_over_time()
        series = [[ts.strftime("%Y-%m-%d"), int(v)]
                  for ts, v in df["bitcoin"].items()]
        return {"gtrends": series[-1][1]}, {"gtrends": series}
    except Exception as e:
        log(f"pytrends skipped: {e}")
        return None


# -------------------------------------------------- Glassnode (key-gated)
GLASSNODE = {
    # metrics.json field           : (glassnode API path, history key)
    "mvrv":                  ("/v1/metrics/market/mvrv", "mvrv"),
    "mvrv_z":                ("/v1/metrics/market/mvrv_z_score", "mvrv_z"),
    "nupl":                  ("/v1/metrics/indicators/net_unrealized_profit_loss", "nupl"),
    "sopr":                  ("/v1/metrics/indicators/sopr", "sopr"),
    "puell":                 ("/v1/metrics/indicators/puell_multiple", "puell"),
    "exchange_reserves_btc": ("/v1/metrics/distribution/balance_exchanges", "reserves"),
    "lth_supply_btc":        ("/v1/metrics/supply/lth_sum", "lth"),
    "sth_cost_basis_usd":    ("/v1/metrics/market/sth_realized_price", "sth_cb"),
}


def glassnode() -> tuple[dict, dict]:
    key = os.environ.get("GLASSNODE_API_KEY")
    if not key:
        return {}, {}
    out, hist = {}, {}
    since = int(dt.datetime.now(dt.timezone.utc).timestamp()) - 400 * 86400
    for field, (path, hkey) in GLASSNODE.items():
        try:
            j = get("https://api.glassnode.com" + path,
                    params={"a": "BTC", "api_key": key, "i": "24h", "s": since}).json()
            if not j:
                continue
            out[field] = j[-1]["v"]
            hist[hkey] = [[dt.datetime.fromtimestamp(p["t"], dt.timezone.utc)
                           .strftime("%Y-%m-%d"), p["v"]] for p in j if p.get("v") is not None]
        except Exception as e:
            log(f"glassnode {field} failed: {e}")
    return out, hist


# ------------------------------------------------------------------ quotes
def quotes() -> dict:
    try:
        import yfinance as yf
        px = yf.download(["MSTR", "STRC"], period="5d", progress=False)["Close"].dropna()
        return {"strc_price": float(px["STRC"].iloc[-1]),
                "mstr_price": float(px["MSTR"].iloc[-1])}
    except Exception as e:
        log(f"yfinance skipped: {e}")
        return {}


# -------------------------------------------------------------------- main
def main() -> None:
    overrides = {}
    if OVERRIDES.exists():
        overrides = json.loads(OVERRIDES.read_text())

    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text())
        except Exception:
            pass
    hist: dict[str, list] = dict(prev.get("history") or {})

    def put_series(new: dict) -> None:
        """Full recompute replaces the stored series."""
        for k, s in new.items():
            if s:
                hist[k] = [[d, round(float(v), 6)] for d, v in s][-2600:]

    def append_point(k: str, v, date: str = TODAY) -> None:
        """One point per day, deduped — history accretes run by run."""
        if v is None:
            return
        arr = [p for p in hist.get(k, []) if p[0] != date]
        arr.append([date, round(float(v), 6)])
        arr.sort()
        hist[k] = arr[-2600:]

    tier1, tier2, tier3, strategy = {}, {}, {}, {}

    for res, dest in ((wma200(), tier1), (etf_flows(), tier2), (funding(), tier2),
                      (ssr(), tier3), (correlations(), tier3),
                      (m2(), tier3), (gtrends(), tier3), (fng(), tier3)):
        if res:
            scalars, series = res
            dest.update(scalars)
            put_series(series)

    poly = polymarket()
    if poly:
        tier1.update(poly)

    gn, gn_hist = glassnode()
    put_series(gn_hist)
    for k in ("exchange_reserves_btc", "lth_supply_btc"):
        if k in gn:
            tier1[k] = gn.pop(k)
    if "sth_cost_basis_usd" in gn:
        tier2["sth_cost_basis_usd"] = gn.pop("sth_cost_basis_usd")
    tier3.update(gn)

    strategy.update(quotes())

    # manual overrides win over everything (edit manual_overrides.json weekly)
    for name, dest in (("tier1", tier1), ("tier2", tier2),
                       ("tier3", tier3), ("strategy", strategy)):
        dest.update(overrides.get(name, {}))

    # snapshot today's levels into history (manual weekly numbers accrete too)
    append_point("poly_p100", tier1.get("poly_p_ge_100k"))
    append_point("poly_p50", tier1.get("poly_p_le_50k"))
    append_point("reserves", tier1.get("exchange_reserves_btc"))
    append_point("lth", tier1.get("lth_supply_btc"))
    append_point("sth_cb", tier2.get("sth_cost_basis_usd"))
    append_point("mvrv", tier3.get("mvrv"))
    append_point("mvrv_z", tier3.get("mvrv_z"))
    append_point("nupl", tier3.get("nupl"))
    append_point("sopr", tier3.get("sopr"))
    append_point("puell", tier3.get("puell"))
    append_point("cb_prem", tier3.get("coinbase_premium_pct"))

    # override history wins last (ignore _note keys and non-series values)
    for k, s in (overrides.get("history") or {}).items():
        if not k.startswith("_") and isinstance(s, list) and s:
            hist[k] = s

    doc = {
        "as_of": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
        "sample": False,
        "tier1": tier1, "tier2": tier2, "tier3": tier3, "strategy": strategy,
        "history": hist,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1))
    log(f"wrote {OUT} ({len(json.dumps(doc))} bytes, {len(hist)} history series)")


if __name__ == "__main__":
    main()
