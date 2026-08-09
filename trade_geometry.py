#!/usr/bin/env python3
"""
Trade Geometry Validator
===========================
The single canonical validation gate for entry(cmp)/stop/target/R:R
consistency. Every trade-geometry number the scanner computes
(calc_targets() in scanner.py) passes through here before it's allowed
to influence score10, tier, recommendation, or the high-conviction
filter.

Why this exists (root-cause investigation, not just a symptom patch):
Production data showed R:R values in the hundreds/thousands — e.g.
TEMBO: cmp=₹63.80, breakout_zone/target levels=₹565-812. Traced to a
real stock split (TEMBO did a confirmed 1:10 split, ex-date 2026-08-05)
whose OLD cached daily bars (pre-split, ~10x today's price) never got
retroactively re-adjusted by the incremental price cache — see
split_guard.py for the source-level fix. Pattern detectors scanning
across that discontinuity built breakout_zone/target levels from the
stale pre-split price scale, while `cmp` is always the latest
(correctly-scaled) cached bar — hence pivot/targets 10x higher than
current price and R:R computed from that gap coming out absurd.

That's one root cause; this validator exists as the general-purpose
safety net regardless of cause (stale cache, wrong symbol mapping,
future detector bugs, corporate actions this scanner doesn't yet
handle, etc.) — anything that produces geometrically inconsistent
entry/stop/target numbers is caught here, not just the split case.

Deliberately does NOT use a fallback like
    risk = max(cmp - stop, cmp * 0.01)
to paper over an invalid stop — that exact pattern (present in
scanner.py's calc_targets()) is what let a corrupted/stale stop turn
into a fake tiny risk and thus a fake huge R:R. If the geometry is
invalid, this returns risk_reward=None, full stop — not a clamped or
estimated substitute.
"""

from __future__ import annotations

import math
from typing import Optional

# A breakout zone / pivot more than this fraction away from CMP is not
# a plausible near-term trigger level for any of nse-scanner's 14
# pattern types — even the widest monthly Cup&Handle patterns don't
# legitimately sit this far from current price. Chosen loose enough
# that genuine long-consolidation setups (which can sit 30-40% below
# a multi-month pivot) stay valid, while still catching order-of-
# magnitude corruption like the TEMBO case (pivot ~12x cmp).
MAX_PIVOT_CMP_DEVIATION = 0.60

# Even a textbook-perfect setup essentially never has a genuine 100:1
# reward:risk. This is a sanity ceiling on the OUTPUT, applied only
# after all the structural checks above already passed — it exists so
# that some not-yet-anticipated source of corruption (not just the
# split case) can't sneak a huge fake R:R through structurally-valid-
# looking numbers. It is deliberately set well above what a genuine
# (if aggressive) tight-stop setup can produce — spot-checking real
# production data found legitimate-looking tight-stop setups (e.g. a
# BullFlag pattern with an unusually shallow flag low, giving a ~0.5%
# structural stop) reaching R:R ~30-48 without any sign of corrupted
# inputs, while confirmed-corrupted cases (stale pre-split cached
# price data) were consistently 800+. 100 sits clearly above the
# former and clearly below the latter. This is not a scoring cap;
# _score() already buckets R:R at >=4.0 as the top tier, so this
# ceiling never actually binds for any genuine setup either way.
MAX_SANE_RR = 100.0


