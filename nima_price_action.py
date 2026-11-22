
import requests
import time

BASE_URL = "https://contract.mexc.com"
TOP_N = 30


# دریافت اطلاعات از MEXC
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


# انتخاب ۳۰ قرارداد برتر
def get_top_contracts():
    info = get_json("/api/v1/contract/detail")["data"]
    tickers = get_json("/api/v1/contract/ticker")["data"]

    if isinstance(tickers, dict):
        tickers = [tickers]

    if isinstance(info, dict):
        info = [info]

    ticker_map = {
        x["symbol"]: x for x in tickers
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

    return [x[0] for x in ranked[:TOP_N]]


# دریافت کندل‌های ۱۵ دقیقه
def get_15m_candles(symbol, limit=120):
    now = int(time.time())
    seconds = 900

    result = get_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": "Min15",
            "start": now - (limit + 5) * seconds,
            "end": now
        }
    )

    data = result["data"]
    candles = []

    volumes = data.get("vol", [0] * len(data["time"]))

    for i, ts in enumerate(data["time"]):
        ts = int(ts)

        if ts + seconds > now:
            continue

        candles.append([
            ts,
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(volumes[i])
        ])

    return candles


# تشخیص سقف و کف معتبر
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

        if all(high > x for x in left_highs + right_highs):
            swings.append({
                "index": i,
                "time": candles[i][0],
                "type": "HIGH",
                "price": high
            })

        if all(low < x for x in left_lows + right_lows):
            swings.append({
                "index": i,
                "time": candles[i][0],
                "type": "LOW",
                "price": low
            })

    return sorted(swings, key=lambda x: x["index"])


# تشخیص ساختار بازار
def detect_market_structure(swings):
    highs = [
        s["price"] for s in swings
        if s["type"] == "HIGH"
    ]

    lows = [
        s["price"] for s in swings
        if s["type"] == "LOW"
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


# دریافت کندل‌های ۳ دقیقه از داده‌های ۱ دقیقه
def get_3m_candles(symbol, limit=150):
    now = int(time.time())

    result = get_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": "Min1",
            "start": now - (limit * 3 + 5) * 60,
            "end": now
        }
    )

    data = result["data"]
    volumes = data.get("vol", [0] * len(data["time"]))

    buckets = {}

    for i, ts in enumerate(data["time"]):
        ts = int(ts)

        # حذف کندل یک دقیقه‌ای باز
        if ts + 60 > now:
            continue

        bucket = (ts // 180) * 180

        if bucket + 180 > now:
            continue

        if bucket not in buckets:
            buckets[bucket] = {}

        buckets[bucket][ts] = [
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(volumes[i])
        ]

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
            first[0],
            max(first[1], second[1], third[1]),
            min(first[2], second[2], third[2]),
            third[3],
            first[4] + second[4] + third[4]
        ])

    return candles_3m[-limit:]


# تشخیص شکست ساختار BOS
def detect_bos(candles, swings):
    if len(candles) < 2 or not swings:
        return "NONE"

    last_close = candles[-1][4]
    previous_close = candles[-2][4]

    highs = [
        s for s in swings if s["type"] == "HIGH"
    ]

    lows = [
        s for s in swings if s["type"] == "LOW"
    ]

    if highs:
        last_high = highs[-1]["price"]

        if (
            last_close > last_high
            and previous_close <= last_high
        ):
            return "BULLISH_BOS"

    if lows:
        last_low = lows[-1]["price"]

        if (
            last_close < last_low
            and previous_close >= last_low
        ):
            return "BEARISH_BOS"

    return "NONE"


def detect_retest(candles, swings, bos):
    if len(candles) < 3 or not swings:
        return "NO_RETEST"

    last_candle = candles[-1]
    previous_candle = candles[-2]

    if bos == "BULLISH_BOS":
        highs = [
            s for s in swings
            if s["type"] == "HIGH"
        ]

        if not highs:
            return "NO_RETEST"

        level = highs[-1]["price"]

        touched = last_candle[3] <= level * 1.002
        held = last_candle[4] > level

        if touched and held:
            return "BULLISH_RETEST"

    elif bos == "BEARISH_BOS":
        lows = [
            s for s in swings
            if s["type"] == "LOW"
        ]

        if not lows:
            return "NO_RETEST"

        level = lows[-1]["price"]

        touched = last_candle[2] >= level * 0.998
        held = last_candle[4] < level

        if touched and held:
            return "BEARISH_RETEST"

    return "NO_RETEST"

# اسکن بازار
def main():
    print("NIMA PRICE ACTION - STRUCTURE TEST")
    print("MEXC FUTURES | 15m + 3m BOS")
    print("--------------------------------")

    symbols = get_top_contracts()

    for symbol in symbols:
        try:
            candles_15m = get_15m_candles(symbol)

            swings_15m = detect_swings(
                candles_15m,
                left=3,
                right=3
            )

            structure = detect_market_structure(
                swings_15m
            )

            candles_3m = get_3m_candles(symbol)

            swings_3m = detect_swings(
                candles_3m,
                left=2,
                right=2
            )

            bos = detect_bos(
                candles_3m,
                swings_3m
            )

            retest = detect_retest(candles_3m, swings_3m, bos)

            print(f"{symbol} | 15m: {structure} | 3m BOS: {bos} | Retest: {retest}")  
except Exception as error:
            print(f"{symbol} | ERROR: {error}")

             time.sleep(0.15)


if __name__ == "__main__":
    main()
