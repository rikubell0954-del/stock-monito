# ============================================================
# 急騰株スクリーナー（低位株 編）
# 実行方法: python stock_screener.py
# 必要ライブラリ:
# pip install yfinance pandas requests openpyxl xlrd
# ============================================================

import time
import warnings
import json
import re
from pathlib import Path
from io import BytesIO
from urllib.parse import urljoin
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
import requests

warnings.filterwarnings("ignore")
HEALTH_FILE = Path("screener_health.json")
FETCH_ERRORS = []
DATA_ERRORS = []

def write_health(payload):
    HEALTH_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

CONFIG = {
    "max_price": 1000,          # 株価上限
    "min_market_cap": 10,       # 時価総額下限（億円）
    "max_market_cap": 100,      # 時価総額上限（億円）
    "vol_surge_ratio": 3.0,     # 出来高急増の閾値
    "range_pct": 0.20,          # 直近20営業日の値幅
    "lookback_days": 60,        # 分析日数
    "output_csv": "screening_result.csv",
    "sleep_seconds": 0.3,       # 取得間隔
    "max_retries": 2,
    "min_jpx_tickers": 500,
    "warn_success_rate": 0.95,
    "fail_success_rate": 0.80,
}


def get_tse_tickers():
    """
    JPX公式「東証上場銘柄一覧」から現在のExcelを取得する。
    固定URLだけに依存せず、公式ページ内の .xls/.xlsx リンクを探索する。
    """
    landing_url = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
    legacy_url = (
        "https://www.jpx.co.jp/markets/statistics-equities/misc/"
        "tvdivq0000001vg2-att/data_j.xls"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/vnd.ms-excel,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "*/*;q=0.8"
        ),
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
    }

    candidates = []

    try:
        page = requests.get(landing_url, headers=headers, timeout=30)
        page.raise_for_status()

        pattern = r'href=["\']([^"\']+\.(?:xls|xlsx)(?:\?[^"\']*)?)["\']'
        hrefs = re.findall(pattern, page.text, flags=re.IGNORECASE)

        hrefs = sorted(
            dict.fromkeys(hrefs),
            key=lambda h: (0 if "data_j" in h.lower() else 1, len(h)),
        )
        candidates.extend(urljoin(landing_url, h) for h in hrefs)
    except Exception as e:
        print(f"JPX案内ページからExcel探索に失敗: {e}")

    if legacy_url not in candidates:
        candidates.append(legacy_url)

    errors = []
    excel_bytes = None
    used_url = None

    for url in candidates:
        try:
            print(f"JPX Excel取得を試行: {url}")
            resp = requests.get(url, headers=headers, timeout=45, allow_redirects=True)

            if resp.status_code != 200:
                errors.append(f"{url} -> HTTP {resp.status_code}")
                continue

            content = resp.content
            head = content[:200].lstrip().lower()

            if b"<html" in head or b"<!doctype html" in head:
                errors.append(f"{url} -> HTML response instead of Excel")
                continue

            if len(content) < 10000:
                errors.append(f"{url} -> file too small ({len(content)} bytes)")
                continue

            excel_bytes = content
            used_url = url
            break

        except Exception as e:
            errors.append(f"{url} -> {type(e).__name__}: {e}")

    if excel_bytes is None:
        msg = " / ".join(errors[-10:]) if errors else "Excel候補URLなし"
        write_health({
            "status": "FATAL",
            "stage": "JPX",
            "message": f"JPX Excel取得失敗: {msg}",
            "target_count": 0,
        })
        raise RuntimeError(f"JPX銘柄リスト取得失敗: {msg}")

    try:
        df = pd.read_excel(BytesIO(excel_bytes))
    except Exception as e:
        write_health({
            "status": "FATAL",
            "stage": "JPX",
            "message": f"JPX Excel解析失敗: {e}",
            "source_url": used_url,
            "target_count": 0,
        })
        raise RuntimeError(f"JPX Excel解析失敗: {e}")

    required = {"市場・商品区分", "コード"}
    missing = required - set(df.columns)
    if missing:
        msg = f"JPX列構成変更の可能性: missing={sorted(missing)}"
        write_health({
            "status": "FATAL",
            "stage": "JPX",
            "message": msg,
            "source_url": used_url,
            "target_count": 0,
        })
        raise RuntimeError(msg)

    target_markets = {
        "グロース（内国株式）",
        "スタンダード（内国株式）",
    }
    target_df = df[df["市場・商品区分"].isin(target_markets)].copy()

    tickers = []
    for code in target_df["コード"].dropna():
        s = str(code).strip()
        if s.endswith(".0"):
            s = s[:-2]
        if not s:
            continue
        tickers.append(f"{s}.T")

    tickers = list(dict.fromkeys(tickers))

    if len(tickers) < CONFIG["min_jpx_tickers"]:
        msg = f"JPX対象銘柄数が異常に少ない: {len(tickers)}"
        write_health({
            "status": "FATAL",
            "stage": "JPX",
            "message": msg,
            "source_url": used_url,
            "target_count": len(tickers),
        })
        raise RuntimeError(msg)

    print(f"JPX取得元: {used_url}")
    print(f"対象銘柄数: {len(tickers)}件")
    return tickers

def to_float(value):
    """
    yfinanceの値を安全にfloatへ変換
    """
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return float(value)
    except Exception:
        return None


