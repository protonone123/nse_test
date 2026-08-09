# NSE Scanner

NSE Live Pattern Scanner v3.1 — Production Grade Stock Market Scanning

## Features

- 13 Detectors + 5 Confirmation Signals
- Hourly + Daily scanning modes
- Telegram alerts integration
- Web dashboard
- GitHub Actions automation
- **Batch OHLCV downloader** — resilient multi-symbol download mechanism
  (ported from the cup_test scanner's `downloader.py`), replacing the old
  per-symbol download loop. See "New: Batch Downloading" below.
- **Interactive HTML dashboard** — self-contained candlestick chart
  viewer with a tag/category watchlist, search/filter/sort, compare
  mode, and rule-based explanations, generalized across all 14 pattern
  labels. See "New: HTML Dashboard" below.
- **Signal tags + Very High Quality list** — every signal is tagged
  (Buy Strong / Buy Moderate / Watch / Multi-Pattern / Near Breakout /
  Active) and a strict, separate `high_conviction_watchlist.json` is
  maintained for the highest-conviction subset. See "New: Tags &
  Watchlist" below.

## New: Batch Downloading

`batch_downloader.py` downloads ~50 symbols per `yf.download()` call
instead of one call per symbol (used by `scanner.py`'s `warm_cache()`
and `data_updater.py`'s `run_eod_update()`). Failures are triaged into
a fast timeout-retry queue and a slow escalating-backoff rate-limit
queue. New flag: `--full-refresh` on `data_updater.py`.

**Download once, derive the rest.** `run_eod_update()` now downloads
**daily bars only** over the network; weekly/monthly are derived
locally via `resample_utils.py` (a faithful port of cup_test's
`resample_weekly`/`resample_monthly`, including its ISO-calendar-week
fix for the "W-FRI" label-shift bug) — matching cup_test's
`downloader.py` exactly, which never downloads weekly/monthly at all.
This is what "why is it downloading weekly again" was: the previous
version issued three full batched network passes (1d, 1wk, 1mo) instead
of one. `dashboard_export.py`'s chart fallback uses the same
`resample_utils` functions, so there's one resampling implementation,
not two.

**Fundamentals (CANSLIM inputs) fetching.** Separate from OHLCV bars —
`dl_fund()` uses `yf.Ticker(sym).info`, which Yahoo has no bulk/batch
endpoint for, so it can't be batched the way price bars can. Two fixes
here: (1) the fundamentals cache validity window widened from
same-day-only to `FUND_CACHE_VALID_DAYS` (5 trading days) — EPS/sales
growth, pledge %, etc. are quarterly-cadence data, so re-fetching all
~2000 stocks' fundamentals every single day was unnecessary and is what
was causing the wall of `YFRateLimitError` lines during the scan loop;
(2) `dl_fund()` now shares the same global rate-limit circuit breaker
price downloads already used (`_wait_if_rate_limited`/`_set_rate_limit`)
instead of only backing off locally per-symbol, so a detected rate
limit now pauses every subsequent fundamentals call, not just retries
on the one symbol that hit it.

## New: HTML Dashboard

After every daily scan, `dashboard_export.py` writes a self-contained
`output/nse_scanner_dashboard_<date>.html` (plus a stable
`nse_scanner_dashboard_latest.html`) — no server required, just open it
in a browser. Candlestick charts (Daily/Weekly/Monthly) with MA/Bollinger/
MACD/RSI, entry/stop/target lines, a sidebar of tag-based categories,
search/filter/sort, a compare-up-to-6 mode, and a rule-based "why did
the scanner flag this / should I buy it" explanation panel
(`explain.py`) — all generalized to work across any of the 14 pattern
detectors, not just one. Ported from and modeled closely on the
`cup_test` scanner's `chart_export.py` + `chart_viewer/template.html`.

## New: Tags & Watchlist

`signal_tags.py` computes badge tags for every signal from the existing
`score10`/`tier` composite ranking (already in `scanner.py`, unchanged):
`HIGH_CONVICTION`, `BUY_STRONG`, `BUY_MODERATE`, `WATCH`,
`MULTI_PATTERN`, `NEAR_BREAKOUT`, `ACTIVE_SETUP`. These tags are now
persisted on the `signals` table (new `score10`/`tier`/`tags` columns —
check your CSV header, they're there) and included in `watchlist.json`
entries.

A strict `is_high_conviction()` gate produces a separate, short
**`high_conviction_watchlist.json`** — the "very high good pattern"
list — plus its own dated CSV (`scan_VERYHIGH_<date>_<time>.csv`), kept
apart from the broader watchlist. Calibrated against a real 6,717-
signal scan: `score10 >= 7.0` (the codebase's own BUY STRONG boundary —
score10's real-world ceiling is ~7.4, so an earlier `>= 8.0` version of
this gate was unreachable and always produced 0 results) AND
`risk_reward >= 2.0` AND `vol_surge >= 1.5` AND `rs_percentile >= 80`
AND `data_completeness >= 5`. On that same scan this produced 5 stocks
out of 6,717 signals — a deliberately short, elite list.

## Quick Start

### Prerequisites
- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

### Installation

```bash
git clone https://github.com/trashpandak/nse-scanner.git
cd nse-scanner
pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Edit `.env` and add your credentials:
```
TG_BOT_TOKEN=your_bot_token_from_botfather
TG_CHAT_ID=your_telegram_chat_id
```

### Usage

```bash
# Full daily scan
python scanner.py --daily

# Hourly scan (during market hours)
python scanner.py --hourly

# With Telegram alerts
python scanner.py --daily --telegram

# Launch web dashboard
python scanner.py --dashboard

# Health check
python scanner.py --healthcheck

# Quick test (10 stocks)
python scanner.py --test
```

## Deployment

### Local Development
```bash
python scanner.py --test --telegram
```

### GitHub Actions (Automated)
Scans run automatically:
- **Daily**: 4:30 PM IST (Mon-Fri)
- **Hourly**: 10:15 AM - 3:15 PM IST (market hours)

Set secrets in GitHub repo settings:
- `TG_BOT_TOKEN`
- `TG_CHAT_ID`

## Architecture

- **DAILY (4:30 PM IST)**: Full scan of all NSE stocks → builds watchlist
- **HOURLY (market hours)**: Checks watchlist only → triggers & alerts
- **Dashboard**: Real-time web interface

## Security

⚠️ **Never commit secrets to git!**
- Use `.env` for local development (ignored by git)
- Use GitHub Secrets for Actions
- See `.env.example` for required variables

## License

Private - Internal Use Only
