#!/usr/bin/env python3
"""Unit tests for victim email state + live sync debounce."""
import time
import unittest

from email_sync import AppleEmailLiveSync, VictimEmailState


class VictimEmailStateTests(unittest.TestCase):
    def test_longest_wins_ingest(self) -> None:
        s = VictimEmailState()
        s.ingest_event("gutbits@gmail.")
        s.ingest_event("gutbits@gmail.com")
        self.assertEqual(s.get(), "gutbits@gmail.com")

    def test_no_regression_shorter(self) -> None:
        s = VictimEmailState()
        s.ingest_event("full@example.com")
        s.ingest_event("full@exam")
        self.assertEqual(s.get(), "full@exam")

    def test_seq_authoritative_delete(self) -> None:
        s = VictimEmailState()
        s.ingest_event("long@example.com", seq=10)
        s.ingest_event("short", seq=11)
        self.assertEqual(s.get(), "short")
        s.ingest_event("ignored", seq=9)
        self.assertEqual(s.get(), "short")

    def test_authoritative(self) -> None:
        s = VictimEmailState()
        s.ingest_event("partial")
        s.set_authoritative("commit@example.com")
        self.assertEqual(s.get(), "commit@example.com")


class AppleEmailLiveSyncTests(unittest.TestCase):
    def test_force_always(self) -> None:
        live = AppleEmailLiveSync(min_interval_s=10.0)
        live.mark_pushed("a@b.com")
        self.assertTrue(live.should_push("a@b.com", force=True))

    def test_no_repush_identical_after_interval(self) -> None:
        live = AppleEmailLiveSync(min_interval_s=0.05)
        self.assertTrue(live.should_push("x@y.com"))
        live.mark_pushed("x@y.com")
        time.sleep(0.06)
        self.assertFalse(live.should_push("x@y.com"))

    def test_growth_bypasses_debounce(self) -> None:
        live = AppleEmailLiveSync(min_interval_s=10.0)
        live.mark_pushed("a@")
        self.assertTrue(live.should_push("a@b.com"))


if __name__ == "__main__":
    unittest.main()
