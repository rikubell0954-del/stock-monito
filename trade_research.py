# -*- coding: utf-8 -*-
"""Add-only research records for Stock Monitor. Does not modify trade decisions/logs.

Run from build_mobile_report.py AFTER workflow copies root outputs to results/.
Outputs (persistent, committed by existing `git add results` step):
  results/trade_signal_features.csv   immutable signal-day snapshots
  results/trade_daily_prices.csv      OHLCV by trade/date, incremental
  results/trade_strategy_comparison.csv  side-by-side paper exit alternatives
  results/trade_research_status.json  coverage and failures

Old signals are NOT assigned today's factor values. Market OHLC backfills are marked
as historical, not as contemporaneously collected signals.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import re
from typing import Optional

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

TOKYO = ZoneInfo("Asia/Tokyo")
VERSION = "1.0.0"
SIGNAL_COLUMNS = [
    "銘柄コード", "銘柄名", "株価", "判定", "出来高比率", "出来高枯れ",
    "出来高再増加", "小実体", "じわ切り上げ", "日足代表価格上",
    "MA5上", "MA25上", "MA75上", "MA200上", "全MA上",
    "直近高値からの下落(%)", "損切り目安下幅(%)",
    "急騰期待スコア", "優先ランク", "スコア根拠", "監視優先度",
]
SNAP_COLUMNS = [
    "trade_id", "code", "signal_date", "signal_score", "signal_rank",
    "snapshot_quality", "snapshot_recorded_at", *SIGNAL_COLUMNS,
]
PRICE_COLUMNS = [
    "trade_id", "code", "date", "open", "high", "low", "close", "volume",
    "source", "price_basis", "record_type", "recorded_at",
]
STRATEGY_COLUMNS = [
    "trade_id", "code", "signal_date", "original_status", "strategy_id",
    "strategy_name", "comparison_status", "data_quality", "bars_available",
    "bars_expected", "close_date", "exit_reason", "realized_pnl_100shares",
    "unrealized_pnl_100shares", "total_pnl_100shares", "total_return_pct",
    "price_basis", "note",
]

STRATEGIES = [
    ("BASE_30_20D", "現行基準（+30%・20日）", 0.30, 20, False),
    ("TP20_20D", "+20%全売却・20日", 0.20, 20, False),
    ("HALF20_REST30", "+20%半売・残り+30%・20日", 0.30, 20, True),
    ("TIME10", "10営業日決済（+30%は維持）", 0.30, 10, False),
    ("TIME15", "15営業日決済（+30%は維持）", 0.30, 15, False),
]


def as_text(v):
    return "" if pd.isna(v) else str(v)


def as_num(v) -> Optional[float]:
    n = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
    return float(n) if pd.notna(n) else None


def normalize_code(value):
    s = as_text(value).strip().replace(".T", "")
    return re.sub(r"\.0$", "", s)


def date10(value):
    return as_text(value)[:10]


def read_csv(path, columns):
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"trade_id": str, "code": str})
    except (pd.errors.EmptyDataError, UnicodeDecodeError):
        df = pd.read_csv(path, encoding="cp932", dtype={"trade_id": str, "code": str})
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    return df[columns]


def write_csv(df, path, columns):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df[columns].to_csv(path, index=False, encoding="utf-8-sig")


def record_signal_snapshots(trades, ranked, old_snapshots, today, timestamp):
    """Never manufacture past factor values from present-day rankings."""
    existing = set(old_snapshots["trade_id"].astype(str))
    by_code = {}
    if "銘柄コード" in ranked.columns:
        for _, r in ranked.iterrows():
            if "Step3" in as_text(r.get("判定", "")).replace(" ", ""):
                by_code[normalize_code(r.get("銘柄コード", ""))] = r
    rows = []
    for _, tr in trades.iterrows():
        tid = as_text(tr.get("trade_id", ""))
        if not tid or tid in existing:
            continue
        code = normalize_code(tr.get("code", ""))
        day = date10(tr.get("signal_date", ""))
        row = {c: "" for c in SNAP_COLUMNS}
        row.update({
            "trade_id": tid, "code": code, "signal_date": day,
            "signal_score": tr.get("signal_score", ""),
            "signal_rank": tr.get("signal_rank", ""),
            "snapshot_quality": "legacy_missing",
            "snapshot_recorded_at": timestamp,
        })
        candidate = by_code.get(code)
        if day == today and candidate is not None:
            # Score and entry must match the immutable trade record.
            candidate_entry = as_num(candidate.get("株価", ""))
            entry = as_num(tr.get("entry_price", ""))
            if (candidate_entry is not None and entry is not None
                    and abs(candidate_entry - entry) < 0.011
                    and as_text(candidate.get("優先ランク", "")) == as_text(tr.get("signal_rank", ""))):
                for c in SIGNAL_COLUMNS:
                    row[c] = candidate.get(c, "")
                row["snapshot_quality"] = "full_signal"
        rows.append(row)
        existing.add(tid)
    if rows:
        old_snapshots = pd.concat([old_snapshots, pd.DataFrame(rows)], ignore_index=True)
    return old_snapshots[SNAP_COLUMNS].drop_duplicates("trade_id", keep="first")


def fetch_ohlcv(tr, today):
    """Fetch adjusted OHLCV consistent with the existing enhancer's auto_adjust=True."""
    if yf is None:
        return pd.DataFrame()
    signal_day = date10(tr.get("signal_date", ""))
    code = normalize_code(tr.get("code", ""))
    if not signal_day or not code:
        return pd.DataFrame()
    try:
        begin = pd.Timestamp(signal_day) - timedelta(days=3)
        end_day = today
        if as_text(tr.get("status", "")).upper() == "CLOSED" and date10(tr.get("exit_date", "")):
            end_day = min(today, date10(tr["exit_date"]))
        end_exclusive = pd.Timestamp(end_day) + timedelta(days=1)
        if end_exclusive <= begin:
            return pd.DataFrame()
        hist = yf.download(
            f"{code}.T", start=begin.strftime("%Y-%m-%d"),
            end=end_exclusive.strftime("%Y-%m-%d"),
            progress=False, threads=False, auto_adjust=True, interval="1d",
        )
        if hist is None or hist.empty:
            return pd.DataFrame()
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        hist = hist.reset_index()
        dtcol = "Date" if "Date" in hist.columns else hist.columns[0]
        hist[dtcol] = pd.to_datetime(hist[dtcol], errors="coerce")
        if getattr(hist[dtcol].dt, "tz", None) is not None:
            hist[dtcol] = hist[dtcol].dt.tz_localize(None)
        hist[dtcol] = hist[dtcol].dt.strftime("%Y-%m-%d")
        hist = hist[(hist[dtcol] > signal_day) & (hist[dtcol] <= end_day)]
        if hist.empty:
            return pd.DataFrame()
        keys = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        for src in ("Open", "High", "Low", "Close"):
            if src not in hist.columns:
                return pd.DataFrame()
        result = pd.DataFrame({"date": hist[dtcol]})
        for src, dest in keys.items():
            result[dest] = pd.to_numeric(hist[src], errors="coerce") if src in hist else None
        return result.dropna(subset=["open", "high", "low", "close"])
    except Exception:
        return pd.DataFrame()


