$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python環境の作成に失敗しました' }
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依存パッケージの導入に失敗しました' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'KiCad Library Loader.lnk'))
    $shortcut.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'loader.py') + '" gui'
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = 'CSE・UltraLibrarian・LCSCをKiCad 10ライブラリに登録'
    $shortcut.Save()
} finally {
    Pop-Location
}
