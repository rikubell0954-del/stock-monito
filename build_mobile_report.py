# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import html
import pandas as pd

TOKYO = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DOCS = ROOT / "docs"
DOCS.mkdir(exist_ok=True)

def read_csv(name):
    p = RESULTS / name
    if not p.exists():
        return pd.DataFrame()
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return pd.read_csv(p, encoding=enc)
        except Exception:
            pass
    return pd.DataFrame()

def esc(v):
    return html.escape(str(v))

ranked = read_csv("ranked_candidates.csv")
trades = read_csv("virtual_trade_log.csv")
summary = read_csv("performance_summary.csv")
step = read_csv("step2_3_result.csv")

now = datetime.now(TOKYO)
updated = now.strftime("%Y-%m-%d %H:%M JST")

rank_counts = {}
if not ranked.empty and "優先ランク" in ranked.columns:
    vc = ranked["優先ランク"].value_counts()
    rank_counts = {r:int(vc.get(r,0)) for r in ["S","A","B","C","D"]}

step3 = pd.DataFrame()
if not ranked.empty and "判定" in ranked.columns:
    step3 = ranked[ranked["判定"].astype(str).str.contains("Step3", na=False)].copy()

top = ranked.head(20).copy() if not ranked.empty else pd.DataFrame()
open_trades = pd.DataFrame()
if not trades.empty and "status" in trades.columns:
    open_trades = trades[trades["status"].astype(str).str.upper()=="OPEN"].copy()

def cards():
    vals = [
        ("Sランク", rank_counts.get("S",0)),
        ("Aランク", rank_counts.get("A",0)),
        ("Step3", len(step3)),
        ("仮想OPEN", len(open_trades)),
    ]
    return "".join(f'<div class="card"><div class="label">{esc(k)}</div><div class="num">{v}</div></div>' for k,v in vals)

def ranking_rows(df):
    if df.empty: return '<tr><td colspan="6">データなし</td></tr>'
    rows=[]
    for _, r in df.iterrows():
        rows.append(
            "<tr>"
            f"<td>{esc(r.get('優先ランク',''))}</td>"
            f"<td>{esc(r.get('急騰期待スコア',''))}</td>"
            f"<td>{esc(r.get('銘柄コード',''))}</td>"
            f"<td>{esc(r.get('銘柄名',''))}</td>"
            f"<td>{esc(r.get('株価',''))}</td>"
            f"<td>{esc(r.get('判定',''))}</td>"
            "</tr>"
        )
    return "".join(rows)

def trade_rows(df):
    if df.empty: return '<tr><td colspan="7">現在OPENの仮想取引はありません</td></tr>'
    rows=[]
    for _, r in df.iterrows():
        rows.append(
            "<tr>"
            f"<td>{esc(r.get('code',''))}</td>"
            f"<td>{esc(r.get('name',''))}</td>"
            f"<td>{esc(r.get('signal_date',''))}</td>"
            f"<td>{esc(r.get('entry_price',''))}</td>"
            f"<td>{esc(r.get('signal_score',''))}</td>"
            f"<td>{esc(r.get('max_gain_pct',''))}</td>"
            f"<td>{esc(r.get('max_drawdown_pct',''))}</td>"
            "</tr>"
        )
    return "".join(rows)

def summary_rows(df):
    if df.empty: return "<p>集計データなし</p>"
    return "".join(f"<div class='summary-row'><span>{esc(r.iloc[0])}</span><strong>{esc(r.iloc[1])}</strong></div>" for _,r in df.iterrows())

page = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>急騰株モニター</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue","Yu Gothic",sans-serif; margin:0; background:#f5f5f7; color:#111; }}
.wrap {{ max-width:980px; margin:auto; padding:16px; }}
h1 {{ font-size:24px; margin:4px 0; }}
.muted {{ color:#666; font-size:13px; }}
.cards {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px; margin:18px 0; }}
.card {{ background:white; border-radius:16px; padding:14px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
.label {{ font-size:13px; color:#666; }}
.num {{ font-size:30px; font-weight:800; margin-top:4px; }}
section {{ background:white; border-radius:16px; padding:14px; margin:14px 0; box-shadow:0 1px 4px rgba(0,0,0,.08); overflow:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; min-width:680px; }}
th,td {{ border-bottom:1px solid #ddd; padding:9px 7px; text-align:left; }}
th {{ position:sticky; top:0; background:white; }}
.summary-row {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #eee; }}
.badge {{ display:inline-block; border-radius:999px; padding:4px 9px; background:#eee; font-size:12px; }}
footer {{ font-size:12px; color:#777; padding:20px 2px 40px; }}
@media (min-width:700px) {{ .cards {{ grid-template-columns:repeat(4,1fr); }} }}
@media (prefers-color-scheme:dark) {{
 body {{ background:#000; color:#f5f5f7; }}
 .card,section {{ background:#1c1c1e; }}
 .label,.muted,footer {{ color:#aaa; }}
 th {{ background:#1c1c1e; }}
 th,td,.summary-row {{ border-color:#333; }}
 .badge {{ background:#333; }}
}}
</style>
</head>
<body>
<div class="wrap">
<h1>急騰株モニター</h1>
<div class="muted">最終更新: {updated}</div>
<div class="cards">{cards()}</div>

<section>
<h2>優先ランキング TOP20</h2>
<table>
<thead><tr><th>Rank</th><th>Score</th><th>Code</th><th>銘柄</th><th>株価</th><th>判定</th></tr></thead>
<tbody>{ranking_rows(top)}</tbody>
</table>
</section>

<section>
<h2>Step3候補</h2>
<table>
<thead><tr><th>Rank</th><th>Score</th><th>Code</th><th>銘柄</th><th>株価</th><th>判定</th></tr></thead>
<tbody>{ranking_rows(step3)}</tbody>
</table>
</section>

<section>
<h2>仮想取引 OPEN</h2>
<table>
<thead><tr><th>Code</th><th>銘柄</th><th>Signal</th><th>Entry</th><th>Score</th><th>最大上昇%</th><th>最大下落%</th></tr></thead>
<tbody>{trade_rows(open_trades)}</tbody>
</table>
</section>

<section>
<h2>検証成績</h2>
{summary_rows(summary)}
</section>

<footer>
これはスクリーニング・検証用の表示です。自動発注は行いません。
CSV原本はGitHubリポジトリの results/ に保存されます。
</footer>
</div>
</body>
</html>"""

(DOCS/"index.html").write_text(page, encoding="utf-8")
print(f"Generated: {DOCS/'index.html'}")
