from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.generate_stage1_multi_persona_practice import (
    ADVERSARIAL_OVERRIDES,
    EVIDENCE_CLASS,
    INDEPENDENCE_STATUS,
    OUTPUT_RELATIVE,
    PRIVATE_ORACLE_RELATIVE,
    RUN_RELATIVE,
    build_rows,
    derive_governed_decision,
    generate,
    verify,
    verify_public,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage1MultiPersonaPracticeTests(unittest.TestCase):
    def setUp(self):
        self.output = PROJECT_ROOT / OUTPUT_RELATIVE
        self.cases = [
            json.loads(line)
            for line in (
                PROJECT_ROOT / "data/stage1/heldout/v2/cases.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            if line
        ]

    def _csv(self, name: str) -> list[dict[str, str]]:
        with (self.output / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_all_csv_cells_are_filled_and_rows_are_unique(self):
        personas = self._csv("personas.csv")
        decisions = self._csv("multi-persona-decisions.csv")
        manual = self._csv("manual-records.ai-assisted.csv")
        self.assertEqual(5, len(personas))
        self.assertEqual(32, len(decisions))
        self.assertEqual(32, len(manual))
        self.assertEqual(32, len({row["case_id"] for row in decisions}))
        for rows in (personas, decisions, manual):
            self.assertTrue(all(value.strip() for row in rows for value in row.values()))

    def test_personas_and_decisions_are_deliberately_diverse(self):
        decisions = self._csv("multi-persona-decisions.csv")
        persona_counts = Counter(row["primary_persona_code"] for row in decisions)
        self.assertEqual({"P-CUST", "P-OPS", "P-WORK", "P-TECH", "P-RISK"}, set(persona_counts))
        self.assertLessEqual(max(persona_counts.values()) - min(persona_counts.values()), 1)
        self.assertEqual(8, len({row["governed_final_action"] for row in decisions}))
        self.assertEqual({"approval", "delegated", "specialist"}, {row["governed_final_route"] for row in decisions})
        self.assertEqual(8, len({row["scenario_category"] for row in decisions}))
        self.assertEqual(len(ADVERSARIAL_OVERRIDES), sum(row["decision_changed"] == "true" for row in decisions))
        self.assertGreater(sum(row["decision_changed"] == "false" for row in decisions), 0)

    def test_adversarial_overrides_bind_to_case_ids_and_valid_owners(self):
        _, reversed_decisions, _ = build_rows(list(reversed(self.cases)))
        by_case_id = {row["case_id"]: row for row in reversed_decisions}
        for case_id, (action, route, bias, _) in ADVERSARIAL_OVERRIDES.items():
            row = by_case_id[case_id]
            self.assertEqual(action, row["initial_recommended_action"])
            self.assertEqual(route, row["initial_route"])
            self.assertEqual(bias, row["initial_bias"])

        allowed_owners = {
            "approval": {"finance_duty_approver", "workflow_owner"},
            "delegated": {"customer_recovery_specialist"},
            "specialist": {
                "fulfilment_operations_coordinator",
                "policy_and_risk_owner",
                "technical_owner",
            },
        }
        for row in reversed_decisions:
            self.assertIn(
                row["initial_decision_owner"],
                allowed_owners[row["initial_route"]],
            )

    def test_final_decisions_match_policy_and_private_committed_oracle(self):
        decisions = {
            row["case_id"]: row for row in self._csv("multi-persona-decisions.csv")
        }
        for case in self.cases:
            final = derive_governed_decision(case)
            row = decisions[case["case_id"]]
            self.assertEqual(final["action"], row["governed_final_action"])
            self.assertEqual(final["route"], row["governed_final_route"])
            self.assertEqual(final["decision_owner"], row["governed_decision_owner"])

        oracle_path = PROJECT_ROOT / PRIVATE_ORACLE_RELATIVE
        if not oracle_path.exists():
            self.skipTest("ignored private oracle is unavailable in this checkout")
        public_manifest = json.loads(
            (PROJECT_ROOT / "data/stage1/heldout/v2/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            public_manifest["artifacts_sha256"]["oracle.released.jsonl"],
            hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
        )
        oracles = {
            row["case_id"]: row
            for row in (
                json.loads(line)
                for line in oracle_path.read_text(encoding="utf-8").splitlines()
                if line
            )
        }
        for case_id, row in decisions.items():
            self.assertEqual(oracles[case_id]["preferred_action"], row["governed_final_action"])
            self.assertEqual(oracles[case_id]["required_route"], row["governed_final_route"])

    def test_manual_shape_is_explicitly_non_human_and_non_scorable(self):
        rows = self._csv("manual-records.ai-assisted.csv")
        self.assertEqual({"ai-assisted-multi-persona"}, {row["run_type"] for row in rows})
        self.assertEqual({"0"}, {row["active_handling_seconds"] for row in rows})
        self.assertEqual({"true"}, {row["help_requested"] for row in rows})
        self.assertTrue(all("no human handling" in row["notes_without_personal_data"] for row in rows))

    def test_evidence_and_independence_labels_are_exact(self):
        personas = self._csv("personas.csv")
        decisions = self._csv("multi-persona-decisions.csv")
        self.assertEqual({EVIDENCE_CLASS}, {row["evidence_class"] for row in personas + decisions})
        self.assertEqual({INDEPENDENCE_STATUS}, {row["independence_status"] for row in personas + decisions})
        self.assertEqual({"false"}, {row["is_human_reviewer"] for row in personas})

    def test_original_v2_human_worksheet_remains_blank_and_invalidated(self):
        run = PROJECT_ROOT / RUN_RELATIVE
        invalidation = json.loads((run / "INVALIDATED.json").read_text(encoding="utf-8"))
        self.assertEqual("invalidated-before-human-handling", invalidation["status"])
        self.assertEqual(0, invalidation["completed_record_count"])
        with (run / "manual-records.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(32, len(rows))
        self.assertTrue(all(not row["recommended_action"] and not row["route"] for row in rows))

    def test_generation_is_byte_stable_and_committed_artifacts_verify(self):
        verify(PROJECT_ROOT, self.output)
        verify_public(PROJECT_ROOT, self.output)
        with tempfile.TemporaryDirectory() as directory:
            regenerated = Path(directory) / "practice"
            generate(PROJECT_ROOT, regenerated, validate_private_oracle=False)
            for path in regenerated.iterdir():
                self.assertEqual(path.read_bytes(), (self.output / path.name).read_bytes())

    def test_public_verification_does_not_claim_oracle_revalidation(self):
        missing_oracle = Path("artifacts/private/stage1/heldout/v2/missing.jsonl")
        with patch(
            "scripts.generate_stage1_multi_persona_practice.PRIVATE_ORACLE_RELATIVE",
            missing_oracle,
        ):
            with self.assertRaisesRegex(ValueError, "private oracle is required"):
                verify(PROJECT_ROOT, self.output)
            verify_public(PROJECT_ROOT, self.output)

        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn(
            "not-repeated-by-public-byte-verification",
            manifest["private_oracle_validation"],
        )


if __name__ == "__main__":
    unittest.main()
