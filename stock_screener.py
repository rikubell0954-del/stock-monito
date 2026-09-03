# ============================================================
# 急騰株スクリーナー（低位株 編）
# 実行方法: python stock_screener.py
# 必要ライブラリ:
# pip install yfinance pandas requests openpyxl xlrd
# ============================================================

import time
import warnings
import json
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

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
    JPX公開の銘柄一覧から、
    東証グロース・スタンダードの銘柄コードを取得
    """
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

    try:
        df = pd.read_excel(url)

        target_markets = [
            "グロース（内国株式）",
            "スタンダード（内国株式）",
        ]

        df = df[df["市場・商品区分"].isin(target_markets)]

        tickers = []
        for code in df["コード"]:
            code_str = str(code).strip().zfill(4)
            tickers.append(code_str + ".T")

        if len(tickers) < CONFIG["min_jpx_tickers"]:
            msg=f"JPX対象銘柄数が異常に少ない: {len(tickers)}"
            write_health({"status":"FATAL","stage":"JPX","message":msg,"target_count":len(tickers)})
            raise RuntimeError(msg)
        print(f"対象銘柄数: {len(tickers)}件")
        return tickers

    except Exception as e:
        write_health({"status":"FATAL","stage":"JPX","message":str(e),"target_count":0})
        raise RuntimeError(f"JPX銘柄リスト取得失敗: {e}")


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
