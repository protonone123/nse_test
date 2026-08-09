#!/usr/bin/env python3
"""
Tests — Trade Geometry Validation & Split Detection
========================================================
Run with: python3 test_trade_geometry.py
(No pytest dependency required — plain assertions, so it runs in any
environment including the GitHub Actions runner without adding a dev
dependency.)
"""

from __future__ import annotations

import math
import sys

import trade_geometry as tg
import split_guard


PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


# ─────────────────────────────────────────────────────────────────────
# Trade geometry: valid cases
# ─────────────────────────────────────────────────────────────────────
print("=== Trade geometry: valid setups ===")

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=105, target_2=110, target_3=115)
check("valid entry/stop/target", r["valid"] and r["risk_reward"] == 2.0, str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=105, target_2=110, target_3=115,
                                breakout_zone=98)
check("valid with breakout_zone close to cmp", r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=1000, stop=970, target_1=1050, target_2=1150, target_3=1300,
                                breakout_zone=650)   # 35% below cmp — still plausible
check("valid with breakout_zone 35% below cmp (legit long consolidation)", r["valid"], str(r))


# ─────────────────────────────────────────────────────────────────────
# Trade geometry: invalid cases (Section 15's explicit list)
# ─────────────────────────────────────────────────────────────────────
print("\n=== Trade geometry: invalid setups ===")

r = tg.validate_trade_geometry(cmp=100, stop=105, target_1=110, target_2=115, target_3=120)
check("stop above entry -> invalid", not r["valid"] and r["risk_reward"] is None, str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=90, target_2=110, target_3=115)
check("target_1 below entry -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=110, target_2=105, target_3=115)
check("target ordering broken (t2 < t1) -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=105, target_2=110, target_3=108)
check("target ordering broken (t3 < t2) -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=-100, stop=95, target_1=105, target_2=110, target_3=115)
check("negative cmp -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=0, target_1=105, target_2=110, target_3=115)
check("zero stop -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=105, target_2=110, target_3=0)
check("zero target_3 -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=float("nan"), target_1=105, target_2=110, target_3=115)
check("NaN stop -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=99.99, target_1=105, target_2=110, target_3=115)
check("absurd R:R from near-zero real risk -> caught by sanity ceiling",
      not r["valid"] and "sanity ceiling" in r["reason"], str(r))

r = tg.validate_trade_geometry(cmp=63.80, stop=573.22, target_1=929.05, target_2=1046.12,
                                target_3=1190.82, breakout_zone=811.98)
check("REAL production case: TEMBO stale-pre-split geometry -> invalid",
      not r["valid"] and "stop_loss" in r["reason"], str(r))

r = tg.validate_trade_geometry(cmp=1965.30, stop=1808.08, target_1=1186.90, target_2=1295.05,
                                target_3=1428.72, breakout_zone=1078.75)
check("REAL production case: AVALON target already exceeded by cmp -> invalid",
      not r["valid"] and "target_1" in r["reason"], str(r))

r = tg.validate_trade_geometry(cmp=100, stop=95, target_1=105, target_2=110, target_3=115,
                                breakout_zone=1200)
check("breakout_zone wildly detached from cmp -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp=None, stop=95, target_1=105, target_2=110, target_3=115)
check("missing cmp -> invalid", not r["valid"], str(r))

r = tg.validate_trade_geometry(cmp="not_a_number", stop=95, target_1=105, target_2=110, target_3=115)
check("non-numeric cmp -> invalid", not r["valid"], str(r))


# ─────────────────────────────────────────────────────────────────────
# downgrade_recommendation — string format correctness
# ─────────────────────────────────────────────────────────────────────
print("\n=== Recommendation downgrade ===")

out = tg.downgrade_recommendation("BUY — strong", "test reason")
check("'BUY — strong' downgraded to WATCH", out.startswith("WATCH") and "BUY" not in out.split("[")[0],
      out)

out = tg.downgrade_recommendation("BUY — moderate", "test reason")
check("'BUY — moderate' downgraded to WATCH", out.startswith("WATCH") and "BUY" not in out.split("[")[0],
      out)

out = tg.downgrade_recommendation("WATCH — mixed", "test reason")
check("non-BUY recommendation passes through with reason appended",
      out.startswith("WATCH — mixed") and "TRADE GEOMETRY INVALID" in out, out)


# ─────────────────────────────────────────────────────────────────────
# Split detection
# ─────────────────────────────────────────────────────────────────────
print("\n=== Split ratio detection ===")

check("1:10 split ratio detected", split_guard.detect_split_ratio(600.0, 63.0) is not None)
check("2:1 bonus ratio detected", split_guard.detect_split_ratio(100.0, 205.0) is not None)
check("normal 3% daily move NOT flagged", split_guard.detect_split_ratio(100.0, 103.0) is None)
check("normal 8% gap-down NOT flagged", split_guard.detect_split_ratio(100.0, 92.0) is None)
check("missing last close -> None (no false trigger)",
      split_guard.detect_split_ratio(None, 100.0) is None)
check("zero last close -> None (no divide-by-zero)",
      split_guard.detect_split_ratio(0.0, 100.0) is None)


# ─────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}\n{PASS} passed, {FAIL} failed\n{'='*50}")
sys.exit(1 if FAIL else 0)
