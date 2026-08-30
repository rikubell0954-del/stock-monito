# -*- coding: utf-8 -*-
"""
Persistent Step3 episode tracker.

Creates/updates results/step3_history.csv from results/ranked_candidates.csv.
One row = one Step3 episode. A ticker that leaves Step3 and later re-enters
gets a new episode_id, so re-entry can be distinguished from continuous Step3.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import pandas as pd

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
RANKED = RESULTS / "ranked_candidates.csv"
HISTORY = RESULTS / "step3_history.csv"
TOKYO = ZoneInfo("Asia/Tokyo")

COLUMNS = [
    "episode_id","code","name","first_step3_date","last_step3_date",
    "entry_price","latest_price","entry_score","latest_score",
    "entry_rank","latest_rank","days_seen","active","exit_date",
    "reentry_no","max_score","min_price","max_price","last_status"
]

def now_day() -> str:
    return datetime.now(TOKYO).strftime("%Y-%m-%d")

def norm_code(v) -> str:
    s = str(v).strip().replace(".T","")
    s = re.sub(r"\.0$","",s)
    m = re.search(r"\d{4}", s)
    return m.group(0) if m else s

def read_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8-sig","cp932","utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception:
            pass
    raise RuntimeError(f"Could not read {path}")

def empty_history():
    return pd.DataFrame(columns=COLUMNS)

def main():
    if not RANKED.exists():
        raise FileNotFoundError(f"{RANKED} not found")

    ranked = read_csv(RANKED)
    required = {"銘柄コード","銘柄名","株価","判定","急騰期待スコア","優先ランク"}
    missing = required - set(ranked.columns)
    if missing:
        raise ValueError("ranked_candidates missing: " + ", ".join(sorted(missing)))

    if HISTORY.exists() and HISTORY.stat().st_size:
        hist = read_csv(HISTORY)
        for c in COLUMNS:
            if c not in hist.columns:
                hist[c] = ""
        hist = hist[COLUMNS].copy()
    else:
        hist = empty_history()

    today = now_day()
    current = ranked[ranked["判定"].astype(str).str.contains("Step3", na=False)].copy()
    current["code_norm"] = current["銘柄コード"].map(norm_code)
    current_codes = set(current["code_norm"])

    # Close active episodes that are no longer Step3.
    if not hist.empty:
        active_mask = hist["active"].astype(str).str.lower().isin(["true","1","yes"])
        for idx in hist[active_mask].index:
            code = norm_code(hist.at[idx, "code"])
            if code not in current_codes:
                hist.at[idx, "active"] = False
                hist.at[idx, "exit_date"] = today
                hist.at[idx, "last_status"] = "EXITED_STEP3"

    # Update existing active episodes or create new/re-entry episodes.
    for _, row in current.iterrows():
        code = row["code_norm"]
        name = str(row.get("銘柄名",""))
        price = float(row.get("株価",0) or 0)
        score = int(float(row.get("急騰期待スコア",0) or 0))
        rank = str(row.get("優先ランク",""))

        active_idx = []
        if not hist.empty:
            mask_code = hist["code"].astype(str).map(norm_code).eq(code)
            mask_active = hist["active"].astype(str).str.lower().isin(["true","1","yes"])
            active_idx = hist[mask_code & mask_active].index.tolist()

        if active_idx:
            idx = active_idx[-1]
            hist.at[idx, "last_step3_date"] = today
            hist.at[idx, "latest_price"] = price
            hist.at[idx, "latest_score"] = score
            hist.at[idx, "latest_rank"] = rank
            seen = pd.to_numeric(pd.Series([hist.at[idx,"days_seen"]]), errors="coerce").iloc[0]
            hist.at[idx, "days_seen"] = int(seen if pd.notna(seen) else 0) + 1
            max_score = pd.to_numeric(pd.Series([hist.at[idx,"max_score"]]), errors="coerce").iloc[0]
            hist.at[idx, "max_score"] = max(score, int(max_score) if pd.notna(max_score) else score)
            min_price = pd.to_numeric(pd.Series([hist.at[idx,"min_price"]]), errors="coerce").iloc[0]
            max_price = pd.to_numeric(pd.Series([hist.at[idx,"max_price"]]), errors="coerce").iloc[0]
            hist.at[idx, "min_price"] = min(price, float(min_price) if pd.notna(min_price) else price)
            hist.at[idx, "max_price"] = max(price, float(max_price) if pd.notna(max_price) else price)
            hist.at[idx, "last_status"] = "CONTINUING"
        else:
            prior_count = 0
            if not hist.empty:
                prior_count = int(hist["code"].astype(str).map(norm_code).eq(code).sum())
            reentry_no = max(0, prior_count)
            episode_id = f"{code}_{today.replace('-','')}_{reentry_no}"
            new = {
                "episode_id": episode_id,
                "code": code,
                "name": name,
                "first_step3_date": today,
                "last_step3_date": today,
                "entry_price": price,
                "latest_price": price,
                "entry_score": score,
                "latest_score": score,
                "entry_rank": rank,
                "latest_rank": rank,
                "days_seen": 1,
                "active": True,
                "exit_date": "",
                "reentry_no": reentry_no,
                "max_score": score,
                "min_price": price,
                "max_price": price,
                "last_status": "REENTRY" if reentry_no > 0 else "NEW",
            }
            hist = pd.concat([hist, pd.DataFrame([new])], ignore_index=True)

    hist = hist[COLUMNS].copy()
    hist.to_csv(HISTORY, index=False, encoding="utf-8-sig")

    active = hist[hist["active"].astype(str).str.lower().isin(["true","1","yes"])]
    new_today = active[active["first_step3_date"].astype(str).eq(today)]
    reentry_today = new_today[pd.to_numeric(new_today["reentry_no"], errors="coerce").fillna(0).gt(0)]
    print(f"Step3 history saved: {HISTORY}")
    print(f"active={len(active)} new_today={len(new_today)} reentry_today={len(reentry_today)} total_episodes={len(hist)}")

if __name__ == "__main__":
    main()
