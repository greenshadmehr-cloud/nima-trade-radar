
import os
import requests
import time
from datetime import datetime, timezone

BASE_URL = "https://contract.mexc.com"
TOP_N = 30
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def get_json(path, params=None):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"MEXC API error: {data}")

    return data


def get_top_contracts():
    print("Getting MEXC contract information...")

    info = get_json("/api/v1/contract/detail")
    contracts = info.get("data", [])

    print("Getting MEXC futures tickers...")

    ticker_data = get_json("/api/v1/contract/ticker")
    tickers = ticker_data.get("data", [])

    if isinstance(tickers, dict):
        tickers = [tickers]

    ticker_map = {
        item["symbol"]: item
        for item in tickers
        if item.get("symbol")
    }

    ranked = []

    for item in contracts:
        symbol = item.get("symbol")

        if (
            item.get("quoteCoin") != "USDT"
            or item.get("settleCoin") != "USDT"
            or item.get("futureType") != 1
            or item.get("state") != 0
            or not symbol
        ):
            continue

        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        price = float(ticker.get("lastPrice", 0))
        volume = float(ticker.get("amount24", 0))

        if price <= 0 or price > 50 or volume <= 0:
            continue

        ranked.append({
            "symbol": symbol,
            "price": price,
            "change": float(ticker.get("riseFallRate", 0)) * 100,
            "volume": volume
        })

    ranked.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    print("Eligible contracts:", len(ranked))

    return ranked[:TOP_N]


def get_klines(symbol, interval, limit=50):
    interval_map = {
        "1m": "Min1",
        "5m": "Min5",
        "15m": "Min15",
        "30m": "Min30",
        "1h": "Min60"
    }

    if interval not in interval_map:
        raise ValueError(f"Unsupported interval: {interval}")

    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600
    }[interval]

    now = int(time.time())

    params = {
        "interval": interval_map[interval],
        "start": now - (limit + 5) * interval_seconds,
        "end": now
    }

    result = get_json(
        f"/api/v1/contract/kline/{symbol}",
        params=params
    )

    data = result.get("data", {})
    times = data.get("time", [])

    candles = []

    for i, timestamp in enumerate(times):
        if timestamp + interval_seconds > now:
            continue

        candles.append([
            int(timestamp) * 1000,
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(data["vol"][i])
        ])

    return candles[-limit:]

    

def aggregate_klines(candles, base_minutes, target_minutes, limit):
    """Aggregate closed base candles into complete higher-timeframe candles."""
    if target_minutes % base_minutes != 0:
        raise ValueError("Target timeframe must be a multiple of base timeframe.")

    factor = target_minutes // base_minutes
    bucket_seconds = target_minutes * 60
    base_seconds = base_minutes * 60
    buckets = {}

    for candle in candles:
        timestamp = int(candle[0] // 1000)
        bucket_start = (timestamp // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket_start, []).append(candle)

    aggregated = []

    for bucket_start in sorted(buckets):
        rows = sorted(buckets[bucket_start], key=lambda row: row[0])
        if len(rows) != factor:
            continue

        timestamps = [int(row[0] // 1000) for row in rows]
        expected = [bucket_start + i * base_seconds for i in range(factor)]
        if timestamps != expected:
            continue

        if bucket_start + bucket_seconds > int(time.time()):
            continue

        aggregated.append([
            bucket_start * 1000,
            float(rows[0][1]),
            max(float(row[2]) for row in rows),
            min(float(row[3]) for row in rows),
            float(rows[-1][4]),
            sum(float(row[5]) for row in rows)
        ])

    return aggregated[-limit:]


def get_timeframe_klines(symbol, timeframe, limit=100):
    """Use MEXC native intervals or aggregate 3m/45m from supported intervals."""
    if timeframe == "3m":
        base = get_klines(symbol, "1m", limit * 3 + 6)
        return aggregate_klines(base, 1, 3, limit)

    if timeframe == "45m":
        base = get_klines(symbol, "15m", limit * 3 + 6)
        return aggregate_klines(base, 15, 45, limit)

    if timeframe in ("1m", "5m", "15m", "30m", "1h"):
        return get_klines(symbol, timeframe, limit)

    raise ValueError(f"Unsupported timeframe: {timeframe}")

def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = (value - result) * multiplier + result

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period
        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    return 100 - (100 / (1 + avg_gain / avg_loss))


def atr(candles, period=14):
    if len(candles) <= period:
        return None

    ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])

        ranges.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        ))

    return sum(ranges[-period:]) / period


