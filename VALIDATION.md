# 検証記録 — 2026-09-17

環境: Windows / Python 3.12.11 / KiCad 10.0.5 / easyeda2kicad 1.0.1。

## 実データ

| 入力 | 変換結果 | 配置 |
|---|---|---|
| CSE `LIB_AP2151WG-7.zip` | AP2151WG-7、5ピン、1フットプリント、STP 1個 | C:/KiCadSync/Libraries/CSE |
| UL `ul_AP2171WG-7.zip` | AP2171WG-7、5ピン、3フットプリント、3D同梱なし | C:/KiCadSync/Libraries/UltraLibrarian |
| LCSC `C2864845` | TPS259474LRPWR、10ピン、1フットプリント、STEP/WRL各1個 | 作業用テストフォルダのみ |

CSE SHA-256: `2aef67e30419d23ae489e7c402a0bb03f8135c61ae32e1bbb0a5a54b0a723113`

UL SHA-256: `e3db3b61d0a7dc7394a858f744e4295715d084c8c635d305f4b80afbb3bd5525`

提供元ZIPはGitに含めません。CSE/ULの実データテストはローカルサンプルがないPCではskipされます。

## 自動テスト

`python -m unittest discover -s tests -v`: 10件成功。

- 引用・エスケープと未知のS式フィールドの保持
- 危険なファイル名・ZIPの親ディレクトリ参照を拒否
- 対象外ZIPの除外
- 途中の書き込み失敗で全ファイルを復元
- 前回の異常終了ジャーナルから復元
- 既存ライブラリテーブルを保持して登録
- ダウンロード完了待ち、監視履歴による再起動後の重複スキップ
- LCSC入力検証
- 実ZIP変換、2番目のシンボル追記、同一ZIPスキップ、異なる同名フットプリントの拒否

GUIのウィンドウ・ZIP/LCSC操作ボタンの生成、およびワーカースレッドの正常終了を確認。ZIP/番号入力からの処理本体はCLIで実データ検証済み。手動のGUIクリック操作やKiCad GUI上での外観確認は未実施です。

変換シンボルはKiCad自身による再読み込み・SVG出力を確認。CSE/ULのピン数は入力と一致。3Dの実物との寸法照合や部品の電気的動作検証は対象外です。
