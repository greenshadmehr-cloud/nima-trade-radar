
import time
import requests
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

# =========================================================
# NIMA ICHIMOKU SCALPER V2
# Research / Backtest only - NO live orders
# =========================================================

BASE_URL = "https://contract.mexc.com"

SYMBOLS = [
    "BTC_USDT",
    "ETH_USDT",
    "SOL_USDT",
    "XRP_USDT",
    "DOGE_USDT",
]

DAYS = 30
START_BALANCE = 1000.0

RISK_PER_TRADE = 0.005
MAX_LEVERAGE = 3.0
RISK_REWARD = 1.5

FEE_RATE = 0.0005
SLIPPAGE = 0.0002

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52

LOOKBACK_STOP = 10
MIN_RR = 1.5

REQUEST_TIMEOUT = 20
REQUEST_DELAY = 0.15
CANDLE_CHUNK = 400

METHODS = ["BREAKOUT", "PULLBACK"]


# =========================================================
# DATA FETCHING
# =========================================================

def fetch_klines(symbol, interval, days):
    """
    Fetch historical MEXC futures candles.
    MEXC intervals: Min1, Min15
    """

    now = int(time.time())
    start_time = now - days * 24 * 60 * 60

    interval_seconds = {
        "Min1": 60,
        "Min15": 900,
    }

    if interval not in interval_seconds:
        raise ValueError(f"Unsupported interval: {interval}")

    seconds = interval_seconds[interval]
    chunk_seconds = CANDLE_CHUNK * seconds

    all_rows = []
    cursor = start_time

    while cursor < now:
        end = min(cursor + chunk_seconds, now)

        url = f"{BASE_URL}/api/v1/contract/kline/{symbol}"
        params = {
            "interval": interval,
            "start": cursor,
            "end": end,
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

            if not payload.get("success"):
                print(f"API error for {symbol}: {payload}")
                cursor = end
                time.sleep(REQUEST_DELAY)
                continue

            data = payload.get("data", {})

            if not isinstance(data, dict) or not data.get("time"):
                cursor = end
                time.sleep(REQUEST_DELAY)
                continue

            times = data.get("time", [])
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("vol", [])

            count = min(
                len(times),
                len(opens),
                len(highs),
                len(lows),
                len(closes),
                len(volumes),
            )

            for i in range(count):
                all_rows.append({
                    "time": int(times[i]),
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    "volume": float(volumes[i]),
                })

        except Exception as exc:
            print(f"Fetch error {symbol} {interval}: {exc}")

        cursor = end
        time.sleep(REQUEST_DELAY)

    unique = {row["time"]: row for row in all_rows}
    candles = sorted(unique.values(), key=lambda x: x["time"])

    return candles


# =========================================================
# CANDLE AGGREGATION
# =========================================================

def aggregate_candles(candles, factor):
    """
    Aggregate candles into larger timeframes.
    Only complete groups are retained.
    """

    result = []

    for start in range(0, len(candles) - factor + 1, factor):
        group = candles[start:start + factor]

        if len(group) != factor:
            continue

        expected = group[0]["time"]
        valid = True

        for i, candle in enumerate(group):
            if candle["time"] != expected + i * 60:
                valid = False
                break

        if not valid:
            continue

        result.append({
            "time": group[0]["time"],
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
            "volume": sum(x["volume"] for x in group),
        })

    return result


def aggregate_15m_to_45m(candles):
    result = []

    for start in range(0, len(candles) - 2, 3):
        group = candles[start:start + 3]

        if len(group) != 3:
            continue

        if group[1]["time"] != group[0]["time"] + 900:
            continue

        if group[2]["time"] != group[0]["time"] + 1800:
            continue

        result.append({
            "time": group[0]["time"],
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
            "volume": sum(x["volume"] for x in group),
        })

    return result


# =========================================================
# ICHIMOKU
# =========================================================

def midpoint(candles, index, period):
    if index + 1 < period:
        return None

    window = candles[index - period + 1:index + 1]

    highest = max(x["high"] for x in window)
    lowest = min(x["low"] for x in window)

    return (highest + lowest) / 2


def calculate_ichimoku(candles):
    result = []

    for i, candle in enumerate(candles):
        tenkan = midpoint(candles, i, TENKAN_PERIOD)
        kijun = midpoint(candles, i, KIJUN_PERIOD)

        span_a = None
        span_b = None

        if tenkan is not None and kijun is not None:
            span_a = (tenkan + kijun) / 2

        span_b = midpoint(candles, i, SENKOU_B_PERIOD)

        result.append({
            "tenkan": tenkan,
            "kijun": kijun,
            "span_a": span_a,
            "span_b": span_b,
            "close": candle["close"],
        })

    return result


def get_trend(candles, ichimoku, index):
    """
    Use the cloud that is visible at the current candle.
    Senkou values are shifted 26 candles forward.
    """

    if index < 77:
        return "NEUTRAL"

    current = ichimoku[index]
    cloud_source = ichimoku[index - KIJUN_PERIOD]

    if (
        current["tenkan"] is None
        or current["kijun"] is None
        or cloud_source["span_a"] is None
        or cloud_source["span_b"] is None
    ):
        return "NEUTRAL"

    cloud_top = max(
        cloud_source["span_a"],
        cloud_source["span_b"],
    )

    cloud_bottom = min(
        cloud_source["span_a"],
        cloud_source["span_b"],
    )

    close = candles[index]["close"]

    if (
        close > cloud_top
        and current["tenkan"] > current["kijun"]
    ):
        return "BULLISH"

    if (
        close < cloud_bottom
        and current["tenkan"] < current["kijun"]
    ):
        return "BEARISH"

    return "NEUTRAL"


def build_trend_data(candles):
    ichimoku = calculate_ichimoku(candles)

    close_times = [
        candle["time"] + 900
        for candle in candles
    ]

    trends = [
        get_trend(candles, ichimoku, i)
        for i in range(len(candles))
    ]

    return close_times, trends


def get_confirmed_trend(close_times, trends, signal_time):
    index = bisect_right(close_times, signal_time) - 1

    if index < 0:
        return "NEUTRAL"

    return trends[index]


# =========================================================
# SIGNALS
# =========================================================

def breakout_signal(candles, ichimoku, index):
    if index < max(77, LOOKBACK_STOP + 2):
        return None

    current = candles[index]
    previous = candles[index - 1]

    data = ichimoku[index]

    if data["tenkan"] is None or data["kijun"] is None:
        return None

    recent = candles[index - LOOKBACK_STOP:index]

    recent_high = max(x["high"] for x in recent)
    recent_low = min(x["low"] for x in recent)

    if (
        current["close"] > recent_high
        and previous["close"] <= recent_high
        and data["tenkan"] > data["kijun"]
    ):
        return "LONG"

    if (
        current["close"] < recent_low
        and previous["close"] >= recent_low
        and data["tenkan"] < data["kijun"]
    ):
        return "SHORT"

    return None


def pullback_signal(candles, ichimoku, index):
    if index < max(77, 6):
        return None

    current = candles[index]
    data = ichimoku[index]

    if data["tenkan"] is None or data["kijun"] is None:
        return None

    recent = candles[index - 5:index]

    touched_tenkan = any(
        x["low"] <= ichimoku[index - 5 + i]["tenkan"]
        <= x["high"]
        for i, x in enumerate(recent)
        if ichimoku[index - 5 + i]["tenkan"] is not None
    )

    if (
        touched_tenkan
        and data["tenkan"] > data["kijun"]
        and current["close"] > data["tenkan"]
        and current["close"] > current["open"]
    ):
        return "LONG"

    if (
        touched_tenkan
        and data["tenkan"] < data["kijun"]
        and current["close"] < data["tenkan"]
        and current["close"] < current["open"]
    ):
        return "SHORT"

    return None


def get_signal(method, candles, ichimoku, index):
    if method == "BREAKOUT":
        return breakout_signal(candles, ichimoku, index)

    if method == "PULLBACK":
        return pullback_signal(candles, ichimoku, index)

    return None


# =========================================================
# POSITION SIZING
# =========================================================

def calculate_position_size(balance, entry, stop):
    distance = abs(entry - stop)

    if distance <= 0 or entry <= 0:
        return 0.0

    risk_cash = balance * RISK_PER_TRADE

    # Risk-based quantity
    quantity = risk_cash / distance

    # Maximum notional allowed by leverage
    max_notional = balance * MAX_LEVERAGE
    max_quantity = max_notional / entry

    quantity = min(quantity, max_quantity)

    if quantity <= 0:
        return 0.0

    return quantity


# =========================================================
# BACKTEST HELPERS
# =========================================================

def apply_entry_slippage(price, side):
    if side == "LONG":
        return price * (1 + SLIPPAGE)

    return price * (1 - SLIPPAGE)


def apply_exit_slippage(price, side):
    if side == "LONG":
        return price * (1 - SLIPPAGE)

    return price * (1 + SLIPPAGE)


def unrealized_pnl(position, mark_price):
    if position["side"] == "LONG":
        return (
            mark_price - position["entry"]
        ) * position["quantity"]

    return (
        position["entry"] - mark_price
    ) * position["quantity"]


def close_position(position, exit_price, balance, reason):
    actual_exit = apply_exit_slippage(
        exit_price,
        position["side"],
    )

    gross_pnl = unrealized_pnl(position, actual_exit)

    exit_fee = (
        abs(actual_exit * position["quantity"])
        * FEE_RATE
    )

    net_pnl = gross_pnl - exit_fee

    balance += net_pnl

    trade = {
        "side": position["side"],
        "entry": position["entry"],
        "exit": actual_exit,
        "quantity": position["quantity"],
        "gross_pnl": gross_pnl,
        "fees": position["entry_fee"] + exit_fee,
        "net_pnl": net_pnl - position["entry_fee"],
        "reason": reason,
    }

    return balance, trade


# =========================================================
# BACKTEST ENGINE
# =========================================================

def backtest(symbol, method, candles_3m, candles_15m, candles_45m):
    if len(candles_3m) < 100:
        print(f"{symbol}: Not enough 3m candles")
        return None

    ichimoku_3m = calculate_ichimoku(candles_3m)

    times_15, trends_15 = build_trend_data(candles_15m)
    times_45, trends_45 = build_trend_data(candles_45m)

    balance = START_BALANCE
    peak_equity = START_BALANCE
    max_drawdown = 0.0

    position = None
    trades = []

    long_count = 0
    short_count = 0

    for i in range(80, len(candles_3m) - 1):
        candle = candles_3m[i]

        # -------------------------------------------------
        # Manage open position
        # -------------------------------------------------

        if position is not None:
            stop_hit = False
            target_hit = False

            if position["side"] == "LONG":
                stop_hit = candle["low"] <= position["stop"]
                target_hit = candle["high"] >= position["target"]

            else:
                stop_hit = candle["high"] >= position["stop"]
                target_hit = candle["low"] <= position["target"]

            # Conservative assumption:
            # if stop and target are both hit in one candle,
            # assume stop was hit first.
            if stop_hit:
                exit_price = position["stop"]

                if position["side"] == "LONG" and candle["open"] < position["stop"]:
                    exit_price = candle["open"]

                if position["side"] == "SHORT" and candle["open"] > position["stop"]:
                    exit_price = candle["open"]

                balance, trade = close_position(
                    position,
                    exit_price,
                    balance,
                    "STOP",
                )

                trades.append(trade)
                position = None

            elif target_hit:
                balance, trade = close_position(
                    position,
                    position["target"],
                    balance,
                    "TARGET",
                )

                trades.append(trade)
                position = None

            # Do not open a new trade on the same candle
            if position is None:
                continue

        # -------------------------------------------------
        # Mark-to-market drawdown
        # -------------------------------------------------

        if position is not None:
            unrealized = unrealized_pnl(
                position,
                candle["close"],
            )

            estimated_exit_fee = (
                candle["close"]
                * position["quantity"]
                * (FEE_RATE + SLIPPAGE)
            )

            equity = balance + unrealized - estimated_exit_fee

        else:
            equity = balance

        peak_equity = max(peak_equity, equity)

        if peak_equity > 0:
            drawdown = (
                peak_equity - equity
            ) / peak_equity * 100

            max_drawdown = max(
                max_drawdown,
                drawdown,
            )

        # -------------------------------------------------
        # New signal
        # -------------------------------------------------

        if position is not None:
            continue

        signal_time = candle["time"] + 180

        trend_15 = get_confirmed_trend(
            times_15,
            trends_15,
            signal_time,
        )

        trend_45 = get_confirmed_trend(
            times_45,
            trends_45,
            signal_time,
        )

        if trend_15 != trend_45:
            continue

        if trend_15 == "NEUTRAL":
            continue

        signal = get_signal(
            method,
            candles_3m,
            ichimoku_3m,
            i,
        )

        if signal is None:
            continue

        if signal == "LONG" and trend_15 != "BULLISH":
            continue

        if signal == "SHORT" and trend_15 != "BEARISH":
            continue

        # Entry occurs at the next candle open
        next_candle = candles_3m[i + 1]

        entry = apply_entry_slippage(
            next_candle["open"],
            signal,
        )

        stop_window = candles_3m[
            max(0, i - LOOKBACK_STOP + 1):i + 1
        ]

        if signal == "LONG":
            stop = min(x["low"] for x in stop_window)

            if stop >= entry:
                continue

            risk_distance = entry - stop
            target = entry + risk_distance * RISK_REWARD

        else:
            stop = max(x["high"] for x in stop_window)

            if stop <= entry:
                continue

            risk_distance = stop - entry
            target = entry - risk_distance * RISK_REWARD

        actual_rr = abs(target - entry) / risk_distance

        if actual_rr < MIN_RR:
            continue

        quantity = calculate_position_size(
            balance,
            entry,
            stop,
        )

        if quantity <= 0:
            continue

        notional = quantity * entry
        required_margin = notional / MAX_LEVERAGE

        if required_margin > balance:
            continue

        entry_fee = notional * FEE_RATE
        balance -= entry_fee

        position = {
            "side": signal,
            "entry": entry,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "entry_fee": entry_fee,
        }

        if signal == "LONG":
            long_count += 1
        else:
            short_count += 1

    # =====================================================
    # Close any remaining open position at final candle
    # =====================================================

    if position is not None:
        final_price = candles_3m[-1]["close"]

        balance, trade = close_position(
            position,
            final_price,
            balance,
            "END_OF_DATA",
        )

        trades.append(trade)
        position = None

        peak_equity = max(peak_equity, balance)

        if peak_equity > 0:
            drawdown = (
                peak_equity - balance
            ) / peak_equity * 100

            max_drawdown = max(
                max_drawdown,
                drawdown,
            )

    # =====================================================
    # Statistics
    # =====================================================

    wins = [
        x for x in trades
        if x["net_pnl"] > 0
    ]

    losses = [
        x for x in trades
        if x["net_pnl"] <= 0
    ]

    gross_profit = sum(
        x["net_pnl"] for x in wins
    )

    gross_loss = abs(sum(
        x["net_pnl"] for x in losses
    ))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf") if gross_profit > 0
        else 0
    )

    win_rate = (
        len(wins) / len(trades) * 100
        if trades else 0
    )

    net_profit = balance - START_BALANCE

    return {
        "symbol": symbol,
        "method": method,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_profit": net_profit,
        "net_percent": net_profit / START_BALANCE * 100,
        "max_drawdown": max_drawdown,
        "longs": long_count,
        "shorts": short_count,
        "final_balance": balance,
    }