def ichimoku(candles):
    if len(candles) < 80:
        return None

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    def midpoint(period, index):
        start = index - period + 1

        if start < 0:
            return None

        highest = max(highs[start:index + 1])
        lowest = min(lows[start:index + 1])

        return (highest + lowest) / 2

    last = len(candles) - 1
    cloud_index = last - 26

    # Current Tenkan and Kijun
    tenkan = midpoint(9, last)
    kijun = midpoint(26, last)

    # Cloud currently visible at the latest candle
    old_tenkan = midpoint(9, cloud_index)
    old_kijun = midpoint(26, cloud_index)
    span_b = midpoint(52, cloud_index)

    if None in (tenkan, kijun, old_tenkan, old_kijun, span_b):
        return None

    span_a = (old_tenkan + old_kijun) / 2

    # Projected future cloud
    future_span_a = (tenkan + kijun) / 2
    future_span_b = midpoint(52, last)

    price = closes[-1]

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)

    bullish = (
        price > cloud_top
        and tenkan > kijun
        and future_span_a > future_span_b
    )

    bearish = (
        price < cloud_bottom
        and tenkan < kijun
        and future_span_a < future_span_b
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "future_span_a": future_span_a,
        "future_span_b": future_span_b,
        "bullish": bullish,
        "bearish": bearish
    }



    

def build_trade_plan(direction, entry, support, resistance, current_atr, min_rr=2.0):
    """Build an ATR/structure-based plan; reject invalid or excessive risk."""
    if current_atr is None or current_atr <= 0 or entry <= 0:
        return None
    buffer = current_atr * 0.15
    max_risk = current_atr * 1.50
    min_risk = current_atr * 0.40
    tolerance = max(current_atr * 1e-9, 1e-12)  # absorb floating-point noise at exact ATR limits
    if direction == "LONG":
        stop = max(support - buffer, entry - max_risk)
        risk = entry - stop
        if risk < min_risk - tolerance or risk > max_risk + tolerance or stop >= entry:
            return None
        if risk > max_risk:
            stop = entry - max_risk
            risk = max_risk
        target = entry + risk * min_rr
        reward = target - entry
    elif direction == "SHORT":
        stop = min(resistance + buffer, entry + max_risk)
        risk = stop - entry
        if risk < min_risk - tolerance or risk > max_risk + tolerance or stop <= entry:
            return None
        if risk > max_risk:
            stop = entry + max_risk
            risk = max_risk
        target = entry - risk * min_rr
        reward = entry - target
    else:
        return None
    if reward <= 0 or risk <= 0:
        return None
    return {"entry": entry, "stop_loss": stop, "take_profit": target,
            "risk_amount": risk, "reward_amount": reward, "rr": reward / risk}


def diagnose_trade_plan(direction, entry, support, resistance, current_atr, min_rr=2.0):
    """Explain exactly why the unchanged trade-plan rules accept/reject a candidate."""
    if current_atr is None or current_atr <= 0 or entry <= 0:
        return {"valid": False, "reason": "INVALID_ATR_OR_ENTRY", "risk_atr": None,
                "sl_atr": None, "tp_atr": None, "rr": None, "stop": None, "target": None}
    buffer = current_atr * 0.15
    max_risk = current_atr * 1.50
    min_risk = current_atr * 0.40
    tolerance = max(current_atr * 1e-9, 1e-12)  # match build_trade_plan boundary handling
    if direction == "LONG":
        stop = max(support - buffer, entry - max_risk)
        risk = entry - stop
        wrong_side = stop >= entry
        target = entry + risk * min_rr
        reward = target - entry
    elif direction == "SHORT":
        stop = min(resistance + buffer, entry + max_risk)
        risk = stop - entry
        wrong_side = stop <= entry
        target = entry - risk * min_rr
        reward = entry - target
    else:
        return {"valid": False, "reason": "INVALID_DIRECTION", "risk_atr": None,
                "sl_atr": None, "tp_atr": None, "rr": None, "stop": None, "target": None}

    if risk > max_risk and risk <= max_risk + tolerance:
        risk = max_risk
        if direction == "LONG":
            stop = entry - max_risk
        else:
            stop = entry + max_risk
        reward = abs(target - entry)
    risk_atr = risk / current_atr
    if wrong_side or risk <= 0:
        reason = "STOP_ON_WRONG_SIDE"
    elif risk < min_risk - tolerance:
        reason = "RISK_BELOW_0_40_ATR"
    elif risk > max_risk + tolerance:
        reason = "RISK_ABOVE_1_50_ATR"
    elif reward <= 0:
        reason = "INVALID_REWARD"
    elif risk > 0 and reward / risk < min_rr:
        reason = "RR_BELOW_2_00"
    else:
        reason = "VALID"

    valid = reason == "VALID"
    return {"valid": valid, "reason": reason, "risk_atr": risk_atr,
            "sl_atr": risk_atr, "tp_atr": (reward / current_atr),
            "rr": (reward / risk if risk > 0 else None),
            "stop": stop, "target": target}


