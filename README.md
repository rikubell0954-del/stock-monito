# 急騰株クラウドモニター v3.1（APIなし）

## 今回の変更
- OpenAI API連携を完全に削除
- Step3履歴機能はそのまま継続
- iPhoneダッシュボードに「ChatGPTへ貼り付け」欄を追加
- ワンタップで以下をコピー可能
  - Sランク銘柄コード
  - 今日の新規Step3銘柄コード
  - 現在のStep3銘柄コード

コピー内容は銘柄コードだけを改行区切りにしています。

例:
3958
7928
4447

これをそのままChatGPTへ貼り付けて企業分析を依頼できます。

## v3/v2から更新するファイル
以下をGitHubへ上書きしてください。
- `.github/workflows/stock-monitor.yml`
- `requirements.txt`
- `build_mobile_report.py`
- `README.md`

v2から直接更新する場合は追加:
- `track_step3_history.py`

## 不要になったもの
以前v3を導入済みの場合、以下は削除して構いません。
- `analyze_s_rank_openai.py`
- `ai_analysis/` フォルダ

GitHub Secretsに `OPENAI_API_KEY` を登録していた場合も削除して構いません。
v3.1では一切使用しません。

## 自動実行
平日16:30 JST。

## Step3履歴
`results/step3_history.csv` に永続保存します。
初回突入・継続・離脱・再突入を追跡できます。
