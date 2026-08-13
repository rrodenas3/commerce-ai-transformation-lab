import ast
import copy
import json
import unittest
from pathlib import Path

from scripts.stage2_contracts import (
    ContractValidationError,
    canonical_json_bytes,
    decide_next_gate,
    load_canonical_json,
    load_evaluation_contract,
    validate_evaluation_contract,
    validate_neutral_record,
)
from scripts.verify_public_safety import (
    check_artifact_metadata,
    load_policy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = PROJECT_ROOT / "data" / "stage2" / "evaluation-contract.json"


class Stage2EvaluationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_evaluation_contract(CONTRACT_PATH)

    def test_committed_contract_is_canonical_and_valid(self):
        payload = CONTRACT_PATH.read_bytes()
        self.assertEqual(canonical_json_bytes(self.contract), payload)
        self.assertEqual(
            "stage2-evaluation-contract/v1", self.contract["schema_version"]
        )
        self.assertEqual("preregistered-before-results", self.contract["status"])

    def test_contract_requires_every_preregistered_section(self):
        required_sections = {
            "artifact_metadata",
            "release_boundary",
            "case_plan",
            "outcome_definitions",
            "metrics",
            "exact_zero_controls",
            "decision_precedence",
            "decision_inputs",
            "virtual_time",
            "evaluation_release",
            "enablement_readiness",
            "claim_template",
        }
        self.assertTrue(required_sections.issubset(self.contract))
        for section in sorted(required_sections):
            with self.subTest(section=section):
                changed = copy.deepcopy(self.contract)
                del changed[section]
                with self.assertRaisesRegex(
                    ContractValidationError, f"missing field.*{section}"
                ):
                    validate_evaluation_contract(changed)

    def test_metric_contract_requires_formula_denominator_and_evidence_label(self):
        for field in ("formula", "denominator", "evidence_label"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.contract)
                del changed["metrics"][0][field]
                with self.assertRaisesRegex(
                    ContractValidationError, f"missing field.*{field}"
                ):
                    validate_evaluation_contract(changed)

    def test_case_plan_freezes_counts_families_and_acceptance_coverage(self):
        plan = self.contract["case_plan"]
        self.assertEqual(24, plan["development_case_count"])
        self.assertEqual(36, plan["confirmatory_case_count"])
        self.assertEqual(12, plan["family_count"])
        self.assertEqual(12, len(plan["families"]))
        self.assertEqual(
            {f"AE{number}" for number in range(1, 21)},
            set(plan["acceptance_example_coverage"]),
        )

        changed = copy.deepcopy(self.contract)
        changed["case_plan"]["confirmatory_case_count"] = 35
        with self.assertRaisesRegex(ContractValidationError, "confirmatory_case_count"):
            validate_evaluation_contract(changed)

        changed = copy.deepcopy(self.contract)
        del changed["case_plan"]["acceptance_example_coverage"]["AE15"]
        with self.assertRaisesRegex(ContractValidationError, "acceptance example"):
            validate_evaluation_contract(changed)

    def test_verified_remedy_excludes_routes_receipts_and_unobserved_outcomes(self):
        definitions = self.contract["outcome_definitions"]
        verified = definitions["verified_remedy"]
        self.assertEqual(["REFUND", "RESHIP"], verified["eligible_operations"])
        self.assertIn("safe_route", verified["does_not_include"])
        self.assertIn("adapter_receipt", verified["does_not_include"])
        self.assertEqual(
            "not_observed", definitions["customer_delivery"]["observation_status"]
        )
        self.assertEqual(
            "not_observed", definitions["customer_satisfaction"]["observation_status"]
        )
        self.assertEqual(
            "not_observed", definitions["realised_value"]["observation_status"]
        )

        changed = copy.deepcopy(self.contract)
        changed["outcome_definitions"]["verified_remedy"]["eligible_operations"].append(
            "SAFE_ROUTE"
        )
        with self.assertRaisesRegex(ContractValidationError, "safe route"):
            validate_evaluation_contract(changed)

    def test_human_and_adoption_measures_are_not_observed_not_zero(self):
        expected = {
            "adoption",
            "customer_satisfaction",
            "enablement_friction",
            "manual_review_time",
            "realised_savings",
            "retained_revenue",
            "trust",
        }
        self.assertEqual(
            {name for name, value in self.contract["human_measures"].items() if value == "not_observed"},
            expected,
        )

        changed = copy.deepcopy(self.contract)
        changed["human_measures"]["adoption"] = 0
        with self.assertRaisesRegex(ContractValidationError, "human_measures.adoption"):
            validate_evaluation_contract(changed)

    def test_maturity_and_claim_boundary_fail_closed(self):
        boundary = self.contract["release_boundary"]
        self.assertEqual("local-mvp", boundary["maturity_cap"])
        self.assertEqual("bounded_experiment_only", boundary["scale_scope"])

        for unsupported in ("pilot", "production"):
            with self.subTest(unsupported=unsupported):
                changed = copy.deepcopy(self.contract)
                changed["release_boundary"]["maturity_cap"] = unsupported
                with self.assertRaisesRegex(ContractValidationError, "maturity_cap"):
                    validate_evaluation_contract(changed)

    def test_decision_precedence_is_preregistered_and_exact_zero_wins(self):
        self.assertEqual(
            [
                "stop",
                "pause",
                "revise",
                "scale_next_experiment",
            ],
            [entry["decision"] for entry in self.contract["decision_precedence"]],
        )
        self.assertEqual(
            "stop",
            decide_next_gate(
                self.contract,
                exact_zero_failures=["ORACLE_CONTAMINATION"],
                incomplete_evidence=True,
                pre_run_exposure=True,
                quality_gate_passed=False,
                reliability_gate_passed=False,
                cost_gate_passed=False,
            ),
        )
        self.assertEqual(
            "pause",
            decide_next_gate(self.contract, incomplete_evidence=True),
        )
        self.assertEqual(
            "pause",
            decide_next_gate(self.contract, pre_run_exposure=True),
        )
        for gate in (
            "quality_gate_passed",
            "reliability_gate_passed",
            "cost_gate_passed",
        ):
            with self.subTest(gate=gate):
                signals = {gate: False}
                self.assertEqual("revise", decide_next_gate(self.contract, **signals))
        self.assertEqual("scale_next_experiment", decide_next_gate(self.contract))

    def test_unknown_exact_zero_signal_is_rejected(self):
        with self.assertRaisesRegex(ContractValidationError, "unknown exact-zero"):
            decide_next_gate(
                self.contract, exact_zero_failures=["UNREGISTERED_CONTROL"]
            )

    def test_release_states_and_oracle_boundary_are_irreversible(self):
        release = self.contract["evaluation_release"]
        self.assertEqual(
            [
                "running",
                "freeze-prepared",
                "output-frozen",
                "eligibility-verified",
                "oracle-released",
                "scored",
            ],
            release["states"],
        )
        self.assertEqual(
            "regression_only", release["post_oracle_adaptation_on_same_pack"]
        )
        self.assertTrue(release["new_pack_required_for_confirmatory_claim"])

    def test_documentation_metadata_passes_existing_public_safety_parser(self):
        policy = load_policy(PROJECT_ROOT)
        for relative in ("docs/DECISION_LOG.md", "docs/EVIDENCE_POLICY.md"):
            with self.subTest(relative=relative):
                text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(
                    [],
                    check_artifact_metadata(
                        relative, text, policy, canonical_source=False
                    ),
                )

    def test_decision_and_evidence_policy_state_creator_evaluated_boundary(self):
        decision_log = (PROJECT_ROOT / "docs" / "DECISION_LOG.md").read_text(
            encoding="utf-8"
        )
        evidence_policy = (PROJECT_ROOT / "docs" / "EVIDENCE_POLICY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("D-023", decision_log)
        self.assertIn("creator-evaluated", decision_log)
        self.assertIn("local-mvp", decision_log)
        self.assertIn("Simulated-role records", evidence_policy)
        self.assertIn("not human-reviewed evidence", evidence_policy)


class Stage2NeutralSerializationTests(unittest.TestCase):
    def test_canonical_json_round_trip_has_stable_utf8_lf_bytes(self):
        value = {
            "schema_version": "stage2-source-record/v1",
            "record_type": "source_record",
            "record_id": "S2-SRC-0001",
            "payload": {"synthetic": True, "quantity": 2, "label": "café"},
        }
        expected = (
            '{"payload":{"label":"café","quantity":2,"synthetic":true},'
            '"record_id":"S2-SRC-0001","record_type":"source_record",'
            '"schema_version":"stage2-source-record/v1"}\n'
        ).encode("utf-8")
        payload = canonical_json_bytes(value)
        self.assertEqual(expected, payload)
        self.assertEqual(value, load_canonical_json(payload))
        self.assertEqual(payload, canonical_json_bytes(load_canonical_json(payload)))

    def test_parser_rejects_duplicate_keys_nonfinite_numbers_and_floats(self):
        invalid_payloads = {
            "duplicate": b'{"a":1,"a":2}\n',
            "nan": b'{"value":NaN}\n',
            "infinity": b'{"value":Infinity}\n',
            "float": b'{"value":1.5}\n',
        }
        for name, payload in invalid_payloads.items():
            with self.subTest(name=name):
                with self.assertRaises(ContractValidationError):
                    load_canonical_json(payload)

    def test_neutral_record_rejects_unknown_schema_vocabulary_and_fields(self):
        valid = {
            "schema_version": "stage2-source-record/v1",
            "record_type": "source_record",
            "record_id": "S2-SRC-0001",
            "payload": {"synthetic": True},
        }
        self.assertEqual(valid, validate_neutral_record(valid))

        mutations = {
            "schema": ("schema_version", "stage2-source-record/v2"),
            "vocabulary": ("record_type", "oracle_record"),
            "identifier": ("record_id", "real-customer-1"),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(valid)
                changed[field] = value
                with self.assertRaises(ContractValidationError):
                    validate_neutral_record(changed)

        changed = copy.deepcopy(valid)
        changed["unexpected"] = True
        with self.assertRaisesRegex(ContractValidationError, "unknown field"):
            validate_neutral_record(changed)

    def test_neutral_record_rejects_evaluator_only_fields_at_any_depth(self):
        record = {
            "schema_version": "stage2-source-record/v1",
            "record_type": "source_record",
            "record_id": "S2-SRC-0001",
            "payload": {"nested": {"expected_action": "REFUND"}},
        }
        with self.assertRaisesRegex(ContractValidationError, "evaluator-only"):
            validate_neutral_record(record)

    def test_contract_module_has_only_standard_library_imports(self):
        path = PROJECT_ROOT / "scripts" / "stage2_contracts.py"
        module = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module.split(".")[0]
            for node in ast.walk(module)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name.split(".")[0]
            for node in ast.walk(module)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertTrue(
            imports.issubset(
                {"__future__", "hashlib", "json", "re", "pathlib", "typing"}
            )
        )


if __name__ == "__main__":
    unittest.main()