def append_prices(trades, prices, today, timestamp, fetcher=fetch_ohlcv, max_backfill=8, previous_attempts=None):
    """Fetch all OPENs and only limited legacy CLOSEDs per run. Never replace old bars."""
    existing = set(zip(prices["trade_id"].astype(str), prices["date"].astype(str)))
    attempts = dict(previous_attempts or {})
    open_count, closed_count, failed = 0, 0, []
    # Open first. Closed history is incrementally reconstructed at a capped rate.
    ordering = trades.assign(
        _is_closed=trades["status"].astype(str).str.upper().eq("CLOSED"),
        _previous_attempts=trades["trade_id"].astype(str).map(lambda tid: attempts.get(tid, 0)),
    )
    # Repeated Yahoo failures must not starve other historical trades.
    ordering = ordering.sort_values(
        ["_is_closed", "_previous_attempts", "signal_date"],
        ascending=[True, True, False], kind="stable",
    )
    rows = []
    for _, tr in ordering.iterrows():
        tid = as_text(tr["trade_id"])
        code = normalize_code(tr["code"])
        status = as_text(tr["status"]).upper()
        if status not in ("OPEN", "CLOSED") or not tid:
            continue
        # Closed historical fetch only if key exit day isn't stored or bar count is short.
        existing_n = sum(1 for t, _ in existing if t == tid)
        expected = int(as_num(tr.get("days_observed", 0)) or 0)
        exitday = date10(tr.get("exit_date", ""))
        if status == "CLOSED":
            needs = expected > existing_n or (exitday and (tid, exitday) not in existing)
            if not needs or closed_count >= max_backfill:
                continue
            closed_count += 1
            attempts[tid] = int(attempts.get(tid, 0)) + 1
        else:
            # New OPEN trades always fetch to append missing daily bars.
            open_count += 1
        hist = fetcher(tr, today)
        if hist.empty:
            # Same-day new OPEN has no post-signal trading day yet, not a failure.
            if date10(tr.get("signal_date", "")) < today:
                failed.append(tid)
            continue
        record_type = "historical_backfill" if status == "CLOSED" else "daily_collection"
        for _, bar in hist.iterrows():
            day = date10(bar["date"])
            key = (tid, day)
            if key in existing:
                continue
            vals = [as_num(bar.get(k)) for k in ("open", "high", "low", "close")]
            if any(v is None or v <= 0 for v in vals) or vals[1] < vals[2]:
                continue
            rows.append({
                "trade_id": tid, "code": code, "date": day,
                "open": round(vals[0], 4), "high": round(vals[1], 4),
                "low": round(vals[2], 4), "close": round(vals[3], 4),
                "volume": as_num(bar.get("volume")), "source": "yfinance",
                "price_basis": "auto_adjust=True", "record_type": record_type,
                "recorded_at": timestamp,
            })
            existing.add(key)
    if rows:
        prices = pd.concat([prices, pd.DataFrame(rows)], ignore_index=True)
    prices = prices.drop_duplicates(["trade_id", "date"], keep="first")
    prices = prices.sort_values(["trade_id", "date"], kind="stable")
    return prices[PRICE_COLUMNS], open_count, closed_count, failed, attempts


