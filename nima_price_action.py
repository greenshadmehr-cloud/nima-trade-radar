import time
import requests
from collections import defaultdict
from datetime import datetime, timezone


# =========================================================
# NIMA PRICE ACTION SCALPER
# MEXC USDT Perpetual Futures
# 15m Structure -> 3m BOS/Retest -> 1m Liquidity Sweep
# =========================================================

BASE_URL = "https://contract.mexc.com"

TOP_N = 30
MAX_PRICE = 50.0

RISK_REWARD = 2.0
MAX_RETEST_AGE = 3
MAX_TRIGGER_AGE = 2

SWING_LEFT = 2
SWING_RIGHT = 2

RETEST_TOLERANCE = 0.0015

CANDLE_LIMIT_15M = 160
CANDLE_LIMIT_1M = 500

TIMEOUT = 15

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 NIMA-PRICE-ACTION-SCALPER"
})


# =========================================================
# GENERAL
# =========================================================

def get_json(url, params=None):
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=TIMEOUT
        )
        response.raise_for_status()

        result = response.json()

        if isinstance(result, dict):
            if result.get("success") is False:
                return None

            if result.get("code") not in (None, 0, 200):
                return None

        return result

    except Exception as error:
        print(f"API ERROR: {error}")
        return None


def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_time(timestamp):
    try:
        return datetime.fromtimestamp(
            timestamp,
            timezone.utc
        ).strftime("%H:%M:%S UTC")
    except Exception:
        return str(timestamp)


# =========================================================
# MARKET DATA
# =========================================================

def get_top_contracts():
    print("Getting MEXC contract information...")

    detail_response = get_json(
        f"{BASE_URL}/api/v1/contract/detail"
    )

    ticker_response = get_json(
        f"{BASE_URL}/api/v1/contract/ticker"
    )

    if not detail_response or not ticker_response:
        print("Could not get contract information.")
        return []

    details = detail_response.get("data", [])
    tickers = ticker_response.get("data", [])

    if not isinstance(details, list):
        return []

    if not isinstance(tickers, list):
        return []

    ticker_map = {}

    for ticker in tickers:
        symbol = ticker.get("symbol")

        if symbol:
            ticker_map[symbol] = ticker

    eligible = []

    for contract in details:
        symbol = contract.get("symbol")

        if not symbol or symbol not in ticker_map:
            continue

        if contract.get("quoteCoin") != "USDT":
            continue

        if contract.get("settleCoin") != "USDT":
            continue

        if contract.get("state") not in (0, "0"):
            continue

        ticker = ticker_map[symbol]

        price = to_float(
            ticker.get("lastPrice")
            or ticker.get("fairPrice")
        )

        amount = to_float(
            ticker.get("amount24")
            or ticker.get("turnover24")
            or ticker.get("volume24")
        )

        if price <= 0 or price > MAX_PRICE:
            continue

        if amount <= 0:
            continue

        eligible.append({
            "symbol": symbol,
            "price": price,
            "amount": amount
        })

    eligible.sort(
        key=lambda item: item["amount"],
        reverse=True
    )

    selected = eligible[:TOP_N]

    print(f"Eligible contracts: {len(eligible)}")
    print(f"Selected contracts: {len(selected)}")

    return selected


def get_candles(symbol, interval, limit, seconds):
    now = int(time.time())

    start = now - (limit * seconds) - seconds
    end = now

    response = get_json(
        f"{BASE_URL}/api/v1/contract/kline/{symbol}",
        params={
            "interval": interval,
            "start": start,
            "end": end
        }
    )

    if not response:
        return []

    data = response.get("data")

    if not isinstance(data, dict):
        return []

    timestamps = (
        data.get("time")
        or data.get("timestamp")
        or []
    )

    opens = data.get("open", [])
    highs = data.get("high", [])
    lows = data.get("low", [])
    closes = data.get("close", [])
    volumes = (
        data.get("vol")
        or data.get("volume")
        or []
    )

    candles = []

    if not all([
        timestamps,
        opens,
        highs,
        lows,
        closes
    ]):
        return []

    count = min(
        len(timestamps),
        len(opens),
        len(highs),
        len(lows),
        len(closes)
    )

    for i in range(count):
        timestamp = int(float(timestamps[i]))

        # Ignore the currently open candle.
        if timestamp + seconds > now:
            continue

        candle = [
            timestamp,
            to_float(opens[i]),
            to_float(highs[i]),
            to_float(lows[i]),
            to_float(closes[i]),
            to_float(volumes[i]) if i < len(volumes) else 0.0
        ]

        if candle[1] <= 0:
            continue

        candles.append(candle)

    candles.sort(key=lambda candle: candle[0])

    return candles[-limit:]


