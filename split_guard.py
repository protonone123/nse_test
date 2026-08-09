#!/usr/bin/env python3
"""
Split Guard — source-level fix for the stale-pre-split-cache bug
====================================================================
Root cause (found via the trade-geometry audit): nse-scanner's
incremental price cache only ever fetches bars AFTER the last cached
date and appends/upserts them (see batch_downloader.py, scanner.py's
dl_cached/_cache_write, data_updater.py's write_cache). yfinance's
auto_adjust=True correctly retro-adjusts ALL historical bars for a
stock split, but only within a SINGLE download call — it has no way
to reach back and fix bars that were already written to the cache by
an EARLIER download, before the split happened.

Concretely, for TEMBO (1:10 split, ex-date 2026-08-05): bars cached
before Aug 5 remained at their pre-split price level (~10x today's
price); bars fetched Aug 5 onward were correctly post-split-adjusted.
Pattern detectors scanning across that discontinuity built
breakout_zone/target levels from the stale pre-split portion of the
series, while cmp (the latest bar) was always correctly scaled —
producing entry/target levels ~10x current price and R:R in the
thousands once trade_geometry.py's validator wasn't there to catch it.

This module closes the gap: before merging newly-fetched bars into an
existing cache, compare the last cached close to the first new close.
A jump outside a plausible single-day range is a split/bonus
signature, not organic price movement (NSE circuit limits cap most
single-day moves well inside this range) — when detected, the entire
stale cache for that symbol is discarded and a full fresh history
re-fetch is triggered instead of merging mismatched-scale data.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd
import yfinance as yf

# A genuine single-day organic price move on NSE essentially never
# exceeds this range even during extreme volatility (most stocks have
# 5/10/20% circuit limits; even circuit-free large-caps rarely gap
# more than ~40-50% in a session). Ratios outside [0.5, 2.0] are a
# split/bonus/consolidation signature, not organic trading.
MIN_PLAUSIBLE_RATIO = 0.5
MAX_PLAUSIBLE_RATIO = 2.0


def detect_split_ratio(last_cached_close: Optional[float],
                        first_new_close: Optional[float]) -> Optional[float]:
    """
    Returns the apparent ratio if the jump between the last cached bar
    and the first newly-fetched bar looks like a corporate action
    rather than organic price movement, else None.
    """
    if last_cached_close is None or first_new_close is None:
        return None
    try:
        last_c = float(last_cached_close)
        new_c = float(first_new_close)
    except (TypeError, ValueError):
        return None
    if last_c <= 0 or new_c <= 0:
        return None
    ratio = new_c / last_c
    if ratio < MIN_PLAUSIBLE_RATIO or ratio > MAX_PLAUSIBLE_RATIO:
        return ratio
    return None


def full_refetch_symbol(sym: str, interval: str = "1d",
                         full_history_start: str = "2015-01-01",
                         log=None) -> Optional[pd.DataFrame]:
    """
    Fresh, full-history single-symbol download (yfinance auto_adjust=True
    correctly re-adjusts ALL bars for every known split/bonus as of
    today when downloading full history in one call — this is what
    fixes the stale-cache discontinuity). Used only when a split is
    detected for one specific symbol; NOT part of the normal batch
    path, so it doesn't reintroduce the per-symbol rate-limit problem
    batch_downloader.py exists to avoid — this only runs for the rare
    handful of symbols that actually had a corporate action.
    """
    try:
        ticker = yf.Ticker(sym)
        df = ticker.history(
            start=full_history_start,
            end=(date.today() + timedelta(days=1)).isoformat(),
            interval=interval,
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.columns = [str(c) for c in df.columns]
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna(how="all")
        if "Close" in df.columns:
            df = df[df["Close"] > 0]
        return df if not df.empty else None
    except Exception as e:
        if log:
            log.debug(f"split_guard: full re-fetch failed for {sym}: {e}")
        return None


def guarded_save(
    sym: str,
    new_df: pd.DataFrame,
    read_existing_fn: Callable[[str], Optional[pd.DataFrame]],
    write_fn: Callable[[str, pd.DataFrame], None],
    interval: str = "1d",
    full_history_start: str = "2015-01-01",
    log=None,
) -> None:
    """
    Drop-in replacement for a plain write_fn(sym, new_df) call — wraps
    it with split detection. read_existing_fn(sym) and write_fn(sym,
    df) use the SAME shape as scanner.py's _cache_read/_cache_write and
    data_updater.py's read_cache/write_cache, so this works as a thin
    wrapper around either without needing to know their schema.
    """
    try:
        existing = read_existing_fn(sym)
    except Exception:
        existing = None

    if existing is not None and not existing.empty and new_df is not None and not new_df.empty \
            and "Close" in existing.columns and "Close" in new_df.columns:
        last_old_close = float(existing["Close"].iloc[-1])
        first_new_close = float(new_df["Close"].iloc[0])
        ratio = detect_split_ratio(last_old_close, first_new_close)
        if ratio is not None:
            if log:
                log.warning(
                    f"{sym}: suspected stock split/bonus issue detected "
                    f"(last cached close {last_old_close:.2f} -> new fetch "
                    f"{first_new_close:.2f}, ratio {ratio:.2f}x) — discarding "
                    f"stale cache and doing a full history re-fetch instead "
                    f"of merging mismatched-scale data"
                )
            refetched = full_refetch_symbol(
                sym, interval=interval,
                full_history_start=full_history_start, log=log)
            if refetched is not None and not refetched.empty:
                write_fn(sym, refetched)
                return
            # Full re-fetch failed (rare) — fall through and merge as
            # normal rather than losing the day's data entirely; the
            # discontinuity will still be present, but
            # trade_geometry.py's validator remains as the downstream
            # safety net either way.
            if log:
                log.warning(f"{sym}: full re-fetch after split detection "
                            f"failed — falling back to normal incremental "
                            f"merge (trade_geometry.py validator still "
                            f"protects scoring/ranking)")

    write_fn(sym, new_df)
