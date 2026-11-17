
import time
import requests
from bisect import bisect_right

# =====================================================
# NIMA ICHIMOKU SCALPER V2
# MEXC USDT Perpetual Futures
# BACKTEST ONLY - NO LIVE ORDERS
# =====================================================

BASE_URL = "https://contract.mexc.com"
TIMEOUT = 25

SYMBOLS = [
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
]

DAYS = 30

TENKAN = 9
KIJUN = 26
SENKOU_B = 52

BREAKOUT_LOOKBACK = 10
PULLBACK_LOOKBACK = 5

START_BALANCE = 1000.0
RISK_PER_TRADE = 0.005
MIN_RR = 1.5

FEE_RATE = 0.0005
SLIPPAGE = 0.0002

SESSION = requests.Session()


# =====================================================
# API
# =====================================================

def get_json(path, params=None):
    url = BASE_URL + path

    response = SESSION.get(
        url,
        params=params,
        timeout=TIMEOUT
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Invalid API response")

    if data.get("success") is False:
        raise ValueError(str(data))

    return data


def fetch_klines(symbol, interval, start_ts, end_ts):
    """
    Returns candles:
    [timestamp_ms, open, high, low, close, volume]
    """

    interval_seconds = {
        "Min1": 60,
        "Min15": 900,
    }

    if interval not in interval_seconds:
        raise ValueError("Unsupported interval")

    step = interval_seconds[interval]
    chunk_seconds = step * 400

    candles = {}
    cursor = start_ts

    while cursor < end_ts:
        chunk_end = min(
            cursor + chunk_seconds,
            end_ts
        )

        data = get_json(
            f"/api/v1/contract/kline/{symbol}",
            {
                "interval": interval,
                "start": cursor,
                "end": chunk_end,
            }
        )

        raw = data.get("data")

        if not isinstance(raw, dict):
            cursor = chunk_end
            time.sleep(0.15)
            continue

        keys = [
            "time", "open", "high",
            "low", "close", "vol"
        ]

        if not all(k in raw for k in keys):
            raise ValueError(
                f"Unexpected candle format: {symbol}"
            )

        count = len(raw["time"])

        for i in range(count):
            ts = int(raw["time"][i])

            # Exclude candles that have not closed.
            if ts + step > end_ts:
                continue

            candles[ts * 1000] = [
                ts * 1000,
                float(raw["open"][i]),
                float(raw["high"][i]),
                float(raw["low"][i]),
                float(raw["close"][i]),
                float(raw["vol"][i]),
            ]

        cursor = chunk_end
        time.sleep(0.15)

    return [
        candles[t]
        for t in sorted(candles)
    ]


# =====================================================
# TIMEFRAME AGGREGATION
# =====================================================

def aggregate(candles, source_minutes, target_minutes):
    if target_minutes % source_minutes != 0:
        raise ValueError("Invalid timeframe")

    required = target_minutes // source_minutes
    groups = {}

    for candle in candles:
        ts = candle[0] // 1000

        bucket = (
            ts // (target_minutes * 60)
        ) * (target_minutes * 60)

        groups.setdefault(bucket, []).append(candle)

    result = []

    for bucket in sorted(groups):
        group = sorted(
            groups[bucket],
            key=lambda x: x[0]
        )

        if len(group) != required:
            continue

        expected = [
            bucket + i * source_minutes * 60
            for i in range(required)
        ]

        actual = [
            c[0] // 1000
            for c in group
        ]

        if actual != expected:
            continue

        result.append([
            bucket * 1000,
            group[0][1],
            max(c[2] for c in group),
            min(c[3] for c in group),
            group[-1][4],
            sum(c[5] for c in group),
        ])

    return result


# =====================================================
# ICHIMOKU
# =====================================================

def midpoint(candles, index, period):
    if index + 1 < period:
        return None

    section = candles[
        index - period + 1:index + 1
    ]

    highest = max(c[2] for c in section)
    lowest = min(c[3] for c in section)

    return (highest + lowest) / 2


def ichimoku(candles, index):
    """
    Cloud values are taken from 26 candles earlier.
    This represents the cloud visible at the current
    candle and avoids using future information.
    """

    if index < 77:
        return None

    tenkan = midpoint(
        candles, index, TENKAN
    )

    kijun = midpoint(
        candles, index, KIJUN
    )

    source_index = index - 26

    source_tenkan = midpoint(
        candles, source_index, TENKAN
    )

    source_kijun = midpoint(
        candles, source_index, KIJUN
    )

    span_a = (
        source_tenkan + source_kijun
    ) / 2

    span_b = midpoint(
        candles, source_index, SENKOU_B
    )

    if span_b is None:
        return None

    return {
        "close": candles[index][4],
        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_top": max(span_a, span_b),
        "cloud_bottom": min(span_a, span_b),
    }


def trend(candles, index):
    values = ichimoku(candles, index)

    if values is None:
        return "NEUTRAL"

    if (
        values["close"] > values["cloud_top"]
        and values["tenkan"] > values["kijun"]
    ):
        return "BULLISH"

    if (
        values["close"] < values["cloud_bottom"]
        and values["tenkan"] < values["kijun"]
    ):
        return "BEARISH"

    return "NEUTRAL"


# =====================================================
# HIGHER TIMEFRAME TREND MAP
# =====================================================

def build_trend_map(candles, timeframe_seconds):
    close_times = []
    states = []

    for i in range(len(candles)):
        close_time = (
            candles[i][0] // 1000
            + timeframe_seconds
        )

        close_times.append(close_time)
        states.append(trend(candles, i))

    return close_times, states


def confirmed_trend(mapping, timestamp):
    close_times, states = mapping

    # Only candles closed at or before timestamp.
    index = bisect_right(
        close_times,
        timestamp
    ) - 1

    if index < 0:
        return "NEUTRAL"

    return states[index]


# =====================================================
# ENTRY SIGNALS
# =====================================================

def make_signal(
    candles,
    index,
    trend15,
    trend45,
    method
):
    if index < 80:
        return None

    current = candles[index]
    previous = candles[index - 1]

    values = ichimoku(candles, index)

    if values is None:
        return None

    side = None

    if (
        trend15 == "BULLISH"
        and trend45 == "BULLISH"
        and current[4] > values["cloud_top"]
        and values["tenkan"] > values["kijun"]
    ):
        side = "LONG"

    elif (
        trend15 == "BEARISH"
        and trend45 == "BEARISH"
        and current[4] < values["cloud_bottom"]
        and values["tenkan"] < values["kijun"]
    ):
        side = "SHORT"

    if side is None:
        return None

    lookback = candles[
        index - BREAKOUT_LOOKBACK:index
    ]

    recent_high = max(c[2] for c in lookback)
    recent_low = min(c[3] for c in lookback)

    if method == "BREAKOUT":

        if side == "LONG":
            valid = (
                previous[4] <= recent_high
                and current[4] > recent_high
            )
        else:
            valid = (
                previous[4] >= recent_low
                and current[4] < recent_low
            )

        if not valid:
            return None

    elif method == "PULLBACK":

        recent = candles[
            index - PULLBACK_LOOKBACK:index + 1
        ]

        touched_tenkan = any(
            c[3] <= values["tenkan"] <= c[2]
            for c in recent
        )

        if side == "LONG":
            valid = (
                touched_tenkan
                and current[4] > values["tenkan"]
                and current[4] > previous[4]
            )
        else:
            valid = (
                touched_tenkan
                and current[4] < values["tenkan"]
                and current[4] < previous[4]
            )

        if not valid:
            return None

    else:
        raise ValueError("Unknown method")

    # Structural stop based on recent candles.
    structure = candles[index - 9:index + 1]

    if side == "LONG":
        stop = min(c[3] for c in structure)

        if stop >= current[4]:
            return None

    else:
        stop = max(c[2] for c in structure)

        if stop <= current[4]:
            return None

    return {
        "side": side,
        "signal_close": current[4],
        "stop": stop,
    }


# =====================================================
# BACKTEST ENGINE
# =====================================================

def backtest(candles, map15, map45, method):
    balance = START_BALANCE
    peak_balance = balance
    max_drawdown = 0.0

    trades = []
    position = None

    for i in range(80, len(candles)):

        candle = candles[i]

        # ---------------------------------------------
        # Manage open position
        # ---------------------------------------------

        if position is not None:
            side = position["side"]

            if side == "LONG":
                hit_sl = candle[3] <= position["stop"]
                hit_tp = candle[2] >= position["target"]
            else:
                hit_sl = candle[2] >= position["stop"]
                hit_tp = candle[3] <= position["target"]

            if hit_sl or hit_tp:

                # Conservative assumption:
                # If both hit in one candle, SL first.
                if hit_sl:
                    raw_exit = position["stop"]
                    result = "LOSS"
                else:
                    raw_exit = position["target"]
                    result = "WIN"

                if side == "LONG":
                    exit_price = raw_exit * (1 - SLIPPAGE)

                    gross = (
                        exit_price - position["entry"]
                    ) * position["quantity"]

                else:
                    exit_price = raw_exit * (1 + SLIPPAGE)

                    gross = (
                        position["entry"] - exit_price
                    ) * position["quantity"]

                exit_fee = (
                    abs(exit_price)
                    * position["quantity"]
                    * FEE_RATE
                )

                net = (
                    gross
                    - position["entry_fee"]
                    - exit_fee
                )

                balance += net

                trades.append({
                    "side": side,
                    "net": net,
                    "result": (
                        "WIN" if net > 0 else "LOSS"
                    ),
                })

                position = None

                peak_balance = max(
                    peak_balance,
                    balance
                )

                if peak_balance > 0:
                    drawdown = (
                        peak_balance - balance
                    ) / peak_balance * 100

                    max_drawdown = max(
                        max_drawdown,
                        drawdown
                    )

            continue

        # ---------------------------------------------
        # Signal at candle close
        # Entry at next candle open
        # ---------------------------------------------

        if i + 1 >= len(candles):
            break

        signal_time = (
            candle[0] // 1000 + 180
        )

        trend15 = confirmed_trend(
            map15,
            signal_time
        )

        trend45 = confirmed_trend(
            map45,
            signal_time
        )

        signal = make_signal(
            candles,
            i,
            trend15,
            trend45,
            method
        )

        if signal is None:
            continue

        next_open = candles[i + 1][1]

        side = signal["side"]
        stop = signal["stop"]

        if side == "LONG":
            entry = next_open * (1 + SLIPPAGE)
            stop_distance = entry - stop

        else:
            entry = next_open * (1 - SLIPPAGE)
            stop_distance = stop - entry

        if stop_distance <= 0:
            continue

        if balance <= 0:
            break

        risk_cash = balance * RISK_PER_TRADE
        quantity = risk_cash / stop_distance

        if side == "LONG":
            target = entry + stop_distance * MIN_RR
        else:
            target = entry - stop_distance * MIN_RR

        entry_fee = (
            abs(entry) * quantity * FEE_RATE
        )

        position = {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "entry_fee": entry_fee,
        }

    # ---------------------------------------------
    # Close any remaining position at final close
    # ---------------------------------------------

    if position is not None and candles:
        last_close = candles[-1][4]
        side = position["side"]

        if side == "LONG":
            exit_price = last_close * (1 - SLIPPAGE)
            gross = (
                exit_price - position["entry"]
            ) * position["quantity"]
        else:
            exit_price = last_close * (1 + SLIPPAGE)
            gross = (
                position["entry"] - exit_price
            ) * position["quantity"]

        exit_fee = (
            abs(exit_price)
            * position["quantity"]
            * FEE_RATE
        )

        net = (
            gross
            - position["entry_fee"]
            - exit_fee
        )

        balance += net

        trades.append({
            "side": side,
            "net": net,
            "result": "WIN" if net > 0 else "LOSS",
        })

    # ---------------------------------------------
    # Statistics
    # ---------------------------------------------

    wins = sum(
        1 for t in trades
        if t["net"] > 0
    )

    losses = sum(
        1 for t in trades
        if t["net"] <= 0
    )

    gross_profit = sum(
        t["net"] for t in trades
        if t["net"] > 0
    )

    gross_loss = abs(sum(
        t["net"] for t in trades
        if t["net"] < 0
    ))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else 0.0
    )

    win_rate = (
        wins / len(trades) * 100
        if trades
        else 0.0
    )

    long_trades = sum(
        1 for t in trades
        if t["side"] == "LONG"
    )

    short_trades = sum(
        1 for t in trades
        if t["side"] == "SHORT"
    )

    return {
        "method": method,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_profit": balance - START_BALANCE,
        "final_balance": balance,
        "drawdown": max_drawdown,
        "long_trades": long_trades,
        "short_trades": short_trades,
    }


