
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
