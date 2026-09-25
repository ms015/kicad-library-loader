$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $runtime)) {
    throw '先に Setup.ps1 を実行してください。'
}
Start-Process -FilePath $runtime -ArgumentList ('"' + (Join-Path $PSScriptRoot 'loader.py') + '" gui') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
