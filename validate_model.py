# -*- coding: utf-8 -*-
from pathlib import Path
import json, pandas as pd
R=Path("results")
def read(n):
 p=R/n
 if not p.exists(): return pd.DataFrame()
 for e in ("utf-8-sig","utf-8","cp932"):
  try:return pd.read_csv(p,encoding=e)
  except:pass
 return pd.DataFrame()
def main():
 t=read("virtual_trade_log.csv"); n=len(t)
 level="DATA_INSUFFICIENT" if n<30 else ("PRELIMINARY" if n<100 else "VALIDATION_READY")
 msg="30件未満のためスコア有効性は評価しません。" if n<30 else ("予備的傾向のみ。配点はまだ変更しません。" if n<100 else "比較検証を開始できる母数です。")
 groups=[]
 if not t.empty and "signal_rank" in t.columns:
  for rank,g in t.groupby("signal_rank"):
   hit=g["hit_10"].astype(str).str.lower().eq("true").mean() if "hit_10" in g else float("nan")
   gain=pd.to_numeric(g["max_gain_pct"],errors="coerce").mean() if "max_gain_pct" in g else float("nan")
   groups.append({"rank":str(rank),"n":len(g),"hit10_rate":None if pd.isna(hit) else round(float(hit),4),"avg_max_gain_pct":None if pd.isna(gain) else round(float(gain),2)})
 out={"trade_count":n,"evidence_level":level,"message":msg,"rank_groups":groups,"policy":"現行条件・ランク閾値・地合い配点は仮説として維持。十分な母数まで自動最適化しない。"}
 (R/"model_validation.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__":main()
