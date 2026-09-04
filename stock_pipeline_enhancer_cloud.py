# -*- coding: utf-8 -*-
"""
Stock Monitor Enhancer v2

Purpose
-------
Read step2_3_result.csv produced by step2_3_monitor.py and create:
  - ranked_candidates.csv
  - virtual_trade_log.csv
  - performance_summary.csv
  - enhancer_diagnostics.txt

Design principles
-----------------
1) The 17 columns currently emitted by step2_3_monitor.py are the formal input schema.
2) Checkmark columns are evaluated directly as booleans. They are NOT treated as numeric MAs.
3) Virtual trades are created for every Step3 signal, independent of ranking score.
4) New trade entry uses the signal-day close already present in step2_3_result.csv.
5) Performance tracking starts on the NEXT trading day because the entry is the signal-day close.
6) Re-running on the same day does not duplicate a signal. An OPEN trade also blocks duplicate daily signals.
7) yfinance failure never prevents ranking or new-signal logging; it only postpones price-history updates.
"""
from __future__ import annotations

import os
import sys
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Tuple

import pandas as pd

try:
    import yfinance as yf
except Exception:
    yf = None

VERSION = "2.1.0-cloud"

CONFIG = {
    "input_csv": "step2_3_result.csv",
    "ranked_csv": "ranked_candidates.csv",
    "trade_log_csv": "virtual_trade_log.csv",
    "summary_csv": "performance_summary.csv",
    "diagnostics_file": "enhancer_diagnostics.txt",
    "target_1_pct": 0.10,
    "target_2_pct": 0.20,
    "target_3_pct": 0.30,
    "fallback_stop_pct": 0.08,
    "max_holding_days": 20,
    "download_days": 120,
    "rank_s": 85,
    "rank_a": 78,
    "rank_b": 68,
    "rank_c": 55,
}

# Exact schema emitted by the supplied step2_3_monitor.py.
REQUIRED_COLUMNS = [
    "銘柄コード", "銘柄名", "株価", "判定", "出来高比率",
    "出来高枯れ", "出来高再増加", "小実体", "じわ切り上げ", "日足代表価格上",
    "MA5上", "MA25上", "MA75上", "MA200上", "全MA上",
    "直近高値からの下落(%)", "損切り目安下幅(%)",
]

BOOL_COLUMNS = [
    "出来高枯れ", "出来高再増加", "小実体", "じわ切り上げ", "日足代表価格上",
    "MA5上", "MA25上", "MA75上", "MA200上", "全MA上",
]

TRADE_COLUMNS = [
    "trade_id", "code", "name", "signal_date", "entry_date", "entry_price",
    "signal_score", "signal_rank", "stop_price", "target10", "target20", "target30",
    "status", "days_observed", "max_high", "min_low", "max_gain_pct", "max_drawdown_pct",
    "hit_10", "hit_20", "hit_30", "hit_stop", "exit_date", "exit_price", "exit_reason",
    "last_update",
]



TOKYO_TZ = ZoneInfo("Asia/Tokyo")

def tokyo_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=TOKYO_TZ).tz_localize(None)

def tokyo_today() -> pd.Timestamp:
    return tokyo_now().normalize()

def read_csv_flexible(path: str) -> pd.DataFrame:
    errors = []
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as exc:
            errors.append(f"{enc}: {exc}")
    raise RuntimeError("CSV read failed:\n" + "\n".join(errors))


def normalize_code(value) -> str:
    s = str(value).strip().replace(".T", "")
    s = re.sub(r"\.0$", "", s)
    m = re.search(r"\d{4}", s)
    return m.group(0) if m else s


