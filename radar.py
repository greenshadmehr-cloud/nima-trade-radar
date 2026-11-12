
import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE = "https://fapi.binance.com"

def send_message(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram secrets are not configured.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=15
    )
    response.raise_for_status()

def check_binance():
    response = requests.get(
        f"{BINANCE}/fapi/v1/ping",
        timeout=15
    )
    response.raise_for_status()
    print("Binance Futures connection: OK")

if __name__ == "__main__":
    check_binance()
    send_message("NIMA TRADE RADAR: اتصال اولیه موفق شد.")
