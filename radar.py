
import os
import requests
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

def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram secrets missing.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    response.raise_for_status()
    print("Telegram message sent.")

def main():
    top = get_top_contracts()

    if not top:
        raise RuntimeError("No active USDT contracts found.")

    now = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    lines = [
        "NIMA TRADE RADAR",
        "Toobit Futures - Top 20",
        f"Scan time: {now}",
        "Ranking: 24h quote volume",
        "----------------------"
    ]

    for index, item in enumerate(top, start=1):
        line = (
            f"{index}. {item['symbol']}\n"
            f"Price: {item['price']}\n"
            f"24h Change: {item['change']:.2f}%\n"
            f"24h Volume: {item['volume']:,.0f} USDT"
        )

        lines.append(line)

        print(line)
        print("----------------------")

    message = "\n".join(lines)

    send_telegram(message)

    print("TOP 20 SCAN: SUCCESS")

if __name__ == "__main__":
    main()
