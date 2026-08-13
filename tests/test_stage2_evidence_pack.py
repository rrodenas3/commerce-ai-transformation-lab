from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.build_stage2_evidence_pack import (
    EvidenceProjectionError,
    build_final_evidence_pack,
    resolve_public_reference,
    validate_final_evidence_pack,
    write_final_evidence_pack,
)
from scripts.stage2_contracts import canonical_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage2EvidencePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = build_final_evidence_pack(PROJECT_ROOT)

    def test_final_pack_is_deterministic_and_truthfully_bounded(self):
        self.assertEqual(self.pack, build_final_evidence_pack(PROJECT_ROOT))
        self.assertEqual("stage2-public-evidence-pack/v2", self.pack["schema_version"])
        self.assertEqual("local-mvp", self.pack["maturity"]["supported_ceiling"])
        self.assertEqual("pause", self.pack["decision"]["recommendation"])
        self.assertEqual("inconclusive", self.pack["economics"]["status"])
        self.assertFalse(self.pack["decision"]["authorises_company_pilot"])
        self.assertEqual("2026-08-12T13:00:00Z", self.pack["generated_at"])
        self.assertEqual(
            {
                "human_evidence": "not_observed",
                "independent_validation": False,
                "live_customer_outcome": "not_observed",
                "realised_value": "not_observed",
                "simulated_actions": True,
                "simulated_approvals": True,
                "synthetic": True,
                "unsent_communications": True,
            },
            self.pack["evidence_boundary"],
        )

    def test_claims_and_metrics_resolve_to_atomic_public_evidence(self):
        for claim in self.pack["claims"]:
            self.assertEqual(
                claim["value"],
                resolve_public_reference(PROJECT_ROOT, claim["evidence_ref"]),
                claim["claim_id"],
            )
        for metric in self.pack["metrics"]:
            self.assertEqual(
                metric["value"],
                resolve_public_reference(PROJECT_ROOT, metric["evidence_ref"]),
                metric["metric_id"],
            )
            self.assertIn("evidence_class", metric)
            self.assertIn("denominator", metric)
            self.assertIn("unit", metric)
        telemetry = {
            metric["metric_id"]: metric
            for metric in self.pack["metrics"]
            if metric["metric_id"] in {
                "provider_cost_unknown",
                "provider_latency_unknown",
            }
        }
        for metric in telemetry.values():
            self.assertEqual(
                "data/stage2/evaluation/v6/manifest.json",
                metric["denominator_evidence_ref"]["path"],
            )
            self.assertEqual(
                "/provider_attempt_count",
                metric["denominator_evidence_ref"]["pointer"],
            )

    def test_complete_case_index_conserves_the_frozen_denominator(self):
        cases = self.pack["cases"]
        self.assertEqual(36, len(cases))
        self.assertEqual([f"S2-CASE-{number:04d}" for number in range(5001, 5037)], [case["case_id"] for case in cases])
        counts: dict[str, int] = {key: 0 for key in self.pack["outcomes"]["counts"]}
        for case in cases:
            counts[case["outcome_bucket"]] = counts.get(case["outcome_bucket"], 0) + 1
            self.assertTrue(case["synthetic"])
            self.assertFalse(case["human_reviewed"])
            self.assertEqual("non-independent", case["validation_label"])
            self.assertGreaterEqual(len(case["evidence_chain"]), 3)
            for step in case["evidence_chain"]:
                self.assertEqual(
                    step["value"],
                    resolve_public_reference(PROJECT_ROOT, step["evidence_ref"]),
                )
        self.assertEqual(self.pack["outcomes"]["counts"], counts)
        self.assertEqual(3, counts["pending"])
        self.assertEqual(15, counts["verified_remedy"])

    def test_pause_action_is_one_owner_bound_capped_synthetic_step(self):
        action = self.pack["decision"]["next_action"]
        self.assertEqual(1, action["action_count"])
        self.assertEqual("Raul Rausell", action["owner"])
        self.assertEqual(36, action["cap"]["maximum_synthetic_cases"])
        self.assertEqual(36, action["cap"]["maximum_provider_attempts"])
        self.assertEqual(7, action["cap"]["maximum_calendar_days"])
        self.assertFalse(action["authorises_company_pilot"])
        self.assertEqual("not_observed", self.pack["human_measures"]["adoption"])
        self.assertEqual("not_observed", self.pack["human_measures"]["realised_savings"])

    def test_validation_rejects_missing_or_drifted_evidence(self):
        drifted = deepcopy(self.pack)
        drifted["claims"][0]["value"] = 35
        drifted["pack_digest"] = canonical_sha256(
            {key: value for key, value in drifted.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "claim evidence mismatch"):
            validate_final_evidence_pack(drifted, PROJECT_ROOT)
        missing = deepcopy(self.pack)
        missing["cases"].pop()
        missing["pack_digest"] = canonical_sha256(
            {key: value for key, value in missing.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "case denominator"):
            validate_final_evidence_pack(missing, PROJECT_ROOT)
        missing_claims = deepcopy(self.pack)
        missing_claims["claims"] = []
        missing_claims["pack_digest"] = canonical_sha256(
            {key: value for key, value in missing_claims.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "claims are missing"):
            validate_final_evidence_pack(missing_claims, PROJECT_ROOT)
        incomplete_chain = deepcopy(self.pack)
        incomplete_chain["cases"][0]["evidence_chain"] = []
        incomplete_chain["pack_digest"] = canonical_sha256(
            {key: value for key, value in incomplete_chain.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "chain is incomplete"):
            validate_final_evidence_pack(incomplete_chain, PROJECT_ROOT)
        rewritten_outcomes = deepcopy(self.pack)
        for case in rewritten_outcomes["cases"]:
            case["outcome_bucket"] = "verified_remedy"
        rewritten_outcomes["outcomes"]["counts"] = {
            key: 36 if key == "verified_remedy" else 0
            for key in rewritten_outcomes["outcomes"]["counts"]
        }
        rewritten_outcomes["pack_digest"] = canonical_sha256(
            {key: value for key, value in rewritten_outcomes.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "outcome evidence mismatch"):
            validate_final_evidence_pack(rewritten_outcomes, PROJECT_ROOT)
        swapped = deepcopy(self.pack)
        first = swapped["cases"][3]
        second = swapped["cases"][12]
        first["outcome_bucket"], second["outcome_bucket"] = (
            second["outcome_bucket"], first["outcome_bucket"]
        )
        first["outcome_classification"], second["outcome_classification"] = (
            second["outcome_classification"], first["outcome_classification"]
        )
        swapped["pack_digest"] = canonical_sha256(
            {key: value for key, value in swapped.items() if key != "pack_digest"}
        )
        with self.assertRaisesRegex(EvidenceProjectionError, "outcome source mismatch"):
            validate_final_evidence_pack(swapped, PROJECT_ROOT)

    def test_verify_mode_matches_committed_public_projection(self):
        committed = PROJECT_ROOT / "demo" / "data" / "evidence-pack.json"
        self.assertTrue(committed.exists())
        with tempfile.TemporaryDirectory(prefix="stage2-final-pack-") as temporary:
            output = Path(temporary) / "evidence-pack.json"
            write_final_evidence_pack(PROJECT_ROOT, output)
            self.assertEqual(committed.read_bytes(), output.read_bytes())


if __name__ == "__main__":
    unittest.main()