# =====================================================
# REPORT
# =====================================================

def print_report(symbol, result):
    print("\n" + "=" * 50)
    print("SYMBOL:", symbol)
    print("METHOD:", result["method"])
    print("=" * 50)

    print("Trades:", result["trades"])
    print("Wins:", result["wins"])
    print("Losses:", result["losses"])

    print(
        "Win rate:",
        round(result["win_rate"], 2),
        "%"
    )

    print(
        "Profit factor:",
        round(result["profit_factor"], 3)
    )

    print(
        "Net profit:",
        round(result["net_profit"], 2)
    )

    print(
        "Final balance:",
        round(result["final_balance"], 2)
    )

    print(
        "Max drawdown:",
        round(result["drawdown"], 2),
        "%"
    )

    print("Long trades:", result["long_trades"])
    print("Short trades:", result["short_trades"])


# =====================================================
# RUN SYMBOL
# =====================================================

def run_symbol(symbol, start_ts, end_ts):
    print("\nDownloading:", symbol)

    one_minute = fetch_klines(
        symbol,
        "Min1",
        start_ts,
        end_ts
    )

    fifteen_minute = fetch_klines(
        symbol,
        "Min15",
        start_ts,
        end_ts
    )

    three_minute = aggregate(
        one_minute,
        1,
        3
    )

    forty_five_minute = aggregate(
        fifteen_minute,
        15,
        45
    )

    print("1m candles:", len(one_minute))
    print("3m candles:", len(three_minute))
    print("15m candles:", len(fifteen_minute))
    print("45m candles:", len(forty_five_minute))

    if (
        len(three_minute) < 200
        or len(fifteen_minute) < 100
        or len(forty_five_minute) < 80
    ):
        print("Not enough data:", symbol)
        return

    map15 = build_trend_map(
        fifteen_minute,
        15 * 60
    )

    map45 = build_trend_map(
        forty_five_minute,
        45 * 60
    )

    # Test each entry method separately.
    for method in ("BREAKOUT", "PULLBACK"):
        result = backtest(
            three_minute,
            map15,
            map45,
            method
        )

        print_report(symbol, result)


# =====================================================
# MAIN
# =====================================================

def main():
    print("NIMA ICHIMOKU SCALPER V2")
    print("BACKTEST ONLY - NO LIVE ORDERS")
    print("Exchange: MEXC")
    print("Entry timeframe: 3m")
    print("Trend timeframes: 15m / 45m")
    print("Test period:", DAYS, "days")

    end_ts = int(time.time())
    start_ts = end_ts - DAYS * 24 * 60 * 60

    for symbol in SYMBOLS:
        try:
            run_symbol(
                symbol,
                start_ts,
                end_ts
            )

        except Exception as error:
            print(
                "\nERROR:",
                symbol,
                str(error)
            )

    print("\nBACKTEST FINISHED")


if __name__ == "__main__":
    main()