def evaluate_strategy(tr, bars, strategy, complete):
    """100-share shadow path. Daily conflict: STOP wins over any profit target."""
    sid, name, target_pct, days_limit, split = strategy
    entry = as_num(tr.get("entry_price"))
    stop = as_num(tr.get("stop_price"))
    expected = int(as_num(tr.get("days_observed")) or 0)
    status = as_text(tr.get("status", "")).upper()
    tid = as_text(tr.get("trade_id"))
    row = dict.fromkeys(STRATEGY_COLUMNS, "")
    row.update({
        "trade_id": tid, "code": normalize_code(tr.get("code")),
        "signal_date": date10(tr.get("signal_date")), "original_status": status,
        "strategy_id": sid, "strategy_name": name,
        "data_quality": "READY" if complete else "PARTIAL",
        "bars_available": len(bars), "bars_expected": expected,
        "price_basis": "auto_adjust=True",
        "note": "参考値: 指値約定/板流動性/手数料/税金を考慮しない。調整株価を使用。",
    })
    if entry is None or entry <= 0:
        row.update({"comparison_status": "NO_DATA", "data_quality": "INVALID_ENTRY"})
        return row
    if len(bars) == 0:
        row.update({"comparison_status": "NO_DATA", "data_quality": "NO_BARS"})
        return row
    # Baseline remains 30%, stop, 20 trading days. It is only an *independent*
    # reconstruction, never used to change the authoritative virtual_trade_log.csv.
    bars = bars.head(days_limit)
    remaining, realized, reason, exit_day = 100, 0.0, "", ""
    last_close = entry
    for index, (_, b) in enumerate(bars.iterrows(), start=1):
        high, low, close = (as_num(b.get(x)) for x in ("high", "low", "close"))
        if any(x is None for x in (high, low, close)):
            continue
        last_close = close
        date = date10(b["date"])
        # Conservative STOP order applies even on the partial-sell day.
        if stop is not None and low <= stop:
            realized += (stop - entry) * remaining
            remaining, reason, exit_day = 0, "STOP", date
            break
        if split:
            if remaining == 100 and high >= entry * 1.20:
                realized += (entry * 1.20 - entry) * 50
                remaining = 50
            if remaining == 50 and high >= entry * 1.30:
                realized += (entry * 1.30 - entry) * 50
                remaining, reason, exit_day = 0, "+30%（半売済）", date
                break
        elif high >= entry * (1 + target_pct):
            realized += entry * target_pct * remaining
            remaining, reason, exit_day = 0, f"+{round(target_pct*100)}%", date
            break
        if index >= days_limit:
            realized += (close - entry) * remaining
            remaining, reason, exit_day = 0, f"{days_limit}営業日経過", date
            break
    unreal = (last_close - entry) * remaining
    if remaining == 0:
        comparison_status = "CLOSED"
    elif status == "CLOSED" and not complete:
        comparison_status = "INCOMPLETE"
    else:
        comparison_status = "OPEN"
    # Only show comparable P&L when historical coverage is complete.
    if not complete:
        comparison_status = "INCOMPLETE"
    row.update({
        "comparison_status": comparison_status,
        "close_date": exit_day, "exit_reason": reason,
        "realized_pnl_100shares": round(realized, 2) if complete else "",
        "unrealized_pnl_100shares": round(unreal, 2) if complete else "",
        "total_pnl_100shares": round(realized + unreal, 2) if complete else "",
        "total_return_pct": round((realized + unreal) / (entry * 100) * 100, 3) if complete else "",
    })
    return row


