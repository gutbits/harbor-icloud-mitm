Set-Location $PSScriptRoot
python -m pip install -r requirements.txt -q
python -m playwright install chromium 2>$null
python mirror_playwright.py @args
