<#
.SYNOPSIS
  Установка stream-shorts на Windows: Python, ffmpeg, venv, зависимости, CUDA для whisper,
  TwitchDownloaderCLI, config.yaml и модель whisper.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -NoProxy -Cpu

.PARAMETER NoProxy
  Качать мимо HTTP(S)_PROXY (только для этого процесса). Нужен, если локальный прокси
  рвёт соединения с PyPI или Hugging Face.
.PARAMETER Cpu
  Без NVIDIA: не ставить CUDA-колёса, в config.yaml — whisper на CPU и libx264.
.PARAMETER Model
  Модель faster-whisper (large-v3, medium, small, ...). По умолчанию large-v3.
.PARAMETER SkipModel
  Не качать модель заранее (faster-whisper скачает её сам при первом запуске).
#>
param(
    [switch]$NoProxy,
    [switch]$Cpu,
    [string]$Model = "large-v3",
    [switch]$SkipModel
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

if ($NoProxy) {
    $env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; $env:http_proxy = ""; $env:https_proxy = ""; $env:NO_PROXY = "*"
    $CurlProxy = @("--noproxy", "*")
    Ok "прокси для этого процесса отключён"
} else {
    $CurlProxy = @()
}

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

function Find-Python {
    # py-лаунчер: берём самый новый 3.11+
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $vers = & py -0p 2>$null | ForEach-Object { if ($_ -match '-V:(3\.(\d+))\S*\s+\*?\s*(.+python\.exe)') { [pscustomobject]@{ Minor = [int]$Matches[2]; Exe = $Matches[3].Trim() } } }
        $best = $vers | Where-Object Minor -ge 11 | Sort-Object Minor -Descending | Select-Object -First 1
        if ($best) { return $best.Exe }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $v = & $py.Source -c "import sys; print(sys.version_info[1] if sys.version_info[0] == 3 else 0)"
        if ([int]$v -ge 11) { return $py.Source }
    }
    return $null
}

# ---------------------------------------------------------------- Python
Step "Python 3.11+"
$Python = Find-Python
if (-not $Python) {
    Warn "не найден — ставлю Python 3.11 через winget"
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $Python = Find-Python
    if (-not $Python) { throw "Python 3.11+ не найден после установки. Перезапусти терминал и запусти скрипт снова." }
}
Ok "$Python ($(& $Python --version))"

# ---------------------------------------------------------------- ffmpeg
Step "ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Warn "не найден — ставлю через winget (Gyan.FFmpeg)"
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Refresh-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "ffmpeg не найден после установки. Перезапусти терминал и запусти скрипт снова." }
}
Ok ((ffmpeg -hide_banner -version | Select-Object -First 1))

# ---------------------------------------------------------------- venv + зависимости
Step "venv и зависимости"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    & $Python -m venv .venv
    Ok "создан .venv"
}
& $VenvPy -m pip install -q --upgrade pip
& $VenvPy -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install не прошёл. Если висит на Retrying/ProxyError — запусти с -NoProxy." }
Ok "requirements.txt установлены"

$HasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if (-not $Cpu -and $HasNvidia) {
    & $VenvPy -m pip install -q nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
    if ($LASTEXITCODE -ne 0) { throw "не поставились CUDA-колёса (nvidia-cublas-cu12, nvidia-cudnn-cu12)" }
    Ok "CUDA-библиотеки для faster-whisper (cuBLAS, cuDNN 9)"
} elseif (-not $Cpu) {
    Warn "nvidia-smi не найден — считаю, что NVIDIA нет; whisper пойдёт на CPU"
    $Cpu = $true
}