def analyze_candles(symbol, c3, c15, c45):
    """Analyze already-closed 3m/15m/45m candles; shared by live scan and backtest."""
    if len(c3) < 80 or len(c15) < 80 or len(c45) < 80:
        return None

    closes3 = [float(c[4]) for c in c3]
    volumes3 = [float(c[5]) for c in c3]
    price = closes3[-1]
    previous_close = closes3[-2]

    current_rsi = rsi(closes3)
    current_atr = atr(c3)
    ichi3 = ichimoku(c3)
    ichi15 = ichimoku(c15)
    ichi45 = ichimoku(c45)

    if (
        current_rsi is None
        or current_atr is None
        or current_atr <= 0
        or ichi3 is None
        or ichi15 is None
        or ichi45 is None
    ):
        return None

    avg_volume = sum(volumes3[-21:-1]) / 20
    if avg_volume <= 0:
        return None
    volume_ratio = volumes3[-1] / avg_volume

    # Use the previous 20 completed 3m candles for local levels.
    previous = c3[-21:-1]
    resistance = max(float(c[2]) for c in previous)
    support = min(float(c[3]) for c in previous)

    long_breakdown = {
        "trend": 0,
        "support_resistance": 0,
        "breakout": 0,
        "volume": 0,
        "momentum": 0,
        "risk_reward": 0
    }
    short_breakdown = {
        "trend": 0,
        "support_resistance": 0,
        "breakout": 0,
        "volume": 0,
        "momentum": 0,
        "risk_reward": 0
    }

    # Higher-timeframe trend confirmation; 3m Ichimoku is displayed as context.
    if ichi15["bullish"] and ichi45["bullish"]:
        long_breakdown["trend"] = 20
    if ichi15["bearish"] and ichi45["bearish"]:
        short_breakdown["trend"] = 20

    if abs(price - support) <= current_atr * 0.6:
        long_breakdown["support_resistance"] = 20
    if abs(price - resistance) <= current_atr * 0.6:
        short_breakdown["support_resistance"] = 20

    long_breakout = (
        price > resistance
        and (previous_close <= resistance or price - resistance <= current_atr * 0.5)
    )
    short_breakout = (
        price < support
        and (previous_close >= support or support - price <= current_atr * 0.5)
    )
    if long_breakout:
        long_breakdown["breakout"] = 25
    if short_breakout:
        short_breakdown["breakout"] = 25

    if volume_ratio >= 1.5:
        volume_score = 15
    elif volume_ratio >= 1.2:
        volume_score = 10
    elif volume_ratio >= 1.0:
        volume_score = 5
    else:
        volume_score = 0
    long_breakdown["volume"] = volume_score
    short_breakdown["volume"] = volume_score

    if 50 <= current_rsi <= 68:
        long_breakdown["momentum"] = 10
    elif 45 <= current_rsi < 50:
        long_breakdown["momentum"] = 5
    if 32 <= current_rsi <= 50:
        short_breakdown["momentum"] = 10
    elif 50 < current_rsi <= 55:
        short_breakdown["momentum"] = 5

    recent_range = resistance - support
    long_stop = resistance - 0.5 * current_atr if long_breakout else support - 0.5 * current_atr
    short_stop = support + 0.5 * current_atr if short_breakout else resistance + 0.5 * current_atr
    long_target = price + recent_range if long_breakout else resistance
    short_target = price - recent_range if short_breakout else support

    long_risk = price - long_stop
    short_risk = short_stop - price
    long_rr = 0.0
    short_rr = 0.0
    if long_risk > 0 and long_target > price and long_stop < price:
        long_rr = (long_target - price) / long_risk
    if short_risk > 0 and short_target < price and short_stop > price:
        short_rr = (price - short_target) / short_risk

    if long_rr >= 2:
        long_breakdown["risk_reward"] = 10
    elif long_rr >= 1.5:
        long_breakdown["risk_reward"] = 5
    if short_rr >= 2:
        short_breakdown["risk_reward"] = 10
    elif short_rr >= 1.5:
        short_breakdown["risk_reward"] = 5

    long_score = sum(long_breakdown.values())
    short_score = sum(short_breakdown.values())

    # Early-entry filter: only consider setups close to the 3m breakout level.
    # A move already stretched away from the level or short-term EMA is rejected.
    ema9 = ema(closes3, 9)
    last_candle_range = float(c3[-1][2]) - float(c3[-1][3])

    long_level_distance_atr = abs(price - resistance) / current_atr
    short_level_distance_atr = abs(price - support) / current_atr
    ema_extension_atr = abs(price - ema9) / current_atr if ema9 else 999.0
    candle_range_atr = last_candle_range / current_atr

    long_near_level = long_level_distance_atr <= 0.40
    short_near_level = short_level_distance_atr <= 0.40

    long_late = (
        (price > resistance and price - resistance > current_atr * 0.60)
        or (ema9 is not None and price > ema9 and price - ema9 > current_atr * 0.80)
        or candle_range_atr > 1.80
    )
    short_late = (
        (price < support and support - price > current_atr * 0.60)
        or (ema9 is not None and price < ema9 and ema9 - price > current_atr * 0.80)
        or candle_range_atr > 1.80
    )

    long_early_candidate = (
        ichi15["bullish"] and ichi45["bullish"]
        and long_near_level
        and 48 <= current_rsi <= 68
        and volume_ratio >= 0.80
    )
    short_early_candidate = (
        ichi15["bearish"] and ichi45["bearish"]
        and short_near_level
        and 32 <= current_rsi <= 52
        and volume_ratio >= 0.80
    )

    late_reasons = []
    if long_late and long_score >= short_score:
        late_reasons.append("LONG move is overextended")
    if short_late and short_score > long_score:
        late_reasons.append("SHORT move is overextended")

    if long_late and long_score >= 65 and long_score > short_score:
        signal, score = "LATE ENTRY — NO TRADE", long_score
        setup_direction = "LONG"
    elif short_late and short_score >= 65 and short_score > long_score:
        signal, score = "LATE ENTRY — NO TRADE", short_score
        setup_direction = "SHORT"
    elif long_early_candidate and long_score >= 65 and long_score > short_score:
        signal, score = "EARLY LONG SETUP", long_score
        setup_direction = "LONG"
    elif short_early_candidate and short_score >= 65 and short_score > long_score:
        signal, score = "EARLY SHORT SETUP", short_score
        setup_direction = "SHORT"
    elif max(long_score, short_score) >= 60:
        signal, score = "WATCH", max(long_score, short_score)
        setup_direction = "NONE"
    else:
        signal, score = "NO ENTRY", max(long_score, short_score)
        setup_direction = "NONE"

    if setup_direction == "LONG":
        setup_distance_atr = long_level_distance_atr
    elif setup_direction == "SHORT":
        setup_distance_atr = short_level_distance_atr
    else:
        setup_distance_atr = min(long_level_distance_atr, short_level_distance_atr)

    trade_plan = None
    if signal in ("EARLY LONG SETUP", "EARLY SHORT SETUP"):
        trade_plan = build_trade_plan(
            setup_direction, price, support, resistance, current_atr, min_rr=2.0
        )
        if trade_plan is None:
            signal = "WATCH — INVALID RISK"
            setup_direction = "NONE"
            score = max(long_score, short_score)

    def ichi_state(data):
        return "BULLISH" if data["bullish"] else "BEARISH" if data["bearish"] else "NEUTRAL"

    return {
        "long_breakdown": long_breakdown,
        "short_breakdown": short_breakdown,
        "symbol": symbol,
        "price": price,
        "signal": signal,
        "score": score,
        "long_score": long_score,
        "short_score": short_score,
        "rsi": current_rsi,
        "volume_ratio": volume_ratio,
        "atr": current_atr,
        "support": support,
        "resistance": resistance,
        "long_rr": long_rr,
        "short_rr": short_rr,
        "long_risk": long_risk,
        "short_risk": short_risk,
        "ichimoku_3m": ichi_state(ichi3),
        "ichimoku_15m": ichi_state(ichi15),
        "ichimoku_45m": ichi_state(ichi45),
        "setup_direction": setup_direction,
        "setup_distance_atr": setup_distance_atr,
        "ema_extension_atr": ema_extension_atr,
        "candle_range_atr": candle_range_atr,
        "late_reason": "; ".join(late_reasons) if late_reasons else "",
        "diagnostic": {
            "long_trend": bool(ichi15["bullish"] and ichi45["bullish"]),
            "short_trend": bool(ichi15["bearish"] and ichi45["bearish"]),
            "long_near_level": bool(long_near_level),
            "short_near_level": bool(short_near_level),
            "long_rsi_ok": bool(48 <= current_rsi <= 68),
            "short_rsi_ok": bool(32 <= current_rsi <= 52),
            "volume_ok": bool(volume_ratio >= 0.80),
            "long_score_ok": bool(long_score >= 65 and long_score > short_score),
            "short_score_ok": bool(short_score >= 65 and short_score > long_score),
            "long_early_candidate": bool(long_early_candidate),
            "short_early_candidate": bool(short_early_candidate),
            "trade_plan_valid": bool(trade_plan is not None),
            "signal": signal
        },
        "trade_plan": trade_plan
    }




