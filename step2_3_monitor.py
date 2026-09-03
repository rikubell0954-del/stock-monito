# ============================================================
# 急騰株モニター Step2・3 判定プログラム 実運用版
#
# 実行方法:
#   python step2_3_monitor.py
#
# 個別銘柄指定:
#   python step2_3_monitor.py 3150 4385 6541
#
# 必要ライブラリ:
#   python -m pip install yfinance pandas requests openpyxl xlrd
# ============================================================

import sys
import time
import json
from pathlib import Path
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
STEP23_HEALTH = Path("step2_3_health.json")

def write_step23_health(payload):
    STEP23_HEALTH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


CONFIG = {
    "step1_csv": "screening_result.csv",
    "output_csv": "step2_3_result.csv",

    # MA200まで見るため、取得期間は長めにする
    "download_days": 420,

    # 判定条件
    "vol_surge_ratio": 2.5,
    "vol_dry_ratio": 0.6,
    "sleep_seconds": 0.5,

    # Step3を厳しめにするか
    # False: MA5・MA25上でStep3判定
    # True : MA5・MA25・MA75上でStep3判定
    "strict_step3": True,
}


def normalize_code(value):
    """銘柄コードを安全に4桁文字列へ変換"""
    code = str(value).strip().replace(".T", "")
    code = code.replace(".0", "")
    return code.zfill(4)


def to_float(value):
    """yfinanceやpandasの値を安全にfloatへ変換"""
    try:
        if hasattr(value, "iloc"):
            value = value.iloc[0]
        return float(value)
    except Exception:
        return None


