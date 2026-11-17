"""
NIMA ICHIMOKU SCALPER — Step 1
MEXC USDT perpetual market data + Ichimoku 9/26/52 core.
Research/backtest only: this file does not place orders or send Telegram messages.
"""

from __future__ import annotations

import time
from typing import Iterable

import requests

BASE_URL = "https://contract.mexc.com"
TIMEOUT = 20

INTERVALS = {
    "1m": ("Min1", 60),
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "30m": ("Min30", 1800),
    "1h": ("Min60", 3600),
}


def get_json(path: str, params: dict | None = None) -> dict:
    """GET a MEXC contract API endpoint and validate its response."""
    response = requests.get(
        BASE_URL + path, params=params, timeout=TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict) or payload.get("success") is False:
        raise RuntimeError(f"MEXC API error: {payload}")

    return payload


def get_klines_between(
    symbol: str,
    interval: str,
    start_ts: int,
    end_ts: int,
) -> list[list[float]]:
    """
    Fetch completed candles in <=500-candle chunks.
    Candle format: [timestamp_ms, open, high, low, close, volume].
    """
    if interval not in INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")

    api_interval, seconds = INTERVALS[interval]
    chunk_seconds = seconds * 450
    candles_by_time: dict[int, list[float]] = {}
    cursor = int(start_ts)
    now = int(time.time())

    while cursor < min(int(end_ts), now):
        chunk_end = min(cursor + chunk_seconds, int(end_ts), now)
        if chunk_end <= cursor:
            break

        payload = get_json(
            f"/api/v1/contract/kline/{symbol}",
            params={
                "interval": api_interval,
                "start": cursor,
                "end": chunk_end,
            },
        )
        data = payload.get("data") or {}
        times = data.get("time") or []

        for i, raw_ts in enumerate(times):
            ts = int(raw_ts)
            # Exclude incomplete candles and anything beyond requested range.
            if ts < start_ts or ts + seconds > end_ts or ts + seconds > now:
                continue

            candles_by_time[ts] = [
                ts * 1000,
                float(data["open"][i]),
                float(data["high"][i]),
                float(data["low"][i]),
                float(data["close"][i]),
                float(data["vol"][i]),
            ]

        cursor = chunk_end
        time.sleep(0.08)

    return [candles_by_time[t] for t in sorted(candles_by_time)]


def aggregate_klines(
    candles: Iterable[list[float]],
    base_minutes: int,
    target_minutes: int,
) -> list[list[float]]:
    """Aggregate only complete, correctly aligned candles."""
    if target_minutes % base_minutes:
        raise ValueError("Target timeframe must be a multiple of base timeframe.")

    factor = target_minutes // base_minutes
    base_seconds = base_minutes * 60
    bucket_seconds = target_minutes * 60
    buckets: dict[int, list[list[float]]] = {}

    for candle in candles:
        ts = int(candle[0] // 1000)
        bucket_start = (ts // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_start, []).append(candle)

    output = []
    now = int(time.time())

    for bucket_start in sorted(buckets):
        rows = sorted(buckets[bucket_start], key=lambda row: row[0])
        if len(rows) != factor:
            continue

        actual = [int(row[0] // 1000) for row in rows]
        expected = [bucket_start + n * base_seconds for n in range(factor)]
        if actual != expected or bucket_start + bucket_seconds > now:
            continue

        output.append([
            bucket_start * 1000,
            float(rows[0][1]),
            max(float(row[2]) for row in rows),
            min(float(row[3]) for row in rows),
            float(rows[-1][4]),
            sum(float(row[5]) for row in rows),
        ])

    return output


def get_timeframe_klines(
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
) -> list[list[float]]:
    """Fetch 15m directly; construct 3m from 1m and 45m from 15m."""
    if timeframe == "3m":
        raw = get_klines_between(symbol, "1m", start_ts, end_ts)
        return aggregate_klines(raw, 1, 3)

    if timeframe == "15m":
        return get_klines_between(symbol, "15m", start_ts, end_ts)

    if timeframe == "45m":
        raw = get_klines_between(symbol, "15m", start_ts, end_ts)
        return aggregate_klines(raw, 15, 45)

    raise ValueError("Supported strategy timeframes are 3m, 15m and 45m.")


def midpoint(highs: list[float], lows: list[float], end_index: int, period: int):
    """Highest-high/lowest-low midpoint ending at end_index, inclusive."""
    start = end_index - period + 1
    if start < 0 or end_index >= len(highs):
        return None
    return (max(highs[start:end_index + 1]) + min(lows[start:end_index + 1])) / 2.0


def ichimoku_at(candles: list[list[float]], index: int = -1) -> dict | None:
    """
    Ichimoku 9/26/52 snapshot with no future data.

    `cloud_a` and `cloud_b` are the cloud values visually present above
    the selected candle (calculated 26 candles earlier).
    `future_a` and `future_b` are the current values projected 26 candles ahead.
    """
    if index < 0:
        index = len(candles) + index
    if index < 78 or index >= len(candles):
        return None

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    tenkan = midpoint(highs, lows, index, 9)
    kijun = midpoint(highs, lows, index, 26)

    cloud_source_index = index - 26
    old_tenkan = midpoint(highs, lows, cloud_source_index, 9)
    old_kijun = midpoint(highs, lows, cloud_source_index, 26)
    cloud_b = midpoint(highs, lows, cloud_source_index, 52)

    future_b = midpoint(highs, lows, index, 52)

    values = (tenkan, kijun, old_tenkan, old_kijun, cloud_b, future_b)
    if any(value is None for value in values):
        return None

    cloud_a = (old_tenkan + old_kijun) / 2.0
    future_a = (tenkan + kijun) / 2.0
    cloud_top = max(cloud_a, cloud_b)
    cloud_bottom = min(cloud_a, cloud_b)
    close = closes[index]

    return {
        "close": close,
        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_a": cloud_a,
        "cloud_b": cloud_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "future_a": future_a,
        "future_b": future_b,
        "price_above_cloud": close > cloud_top,
        "price_below_cloud": close < cloud_bottom,
        "tenkan_above_kijun": tenkan > kijun,
        "tenkan_below_kijun": tenkan < kijun,
        "future_cloud_bullish": future_a > future_b,
        "future_cloud_bearish": future_a < future_b,
    }


def trend_state(candles: list[list[float]], index: int = -1) -> str:
    """Strict trend state used as a filter, not an entry trigger."""
    ichi = ichimoku_at(candles, index)
    if ichi is None:
        return "INSUFFICIENT_DATA"

    if (
        ichi["price_above_cloud"]
        and ichi["tenkan_above_kijun"]
        and ichi["future_cloud_bullish"]
    ):
        return "BULLISH"

    if (
        ichi["price_below_cloud"]
        and ichi["tenkan_below_kijun"]
        and ichi["future_cloud_bearish"]
    ):
        return "BEARISH"

    return "NEUTRAL"


def _self_test() -> None:
    """Small deterministic test: enough candles, valid output, no API call."""
    candles = []
    price = 100.0
    for i in range(140):
        open_price = price
        close = price + 0.15
        high = close + 0.2
        low = open_price - 0.1
        candles.append([i * 180_000, open_price, high, low, close, 100.0])
        price = close

    result = ichimoku_at(candles)
    assert result is not None
    assert result["tenkan"] > 0 and result["kijun"] > 0
    assert result["cloud_top"] >= result["cloud_bottom"]
    assert trend_state(candles) in {"BULLISH", "BEARISH", "NEUTRAL"}
    print("SELF-TEST PASSED: Ichimoku 9/26/52 calculations are available.")


if __name__ == "__main__":
    _self_test()
