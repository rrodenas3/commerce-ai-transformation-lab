import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts.stage1_case_system import (
    CASE_FAMILIES,
    FOUNDATION_CASE_COUNT,
    build_foundation_cases,
    build_oracle,
    generate_stage1_artifacts,
    load_stage1_policy,
    validate_case,
    validate_oracle,
)
from scripts.stage1_deterministic_baseline import (
    decide_case,
    run_generated_baseline,
)
from scripts.score_stage1_manual import (
    PUBLIC_ORACLE_EXPOSURE_STATUS,
    RUN_MANIFEST_SCHEMA_VERSION,
    _verify_artifact,
    score_manual_records,
)
from scripts.stage1_scoring import evaluate_decisions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATED_FILENAMES = (
    "cases.jsonl",
    "oracle.jsonl",
    "manifest.json",
    "deterministic-decisions.jsonl",
    "deterministic-summary.json",
    "manual-baseline-template.csv",
    "manual-run-manifest-template.json",
)


class Stage1CaseSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_stage1_policy(PROJECT_ROOT)
        cls.cases = build_foundation_cases(cls.policy)
        cls.oracles = [build_oracle(case, cls.policy) for case in cls.cases]

    def test_foundation_set_is_balanced_and_deterministic(self):
        repeated = build_foundation_cases(self.policy)

        self.assertEqual(FOUNDATION_CASE_COUNT, len(self.cases))
        self.assertEqual(self.cases, repeated)
        self.assertEqual(
            {family: 3 for family in CASE_FAMILIES},
            Counter(case["case_family"] for case in self.cases),
        )
        self.assertEqual(
            FOUNDATION_CASE_COUNT,
            len({case["case_id"] for case in self.cases}),
        )

    def test_cases_are_synthetic_structured_and_public_safe(self):
        forbidden_fields = {
            "customer_name",
            "email",
            "phone",
            "postal_address",
            "free_text",
        }

        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                self.assertEqual([], validate_case(case, self.policy))
                self.assertTrue(case["synthetic"])
                self.assertTrue(case["order"]["order_id"].startswith("SYN-O-"))
                self.assertTrue(case["customer"]["customer_id"].startswith("SYN-C-"))
                serialized = json.dumps(case, sort_keys=True)
                self.assertNotIn("@", serialized)
                self.assertTrue(forbidden_fields.isdisjoint(self._all_keys(case)))

    def test_oracle_is_derived_only_from_case_and_policy(self):
        case = copy.deepcopy(self.cases[0])
        original = build_oracle(case, self.policy)
        case["model_output"] = {
            "recommended_action": "REFUND_FULL_ORDER",
            "confidence": 1.0,
        }

        self.assertEqual(original, build_oracle(case, self.policy))
        self.assertEqual([], validate_oracle(original, self.policy))

    def test_oracle_enforces_specialist_risk_route(self):
        risk_cases = [case for case in self.cases if case["risk_flags"]]

        self.assertGreaterEqual(len(risk_cases), 3)
        for case in risk_cases:
            with self.subTest(case_id=case["case_id"]):
                oracle = build_oracle(case, self.policy)
                self.assertEqual("specialist", oracle["required_route"])
                self.assertEqual(["ESCALATE_SPECIALIST"], oracle["allowed_actions"])
                self.assertTrue(oracle["critical_if_under_escalated"])

    def test_authoritative_chargeback_state_enforces_specialist_stop(self):
        case = copy.deepcopy(self.cases[0])
        case["risk_flags"] = []
        case["payment"]["active_chargeback"] = True

        oracle = build_oracle(case, self.policy)

        self.assertEqual("ESCALATE_SPECIALIST", oracle["preferred_action"])
        self.assertEqual("specialist", oracle["required_route"])
        self.assertEqual("policy_and_risk_owner", oracle["decision_owner"])
        self.assertIn("risk_stop:active_chargeback", oracle["rationale_codes"])
        self.assertFalse(oracle["eligible_recovery_case"])

    def test_verified_status_requires_action_specific_postcondition(self):
        refund_case = copy.deepcopy(self._case("duplicate_or_stale", 1))
        refund_case["payment"]["refunded_cents"] -= 1
        refund_oracle = build_oracle(refund_case, self.policy)
        self.assertEqual("ESCALATE_ACTION_RECOVERY", refund_oracle["preferred_action"])
        self.assertIn(
            "prior_action:verified_without_authoritative_postcondition",
            refund_oracle["rationale_codes"],
        )
        refund_case["payment"]["refunded_cents"] += 1
        self.assertEqual(
            "NO_ACTION_ALREADY_RECOVERED",
            build_oracle(refund_case, self.policy)["preferred_action"],
        )

        reship_case = copy.deepcopy(self.cases[0])
        reship_case["history"]["prior_action"] = {
            "action_id": "SYN-ACT-R1",
            "type": "RESHIP_MISSING",
            "status": "VERIFIED",
        }
        self.assertEqual(
            "ESCALATE_ACTION_RECOVERY",
            build_oracle(reship_case, self.policy)["preferred_action"],
        )
        reship_case["inventory"]["replacement_reservation"] = {
            "action_id": "SYN-ACT-R1",
            "status": "VERIFIED",
            "reserved_qty": reship_case["order"]["remaining_qty"],
        }
        self.assertEqual(
            "NO_ACTION_ALREADY_RECOVERED",
            build_oracle(reship_case, self.policy)["preferred_action"],
        )

    def test_source_freshness_is_derived_from_timezone_aware_age(self):
        case = copy.deepcopy(self.cases[0])
        carrier = case["evidence"]["sources"]["CARRIER"]
        carrier["as_of"] = "2026-08-08T22:00:00Z"
        carrier["fresh"] = True
        self.assertEqual([], validate_case(case, self.policy))

        carrier["as_of"] = "2026-08-08T21:59:59Z"
        self.assertTrue(
            any("CARRIER.fresh" in error for error in validate_case(case, self.policy))
        )
        carrier["fresh"] = False
        self.assertEqual([], validate_case(case, self.policy))

        carrier["as_of"] = "2026-08-09T10:00:01Z"
        self.assertTrue(
            any("cannot be after observed_at" in error for error in validate_case(case, self.policy))
        )
        carrier["as_of"] = "2026-08-09T09:00:00"
        self.assertTrue(
            any("timezone" in error for error in validate_case(case, self.policy))
        )

    def test_policy_boundaries_change_required_authority(self):
        delegated = self._case("authority_boundary", 1)
        approval = self._case("authority_boundary", 2)

        delegated_oracle = build_oracle(delegated, self.policy)
        approval_oracle = build_oracle(approval, self.policy)

        self.assertEqual("delegated", delegated_oracle["required_route"])
        self.assertEqual("approval", approval_oracle["required_route"])

    def test_recovery_denominator_excludes_controls_and_duplicate_no_action(self):
        eligible = [oracle for oracle in self.oracles if oracle["eligible_recovery_case"]]
        excluded = [oracle for oracle in self.oracles if not oracle["eligible_recovery_case"]]

        self.assertEqual(19, len(eligible))
        self.assertEqual(5, len(excluded))
        self.assertTrue(
            all(
                oracle["preferred_action"].startswith(("NO_ACTION", "ESCALATE_SPECIALIST"))
                for oracle in excluded
            )
        )

    def test_verified_prior_refund_has_matching_payment_postcondition(self):
        case = self._case("duplicate_or_stale", 1)

        self.assertEqual("VERIFIED", case["history"]["prior_action"]["status"])
        self.assertEqual(
            case["order"]["affected_value_cents"],
            case["payment"]["refunded_cents"],
        )

    def test_generated_artifacts_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory)
            result = generate_stage1_artifacts(PROJECT_ROOT, output_root)

            self.assertEqual(FOUNDATION_CASE_COUNT, result["case_count"])
            case_lines = (output_root / "cases.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            oracle_lines = (output_root / "oracle.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(FOUNDATION_CASE_COUNT, len(case_lines))
            self.assertEqual(FOUNDATION_CASE_COUNT, len(oracle_lines))
            self.assertEqual(self.cases[0], json.loads(case_lines[0]))

    def test_full_generated_artifact_set_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root = Path(first)
            second_root = Path(second)
            self._generate_full_artifact_set(first_root)
            self._generate_full_artifact_set(second_root)

            for filename in GENERATED_FILENAMES:
                with self.subTest(filename=filename):
                    self.assertEqual(
                        (first_root / filename).read_bytes(),
                        (second_root / filename).read_bytes(),
                    )

    def test_every_committed_generated_artifact_matches_full_reproduction(self):
        generated = PROJECT_ROOT / "data" / "stage1" / "generated"
        with tempfile.TemporaryDirectory() as directory:
            reproduced = Path(directory)
            self._generate_full_artifact_set(reproduced)
            all_committed = all(
                (generated / filename).is_file() for filename in GENERATED_FILENAMES
            )

            for filename in GENERATED_FILENAMES:
                with self.subTest(filename=filename):
                    self.assertTrue(
                        (generated / filename).is_file(),
                        f"committed generated artifact is missing: {filename}",
                    )
                    self.assertEqual(
                        (reproduced / filename).read_bytes(),
                        (generated / filename).read_bytes(),
                    )

            self._assert_generated_hash_contract(reproduced)
            if all_committed:
                self._assert_generated_hash_contract(generated)

    def test_deterministic_baseline_is_safe_and_explicitly_bounded(self):
        decisions = [decide_case(case, self.policy) for case in self.cases]
        summary = evaluate_decisions(
            self.cases,
            decisions,
            self.oracles,
            baseline_id="scc-01-deterministic-baseline-v1",
        )

        self.assertEqual(FOUNDATION_CASE_COUNT, summary["case_count"])
        self.assertEqual(0, summary["critical_violation_count"])
        self.assertEqual(0, summary["unsupported_fact_count"])
        self.assertGreater(summary["abstention_count"], 0)
        self.assertLess(summary["decision_coverage_rate"], 1.0)
        self.assertEqual(
            summary["case_count"],
            summary["successful_or_safe_escalation_count"],
        )

    def test_manual_record_scoring_preserves_human_work_measures_and_denominator(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        record = self._manual_record(case)

        summary = score_manual_records(
            [case],
            [oracle],
            [record],
            run_metadata=self._run_metadata([case], [oracle]),
        )

        self.assertEqual("manual-no-ai", summary["baseline_id"])
        self.assertEqual(240, summary["active_handling_seconds"]["median"])
        self.assertEqual(1, summary["handoff_count"])
        self.assertEqual(2, summary["policy_lookup_count"])
        self.assertEqual(0, summary["help_requested_count"])
        self.assertEqual(1, summary["assigned_case_count"])
        self.assertEqual(1, summary["completed_case_count"])
        self.assertEqual(0, summary["unresolved_case_count"])
        self.assertEqual([case["case_id"]], summary["assigned_case_ids"])
        self.assertEqual([case["case_id"]], summary["completed_case_ids"])
        self.assertEqual([], summary["unresolved_case_ids"])

    def test_blank_manual_template_rows_cannot_be_scored(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        with self.assertRaisesRegex(ValueError, "missing required manual field"):
            score_manual_records(
                [case],
                [oracle],
                [{"case_id": case["case_id"], "run_type": "manual-no-ai"}],
                run_metadata=self._run_metadata([case], [oracle]),
            )

    def test_manual_clocks_reject_malformed_naive_reversed_and_impossible_values(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        metadata = self._run_metadata([case], [oracle])
        mutations = (
            ("started_at_utc", "not-a-timestamp", "ISO-8601 UTC"),
            ("started_at_utc", "2026-08-09T10:00:00", "ISO-8601 UTC"),
            ("ended_at_utc", "2026-08-09T09:59:59Z", "must not be before"),
            ("active_handling_seconds", "241", "must not exceed elapsed"),
        )

        for field, value, expected_error in mutations:
            with self.subTest(field=field, value=value):
                record = self._manual_record(case)
                record[field] = value
                with self.assertRaisesRegex(ValueError, expected_error):
                    score_manual_records(
                        [case], [oracle], [record], run_metadata=metadata
                    )

    def test_manual_assignment_omission_and_unassigned_record_are_rejected(self):
        cases = self.cases[:2]
        oracles = self.oracles[:2]
        metadata = self._run_metadata(cases, oracles)

        with self.assertRaisesRegex(ValueError, "unresolved=.*SCC-01-FND-002"):
            score_manual_records(
                cases,
                oracles,
                [self._manual_record(cases[0])],
                run_metadata=metadata,
            )

        metadata = self._run_metadata([cases[0]], [oracles[0]])
        with self.assertRaisesRegex(ValueError, "unexpected=.*SCC-01-FND-002"):
            score_manual_records(
                cases,
                oracles,
                [self._manual_record(case) for case in cases],
                run_metadata=metadata,
            )

    def test_manual_run_manifest_contract_rejects_mismatches(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        record = self._manual_record(case)
        base = self._run_metadata([case], [oracle])
        mutations = (
            ("unknown assignment", lambda value: value["assigned_case_ids"].append("SCC-01-UNKNOWN"), "absent from pinned artifacts"),
            ("policy version", lambda value: value["policy"].update(version="9.9.9"), "pinned policy version"),
            ("oracle version", lambda value: value["oracle"].update(version="9.9.9"), "pinned oracle version"),
            ("record run type", lambda value: value.update(run_type="manual-no-ai-independent"), "record run_type"),
            ("oracle exposure", lambda value: value.update(oracle_exposure_status="blinded"), "oracle_exposure_status"),
        )

        for label, mutate, expected_error in mutations:
            with self.subTest(label=label):
                metadata = copy.deepcopy(base)
                mutate(metadata)
                with self.assertRaisesRegex(ValueError, expected_error):
                    score_manual_records(
                        [case], [oracle], [record], run_metadata=metadata
                    )

        malformed_hash = copy.deepcopy(base)
        malformed_hash["artifacts"]["cases"]["sha256"] = "not-a-hash"
        with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
            score_manual_records(
                [case], [oracle], [record], run_metadata=malformed_hash
            )

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "cases.jsonl"
            artifact.write_text("synthetic\n", encoding="utf-8")
            wrong_pin = {"path": str(artifact), "sha256": "0" * 64}
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _verify_artifact(artifact, wrong_pin, "cases")

    def test_scoring_rejects_empty_duplicate_and_mismatched_id_sets(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        decision = decide_case(case, self.policy)

        with self.assertRaisesRegex(ValueError, "cases must not be empty"):
            evaluate_decisions([], [], [], baseline_id="contract-test")
        with self.assertRaisesRegex(ValueError, "decisions contains duplicate case IDs"):
            evaluate_decisions(
                [case], [decision, copy.deepcopy(decision)], [oracle], baseline_id="contract-test"
            )
        with self.assertRaisesRegex(ValueError, "cases contains duplicate case IDs"):
            evaluate_decisions(
                [case, copy.deepcopy(case)], [decision], [oracle], baseline_id="contract-test"
            )
        with self.assertRaisesRegex(ValueError, "oracles contains duplicate case IDs"):
            evaluate_decisions(
                [case], [decision], [oracle, copy.deepcopy(oracle)], baseline_id="contract-test"
            )
        mismatched = copy.deepcopy(decision)
        mismatched["case_id"] = "SCC-01-NOT-THE-CASE"
        with self.assertRaisesRegex(ValueError, "identical unique case IDs"):
            evaluate_decisions(
                [case], [mismatched], [oracle], baseline_id="contract-test"
            )

    def test_named_critical_controls_capture_under_routing_and_duplicate_actions(self):
        approval_case = self._case("authority_boundary", 2)
        approval_oracle = build_oracle(approval_case, self.policy)
        under_routed = self._decision_for_oracle(approval_case, approval_oracle)
        under_routed["route"] = "delegated"
        approval_summary = evaluate_decisions(
            [approval_case], [under_routed], [approval_oracle], baseline_id="contract-test"
        )
        self.assertEqual(1, approval_summary["critical_violation_count"])
        self.assertEqual(
            {"unauthorised_consequential_action": 1},
            approval_summary["critical_control_violation_counts"],
        )

        duplicate_case = self._case("duplicate_or_stale", 2)
        duplicate_oracle = build_oracle(duplicate_case, self.policy)
        duplicate_action = self._decision_for_oracle(duplicate_case, duplicate_oracle)
        duplicate_action["recommended_action"] = "RESHIP_MISSING"
        duplicate_action["abstained"] = False
        duplicate_summary = evaluate_decisions(
            [duplicate_case], [duplicate_action], [duplicate_oracle], baseline_id="contract-test"
        )
        self.assertEqual(1, duplicate_summary["critical_violation_count"])
        self.assertEqual(
            {
                "duplicate_consequential_action": 1,
                "unauthorised_consequential_action": 1,
            },
            duplicate_summary["critical_control_violation_counts"],
        )

    def test_abstention_is_derived_from_action_and_contradictions_are_rejected(self):
        case = self._case("conflicting_evidence", 1)
        oracle = build_oracle(case, self.policy)
        decision = self._decision_for_oracle(case, oracle)
        decision.pop("abstained")

        summary = evaluate_decisions(
            [case], [decision], [oracle], baseline_id="contract-test"
        )
        self.assertEqual(1, summary["abstention_count"])
        self.assertEqual(0, summary["decision_coverage_count"])
        self.assertEqual(0.0, summary["decision_coverage_rate"])

        decision["abstained"] = False
        with self.assertRaisesRegex(ValueError, "abstained must match"):
            evaluate_decisions(
                [case], [decision], [oracle], baseline_id="contract-test"
            )

    def test_unknown_message_tokens_fail_without_echoing_sensitive_input(self):
        case = self.cases[0]
        oracle = self.oracles[0]
        decision = self._decision_for_oracle(case, oracle)
        sensitive_tokens = ["person@example.test", "Call customer at private number"]
        decision["message_facts"] = sensitive_tokens

        with self.assertRaises(ValueError) as captured:
            evaluate_decisions(
                [case], [decision], [oracle], baseline_id="contract-test"
            )
        serialized_error = json.dumps(captured.exception.args)
        for token in sensitive_tokens:
            self.assertNotIn(token, str(captured.exception))
            self.assertNotIn(token, serialized_error)

    def test_generated_manual_manifest_truthfully_pins_public_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory)
            self._generate_full_artifact_set(generated)
            manifest = json.loads(
                (generated / "manual-run-manifest-template.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(RUN_MANIFEST_SCHEMA_VERSION, manifest["schema_version"])
            self.assertEqual("public-foundation-discovery", manifest["dataset_role"])
            self.assertEqual("manual-no-ai", manifest["run_type"])
            self.assertEqual(
                PUBLIC_ORACLE_EXPOSURE_STATUS, manifest["oracle_exposure_status"]
            )
            self.assertEqual(
                [case["case_id"] for case in self.cases], manifest["assigned_case_ids"]
            )
            self.assertEqual(self.policy["version"], manifest["policy"]["version"])
            self.assertEqual("1.0.0", manifest["oracle"]["version"])
            self.assertEqual(
                {
                    "cases": "data/stage1/generated/cases.jsonl",
                    "oracle": "data/stage1/generated/oracle.jsonl",
                    "policy": "data/stage1/policy.json",
                    "artifact_manifest": "data/stage1/generated/manifest.json",
                },
                {
                    name: pin["path"]
                    for name, pin in manifest["artifacts"].items()
                },
            )
            self._assert_generated_hash_contract(generated)

    def _case(self, family: str, ordinal: int) -> dict:
        matching = [case for case in self.cases if case["case_family"] == family]
        return matching[ordinal - 1]

    def _manual_record(self, case: dict) -> dict[str, str]:
        expected = decide_case(case, self.policy)
        return {
            "case_id": case["case_id"],
            "reviewer_code": "REV-01",
            "run_type": "manual-no-ai",
            "started_at_utc": "2026-08-09T10:00:00Z",
            "ended_at_utc": "2026-08-09T10:04:00Z",
            "active_handling_seconds": "240",
            "recommended_action": expected["recommended_action"],
            "route": expected["route"],
            "evidence_used_pipe_delimited": "|".join(expected["evidence_used"]),
            "message_facts_pipe_delimited": "|".join(expected["message_facts"]),
            "confidence_1_to_5": "4",
            "help_requested": "false",
            "handoff_count": "1",
            "policy_lookup_count": "2",
            "notes_without_personal_data": "Used the policy boundary table.",
        }

    def _run_metadata(
        self,
        cases: list[dict],
        oracles: list[dict],
        *,
        run_type: str = "manual-no-ai",
    ) -> dict:
        return {
            "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
            "dataset_role": "public-foundation-discovery",
            "run_type": run_type,
            "oracle_exposure_status": PUBLIC_ORACLE_EXPOSURE_STATUS,
            "policy": {
                "policy_id": self.policy["policy_id"],
                "version": self.policy["version"],
            },
            "oracle": {"version": oracles[0]["oracle_version"]},
            "assigned_case_ids": [case["case_id"] for case in cases],
            "artifacts": {
                name: {
                    "path": f"synthetic-test/{name}",
                    "sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
                }
                for name in ("cases", "oracle", "policy", "artifact_manifest")
            },
        }

    def _decision_for_oracle(self, case: dict, oracle: dict) -> dict:
        action = oracle["preferred_action"]
        return {
            "case_id": case["case_id"],
            "recommended_action": action,
            "route": oracle["required_route"],
            "evidence_used": list(oracle["required_evidence"]),
            "message_facts": [],
            "abstained": action.startswith("ESCALATE_"),
            "executed_action": False,
            "postcondition_verified": False,
        }

    def _generate_full_artifact_set(self, generated: Path) -> None:
        generate_stage1_artifacts(PROJECT_ROOT, generated)
        run_generated_baseline(PROJECT_ROOT, generated)

    def _assert_generated_hash_contract(self, generated: Path) -> None:
        source_manifest = json.loads(
            (generated / "manifest.json").read_text(encoding="utf-8")
        )
        for filename in ("cases.jsonl", "oracle.jsonl"):
            with self.subTest(manifest="source", filename=filename):
                digest = hashlib.sha256((generated / filename).read_bytes()).hexdigest()
                self.assertEqual(digest, source_manifest["artifacts_sha256"][filename])

        run_manifest = json.loads(
            (generated / "manual-run-manifest-template.json").read_text(
                encoding="utf-8"
            )
        )
        artifact_paths = {
            "cases": generated / "cases.jsonl",
            "oracle": generated / "oracle.jsonl",
            "policy": PROJECT_ROOT / "data" / "stage1" / "policy.json",
            "artifact_manifest": generated / "manifest.json",
        }
        for name, path in artifact_paths.items():
            with self.subTest(manifest="manual-run", artifact=name):
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    run_manifest["artifacts"][name]["sha256"],
                )

    def _all_keys(self, value):
        if isinstance(value, dict):
            keys = set(value)
            for nested in value.values():
                keys.update(self._all_keys(nested))
            return keys
        if isinstance(value, list):
            keys = set()
            for nested in value:
                keys.update(self._all_keys(nested))
            return keys
        return set()


if __name__ == "__main__":
    unittest.main()