def screen_stock(ticker):
    """
    個別銘柄をスクリーニング
    """
    try:
        end = datetime.today()
        start = end - timedelta(days=CONFIG["lookback_days"] + 40)

        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if df is None or df.empty or len(df) < 30:
            return None

        # MultiIndex対策
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.tail(CONFIG["lookback_days"])

        close = df["Close"]
        volume = df["Volume"]
        low = df["Low"]

        latest = to_float(close.iloc[-1])
        if latest is None:
            return None

        # ① 株価フィルタ
        if latest >= CONFIG["max_price"]:
            return None

        # ② 時価総額フィルタ
        info = yf.Ticker(ticker).info
        market_cap = info.get("marketCap")

        if not market_cap:
            return None

        cap_oku = market_cap / 100_000_000

        if not (CONFIG["min_market_cap"] <= cap_oku <= CONFIG["max_market_cap"]):
            return None

        # ③ ヨコヨコ判定
        recent = close.tail(20)
        recent_max = to_float(recent.max())
        recent_min = to_float(recent.min())

        if not recent_max or not recent_min or recent_min <= 0:
            return None

        price_range = (recent_max - recent_min) / recent_min

        if price_range > CONFIG["range_pct"]:
            return None

        # ④ 移動平均線
        ma5 = close.rolling(5).mean()
        ma25 = close.rolling(25).mean()

        ma5_latest = to_float(ma5.iloc[-1])
        ma25_latest = to_float(ma25.iloc[-1])

        if ma5_latest is None or ma25_latest is None:
            return None

        above_ma5 = latest > ma5_latest
        above_ma25 = latest > ma25_latest

        # ⑤ 出来高急増
        vol_mean = to_float(volume.iloc[:-1].mean())
        vol_latest = to_float(volume.iloc[-1])

        if not vol_mean or vol_mean <= 0:
            vol_ratio = 0
        else:
            vol_ratio = vol_latest / vol_mean

        # ⑥ 下値が揃っているか
        recent10_low = low.tail(10)

        low_min = to_float(recent10_low.min())
        low_max = to_float(recent10_low.max())

        if not low_min or low_min <= 0:
            lower_wick_aligned = False
        else:
            low_range_pct = (low_max - low_min) / low_min
            lower_wick_aligned = low_range_pct < 0.05

        # ⑦ 判定
        step = "監視候補"

        if vol_ratio >= CONFIG["vol_surge_ratio"] and above_ma5 and above_ma25:
            step = "★ Step1（初動）"
        elif above_ma5 and above_ma25 and lower_wick_aligned:
            step = "◆ Step0（仕込み）"

        return {
            "銘柄コード": ticker.replace(".T", ""),
            "銘柄名": info.get("longName", "不明"),
            "株価": round(latest, 1),
            "時価総額(億円)": round(cap_oku, 1),
            "出来高比率": round(vol_ratio, 2),
            "値幅(%)": round(price_range * 100, 1),
            "MA5上": "✓" if above_ma5 else "✗",
            "MA25上": "✓" if above_ma25 else "✗",
            "下値集中": "✓" if lower_wick_aligned else "✗",
            "判定": step,
        }

    except Exception as e:
        FETCH_ERRORS.append({"ticker": ticker, "reason": f"{type(e).__name__}: {e}"})
        return None


def main():
    print("=" * 50)
    print(" 急騰株スクリーナー 起動")
    print("=" * 50)

    tickers = get_tse_tickers()

    if not tickers:
        print("銘柄リストを取得できませんでした。")
        return

    results = []

    for i, ticker in enumerate(tickers, start=1):
        print(f"\r処理中... {i}/{len(tickers)} ({ticker})", end="")

        result = screen_stock(ticker)

        if result:
            results.append(result)

        time.sleep(CONFIG["sleep_seconds"])

    print("\n" + "=" * 50)

    target_count = len(tickers)
    fetch_error_count = len(FETCH_ERRORS)
    judged_count = max(0, target_count - fetch_error_count)
    success_rate = judged_count / target_count if target_count else 0
    health_status = "OK" if success_rate >= CONFIG["warn_success_rate"] else ("WARNING" if success_rate >= CONFIG["fail_success_rate"] else "FATAL")
    write_health({"status":health_status,"stage":"SCREENER","target_count":target_count,
                  "judged_count":judged_count,"candidate_count":len(results),
                  "fetch_error_count":fetch_error_count,"success_rate":round(success_rate,4),
                  "fetch_errors":FETCH_ERRORS[:100]})
    print(f"データ取得成功率: {success_rate:.1%} ({judged_count}/{target_count}) / 明示的取得失敗: {fetch_error_count}")
    if health_status == "FATAL":
        raise RuntimeError(f"データ取得成功率が低すぎます: {success_rate:.1%}")

    if not results:
        # stale CSVを残さない
        pd.DataFrame(columns=["銘柄コード","銘柄名","株価","時価総額(億円)","出来高比率","値幅(%)","MA5上","MA25上","下値集中","判定"]).to_csv(CONFIG["output_csv"], index=False, encoding="utf-8-sig")
        print("条件に合う銘柄は見つかりませんでした。")
        return

    df_result = pd.DataFrame(results)

    order = {
        "★ Step1（初動）": 0,
        "◆ Step0（仕込み）": 1,
        "監視候補": 2,
    }

    df_result["sort_key"] = df_result["判定"].map(order)
    df_result = df_result.sort_values("sort_key").drop(columns=["sort_key"])

    df_result.to_csv(
        CONFIG["output_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    print(f"✅ スクリーニング完了！ {len(results)}件の候補を発見")
    print(f"📄 結果ファイル: {CONFIG['output_csv']}")
    print()
    print("【内訳】")
    print(df_result["判定"].value_counts().to_string())


if __name__ == "__main__":
    main()
