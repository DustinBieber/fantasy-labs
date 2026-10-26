"""Environment config (secrets, URLs) and editable default settings.

Secrets come from .env only. Everything a user should be able to tune lives in
DEFAULT_SETTINGS and is edited from the Settings page (stored in the DB).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# The Mac installer keeps settings outside the app folder (~/KalshiEdge/kalshi-edge.env) so updates never touch them.
load_dotenv(os.getenv("KALSHI_EDGE_ENV_FILE") or ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


DATABASE_URL = _env("DATABASE_URL") or f"sqlite:///{ROOT / 'data' / 'kalshi_edge.db'}"
# Kalshi recommends the external-api host; api.elections.kalshi.com is still supported (set it in .env if needed).
KALSHI_API_BASE = _env("KALSHI_API_BASE", "https://external-api.kalshi.com/trade-api/v2").rstrip("/")
ODDS_API_KEY = _env("ODDS_API_KEY")
ODDS_API_BASE = _env("ODDS_API_BASE", "https://api.the-odds-api.com/v4").rstrip("/")
ESPN_NFL_SCOREBOARD = _env(
    "ESPN_NFL_SCOREBOARD", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
AAA_URL = _env("AAA_URL", "https://gasprices.aaa.com/")
# Public, read-only Polymarket data (on-chain records). Used only to grade and follow bettors; never to trade.
POLYMARKET_DATA_API = _env("POLYMARKET_DATA_API", "https://data-api.polymarket.com").rstrip("/")
# Your Kalshi API key (create it in Kalshi -> Settings -> API keys). The private key file stays on this Mac.
KALSHI_API_KEY_ID = _env("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = _env("KALSHI_PRIVATE_KEY_PATH")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
# Dashboard password (HTTP Basic). Required before the AI trader can go live.
APP_PASSWORD = _env("APP_PASSWORD")
APP_HOST = _env("APP_HOST", "0.0.0.0")
# 8100, not 8000: Family Hub runs on 8000 on the same Mac.
APP_PORT = int(_env("APP_PORT", "8100") or 8100)
SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")
HTTP_TIMEOUT_S = float(_env("HTTP_TIMEOUT_S", "20") or 20)
LOCAL_TZ = "Pacific/Honolulu"

# Kalshi fee schedule, coded as config (editable on the Settings page). See app/engine/fees.py.
#   taker fee = 0.07 x fee_multiplier x C x P x (1 - P)
#   maker fee = taker fee x ratio for the fee_type Kalshi reports on the series (or event override)
#   rounding  = up to $0.0001 for direct Kalshi members ($0.01 if you trade through a non-direct member)
DEFAULT_FEES = {
    "taker_rate": 0.07,
    "maker_ratio_by_fee_type": {
        "quadratic": 0.0,
        "quadratic_with_maker_fees": 0.25,
        "quadratic_with_combo_maker_fees": 0.5,
    },
    # Used only when a series' fee type is unknown or not coded (conservative).
    "maker_rate": 0.0175,
    "index_taker_rate": 0.035,
    "index_series_prefixes": ["INX", "KXINX", "NASDAQ100", "KXNASDAQ100"],
    "assume_maker_fee_when_unknown": True,
    "rounding_precision": 0.0001,
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
        "odds_api": 780,
        "aaa": 36 * 60,
        "manual_lines": 6 * 60,
        "follow_picks": 60,
    },
    "intervals": {
        "full_scan_minutes": 10,
        "active_snapshot_seconds": 60,
        "espn_minutes": 15,
        # 720 = twice a day: 6 credits x 2 x 30 = 360 of the free tier's 500 a month. On a paid plan, use 30.
        "odds_api_minutes": 720,
        "aaa_minutes": 60,
        "settle_minutes": 2,
        "edge_record_minutes": 5,
        "follow_discover_hours": 24,
        "follow_picks_minutes": 15,
        "autotrader_minutes": 2,
    },
    "active_series": ["KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"],
    "top_volume_active_count": 40,
    "watched_tickers": [],
    "nfl": {
        "series": ["KXNFLGAME"],
        "spread_series": ["KXNFLSPREAD"],
        "total_series": ["KXNFLTOTAL"],
        # Any open market in a series starting with this whose title reads "Player: N+ stat" is a prop.
        "prop_series_prefix": "KXNFL",
        # Bulk pull: 1 credit per market per region. h2h,spreads,totals x us,eu = 6 credits a pull.
        "odds_api_markets": "h2h,spreads,totals",
        # Per-game pull of alternate lines + player props (only stats Kalshi lists for that game).
        "deep_pull_regions": "us",
        "spread_sd": 13.5,
        "total_sd": 10.0,
        # Priority of fair-probability sources; the first fresh one is used.
        "fair_source_priority": ["odds_api", "manual", "espn"],
        # "us,eu" adds Pinnacle (2 credits per pull instead of 1). With the 240-minute poll that is
        # about 360 of the free tier's 500 credits a month.
        "odds_api_regions": "us,eu",
        "odds_api_bookmakers": "",
        # When any of these books is in the pull, the fair price uses them instead of the all-book median.
        "sharp_books": ["pinnacle", "circasports", "lowvig", "betonlineag"],
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
        # AAA publishes the national average to $0.0001; YES needs strictly above the strike.
        "aaa_tick": 0.0001,
        "calibration_backfill_days": 30,
        # Calibration prices each settled ladder this many hours before close (6 = about noon HST, after
        # AAA has published the prior day's value).
        "calibration_horizon_hours": 6,
        # The model may rate gas bets above Watch only after beating Kalshi's own prices (Brier score)
        # over at least this many settled days.
        "min_graded_days": 10,
    },
    # The AI trader: runs your rules automatically. Hard limits are enforced in code on every order.
    "autotrader": {
        "mode": "off",  # off | paper | live (live also needs the go-live test, a passed live check, and a password)
        "bankroll": 500.0,
        "tiers": ["S", "G"],
        "trade_nfl_singles": True,
        "trade_parlays": True,
        "trade_gas": True,
        "allow_sports": True,  # regulatory switch: turn off to stop all sports trading at once
        "max_order_usd": 15.0,
        "max_open_usd": 150.0,
        "max_orders_per_day": 25,
        "max_parlays_per_day": 2,
        "max_parlay_usd": 5.0,
        "daily_loss_limit_usd": 25.0,
        "total_stop_usd": 50.0,
        "order_ttl_minutes": 30,
        "min_minutes_before_close": 15,
        "max_price_age_minutes": 5,
        "max_consecutive_errors": 3,
        "parlays_live": False,  # live combo quotes stay off until verified; parlays are paper + phone alert
        "gate": {"paper_days": 14, "min_settled": 10, "require_profit": True, "require_beat_spy": True,
                 "require_clv": True},
        "tax_set_aside_pct": 30.0,
    },
    # Following proven bettors: Polymarket wallets graded by this app from their public NFL record.
    "follow": {
        "enabled": True,
        "sport": "NFL",
        "leaderboard_category": "SPORTS",
        "leaderboard_windows": ["ALL", "MONTH"],
        "candidates_per_window": 50,
        "max_position_pages": 20,
        # Your proof rule: 200+ graded picks (or a full season) in this exact sport, with positive units.
        "min_proof_picks": 200,
        "full_season_weeks": 15,
        # Ignore positions smaller than this when following (noise, tests, dust).
        "min_position_usd": 200,
        # Wallets that hold both sides of the same game this often look like market makers: context only.
        "max_hedge_share": 0.3,
    },
    # Parlays = Kalshi Combos. Multi-game only (same-game legs are correlated and not modeled yet).
    "parlays": {
        "min_legs": 2,
        "max_legs": 4,
        "candidate_legs": 12,
        # The maximum quote to accept is the price where net EV after fees falls to this.
        "min_net_ev_pct": 10.0,
        "show": 12,
        "leg_types": ["moneyline", "spread", "total", "prop"],
        # Props priced from a one-sided alt line scaled by the book's own main-line vig.
        "allow_scaled_props": True,
        # Spreads/totals priced by the normal-model fallback (no exact book line): off by default.
        "allow_model_legs": False,
    },
    "news_feeds": [],
    # Heuristic: series tickers (KX prefix removed) matching this, or prop-like titles, go to the Props board.
    "props_series_regex": r"(?i)(ANYTD|FIRSTTD|PASSYDS|RUSHYDS|RECYDS|RECEPTIONS|PROP|PLAYER)",
}
