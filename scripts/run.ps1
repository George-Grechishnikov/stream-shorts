<#
.SYNOPSIS
  Запуск stream-shorts: воркер очереди + веб-панель (http://127.0.0.1:8000). Ctrl+C — остановить оба.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run.ps1
  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Port 8080 -NoBrowser
#>
param(
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "нет .venv — сначала запусти scripts\setup_windows.ps1" }
if (-not (Test-Path config.yaml)) { throw "нет config.yaml — сначала запусти scripts\setup_windows.ps1" }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
# панель и воркер ходят на 127.0.0.1 — мимо прокси
$env:NO_PROXY = (@("127.0.0.1", "localhost", $env:NO_PROXY) | Where-Object { $_ }) -join ","

New-Item -ItemType Directory -Force (Join-Path $Root "work") | Out-Null
$log = Join-Path $Root "work\worker.log"
Write-Host "воркер: лог в $log" -ForegroundColor Cyan
$worker = Start-Process -FilePath $Py -ArgumentList "-m", "server.queue" -WorkingDirectory $Root `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err" -WindowStyle Hidden -PassThru

try {
    if (-not $NoBrowser) {
        Start-Job -ScriptBlock { param($u) Start-Sleep 2; Start-Process $u } -ArgumentList "http://127.0.0.1:$Port" | Out-Null
    }
    Write-Host "панель: http://127.0.0.1:$Port  (Ctrl+C — остановить)" -ForegroundColor Cyan
    & $Py -m server.app --port $Port
} finally {
    if ($worker -and -not $worker.HasExited) {
        # /T — вместе с шагом конвейера и его ffmpeg
        taskkill /T /F /PID $worker.Id | Out-Null
        Write-Host "воркер остановлен" -ForegroundColor Cyan
    }
}