def analyze_symbol(symbol):
    # Live analysis uses only completed candles from all three timeframes.
    c3 = get_timeframe_klines(symbol, "3m", 100)
    c15 = get_timeframe_klines(symbol, "15m", 100)
    c45 = get_timeframe_klines(symbol, "45m", 100)
    return analyze_candles(symbol, c3, c15, c45)


def get_klines_between(symbol, interval, start_ts, end_ts):
    """Fetch historical candles in small time windows to avoid oversized requests."""
    interval_map = {"1m": "Min1", "15m": "Min15"}
    interval_seconds = {"1m": 60, "15m": 900}
    if interval not in interval_map:
        raise ValueError(f"Unsupported historical interval: {interval}")

    step = interval_seconds[interval]
    chunk_seconds = step * 500
    candles_by_time = {}
    cursor = int(start_ts)
    now = int(time.time())

    while cursor < int(end_ts):
        chunk_end = min(cursor + chunk_seconds, int(end_ts), now)
        if chunk_end <= cursor:
            break
        result = get_json(
            f"/api/v1/contract/kline/{symbol}",
            params={"interval": interval_map[interval], "start": cursor, "end": chunk_end}
        )
        data = result.get("data", {})
        times = data.get("time", [])
        for i, timestamp in enumerate(times):
            timestamp = int(timestamp)
            if timestamp < start_ts or timestamp + step > end_ts or timestamp + step > now:
                continue
            candles_by_time[timestamp] = [
                timestamp * 1000,
                float(data["open"][i]),
                float(data["high"][i]),
                float(data["low"][i]),
                float(data["close"][i]),
                float(data["vol"][i])
            ]
        cursor = chunk_end
        time.sleep(0.05)

    return [candles_by_time[t] for t in sorted(candles_by_time)]


