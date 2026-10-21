$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python installation / venv failed.' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Pygame installation failed.' }
Write-Host 'Setup finished. Run: .\.venv\Scripts\python.exe main.py --windowed'
