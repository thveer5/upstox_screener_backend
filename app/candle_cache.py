"""Daily-candle cache, rally/fall streaks, and prior-day breakout detection.

Streaks and breakout flags are derived from real OHLC data (Upstox
historical-candle API), not from when the dashboard happens to be opened.
Daily candles are immutable once a trading day closes, so we cache them
permanently in SQLite and only re-fetch when today's candle is missing.

Requires the user to be OAuth-authenticated (historical-candle uses Bearer).
If not authenticated, enrichment is a silent no-op.
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .config import get_settings
from .token_store import get_access_token

DB_PATH = Path(__file__).resolve().parent.parent / "movers.db"
IST = ZoneInfo("Asia/Kolkata")

LOOKBACK_DAYS = 12          # enough to compute streaks up to ~10 days
API_BUFFER_DAYS = 20        # extra calendar days to absorb weekends + holidays
MAX_CONCURRENT_FETCHES = 15  # stay well under Upstox 25 req/s limit


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_candle_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_candles (
                instrument_key TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL NOT NULL,
                PRIMARY KEY (instrument_key, date)
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_candles_key ON daily_candles(instrument_key)"
        )
        # Migrate older schema (when only `close` existed) by adding missing columns.
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(daily_candles)").fetchall()}
        for col in ("open", "high", "low"):
            if col not in existing_cols:
                c.execute(f"ALTER TABLE daily_candles ADD COLUMN {col} REAL")


def _today_ist():
    return datetime.now(IST).date()


def _cached_candles(instrument_key: str, since_date: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT date, open, high, low, close FROM daily_candles "
            "WHERE instrument_key = ? AND date >= ? ORDER BY date ASC",
            (instrument_key, since_date),
        ).fetchall()
    return [
        {
            "date": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
        }
        for r in rows
    ]


def _store_candles(instrument_key: str, candles: list[dict]) -> None:
    if not candles:
        return
    rows = [
        (instrument_key, c["date"], c.get("open"), c.get("high"), c.get("low"), c["close"])
        for c in candles
    ]
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_candles "
            "(instrument_key, date, open, high, low, close) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


async def _fetch_candles_from_api(
    client: httpx.AsyncClient,
    instrument_key: str,
    headers: dict,
) -> list[dict]:
    settings = get_settings()
    today = _today_ist()
    from_date = (today - timedelta(days=LOOKBACK_DAYS + API_BUFFER_DAYS)).isoformat()
    to_date = today.isoformat()
    key = quote(instrument_key, safe="")
    url = (
        f"{settings.upstox_api_base}/historical-candle/"
        f"{key}/day/{to_date}/{from_date}"
    )
    try:
        resp = await client.get(url, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return []
        body = resp.json()
        candles = body.get("data", {}).get("candles", []) or []
        out = []
        for c in candles:
            # Upstox order: [timestamp, open, high, low, close, volume, oi]
            if len(c) >= 5:
                out.append({
                    "date": c[0][:10],
                    "open": float(c[1]) if c[1] is not None else None,
                    "high": float(c[2]) if c[2] is not None else None,
                    "low": float(c[3]) if c[3] is not None else None,
                    "close": float(c[4]),
                })
        out.sort(key=lambda x: x["date"])
        return out
    except Exception:
        return []


async def _get_daily_candles(
    instrument_key: str,
    client: httpx.AsyncClient,
    headers: dict,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    today = _today_ist()
    since = (today - timedelta(days=LOOKBACK_DAYS + API_BUFFER_DAYS)).isoformat()
    cached = _cached_candles(instrument_key, since)

    # Trust the cache if it already includes yesterday's row.
    cutoff = (today - timedelta(days=1)).isoformat()
    if cached and cached[-1]["date"] >= cutoff:
        return cached

    async with semaphore:
        fresh = await _fetch_candles_from_api(client, instrument_key, headers)
    if fresh:
        _store_candles(instrument_key, fresh)
        return fresh
    return cached


def _compute_streaks(
    candles: list[dict], today_change_percent: float | None
) -> tuple[int, int]:
    """Return (rally_streak, fall_streak) — at most one is non-zero."""
    directions: list[int] = []
    for i in range(1, len(candles)):
        diff = candles[i]["close"] - candles[i - 1]["close"]
        directions.append(1 if diff > 0 else -1 if diff < 0 else 0)

    if today_change_percent is not None:
        today_dir = (
            1 if today_change_percent > 0 else -1 if today_change_percent < 0 else 0
        )
        today_str = _today_ist().isoformat()
        if candles and candles[-1]["date"] == today_str:
            directions[-1] = today_dir
        else:
            directions.append(today_dir)

    if not directions:
        return 0, 0
    last = directions[-1]
    if last == 0:
        return 0, 0
    streak = 0
    for d in reversed(directions):
        if d == last:
            streak += 1
        else:
            break
    return (streak, 0) if last == 1 else (0, streak)


async def enrich_movers(instruments: list[dict]) -> None:
    """Mutate instruments in place, adding rally_streak + fall_streak from
    historical daily candles.

    Today's high/low/open are returned by the screener itself (see DEFAULT_FIELDS),
    so the day-range / day-high signal is computed client-side and needs no
    backend work here.

    Silent no-op if OAuth not configured or upstream fails.
    """
    for inst in instruments:
        inst["rally_streak"] = 0
        inst["fall_streak"] = 0

    token = get_access_token()
    if not token:
        return

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async with httpx.AsyncClient() as client:
        tasks = [
            _get_daily_candles(inst["instrument_key"], client, headers, semaphore)
            if inst.get("instrument_key") else _noop()
            for inst in instruments
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for inst, candles in zip(instruments, results):
        if isinstance(candles, Exception) or not candles:
            continue
        rally, fall = _compute_streaks(candles, inst.get("change_percent"))
        inst["rally_streak"] = rally
        inst["fall_streak"] = fall


async def _noop() -> list[dict]:
    return []


async def get_recent_daily_candles(instrument_key: str, limit: int = 12) -> list[dict]:
    """Return up to `limit` most-recent daily candles (oldest -> newest) for a
    single instrument.

    Reads from the SQLite cache and tops up from the historical-candle API when
    the cache is missing or stale. Returns whatever is cached (possibly []) when
    OAuth isn't configured or the upstream call fails.
    """
    today = _today_ist()
    since = (today - timedelta(days=LOOKBACK_DAYS + API_BUFFER_DAYS)).isoformat()
    cached = _cached_candles(instrument_key, since)

    cutoff = (today - timedelta(days=1)).isoformat()
    stale = not cached or cached[-1]["date"] < cutoff

    token = get_access_token()
    if stale and token:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            fresh = await _fetch_candles_from_api(client, instrument_key, headers)
        if fresh:
            _store_candles(instrument_key, fresh)
            cached = fresh

    return cached[-limit:] if limit else cached