def get_15m_candles(symbol):
    return get_candles(
        symbol,
        "Min15",
        CANDLE_LIMIT_15M,
        900
    )


def get_1m_candles(symbol):
    return get_candles(
        symbol,
        "Min1",
        CANDLE_LIMIT_1M,
        60
    )


# =========================================================
# 3 MINUTE CANDLE AGGREGATION
# =========================================================

def get_3m_candles_from_1m(candles_1m):
    buckets = defaultdict(list)

    for candle in candles_1m:
        timestamp = candle[0]
        bucket = (timestamp // 180) * 180
        buckets[bucket].append(candle)

    candles_3m = []

    for bucket in sorted(buckets):
        group = sorted(
            buckets[bucket],
            key=lambda candle: candle[0]
        )

        # A valid 3m candle must contain all 3 one-minute candles.
        expected = [
            bucket,
            bucket + 60,
            bucket + 120
        ]

        actual = [
            candle[0] for candle in group
        ]

        if actual != expected:
            continue

        candles_3m.append([
            bucket,
            group[0][1],
            max(candle[2] for candle in group),
            min(candle[3] for candle in group),
            group[-1][4],
            sum(candle[5] for candle in group)
        ])

    return candles_3m


# =========================================================
# SWING DETECTION
# =========================================================

def detect_swings(
    candles,
    left=SWING_LEFT,
    right=SWING_RIGHT
):
    swings = []

    if len(candles) < left + right + 3:
        return swings

    for i in range(left, len(candles) - right):

        current_high = candles[i][2]
        current_low = candles[i][3]

        left_highs = [
            candles[j][2]
            for j in range(i - left, i)
        ]

        right_highs = [
            candles[j][2]
            for j in range(i + 1, i + right + 1)
        ]

        left_lows = [
            candles[j][3]
            for j in range(i - left, i)
        ]

        right_lows = [
            candles[j][3]
            for j in range(i + 1, i + right + 1)
        ]

        if (
            current_high > max(left_highs)
            and current_high > max(right_highs)
        ):
            swings.append({
                "index": i,
                "time": candles[i][0],
                "type": "HIGH",
                "price": current_high
            })

        if (
            current_low < min(left_lows)
            and current_low < min(right_lows)
        ):
            swings.append({
                "index": i,
                "time": candles[i][0],
                "type": "LOW",
                "price": current_low
            })

    swings.sort(key=lambda swing: swing["index"])

    return swings


# =========================================================
# 15M MARKET STRUCTURE
# =========================================================

def detect_market_structure(swings):
    highs = [
        swing for swing in swings
        if swing["type"] == "HIGH"
    ]

    lows = [
        swing for swing in swings
        if swing["type"] == "LOW"
    ]

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    previous_high = highs[-2]["price"]
    latest_high = highs[-1]["price"]

    previous_low = lows[-2]["price"]
    latest_low = lows[-1]["price"]

    bullish = (
        latest_high > previous_high
        and latest_low > previous_low
    )

    bearish = (
        latest_high < previous_high
        and latest_low < previous_low
    )

    if bullish:
        return "BULLISH"

    if bearish:
        return "BEARISH"

    return "NEUTRAL"


# =========================================================
# 3M BREAK OF STRUCTURE + RETEST
# =========================================================

def find_latest_bos_and_retest(
    candles,
    swings,
    right=SWING_RIGHT,
    tolerance=RETEST_TOLERANCE,
    max_bars=10
):
    result = {
        "bos": "NONE",
        "retest": "NO_BOS",
        "direction": None,
        "level": None,
        "bos_index": None,
        "retest_index": None,
        "reason": "No confirmed BOS"
    }

    if len(candles) < 15 or not swings:
        result["reason"] = "Insufficient 3m data"
        return result

    latest_bos = None

    for i in range(len(candles)):

        close = candles[i][4]
        previous_close = candles[i - 1][4] if i > 0 else close

        confirmed_swings = [
            swing for swing in swings
            if swing["index"] + right <= i
        ]

        available_highs = [
            swing for swing in confirmed_swings
            if swing["type"] == "HIGH"
        ]

        available_lows = [
            swing for swing in confirmed_swings
            if swing["type"] == "LOW"
        ]

        if available_highs:
            level_high = available_highs[-1]

            crossed_up = (
                previous_close <= level_high["price"]
                and close > level_high["price"]
            )

            if crossed_up:
                latest_bos = {
                    "direction": "BULLISH",
                    "level": level_high["price"],
                    "bos_index": i,
                    "retest_index": None,
                    "status": "WAITING_RETEST"
                }

        if available_lows:
            level_low = available_lows[-1]

            crossed_down = (
                previous_close >= level_low["price"]
                and close < level_low["price"]
            )

            if crossed_down:
                latest_bos = {
                    "direction": "BEARISH",
                    "level": level_low["price"],
                    "bos_index": i,
                    "retest_index": None,
                    "status": "WAITING_RETEST"
                }

        if latest_bos is None:
            continue

        direction = latest_bos["direction"]
        level = latest_bos["level"]
        bos_index = latest_bos["bos_index"]

        if i <= bos_index:
            continue

        age = i - bos_index

        if age > max_bars:
            latest_bos["status"] = "BOS_EXPIRED"
            continue

        candle = candles[i]

        if direction == "BULLISH":

            # Closing back below the broken level invalidates BOS.
            if candle[4] < level:
                latest_bos["status"] = "BOS_FAILED"
                latest_bos["retest_index"] = None
                continue

            touched = (
                candle[3] <= level * (1 + tolerance)
                and candle[3] >= level * (1 - tolerance)
            )

            held_above = candle[4] > level

            if touched and held_above:
                latest_bos["status"] = "BULLISH_RETEST"
                latest_bos["retest_index"] = i

        else:

            if candle[4] > level:
                latest_bos["status"] = "BOS_FAILED"
                latest_bos["retest_index"] = None
                continue

            touched = (
                candle[2] >= level * (1 - tolerance)
                and candle[2] <= level * (1 + tolerance)
            )

            held_below = candle[4] < level

            if touched and held_below:
                latest_bos["status"] = "BEARISH_RETEST"
                latest_bos["retest_index"] = i

    if latest_bos is None:
        return result

    result.update({
        "bos": latest_bos["direction"] + "_BOS",
        "retest": latest_bos["status"],
        "direction": latest_bos["direction"],
        "level": latest_bos["level"],
        "bos_index": latest_bos["bos_index"],
        "retest_index": latest_bos["retest_index"]
    })

    if latest_bos["status"] == "WAITING_RETEST":
        result["reason"] = "Waiting for pullback"

    elif latest_bos["status"] == "BOS_FAILED":
        result["reason"] = "BOS level was invalidated"

    elif latest_bos["status"] == "BOS_EXPIRED":
        result["reason"] = "BOS setup expired"

    elif latest_bos["status"] in (
        "BULLISH_RETEST",
        "BEARISH_RETEST"
    ):
        result["reason"] = "Retest confirmed"

    return result


# =========================================================
# 1M LIQUIDITY SWEEP + CONFIRMATION
# =========================================================

def detect_1m_trigger(candles, retest_time=None):
    result = {
        "trigger": "NONE",
        "status": "WAITING_SWEEP",
        "reason": "No liquidity sweep found",
        "entry": None,
        "stop_loss": None,
        "sweep_time": None,
        "confirm_time": None
    }

    if len(candles) < 10:
        result["status"] = "INSUFFICIENT_DATA"
        result["reason"] = "Not enough 1m candles"
        return result

    found_sweep = False
    latest_candidate = None

    # Search recent candles for a sweep occurring after the retest.
    start_index = max(5, len(candles) - 15)
    end_index = len(candles) - 1

    for i in range(start_index, end_index):

        sweep = candles[i]
        previous = candles[i - 5:i]

        if len(previous) < 5:
            continue

        if retest_time is not None and sweep[0] <= retest_time:
            continue

        previous_high = max(candle[2] for candle in previous)
        previous_low = min(candle[3] for candle in previous)

        confirm = candles[i + 1]

        bullish_sweep = (
            sweep[3] < previous_low
            and sweep[4] > previous_low
        )

        bearish_sweep = (
            sweep[2] > previous_high
            and sweep[4] < previous_high
        )

        if bullish_sweep:
            found_sweep = True

            bullish_confirmation = (
                confirm[4] > confirm[1]
                and confirm[4] > sweep[2]
            )

            latest_candidate = {
                "direction": "BULLISH",
                "sweep": sweep,
                "confirm": confirm,
                "confirmed": bullish_confirmation,
                "stop_loss": sweep[3]
            }

        elif bearish_sweep:
            found_sweep = True

            bearish_confirmation = (
                confirm[4] < confirm[1]
                and confirm[4] < sweep[3]
            )

            latest_candidate = {
                "direction": "BEARISH",
                "sweep": sweep,
                "confirm": confirm,
                "confirmed": bearish_confirmation,
                "stop_loss": sweep[2]
            }

    if not found_sweep or latest_candidate is None:
        return result

    sweep = latest_candidate["sweep"]
    confirm = latest_candidate["confirm"]

    result["sweep_time"] = sweep[0]
    result["confirm_time"] = confirm[0]

    confirmation_age = len(candles) - 1 - (
        candles.index(confirm)
    )

    if not latest_candidate["confirmed"]:
        result["status"] = "SWEEP_WAITING_CONFIRMATION"
        result["reason"] = "Liquidity sweep found; waiting for confirmation"
        return result

    if confirmation_age > MAX_TRIGGER_AGE:
        result["status"] = "TRIGGER_TOO_OLD"
        result["reason"] = "1m confirmation is too old"
        return result

    result["trigger"] = (
        latest_candidate["direction"] + "_TRIGGER"
    )

    result["status"] = "READY"
    result["reason"] = "Liquidity sweep and confirmation completed"
    result["entry"] = confirm[4]
    result["stop_loss"] = latest_candidate["stop_loss"]

    return result


# =========================================================
# FINAL SIGNAL / NEAR SETUP DIAGNOSTICS
# =========================================================

def build_signal(structure, pa, trigger, candles_3m):
    result = {
        "signal": "NO ENTRY",
        "diagnostic": "NO SETUP",
        "reason": "",
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": RISK_REWARD
    }

    direction = pa.get("direction")

    if direction is None:
        result["diagnostic"] = pa.get("retest", "NO BOS")
        result["reason"] = pa.get("reason", "No valid BOS")
        return result

    if pa.get("retest") == "WAITING_RETEST":
        result["diagnostic"] = "WAITING_RETEST"
        result["reason"] = "Waiting for 3m pullback"
        return result

    if pa.get("retest") == "BOS_FAILED":
        result["diagnostic"] = "BOS_FAILED"
        result["reason"] = "3m BOS level invalidated"
        return result

    if pa.get("retest") == "BOS_EXPIRED":
        result["diagnostic"] = "BOS_EXPIRED"
        result["reason"] = "3m BOS setup expired"
        return result

    if pa.get("retest") not in (
        "BULLISH_RETEST",
        "BEARISH_RETEST"
    ):
        result["diagnostic"] = "WAITING_RETEST"
        result["reason"] = "No valid retest"
        return result

    retest_index = pa.get("retest_index")

    if retest_index is None:
        result["diagnostic"] = "WAITING_RETEST"
        result["reason"] = "Retest index unavailable"
        return result

    retest_age = len(candles_3m) - 1 - retest_index

    if retest_age > MAX_RETEST_AGE:
        result["diagnostic"] = "RETEST_TOO_OLD"
        result["reason"] = "3m retest is too old"
        return result

    if structure == "NEUTRAL":
        result["diagnostic"] = "STRUCTURE_NEUTRAL"
        result["reason"] = "15m structure is neutral"
        return result

    if structure != direction:
        result["diagnostic"] = "TIMEFRAME_CONFLICT"
        result["reason"] = "15m and 3m direction conflict"
        return result

    if trigger.get("status") == "WAITING_SWEEP":
        result["diagnostic"] = "WAITING_LIQUIDITY_SWEEP"
        result["reason"] = trigger["reason"]
        return result

    if trigger.get("status") == "SWEEP_WAITING_CONFIRMATION":
        result["diagnostic"] = "SWEEP_WAITING_CONFIRMATION"
        result["reason"] = trigger["reason"]
        return result

    if trigger.get("status") == "TRIGGER_TOO_OLD":
        result["diagnostic"] = "TRIGGER_TOO_OLD"
        result["reason"] = trigger["reason"]
        return result

    if trigger.get("status") == "INSUFFICIENT_DATA":
        result["diagnostic"] = "INSUFFICIENT_1M_DATA"
        result["reason"] = trigger["reason"]
        return result

    if trigger.get("status") != "READY":
        result["diagnostic"] = "WAITING_1M_CONFIRMATION"
        result["reason"] = "No completed 1m confirmation"
        return result

    if trigger.get("trigger") != direction + "_TRIGGER":
        result["diagnostic"] = "TRIGGER_DIRECTION_CONFLICT"
        result["reason"] = "1m trigger direction does not match 3m"
        return result

    retest_time = candles_3m[retest_index][0]

    if trigger.get("sweep_time") is None:
        result["diagnostic"] = "WAITING_LIQUIDITY_SWEEP"
        result["reason"] = "Sweep timestamp unavailable"
        return result

    if trigger["sweep_time"] <= retest_time:
        result["diagnostic"] = "SWEEP_BEFORE_RETEST"
        result["reason"] = "1m sweep happened before 3m retest"
        return result

    entry = trigger.get("entry")
    stop_loss = trigger.get("stop_loss")

    if entry is None or stop_loss is None:
        result["diagnostic"] = "INVALID_PRICE_DATA"
        result["reason"] = "Entry or stop loss unavailable"
        return result

    if direction == "BULLISH":
        risk = entry - stop_loss

        if risk <= 0:
            result["diagnostic"] = "INVALID_RISK"
            result["reason"] = "Invalid long stop loss"
            return result

        take_profit = entry + (risk * RISK_REWARD)
        signal = "LONG"

    else:
        risk = stop_loss - entry

        if risk <= 0:
            result["diagnostic"] = "INVALID_RISK"
            result["reason"] = "Invalid short stop loss"
            return result

        take_profit = entry - (risk * RISK_REWARD)
        signal = "SHORT"

    result.update({
        "signal": signal,
        "diagnostic": "READY",
        "reason": "All conditions confirmed",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit
    })

    return result


# =========================================================
# SYMBOL ANALYSIS
# =========================================================

def analyze_symbol(symbol):
    candles_15m = get_15m_candles(symbol)
    candles_1m = get_1m_candles(symbol)

    if len(candles_15m) < 30:
        return {
            "symbol": symbol,
            "error": "Insufficient 15m candles"
        }

    if len(candles_1m) < 30:
        return {
            "symbol": symbol,
            "error": "Insufficient 1m candles"
        }

    candles_3m = get_3m_candles_from_1m(candles_1m)

    if len(candles_3m) < 20:
        return {
            "symbol": symbol,
            "error": "Insufficient complete 3m candles"
        }

    swings_15m = detect_swings(candles_15m)
    swings_3m = detect_swings(candles_3m)

    structure = detect_market_structure(swings_15m)

    pa = find_latest_bos_and_retest(
        candles_3m,
        swings_3m
    )

    retest_time = None

    if pa.get("retest_index") is not None:
        retest_time = candles_3m[
            pa["retest_index"]
        ][0]

    trigger = detect_1m_trigger(
        candles_1m,
        retest_time
    )

    signal = build_signal(
        structure,
        pa,
        trigger,
        candles_3m
    )

    return {
        "symbol": symbol,
        "structure": structure,
        "bos": pa["bos"],
        "retest": pa["retest"],
        "trigger": trigger["trigger"],
        "trigger_status": trigger["status"],
        "signal": signal["signal"],
        "diagnostic": signal["diagnostic"],
        "reason": signal["reason"],
        "entry": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "take_profit": signal["take_profit"]
    }


# =========================================================
# MAIN
# =========================================================

def main():
    print()
    print("=" * 60)
    print("NIMA PRICE ACTION SCALPER")
    print("=" * 60)

    print("Exchange: MEXC Futures")
    print("Universe: Top 30 USDT perpetuals")
    print("Price filter: <= $50")
    print("Timeframes: 15m / 3m / 1m")
    print("Method: Pure Price Action")
    print("Risk Reward: 1:2")
    print("=" * 60)

    contracts = get_top_contracts()

    if not contracts:
        print("No eligible contracts found.")
        return

    counts = defaultdict(int)
    results = []

    print()
    print(f"Scanning {len(contracts)} contracts...")
    print()

    for number, contract in enumerate(contracts, 1):
        symbol = contract["symbol"]

        print(
            f"[{number}/{len(contracts)}] Analyzing {symbol}..."
        )

        try:
            result = analyze_symbol(symbol)

            results.append(result)

            if "error" in result:
                counts["DATA_ERROR"] += 1

                print(f"  DATA ERROR: {result['error']}")
                print()
                continue

            counts[result["diagnostic"]] += 1

            print(f"  15m Structure: {result['structure']}")
            print(f"  3m BOS:        {result['bos']}")
            print(f"  3m Retest:     {result['retest']}")
            print(f"  1m Trigger:    {result['trigger']}")
            print(f"  Diagnostic:    {result['diagnostic']}")
            print(f"  Signal:        {result['signal']}")
            print(f"  Reason:        {result['reason']}")

            if result["signal"] in ("LONG", "SHORT"):
                print(f"  Entry:         {result['entry']}")
                print(f"  Stop Loss:     {result['stop_loss']}")
                print(f"  Take Profit:   {result['take_profit']}")
                print("  Risk/Reward:   1:2")

            print()

        except Exception as error:
            counts["ERROR"] += 1
            print(f"  ERROR: {error}")
            print()

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    print()
    print("=" * 60)
    print("SCAN SUMMARY")
    print("=" * 60)

    print(f"Total scanned: {len(contracts)}")

    long_count = sum(
        1 for item in results
        if item.get("signal") == "LONG"
    )

    short_count = sum(
        1 for item in results
        if item.get("signal") == "SHORT"
    )

    no_entry_count = sum(
        1 for item in results
        if item.get("signal") == "NO ENTRY"
    )

    print(f"LONG signals:  {long_count}")
    print(f"SHORT signals: {short_count}")
    print(f"NO ENTRY:      {no_entry_count}")

    print()
    print("DIAGNOSTIC COUNTS:")

    for name, count in sorted(counts.items()):
        print(f"{name}: {count}")

    print()
    print("=" * 60)

    ready_setups = [
        item for item in results
        if item.get("diagnostic") == "READY"
    ]

    if ready_setups:
        print("CONFIRMED SIGNALS:")

        for item in ready_setups:
            print(
                f"{item['symbol']} | {item['signal']} | "
                f"Entry: {item['entry']} | "
                f"SL: {item['stop_loss']} | "
                f"TP: {item['take_profit']}"
            )
    else:
        print("No confirmed trading signals.")

    print("=" * 60)


if __name__ == "__main__":
    main()