def build_strategy_comparison(trades, prices):
    groups = {tid: grp.sort_values("date", kind="stable") for tid, grp in prices.groupby("trade_id", sort=False)}
    rows = []
    for _, tr in trades.iterrows():
        tid = as_text(tr["trade_id"])
        hist = groups.get(tid, pd.DataFrame(columns=PRICE_COLUMNS))
        expected = int(as_num(tr.get("days_observed")) or 0)
        status = as_text(tr.get("status", "")).upper()
        exit_day = date10(tr.get("exit_date", ""))
        last = date10(hist.iloc[-1]["date"]) if len(hist) else ""
        complete = (expected > 0 and len(hist) >= expected
                    and (status != "CLOSED" or (exit_day and last >= exit_day)))
        # If original already CLOSED, shadow strategies must not see bars after
        # its actual original exit date; alternatively the +20 strategy could
        # be compared only up to the original observed holding window.
        if status == "CLOSED" and exit_day:
            hist = hist[hist["date"].astype(str) <= exit_day]
        for strategy in STRATEGIES:
            rows.append(evaluate_strategy(tr, hist, strategy, complete))
    # Historical adjusted OHLC and persisted original trade prices can disagree
    # (corporate actions, missing bars, vendor revisions). Reject that trade
    # from any comparative aggregates rather than silently showing false precision.
    frame = pd.DataFrame(rows, columns=STRATEGY_COLUMNS)
    for _, tr in trades.iterrows():
        if as_text(tr.get("status")).upper() != "CLOSED":
            continue
        entry, exitprice = as_num(tr.get("entry_price")), as_num(tr.get("exit_price"))
        if entry is None or exitprice is None:
            continue
        tid = as_text(tr["trade_id"])
        base = frame[(frame["trade_id"] == tid) & (frame["strategy_id"] == "BASE_30_20D")]
        if base.empty or as_text(base.iloc[0]["data_quality"]) != "READY":
            continue
        modeled = as_num(base.iloc[0]["realized_pnl_100shares"])
        official = (exitprice - entry) * 100
        # Rounding of price to 2 decimals can cause up to ~1 yen on 100 shares.
        if modeled is None or abs(modeled - official) > 3.0:
            mismatch = frame["trade_id"].eq(tid)
            frame.loc[mismatch, "data_quality"] = "RECONCILIATION_MISMATCH"
            frame.loc[mismatch, "note"] = "既存ログの決済損益と再計算が一致しないため比較対象外"
    return frame


