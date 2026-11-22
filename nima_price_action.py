
import requests
import time

BASE_URL = "https://contract.mexc.com"
TOP_N = 30
RISK_REWARD = 2.0
MAX_RETEST_AGE = 3


# -----------------------------
# دریافت اطلاعات از MEXC
# -----------------------------
def get_json(path, params=None):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    result = response.json()

    if result.get("success") is False:
        raise RuntimeError(result)

    return result


# -----------------------------
# انتخاب ۳۰ قرارداد برتر
# -----------------------------
def get_top_contracts():
    info = get_json("/api/v1/contract/detail")["data"]
    tickers = get_json("/api/v1/contract/ticker")["data"]

    if isinstance(info, dict):
        info = [info]

    if isinstance(tickers, dict):
        tickers = [tickers]

    ticker_map = {
        item["symbol"]: item for item in tickers
    }

    ranked = []

    for item in info:
        symbol = item.get("symbol")
        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        price = float(ticker.get("lastPrice", 0))
        volume = float(ticker.get("amount24", 0))

        if (
            item.get("quoteCoin") == "USDT"
            and item.get("settleCoin") == "USDT"
            and item.get("futureType") == 1
            and item.get("state") == 0
            and 0 < price <= 50
            and volume > 0
        ):
            ranked.append((symbol, volume))

    ranked.sort(key=lambda x: x[1], reverse=True)

    return [item[0] for item in ranked[:TOP_N]]


# -----------------------------
# دریافت کندل‌های بسته‌شده
# -----------------------------
def get_candles(symbol, interval, limit, seconds):
    now = int(time.time())

    result = get_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": interval,
            "start": now - (limit + 5) * seconds,
            "end": now
        }
    )

    data = result["data"]
    volumes = data.get("vol", [0] * len(data["time"]))

    candles = []

    for i, timestamp in enumerate(data["time"]):
        timestamp = int(timestamp)

        if timestamp + seconds > now:
            continue

        candles.append([
            timestamp,
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(volumes[i])
        ])

    return candles[-limit:]


def get_15m_candles(symbol, limit=120):
    return get_candles(symbol, "Min15", limit, 900)


def get_1m_candles(symbol, limit=120):
    return get_candles(symbol, "Min1", limit, 60)


