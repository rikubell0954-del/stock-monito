# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
OUT = RESULTS / "market_regime.csv"
HISTORY = RESULTS / "market_regime_history.csv"
TOKYO = ZoneInfo("Asia/Tokyo")
TICKERS={"nikkei":"^N225","nasdaq":"^IXIC","sp500":"^GSPC","sox":"^SOX","vix":"^VIX"}

def f(v):
    try:
        if hasattr(v,"iloc"): v=v.iloc[0]
        return float(v)
    except Exception: return None

def fetch(symbol):
    df=yf.download(symbol,period="3mo",interval="1d",progress=False,auto_adjust=True,threads=False)
    if df is None or df.empty: return None
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    c=df["Close"].dropna()
    if len(c)<26: return None
    latest,prev,ma25=f(c.iloc[-1]),f(c.iloc[-2]),f(c.rolling(25).mean().iloc[-1])
    if latest is None or prev in (None,0) or ma25 in (None,0): return None
    return {"close":round(latest,2),"change_pct":round((latest/prev-1)*100,2),
            "ma25":round(ma25,2),"above_ma25":latest>=ma25,
            "data_date":pd.Timestamp(c.index[-1]).strftime("%Y-%m-%d")}

def classify(d):
    score=0; reasons=[]
    tests=[
      (d["nikkei"]["change_pct"]>0,15,"日経上昇"),
      (d["nikkei"]["above_ma25"],10,"日経25日線上"),
      (d["nasdaq"]["change_pct"]>0,15,"NASDAQ上昇"),
      (d["nasdaq"]["above_ma25"],10,"NASDAQ25日線上"),
      (d["sp500"]["change_pct"]>0,10,"S&P500上昇"),
      (d["sox"]["change_pct"]>0,15,"SOX上昇"),
      (d["vix"]["change_pct"]<0,15,"VIX低下"),
      (d["vix"]["close"]<20,10,"VIX20未満")]
    for ok,pts,name in tests:
        if ok: score+=pts; reasons.append(name)
    if score>=75: label,emoji,env="RISK ON","🟢","良好"
    elif score>=55: label,emoji,env="やや強気","🟡","やや良好"
    elif score>=40: label,emoji,env="NEUTRAL","⚪","中立"
    elif score>=20: label,emoji,env="やや弱気","🟠","慎重"
    else: label,emoji,env="RISK OFF","🔴","見送り寄り"
    return score,label,emoji,env," / ".join(reasons)

def read_csv(path):
    for enc in ("utf-8-sig","cp932","utf-8"):
        try: return pd.read_csv(path,encoding=enc)
        except Exception: pass
    return pd.DataFrame()

def main():
    data={}; errors=[]
    for key,sym in TICKERS.items():
        try: item=fetch(sym)
        except Exception as e: item=None; errors.append(f"{sym}:{e}")
        if item is None: errors.append(f"{sym}:no_data")
        else: data[key]=item
    now=datetime.now(TOKYO)
    row={"run_date":now.strftime("%Y-%m-%d"),"run_time_jst":now.strftime("%Y-%m-%d %H:%M:%S")}
    if set(data)!=set(TICKERS):
        row.update(status="INCOMPLETE",score="",label="判定不能",emoji="⚠️",entry_environment="データ不足",reasons="",errors=" | ".join(errors))
    else:
        score,label,emoji,env,reasons=classify(data)
        row.update(status="OK",score=score,label=label,emoji=emoji,entry_environment=env,reasons=reasons,errors="")
        for key,item in data.items():
            for k,v in item.items(): row[f"{key}_{k}"]=v
    latest=pd.DataFrame([row]); latest.to_csv(OUT,index=False,encoding="utf-8-sig")
    hist=read_csv(HISTORY) if HISTORY.exists() else pd.DataFrame()
    if not hist.empty and "run_date" in hist.columns:
        hist=hist[hist["run_date"].astype(str)!=row["run_date"]]
    pd.concat([hist,latest],ignore_index=True,sort=False).to_csv(HISTORY,index=False,encoding="utf-8-sig")
    print(f"Market regime: {row.get('emoji')} {row.get('label')} score={row.get('score')}")
if __name__=="__main__": main()