def validate_trade_geometry(cmp, stop, target_1, target_2, target_3,
                             breakout_zone=None) -> dict:
    """
    Returns:
        {
          "valid": bool,
          "reason": str | None,          # populated iff not valid
          "risk_reward": float | None,   # None if invalid — never a
                                          # fabricated/clamped fallback
        }

    Field names match scanner.py's row schema directly (cmp,
    stop_loss, target_1/2/3, breakout_zone) rather than generic
    "entry" terminology, since this codebase has no separate "entry"
    concept distinct from cmp — calc_targets() itself measures
    risk/reward relative to cmp, and this validator preserves that
    exact convention rather than introducing a new one.
    """
    def _bad(reason: str) -> dict:
        return {"valid": False, "reason": reason, "risk_reward": None}

    fields = {"cmp": cmp, "stop_loss": stop, "target_1": target_1,
              "target_2": target_2, "target_3": target_3}
    parsed = {}
    for name, v in fields.items():
        if v is None:
            return _bad(f"{name} is missing")
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return _bad(f"{name} is not numeric ({v!r})")
        if math.isnan(fv) or math.isinf(fv):
            return _bad(f"{name} is NaN/Inf")
        if fv <= 0:
            return _bad(f"{name} <= 0 ({fv})")
        parsed[name] = fv

    cmp_v, stop_v = parsed["cmp"], parsed["stop_loss"]
    t1, t2, t3 = parsed["target_1"], parsed["target_2"], parsed["target_3"]

    if stop_v >= cmp_v:
        return _bad(f"stop_loss ({stop_v}) >= cmp ({cmp_v}) — invalid for a "
                    f"long setup; the stop must sit below current price")
    if t1 <= cmp_v:
        return _bad(f"target_1 ({t1}) <= cmp ({cmp_v})")
    if t2 <= t1:
        return _bad(f"target_2 ({t2}) <= target_1 ({t1})")
    if t3 <= t2:
        return _bad(f"target_3 ({t3}) <= target_2 ({t2})")

    if breakout_zone is not None:
        try:
            bz_v = float(breakout_zone)
        except (TypeError, ValueError):
            bz_v = None
        if bz_v is not None and bz_v > 0 and not (math.isnan(bz_v) or math.isinf(bz_v)):
            deviation = abs(bz_v - cmp_v) / cmp_v
            if deviation > MAX_PIVOT_CMP_DEVIATION:
                return _bad(
                    f"breakout_zone ({bz_v}) is {deviation*100:.0f}% away from "
                    f"cmp ({cmp_v}) — beyond any plausible near-term trigger "
                    f"level; almost always stale/mis-scaled cached price data "
                    f"(a stock split not retroactively applied to old cached "
                    f"bars is the most common cause), not a genuine setup"
                )

    # Same reference points calc_targets() itself uses (reward measured
    # to target_2, risk measured cmp-to-stop) — this validator does not
    # change WHAT is measured, only refuses to fabricate a fallback
    # when the inputs are invalid.
    risk = cmp_v - stop_v
    reward = t2 - cmp_v
    if risk <= 0:
        return _bad("computed risk (cmp - stop_loss) <= 0")
    rr = reward / risk
    if math.isnan(rr) or math.isinf(rr) or rr <= 0:
        return _bad(f"computed R:R is not a valid positive number ({rr})")
    if rr > MAX_SANE_RR:
        return _bad(f"computed R:R ({rr:.1f}) exceeds the sanity ceiling "
                    f"({MAX_SANE_RR}:1) — reward/risk this large is essentially "
                    f"always corrupted inputs, not a genuine edge")

    return {"valid": True, "reason": None, "risk_reward": round(rr, 2)}


def downgrade_recommendation(rec: str, reason: str) -> str:
    """
    Downgrades a BUY recommendation to WATCH when trade geometry is
    invalid. NOTE: scanner.py's recommend() actually returns strings
    like "BUY — strong" / "BUY — moderate" (em-dash, lowercase) — not
    "BUY STRONG" / "BUY MODERATE" (that's the separate _tier() star-
    rating format). An existing downgrade at the weekly-chart-
    validation gate ("Gap 10 FIX" in scanner.py) searches for the
    wrong format and silently never matches; this function uses the
    format recommend() actually produces.
    """
    if not rec:
        rec = "WATCH"
    if "BUY" in rec:
        rec = rec.replace("BUY — strong", "WATCH").replace("BUY — moderate", "WATCH")
        if "BUY" in rec:   # catch any other "BUY ..." form defensively
            rec = "WATCH" + rec[rec.index("BUY") + 3:]
    return f"{rec} [TRADE GEOMETRY INVALID: {reason}]"
