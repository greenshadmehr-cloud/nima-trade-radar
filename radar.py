import os
import requests
import time

BASE_URL = "https://api.toobit.com"

def test_api():
    endpoints = [
        ("/api/v1/time", {}),
        ("/quote/v1/contract/ticker/24hr", {
            "symbol": "BTC-SWAP-USDT"
        }),
        ("/quote/v1/klines", {
            "symbol": "BTC-SWAP-USDT",
            "interval": "15m",
            "startTime": int(time.time() * 1000) - 6 * 60 * 60 * 1000,
            "endTime": int(time.time() * 1000),
            "limit": 30
        })
    ]

    for path, params in endpoints:
        url = BASE_URL + path
        print("\nTesting:", url)

        response = requests.get(
            url,
            params=params,
            timeout=20
        )

        print("HTTP Status:", response.status_code)
        print("Response:", response.text[:1000])

        response.raise_for_status()

    print("\nTOOBIT API TEST: SUCCESS")

if __name__ == "__main__":
    test_api()