def backtest_symbol(symbol, days=14, fee_round_trip=0.001, slippage_round_trip=0.0004, max_hold_bars=60):
    """Conservative historical simulation. Entry is next 3m open; same-bar SL/TP => SL."""
    end_ts = int(time.time())
    start_ts = end_ts - int(days * 86400)
    print(f"Downloading {symbol} history for {days} days...")
    raw1 = get_klines_between(symbol, "1m", start_ts - 6 * 3600, end_ts)
    raw15 = get_klines_between(symbol, "15m", start_ts - 4 * 86400, end_ts)
    c3 = aggregate_klines(raw1, 1, 3, max(1, len(raw1) // 3))
    c15 = raw15
    c45 = aggregate_klines(raw15, 15, 45, max(1, len(raw15) // 3))

    if len(c3) < 100 or len(c15) < 80 or len(c45) < 80:
        raise RuntimeError(
            f"Not enough complete history: 3m={len(c3)}, 15m={len(c15)}, 45m={len(c45)}. "
            "Increase --days or retry later."
        )

    trades = []
    diag = {
        "evaluated": 0,
        "long_trend": 0, "short_trend": 0,
        "long_near_level": 0, "short_near_level": 0,
        "long_rsi_ok": 0, "short_rsi_ok": 0,
        "volume_ok": 0,
        "long_score_ok": 0, "short_score_ok": 0,
        "long_early_candidate": 0, "short_early_candidate": 0,
        "plan_valid": 0,
        "candidate_plans": 0,
        "candidate_plan_valid": 0,
        "plan_invalid_atr": 0,
        "plan_wrong_side": 0,
        "plan_risk_below": 0,
        "plan_risk_above": 0,
        "plan_invalid_reward": 0,
        "plan_rr_below": 0,
        "candidate_risk_atr": [],
        "candidate_tp_atr": [],
        "candidate_examples": [],
        "watch": 0, "late_no_trade": 0
    }
    i = 79
    j15 = 0
    j45 = 0
    while i < len(c3) - 1:
        signal_candle = c3[i]
        signal_close_time = int(signal_candle[0] // 1000) + 180
        while j15 < len(c15) and int(c15[j15][0] // 1000) + 900 <= signal_close_time:
            j15 += 1
        while j45 < len(c45) and int(c45[j45][0] // 1000) + 2700 <= signal_close_time:
            j45 += 1
        if j15 < 80 or j45 < 80:
            i += 1
            continue

        # Indicators need recent history only; cap windows to keep long tests efficient.
        hist3 = c3[max(0, i - 119):i + 1]
        hist15 = c15[max(0, j15 - 120):j15]
        hist45 = c45[max(0, j45 - 120):j45]
        result = analyze_candles(symbol, hist3, hist15, hist45)
        if not result:
            i += 1
            continue

        diag["evaluated"] += 1
        flags = result["diagnostic"]
        for key in ("long_trend", "short_trend", "long_near_level", "short_near_level",
                    "long_rsi_ok", "short_rsi_ok", "volume_ok", "long_score_ok",
                    "short_score_ok", "long_early_candidate", "short_early_candidate"):
            diag[key] += int(flags[key])
        if flags["signal"] == "WATCH":
            diag["watch"] += 1
        if flags["signal"] == "LATE ENTRY — NO TRADE":
            diag["late_no_trade"] += 1

        # Diagnose every early candidate independently of score ranking/selection.
        # This reveals whether the plan builder itself is the bottleneck.
        for direction, flag_name in (("LONG", "long_early_candidate"), ("SHORT", "short_early_candidate")):
            if not flags[flag_name]:
                continue
            detail = diagnose_trade_plan(
                direction, float(result["price"]), float(result["support"]),
                float(result["resistance"]), float(result["atr"]), min_rr=2.0
            )
            diag["candidate_plans"] += 1
            if detail["valid"]:
                diag["candidate_plan_valid"] += 1
            else:
                reason_to_key = {
                    "INVALID_ATR_OR_ENTRY": "plan_invalid_atr",
                    "STOP_ON_WRONG_SIDE": "plan_wrong_side",
                    "RISK_BELOW_0_40_ATR": "plan_risk_below",
                    "RISK_ABOVE_1_50_ATR": "plan_risk_above",
                    "INVALID_REWARD": "plan_invalid_reward",
                    "RR_BELOW_2_00": "plan_rr_below",
                }
                key = reason_to_key.get(detail["reason"])
                if key:
                    diag[key] += 1
            if detail["risk_atr"] is not None and detail["risk_atr"] > 0:
                diag["candidate_risk_atr"].append(detail["risk_atr"])
            if detail["tp_atr"] is not None and detail["tp_atr"] > 0:
                diag["candidate_tp_atr"].append(detail["tp_atr"])
            if len(diag["candidate_examples"]) < 12:
                stamp = datetime.fromtimestamp(
                    int(signal_candle[0] // 1000), timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")
                diag["candidate_examples"].append({
                    "time": stamp, "direction": direction, "entry": result["price"],
                    "atr": result["atr"], "reason": detail["reason"],
                    "risk_atr": detail["risk_atr"], "tp_atr": detail["tp_atr"],
                    "rr": detail["rr"], "stop": detail["stop"], "target": detail["target"]
                })

        if result.get("signal") not in ("EARLY LONG SETUP", "EARLY SHORT SETUP"):
            i += 1
            continue

        plan = result.get("trade_plan")
        if not plan:
            i += 1
            continue
        diag["plan_valid"] += 1

        direction = result["setup_direction"]
        entry = float(c3[i + 1][1])  # next candle open; avoids assuming fill at signal close
        planned_entry = float(plan["entry"])
        shift = entry - planned_entry
        stop = float(plan["stop_loss"]) + shift
        target = float(plan["take_profit"]) + shift
        risk = (entry - stop) if direction == "LONG" else (stop - entry)
        if risk <= 0:
            i += 1
            continue

        exit_price = None
        exit_reason = "TIME"
        exit_index = min(i + 1 + max_hold_bars, len(c3) - 1)
        resolved = False
        for j in range(i + 1, exit_index + 1):
            bar = c3[j]
            high, low = float(bar[2]), float(bar[3])
            if direction == "LONG":
                stop_hit = low <= stop
                target_hit = high >= target
            else:
                stop_hit = high >= stop
                target_hit = low <= target

            # Conservative assumption when both levels occur inside one 3m candle.
            if stop_hit:
                exit_price, exit_reason, resolved = stop, "SL", True
                exit_index = j
                break
            if target_hit:
                exit_price, exit_reason, resolved = target, "TP", True
                exit_index = j
                break

        if not resolved:
            # A full time-stop is included; a truncated end-of-data trade is excluded.
            if i + 1 + max_hold_bars >= len(c3):
                break
            exit_price = float(c3[exit_index][4])
            exit_reason = "TIME"

        gross = ((exit_price - entry) / entry) if direction == "LONG" else ((entry - exit_price) / entry)
        net = gross - fee_round_trip - slippage_round_trip
        risk_pct = risk / entry
        r_multiple = net / risk_pct if risk_pct > 0 else 0.0
        trades.append({"direction": direction, "entry": entry, "exit": exit_price,
                       "reason": exit_reason, "net_pct": net * 100, "r": r_multiple,
                       "bars": exit_index - i})
        i = exit_index + 1  # no overlapping positions

    wins = [t for t in trades if t["net_pct"] > 0]
    losses = [t for t in trades if t["net_pct"] <= 0]
    total_net = sum(t["net_pct"] for t in trades)
    avg_r = sum(t["r"] for t in trades) / len(trades) if trades else 0.0
    gross_profit = sum(t["net_pct"] for t in wins)
    gross_loss = abs(sum(t["net_pct"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0)

    print("\nNIMA SCALP RADAR — BACKTEST")
    print(f"Symbol: {symbol} | Period: {days} days")
    print(f"Closed candles: 3m={len(c3)}, 15m={len(c15)}, 45m={len(c45)}")
    print(f"Trades: {len(trades)} | Wins: {len(wins)} | Losses: {len(losses)}")
    print("\nDIAGNOSTIC FILTER COUNTS (overlapping counts; not additive):")
    print(f"Evaluated candles: {diag['evaluated']}")
    print(f"Trend aligned | LONG: {diag['long_trend']} | SHORT: {diag['short_trend']}")
    print(f"Near level     | LONG: {diag['long_near_level']} | SHORT: {diag['short_near_level']}")
    print(f"RSI in range   | LONG: {diag['long_rsi_ok']} | SHORT: {diag['short_rsi_ok']}")
    print(f"Volume >= 0.8x: {diag['volume_ok']}")
    print(f"Score >= 65    | LONG: {diag['long_score_ok']} | SHORT: {diag['short_score_ok']}")
    print(f"Early candidate| LONG: {diag['long_early_candidate']} | SHORT: {diag['short_early_candidate']}")
    print(f"Selected-signal valid plans: {diag['plan_valid']} | WATCH: {diag['watch']} | Late rejected: {diag['late_no_trade']}")
    print("\nTRADE-PLAN DIAGNOSTICS (all early candidates, both directions):")
    print(f"Candidates checked: {diag['candidate_plans']} | Valid plans: {diag['candidate_plan_valid']}")
    print(f"Rejected | invalid ATR/entry: {diag['plan_invalid_atr']} | stop wrong side: {diag['plan_wrong_side']}")
    print(f"Rejected | risk < 0.40 ATR: {diag['plan_risk_below']} | risk > 1.50 ATR: {diag['plan_risk_above']}")
    print(f"Rejected | invalid reward: {diag['plan_invalid_reward']} | RR < 2.00: {diag['plan_rr_below']}")
    if diag["candidate_risk_atr"]:
        vals = diag["candidate_risk_atr"]
        print(f"Risk distance (ATR): min={min(vals):.3f} | avg={sum(vals)/len(vals):.3f} | max={max(vals):.3f}")
    if diag["candidate_tp_atr"]:
        vals = diag["candidate_tp_atr"]
        print(f"Target distance (ATR): min={min(vals):.3f} | avg={sum(vals)/len(vals):.3f} | max={max(vals):.3f}")
    print("\nFIRST CANDIDATE EXAMPLES:")
    for item in diag["candidate_examples"]:
        risk_text = f"{item['risk_atr']:.3f} ATR" if item["risk_atr"] is not None else "n/a"
        rr_text = f"{item['rr']:.2f}" if item["rr"] is not None else "n/a"
        print(f"{item['time']} | {item['direction']} | {item['reason']} | risk={risk_text} | RR={rr_text} | SL={item['stop']} | TP={item['target']}")
    print(f"Win rate: {(len(wins) / len(trades) * 100) if trades else 0:.1f}%")
    print(f"Net result: {total_net:.2f}% | Average R: {avg_r:.2f} | Profit factor: {profit_factor:.2f}")
    print(f"Assumed round-trip fees: {fee_round_trip*100:.3f}% | Slippage: {slippage_round_trip*100:.3f}%")
    print("Execution: next 3m open; if SL and TP touch in same candle, counted as SL.")
    print("\nLast 10 trades:")
    for trade in trades[-10:]:
        print(f"{trade['direction']} | {trade['reason']} | net {trade['net_pct']:.3f}% | {trade['r']:.2f}R | {trade['bars']} bars")

    return {"trades": trades, "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
            "net_pct": total_net, "average_r": avg_r, "profit_factor": profit_factor}


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram secrets missing.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    # Split message into chunks below Telegram's limit
    chunks = [
        message[i:i + 3500]
        for i in range(0, len(message), 3500)
    ]

    for chunk in chunks:
        response = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": chunk
            },
            timeout=20
        )

        print("Telegram HTTP:", response.status_code)
        print("Telegram response:", response.text[:1000])

        response.raise_for_status()

    print("Telegram message sent.")




def main():
    top = get_top_contracts()

    results = []
    errors = []

    print("NIMA SCALP RADAR - ANALYSIS")
    print("==========================")

    for item in top:
        symbol = item["symbol"]

        try:
            result = analyze_symbol(symbol)

            if result is None:
                errors.append(symbol)
                continue

            results.append(result)

            print(
    f"{symbol} | {result['signal']} | "
    f"Score: {result['score']} | "
    f"LONG: {result['long_score']} | "
    f"SHORT: {result['short_score']} | "
    f"Ichimoku 3m: {result['ichimoku_3m']} | "
    f"15m: {result['ichimoku_15m']} | "
    f"45m: {result['ichimoku_45m']} | "
    f"RSI: {result['rsi']:.1f} | "
    f"Volume: {result['volume_ratio']:.2f}x"
            )

        except Exception as error:
            print(f"ERROR {symbol}: {error}")
            errors.append(symbol)

        time.sleep(0.15)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    lines = [
        "NIMA SCALP RADAR",
        "MEXC Futures | 3m + 15m + 45m",
        "--------------------------"
    ]

    for result in results:
        if result["signal"] == "NO ENTRY" or result["signal"].startswith("WATCH"):
            continue

        lines.extend([
            f"{result['symbol']} - {result['signal']}",
            f"Score: {result['score']}/100",
            f"Price: {result['price']}",
            f"Setup: {result['setup_direction']} | Level distance: {result['setup_distance_atr']:.2f} ATR",
            *([
                f"ENTRY: {result['trade_plan']['entry']:.8g}",
                f"STOP LOSS: {result['trade_plan']['stop_loss']:.8g}",
                f"TAKE PROFIT: {result['trade_plan']['take_profit']:.8g}",
                f"RISK/REWARD: 1:{result['trade_plan']['rr']:.2f}"
            ] if result.get("trade_plan") else []),
            f"EMA extension: {result['ema_extension_atr']:.2f} ATR | Candle range: {result['candle_range_atr']:.2f} ATR",
            *([f"Filter: {result['late_reason']}"] if result['late_reason'] else []),
            f"RSI: {result['rsi']:.1f}",
            f"Volume: {result['volume_ratio']:.2f}x",
            f"Ichimoku 3m: {result['ichimoku_3m']}",
            f"Ichimoku 15m: {result['ichimoku_15m']}",
            f"Ichimoku 45m: {result['ichimoku_45m']}",
            f"Support: {result['support']}",
            f"Resistance: {result['resistance']}",
            f"Estimated Long RR: {result['long_rr']:.2f}",
            f"Estimated Short RR: {result['short_rr']:.2f}",
            "--------------------------"
        ])

    if len(lines) == 3:
        lines.append("No qualifying setup. NO ENTRY.")

    lines.append(f"Analyzed: {len(results)}/{len(top)}")
    lines.append(f"Data errors: {len(errors)}")

    message = "\n".join(lines)

    print("\n" + message)

    send_telegram(message)

    print("\nANALYSIS FINISHED")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NIMA SCALP RADAR")
    parser.add_argument("--backtest", metavar="SYMBOL", help="Run historical backtest for one MEXC USDT perpetual symbol")
    parser.add_argument("--days", type=int, default=14, help="Backtest history window in days (default: 14)")
    parser.add_argument("--fee", type=float, default=0.001, help="Assumed round-trip fee as decimal (default: 0.001 = 0.10%%)")
    parser.add_argument("--slippage", type=float, default=0.0004, help="Assumed round-trip slippage as decimal (default: 0.0004 = 0.04%%)")
    args = parser.parse_args()
    if args.backtest:
        if args.days < 5 or args.days > 90:
            parser.error("--days must be between 5 and 90")
        backtest_symbol(args.backtest.upper(), args.days, args.fee, args.slippage)
    else:
        main()

