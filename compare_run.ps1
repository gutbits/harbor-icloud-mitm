Set-Location $PSScriptRoot
python -m pip install -r requirements.txt -q
python two_tab_compare.py @args