# =========================================================
# REPORT
# =========================================================

def print_result(result):
    if result is None:
        return

    print("\n" + "=" * 55)
    print(f"SYMBOL: {result['symbol']}")
    print(f"METHOD: {result['method']}")
    print("-" * 55)

    print(f"Trades       : {result['trades']}")
    print(f"Wins         : {result['wins']}")
    print(f"Losses       : {result['losses']}")
    print(f"Win rate     : {result['win_rate']:.2f}%")

    if result["profit_factor"] == float("inf"):
        print("Profit factor: INF")
    else:
        print(
            f"Profit factor: "
            f"{result['profit_factor']:.3f}"
        )

    print(
        f"Net profit   : "
        f"${result['net_profit']:.2f}"
    )

    print(
        f"Net return   : "
        f"{result['net_percent']:.2f}%"
    )

    print(
        f"Max drawdown : "
        f"{result['max_drawdown']:.2f}%"
    )

    print(f"Long trades  : {result['longs']}")
    print(f"Short trades : {result['shorts']}")

    print(
        f"Final balance: "
        f"${result['final_balance']:.2f}"
    )


# =========================================================
# SYMBOL RUNNER
# =========================================================

def run_symbol(symbol):
    print("\n" + "#" * 60)
    print(f"Downloading data for {symbol}")

    candles_1m = fetch_klines(
        symbol,
        "Min1",
        DAYS,
    )

    candles_15m = fetch_klines(
        symbol,
        "Min15",
        DAYS,
    )

    if not candles_1m or not candles_15m:
        print(f"Data unavailable for {symbol}")
        return []

    candles_3m = aggregate_candles(
        candles_1m,
        3,
    )

    candles_45m = aggregate_15m_to_45m(
        candles_15m,
    )

    print(f"1m candles : {len(candles_1m)}")
    print(f"3m candles : {len(candles_3m)}")
    print(f"15m candles: {len(candles_15m)}")
    print(f"45m candles: {len(candles_45m)}")

    results = []

    for method in METHODS:
        print(f"\nRunning {method} backtest...")

        result = backtest(
            symbol,
            method,
            candles_3m,
            candles_15m,
            candles_45m,
        )

        print_result(result)

        if result is not None:
            results.append(result)

    return results


