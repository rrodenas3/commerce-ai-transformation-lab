#!/usr/bin/env python3
"""Focused truth-boundary tests for the Stage 2 current-state comparator."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.stage2_case_system import build_development_case_material  # noqa: E402
from scripts.stage2_current_state import (  # noqa: E402
    ASSUMPTION_VERSION,
    replay_current_state,
    summarise_current_state,
)
from scripts.stage2_facts import SourceValidationError, parse_instant  # noqa: E402


class Stage2CurrentStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = build_development_case_material(PROJECT_ROOT)
        cls.rows = [replay_current_state(batch) for batch in cls.material["case_batches"]]

    def test_every_replay_traverses_q1_to_q8_with_one_canonical_case(self):
        for row in self.rows:
            queues = {event["queue"] for event in row["queue_trace"]}
            self.assertEqual({f"Q{number}" for number in range(1, 9)}, queues)
            self.assertEqual(1, row["structural_work"]["canonical_cases"])
            self.assertGreaterEqual(row["structural_work"]["source_opens"], 7)
            self.assertGreaterEqual(row["structural_work"]["policy_lookups"], 1)

    def test_duplicate_intake_deduplicates_without_hiding_work(self):
        duplicate = next(row for row in self.rows if row["derived_state"]["duplicate_signal"])
        self.assertEqual(2, duplicate["structural_work"]["intake_signals"])
        self.assertEqual(1, duplicate["structural_work"]["canonical_cases"])
        self.assertEqual(1, duplicate["structural_work"]["deduplication_events"])

    def test_active_work_wait_and_elapsed_are_separate_and_conserved(self):
        waited = []
        for row in self.rows:
            timing = row["virtual_time"]
            self.assertEqual(ASSUMPTION_VERSION, timing["assumption_version"])
            self.assertEqual("hypothetical-impact", timing["duration_evidence_label"])
            self.assertEqual(
                timing["total_elapsed_milliseconds"],
                timing["active_work_milliseconds"]
                + timing["dependency_wait_milliseconds"],
            )
            self.assertEqual(
                "not_observed", row["human_measures"]["manual_review_time"]
            )
            if timing["dependency_wait_milliseconds"]:
                waited.append(row)
        self.assertTrue(waited)
        self.assertTrue(
            any(row["structural_work"]["clarification_requests"] for row in waited)
        )
        self.assertTrue(any(row["structural_work"]["approval_steps"] for row in waited))

    def test_conflict_and_freshness_are_derived_not_caller_labels(self):
        conflict_batch = next(
            batch for batch in self.material["case_batches"]
            if replay_current_state(batch)["derived_state"]["has_source_conflict"]
        )
        changed = copy.deepcopy(conflict_batch)
        changed["payload"]["has_source_conflict"] = False
        with self.assertRaises(SourceValidationError):
            replay_current_state(changed)

    def test_summary_conserves_denominator_and_evidence_classes(self):
        summary = summarise_current_state(self.rows)
        self.assertEqual(24, summary["denominator"]["scheduled_cases"])
        self.assertEqual(
            24,
            sum(summary["deterministic_outcomes"].values()),
        )
        self.assertEqual("synthetic-observed", summary["structural_evidence_class"])
        self.assertEqual("hypothetical-impact", summary["duration_evidence_class"])
        self.assertEqual("not_observed", summary["human_measures"]["trust"])
        self.assertEqual("not_observed", summary["human_measures"]["adoption"])

    def test_time_parser_accepts_dst_offsets_and_rejects_ambiguous_or_malformed(self):
        winter = parse_instant("2026-01-15T10:00:00+01:00", "winter")
        summer = parse_instant("2026-08-11T10:00:00+02:00", "summer")
        self.assertEqual("2026-01-15T09:00:00+00:00", winter.isoformat())
        self.assertEqual("2026-08-11T08:00:00+00:00", summer.isoformat())
        for value in (
            "2026-08-11T10:00:00",
            "2026/08/11 10:00:00Z",
            "not-a-time",
        ):
            with self.subTest(value=value):
                with self.assertRaises(SourceValidationError):
                    parse_instant(value, "test")


if __name__ == "__main__":
    unittest.main()
