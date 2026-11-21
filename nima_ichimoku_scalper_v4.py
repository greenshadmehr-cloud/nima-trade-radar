"""NIMA ICHIMOKU SCALPER v4 — MEXC USDT perpetual research/backtest only.
No order placement. Open Interest is intentionally excluded.

Usage:
  pip install requests
  python nima_ichimoku_scalper_v4.py --symbol BTC_USDT --days 30
  python nima_ichimoku_scalper_v4.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from typing import Optional

import requests

BASE = "https://contract.mexc.com"
TIMEOUT = 20
FEE_SIDE = 0.0005
SLIPPAGE_SIDE = 0.0002
RISK_FRACTION = 0.005
MAX_LEVERAGE = 3.0
REWARD_RISK = 1.5
ADX_MIN = 18.0
ICHIMOKU = (9, 26, 52)
INTERVALS = {
    "5m": ("Min5", 300),
    "30m": ("Min30", 1800),
    "1h": ("Min60", 3600),
}


def api(path: str, params: dict | None = None) -> dict:
    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(BASE + path, params=params, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("success") is False:
                raise RuntimeError(f"MEXC API error: {data}")
            return data
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API request failed after retries: {last_error}")


def fetch_klines(symbol: str, tf: str, start: int, end: int) -> list[list[float]]:
    if tf not in INTERVALS:
        raise ValueError(f"Unsupported timeframe: {tf}")
    api_tf, seconds = INTERVALS[tf]
    rows: dict[int, list[float]] = {}
    cursor = start
    now = int(time.time())
    chunk = seconds * 450

    while cursor < min(end, now):
        stop = min(cursor + chunk, end, now)
        payload = api(
            f"/api/v1/contract/kline/{symbol}",
            {"interval": api_tf, "start": cursor, "end": stop},
        )
        data = payload.get("data") or {}
        times = data.get("time") or []
        for i, ts0 in enumerate(times):
            ts = int(ts0)
            if ts < start or ts + seconds > end or ts + seconds > now:
                continue
            rows[ts] = [
                ts,
                float(data["open"][i]),
                float(data["high"][i]),
                float(data["low"][i]),
                float(data["close"][i]),
                float(data["vol"][i]),
            ]
        cursor = stop
        time.sleep(0.08)

    return [rows[k] for k in sorted(rows)]


def midpoint(candles: list[list[float]], index: int, period: int) -> Optional[float]:
    if index - period + 1 < 0:
        return None
    window = candles[index - period + 1:index + 1]
    return (max(row[2] for row in window) + min(row[3] for row in window)) / 2.0


def ichimoku(candles: list[list[float]], index: int) -> Optional[dict]:
    if index < 77:
        return None

    tenkan = midpoint(candles, index, 9)
    kijun = midpoint(candles, index, 26)
    old_index = index - 26

    span_a_at_current_position = (
        midpoint(candles, old_index, 9) + midpoint(candles, old_index, 26)
    ) / 2.0
    span_b_at_current_position = midpoint(candles, old_index, 52)
    span_a_now = (tenkan + kijun) / 2.0
    span_b_now = midpoint(candles, index, 52)

    cloud_top = max(span_a_at_current_position, span_b_at_current_position)
    cloud_bottom = min(span_a_at_current_position, span_b_at_current_position)
    close = candles[index][4]

    bullish = (
        close > cloud_top
        and tenkan > kijun
        and span_a_now > span_b_now
        and close > candles[index - 26][4]
    )
    bearish = (
        close < cloud_bottom
        and tenkan < kijun
        and span_a_now < span_b_now
        and close < candles[index - 26][4]
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "bull": bullish,
        "bear": bearish,
    }


def atr(candles: list[list[float]], index: int, period: int = 14) -> Optional[float]:
    if index < period:
        return None
    true_ranges = []
    for j in range(index - period + 1, index + 1):
        previous_close = candles[j - 1][4]
        true_ranges.append(
            max(
                candles[j][2] - candles[j][3],
                abs(candles[j][2] - previous_close),
                abs(candles[j][3] - previous_close),
            )
        )
    return sum(true_ranges) / period


def adx(candles: list[list[float]], index: int, period: int = 14) -> Optional[float]:
    """ADX using Wilder smoothing; returns None until enough candles exist."""
    if index < period * 2:
        return None

    plus_dm = []
    minus_dm = []
    true_ranges = []

    for j in range(1, index + 1):
        up_move = candles[j][2] - candles[j - 1][2]
        down_move = candles[j - 1][3] - candles[j][3]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

        prev_close = candles[j - 1][4]
        true_ranges.append(
            max(
                candles[j][2] - candles[j][3],
                abs(candles[j][2] - prev_close),
                abs(candles[j][3] - prev_close),
            )
        )

    tr_smooth = sum(true_ranges[:period])
    plus_smooth = sum(plus_dm[:period])
    minus_smooth = sum(minus_dm[:period])
    dx_values = []

    for k in range(period, len(true_ranges)):
        tr_smooth = tr_smooth - tr_smooth / period + true_ranges[k]
        plus_smooth = plus_smooth - plus_smooth / period + plus_dm[k]
        minus_smooth = minus_smooth - minus_smooth / period + minus_dm[k]

        if tr_smooth <= 0:
            dx_values.append(0.0)
            continue

        plus_di = 100.0 * plus_smooth / tr_smooth
        minus_di = 100.0 * minus_smooth / tr_smooth
        denominator = plus_di + minus_di
        dx_values.append(
            100.0 * abs(plus_di - minus_di) / denominator if denominator else 0.0
        )

    if len(dx_values) < period:
        return None

    current_adx = sum(dx_values[:period]) / period
    for value in dx_values[period:]:
        current_adx = ((current_adx * (period - 1)) + value) / period
    return current_adx


def last_closed_index(candles: list[list[float]], timestamp: int, seconds: int) -> int:
    low, high, answer = 0, len(candles) - 1, -1
    while low <= high:
        middle = (low + high) // 2
        if candles[middle][0] + seconds <= timestamp:
            answer = middle
            low = middle + 1
        else:
            high = middle - 1
    return answer


def recent_pullback(candles: list[list[float]], index: int, side: str) -> bool:
    if index < 80:
        return False

    for j in range(max(77, index - 2), index + 1):
        state = ichimoku(candles, j)
        if not state:
            continue
        bar = candles[j]

        if side == "LONG":
            touched = bar[3] <= max(state["tenkan"], state["kijun"])
            recovered = bar[4] > state["kijun"]
            if state["bull"] and touched and recovered:
                return True

        if side == "SHORT":
            touched = bar[2] >= min(state["tenkan"], state["kijun"])
            recovered = bar[4] < state["kijun"]
            if state["bear"] and touched and recovered:
                return True

    return False


def make_signals(c5, c30, c60):
    signals = {}
    last_signal_30m = None

    for i, bar in enumerate(c5):
        timestamp = bar[0] + 300
        i30 = last_closed_index(c30, timestamp, 1800)
        i60 = last_closed_index(c60, timestamp, 3600)

        if i < 80 or i30 < 80 or i60 < 80:
            continue

        # Only use completed candles from all timeframes.
        hour_state = ichimoku(c60, i60)
        half_hour_state = ichimoku(c30, i30)
        five_min_state = ichimoku(c5, i)
        trend_strength = adx(c30, i30)

        if not hour_state or not half_hour_state or not five_min_state:
            continue
        if trend_strength is None or trend_strength < ADX_MIN:
            continue
        if last_signal_30m == i30:
            continue

        side = None
        if (
            hour_state["bull"]
            and half_hour_state["bull"]
            and recent_pullback(c30, i30, "LONG")
            and five_min_state["bull"]
            and bar[4] > max(c5[k][2] for k in range(i - 3, i))
        ):
            side = "LONG"

        elif (
            hour_state["bear"]
            and half_hour_state["bear"]
            and recent_pullback(c30, i30, "SHORT")
            and five_min_state["bear"]
            and bar[4] < min(c5[k][3] for k in range(i - 3, i))
        ):
            side = "SHORT"

        if not side:
            continue

        current_atr = atr(c5, i)
        if not current_atr or current_atr <= 0:
            continue

        swing_low = min(c5[k][3] for k in range(i - 5, i + 1))
        swing_high = max(c5[k][2] for k in range(i - 5, i + 1))
        close = bar[4]

        if side == "LONG":
            stop_price = min(swing_low, close - 1.5 * current_atr)
            if stop_price >= close:
                continue
        else:
            stop_price = max(swing_high, close + 1.5 * current_atr)
            if stop_price <= close:
                continue

        signals[i] = {
            "index": i,
            "time": bar[0],
            "side": side,
            "stop_price": stop_price,
            "adx_30m": trend_strength,
        }
        last_signal_30m = i30

    return signals


def close_trade(position, exit_price, exit_time, reason, balance):
    side = position["side"]
    exit_fill = exit_price * (
        1 - SLIPPAGE_SIDE if side == "LONG" else 1 + SLIPPAGE_SIDE
    )
    direction = 1 if side == "LONG" else -1
    gross = (exit_fill - position["entry_fill"]) * position["qty"] * direction
    exit_fee = abs(exit_fill * position["qty"]) * FEE_SIDE
    net = gross - exit_fee - position["entry_fee"]

    # Entry fee was deducted from cash when the position opened.
    balance += gross - exit_fee

    trade = {
        "side": side,
        "entry_time": position["entry_time"],
        "exit_time": exit_time,
        "entry": position["entry_fill"],
        "stop": position["stop"],
        "target": position["target"],
        "exit": exit_fill,
        "qty": position["qty"],
        "adx_30m": position["adx_30m"],
        "net_pnl": net,
        "risk_cash": position["risk_cash"],
        "r_multiple": net / max(position["risk_cash"], 1e-12),
        "reason": reason,
    }
    return balance, trade


def backtest(symbol: str, days: int, starting_balance: float):
    end = int(time.time())
    start = end - days * 86400

    c5 = fetch_klines(symbol, "5m", start - 500 * 300, end)
    c30 = fetch_klines(symbol, "30m", start - 120 * 1800, end)
    c60 = fetch_klines(symbol, "1h", start - 120 * 3600, end)

    if min(len(c5), len(c30), len(c60)) < 120:
        raise RuntimeError(
            f"Insufficient history: 5m={len(c5)}, 30m={len(c30)}, 1h={len(c60)}"
        )

    signals = make_signals(c5, c30, c60)
    balance = starting_balance
    peak_equity = starting_balance
    max_drawdown = 0.0
    trades = []
    position = None

    for i in range(80, len(c5)):
        bar = c5[i]
        if bar[0] < start:
            continue

        if position:
            side = position["side"]
            stop = position["stop"]
            target = position["target"]

            if side == "LONG":
                hit_stop = bar[3] <= stop
                hit_target = bar[2] >= target
                gap_stop = bar[1] < stop
            else:
                hit_stop = bar[2] >= stop
                hit_target = bar[3] <= target
                gap_stop = bar[1] > stop

            if hit_stop:
                # If price gaps through the stop, model the worse opening price.
                exit_price = bar[1] if gap_stop else stop
                balance, trade = close_trade(
                    position, exit_price, bar[0], "STOP", balance
                )
                trades.append(trade)
                position = None

            elif hit_target:
                balance, trade = close_trade(
                    position, target, bar[0], "TARGET", balance
                )
                trades.append(trade)
                position = None

        # Enter at the next candle open after a completed signal candle.
        signal_index = i - 1
        if position is None and signal_index in signals:
            signal = signals[signal_index]
            side = signal["side"]
            raw_entry = bar[1]
            entry_fill = raw_entry * (
                1 + SLIPPAGE_SIDE if side == "LONG" else 1 - SLIPPAGE_SIDE
            )
            stop = signal["stop_price"]

            if side == "LONG" and stop >= entry_fill:
                continue
            if side == "SHORT" and stop <= entry_fill:
                continue

            distance = abs(entry_fill - stop)
            risk_budget = balance * RISK_FRACTION
            # Include estimated round-trip fees and slippage in position sizing.
            estimated_cost_per_unit = (
                distance
                + entry_fill * FEE_SIDE
                + stop * FEE_SIDE
                + entry_fill * SLIPPAGE_SIDE
                + stop * SLIPPAGE_SIDE
            )
            qty_by_risk = risk_budget / estimated_cost_per_unit
            qty_by_leverage = balance * MAX_LEVERAGE / entry_fill
            qty = min(qty_by_risk, qty_by_leverage)

            if qty <= 0:
                continue

            entry_fee = entry_fill * qty * FEE_SIDE
            balance -= entry_fee
            actual_risk_cash = qty * estimated_cost_per_unit

            target = (
                entry_fill + distance * REWARD_RISK
                if side == "LONG"
                else entry_fill - distance * REWARD_RISK
            )

            position = {
                "side": side,
                "entry_fill": entry_fill,
                "stop": stop,
                "target": target,
                "qty": qty,
                "entry_fee": entry_fee,
                "risk_cash": actual_risk_cash,
                "entry_time": bar[0],
                "adx_30m": signal["adx_30m"],
            }

        # Mark-to-market equity, including estimated exit fee.
        if position:
            mark = bar[4]
            direction = 1 if position["side"] == "LONG" else -1
            unrealized = (
                (mark - position["entry_fill"]) * position["qty"] * direction
            )
            estimated_exit_fee = abs(mark * position["qty"]) * FEE_SIDE
            equity = balance + unrealized - estimated_exit_fee
        else:
            equity = balance

        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_drawdown = max(
                max_drawdown, (peak_equity - equity) / peak_equity
            )

    # Close any remaining position at the final available close and include it in CSV.
    if position:
        last_bar = c5[-1]
        balance, trade = close_trade(
            position, last_bar[4], last_bar[0], "END_OF_TEST", balance
        )
        trades.append(trade)
        position = None
        peak_equity = max(peak_equity, balance)
        if peak_equity > 0:
            max_drawdown = max(
                max_drawdown, (peak_equity - balance) / peak_equity
            )

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss
        else (math.inf if gross_profit else 0.0)
    )

    print(f"\nNIMA ICHIMOKU SCALPER v4 | {symbol} | {days} days")
    print(f"Candles: 5m={len(c5)} 30m={len(c30)} 1h={len(c60)}")
    print(f"Signals={len(signals)} | Trades={len(trades)}")
    print(
        f"Start=${starting_balance:.2f} End=${balance:.2f} "
        f"Net={(balance / starting_balance - 1) * 100:.2f}%"
    )
    print(
        f"Wins={len(wins)} Losses={len(losses)} "
        f"WinRate={(len(wins) / len(trades) * 100 if trades else 0):.1f}% "
        f"PF={profit_factor:.3f} MaxDD={max_drawdown * 100:.2f}%"
    )

    for side in ("LONG", "SHORT"):
        subset = [t for t in trades if t["side"] == side]
        side_wins = sum(t["net_pnl"] > 0 for t in subset)
        side_pnl = sum(t["net_pnl"] for t in subset)
        print(
            f"{side}: trades={len(subset)} wins={side_wins} "
            f"winrate={(side_wins / len(subset) * 100 if subset else 0):.1f}% "
            f"net=${side_pnl:.2f}"
        )

    if trades:
        output = f"trades_{symbol}_{days}d.csv"
        with open(output, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(trades)
        print(f"Trade log: {output}")
    else:
        print("Trade log: no trades; CSV not created.")

    return trades


def self_test():
    candles = []
    price = 100.0
    for i in range(400):
        opening = price
        price += 0.12 + (0.04 if i % 9 else -0.03)
        candles.append(
            [i * 300, opening, price + 0.25, opening - 0.25, price, 100.0]
        )

    state = ichimoku(candles, 200)
    assert state is not None
    assert state["tenkan"] > 0 and state["kijun"] > 0
    assert state["cloud_top"] >= state["cloud_bottom"]
    assert atr(candles, 200) > 0
    assert adx(candles, 200) is not None
    assert last_closed_index(candles, candles[100][0] + 300, 300) == 100
    print("SELF-TEST PASSED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC_USDT")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        if args.days < 1:
            parser.error("--days must be at least 1")
        if args.balance <= 0:
            parser.error("--balance must be positive")
        backtest(args.symbol, args.days, args.balance)
