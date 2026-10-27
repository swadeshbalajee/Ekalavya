param([string]$EnvironmentName = 'scholarmotion')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$envs = (conda env list --json | ConvertFrom-Json).envs
$prefix = $envs | Where-Object { (Split-Path -Leaf $_) -eq $EnvironmentName } | Select-Object -First 1
if (-not $prefix) { throw "Create the $EnvironmentName Conda environment first." }
$env:PATH = "$prefix;$prefix\Scripts;$prefix\Library\bin;$env:PATH"
$env:PYTHONPATH = Join-Path $root 'src'
$env:PYTHONUTF8 = '1'
$texBin = Join-Path $root '.local\bin\TinyTeX\bin\windows'
if (Test-Path $texBin) { $env:PATH = "$texBin;$env:PATH" }
if (-not (Test-Path .env)) { throw 'Configure .env before starting.' }
$keyLine = Get-Content .env | Where-Object { $_ -match '^APP_API_KEY=' } | Select-Object -Last 1
$env:SCHOLARMOTION_API_KEY = $keyLine.Substring('APP_API_KEY='.Length).Trim()
$env:SCHOLARMOTION_API_URL = 'http://127.0.0.1:8000'
New-Item -ItemType Directory -Force .local/logs | Out-Null
if (-not (Test-Path .local/empty.stdin)) { New-Item -ItemType File .local/empty.stdin | Out-Null }
$state = @{}
if (Test-Path .local/service-pids.json) {
    $old = Get-Content .local/service-pids.json -Raw | ConvertFrom-Json
    foreach ($p in $old.PSObject.Properties) { $state[$p.Name] = $p.Value }
}
function Start-LocalService($name, $exe, $arguments, $port = 0) {
    if ($port -and (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        Write-Host "$name is already listening on $port"
        return
    }
    if ($state.ContainsKey($name)) {
        $running = Get-CimInstance Win32_Process -Filter "ProcessId=$($state[$name])" -ErrorAction SilentlyContinue
        if ($running -and $running.ExecutablePath -eq [IO.Path]::GetFullPath($exe)) {
            Write-Host "$name is already running"
            return
        }
    }
    $logName = "$name-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')"
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardInput "$root/.local/empty.stdin" -RedirectStandardOutput "$root/.local/logs/$logName.log" -RedirectStandardError "$root/.local/logs/$logName.err.log" -PassThru
    $state[$name] = $process.Id
    $state | ConvertTo-Json | Set-Content .local/service-pids.json
    Write-Host "$name started (PID $($process.Id))"
}
& "$prefix/Library/bin/pg_isready.exe" -h 127.0.0.1 -p 5432 *> $null
if ($LASTEXITCODE -ne 0) {
    & "$prefix/Library/bin/pg_ctl.exe" -D .local/postgres -l .local/logs/postgres.log -o '-h 127.0.0.1 -p 5432' start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start; initialize .local/postgres first.' }
}
Start-LocalService 'qdrant' "$root/.local/bin/qdrant/qdrant.exe" @('--config-path', '.local/qdrant/config.yaml') 6333
Start-LocalService 'temporal' "$root/.local/bin/temporal/temporal.exe" @('server','start-dev','--ip','127.0.0.1','--db-filename','.local/temporal.db') 7233
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$migrationOutput = & "$prefix/python.exe" -m alembic upgrade head 2>&1
$migrationExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction
foreach ($line in $migrationOutput) { Write-Host $line.ToString() }
if ($migrationExitCode -ne 0) { throw 'Database migration failed.' }
Start-LocalService 'api' "$prefix/python.exe" @('-m','uvicorn','scholarmotion.api.main:app','--host','127.0.0.1','--port','8000') 8000
Start-LocalService 'worker' "$prefix/python.exe" @('-m','scholarmotion.workflows.worker')
Start-LocalService 'frontend' "$prefix/python.exe" @('-m','streamlit','run','frontend/app.py','--server.address','127.0.0.1','--server.port','8501','--server.headless','true','--browser.gatherUsageStats','false') 8501
Write-Host 'App: http://127.0.0.1:8501 | API docs: http://127.0.0.1:8000/docs | Temporal: http://127.0.0.1:8233'