# -----------------------------
# ساخت کندل‌های ۳ دقیقه‌ای
# -----------------------------
def get_3m_candles(symbol, limit=150):
    one_minute = get_1m_candles(
        symbol,
        limit * 3 + 5
    )

    buckets = {}

    for candle in one_minute:
        timestamp = candle[0]
        bucket = (timestamp // 180) * 180

        if bucket not in buckets:
            buckets[bucket] = {}

        buckets[bucket][timestamp] = candle

    candles_3m = []

    for bucket in sorted(buckets):
        group = buckets[bucket]

        if not all(
            bucket + offset in group
            for offset in (0, 60, 120)
        ):
            continue

        first = group[bucket]
        second = group[bucket + 60]
        third = group[bucket + 120]

        candles_3m.append([
            bucket,
            first[1],
            max(first[2], second[2], third[2]),
            min(first[3], second[3], third[3]),
            third[4],
            first[5] + second[5] + third[5]
        ])

    return candles_3m[-limit:]


# -----------------------------
# تشخیص سقف و کف معتبر
# -----------------------------
def detect_swings(candles, left=2, right=2):
    swings = []

    for i in range(left, len(candles) - right):
        high = candles[i][2]
        low = candles[i][3]

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

        if all(high > value for value in left_highs + right_highs):
            swings.append({
                "index": i,
                "type": "HIGH",
                "price": high
            })

        if all(low < value for value in left_lows + right_lows):
            swings.append({
                "index": i,
                "type": "LOW",
                "price": low
            })

    return sorted(swings, key=lambda item: item["index"])


# -----------------------------
# تشخیص ساختار بازار
# -----------------------------
def detect_market_structure(swings):
    highs = [
        item["price"] for item in swings
        if item["type"] == "HIGH"
    ]

    lows = [
        item["price"] for item in swings
        if item["type"] == "LOW"
    ]

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    higher_high = highs[-1] > highs[-2]
    higher_low = lows[-1] > lows[-2]

    lower_high = highs[-1] < highs[-2]
    lower_low = lows[-1] < lows[-2]

    if higher_high and higher_low:
        return "BULLISH"

    if lower_high and lower_low:
        return "BEARISH"

    return "NEUTRAL"


# -----------------------------
# تشخیص آخرین BOS و Retest
# -----------------------------
def find_latest_bos_and_retest(
    candles,
    swings,
    right=2,
    tolerance=0.0015,
    max_bars=10
):
    result = {
        "bos": "NONE",
        "retest": "NO_RETEST",
        "level": None,
        "bos_index": None,
        "retest_index": None
    }

    if len(candles) < 5 or not swings:
        return result

    latest_bos = None

    for i in range(1, len(candles)):
        previous_close = candles[i - 1][4]
        current_close = candles[i][4]

        available_highs = [
            swing for swing in swings
            if swing["type"] == "HIGH"
            and swing["index"] < i
            and swing["index"] + right <= i
        ]

        available_lows = [
            swing for swing in swings
            if swing["type"] == "LOW"
            and swing["index"] < i
            and swing["index"] + right <= i
        ]

        if available_highs:
            last_high = max(
                available_highs,
                key=lambda item: item["index"]
            )

            level = last_high["price"]

            if previous_close <= level and current_close > level:
                latest_bos = {
                    "direction": "BULLISH",
                    "level": level,
                    "index": i
                }

        if available_lows:
            last_low = max(
                available_lows,
                key=lambda item: item["index"]
            )

            level = last_low["price"]

            if previous_close >= level and current_close < level:
                latest_bos = {
                    "direction": "BEARISH",
                    "level": level,
                    "index": i
                }

    if latest_bos is None:
        return result

    direction = latest_bos["direction"]
    level = latest_bos["level"]
    bos_index = latest_bos["index"]

    result["bos"] = direction + "_BOS"
    result["level"] = level
    result["bos_index"] = bos_index

    if len(candles) - 1 - bos_index > max_bars:
        result["retest"] = "BOS_EXPIRED"
        return result

    for i in range(bos_index + 1, len(candles)):
        candle = candles[i]

        high = candle[2]
        low = candle[3]
        close = candle[4]

        if direction == "BULLISH":
            if close < level:
                result["retest"] = "BOS_FAILED"
                return result

            if low <= level * (1 + tolerance) and close > level:
                result["retest"] = "BULLISH_RETEST"
                result["retest_index"] = i
                return result

        else:
            if close > level:
                result["retest"] = "BOS_FAILED"
                return result

            if high >= level * (1 - tolerance) and close < level:
                result["retest"] = "BEARISH_RETEST"
                result["retest_index"] = i
                return result

    result["retest"] = "WAITING_RETEST"
    return result


# -----------------------------
# تأیید ورود یک دقیقه‌ای
# -----------------------------
def detect_1m_trigger(candles):
    result = {
        "trigger": "NONE",
        "stop_loss": None,
        "entry": None
    }

    if len(candles) < 8:
        return result

    sweep = candles[-2]
    confirm = candles[-1]

    previous_candles = candles[-7:-2]

    previous_lows = [
        candle[3] for candle in previous_candles
    ]

    previous_highs = [
        candle[2] for candle in previous_candles
    ]

    liquidity_low = min(previous_lows)
    liquidity_high = max(previous_highs)

    # جمع‌آوری نقدینگی زیر کف و تأیید صعودی
    bullish_sweep = (
        sweep[3] < liquidity_low
        and sweep[4] > liquidity_low
    )

    bullish_confirmation = (
        confirm[4] > confirm[1]
        and confirm[4] > sweep[2]
    )

    if bullish_sweep and bullish_confirmation:
        result["trigger"] = "BULLISH_TRIGGER"
        result["stop_loss"] = sweep[3]
        result["entry"] = confirm[4]
        return result

    # جمع‌آوری نقدینگی بالای سقف و تأیید نزولی
    bearish_sweep = (
        sweep[2] > liquidity_high
        and sweep[4] < liquidity_high
    )

    bearish_confirmation = (
        confirm[4] < confirm[1]
        and confirm[4] < sweep[3]
    )

    if bearish_sweep and bearish_confirmation:
        result["trigger"] = "BEARISH_TRIGGER"
        result["stop_loss"] = sweep[2]
        result["entry"] = confirm[4]
        return result

    return result


# -----------------------------
# ساخت سیگنال نهایی
# -----------------------------
def build_signal(structure, pa, trigger, candles_3m):
    result = {
        "signal": "NO ENTRY",
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "reason": "Conditions not complete"
    }

    if pa["retest_index"] is None:
        result["reason"] = "No valid retest"
        return result

    # پولبک باید جدید باشد
    retest_age = len(candles_3m) - 1 - pa["retest_index"]

    if retest_age > MAX_RETEST_AGE:
        result["reason"] = "Retest is too old"
        return result

    retest_time = candles_3m[pa["retest_index"]][0]

    if structure == "BULLISH":
        if pa["bos"] != "BULLISH_BOS":
            result["reason"] = "15m and 3m direction conflict"
            return result

        if pa["retest"] != "BULLISH_RETEST":
            result["reason"] = "No bullish retest"
            return result

        if trigger["trigger"] != "BULLISH_TRIGGER":
            result["reason"] = "No bullish 1m confirmation"
            return result

    elif structure == "BEARISH":
        if pa["bos"] != "BEARISH_BOS":
            result["reason"] = "15m and 3m direction conflict"
            return result

        if pa["retest"] != "BEARISH_RETEST":
            result["reason"] = "No bearish retest"
            return result

        if trigger["trigger"] != "BEARISH_TRIGGER":
            result["reason"] = "No bearish 1m confirmation"
            return result

    else:
        result["reason"] = "15m structure is neutral"
        return result

    # تأیید یک‌دقیقه‌ای باید بعد از کندل Retest باشد
    # کندل تأیید یک‌دقیقه‌ای، آخرین کندل بسته‌شده است
    # زمان آن در تابع اصلی بررسی می‌شود

    entry = trigger["entry"]
    stop = trigger["stop_loss"]

    if structure == "BULLISH":
        risk = entry - stop

        if risk <= 0:
            result["reason"] = "Invalid long stop loss"
            return result

        target = entry + (risk * RISK_REWARD)
        result["signal"] = "LONG"

    else:
        risk = stop - entry

        if risk <= 0:
            result["reason"] = "Invalid short stop loss"
            return result

        target = entry - (risk * RISK_REWARD)
        result["signal"] = "SHORT"

    result["entry"] = entry
    result["stop_loss"] = stop
    result["take_profit"] = target
    result["reason"] = "All conditions confirmed"

    return result


# -----------------------------
# اسکن بازار
# -----------------------------
def main():
    print("NIMA PRICE ACTION SCALPER")
    print("-------------------------")

    symbols = get_top_contracts()

    print(f"Scanning {len(symbols)} contracts...\n")

    for symbol in symbols:
        try:
            candles_15m = get_15m_candles(symbol)
            candles_3m = get_3m_candles(symbol)
            candles_1m = get_1m_candles(symbol)

            swings_15m = detect_swings(
                candles_15m,
                left=3,
                right=3
            )

            swings_3m = detect_swings(
                candles_3m,
                left=2,
                right=2
            )

            structure = detect_market_structure(swings_15m)

            pa = find_latest_bos_and_retest(
                candles_3m,
                swings_3m,
                right=2
            )

            trigger = detect_1m_trigger(candles_1m)

            signal = build_signal(
                structure,
                pa,
                trigger,
                candles_3m
            )

            # اطمینان از اینکه تأیید یک‌دقیقه‌ای بعد از Retest است
            if (
                signal["signal"] != "NO ENTRY"
                and pa["retest_index"] is not None
            ):
                retest_time = candles_3m[pa["retest_index"]][0]
                trigger_time = candles_1m[-1][0]

                if trigger_time <= retest_time:
                    signal = {
                        "signal": "NO ENTRY",
                        "entry": None,
                        "stop_loss": None,
                        "take_profit": None,
                        "reason": "1m trigger is not after retest"
                    }

            print(f"\n{symbol}")
            print(f"15m Structure: {structure}")
            print(f"3m BOS: {pa['bos']}")
            print(f"3m Retest: {pa['retest']}")
            print(f"1m Trigger: {trigger['trigger']}")
            print(f"Signal: {signal['signal']}")

            if signal["signal"] != "NO ENTRY":
                print(f"Entry: {signal['entry']}")
                print(f"Stop Loss: {signal['stop_loss']}")
                print(f"Take Profit: {signal['take_profit']}")
                print(f"Risk/Reward: 1:{RISK_REWARD}")

            print(f"Reason: {signal['reason']}")

        except Exception as error:
            print(f"{symbol} | ERROR: {error}")

        time.sleep(0.15)


if __name__ == "__main__":
    main()
