
import os
import requests
import time
from datetime import datetime, timezone

BASE_URL = "https://api.toobit.com"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def get_json(path):
    response = requests.get(
        BASE_URL + path,
        timeout=20
    )
    response.raise_for_status()
    return response.json()

def get_top_contracts():
    print("Getting exchange information...")

    info = get_json("/api/v1/exchangeInfo")
    contracts = info.get("contracts", [])

    active_symbols = {
        item["symbol"]
        for item in contracts
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and item.get("inverse") is False
        and item.get("symbol", "").endswith("-SWAP-USDT")
    }

    print("Active USDT contracts:", len(active_symbols))

    print("Getting 24h futures tickers...")

    tickers = get_json("/quote/v1/contract/ticker/24hr")

    ranked = []

    for item in tickers:
        symbol = item.get("s")

        if symbol not in active_symbols:
            continue

        ranked.append({
            "symbol": symbol,
            "price": float(item["c"]),
            "change": float(item["pcp"]) * 100,
            "volume": float(item["qv"])
        })

    ranked.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return ranked[:20]


def get_klines(symbol, interval, limit=50):
    interval_ms = {
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000
    }[interval]

    now = int(time.time() * 1000)

    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": now - (limit + 2) * interval_ms,
        "endTime": now,
        "limit": limit + 2
    }

    response = requests.get(
        BASE_URL + "/quote/v1/klines",
        params=params,
        timeout=20
    )
    response.raise_for_status()

    candles = response.json()

    if not isinstance(candles, list):
        raise RuntimeError(f"Unexpected candle response: {symbol}")

    # فقط کندل‌های بسته‌شده
    candles = [
        candle for candle in candles
        if isinstance(candle, list)
        and len(candle) >= 6
        and int(candle[0]) + interval_ms <= now
    ]

    return candles[-limit:]
    
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


def analyze_symbol(symbol):
    c15 = get_klines(symbol, "15m", 60)
    c1h = get_klines(symbol, "1h", 60)

    if len(c15) < 50 or len(c1h) < 50:
        return None

    closes15 = [float(c[4]) for c in c15]
    closes1h = [float(c[4]) for c in c1h]

    volumes = [float(c[5]) for c in c15]

    price = closes15[-1]
    current_volume = volumes[-1]
    avg_volume = sum(volumes[-21:-1]) / 20

    if avg_volume <= 0:
        return None

    volume_ratio = current_volume / avg_volume

    ema20_15 = ema(closes15, 20)
    ema50_15 = ema(closes15, 50)
    ema20_1h = ema(closes1h, 20)
    ema50_1h = ema(closes1h, 50)

    current_rsi = rsi(closes15)
    current_atr = atr(c15)

    if None in (
        ema20_15, ema50_15,
        ema20_1h, ema50_1h,
        current_rsi, current_atr
    ):
        return None

    if current_atr <= 0:
        return None

    # Previous 20 candles, excluding current candle
    previous = c15[-21:-1]

    resistance = max(float(c[2]) for c in previous)
    support = min(float(c[3]) for c in previous)

    long_score = 0
    short_score = 0

    # 1. Trend: maximum 20
    if ema20_15 > ema50_15 and ema20_1h > ema50_1h:
        long_score += 20

    if ema20_15 < ema50_15 and ema20_1h < ema50_1h:
        short_score += 20

    # 2. Support / resistance: maximum 20
    if abs(price - support) <= current_atr * 0.6:
        long_score += 20

    if abs(price - resistance) <= current_atr * 0.6:
        short_score += 20

    # 3. Breakout: maximum 25
    if price > resistance:
        long_score += 25

    if price < support:
        short_score += 25

    # 4. Volume: maximum 15
    if volume_ratio >= 1.5:
        volume_score = 15
    elif volume_ratio >= 1.2:
        volume_score = 10
    elif volume_ratio >= 1.0:
        volume_score = 5
    else:
        volume_score = 0

    long_score += volume_score
    short_score += volume_score

    # 5. Momentum: maximum 10
    if 50 <= current_rsi <= 68:
        long_score += 10
    elif 45 <= current_rsi < 50:
        long_score += 5

    if 32 <= current_rsi <= 50:
        short_score += 10
    elif 50 < current_rsi <= 55:
        short_score += 5


    # 6. Estimated risk / reward: maximum 10

    long_stop = price - (1.5 * current_atr)
    short_stop = price + (1.5 * current_atr)
    recent_range = resistance - support

    long_target = price + recent_range
    short_target = price - recent_range

    long_rr = (
        (long_target - price) / (price - long_stop)
        if price > long_stop else 0
    )

    short_rr = (
        (price - short_target) / (short_stop - price)
        if short_stop > price else 0
    )

    if long_rr >= 2:
        long_score += 10
    elif long_rr >= 1.5:
        long_score += 5

    if short_rr >= 2:
        short_score += 10
    elif short_rr >= 1.5:
        short_score += 5


    # Reject conflicting or insufficient setups
    if long_score >= 80 and long_score > short_score:
        signal = "LONG REVIEW"
        score = long_score
    elif short_score >= 80 and short_score > long_score:
        signal = "SHORT REVIEW"
        score = short_score
    elif max(long_score, short_score) >= 70:
        signal = "WATCH"
        score = max(long_score, short_score)
    else:
        signal = "NO ENTRY"
        score = max(long_score, short_score)

    return {
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
        "short_rr": short_rr
    }


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

    print("NIMA TRADE RADAR - ANALYSIS")
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
        "NIMA TRADE RADAR",
        "Toobit Futures | 15m + 1h",
        "--------------------------"
    ]

    for result in results:
        if result["signal"] == "NO ENTRY":
            continue

        lines.extend([
            f"{result['symbol']} - {result['signal']}",
            f"Score: {result['score']}/100",
            f"Price: {result['price']}",
            f"RSI: {result['rsi']:.1f}",
            f"Volume: {result['volume_ratio']:.2f}x",
            f"Support: {result['support']}",
            f"Resistance: {result['resistance']}",
            f"Estimated Long RR: {result['long_rr']:.2f}",
            f"Estimated Short RR: {result['short_rr']:.2f}",
            "--------------------------"
        ])

    if len(lines) == 3:
        lines.append("No qualifying setup. NO ENTRY.")

    lines.append(f"Analyzed: {len(results)}/20")
    lines.append(f"Data errors: {len(errors)}")

    message = "\n".join(lines)

    print("\n" + message)

    send_telegram(message)

    print("\nANALYSIS FINISHED")


if __name__ == "__main__":
    main()

