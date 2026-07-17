import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from diagnostics.analysis_snapshot import SNAPSHOT_SECTION_NAMES
from tools.export_analysis_snapshot import create_snapshot, discover_input_file, main


class Args:
    def __init__(
        self,
        symbol="INJUSDT",
        input=None,
        date=None,
        at=None,
        max_time_diff_minutes=None,
        output_dir=None,
        history=0,
    ):
        self.symbol = symbol
        self.input = input
        self.date = date
        self.at = at
        self.max_time_diff_minutes = max_time_diff_minutes
        self.output_dir = output_dir
        self.history = history


def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            if isinstance(row, str):
                fh.write(row + "\n")
            else:
                fh.write(json.dumps(row) + "\n")


def scan(symbol, timestamp, **extra):
    row = {
        "record_type": "symbol_scan",
        "timestamp": timestamp,
        "symbol": symbol,
        "current_price": 10.0,
        "atr": 0.5,
        "decision": "Ignore",
        "final_decision": "Ignore",
        "score": 0,
        "raw_score": 0,
        "no_trade_reason": "scenario_waiting",
        "features": {
            "htf_context": {"direction": "bearish", "reason": "BOS down"},
            "scenario_scan": {
                "scenario_valid": False,
                "selected_scenario": {
                    "candidate_id": "CAND-INJ-1",
                    "status": "building",
                    "direction": "SHORT",
                },
            },
            "trigger_debug": {"trigger_confirmed": False},
            "shadow_candidate": {"shadow_candidate_id": "SHADOW-INJ-1", "shadow_tier": "B"},
        },
        "trigger_diagnostics": {
            "candidate_id": "CAND-INJ-1",
            "trigger_stage": "waiting_for_confirmed_trigger",
            "missing_conditions": ["fvg_not_retested"],
            "early_trigger_detected": True,
            "confirmed_trigger_detected": False,
        },
    }
    row.update(extra)
    return row


class ExportAnalysisSnapshotTest(unittest.TestCase):
    def test_latest_scan_selected_and_snapshot_sections_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            write_jsonl(
                journal,
                [
                    scan("INJ", "2026-07-17T10:00:00+03:00", current_price=8.0),
                    scan("INJ", "2026-07-17T10:15:00+03:00", current_price=9.0),
                ],
            )

            path, snapshot, result, diff, requested = create_snapshot(Args(input=str(journal), output_dir=str(out)))

            self.assertTrue(path.exists())
            self.assertEqual(snapshot["symbol"], "INJUSDT")
            self.assertEqual(snapshot["market"]["current_price"], 9.0)
            self.assertIsNone(diff)
            self.assertIsNone(requested)
            self.assertEqual(result.skipped_invalid_lines, 0)
            for section in SNAPSHOT_SECTION_NAMES:
                self.assertIn(section, snapshot)

    def test_symbol_matching_is_case_insensitive_and_usdt_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            write_jsonl(
                journal,
                [
                    scan("BTC", "2026-07-17T10:00:00+03:00"),
                    scan("INJ", "2026-07-17T10:15:00+03:00"),
                ],
            )

            _path, snapshot, _result, _diff, _requested = create_snapshot(
                Args(symbol="injusdt", input=str(journal), output_dir=str(out))
            )

            self.assertEqual(snapshot["symbol"], "INJUSDT")
            self.assertEqual(snapshot["source"]["line_number"], 2)

    def test_malformed_jsonl_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            write_jsonl(journal, ["{bad json", scan("INJ", "2026-07-17T10:15:00+03:00")])

            _path, snapshot, result, _diff, _requested = create_snapshot(Args(input=str(journal), output_dir=str(out)))

            self.assertEqual(result.skipped_invalid_lines, 1)
            self.assertEqual(snapshot["source"]["skipped_invalid_lines"], 1)

    def test_nearest_scan_selected_by_at_and_max_diff_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            write_jsonl(
                journal,
                [
                    scan("INJ", "2026-07-17T10:00:00+03:00", current_price=8.0),
                    scan("INJ", "2026-07-17T10:14:42+03:00", current_price=9.0),
                ],
            )

            _path, snapshot, _result, diff, requested = create_snapshot(
                Args(input=str(journal), output_dir=str(out), at="2026-07-17T07:15:00Z", max_time_diff_minutes=1)
            )

            self.assertEqual(snapshot["market"]["current_price"], 9.0)
            self.assertEqual(diff, 18)
            self.assertEqual(requested.isoformat(), "2026-07-17T07:15:00+00:00")

            with self.assertRaises(TimeoutError):
                create_snapshot(
                    Args(input=str(journal), output_dir=str(out), at="2026-07-17T07:15:00Z", max_time_diff_minutes=0.1)
                )

    def test_missing_symbol_returns_non_zero_from_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            write_jsonl(journal, [scan("BTC", "2026-07-17T10:00:00+03:00")])

            code = main(["INJUSDT", "--input", str(journal), "--output-dir", str(out)])

            self.assertEqual(code, 1)

    def test_missing_sections_and_recent_history_are_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "scans_2026-07-17.jsonl"
            out = Path(tmp) / "snapshots"
            first = scan("INJ", "2026-07-17T10:00:00+03:00")
            second = scan("INJ", "2026-07-17T10:15:00+03:00")
            second["features"].pop("risk_plan", None)
            write_jsonl(journal, [first, second])

            _path, snapshot, _result, _diff, _requested = create_snapshot(
                Args(input=str(journal), output_dir=str(out), history=1)
            )

            self.assertIn("risk_plan", snapshot["missing_sections"])
            self.assertEqual(len(snapshot["recent_history"]), 1)
            self.assertIn("candidate_id", snapshot["recent_history"][0])
            self.assertNotIn("features", snapshot["recent_history"][0])

    def test_output_filename_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_dir = root / "data" / "journal"
            journal_dir.mkdir(parents=True)
            journal = journal_dir / "scans_2026-07-17.jsonl"
            out = root / "snapshots"
            write_jsonl(journal, [scan("INJ", "2026-07-17T10:15:00+03:00")])

            code = main(["INJUSDT", "--input", str(journal), "--output-dir", str(out)])

            self.assertEqual(code, 0)
            files = list(out.glob("INJUSDT_*.json"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^INJUSDT_2026-07-17T071500Z\.json$")

    def test_date_based_file_discovery_uses_project_convention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_dir = root / "data" / "journal"
            journal_dir.mkdir(parents=True)
            journal = journal_dir / "scans_2026-07-17.jsonl"
            journal.write_text("", encoding="utf-8")

            found = discover_input_file(date(2026, 7, 17), root=root)

            self.assertEqual(found, journal)


if __name__ == "__main__":
    unittest.main()
