# KiCad Library Loader

## **このソフトウェアはAIによって生成されています**

本ソフトウェアのコード・ドキュメントはAIによって生成されたものです。動作や変換結果の正確性は保証しません。使用前に内容を確認し、取り込んだCADデータはメーカーのデータシートと照合してください。

Windows用の自作ライブラリローダー。CSE / UltraLibrarian / SnapEDAのWebダウンロードZIPと、LCSC番号から取得した部品をKiCad 10の共通ライブラリ `Parts` に登録します。追加元は部品属性と取り込み履歴に保持します。

## 起動

初回はPowerShellで `./Setup.ps1`（uvが必要）。以後、デスクトップの **KiCad Library Loader** を開きます。`Start.ps1` でも起動できます。

- 起動時に `~/Downloads` とサブフォルダの既存ZIPを記録し、その後に追加されたZIPだけを自動監視します。既存ZIPを取り込む場合は「ZIPを選んで取り込む」を使用してください。
- 既定では0.5秒ごとに確認し、サイズ・更新時刻が1.5秒変化しない完全なZIPを内容から判別して取り込みます。ファイル名の接頭辞には依存しません。
- 「ZIPを選んで取り込む」で手動指定・エラー後の再試行。登録済みZIPも位置合わせ画面を開き直せます。
- LCSC番号欄に `C2040` のように入力して「取得して登録」。easyeda2kicad 1.0.1を使用します。
- 閉じると監視を終了。位置合わせ中は取り込みをキャンセルし、レンダリング中なら終了を待って閉じます。Windows自動起動は設定しません。

## 取り込み前の3D位置合わせ

GUIで3Dモデル付き部品を取り込むと、自動監視・手動ZIP・LCSCのいずれも確定前に確認画面が開きます。3Dモデルがない部品は従来どおり登録します。

1. モデルとフットプリントの画像を確認します。
2. モデル原点を`フットプリント原点`、`1番ピン位置`、`外形中心`から選びます。必要なら`手動`でXYZ移動量（mm）を調整できます。
3. XYZの角度を入力して「プレビュー更新」。各軸の「+90°」ボタンでも回転できます。視点は`斜め`、`上`、`正面`、`右`のボタンで切り替えます。
4. 「この位置で取り込む」で確定します。角度変更後は最新画像が表示されるまで確定できません。複数フットプリントがある場合は上の一覧からそれぞれを確認します。

「キャンセル」はライブラリに書き込みません。倍率は元データを保持します。モデルの形状自体は変更せず、フットプリント内の回転・移動量だけを保存します。調整値はimportsの記録にも残ります。登録済みZIPを手動で選び直した場合は、元ZIPの状態から調整し、既存部品との差分があれば別名登録します。

KiCad付属のPythonで小さな試験基板を作り、KiCad CLIのbasic品質・560×480ピクセルのPNGを表示します。専用CADエンジンや新しいPython依存パッケージは不要です。CD74HC4067の実測では1枚約1.9秒でした（モデル・PCに依存します）。これはボタン更新式のプレビューで、ドラッグ操作するリアルタイム3Dビューアではありません。

CLIの `zip` / `lcsc` / `watch` は従来の無人取り込みです。位置合わせ画面を使うときはGUI（通常起動、または `loader.py gui`）を使用してください。モデル参照先を解決・描画できない場合は確定できません。

## 保存形式

既定の `C:/KiCadSync/Libraries/Parts` に、すべての追加元の部品をまとめて配置します。

```text
Parts/
  Parts.kicad_symdir/ # KiCad 10で変換した部品ごとのシンボル
  Parts.pretty/      # フットプリント（ULのL/M等の別形状も保持）
  Parts.3dshapes/    # 同梱モデル。姿勢・倍率を保持
  imports/           # 追加元別の履歴。出典、SHA-256、変換時刻、警告
```

`Tables/sym-lib-table` と `Tables/fp-lib-table` に `Parts` を登録します。既存のSyncedテーブル構成と `${KICAD_SYNC_ROOT}` 環境変数が設定済みのPCを対象とします。別PCではこれらの設定が必要です。KiCadを起動中に新規ライブラリを登録した場合は再起動してください。

旧サービス別ライブラリの初回統合は `python migrate_unified.py` で実行します。元フォルダを残し、全ファイルを試験領域で統合・参照確認してから書き込み、旧4登録をPartsに置き換えます。テーブルは通常のトランザクションバックアップに保存します。Partsがすでにある場合は停止します。既存プロジェクトで旧ライブラリ名を参照している場合は、事前に移行方法を確認してください。SamacSysとPersonalはこの移行の対象外です。

シンボルのFootprint参照と3Dパスを配置先へ修正。元のメーカー・販売店フィールドを保持し、MPN/CAD Source等を補います。モデルが同梱されない場合は警告し、存在しない参照は除去します。3D形状の作成・推測はしません。

