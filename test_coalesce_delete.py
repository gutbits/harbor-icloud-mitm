#!/usr/bin/env python3
"""Coalesce account inputs: newest seq wins even when shorter (backspace)."""
import phish_mirror as pm


def test_coalesce_account_backspace() -> None:
    events = [
        {"type": "input", "payload": {"field": "account", "value": "user@gmail.com"}, "seq": 1, "t": 1},
        {"type": "input", "payload": {"field": "account", "value": "user@gmail."}, "seq": 2, "t": 2},
    ]
    out = pm.coalesce_relay_events(events)
    ac = [e for e in out if e.get("type") == "input"]
    assert len(ac) == 1
    assert ac[0]["payload"]["value"] == "user@gmail."


if __name__ == "__main__":
    test_coalesce_account_backspace()
    print("OK")
