"""Environment config (secrets, URLs) and editable default settings.

Secrets come from .env only. Everything a user should be able to tune lives in
DEFAULT_SETTINGS and is edited from the Settings page (stored in the DB).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


DATABASE_URL = _env("DATABASE_URL") or f"sqlite:///{ROOT / 'data' / 'kalshi_edge.db'}"
KALSHI_API_BASE = _env("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2").rstrip("/")
ODDS_API_KEY = _env("ODDS_API_KEY")
ODDS_API_BASE = _env("ODDS_API_BASE", "https://api.the-odds-api.com/v4").rstrip("/")
ESPN_NFL_SCOREBOARD = _env(
    "ESPN_NFL_SCOREBOARD", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
AAA_URL = _env("AAA_URL", "https://gasprices.aaa.com/")
APP_HOST = _env("APP_HOST", "0.0.0.0")
APP_PORT = int(_env("APP_PORT", "8000") or 8000)
SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")
HTTP_TIMEOUT_S = float(_env("HTTP_TIMEOUT_S", "20") or 20)
LOCAL_TZ = "Pacific/Honolulu"

# Kalshi fee schedule, coded as config (editable on the Settings page).
# Source: Kalshi Fee Schedule (kalshi.com/docs/kalshi-fee-schedule.pdf):
#   taker fee = round_up_to_cent(0.07   * C * P * (1 - P))   general markets
#   taker fee = round_up_to_cent(0.035  * C * P * (1 - P))   S&P 500 / Nasdaq-100 index markets
#   maker fee = round_up_to_cent(0.0175 * C * P * (1 - P))   only on markets that charge maker fees
# The public /series endpoint reports fee_type ("quadratic", "quadratic_with_maker_fees", "flat")
# and fee_multiplier for each series; when present they override the defaults below.
DEFAULT_FEES = {
    "taker_rate": 0.07,
    "maker_rate": 0.0175,
    "index_taker_rate": 0.035,
    "index_series_prefixes": ["INX", "KXINX", "NASDAQ100", "KXNASDAQ100"],
    # If we have not loaded a series' fee_type yet, assume maker fees apply (conservative).
    "assume_maker_fee_when_unknown": True,
    # Per-series overrides, e.g. {"KXNFLGAME": {"maker_rate": 0.0175, "taker_rate": 0.07}}
    "series_overrides": {},
}

DEFAULT_SETTINGS = {
    "bankroll": 1000.0,
    "kelly_fraction": 0.25,
    "per_strike_cap_pct": 2.5,
    "correlated_cap_pct": 8.0,
    "tiers": {
        "s_min_edge_pts": 10.0,
        "s_min_net_ev_pct": 20.0,
        "g_min_edge_pts": 5.0,
        "g_min_net_ev_pct": 10.0,
        "g_size_multiplier": 0.5,
        "w_min_edge_pts": 3.0,
    },
    "fees": DEFAULT_FEES,
    "maker": {
        # Post one tick better than the best bid, never at or through the ask.
        "improve_ticks": 1,
    },
    "staleness_minutes": {
        "kalshi_scan": 20,
        "kalshi_active": 5,
        "espn_nfl": 60,
        "odds_api": 240,
        "aaa": 36 * 60,
        "manual_lines": 6 * 60,
    },
    "intervals": {
        "full_scan_minutes": 10,
        "active_snapshot_seconds": 60,
        "espn_minutes": 15,
        "odds_api_minutes": 120,
        "aaa_minutes": 60,
        "settle_minutes": 2,
        "edge_record_minutes": 5,
    },
    "active_series": ["KXNFLGAME"],
    "top_volume_active_count": 40,
    "watched_tickers": [],
    "nfl": {
        "series": ["KXNFLGAME"],
        # Priority of fair-probability sources; the first fresh one is used.
        "fair_source_priority": ["odds_api", "manual", "espn"],
        "odds_api_regions": "us",
        "odds_api_bookmakers": "",
    },
    "gas": {
        "series": ["KXAAAGASD", "KXAAAGASW", "KXAAAGASM"],
        "title_regex": r"(?i)\bgas(oline)? price|\bAAA\b",
        "exclude_regex": r"(?i)natural gas",
        # Fair P(YES) = model_weight * model probability + (1 - model_weight) * Kalshi's smoothed ladder probability.
        # Kalshi gas ladders have been well calibrated, so the model is shrunk toward the market.
        "model_weight": 0.5,
        "drift_weight": 0.7,
        "min_history_days": 7,
        "sigma_lookback_days": 30,
        "sigma_floor": 0.004,
        "calibration_backfill_days": 45,
        "calibration_horizon_hours": 18,
    },
    "news_feeds": [],
    # Heuristic: series tickers (KX prefix removed) matching this, or prop-like titles, go to the Props board.
    "props_series_regex": r"(?i)(ANYTD|FIRSTTD|PASSYDS|RUSHYDS|RECYDS|RECEPTIONS|PROP|PLAYER)",
}