# ---------------------------------------------------------------- TwitchDownloaderCLI
Step "TwitchDownloaderCLI (чат)"
$Tdl = Join-Path $Root "tools\TwitchDownloaderCLI.exe"
function Tdl-Version { (cmd /c "`"$Tdl`" --version 2>&1") -join " " }  # версию печатает в stderr
if (Test-Path $Tdl) {
    Ok "уже есть: $(Tdl-Version)"
} else {
    New-Item -ItemType Directory -Force (Join-Path $Root "tools") | Out-Null
    $rel = Invoke-RestMethod "https://api.github.com/repos/lay295/TwitchDownloader/releases/latest"
    $asset = $rel.assets | Where-Object { $_.name -match 'CLI.*Windows.*x64\.zip$' } | Select-Object -First 1
    if (-not $asset) { throw "в релизе $($rel.tag_name) нет сборки CLI для Windows x64" }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("tdl_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory $tmp | Out-Null
    curl.exe -sSL @CurlProxy -o "$tmp\cli.zip" $asset.browser_download_url
    if ($LASTEXITCODE -ne 0) { throw "не скачался $($asset.name)" }
    Expand-Archive "$tmp\cli.zip" -DestinationPath "$tmp\x" -Force
    Copy-Item "$tmp\x\TwitchDownloaderCLI.exe" $Tdl
    Remove-Item $tmp -Recurse -Force
    Ok "$($asset.name) → tools\ ($(Tdl-Version))"
}

# ---------------------------------------------------------------- модель whisper
$ModelDir = Join-Path $env:USERPROFILE ".cache\whisper\faster-whisper-$Model"
if (-not $SkipModel) {
    Step "модель faster-whisper-$Model"
    $repo = "https://huggingface.co/Systran/faster-whisper-$Model/resolve/main"
    New-Item -ItemType Directory -Force $ModelDir | Out-Null
    # curl, а не huggingface_hub: Xet-загрузка больших файлов виснет за прокси
    foreach ($f in "config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json", "model.bin") {
        $dst = Join-Path $ModelDir $f
        $ok = $false
        for ($i = 0; $i -lt 5 -and -not $ok; $i++) {
            curl.exe -sSL @CurlProxy -C - --retry 3 -o $dst "$repo/$f"
            $ok = ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq 33)  # 33: файл уже скачан целиком
        }
        if (-not $ok) { throw "не скачался $f (запусти ещё раз — curl докачает)" }
    }
    $size = [math]::Round((Get-Item (Join-Path $ModelDir "model.bin")).Length / 1GB, 2)
    Ok "$ModelDir (model.bin $size ГБ)"
}

# ---------------------------------------------------------------- config.yaml
Step "config.yaml"
if (Test-Path config.yaml) {
    Ok "уже есть — не трогаю"
} else {
    $cfg = Get-Content config.example.yaml -Raw -Encoding utf8
    if (-not $SkipModel) {
        $m = $ModelDir.Replace("\", "/")
        $cfg = $cfg -replace '(?m)^(\s*model:\s*)large-v3', "`${1}$m  #"
    }
    if ($Cpu) {
        $cfg = $cfg -replace '(?m)^(\s*device:\s*)cuda', '${1}cpu'
        $cfg = $cfg -replace '(?m)^(\s*compute_type:\s*)int8_float16', '${1}int8'
        $cfg = $cfg -replace '(?m)^(\s*encoder:\s*)h264_nvenc', '${1}libx264'
    }
    [IO.File]::WriteAllText((Join-Path $Root "config.yaml"), $cfg, (New-Object Text.UTF8Encoding $false))
    Ok "создан из config.example.yaml"
}

# ---------------------------------------------------------------- проверка
Step "проверка"
# через файл: PowerShell 5.1 съедает кавычки в аргументах `python -c`
$check = Join-Path ([IO.Path]::GetTempPath()) "shorts_check.py"
@'
import sys
sys.path.insert(0, ".")
from shorts.config import load_config
from shorts.transcribe import load_model
cfg = load_config("config.yaml")
m = load_model(cfg)
print("   whisper:", m.model.device, cfg["whisper"]["model"])
'@ | Set-Content -Encoding utf8 $check
& $VenvPy $check
Remove-Item $check -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { Warn "whisper не загрузился — смотри ошибку выше" } else { Ok "готово" }

Write-Host "`nДальше:" -ForegroundColor Cyan
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # панель на http://127.0.0.1:8000"
Write-Host "  .venv\Scripts\python.exe -m shorts run <ссылка на VOD>      # или из командной строки"
