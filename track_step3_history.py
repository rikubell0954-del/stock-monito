# v3.3.1 緊急修正版

修正内容:
- Step2/3が入力0件・分析成功0件でも exit code 0 で終わる問題を修正。
- step2_3_result.csv を作れない場合は、その場で明示的に異常終了。
- screening_result.csv と step2_3_result.csv を次工程へ渡す前に存在・行数を検証。
- screener_health.json / step2_3_health.json を results/ に保存。
- v3.3のスコア検証機能、地合いフィルター、Step3履歴、EDINET導線は維持。
- 自動実行は平日17:30 JST。

今回のエラー:
Run ranking and virtual trades で
ERROR: step2_3_result.csv not found
となったのは、Step2/3が出力を作らず正常終了していたため。
v3.3.1ではランキングまで進む前に原因箇所で停止し、明確なエラーを表示します。
