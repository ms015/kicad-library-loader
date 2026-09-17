# KiCad Library Loader

Windows用の自作ライブラリローダー。CSE / UltraLibrarianのWebダウンロードZIPと、LCSC番号から取得した部品をKiCad 10の独立したライブラリに登録します。

## 起動

初回はPowerShellで `./Setup.ps1`（uvが必要）。以後、デスクトップの **KiCad Library Loader** を開きます。`Start.ps1` でも起動できます。

- 起動中は `~/Downloads` とサブフォルダを監視。既存ZIPも対象です。
- 6秒間サイズ・更新時刻が変化しないZIPを内容から判別して取り込みます。ファイル名の接頭辞には依存しません。
- 「ZIPを選んで取り込む」で手動指定・エラー後の再試行。
- LCSC番号欄に `C2040` のように入力して「取得して登録」。easyeda2kicad 1.0.1を使用します。
- 閉じると監視を終了。処理中の場合は変換完了後に終了します。Windows自動起動は設定しません。

## 保存形式

既定の `C:/KiCadSync/Libraries` の下に、`CSE`、`UltraLibrarian`、`LCSC` を作成します。各サービスの下に次を配置します。

```text
CSE/
  CSE.kicad_symdir/   # KiCad 10で変換した部品ごとのシンボル
  CSE.pretty/         # フットプリント（ULのL/M等の別形状も保持）
  CSE.3dshapes/       # 同梱モデル。姿勢・倍率を保持
  imports/           # 出典、SHA-256、変換時刻、警告
```

`Tables/sym-lib-table` と `Tables/fp-lib-table` にサービス別の登録を追加します。既存のSyncedテーブル構成と `${KICAD_SYNC_ROOT}` 環境変数が設定済みのPCを対象とします。別PCではこれらの設定が必要です。KiCadを起動中に新規ライブラリを登録した場合は再起動してください。

シンボルのFootprint参照と3Dパスを配置先へ修正。元のメーカー・販売店フィールドを保持し、MPN/CAD Source等を補います。モデルが同梱されない場合は警告し、存在しない参照は除去します。3D形状の作成・推測はしません。

## 設定・CLI

`config.json`（Git管理対象外）に既定値の上書きを指定できます。

```json
{
  "watch_folder": "C:/Users/<username>/Downloads",
  "library_root": "C:/KiCadSync/Libraries",
  "kicad_cli": "C:/Program Files (user)/KiCad/10.0/bin/kicad-cli.exe",
  "stable_seconds": 6,
  "poll_seconds": 3
}
```

出力ルートを変更する場合、KiCad側のKICAD_SYNC_ROOT/Librariesと一致させてください。

```powershell
.venv/Scripts/python.exe loader.py zip C:/path/component.zip
.venv/Scripts/python.exe loader.py lcsc C2040
.venv/Scripts/python.exe loader.py watch
.venv/Scripts/python.exe loader.py --config test-settings.json zip C:/path/component.zip
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

## 重複・失敗時

- 同じZIPのSHA-256、同じLCSC番号は取り込み済みとしてスキップします。
- 同名で異なる部品・モデルは上書きせず、そのインポート全体を失敗にします。自動更新は未対応です。
- 変換・参照検証・KiCad SVG出力が成功してから配置。書き込み失敗は巻き戻します。途中終了の記録は次回インポート時に復元します。
- `%LOCALAPPDATA%/KiCadLibraryLoader` に元ZIP、テーブル変更前バックアップ、監視履歴を保存します。
- 自動監視で失敗したファイルはログに表示し、変更されるまで再試行しません。手動ZIP指定で再試行できます。
- 同一PCでの書き込みは排他制御します。Syncthing経由の複数PC同時書き込みは排他できないため、取り込みは1台で行ってください。
- ZIP内の実行ファイル・マクロは実行しません。パストラバーサル、過大展開、曖昧な同名データを拒否します。

## 対応範囲

CSEのKiCad同梱ZIP、およびULのKiCADv6等のKiCad書き出しZIPを対象とします。KiCad形式を含まないZIP、古い`.mod`だけのZIP、パスワード付きZIPには未対応。提供元CADのピン配置や電気的正確さは変換ソフトでは保証できないため、設計前にデータシートと照合してください。

変換はインストール済みKiCad 10 CLIを使用。ZIP変換コードは本リポジトリ内にあり、Import-LIB等のプラグインには依存しません。

参考: [KiCad CLI](https://docs.kicad.org/10.0/en/cli/cli.html)、[easyeda2kicad](https://github.com/uPesy/easyeda2kicad.py)
