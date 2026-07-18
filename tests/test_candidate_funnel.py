import unittest

from research.analyze_candidate_funnel import (
    build_candidates,
    candidate_rows,
    collect_reasons,
    funnel,
    funnel_table,
    normalize_reason,
    reason_tables,
)


def record(symbol, timestamp, candidates, **extra):
    scan = {
        "selected_scenario": candidates[0] if candidates else None,
        "top_candidates": candidates,
    }
    payload = {
        "record_type": "symbol_scan",
        "symbol": symbol,
        "timestamp": timestamp,
        "score": extra.pop("score", 10),
        "decision": extra.pop("decision", "Ignore"),
        "features": {"scenario_scan": scan},
        "diagnostics": {"scenario_scan": scan},
    }
    payload.update(extra)
    return ("journal.jsonl", payload)


def candidate(candidate_id, direction="LONG", events=None, **extra):
    payload = {
        "candidate_id": candidate_id,
        "direction": direction,
        "anchor_type": "SFP_CONFIRMED",
        "anchor_index": "2026-01-01 10:00:00",
        "current_step": "liquidity_sweep_confirmed",
        "next_expected_step": "EARLY_TRIGGER_CONFIRMED",
        "risk_reason": "candidate_fvg_not_created",
        "events_used": events or [{"event_type": "HTF_CONTEXT_CONFIRMED"}, {"event_type": "SFP_CONFIRMED"}],
    }
    payload.update(extra)
    return payload


class CandidateFunnelTest(unittest.TestCase):
    def test_same_candidate_across_multiple_scan_rows_is_deduplicated(self):
        first = candidate("C1", events=[{"event_type": "HTF_CONTEXT_CONFIRMED"}, {"event_type": "SFP_CONFIRMED"}])
        second = candidate(
            "C1",
            events=[
                {"event_type": "HTF_CONTEXT_CONFIRMED"},
                {"event_type": "SFP_CONFIRMED"},
                {"event_type": "EARLY_TRIGGER_CONFIRMED"},
            ],
            current_step="early_trigger_confirmed",
        )

        candidates, stats = build_candidates([
            record("SOL", "2026-01-01T10:00:00Z", [first], score=5),
            record("SOL", "2026-01-01T10:15:00Z", [second], score=15),
        ])

        self.assertEqual(len(candidates), 1)
        only = next(iter(candidates.values()))
        self.assertEqual(only.row_count, 2)
        self.assertEqual(only.max_stage(), "EARLY_TRIGGER_CONFIRMED")
        self.assertEqual(only.max_score, 15)
        self.assertEqual(stats["symbol_scan_rows"], 2)

    def test_two_candidates_same_symbol_are_separate(self):
        candidates, _stats = build_candidates([
            record("SOL", "2026-01-01T10:00:00Z", [candidate("C1"), candidate("C2")]),
        ])

        self.assertEqual({item.candidate_id for item in candidates.values()}, {"C1", "C2"})

    def test_long_short_funnel_separation(self):
        long = candidate("LONG-1", direction="LONG")
        short = candidate("SHORT-1", direction="SHORT")
        candidates, _stats = build_candidates([record("INJ", "2026-01-01T10:00:00Z", [long, short])])

        grouped = funnel(candidates.values(), group_key="direction")

        self.assertEqual(grouped["long"][0]["reached"], 1)
        self.assertEqual(grouped["short"][0]["reached"], 1)

    def test_maximum_stage_selection_uses_stage_order(self):
        agg = next(iter(build_candidates([
            record("BNB", "2026-01-01T10:00:00Z", [
                candidate(
                    "C1",
                    events=[
                        {"event_type": "HTF_CONTEXT_CONFIRMED"},
                        {"event_type": "SFP_CONFIRMED"},
                        {"event_type": "EARLY_TRIGGER_CONFIRMED"},
                        {"event_type": "CONFIRMED_TRIGGER_CONFIRMED"},
                        {"event_type": "FVG_CREATED"},
                    ],
                )
            ]),
        ])[0].values()))

        self.assertEqual(agg.max_stage(), "FVG_CREATED")
        self.assertEqual(funnel_table([agg])[5]["stage"], "FVG_CREATED")
        self.assertEqual(funnel_table([agg])[5]["reached"], 1)

    def test_primary_stop_reason_prefers_recorded_candidate_reason(self):
        item = candidate("C1", risk_reason="confirmed_trigger_missing")
        candidates, _stats = build_candidates([record("SOL", "2026-01-01T10:00:00Z", [item])])
        row = candidate_rows(candidates.values())[0]

        self.assertEqual(row["primary_stop_reason"], "confirmed_bos_not_found")

    def test_secondary_failed_conditions_counting_is_separate_from_primary(self):
        item = candidate(
            "C1",
            trigger_scan={
                "confirmed_trigger_debug": {
                    "rejected_candidates": [
                        {
                            "type": "bearish_bos",
                            "index": "2026-01-01 10:15:00",
                            "rejected_reason": "quality_below_min",
                            "primary_reason": "quality_below_min",
                            "failed_conditions": ["close_position_below_min", "volume_not_confirmed"],
                        }
                    ]
                }
            },
        )
        candidates, _stats = build_candidates([record("SOL", "2026-01-01T10:00:00Z", [item])])
        reasons = reason_tables(candidates.values())

        self.assertEqual(reasons["primary_rejection_reasons"][0]["reason"], "candidate_fvg_not_created")
        secondary = {row["reason"]: row["unique_candidates"] for row in reasons["secondary_failed_conditions"]}
        self.assertEqual(secondary["close_position_below_min"], 1)
        self.assertEqual(secondary["volume_not_confirmed"], 1)

    def test_pre_patch_rows_without_failed_conditions_are_not_zeroed(self):
        item = candidate("C1", trigger_scan={"confirmed_trigger_debug": {"rejected_candidates": [{"type": "bearish_bos", "quality_score": 35, "rejected_reason": "quality_below_min"}]}})

        primary, secondary, _waiting, _gates = collect_reasons({"score": 0}, item)

        self.assertIn("candidate_fvg_not_created", primary)
        self.assertEqual(secondary, [])

    def test_reason_aliases_are_normalized(self):
        self.assertEqual(normalize_reason("trigger_confirmed"), "confirmed_bos_not_found")
        self.assertEqual(normalize_reason("score_threshold"), "score_below_min")


if __name__ == "__main__":
    unittest.main()