def research_status(trades, signals, prices, compare, attempted_open, attempted_closed, failures, attempts, timestamp):
    full = int(signals["snapshot_quality"].eq("full_signal").sum()) if len(signals) else 0
    ids = set(prices["trade_id"].astype(str))
    complete = 0
    if not compare.empty:
        base = compare[compare["strategy_id"].eq("BASE_30_20D")]
        complete = int(base["data_quality"].eq("READY").sum())
    mismatch_count = int(compare.loc[compare["strategy_id"].eq("BASE_30_20D"), "data_quality"].eq("RECONCILIATION_MISMATCH").sum()) if len(compare) else 0
    return {
        "version": VERSION, "updated_at": timestamp,
        "trade_count": int(len(trades)),
        "full_signal_snapshots": full,
        "legacy_signal_snapshots": int(len(signals) - full),
        "daily_price_rows": int(len(prices)),
        "trades_with_price_rows": int(len(ids)),
        "trades_with_complete_price_history": complete,
        "trades_with_baseline_reconciliation_mismatch": mismatch_count,
        "historical_attempt_counts": attempts,
        "open_fetch_attempted": attempted_open,
        "closed_backfill_attempted": attempted_closed,
        "price_fetch_failed_trade_ids": failures[:30],
        "price_source": "yfinance auto_adjust=True (reconstructed history where marked)",
        "note": "Old signal factors cannot be reconstructed from present-day rankings. Strategy results require complete OHLC bars and are NOT real executed trades.",
    }


def run(root: Path | str = None, today: str = None, fetcher=None, max_backfill: int = 8):
    root = Path(root or Path(__file__).resolve().parent)
    out = root / "results"
    out.mkdir(parents=True, exist_ok=True)
    day = today or datetime.now(TOKYO).strftime("%Y-%m-%d")
    timestamp = datetime.now(TOKYO).strftime("%Y-%m-%d %H:%M:%S JST")
    trades = read_csv(out / "virtual_trade_log.csv", [
        "trade_id", "code", "name", "signal_date", "entry_price", "signal_score",
        "signal_rank", "stop_price", "status", "days_observed", "exit_date", "exit_price", "exit_reason",
    ])
    ranked_path = out / "ranked_candidates.csv"
    ranked = pd.read_csv(ranked_path, encoding="utf-8-sig") if ranked_path.exists() else pd.DataFrame()
    snapshots = read_csv(out / "trade_signal_features.csv", SNAP_COLUMNS)
    prices = read_csv(out / "trade_daily_prices.csv", PRICE_COLUMNS)
    if trades.empty:
        raise RuntimeError("virtual_trade_log.csv is missing or empty; research inputs not ready")
    if trades["trade_id"].duplicated().any():
        raise RuntimeError("Duplicate trade_id detected; refuse to corrupt research history")
    snapshots = record_signal_snapshots(trades, ranked, snapshots, day, timestamp)
    old_status_path = out / "trade_research_status.json"
    try:
        prior = json.loads(old_status_path.read_text(encoding="utf-8")) if old_status_path.exists() else {}
    except (ValueError, OSError):
        prior = {}
    prices, oc, cc, fails, attempts = append_prices(
        trades, prices, day, timestamp, fetcher=fetcher or fetch_ohlcv,
        max_backfill=max_backfill,
        previous_attempts=prior.get("historical_attempt_counts", {}),
    )
    compare = build_strategy_comparison(trades, prices)
    status = research_status(trades, snapshots, prices, compare, oc, cc, fails, attempts, timestamp)
    write_csv(snapshots, out / "trade_signal_features.csv", SNAP_COLUMNS)
    write_csv(prices, out / "trade_daily_prices.csv", PRICE_COLUMNS)
    write_csv(compare, out / "trade_strategy_comparison.csv", STRATEGY_COLUMNS)
    (out / "trade_research_status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Research: trades={len(trades)}, OHLC rows={len(prices)}, full_signal={status['full_signal_snapshots']}, comparable={status['trades_with_complete_price_history']}, failed_fetch={len(fails)}")
    return status


if __name__ == "__main__":
    run()
