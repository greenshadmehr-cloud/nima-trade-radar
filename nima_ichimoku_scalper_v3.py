"""NIMA ICHIMOKU SCALPER v3 — MEXC USDT perpetual research/backtest only.
No order placement. OI is recorded as forward snapshots because the public
MEXC ticker exposes current holdVol; historical OI candles are not assumed.

Usage:
  pip install requests
  python nima_ichimoku_scalper_v3.py --symbol BTC_USDT --days 30
  python nima_ichimoku_scalper_v3.py --record-oi BTC_USDT --interval 60
"""
from __future__ import annotations
import argparse, csv, json, math, os, time
from datetime import datetime, timezone
from typing import Optional
import requests

BASE = "https://contract.mexc.com"
TIMEOUT = 20
FEE_SIDE = 0.0005       # 0.05% per side; adjust to actual account tier
SLIPPAGE_SIDE = 0.0002  # 0.02% per side
RISK_FRACTION = 0.005   # 0.5% equity risk per trade
MAX_LEVERAGE = 3.0
RR = 1.5
ICHIMOKU = (9, 26, 52)
INTERVALS = {"5m": ("Min5", 300), "30m": ("Min30", 1800), "1h": ("Min60", 3600)}


def api(path: str, params: dict | None = None) -> dict:
    r = requests.get(BASE + path, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"MEXC API error: {data}")
    return data


def fetch_klines(symbol: str, tf: str, start: int, end: int) -> list[list[float]]:
    if tf not in INTERVALS: raise ValueError(f"Unsupported timeframe: {tf}")
    api_tf, sec = INTERVALS[tf]
    rows: dict[int, list[float]] = {}
    cursor, now = start, int(time.time())
    # Keep each request comfortably below the documented 2,000-row maximum.
    chunk = sec * 450
    while cursor < min(end, now):
        stop = min(cursor + chunk, end, now)
        payload = api(f"/api/v1/contract/kline/{symbol}", {"interval": api_tf, "start": cursor, "end": stop})
        d = payload.get("data") or {}
        for i, ts0 in enumerate(d.get("time") or []):
            ts = int(ts0)
            if ts < start or ts + sec > end or ts + sec > now: continue
            rows[ts] = [ts, float(d["open"][i]), float(d["high"][i]), float(d["low"][i]), float(d["close"][i]), float(d["vol"][i])]
        cursor = stop
        time.sleep(0.08)
    return [rows[k] for k in sorted(rows)]


def midpoint(c: list[list[float]], i: int, n: int) -> Optional[float]:
    if i - n + 1 < 0: return None
    w = c[i-n+1:i+1]
    return (max(x[2] for x in w) + min(x[3] for x in w)) / 2.0


def ichi(c: list[list[float]], i: int) -> Optional[dict]:
    if i < 77: return None
    ten, kij = midpoint(c, i, 9), midpoint(c, i, 26)
    src = i - 26
    a_old = (midpoint(c, src, 9) + midpoint(c, src, 26)) / 2
    b_old = midpoint(c, src, 52)
    a_now = (ten + kij) / 2
    b_now = midpoint(c, i, 52)
    top, bot = max(a_old, b_old), min(a_old, b_old)
    close = c[i][4]
    # Chikou confirmation is expressed without plotting: current close vs close 26 bars ago.
    chikou_up = close > c[i-26][4]
    chikou_dn = close < c[i-26][4]
    bull = close > top and ten > kij and a_now > b_now and chikou_up
    bear = close < bot and ten < kij and a_now < b_now and chikou_dn
    return {"tenkan": ten, "kijun": kij, "cloud_top": top, "cloud_bottom": bot,
            "bull": bull, "bear": bear}


def atr(c: list[list[float]], i: int, n: int = 14) -> Optional[float]:
    if i < n: return None
    vals=[]
    for j in range(i-n+1, i+1):
        prev=c[j-1][4]
        vals.append(max(c[j][2]-c[j][3], abs(c[j][2]-prev), abs(c[j][3]-prev)))
    return sum(vals)/n

def setup_30m(c30: list[list[float]], i: int, direction: str) -> bool:
    """Recent pullback to Tenkan/Kijun, then recovery; only closed bars used."""
    if i < 80: return False
    for j in range(max(77, i-2), i+1):
        x=ichi(c30,j)
        if not x: continue
        bar=c30[j]
        if direction == "LONG" and x["bull"] and bar[3] <= max(x["tenkan"],x["kijun"]) and bar[4] > x["kijun"]:
            return True
        if direction == "SHORT" and x["bear"] and bar[2] >= min(x["tenkan"],x["kijun"]) and bar[4] < x["kijun"]:
            return True
    return False


