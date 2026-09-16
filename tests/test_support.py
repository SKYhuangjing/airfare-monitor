from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from airfare_monitor.ui.support import PROMPT_COOLDOWN_DAYS, SupportPromptState


class SupportPromptStateTests(unittest.TestCase):
    def test_only_offers_after_a_confirmed_low_price(self):
        with TemporaryDirectory() as temp:
            state = SupportPromptState(Path(temp) / "support-prompt.json")
            ordinary = [{
                "event_type": "cycle_finished",
                "occurred_at": "2026-09-16T10:00:00",
            }]
            confirmed = [{
                "event_type": "low_price_confirmed",
                "occurred_at": "2026-09-16T10:01:00",
            }]
            self.assertFalse(state.should_offer(ordinary, now=datetime(2026, 9, 16, 10, 2)))
            self.assertTrue(state.should_offer(confirmed, now=datetime(2026, 9, 16, 10, 2)))

    def test_dismissal_needs_a_new_event_and_thirty_day_cooldown(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "support-prompt.json"
            state = SupportPromptState(path)
            first = [{
                "event_type": "low_price_confirmed",
                "occurred_at": "2026-09-16T10:01:00",
            }]
            dismissed_at = datetime(2026, 9, 16, 10, 2)
            state.dismiss(first, now=dismissed_at)
            self.assertFalse(state.should_offer(first, now=dismissed_at + timedelta(days=31)))

            newer = [{
                "event_type": "low_price_confirmed",
                "occurred_at": "2026-09-20T10:01:00",
            }]
            self.assertFalse(state.should_offer(newer, now=dismissed_at + timedelta(days=5)))
            self.assertTrue(
                state.should_offer(
                    newer,
                    now=dismissed_at + timedelta(days=PROMPT_COOLDOWN_DAYS, seconds=1),
                )
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("payment", payload)
            self.assertEqual(set(payload), {"dismissed_event_at", "dismissed_until"})


if __name__ == "__main__":
    unittest.main()