def to_num(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_true(value) -> bool:
    s = str(value).strip().lower()
    return s in {"✓", "○", "◯", "true", "1", "yes", "y", "on"}


def is_false(value) -> bool:
    s = str(value).strip().lower()
    return s in {"✗", "×", "false", "0", "no", "n", "off"}


def stage_name(value) -> str:
    s = str(value).lower().replace(" ", "")
    if "step3" in s:
        return "Step3"
    if "step2完了" in s:
        return "Step2完了"
    if "step2中" in s or "step2" in s:
        return "Step2中"
    return "監視"


def validate_input(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append("Missing required columns: " + ", ".join(missing))
        return errors

    if df.empty:
        errors.append("Input CSV has zero data rows.")
        return errors

    codes = df["銘柄コード"].map(normalize_code)
    if codes.eq("").any():
        errors.append("Blank ticker code exists.")
    if codes.duplicated().any():
        dupes = codes[codes.duplicated(keep=False)].unique().tolist()[:10]
        errors.append("Duplicate ticker codes: " + ", ".join(dupes))

    for col in BOOL_COLUMNS:
        bad = df[col].dropna().map(lambda x: not (is_true(x) or is_false(x)))
        if bad.any():
            samples = df.loc[bad[bad].index, col].astype(str).unique().tolist()[:5]
            errors.append(f"Unexpected boolean values in {col}: {samples}")

    return errors


def score_row(row: pd.Series) -> Tuple[int, str, str, str]:
    """Score using the exact boolean/numeric fields emitted by step2_3_monitor.py."""
    score = 0
    parts: list[str] = []
    risks: list[str] = []

    stage = stage_name(row["判定"])

    # A. Stage: max 32
    stage_points = {"Step3": 32, "Step2完了": 24, "Step2中": 14, "監視": 4}[stage]
    score += stage_points
    parts.append(f"stage={stage_points}")

    # B. Price position / MAs: max 27
    pos = 0
    if is_true(row["日足代表価格上"]):
        pos += 8
    if is_true(row["MA5上"]):
        pos += 4
    if is_true(row["MA25上"]):
        pos += 4
    if is_true(row["MA75上"]):
        pos += 5
    if is_true(row["MA200上"]):
        pos += 3
    if is_true(row["全MA上"]):
        pos += 3
    score += pos
    parts.append(f"position={pos}")

    # C. Volume behavior: max 18
    vol = 0
    vr = to_num(row["出来高比率"]) or 0.0
    if is_true(row["出来高枯れ"]):
        vol += 8
    if is_true(row["出来高再増加"]):
        vol += 6
        if vr >= 4.0:
            vol += 4
        elif vr >= 2.5:
            vol += 3
        elif vr >= 1.5:
            vol += 2
    elif vr >= 1.5:
        # Useful observation, but not enough to call resurgence.
        vol += 2
    score += vol
    parts.append(f"volume={vol}")

    # D. Consolidation quality: max 11
    shape = 0
    if is_true(row["小実体"]):
        shape += 6
    if is_true(row["じわ切り上げ"]):
        shape += 5
    score += shape
    parts.append(f"shape={shape}")

    # E. Entry location / risk quality: max 12, penalties possible
    risk_points = 0
    dd = to_num(row["直近高値からの下落(%)"])
    stop_width = to_num(row["損切り目安下幅(%)"])

    if dd is not None:
        # Near the recent high but not extremely extended is preferred.
        if -8.0 <= dd <= -1.0:
            risk_points += 5
        elif -15.0 <= dd < -8.0:
            risk_points += 3
        elif -1.0 < dd <= 1.0:
            risk_points += 3
        elif dd < -20.0:
            risk_points -= 3
            risks.append("recent-high drawdown >20%")

    if stop_width is not None:
        if 0 < stop_width <= 5.0:
            risk_points += 5
        elif stop_width <= 8.0:
            risk_points += 4
        elif stop_width <= 12.0:
            risk_points += 2
        elif stop_width > 15.0:
            risk_points -= 4
            risks.append("stop width >15%")
    score += risk_points
    parts.append(f"risk={risk_points}")

    # F. Consistency penalties. These catch unusual rows instead of silently scoring them highly.
    if stage == "Step3" and not is_true(row["出来高再増加"]):
        score -= 15
        risks.append("Step3 without volume resurgence")
    if stage == "Step3" and not is_true(row["日足代表価格上"]):
        score -= 12
        risks.append("Step3 below representative price")
    if stage == "Step3" and not (is_true(row["MA5上"]) and is_true(row["MA25上"]) and is_true(row["MA75上"])):
        score -= 15
        risks.append("Step3 MA condition mismatch")

    score = max(0, min(100, int(round(score))))

    if score >= CONFIG["rank_s"]:
        rank = "S"
        priority = "★★★ 最優先"
    elif score >= CONFIG["rank_a"]:
        rank = "A"
        priority = "★★ 強監視"
    elif score >= CONFIG["rank_b"]:
        rank = "B"
        priority = "★ 監視"
    elif score >= CONFIG["rank_c"]:
        rank = "C"
        priority = "継続監視"
    else:
        rank = "D"
        priority = "低優先"

    reason = "; ".join(parts)
    if risks:
        reason += " | risk: " + "; ".join(risks)
    return score, rank, reason, priority


def build_ranking(df: pd.DataFrame) -> pd.DataFrame:
    errors = validate_input(df)
    if errors:
        raise ValueError("Input validation failed:\n- " + "\n- ".join(errors))

    out = df.copy()
    scored = out.apply(score_row, axis=1)
    out["急騰期待スコア"] = [x[0] for x in scored]
    out["優先ランク"] = [x[1] for x in scored]
    out["スコア根拠"] = [x[2] for x in scored]
    out["監視優先度"] = [x[3] for x in scored]

    # Deterministic sort: score desc, then stage priority, then code asc.
    stage_order = {"Step3": 0, "Step2完了": 1, "Step2中": 2, "監視": 3}
    out["_stage_order"] = out["判定"].map(lambda x: stage_order[stage_name(x)])
    out["_code_sort"] = out["銘柄コード"].map(normalize_code)
    out = out.sort_values(
        ["急騰期待スコア", "_stage_order", "_code_sort"],
        ascending=[False, True, True],
        kind="stable",
    ).drop(columns=["_stage_order", "_code_sort"]).reset_index(drop=True)
    return out


def init_trade_log() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def load_trade_log(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return init_trade_log()
    try:
        df = read_csv_flexible(path)
    except Exception:
        return init_trade_log()
    for col in TRADE_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[TRADE_COLUMNS]


def calc_stop_from_signal(row: pd.Series, entry: float) -> float:
    width = to_num(row.get("損切り目安下幅(%)"))
    if width is not None and 0 < width < 50:
        return entry * (1.0 - width / 100.0)
    return entry * (1.0 - CONFIG["fallback_stop_pct"])


def add_new_virtual_trades(ranked: pd.DataFrame, log: pd.DataFrame, signal_date: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Register ALL Step3 signals. Ranking score does not gate validation trades."""
    today = (signal_date or tokyo_today()).normalize()
    day = today.strftime("%Y-%m-%d")

    existing_same_day = set()
    open_codes = set()
    if not log.empty:
        for _, r in log.iterrows():
            code = normalize_code(r.get("code", ""))
            sig = str(r.get("signal_date", ""))[:10]
            existing_same_day.add((code, sig))
            if str(r.get("status", "")).upper() == "OPEN":
                open_codes.add(code)

    new_rows = []
    for _, row in ranked.iterrows():
        if stage_name(row["判定"]) != "Step3":
            continue

        code = normalize_code(row["銘柄コード"])
        if (code, day) in existing_same_day or code in open_codes:
            continue

        entry = to_num(row["株価"])
        if entry is None or entry <= 0:
            continue

        stop = calc_stop_from_signal(row, entry)
        score = int(to_num(row["急騰期待スコア"]) or 0)
        rank = str(row["優先ランク"])
        name = str(row["銘柄名"])

        new_rows.append({
            "trade_id": f"{code}_{today.strftime('%Y%m%d')}",
            "code": code,
            "name": name,
            "signal_date": day,
            "entry_date": day,
            "entry_price": round(entry, 2),
            "signal_score": score,
            "signal_rank": rank,
            "stop_price": round(stop, 2),
            "target10": round(entry * (1 + CONFIG["target_1_pct"]), 2),
            "target20": round(entry * (1 + CONFIG["target_2_pct"]), 2),
            "target30": round(entry * (1 + CONFIG["target_3_pct"]), 2),
            "status": "OPEN",
            "days_observed": 0,
            "max_high": entry,
            "min_low": entry,
            "max_gain_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "hit_10": False,
            "hit_20": False,
            "hit_30": False,
            "hit_stop": False,
            "exit_date": "",
            "exit_price": "",
            "exit_reason": "",
            "last_update": day,
        })
        existing_same_day.add((code, day))
        open_codes.add(code)

    if new_rows:
        log = pd.concat([log, pd.DataFrame(new_rows)], ignore_index=True)
    return log[TRADE_COLUMNS]


def download_history(code: str, signal_date: str) -> pd.DataFrame:
    if yf is None:
        return pd.DataFrame()
    start = pd.Timestamp(signal_date) - pd.Timedelta(days=3)
    end = tokyo_today().normalize() + pd.Timedelta(days=1)
    try:
        hist = yf.download(
            f"{normalize_code(code)}.T",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)
    hist = hist.reset_index()
    date_col = "Date" if "Date" in hist.columns else hist.columns[0]
    hist[date_col] = pd.to_datetime(hist[date_col]).dt.tz_localize(None)
    if date_col != "Date":
        hist = hist.rename(columns={date_col: "Date"})
    return hist


def update_one_trade(tr: pd.Series, hist: pd.DataFrame) -> dict:
    changes = {}
    if hist.empty:
        return changes

    entry = to_num(tr["entry_price"])
    stop = to_num(tr["stop_price"])
    t10 = to_num(tr["target10"])
    t20 = to_num(tr["target20"])
    t30 = to_num(tr["target30"])
    if entry is None or entry <= 0:
        return changes

    signal_ts = pd.Timestamp(str(tr["signal_date"])[:10])
    # Entry is signal-day close, so only subsequent trading days count.
    obs = hist[hist["Date"] > signal_ts].copy().sort_values("Date")
    if obs.empty:
        return changes
    obs = obs.head(CONFIG["max_holding_days"])

    max_high = entry
    min_low = entry
    hit10 = str(tr.get("hit_10", "")).lower() == "true"
    hit20 = str(tr.get("hit_20", "")).lower() == "true"
    hit30 = str(tr.get("hit_30", "")).lower() == "true"
    hitstop = str(tr.get("hit_stop", "")).lower() == "true"
    exit_date = ""
    exit_price = ""
    exit_reason = ""

    days = 0
    for _, day in obs.iterrows():
        high = to_num(day.get("High"))
        low = to_num(day.get("Low"))
        close = to_num(day.get("Close"))
        if high is None or low is None or close is None:
            continue
        days += 1
        max_high = max(max_high, high)
        min_low = min(min_low, low)

        day_hit10 = bool(t10 is not None and high >= t10)
        day_hit20 = bool(t20 is not None and high >= t20)
        day_hit30 = bool(t30 is not None and high >= t30)
        day_hitstop = bool(stop is not None and low <= stop)
        hit10 = hit10 or day_hit10
        hit20 = hit20 or day_hit20
        hit30 = hit30 or day_hit30
        hitstop = hitstop or day_hitstop

        # Conservative rule for daily bars: if stop and +30% both appear same day, STOP wins.
        if day_hitstop:
            exit_date = day["Date"].strftime("%Y-%m-%d")
            exit_price = round(stop, 2) if stop is not None else round(close, 2)
            exit_reason = "STOP"
            break
        if day_hit30:
            exit_date = day["Date"].strftime("%Y-%m-%d")
            exit_price = round(t30, 2) if t30 is not None else round(close, 2)
            exit_reason = "+30%"
            break

        if days >= CONFIG["max_holding_days"]:
            exit_date = day["Date"].strftime("%Y-%m-%d")
            exit_price = round(close, 2)
            exit_reason = f"{CONFIG['max_holding_days']}営業日経過"
            break

    changes.update({
        "days_observed": days,
        "max_high": round(max_high, 2),
        "min_low": round(min_low, 2),
        "max_gain_pct": round((max_high / entry - 1) * 100, 2),
        "max_drawdown_pct": round((min_low / entry - 1) * 100, 2),
        "hit_10": bool(hit10),
        "hit_20": bool(hit20),
        "hit_30": bool(hit30),
        "hit_stop": bool(hitstop),
        "last_update": tokyo_today().strftime("%Y-%m-%d"),
    })
    if exit_reason:
        changes.update({
            "status": "CLOSED",
            "exit_date": exit_date,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
        })
    return changes


def update_virtual_trades(log: pd.DataFrame) -> Tuple[pd.DataFrame, int, int]:
    if log.empty:
        return log, 0, 0
    attempted = 0
    updated = 0
    for i, tr in log.iterrows():
        if str(tr.get("status", "")).upper() != "OPEN":
            continue
        attempted += 1
        hist = download_history(normalize_code(tr["code"]), str(tr["signal_date"])[:10])
        if hist.empty:
            continue
        changes = update_one_trade(tr, hist)
        if changes:
            updated += 1
            for k, v in changes.items():
                log.at[i, k] = v
    return log[TRADE_COLUMNS], attempted, updated


def truthy_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["true", "1", "yes", "y"])


def make_summary(log: pd.DataFrame) -> pd.DataFrame:
    if log.empty:
        return pd.DataFrame([
            ["仮想取引数", 0], ["OPEN件数", 0], ["CLOSED件数", 0],
            ["+10%到達率", "-"], ["+20%到達率", "-"], ["+30%到達率", "-"],
            ["損切り到達率", "-"], ["平均最大上昇率", "-"], ["平均最大下落率", "-"],
        ], columns=["指標", "値"])

    n = len(log)
    hit10 = int(truthy_series(log["hit_10"]).sum())
    hit20 = int(truthy_series(log["hit_20"]).sum())
    hit30 = int(truthy_series(log["hit_30"]).sum())
    stop = int(truthy_series(log["hit_stop"]).sum())
    gains = pd.to_numeric(log["max_gain_pct"], errors="coerce")
    dds = pd.to_numeric(log["max_drawdown_pct"], errors="coerce")
    rate = lambda x: f"{x / n * 100:.1f}%" if n else "-"

    rows = [
        ["仮想取引数", n],
        ["OPEN件数", int((log["status"].astype(str).str.upper() == "OPEN").sum())],
        ["CLOSED件数", int((log["status"].astype(str).str.upper() == "CLOSED").sum())],
        ["+10%到達件数", hit10], ["+10%到達率", rate(hit10)],
        ["+20%到達件数", hit20], ["+20%到達率", rate(hit20)],
        ["+30%到達件数", hit30], ["+30%到達率", rate(hit30)],
        ["損切り到達件数", stop], ["損切り到達率", rate(stop)],
        ["平均最大上昇率", f"{gains.mean():.2f}%" if gains.notna().any() else "-"],
        ["平均最大下落率", f"{dds.mean():.2f}%" if dds.notna().any() else "-"],
    ]
    return pd.DataFrame(rows, columns=["指標", "値"])


def write_diagnostics(df: pd.DataFrame, ranked: pd.DataFrame, log: pd.DataFrame, update_attempted: int, update_success: int) -> str:
    rank_counts = ranked["優先ランク"].value_counts().reindex(["S", "A", "B", "C", "D"], fill_value=0)
    stage_counts = ranked["判定"].map(stage_name).value_counts().reindex(["Step3", "Step2完了", "Step2中", "監視"], fill_value=0)
    step3 = ranked[ranked["判定"].map(stage_name) == "Step3"]
    min_step3 = int(step3["急騰期待スコア"].min()) if not step3.empty else None
    max_step3 = int(step3["急騰期待スコア"].max()) if not step3.empty else None

    source_by_code = df.copy()
    source_by_code["_code"] = source_by_code["銘柄コード"].map(normalize_code)
    ranked_by_code = ranked.copy()
    ranked_by_code["_code"] = ranked_by_code["銘柄コード"].map(normalize_code)
    source_by_code = source_by_code.set_index("_code")[REQUIRED_COLUMNS].sort_index().astype(str)
    ranked_by_code = ranked_by_code.set_index("_code")[REQUIRED_COLUMNS].sort_index().astype(str)
    source_values_preserved = source_by_code.equals(ranked_by_code)

    current_day = tokyo_today().strftime("%Y-%m-%d")
    current_step3_codes = set(step3["銘柄コード"].map(normalize_code))
    logged_current_codes = set()
    if not log.empty:
        for _, tr in log.iterrows():
            if str(tr.get("signal_date", ""))[:10] == current_day or str(tr.get("status", "")).upper() == "OPEN":
                logged_current_codes.add(normalize_code(tr.get("code", "")))

    checks = {
        "row_count_preserved": len(df) == len(ranked),
        "source_values_preserved": source_values_preserved,
        "codes_unique": ranked["銘柄コード"].map(normalize_code).is_unique,
        "score_range_valid": ranked["急騰期待スコア"].between(0, 100).all(),
        "rank_values_valid": ranked["優先ランク"].isin(["S", "A", "B", "C", "D"]).all(),
        "not_all_D_when_step3_exists": not (len(step3) > 0 and (ranked["優先ランク"] == "D").all()),
        "every_current_step3_logged": current_step3_codes.issubset(logged_current_codes),
    }

    lines = [
        f"Stock Pipeline Enhancer v{VERSION}",
        f"run_time={tokyo_now().isoformat(timespec='seconds')}",
        f"script_path={os.path.abspath(__file__)}",
        f"input_path={os.path.abspath(CONFIG['input_csv'])}",
        f"input_rows={len(df)}",
        "",
        "[stage_counts]",
    ]
    lines += [f"{k}={int(v)}" for k, v in stage_counts.items()]
    lines += ["", "[rank_counts]"]
    lines += [f"{k}={int(v)}" for k, v in rank_counts.items()]
    lines += [
        "",
        f"step3_score_min={min_step3}",
        f"step3_score_max={max_step3}",
        f"trade_log_rows={len(log)}",
        f"history_update_attempted={update_attempted}",
        f"history_update_success={update_success}",
        f"yfinance_available={yf is not None}",
        "",
        "[self_checks]",
    ]
    lines += [f"{k}={'PASS' if v else 'FAIL'}" for k, v in checks.items()]

    text = "\n".join(lines) + "\n"
    with open(CONFIG["diagnostics_file"], "w", encoding="utf-8") as f:
        f.write(text)

    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise RuntimeError("Self-check failed: " + ", ".join(failed))
    return text


def print_top(ranked: pd.DataFrame) -> None:
    print("\n" + "=" * 76)
    print(" Ranking TOP 15")
    print("=" * 76)
    for i, row in ranked.head(15).iterrows():
        print(
            f"{i+1:>2}. {normalize_code(row['銘柄コード']):<5} "
            f"{str(row['銘柄名'])[:22]:<22} "
            f"{int(row['急騰期待スコア']):>3} {row['優先ランク']} "
            f"{stage_name(row['判定'])}"
        )
    print("=" * 76)


def main() -> int:
    print("=" * 76)
    print(f" Stock Pipeline Enhancer v{VERSION}")
    print(f" Script: {os.path.abspath(__file__)}")
    print(f" Folder: {os.getcwd()}")
    print("=" * 76)

    if not os.path.exists(CONFIG["input_csv"]):
        print(f"ERROR: {CONFIG['input_csv']} not found")
        return 2

    try:
        df = read_csv_flexible(CONFIG["input_csv"])
        print(f"[1/5] Input loaded: {len(df)} rows")

        ranked = build_ranking(df)
        ranked.to_csv(CONFIG["ranked_csv"], index=False, encoding="utf-8-sig")
        print(f"[2/5] Ranking saved: {CONFIG['ranked_csv']}")
        print_top(ranked)

        log = load_trade_log(CONFIG["trade_log_csv"])
        # First update old open trades, then add today's new Step3 signals.
        log, attempted, updated = update_virtual_trades(log)
        before = len(log)
        log = add_new_virtual_trades(ranked, log)
        added = len(log) - before
        log.to_csv(CONFIG["trade_log_csv"], index=False, encoding="utf-8-sig")
        print(f"[3/5] Trade log saved: {CONFIG['trade_log_csv']} (added={added}, total={len(log)})")

        summary = make_summary(log)
        summary.to_csv(CONFIG["summary_csv"], index=False, encoding="utf-8-sig")
        print(f"[4/5] Summary saved: {CONFIG['summary_csv']}")

        diag = write_diagnostics(df, ranked, log, attempted, updated)
        print(f"[5/5] Self-check PASS: {CONFIG['diagnostics_file']}")
        print("\n" + diag)
        print("Finished successfully.")
        return 0
    except Exception as exc:
        print("\nERROR: enhancer stopped because validation/self-check failed.")
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