def last_closed_index(c: list[list[float]], ts: int, seconds: int) -> int:
    # The API timestamps candles by their open time. Never use a candle before it closes.
    lo, hi, ans = 0, len(c)-1, -1
    while lo <= hi:
        m=(lo+hi)//2
        if c[m][0]+seconds <= ts: ans=m; lo=m+1
        else: hi=m-1
    return ans


def make_signals(c5, c30, c60):
    signals=[]
    for i, bar in enumerate(c5):
        ts=bar[0]+300
        i30=last_closed_index(c30,ts,1800)
        i60=last_closed_index(c60,ts,3600)
        if i30 < 80 or i60 < 80 or i < 80: continue
        h=ichi(c60,i60); m=ichi(c30,i30); x=ichi(c5,i)
        if not h or not m or not x: continue
        direction=None
        if h["bull"] and m["bull"] and setup_30m(c30,i30,"LONG") and x["bull"]:
            # Confirm on close beyond the previous three completed 5m highs.
            if bar[4] > max(c5[k][2] for k in range(i-3,i)):
                direction="LONG"
        elif h["bear"] and m["bear"] and setup_30m(c30,i30,"SHORT") and x["bear"]:
            if bar[4] < min(c5[k][3] for k in range(i-3,i)):
                direction="SHORT"
        if direction:
            a=atr(c5,i)
            if not a or a <= 0: continue
            swing_low=min(c5[k][3] for k in range(i-5,i+1))
            swing_high=max(c5[k][2] for k in range(i-5,i+1))
            close=bar[4]
            if direction=="LONG":
                stop=min(swing_low, close-1.5*a)
                if stop >= close: continue
            else:
                stop=max(swing_high, close+1.5*a)
                if stop <= close: continue
            signals.append({"index":i,"time":bar[0],"direction":direction,"stop_distance":abs(close-stop)})
    return signals


