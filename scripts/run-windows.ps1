$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if (-not (Test-Path .\.venv\Scripts\python.exe)) { throw 'First run scripts/setup-windows.ps1' }
& .\.venv\Scripts\python.exe main.py --windowed
