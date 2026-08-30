# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import html
import re
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
    for enc in ("utf-8-sig","cp932","utf-8"):
        try:
            return pd.read_csv(p, encoding=enc)
        except Exception:
            pass
    return pd.DataFrame()

def esc(v):
    if pd.isna(v): return ""
    return html.escape(str(v))

def truth(v):
    return str(v).strip().lower() in {"true","1","yes","y"}

def norm_code(v):
    s = str(v).strip().replace(".T","")
    s = re.sub(r"\.0$","",s)
    m = re.search(r"\d{4}",s)
    return m.group(0) if m else s

ranked = read_csv("ranked_candidates.csv")
trades = read_csv("virtual_trade_log.csv")
summary = read_csv("performance_summary.csv")
history = read_csv("step3_history.csv")

now = datetime.now(TOKYO)
today = now.strftime("%Y-%m-%d")
updated = now.strftime("%Y-%m-%d %H:%M JST")

rank_counts = {}
if not ranked.empty and "優先ランク" in ranked.columns:
    vc = ranked["優先ランク"].value_counts()
    rank_counts = {r:int(vc.get(r,0)) for r in ["S","A","B","C","D"]}

step3 = ranked[ranked["判定"].astype(str).str.contains("Step3",na=False)].copy() if not ranked.empty else pd.DataFrame()
s_rank = ranked[ranked["優先ランク"].astype(str).eq("S")].copy() if not ranked.empty else pd.DataFrame()
top = ranked.head(20).copy() if not ranked.empty else pd.DataFrame()

active_hist = pd.DataFrame()
new_step3 = pd.DataFrame()
continuing_step3 = pd.DataFrame()
if not history.empty:
    active_hist = history[history["active"].map(truth)].copy()
    new_step3 = active_hist[active_hist["first_step3_date"].astype(str).eq(today)].copy()
    continuing_step3 = active_hist[~active_hist["first_step3_date"].astype(str).eq(today)].copy()

open_trades = trades[trades["status"].astype(str).str.upper().eq("OPEN")].copy() if not trades.empty else pd.DataFrame()

s_codes = "\n".join(s_rank["銘柄コード"].map(norm_code).tolist()) if not s_rank.empty else ""
step3_codes = "\n".join(step3["銘柄コード"].map(norm_code).tolist()) if not step3.empty else ""
new_step3_codes = "\n".join(new_step3["code"].map(norm_code).tolist()) if not new_step3.empty else ""

def cards():
    vals = [
        ("Sランク", rank_counts.get("S",0)),
        ("新規Step3", len(new_step3)),
        ("Step3継続", len(continuing_step3)),
        ("仮想OPEN", len(open_trades)),
    ]
    return "".join(f'<div class="card"><div class="label">{esc(k)}</div><div class="num">{v}</div></div>' for k,v in vals)

def ranking_rows(df):
    if df.empty: return '<tr><td colspan="6">データなし</td></tr>'
    rows=[]
    for _,r in df.iterrows():
        rows.append("<tr>"
          f"<td><b>{esc(r.get('優先ランク',''))}</b></td>"
          f"<td>{esc(r.get('急騰期待スコア',''))}</td>"
          f"<td>{esc(norm_code(r.get('銘柄コード','')))}</td>"
          f"<td>{esc(r.get('銘柄名',''))}</td>"
          f"<td>{esc(r.get('株価',''))}</td>"
          f"<td>{esc(r.get('判定',''))}</td></tr>")
    return "".join(rows)

def hist_rows(df):
    if df.empty: return '<tr><td colspan="8">該当銘柄なし</td></tr>'
    rows=[]
    for _,r in df.iterrows():
        re_no = pd.to_numeric(pd.Series([r.get("reentry_no",0)]),errors="coerce").fillna(0).iloc[0]
        badge = "再突入" if float(re_no) > 0 else "初回"
        rows.append("<tr>"
          f"<td><span class='pill'>{badge}</span></td>"
          f"<td>{esc(r.get('code',''))}</td>"
          f"<td>{esc(r.get('name',''))}</td>"
          f"<td>{esc(r.get('first_step3_date',''))}</td>"
          f"<td>{esc(r.get('days_seen',''))}</td>"
          f"<td>{esc(r.get('entry_price',''))}</td>"
          f"<td>{esc(r.get('latest_price',''))}</td>"
          f"<td>{esc(r.get('latest_score',''))}</td></tr>")
    return "".join(rows)

def trade_rows(df):
    if df.empty: return '<tr><td colspan="7">現在OPENの仮想取引はありません</td></tr>'
    rows=[]
    for _,r in df.iterrows():
        rows.append("<tr>"
          f"<td>{esc(r.get('code',''))}</td><td>{esc(r.get('name',''))}</td>"
          f"<td>{esc(r.get('signal_date',''))}</td><td>{esc(r.get('entry_price',''))}</td>"
          f"<td>{esc(r.get('signal_score',''))}</td><td>{esc(r.get('max_gain_pct',''))}</td>"
          f"<td>{esc(r.get('max_drawdown_pct',''))}</td></tr>")
    return "".join(rows)

def summary_rows(df):
    if df.empty: return "<p>集計データなし</p>"
    return "".join(f"<div class='summary-row'><span>{esc(r.iloc[0])}</span><strong>{esc(r.iloc[1])}</strong></div>" for _,r in df.iterrows())

