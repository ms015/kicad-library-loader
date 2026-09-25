# Third-party notices

本リポジトリの0BSDライセンスは独自コード・ドキュメントに適用されます。以下の外部ソフトウェアやCADデータには、それぞれのライセンス・利用条件が適用されます。

## easyeda2kicad 1.0.1

- Author: uPesy and contributors
- License: GNU Affero General Public License v3 (AGPLv3)
- [対象バージョンの配布・ソースアーカイブ](https://pypi.org/project/easyeda2kicad/1.0.1/)
- [ソースリポジトリ](https://github.com/uPesy/easyeda2kicad.py)
- [AGPLv3本文](https://www.gnu.org/licenses/agpl-3.0.html)

easyeda2kicad is not included in this repository. It is installed separately
from PyPI by Setup.ps1 and invoked as an external command
(`python -m easyeda2kicad`). Its source code is not copied into this project,
and its Python internal API is not imported by this project.

この通知はeasyeda2kicadのライセンスを変更しません。将来その本体、仮想環境、wheel、または本体を含む実行ファイルを配布する場合、この通知とリンクだけでは配布条件を満たしません。配布するバージョンの著作権表示・ライセンス本文を維持し、対応ソースの提供など適用されるAGPLの条件を満たしてください。

## その他の実行環境

Python、uv、KiCadは利用者が用意する外部ソフトウェアです。本リポジトリには同梱しません。KiCad付属のPython・pcbnew・CLIを使用する機能があります。それぞれの配布元のライセンスが適用されます。

## CADデータとサービス

CSE、Ultra Librarian、SnapEDA / SnapMagic、LCSC / EasyEDA、SamacSys等のシンボル、フットプリント、3Dモデル、ダウンロードZIPは本プロジェクトの0BSDライセンスの対象外です。本リポジトリにはこれらの第三者CADデータを含めません。

利用者自身が取得するデータには提供元の利用規約と個別データのライセンスが適用されます。帰属表示などの条件がある場合は維持してください。本ソフトウェアで変換・統合しても、データを公開・再配布する権利を新たに付与するものではありません。各社・各サービス名は対応形式や出典の説明のために使用しており、公式製品・提携・推奨を示すものではありません。

ローカルの実データを使用する統合テストは、利用者の環境にサンプルがある場合だけ実行されます。テスト用ZIPも配布対象に含めないでください。
