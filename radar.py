
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

    print("TOP 20 CANDLE TEST")
    print("====================")

    successful = 0
    failed = 0

    for item in top:
        symbol = item["symbol"]

        try:
            candles_15m = get_klines(symbol, "15m")
            candles_1h = get_klines(symbol, "1h")

            if len(candles_15m) < 30 or len(candles_1h) < 30:
                raise RuntimeError("Not enough candles")

            latest = candles_15m[-1]

            print(f"\n{symbol}")
            print("15m candles:", len(candles_15m))
            print("1h candles:", len(candles_1h))
            print("Last closed 15m candle:")
            print("Open:", latest[1])
            print("High:", latest[2])
            print("Low:", latest[3])
            print("Close:", latest[4])
            print("Volume:", latest[5])

            successful += 1

        except Exception as error:
            print(f"\nERROR {symbol}: {error}")
            failed += 1

        time.sleep(0.15)

    print("\n====================")
    print("CANDLE TEST FINISHED")
    print("Successful:", successful)
    print("Failed:", failed)

if __name__ == "__main__":
    main()