def fetch_ohlcv(ticker):
    """株価データ取得"""
    end = datetime.today()
    start = end - timedelta(days=CONFIG["download_days"])

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

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def analyze_stock(ticker_raw):
    """1銘柄をStep2・Step3判定"""
    code = normalize_code(ticker_raw)
    ticker = code + ".T"

    try:
        df = fetch_ohlcv(ticker)

        if df is None:
            return None

        close = df["Close"]
        open_ = df["Open"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        latest_close = to_float(close.iloc[-1])
        if latest_close is None:
            return None

        # 移動平均
        ma5 = to_float(close.rolling(5).mean().iloc[-1])
        ma25 = to_float(close.rolling(25).mean().iloc[-1])
        ma75 = to_float(close.rolling(75).mean().iloc[-1])
        ma200 = to_float(close.rolling(200).mean().iloc[-1])

        above_ma5 = ma5 is not None and latest_close > ma5
        above_ma25 = ma25 is not None and latest_close > ma25
        above_ma75 = ma75 is not None and latest_close > ma75
        above_ma200 = ma200 is not None and latest_close > ma200

        above_basic_ma = above_ma5 and above_ma25
        above_strict_ma = above_ma5 and above_ma25 and above_ma75
        above_all_ma = above_ma5 and above_ma25 and above_ma75 and above_ma200

        # 出来高分析
        vol_series = volume.dropna()

        if len(vol_series) < 30:
            return None

        vol_base = vol_series.iloc[:-5]
        vol_recent5 = vol_series.iloc[-5:]

        vol_latest = to_float(vol_series.iloc[-1])
        vol_base_mean = to_float(vol_base.mean())
        vol_recent_mean = to_float(vol_recent5.mean())

        if not vol_base_mean or vol_base_mean <= 0:
            vol_ratio = 0
            vol_dried = False
            vol_resurgence = False
        else:
            vol_ratio = round(vol_latest / vol_base_mean, 2)
            vol_dried = vol_recent_mean <= vol_base_mean * CONFIG["vol_dry_ratio"]
            vol_resurgence = vol_latest >= vol_base_mean * CONFIG["vol_surge_ratio"]

        # ローソク足：直近3日中2日以上が小実体
        body_small_days = 0

        for i in range(-3, 0):
            o = to_float(open_.iloc[i])
            c = to_float(close.iloc[i])
            h = to_float(high.iloc[i])
            l = to_float(low.iloc[i])

            if None in (o, c, h, l):
                continue

            day_range = h - l
            if day_range <= 0:
                continue

            body_ratio = abs(c - o) / day_range

            if body_ratio <= 0.35:
                body_small_days += 1

        candle_small = body_small_days >= 2

        # じわじわ切り上げ：直近5日の終値比較
        recent5_close = [to_float(close.iloc[i]) for i in range(-5, 0)]
        recent5_close = [v for v in recent5_close if v is not None]

        price_drifting_up = (
            len(recent5_close) >= 5
            and recent5_close[-1] >= recent5_close[0]
        )

        # VWAPではなく、日足の代表価格
        h_latest = to_float(high.iloc[-1])
        l_latest = to_float(low.iloc[-1])

        typical_price = None
        above_typical_price = False

        if None not in (h_latest, l_latest, latest_close):
            typical_price = (h_latest + l_latest + latest_close) / 3
            above_typical_price = latest_close >= typical_price * 0.99

        # 直近高値からの下落率
        recent20_high = to_float(high.tail(20).max())

        drawdown_pct = None
        if recent20_high and recent20_high > 0:
            drawdown_pct = round((latest_close - recent20_high) / recent20_high * 100, 1)

        # 損切り目安：直近30日安値までの距離
        recent30_low = to_float(low.tail(30).min())

        stop_loss_pct = None
        if recent30_low and latest_close > 0:
            stop_loss_pct = round((latest_close - recent30_low) / latest_close * 100, 1)

        # Step判定
        if CONFIG["strict_step3"]:
            step3_ma_condition = above_strict_ma
        else:
            step3_ma_condition = above_basic_ma

        if vol_resurgence and step3_ma_condition and above_typical_price:
            step = "🚀 Step3（エントリー候補）"

        elif vol_dried and above_basic_ma and candle_small and above_typical_price:
            step = "⏳ Step2完了（次の急騰待ち）"

        elif vol_dried and above_basic_ma:
            step = "📉 Step2中（調整中）"

        else:
            step = "👀 監視継続"

        # 銘柄名取得
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName", "不明")
        except Exception:
            name = "不明"

        return {
            "銘柄コード": code,
            "銘柄名": name,
            "株価": round(latest_close, 1),
            "判定": step,
            "出来高比率": vol_ratio,
            "出来高枯れ": "✓" if vol_dried else "✗",
            "出来高再増加": "✓" if vol_resurgence else "✗",
            "小実体": "✓" if candle_small else "✗",
            "じわ切り上げ": "✓" if price_drifting_up else "✗",
            "日足代表価格上": "✓" if above_typical_price else "✗",
            "MA5上": "✓" if above_ma5 else "✗",
            "MA25上": "✓" if above_ma25 else "✗",
            "MA75上": "✓" if above_ma75 else "✗",
            "MA200上": "✓" if above_ma200 else "✗",
            "全MA上": "✓" if above_all_ma else "✗",
            "直近高値からの下落(%)": drawdown_pct,
            "損切り目安下幅(%)": stop_loss_pct,
        }

    except Exception:
        return None


def load_tickers_from_args():
    codes = [normalize_code(c) for c in sys.argv[1:]]
    print(f"📌 指定銘柄: {', '.join(codes)}")
    return codes


def load_tickers_from_csv():
    try:
        df = pd.read_csv(CONFIG["step1_csv"], encoding="utf-8-sig")

        if "銘柄コード" not in df.columns:
            print("❌ CSVに『銘柄コード』列がありません。")
            return []

        codes = df["銘柄コード"].dropna().apply(normalize_code).tolist()

        # 重複削除
        codes = list(dict.fromkeys(codes))

        print(f"📄 CSVから {len(codes)}件を読み込みました: {CONFIG['step1_csv']}")
        return codes

    except FileNotFoundError:
        print(f"❌ CSVが見つかりません: {CONFIG['step1_csv']}")
        print("先に Step1 の stock_screener.py を実行してください。")
        return []

    except Exception as e:
        print(f"❌ CSV読み込みエラー: {e}")
        return []


def main():
    print("=" * 60)
    print(" 急騰株モニター Step2・3 判定プログラム 実運用版")
    print("=" * 60)
    print(f" 実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if len(sys.argv) > 1:
        tickers = load_tickers_from_args()
    else:
        tickers = load_tickers_from_csv()

    if not tickers:
        # A missing/empty Step1 input is an upstream failure, not a valid zero-candidate day.
        out_path = Path(CONFIG["output_csv"])
        if out_path.exists():
            out_path.unlink()
        write_step23_health({
            "status": "FATAL",
            "stage": "INPUT",
            "input_count": 0,
            "analyzed_count": 0,
            "message": "screening_result.csv に解析対象銘柄がありません",
        })
        raise RuntimeError(
            "Step2/3停止: screening_result.csv に解析対象銘柄がありません。"
        )

    results = []

    for i, code in enumerate(tickers, start=1):
        print(f"\r分析中... {i}/{len(tickers)} ({code})", end="")

        result = analyze_stock(code)

        if result:
            results.append(result)

        time.sleep(CONFIG["sleep_seconds"])

    print("\n" + "=" * 60)

    if not results:
        out_path = Path(CONFIG["output_csv"])
        if out_path.exists():
            out_path.unlink()
        write_step23_health({
            "status": "FATAL",
            "stage": "ANALYSIS",
            "input_count": len(tickers),
            "analyzed_count": 0,
            "message": "全銘柄のStep2/3分析に失敗",
        })
        raise RuntimeError(
            f"Step2/3停止: 入力{len(tickers)}銘柄に対し分析成功0件です。"
        )

    df_out = pd.DataFrame(results)

    order = {
        "🚀 Step3（エントリー候補）": 0,
        "⏳ Step2完了（次の急騰待ち）": 1,
        "📉 Step2中（調整中）": 2,
        "👀 監視継続": 3,
    }

    df_out["_sort"] = df_out["判定"].map(order).fillna(9)
    df_out = df_out.sort_values("_sort").drop(columns=["_sort"])

    df_out.to_csv(
        CONFIG["output_csv"],
        index=False,
        encoding="utf-8-sig",
    )

    write_step23_health({
        "status": "OK",
        "stage": "STEP2_3",
        "input_count": len(tickers),
        "analyzed_count": len(df_out),
        "output_file": CONFIG["output_csv"],
    })

    print("【結果サマリー】")
    print(df_out["判定"].value_counts().to_string())
    print()

    step3 = df_out[df_out["判定"].str.contains("Step3", na=False)]
    if not step3.empty:
        print("🚀 【Step3 エントリー候補】")
        for _, row in step3.iterrows():
            print(f"[{row['銘柄コード']}] {row['銘柄名']}")
            print(f"  株価: {row['株価']}円 / 出来高比率: {row['出来高比率']}倍")
            print(f"  MA75上: {row['MA75上']} / MA200上: {row['MA200上']}")
            print(f"  損切り目安下幅: {row['損切り目安下幅(%)']}%")
            print()

    step2done = df_out[df_out["判定"].str.contains("Step2完了", na=False)]
    if not step2done.empty:
        print("⏳ 【Step2完了 次の急騰待ち】")
        for _, row in step2done.iterrows():
            print(f"[{row['銘柄コード']}] {row['銘柄名']}")
            print(f"  株価: {row['株価']}円 / 出来高比率: {row['出来高比率']}倍")
            print(f"  小実体: {row['小実体']} / じわ切り上げ: {row['じわ切り上げ']}")
            print()

    print(f"✅ 完了！ → {CONFIG['output_csv']} に保存しました")
    print()
    print("【見方】")
    print("🚀 Step3      : 出来高再増加。チャート確認後、エントリー検討")
    print("⏳ Step2完了 : 調整終盤。出来高再増加待ち")
    print("📉 Step2中   : 調整中。焦らず監視")
    print("👀 監視継続  : まだ条件未達")


if __name__ == "__main__":
    main()