# =========================================================
# MAIN
# =========================================================

def main():
    print("\nNIMA ICHIMOKU SCALPER V2")
    print("MEXC FUTURES - BACKTEST ONLY")
    print("=" * 60)

    print(f"Period       : {DAYS} days")
    print(f"Start balance: ${START_BALANCE:.2f}")
    print(f"Risk/trade   : {RISK_PER_TRADE * 100:.2f}%")
    print(f"Max leverage : {MAX_LEVERAGE}x")
    print(f"Risk/Reward  : 1:{RISK_REWARD}")
    print(f"Fee per side : {FEE_RATE * 100:.3f}%")
    print(f"Slippage     : {SLIPPAGE * 100:.3f}%")

    all_results = []

    for symbol in SYMBOLS:
        try:
            results = run_symbol(symbol)
            all_results.extend(results)

        except Exception as exc:
            print(f"Error processing {symbol}: {exc}")

    print("\n\nFINAL COMPARISON")
    print("=" * 75)

    print(
        f"{'SYMBOL':<12}"
        f"{'METHOD':<12}"
        f"{'TRADES':>8}"
        f"{'WIN%':>9}"
        f"{'PF':>9}"
        f"{'NET%':>10}"
        f"{'DD%':>9}"
    )

    for r in all_results:
        pf = (
            "INF"
            if r["profit_factor"] == float("inf")
            else f"{r['profit_factor']:.2f}"
        )

        print(
            f"{r['symbol']:<12}"
            f"{r['method']:<12}"
            f"{r['trades']:>8}"
            f"{r['win_rate']:>8.2f}%"
            f"{pf:>9}"
            f"{r['net_percent']:>9.2f}%"
            f"{r['max_drawdown']:>8.2f}%"
        )

    print("\nBacktest completed.")
    print("This script does not place live orders.")


if __name__ == "__main__":
    main()
