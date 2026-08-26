# iPhone向け 急騰株クラウドモニター

## できること
- GitHub Actions上で `stock_screener.py` → `step2_3_monitor.py` → `stock_pipeline_enhancer_cloud.py` を順番に実行
- 平日16:30（日本時間）に自動実行
- iPhoneから GitHub → Actions → Stock Monitor → Run workflow で手動実行
- `results/` にCSVと自己診断ログを保存
- `virtual_trade_log.csv` を次回へ引き継ぎ、検証履歴を蓄積
- `docs/index.html` をiPhone向け画面として生成
- GitHub Pagesへ直接デプロイし、Safariから結果を閲覧可能

## 初回セットアップ
1. GitHubで新しいリポジトリを作成（例: `stock-monitor`）。
2. このZIPの中身をリポジトリ直下へアップロード。
3. Settings → Actions → General → Workflow permissions を `Read and write permissions` に設定。
4. Settings → Pages → Build and deployment → Source を `GitHub Actions` に設定。
5. Actions → Stock Monitor → `Run workflow` を実行。
6. 完了後、Actions実行画面の `deploy-pages` に表示されるURLを開く。

## iPhoneでの手動実行
GitHubアプリまたはSafari:
1. リポジトリを開く
2. Actions
3. Stock Monitor
4. Run workflow
5. Run workflow

## iPhoneで結果を見る
GitHub PagesのURLをSafariで開いてください。
Safariの共有 →「ホーム画面に追加」にすると、ほぼアプリ感覚で確認できます。

画面には:
- S/Aランク件数
- Step3件数
- 仮想取引OPEN件数
- 優先ランキングTOP20
- Step3候補一覧
- 仮想取引OPEN
- 検証成績

を表示します。

## 出力ファイル
`results/`
- screening_result.csv
- step2_3_result.csv
- ranked_candidates.csv
- virtual_trade_log.csv
- performance_summary.csv
- enhancer_diagnostics.txt

## 注意
- 自動発注は行いません。
- Yahoo Finance/JPX/GitHub側の一時的な通信制限で取得に失敗する可能性があります。
- `results/enhancer_diagnostics.txt` の self_checks がすべて PASS か確認してください。
- Pages公開URLの公開範囲はGitHubプラン/リポジトリ設定に依存します。公開したくない情報を追加しないでください。
