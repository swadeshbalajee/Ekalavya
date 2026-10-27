# One-time local setup on Windows: Conda env, .env, Qdrant and Temporal binaries,
# and a PostgreSQL cluster under .local/. Safe to re-run; finished steps are skipped.
param([string]$EnvironmentName = 'scholarmotion')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

# 1. Conda environment (Python 3.11, FFmpeg, Cairo/Pango, Manim, PostgreSQL).
$envs = (conda env list --json | ConvertFrom-Json).envs
$prefix = $envs | Where-Object { (Split-Path -Leaf $_) -eq $EnvironmentName } | Select-Object -First 1
if (-not $prefix) {
    Step "Creating the $EnvironmentName Conda environment (takes several minutes)"
    conda env create -n $EnvironmentName -f environment.yml
    if ($LASTEXITCODE -ne 0) { throw 'conda env create failed.' }
    $envs = (conda env list --json | ConvertFrom-Json).envs
    $prefix = $envs | Where-Object { (Split-Path -Leaf $_) -eq $EnvironmentName } | Select-Object -First 1
} else {
    Step "Conda environment found: $prefix"
}
$bin = "$prefix\Library\bin"

# 2. .env with a random client key; provider keys are left for you to fill in.
if (-not (Test-Path .env)) {
    Step 'Creating .env from .env.example'
    $key = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    (Get-Content .env.example) -replace '^APP_API_KEY=$', "APP_API_KEY=$key" |
        Set-Content -Encoding utf8 .env
    Write-Host '    Edit .env to add your provider API keys (see README).'
}

# 3. Qdrant and Temporal native binaries.
New-Item -ItemType Directory -Force .local\bin, .local\logs, .local\qdrant | Out-Null
function Install-Binary($name, $url, $exe) {
    if (Test-Path $exe) { Step "$name already installed"; return }
    Step "Downloading $name"
    $zip = ".local\bin\$name.zip"
    Invoke-WebRequest -Uri $url -OutFile $zip
    Expand-Archive -Force $zip ".local\bin\$name"
    Remove-Item $zip
}
Install-Binary 'qdrant' 'https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-pc-windows-msvc.zip' '.local\bin\qdrant\qdrant.exe'
Install-Binary 'temporal' 'https://temporal.download/cli/archive/latest?platform=windows&arch=amd64' '.local\bin\temporal\temporal.exe'
if (-not (Test-Path .local\qdrant\config.yaml)) {
    @'
service:
  host: 127.0.0.1
  http_port: 6333
  grpc_port: 6334
storage:
  storage_path: .local/qdrant/storage
telemetry_disabled: true
'@ | Set-Content -Encoding utf8 .local\qdrant\config.yaml
}

# 4. PostgreSQL cluster owned by the `scholarmotion` role, local trust auth.
if (-not (Test-Path .local\postgres\PG_VERSION)) {
    Step 'Initializing PostgreSQL in .local\postgres'
    & "$bin\initdb.exe" -D .local\postgres -U scholarmotion -A trust -E UTF8 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed.' }
    # Not piped: the server inherits pg_ctl's stdout, so a pipe would never close.
    & "$bin\pg_ctl.exe" -D .local\postgres -l .local\logs\postgres.log -o '-h 127.0.0.1 -p 5432' -w start
    & "$bin\createdb.exe" -h 127.0.0.1 -p 5432 -U scholarmotion scholarmotion
    $created = $LASTEXITCODE
    & "$bin\pg_ctl.exe" -D .local\postgres -w stop -m fast | Out-Null
    if ($created -ne 0) { throw 'createdb failed.' }
} else {
    Step 'PostgreSQL cluster already initialized'
}

Write-Host ''
Write-Host 'Setup complete. Optional: install TinyTeX (https://yihui.org/tinytex/) into'
Write-Host '.local\bin\TinyTeX for typeset equations; without it equations render as Unicode.'
Write-Host 'Start the app with:  powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1'
