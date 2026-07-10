import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.journal import write_scan_record


class JournalTest(unittest.TestCase):
    def test_write_scan_record_creates_jsonl_with_safe_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_scan_record(
                {
                    "timestamp": "2026-07-10T10:15:00+03:00",
                    "symbol": "BTC",
                    "series": pd.Series({"score": 42}),
                    "nested": {"time": pd.Timestamp("2026-07-10T07:15:00Z")},
                },
                journal_dir=Path(tmpdir),
            )

            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["symbol"], "BTC")
            self.assertEqual(payload["series"]["score"], 42)
            self.assertEqual(payload["nested"]["time"], "2026-07-10T07:15:00+00:00")

    def test_write_scan_record_uses_env_journal_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            previous = os.environ.get("SCAN_JOURNAL_DIR")
            os.environ["SCAN_JOURNAL_DIR"] = tmpdir
            try:
                path = write_scan_record({
                    "timestamp": "2026-07-10T10:15:00+03:00",
                    "symbol": "ETH",
                })
            finally:
                if previous is None:
                    os.environ.pop("SCAN_JOURNAL_DIR", None)
                else:
                    os.environ["SCAN_JOURNAL_DIR"] = previous

            self.assertEqual(path.parent, Path(tmpdir))
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
