#!/usr/bin/env python3
"""Focused contract tests for the Stage 2 synthetic case system."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.stage2_case_system import (  # noqa: E402
    DEVELOPMENT_CASE_COUNT,
    build_development_case_material,
    generate_stage2_development_artifacts,
)
from scripts.stage2_facts import (  # noqa: E402
    SourceValidationError,
    derive_case_facts,
    revision_pin_sha256,
    source_event_cut_sha256,
    source_provenance_material,
    validate_source_batch,
)
from scripts.stage2_contracts import canonical_json_bytes  # noqa: E402


class Stage2CaseSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = build_development_case_material(PROJECT_ROOT)

    def test_development_denominator_is_balanced_and_reconciles(self):
        batches = self.material["case_batches"]
        evaluator = self.material["evaluator_projections"]
        contract = json.loads(
            (PROJECT_ROOT / "data" / "stage2" / "evaluation-contract.json").read_text(
                encoding="utf-8"
            )
        )
        frozen_families = [
            family["family_id"] for family in contract["case_plan"]["families"]
        ]
        self.assertEqual(DEVELOPMENT_CASE_COUNT, len(batches))
        self.assertEqual(
            frozen_families,
            list(dict.fromkeys(row["evaluation_family"] for row in evaluator)),
        )
        self.assertEqual(
            {2}, set(Counter(row["evaluation_family"] for row in evaluator).values())
        )

        for batch in batches:
            validated = validate_source_batch(batch)
            facts = derive_case_facts(validated)
            self.assertTrue(1 <= len(facts["lines"]) <= 5)
            self.assertTrue(1 <= len(facts["parcels"]) <= 2)
            self.assertGreaterEqual(facts["ordered_quantity"], facts["shipped_quantity"])
            self.assertGreaterEqual(facts["shipped_quantity"], facts["delivered_quantity"])
            self.assertGreaterEqual(facts["remaining_quantity"], 0)
            self.assertGreaterEqual(facts["replacement_quantity"], 0)
            self.assertGreaterEqual(facts["refunded_quantity"], 0)
            self.assertLessEqual(
                facts["recovered_quantity"], facts["remaining_quantity"]
            )

    def test_development_expectations_match_reproducible_comparator(self):
        from scripts.stage2_current_state import replay_current_state

        expectations = {
            row["case_id"]: row["expected_deterministic_outcome"]
            for row in self.material["evaluator_projections"]
        }
        observed = {
            batch["payload"]["case_id"]: replay_current_state(batch)["deterministic_outcome"]
            for batch in self.material["case_batches"]
        }
        self.assertEqual(expectations, observed)

    def test_source_batch_has_commit_marker_and_revision_pins(self):
        batch = self.material["case_batches"][0]
        validated = validate_source_batch(batch)
        payload = validated["payload"]
        self.assertTrue(payload["committed"])
        self.assertRegex(payload["source_event_cut_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(payload["ledger_head_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(payload["revision_pin_sha256"], r"^[0-9a-f]{64}$")

        changed = copy.deepcopy(batch)
        changed["payload"]["records"][0]["payload"]["data"]["currency"] = "USD"
        with self.assertRaisesRegex(SourceValidationError, "provenance|event cut"):
            validate_source_batch(changed)

    def test_derived_facts_reject_over_recovery_even_when_digests_are_valid(self):
        changed = copy.deepcopy(self.material["case_batches"][0])
        payment = next(
            record for record in changed["payload"]["records"]
            if record["payload"]["source_name"] == "PAYMENT"
        )
        payment["payload"]["data"]["refund_entries"] = [
            {
                "action_id": "S2-ACTION-OVER-RECOVERY",
                "amount_cents": 999999,
                "currency": "EUR",
                "quantity": 99,
            }
        ]
        material = source_provenance_material(payment["payload"])
        payment["payload"]["provenance_digest"] = hashlib.sha256(
            canonical_json_bytes(material)
        ).hexdigest()
        payload = changed["payload"]
        payload["source_event_cut_sha256"] = source_event_cut_sha256(payload["records"])
        payload["revision_pin_sha256"] = revision_pin_sha256(
            payload["case_id"],
            payload["case_revision"],
            payload["source_event_cut_sha256"],
            payload["ledger_head_digest"],
        )
        with self.assertRaisesRegex(SourceValidationError, "over-recovers"):
            derive_case_facts(changed)

    def test_generation_is_byte_stable_and_manifest_covers_every_artifact(self):
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = Path(first_raw)
            second = Path(second_raw)
            manifest_a = generate_stage2_development_artifacts(PROJECT_ROOT, first)
            manifest_b = generate_stage2_development_artifacts(PROJECT_ROOT, second)
            self.assertEqual(manifest_a, manifest_b)

            files_a = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
            files_b = sorted(path.relative_to(second) for path in second.rglob("*") if path.is_file())
            self.assertEqual(files_a, files_b)
            for relative in files_a:
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes())

            expected_hashed = {path.as_posix() for path in files_a if path.name != "manifest.json"}
            self.assertEqual(expected_hashed, set(manifest_a["artifacts_sha256"]))
            for relative, expected_digest in manifest_a["artifacts_sha256"].items():
                self.assertEqual(
                    expected_digest,
                    hashlib.sha256((first / relative).read_bytes()).hexdigest(),
                )

    def test_generated_material_is_public_safe_and_has_no_trusted_truth_labels(self):
        forbidden_keys = {
            "expected_action",
            "expected_route",
            "fresh",
            "source_conflict",
            "remaining_quantity",
            "active_chargeback",
        }

        def keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)

        for batch in self.material["case_batches"]:
            self.assertTrue(forbidden_keys.isdisjoint(set(keys(batch))))
            rendered = json.dumps(batch, sort_keys=True).lower()
            self.assertNotIn("@", rendered)
            self.assertNotIn("customer_name", rendered)
            self.assertNotIn("address", rendered)
            self.assertNotIn("payment_instrument", rendered)

    def test_invalid_source_identity_time_and_policy_fail_closed(self):
        base = self.material["case_batches"][0]
        mutations = {}

        unknown_source = copy.deepcopy(base)
        unknown_source["payload"]["records"][0]["payload"]["source_name"] = "ERP"
        mutations["unknown source"] = unknown_source

        naive_time = copy.deepcopy(base)
        naive_time["payload"]["records"][0]["payload"]["observed_at"] = "2026-08-11T10:00:00"
        mutations["timezone"] = naive_time

        cross_case = copy.deepcopy(base)
        cross_case["payload"]["records"][0]["payload"]["case_id"] = "S2-CASE-9999"
        mutations["cross-case"] = cross_case

        duplicate = copy.deepcopy(base)
        duplicate["payload"]["records"].append(
            copy.deepcopy(duplicate["payload"]["records"][0])
        )
        mutations["duplicate"] = duplicate

        future_policy = copy.deepcopy(base)
        policy_record = next(
            row for row in future_policy["payload"]["records"]
            if row["payload"]["source_name"] == "POLICY"
        )
        policy_record["payload"]["effective_at"] = "2026-08-12T00:00:00Z"
        mutations["policy"] = future_policy

        for label, changed in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(SourceValidationError):
                    validate_source_batch(changed)

    def test_runtime_import_boundary_and_pure_facts_dependency(self):
        runtime_paths = [
            PROJECT_ROOT / "scripts" / "stage2_facts.py",
            PROJECT_ROOT / "scripts" / "stage2_current_state.py",
        ]
        forbidden = {
            "stage2_case_system",
            "stage1_case_system",
            "oracle",
            "evaluator",
            "release",
        }
        for path in runtime_paths:
            module = ast.parse(path.read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(module):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            with self.subTest(path=path.name):
                self.assertFalse(
                    [name for name in imports if any(token in name for token in forbidden)]
                )
        facts_text = runtime_paths[0].read_text(encoding="utf-8")
        self.assertIn("scripts.stage2_contracts", facts_text)

    def test_stage1_tracked_bytes_match_head(self):
        paths = subprocess.run(
            ["git", "ls-files", "data/stage1"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.assertGreater(len(paths), 20)
        for relative in paths:
            head = subprocess.run(
                ["git", "show", f"HEAD:{relative}"],
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
            ).stdout
            with self.subTest(path=relative):
                self.assertEqual(head, (PROJECT_ROOT / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()