def backtest(symbol: str, days: int, balance0: float):
    end=int(time.time()); start=end-days*86400
    # Fetch from the earliest required timeframe; each is aligned to exchange candle boundaries.
    c5=fetch_klines(symbol,"5m",start-400*300,end)
    c30=fetch_klines(symbol,"30m",start-100*1800,end)
    c60=fetch_klines(symbol,"1h",start-100*3600,end)
    if min(len(c5),len(c30),len(c60)) < 100:
        raise RuntimeError(f"Insufficient history: 5m={len(c5)}, 30m={len(c30)}, 1h={len(c60)}")
    sigs={s["index"]:s for s in make_signals(c5,c30,c60)}
    balance=balance0; peak=balance; maxdd=0.0; trades=[]; pos=None
    start_cut=start
    for i in range(80,len(c5)-1):
        bar=c5[i]
        if bar[0] < start_cut: continue
        if pos:
            side=pos["side"]; stop=pos["stop"]; target=pos["target"]
            hit_stop = bar[3] <= stop if side=="LONG" else bar[2] >= stop
            hit_target = bar[2] >= target if side=="LONG" else bar[3] <= target
            exit_price=None; reason=None
            if hit_stop: exit_price=stop; reason="STOP"  # conservative if both hit in same candle
            elif hit_target: exit_price=target; reason="TARGET"
            if exit_price is not None:
                exit_fill=exit_price*(1-SLIPPAGE_SIDE if side=="LONG" else 1+SLIPPAGE_SIDE)
                gross=(exit_fill-pos["entry_fill"])*pos["qty"]*(1 if side=="LONG" else -1)
                exit_fee=abs(exit_fill*pos["qty"])*FEE_SIDE
                net=gross-exit_fee-pos["entry_fee"]
                balance += gross-exit_fee  # entry fee was deducted when the position opened
                trades.append({"side":side,"entry_time":pos["entry_time"],"exit_time":bar[0],"entry":pos["entry_fill"],"exit":exit_fill,"qty":pos["qty"],"net_pnl":net,"r_multiple":net/max(pos["risk_cash"],1e-12),"reason":reason})
                pos=None
                peak=max(peak,balance); maxdd=max(maxdd,(peak-balance)/peak if peak else 0)
                continue
        if pos is None and i in sigs:
            s=sigs[i]; side=s["direction"]; raw_entry=c5[i+1][1]
            entry_fill=raw_entry*(1+SLIPPAGE_SIDE if side=="LONG" else 1-SLIPPAGE_SIDE)
            distance=s["stop_distance"]
            stop=entry_fill-distance if side=="LONG" else entry_fill+distance
            risk_cash=balance*RISK_FRACTION
            qty=min(risk_cash/distance, balance*MAX_LEVERAGE/entry_fill)
            if qty <= 0: continue
            entry_fee=entry_fill*qty*FEE_SIDE
            balance -= entry_fee
            risk_cash += entry_fee
            target=entry_fill+distance*RR if side=="LONG" else entry_fill-distance*RR
            pos={"side":side,"entry_fill":entry_fill,"stop":stop,"target":target,"qty":qty,"risk_cash":risk_cash,"entry_fee":entry_fee,"entry_time":c5[i+1][0]}
        # Mark-to-market drawdown on every completed 5m candle, not only closed trades.
        if pos and c5[i][0] >= pos["entry_time"]:
            mark=c5[i][4]
            unreal=(mark-pos["entry_fill"])*pos["qty"]*(1 if pos["side"]=="LONG" else -1)
            equity=balance+unreal
            peak=max(peak,equity)
            if peak>0: maxdd=max(maxdd,(peak-equity)/peak)
        elif pos is None:
            peak=max(peak,balance)
            if peak>0: maxdd=max(maxdd,(peak-balance)/peak)
    # Close remaining open position at last close for a marked-to-market report, not a completed trade.
    if pos:
        last=c5[-1][4]; fill=last*(1-SLIPPAGE_SIDE if pos["side"]=="LONG" else 1+SLIPPAGE_SIDE)
        gross=(fill-pos["entry_fill"])*pos["qty"]*(1 if pos["side"]=="LONG" else -1)
        balance += gross-abs(fill*pos["qty"])*FEE_SIDE
    wins=sum(t["net_pnl"]>0 for t in trades); losses=len(trades)-wins
    gross_win=sum(t["net_pnl"] for t in trades if t["net_pnl"]>0)
    gross_loss=-sum(t["net_pnl"] for t in trades if t["net_pnl"]<0)
    pf=gross_win/gross_loss if gross_loss else (math.inf if gross_win else 0)
    print(f"\nNIMA ICHIMOKU SCALPER v3 | {symbol} | {days} days")
    print(f"Candles: 5m={len(c5)} 30m={len(c30)} 1h={len(c60)} | Signals/trades={len(trades)}")
    print(f"Start=${balance0:.2f} End=${balance:.2f} Net={(balance/balance0-1)*100:.2f}%")
    print(f"Wins={wins} Losses={losses} WinRate={(wins/len(trades)*100 if trades else 0):.1f}% PF={pf:.3f} MaxDD={maxdd*100:.2f}%")
    print("OI: NOT USED in historical backtest (historical OI series not available in this data path).")
    out=f"trades_{symbol}_{days}d.csv"
    if trades:
        with open(out,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=list(trades[0])); w.writeheader(); w.writerows(trades)
        print(f"Trade log: {out}")
    return trades


def record_oi(symbol: str):
    """Append current public holdVol snapshot. Run repeatedly to build your own OI history."""
    data=api("/api/v1/contract/ticker",{"symbol":symbol}).get("data")
    if isinstance(data,list): data=data[0] if data else None
    if not data or "holdVol" not in data: raise RuntimeError("MEXC ticker did not return holdVol")
    row={"timestamp_ms":int(data.get("timestamp",int(time.time()*1000))),"symbol":symbol,
         "lastPrice":float(data["lastPrice"]),"holdVol":float(data["holdVol"]),"fundingRate":float(data.get("fundingRate",0))}
    path=f"oi_{symbol}.csv"; exists=os.path.exists(path)
    with open(path,"a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(row));
        if not exists: w.writeheader()
        w.writerow(row)
    print(json.dumps(row,ensure_ascii=False))


def self_test():
    c=[]; p=100.0
    for i in range(300):
        o=p; p += 0.1 + (0.03 if i%7 else -0.02)
        c.append([i*300,o,p+0.2,o-0.2,p,100.0])
    x=ichi(c,200)
    assert x and x["tenkan"]>0 and x["kijun"]>0
    assert x["cloud_top"]>=x["cloud_bottom"]
    assert atr(c,200)>0
    print("SELF-TEST PASSED")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--symbol",default="BTC_USDT")
    p.add_argument("--days",type=int,default=30)
    p.add_argument("--balance",type=float,default=1000.0)
    p.add_argument("--record-oi",metavar="SYMBOL")
    p.add_argument("--interval",type=int,default=60,help="OI snapshot interval in seconds; use an external scheduler")
    p.add_argument("--self-test",action="store_true")
    a=p.parse_args()
    if a.self_test: self_test()
    elif a.record_oi: record_oi(a.record_oi)
    else: backtest(a.symbol,a.days,a.balance)