追加元はシンボルのカスタム属性 `CAD Source` で確認できます（CSE / UltraLibrarian / SnapMagic＝SnapEDA / LCSC）。`CAD Source ID` には取得できた部品URLやLCSC番号、`MPN` には元のメーカー型番を保存します。これらは図面上では非表示の属性です。元ZIP名とSHA-256も `imports/<追加元>/` のJSONに記録します。

SnapEDAは出典メタデータから判別し、**1部品の個別ZIPのみ**取り込みます。SnapEDA-Library.zip等の一括ZIPは対象外です。ZIP自体を改名しても中身から除外します。CSE・UltraLibrarianの対応範囲は変更しません。メーカー・型番・出典フィールドを保持します。STEP参照がない場合、一意に対応するモデルだけを原点・回転0°・倍率1でリンクし、位置合わせ未確認の警告を残します。

同名で差分がある場合は、`AP21510FM-7__012345abcdef` のように内容由来の12桁ハッシュを付けて別名登録します。関連するシンボル・フットプリント・3Dモデルを同じ接尾辞でまとめ、参照先も更新します。MPN等の元情報は保持します。同じ変更版は同じ名前を再利用します。ピンの表示・電気的属性を勝手に変更せず、既存部品も上書きしません。この動作は全サービス共通です。[AP21510FM-7の比較記録](SNAPEDA-COMPARISON.md)も参照してください。

## 設定・CLI

`config.json`（Git管理対象外）に既定値の上書きを指定できます。

```json
{
  "watch_folder": "C:/Users/<username>/Downloads",
  "library_root": "C:/KiCadSync/Libraries",
  "kicad_cli": "C:/Program Files (user)/KiCad/10.0/bin/kicad-cli.exe",
  "stable_seconds": 1.5,
  "poll_seconds": 0.5
}
```

出力ルートを変更する場合、KiCad側のKICAD_SYNC_ROOT/Librariesと一致させてください。プレビューはkicad-cli.exeと同じフォルダのpython.exeを使用します。必要なら `kicad_python` でそのパスを指定できます。

```powershell
.venv/Scripts/python.exe loader.py zip C:/path/component.zip
.venv/Scripts/python.exe loader.py lcsc C2040
.venv/Scripts/python.exe loader.py watch
.venv/Scripts/python.exe loader.py --config test-settings.json zip C:/path/component.zip
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

## 重複・失敗時

- 自動監視・CLIでは同じZIPのSHA-256、同じLCSC番号は取り込み済みとしてスキップします。GUIの手動ZIP指定は再確認できます。
- 同名で異なる部品・モデルは関連ファイルごと別名登録します。既存部品を置き換える自動更新はしません。
- 変換・参照検証・KiCad SVG出力が成功してから配置。書き込み失敗は巻き戻します。途中終了の記録は次回インポート時に復元します。
- `%LOCALAPPDATA%/KiCadLibraryLoader` に元ZIP、テーブル変更前バックアップ、監視履歴を保存します。
- 自動監視で失敗したファイルはログに表示し、変更されるまで再試行しません。手動ZIP指定で再試行できます。
- 対応形式を追加した版では監視履歴を再評価し、以前対象外だったZIPも取り込みます。アプリを起動中の場合は再起動してください。
- 同一PCでの書き込みは排他制御します。Syncthing経由の複数PC同時書き込みは排他できないため、取り込みは1台で行ってください。
- ZIP内の実行ファイル・マクロは実行しません。パストラバーサル、過大展開、曖昧な同名データを拒否します。

## 対応範囲

CSEのKiCad同梱ZIP、ULのKiCADv6等のKiCad書き出しZIP、SnapEDAのKiCad個別ZIPを対象とします。KiCad形式を含まないZIP、古い`.mod`だけのZIP、パスワード付きZIPには未対応。提供元CADのピン配置や電気的正確さは変換ソフトでは保証できないため、設計前にデータシートと照合してください。

変換はインストール済みKiCad 10 CLIを使用。ZIP変換コードは本リポジトリ内にあり、Import-LIB等のプラグインには依存しません。

参考: [KiCad CLI](https://docs.kicad.org/10.0/en/cli/cli.html)、[easyeda2kicad](https://github.com/uPesy/easyeda2kicad.py)

## ライセンス・第三者データ

本リポジトリの独自コード・ドキュメントには **BSD Zero Clause License（0BSD）** を適用します。[LICENSE](LICENSE) が正式な条件です。商用利用、改変、複製、再配布を許可し、著作権表示・ライセンス本文の保持やソースコード公開を条件としません。無保証で提供します。

このライセンスは、外部ソフトウェアや取り込むCADデータの権利を変更しません。`easyeda2kicad 1.0.1` はAGPLv3の外部プログラムで、`Setup.ps1` が利用者の環境に別途インストールし、別プロセスで呼び出します。本リポジトリにその本体や配布パッケージは含めません。

LCSC/EasyEDAおよび各CADデータ提供元の利用については、各サービスの利用規約と各データのライセンスに従ってください。変換・保存しても再配布の権利が新たに得られるわけではありません。第三者CADデータや取得済みZIPは本リポジトリに含めません。詳細は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。
