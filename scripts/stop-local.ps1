$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (Test-Path .local/service-pids.json) {
    $state = Get-Content .local/service-pids.json -Raw | ConvertFrom-Json
    foreach ($entry in $state.PSObject.Properties) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($entry.Value)" -ErrorAction SilentlyContinue
        # Check identity before stopping a persisted PID which Windows may have reused.
        $matches = $process -and (
            ($entry.Name -eq 'qdrant' -and $process.ExecutablePath -eq "$root\.local\bin\qdrant\qdrant.exe") -or
            ($entry.Name -eq 'temporal' -and $process.ExecutablePath -eq "$root\.local\bin\temporal\temporal.exe") -or
            ($entry.Name -eq 'api' -and $process.CommandLine -match 'uvicorn scholarmotion.api.main:app') -or
            ($entry.Name -eq 'worker' -and $process.CommandLine -match 'scholarmotion.workflows.worker') -or
            ($entry.Name -eq 'frontend' -and $process.CommandLine -match 'streamlit run frontend/app.py')
        )
        if ($matches) { Stop-Process -Id $entry.Value; Write-Host "Stopped $($entry.Name)" }
    }
}
$envs = (conda env list --json | ConvertFrom-Json).envs
$prefix = $envs | Where-Object { (Split-Path -Leaf $_) -eq 'scholarmotion' } | Select-Object -First 1
if ($prefix) {
    $env:PATH = "$prefix/Library/bin;$env:PATH"
    & "$prefix/Library/bin/pg_ctl.exe" -D .local/postgres stop -m fast
}
