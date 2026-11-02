Set-Location $PSScriptRoot
python -m pip install -r requirements.txt -q
python apple_harbor_mirror.py @args
