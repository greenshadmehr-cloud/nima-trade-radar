import requests
import time
def detect_swings(candles, left=2, right=2):
    """
    candles: [timestamp, open, high, low, close, volume]
    فقط پیوت‌هایی ثبت می‌شوند که کندل‌های سمت راستشان بسته شده باشند.
    """
    swings = []

    for i in range(left, len(candles) - right):
        high = candles[i][2]
        low = candles[i][3]

        left_highs = [candles[j][2] for j in range(i-left, i)]
        right_highs = [candles[j][2] for j in range(i+1, i+right+1)]

        left_lows = [candles[j][3] for j in range(i-left, i)]
        right_lows = [candles[j][3] for j in range(i+1, i+right+1)]

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


def detect_market_structure(swings):
    highs = [s["price"] for s in swings if s["type"] == "HIGH"]
    lows = [s["price"] for s in swings if s["type"] == "LOW"]

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
    

BASE_URL = "https://contract.mexc.com"
TOP_N = 30


def get_json(path, params=None):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    data = response.json()

    if data.get("success") is False:
        raise RuntimeError(data)

    return data


def get_top_contracts():
    info = get_json("/api/v1/contract/detail")["data"]
    tickers = get_json("/api/v1/contract/ticker")["data"]

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

    for i, ts in enumerate(data["time"]):
        ts = int(ts)

        # فقط کندل‌های کاملاً بسته‌شده
        if ts + seconds > now:
            continue

        candles.append([
            ts,
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(data["vol"][i])
        ])

    return candles



def main():
    print("NIMA PRICE ACTION - STRUCTURE TEST")
    print("MEXC FUTURES | 15m + 3m BOS")
    print("--------------------------------")

    symbols = get_top_contracts()

    for symbol in symbols:
        try:
            candles_15m = get_15m_candles(symbol)
            swings_15m = detect_swings(
                candles_15m, left=3, right=3
            )
            structure = detect_market_structure(swings_15m)

            candles_3m = get_3m_candles(symbol)
            swings_3m = detect_swings(
                candles_3m, left=2, right=2
            )
            bos = detect_bos(candles_3m, swings_3m)

            print(
                f"{symbol} | 15m: {structure} | 3m BOS: {bos}"
            )

        except Exception as error:
            print(f"{symbol} | ERROR: {error}")

        time.sleep(0.15)


if __name__ == "__main__":
    main()
def detect_bos(candles, swings):
    if len(candles) < 2:
        return "NONE"

    if not swings:
        return "NONE"

    last_candle = candles[-1]
    previous_candle = candles[-2]

    close_price = last_candle[4]

    highs = [s for s in swings if s["type"] == "HIGH"]
    lows = [s for s in swings if s["type"] == "LOW"]

    if not highs or not lows:
        return "NONE"

    last_high = highs[-1]["price"]
    last_low = lows[-1]["price"]

    # شکست سقف با بسته شدن کندل
    if close_price > last_high and previous_candle[4] <= last_high:
        return "BULLISH_BOS"

    # شکست کف با بسته شدن کندل
    if close_price < last_low and previous_candle[4] >= last_low:
        return "BEARISH_BOS"

    return "NONE"
def get_3m_candles(symbol, limit=150):
    now = int(time.time())
    seconds = 180

    result = get_json(
        f"/api/v1/contract/kline/{symbol}",
        {
            "interval": "Min1",
            "start": now - (limit * 3 + 5) * 60,
            "end": now
        }
    )

    data = result["data"]
    candles_1m = []

    for i, ts in enumerate(data["time"]):
        ts = int(ts)

        if ts + 60 > now:
            continue

        candles_1m.append([
            ts,
            float(data["open"][i]),
            float(data["high"][i]),
            float(data["low"][i]),
            float(data["close"][i]),
            float(data["vol"][i])
        ])

    candles_3m = []

    for i in range(0, len(candles_1m) - 2, 3):
        group = candles_1m[i:i + 3]

        if len(group) != 3:
            continue

        if group[0][0] % seconds != 0:
            continue

        if group[1][0] != group[0][0] + 60:
            continue

        if group[2][0] != group[0][0] + 120:
            continue

        candles_3m.append([
            group[0][0],
            group[0][1],
            max(x[2] for x in group),
            min(x[3] for x in group),
            group[2][4],
            sum(x[5] for x in group)
        ])

    return candles_3m