def copy_box(title, textarea_id, text, note):
    disabled = " disabled" if not text else ""
    button_label = "該当銘柄なし" if not text else "コードをコピー"
    return f"""
    <div class="copy-box">
      <div class="copy-head"><div><b>{esc(title)}</b><div class="muted">{esc(note)}</div></div>
      <button class="copy-btn" onclick="copyCodes('{textarea_id}', this)"{disabled}>{button_label}</button></div>
      <textarea id="{textarea_id}" readonly>{esc(text)}</textarea>
    </div>
    """

copy_sections = (
    copy_box("Sランク銘柄コード", "sCodes", s_codes, "ChatGPT企業分析用。コードのみ、改行区切り。")
    + copy_box("今日の新規Step3コード", "newStep3Codes", new_step3_codes, "今日初めてStep3へ入った銘柄だけ。")
    + copy_box("現在のStep3銘柄コード", "step3Codes", step3_codes, "現在Step3判定の全銘柄。")
)

page = f"""<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>急騰株モニター</title>
<style>
:root{{color-scheme:light dark}}body{{font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue","Yu Gothic",sans-serif;margin:0;background:#f5f5f7;color:#111}}
.wrap{{max-width:1040px;margin:auto;padding:16px}}h1{{font-size:25px;margin:4px 0}}h2{{font-size:20px}}
.muted{{color:#666;font-size:13px}}.cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:18px 0}}
.card,section,.copy-box{{background:white;border-radius:16px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}.card{{padding:14px}}.label{{font-size:13px;color:#666}}.num{{font-size:30px;font-weight:800;margin-top:4px}}
section{{padding:14px;margin:14px 0;overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px;min-width:720px}}th,td{{border-bottom:1px solid #ddd;padding:9px 7px;text-align:left}}th{{position:sticky;top:0;background:white}}
.pill{{display:inline-block;padding:3px 7px;border-radius:999px;background:#eee;font-size:11px}}.summary-row{{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #eee}}
.copy-box{{padding:14px;margin:10px 0}}.copy-head{{display:flex;gap:10px;justify-content:space-between;align-items:center}}.copy-btn{{border:0;border-radius:12px;padding:11px 14px;font-weight:700;background:#111;color:white;white-space:nowrap}}
.copy-btn:disabled{{opacity:.4}}textarea{{width:100%;box-sizing:border-box;margin-top:10px;min-height:90px;border:1px solid #ccc;border-radius:12px;padding:12px;font-size:17px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;line-height:1.5;background:transparent;color:inherit}}
footer{{font-size:12px;color:#777;padding:20px 2px 40px}}
@media(min-width:700px){{.cards{{grid-template-columns:repeat(4,1fr)}}}}
@media(prefers-color-scheme:dark){{body{{background:#000;color:#f5f5f7}}.card,section,.copy-box{{background:#1c1c1e}}.label,.muted,footer{{color:#aaa}}th{{background:#1c1c1e}}th,td,.summary-row{{border-color:#333}}.pill{{background:#333}}.copy-btn{{background:#f5f5f7;color:#111}}textarea{{border-color:#444}}}}
</style>
<script>
async function copyCodes(id, btn) {{
  const el = document.getElementById(id);
  const text = el.value.trim();
  if (!text) return;
  try {{
    await navigator.clipboard.writeText(text);
  }} catch(e) {{
    el.focus(); el.select(); document.execCommand('copy'); el.setSelectionRange(0,0);
  }}
  const old = btn.textContent;
  btn.textContent = 'コピーしました ✓';
  setTimeout(()=>btn.textContent=old, 1600);
}}
</script>
</head><body><div class="wrap">
<h1>急騰株モニター</h1><div class="muted">最終更新: {updated}</div>
<div class="cards">{cards()}</div>

<section><h2>📋 ChatGPTへ貼り付け</h2>{copy_sections}</section>

<section><h2>🆕 今日Step3に突入</h2>
<table><thead><tr><th>区分</th><th>Code</th><th>銘柄</th><th>突入日</th><th>日数</th><th>突入価格</th><th>現在価格</th><th>Score</th></tr></thead>
<tbody>{hist_rows(new_step3)}</tbody></table></section>

<section><h2>🔁 Step3継続監視</h2>
<table><thead><tr><th>区分</th><th>Code</th><th>銘柄</th><th>突入日</th><th>日数</th><th>突入価格</th><th>現在価格</th><th>Score</th></tr></thead>
<tbody>{hist_rows(continuing_step3)}</tbody></table></section>

<section><h2>優先ランキング TOP20</h2><table>
<thead><tr><th>Rank</th><th>Score</th><th>Code</th><th>銘柄</th><th>株価</th><th>判定</th></tr></thead>
<tbody>{ranking_rows(top)}</tbody></table></section>

<section><h2>現在のStep3候補</h2><table>
<thead><tr><th>Rank</th><th>Score</th><th>Code</th><th>銘柄</th><th>株価</th><th>判定</th></tr></thead>
<tbody>{ranking_rows(step3)}</tbody></table></section>

<section><h2>仮想取引 OPEN</h2><table>
<thead><tr><th>Code</th><th>銘柄</th><th>Signal</th><th>Entry</th><th>Score</th><th>最大上昇%</th><th>最大下落%</th></tr></thead>
<tbody>{trade_rows(open_trades)}</tbody></table></section>

<section><h2>検証成績</h2>{summary_rows(summary)}</section>
<footer>スクリーニング・検証用です。自動発注は行いません。Sランク等のコードは上のコピーボタンからChatGPTへ貼り付けできます。</footer>
</div></body></html>"""
(DOCS/"index.html").write_text(page, encoding="utf-8")
print(f"Generated: {DOCS/'index.html'}")
