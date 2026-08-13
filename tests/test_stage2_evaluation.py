from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts.evaluate_recovery_workflow import (
    EvaluationIntegrityError,
    apply_thresholds,
    evaluate_raw_outputs,
)
from scripts.generate_stage2_evaluation import (
    DEFAULT_PRIVATE_ROOT,
    DEFAULT_PUBLIC_ROOT,
    EvaluationPackError,
    PACK_ID,
    PACK_SCHEMA,
    build_confirmatory_material,
    build_fault_inventory,
    build_adaptation_ledger,
    build_acquisition_contract,
    execute_fault_inventory,
    select_adaptation_fault,
    verify_evaluation_pack,
    validate_recorded_attempts,
    write_evaluation_pack,
    resolve_clean_git_binding,
    main as generator_main,
    _test_only_unavailable_attempts,
)
from scripts.recovery_recommender import (
    CandidateValidationError,
    DEFAULT_PROVIDER_ENVELOPE,
    _BIDI_PATTERN,
    _INSTRUCTION_PATTERNS,
    parse_candidate,
)
from scripts.stage2_contracts import EVALUATOR_ONLY_FIELDS, canonical_json_bytes, canonical_sha256
from scripts.run_stage2_isolated import run_inner_evaluation
from scripts.recovery_policy import decide_policy
from scripts.stage2_facts import derive_case_facts


ROOT = Path(__file__).resolve().parents[1]
FAKE_COMMIT = "1" * 40
FAKE_TREE = "2" * 40


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_walk_keys(item))
    return keys


class Stage2EvaluationGenerationTests(unittest.TestCase):
    def test_v6_is_the_only_current_confirmatory_identity_and_default_path(self) -> None:
        self.assertEqual(PACK_ID, "S2-EVALUATION-20260812-V6")
        self.assertEqual(PACK_SCHEMA, "stage2-confirmatory-pack/v6")
        self.assertEqual(DEFAULT_PUBLIC_ROOT, Path("data/stage2/evaluation/v6"))
        self.assertEqual(DEFAULT_PRIVATE_ROOT, Path("artifacts/private/stage2-evaluation/v6"))

    def test_v1_pre_run_invalidation_is_public_safe_and_binds_manifest(self) -> None:
        record = json.loads(
            (ROOT / "data/stage2/development/evaluation-v1-pre-run-invalidation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["pack_id"], "S2-EVALUATION-20260811-V1")
        self.assertEqual(record["status"], "invalidated-before-run")
        self.assertEqual(
            record["public_manifest_sha256"],
            "4ac8438652f425dc34ac2aa8feb4c39a2329cc661d5b77533621a3dc289dafa4",
        )
        self.assertFalse(record["container_run_started"])
        self.assertFalse(record["oracle_released"])
        self.assertFalse(record["score_created"])
        self.assertEqual(record["private_oracle_nonce_status"], "unopened")
        self.assertTrue(record["new_pack_required"])
        self.assertFalse(
            _walk_keys(record)
            & {"oracle_commitment", "oracle_commitment_sha256", "oracle_sha256", "nonce", "nonce_hex"}
        )

    def test_v2_pre_run_invalidation_is_public_safe_and_binds_manifest(self) -> None:
        record = json.loads(
            (ROOT / "data/stage2/development/evaluation-v2-pre-run-invalidation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["pack_id"], "S2-EVALUATION-20260812-V2")
        self.assertEqual(record["status"], "invalidated-before-run")
        self.assertEqual(record["release_state"], "not-started")
        self.assertEqual(
            record["public_manifest_sha256"],
            "adec702022af219179ff7ff197d6bb319d1363002d40aabb7cbd5dd72b863507",
        )
        self.assertFalse(record["container_run_started"])
        self.assertFalse(record["state_advanced"])
        self.assertFalse(record["oracle_released"])
        self.assertFalse(record["score_created"])
        self.assertEqual(record["private_oracle_nonce_status"], "unopened")
        self.assertTrue(record["new_pack_required"])
        self.assertIn("non-reproducible", record["reason"])
        self.assertFalse(
            _walk_keys(record)
            & {"oracle_commitment", "oracle_commitment_sha256", "oracle_sha256", "nonce", "nonce_hex"}
        )

    def test_v3_pre_run_invalidation_is_public_safe_and_binds_manifest(self) -> None:
        record = json.loads(
            (ROOT / "data/stage2/development/evaluation-v3-pre-run-invalidation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["pack_id"], "S2-EVALUATION-20260812-V3")
        self.assertEqual(record["status"], "invalidated-before-run")
        self.assertEqual(record["release_state"], "not-started")
        self.assertEqual(
            record["public_manifest_sha256"],
            "ff79e009c6234d9a7a635061b111f208137990e445b3427a787b93e4cefeacf3",
        )
        self.assertFalse(record["container_run_started"])
        self.assertFalse(record["state_advanced"])
        self.assertFalse(record["oracle_released"])
        self.assertFalse(record["score_created"])
        self.assertEqual(record["private_oracle_nonce_status"], "unopened")
        self.assertTrue(record["new_pack_required"])
        self.assertIn("explicit clean source", record["reason"])
        self.assertFalse(
            _walk_keys(record)
            & {"oracle_commitment", "oracle_commitment_sha256", "oracle_sha256", "nonce", "nonce_hex"}
        )

    def test_v4_pre_run_invalidation_is_public_safe_and_binds_manifest(self) -> None:
        record = json.loads(
            (ROOT / "data/stage2/development/evaluation-v4-pre-run-invalidation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["pack_id"], "S2-EVALUATION-20260812-V4")
        self.assertEqual(record["status"], "invalidated-before-run")
        self.assertEqual(record["release_state"], "running")
        self.assertEqual(record["preparation_status"], "passed-canonical")
        self.assertTrue(record["canonical_eligible"])
        self.assertEqual(
            record["public_manifest_sha256"],
            "ea4ab65e0b8204f0c796ffa855d9fe2a7da0dd6621c4d6c3b88519e4d571966e",
        )
        self.assertFalse(record["container_run_started"])
        self.assertTrue(record["state_advanced"])
        self.assertFalse(record["output_materialized"])
        self.assertFalse(record["attestation_created"])
        self.assertFalse(record["evaluated_execution_observed"])
        self.assertFalse(record["oracle_released"])
        self.assertFalse(record["score_created"])
        self.assertEqual(record["private_oracle_nonce_status"], "unopened")
        self.assertTrue(record["new_pack_required"])
        self.assertIn("inline JSON", record["reason"])
        self.assertEqual(
            record["seccomp_profile_binding_status"],
            "path-only-not-committed-byte-bound",
        )
        self.assertEqual(len(record["additional_pre_run_control_findings"]), 1)
        self.assertFalse(
            _walk_keys(record)
            & {"oracle_commitment", "oracle_commitment_sha256", "oracle_sha256", "nonce", "nonce_hex"}
        )

    def test_v5_pre_run_invalidation_is_public_safe_and_binds_manifest(self) -> None:
        record = json.loads(
            (ROOT / "data/stage2/development/evaluation-v5-pre-run-invalidation.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["pack_id"], "S2-EVALUATION-20260812-V5")
        self.assertEqual(record["status"], "invalidated-before-run")
        self.assertEqual(record["release_state"], "running")
        self.assertEqual(record["preparation_status"], "passed-canonical")
        self.assertTrue(record["canonical_eligible"])
        self.assertEqual(
            record["public_manifest_sha256"],
            "27b246c74492791b7be40e0fd74dceb66599cb29a9e6006948b432adb27878a2",
        )
        self.assertTrue(record["seccomp_proof_passed"])
        self.assertEqual(record["failure_boundary"], "mount-identity-check-before-start")
        self.assertFalse(record["container_run_started"])
        self.assertFalse(record["evaluated_execution_observed"])
        self.assertFalse(record["output_materialized"])
        self.assertFalse(record["attestation_created"])
        self.assertFalse(record["oracle_released"])
        self.assertFalse(record["score_created"])
        self.assertEqual(record["private_oracle_nonce_status"], "unopened")
        self.assertTrue(record["new_pack_required"])
        self.assertIn("UTF-8", record["reason"])
        self.assertIn("mojibake", record["reason"])
        self.assertFalse(
            _walk_keys(record)
            & {"oracle_commitment", "oracle_commitment_sha256", "oracle_sha256", "nonce", "nonce_hex"}
        )

    def test_fault_inventory_is_frozen_ranked_and_complete(self) -> None:
        inventory = build_fault_inventory(ROOT)
        self.assertEqual(inventory["status"], "frozen-before-execution")
        self.assertEqual(len(inventory["faults"]), 24)
        self.assertEqual(
            [item["selection_rank"] for item in inventory["faults"]],
            list(range(1, 25)),
        )
        self.assertEqual(len({item["development_case_id"] for item in inventory["faults"]}), 24)

    def test_fault_selection_rejects_convenient_lower_ranked_failure(self) -> None:
        inventory = build_fault_inventory(ROOT)
        results = [
            {
                "fault_id": item["fault_id"],
                "original_result": "FAILED" if item["selection_rank"] in {1, 4} else "CONTROL_HELD",
                "original_trace_sha256": hashlib.sha256(item["fault_id"].encode()).hexdigest(),
                "resolved_before_selection": False,
            }
            for item in inventory["faults"]
        ]
        selected = select_adaptation_fault(inventory, results)
        self.assertEqual(selected["selection_rank"], 1)
        with self.assertRaises(EvaluationPackError):
            select_adaptation_fault(inventory, results, requested_fault_id=inventory["faults"][3]["fault_id"])

    def test_fault_selection_requires_frozen_inventory_and_all_results(self) -> None:
        inventory = build_fault_inventory(ROOT)
        results = [
            {
                "fault_id": item["fault_id"],
                "original_result": "CONTROL_HELD",
                "original_trace_sha256": hashlib.sha256(item["fault_id"].encode()).hexdigest(),
                "resolved_before_selection": False,
            }
            for item in inventory["faults"]
        ]
        mutated = dict(inventory, status="draft")
        with self.assertRaises(EvaluationPackError):
            select_adaptation_fault(mutated, results)
        with self.assertRaises(EvaluationPackError):
            select_adaptation_fault(inventory, results[:-1])

    def test_every_fault_is_executed_and_adaptation_binds_actual_regression(self) -> None:
        inventory = build_fault_inventory(ROOT)
        results = execute_fault_inventory(ROOT, inventory)
        self.assertEqual(len(results), 24)
        self.assertEqual(results[0]["original_result"], "FAILED")
        self.assertTrue(all(item["original_trace"]["control_function"] for item in results))
        _, preserved, ledger = build_adaptation_ledger(ROOT)
        self.assertEqual(preserved, results)
        self.assertTrue(ledger[0]["regression_runtime_evidence"]["observed_rejection"])
        self.assertEqual(ledger[0]["original_trace_sha256"], results[0]["original_trace_sha256"])

    def test_confirmatory_material_has_three_new_cases_per_frozen_family(self) -> None:
        material = build_confirmatory_material(ROOT)
        cases = material["cases"]
        oracles = material["oracle"]
        self.assertEqual(len(cases), 36)
        self.assertEqual(len(oracles), 36)
        self.assertEqual(set(Counter(row["family_id"] for row in oracles).values()), {3})
        self.assertTrue(all("family_id" not in row for row in material["schedules"]))
        development = {
            json.loads(line)["record_id"]
            for line in (ROOT / "data/stage2/development/cases.jsonl").read_text(encoding="utf-8").splitlines()
        }
        self.assertFalse({case["record_id"] for case in cases} & development)
        joined = canonical_json_bytes({"cases": cases, "schedules": material["schedules"]})
        self.assertNotIn(b"S1-", joined)
        self.assertNotIn(b"PERSONA", joined.upper())
        for case in cases:
            self.assertFalse(_walk_keys(case) & EVALUATOR_ONLY_FIELDS)
        for case, oracle in zip(cases, oracles, strict=True):
            decision = decide_policy(derive_case_facts(case))
            self.assertEqual(decision.proposed_action, oracle["expected_action"])
            self.assertEqual(decision.authority_route, oracle["expected_route"])

    def test_public_pack_uses_commitment_not_oracle_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            private = root / "artifacts/private"
            nonce = bytes(range(32))
            manifest = write_evaluation_pack(
                ROOT,
                public,
                private,
                source_commit=FAKE_COMMIT,
                source_tree=FAKE_TREE,
                nonce=nonce,
            )
            self.assertTrue((private / "oracle.jsonl").is_file())
            self.assertTrue((private / "oracle-nonce.bin").is_file())
            self.assertFalse((public / "oracle.jsonl").exists())
            expected = hashlib.sha256((private / "oracle.jsonl").read_bytes() + nonce).hexdigest()
            self.assertEqual(manifest["oracle_commitment_sha256"], expected)
            self.assertEqual(verify_evaluation_pack(ROOT, public, private), [])

    def test_public_manifest_has_only_anonymous_coverage_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            private = root / "private"
            manifest = write_evaluation_pack(
                ROOT, public, private,
                source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"c" * 32,
            )
            self.assertNotIn("family_counts", manifest)
            self.assertFalse(any("family" in key.casefold() for key in _walk_keys(manifest)))
            self.assertEqual(manifest["coverage_group_count"], 12)
            self.assertEqual(manifest["cases_per_coverage_group"], 3)
            self.assertEqual(manifest["coverage_mapping_status"], "private-until-oracle-release")

            old = dict(manifest)
            old["family_counts"] = {"reliable_eta_wait": 3}
            (public / "manifest.json").chmod(0o644)
            (public / "manifest.json").write_bytes(canonical_json_bytes(old))
            self.assertTrue(any("coverage" in error for error in verify_evaluation_pack(ROOT, public)))

    def test_generator_default_verify_is_public_only_when_private_material_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            private = root / "private"
            write_evaluation_pack(
                ROOT,
                public,
                private,
                source_commit=FAKE_COMMIT,
                source_tree=FAKE_TREE,
                nonce=b"v" * 32,
            )
            (private / "oracle.jsonl").chmod(0o644)
            (private / "oracle-nonce.bin").chmod(0o644)
            (private / "oracle.jsonl").unlink()
            (private / "oracle-nonce.bin").unlink()
            self.assertEqual(
                generator_main(
                    [
                        "--verify",
                        "--output", str(public),
                        "--private-root", str(private),
                    ]
                ),
                0,
            )

    def test_pack_verification_detects_case_mutation_and_missing_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            private = root / "artifacts/private"
            write_evaluation_pack(ROOT, public, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"x" * 32)
            cases = public / "cases.jsonl"
            cases.chmod(0o644)
            cases.write_bytes(cases.read_bytes() + b"{}\n")
            errors = verify_evaluation_pack(ROOT, public, private)
            self.assertTrue(any("cases.jsonl" in error for error in errors))

    def test_v6_pins_bind_pack_schema_and_committed_seccomp_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            private = root / "private"
            write_evaluation_pack(
                ROOT, public, private,
                source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"s" * 32,
            )
            pins = json.loads((public / "pins.json").read_text(encoding="utf-8"))
            self.assertEqual(pins["pack_id"], PACK_ID)
            self.assertEqual(pins["pack_schema"], PACK_SCHEMA)
            self.assertEqual(
                pins["seccomp_profile_sha256"],
                hashlib.sha256((ROOT / "containers/stage2-evaluation/seccomp.json").read_bytes()).hexdigest(),
            )

    def test_superseded_id_and_schema_each_fail_current_public_verifier(self) -> None:
        for field, value in (
            ("pack_id", "S2-EVALUATION-20260811-V1"),
            ("schema_version", "stage2-confirmatory-pack/v1"),
            ("pack_id", "S2-EVALUATION-20260812-V2"),
            ("schema_version", "stage2-confirmatory-pack/v2"),
            ("pack_id", "S2-EVALUATION-20260812-V3"),
            ("schema_version", "stage2-confirmatory-pack/v3"),
            ("pack_id", "S2-EVALUATION-20260812-V4"),
            ("schema_version", "stage2-confirmatory-pack/v4"),
            ("pack_id", "S2-EVALUATION-20260812-V5"),
            ("schema_version", "stage2-confirmatory-pack/v5"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                public = root / "public"
                private = root / "private"
                write_evaluation_pack(
                    ROOT, public, private,
                    source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"i" * 32,
                )
                manifest_path = public / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest[field] = value
                manifest_path.chmod(0o644)
                manifest_path.write_bytes(canonical_json_bytes(manifest))
                self.assertEqual(
                    verify_evaluation_pack(ROOT, public),
                    ["manifest.json: wrong or missing Stage 2 evaluation identity"],
                )

    def test_frozen_pack_is_equal_bytes_only_and_nonce_change_requires_new_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); public = root / "public"; private = root / "private"
            write_evaluation_pack(ROOT, public, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"a" * 32)
            write_evaluation_pack(ROOT, public, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"a" * 32)
            with self.assertRaises(EvaluationPackError):
                write_evaluation_pack(ROOT, public, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"b" * 32)

    def test_canonical_binding_resolves_real_clean_git_export_and_rejects_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "stage2@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Stage2 Test"], check=True)
            (repo / "source.txt").write_text("frozen\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "source"], check=True)
            binding = resolve_clean_git_binding(repo)
            self.assertEqual(binding["source_binding_status"], "verified-clean-git-export")
            self.assertEqual(len(binding["source_export_sha256"]), 64)
            (repo / "dirty.txt").write_text("untracked\n", encoding="utf-8")
            with self.assertRaises(EvaluationPackError):
                resolve_clean_git_binding(repo)

    def test_canonical_pack_rejects_generator_created_unavailable_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binding = {
                "source_binding_status": "verified-clean-git-export",
                "source_commit": FAKE_COMMIT,
                "source_export_sha256": "3" * 64,
                "source_tree": FAKE_TREE,
            }
            with self.assertRaises(EvaluationPackError):
                write_evaluation_pack(
                    ROOT, root / "public", root / "private",
                    source_commit=FAKE_COMMIT, source_tree=FAKE_TREE,
                    nonce=b"n" * 32, source_binding=binding,
                )

    def test_recorded_attempt_denominator_is_complete_and_not_oracle_bearing(self) -> None:
        material = build_confirmatory_material(ROOT)
        requests = material["provider_requests"]
        self.assertEqual(len(requests), 36)
        self.assertEqual({item["case_id"] for item in requests}, {item["case_id"] for item in material["schedules"]})
        self.assertFalse(_walk_keys(requests) & EVALUATOR_ONLY_FIELDS)
        self.assertTrue(all("family_id" not in _walk_keys(item) for item in requests))

    def test_acquisition_v2_discloses_exact_machine_candidate_and_attempt_contracts(self) -> None:
        material = build_confirmatory_material(ROOT)
        contract = build_acquisition_contract(material)
        self.assertEqual(contract["acquisition_id"], "S2-RECORDED-AI-ACQUISITION-20260811-V2")
        self.assertEqual(contract["pack_id"], "S2-EVALUATION-20260812-V6")
        self.assertEqual(contract["schema_version"], "stage2-confirmatory-provider-acquisition/v2")
        candidate = contract["candidate_output_contract"]
        self.assertEqual(candidate["schema_version_literal"], "stage2-provider-candidate/v1")
        self.assertEqual(
            set(candidate["exact_fields"]),
            {
                "candidate_id", "case_id", "case_revision", "cited_evidence",
                "material_limitations", "message_fact_candidates", "proposed_action",
                "proposed_route", "rejected_alternatives", "schema_version", "uncertainty",
            },
        )
        self.assertIn("WAIT_VERIFIED_ETA", candidate["allowed_proposed_actions"])
        self.assertNotIn("WAIT", candidate["allowed_proposed_actions"])
        self.assertIn("DELEGATED_DECISION", candidate["allowed_proposed_routes"])
        self.assertNotIn("DELEGATED", candidate["allowed_proposed_routes"])
        self.assertIn("WAIT_ESTIMATE_QUALIFIED", candidate["allowed_message_fact_candidates"])
        self.assertIn("S2-SRC-", candidate["citation_requirement"])
        self.assertEqual(candidate["parser_envelope"], DEFAULT_PROVIDER_ENVELOPE.to_dict())
        restrictions = candidate["text_restrictions"]
        self.assertEqual(restrictions["bidirectional_control_pattern"], _BIDI_PATTERN.pattern)
        self.assertEqual(restrictions["instruction_patterns_casefolded"], list(_INSTRUCTION_PATTERNS))
        self.assertIn("U+0020", restrictions["control_character_rule"])
        attempt = contract["attempt_output_contract"]
        self.assertEqual(attempt["schema_version_literal"], "stage2-recorded-provider-attempt/v2")
        self.assertIn("acquisition_id", attempt["exact_fields"])
        self.assertIn("acquisition_contract_sha256", attempt["exact_fields"])
        self.assertEqual(attempt["field_types"]["metadata_limitations"], "nonempty unique list of nonempty strings")
        self.assertEqual(attempt["field_types"]["token_usage"], "object or null")
        contract_sha256 = canonical_sha256(contract)
        attempts = _test_only_unavailable_attempts(material)
        self.assertTrue(all(item["acquisition_id"] == contract["acquisition_id"] for item in attempts))
        self.assertTrue(all(item["acquisition_contract_sha256"] == contract_sha256 for item in attempts))
        self.assertTrue(all(item["schema_version"] == "stage2-recorded-provider-attempt/v2" for item in attempts))

    def test_recorded_attempts_fail_closed_on_v2_acquisition_binding(self) -> None:
        material = build_confirmatory_material(ROOT)
        for field, invalid in (
            ("acquisition_id", "S2-RECORDED-AI-ACQUISITION-WRONG"),
            ("acquisition_contract_sha256", "0" * 64),
            ("schema_version", "stage2-recorded-provider-attempt/v1"),
        ):
            with self.subTest(field=field):
                attempts = _test_only_unavailable_attempts(material)
                attempts[0] = {**attempts[0], field: invalid}
                with self.assertRaises(EvaluationPackError):
                    validate_recorded_attempts(material, attempts, canonical=False)

    def test_natural_language_candidate_aliases_fail_real_parser_and_attempt_validation(self) -> None:
        material = build_confirmatory_material(ROOT)
        attempts = _test_only_unavailable_attempts(material)
        request = material["provider_requests"][0]
        invalid_candidate = {
            "candidate_id": "S2-CANDIDATE-CF-0001",
            "case_id": request["case_id"],
            "case_revision": 1,
            "cited_evidence": [request["schedule_sha256"]],
            "material_limitations": ["Synthetic case."],
            "message_fact_candidates": ["Wait for the parcel."],
            "proposed_action": "WAIT",
            "proposed_route": "DELEGATED",
            "rejected_alternatives": [],
            "schema_version": "stage2-provider-candidate/v1",
            "uncertainty": "LOW",
        }
        with self.assertRaises(CandidateValidationError):
            parse_candidate(canonical_json_bytes(invalid_candidate))
        attempts[0] = {
            **attempts[0],
            "fallback_disposition": "NOT_USED",
            "recorded_candidate": invalid_candidate,
            "response_sha256": hashlib.sha256(canonical_json_bytes(invalid_candidate)).hexdigest(),
            "terminal_status": "SUCCESS",
            "validation_result": "ACCEPTED",
        }
        with self.assertRaises(EvaluationPackError):
            validate_recorded_attempts(material, attempts, canonical=True)


class LayeredEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.pack = cls.root / "pack"
        cls.private = cls.root / "private"
        cls.output = cls.root / "output"
        write_evaluation_pack(
            ROOT, cls.pack, cls.private,
            source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"r" * 32,
        )
        run_inner_evaluation(cls.pack, cls.output)
        cls.oracle = [json.loads(line) for line in (cls.private / "oracle.jsonl").read_text(encoding="utf-8").splitlines()]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _copy_output(self, root: Path) -> Path:
        target = root / "output"
        shutil.copytree(self.output, target)
        return target

    def test_denominator_is_conserved_and_safe_routes_never_count_as_recovery(self) -> None:
        report = evaluate_raw_outputs(self.output, self.pack, self.oracle)
        denominator = report["denominator_conservation"]
        self.assertEqual(denominator["all_scheduled_cases"], 36)
        self.assertEqual(sum(denominator["mutually_exclusive_outcomes"].values()), 36)
        self.assertEqual(report["assisted"]["safe_escalations_in_verified_remedy_numerator"], 0)

    def test_wrong_oracle_recommendation_fails_even_when_runtime_policy_agrees(self) -> None:
        baseline = evaluate_raw_outputs(self.output, self.pack, self.oracle)
        self.assertEqual(
            baseline["assisted"]["recommendation_correctness_basis_points"],
            10000,
        )
        changed_oracle = [dict(item) for item in self.oracle]
        for item in changed_oracle[:4]:
            item["expected_action"] = "CONTROL_STOP"
        report = evaluate_raw_outputs(self.output, self.pack, changed_oracle)
        self.assertEqual(
            report["assisted"]["recommendation_correctness_basis_points"],
            8888,
        )
        self.assertFalse(report["threshold_gates"]["recommendation_correctness"])

    def test_post_release_scorer_rejects_wrong_named_coverage_mapping(self) -> None:
        changed_oracle = [dict(item) for item in self.oracle]
        changed_oracle[0]["family_id"] = changed_oracle[-1]["family_id"]
        with self.assertRaises(EvaluationIntegrityError):
            evaluate_raw_outputs(self.output, self.pack, changed_oracle)

    def test_zero_safe_route_cannot_default_to_pass(self) -> None:
        thresholds = json.loads((self.pack / "thresholds.json").read_text(encoding="utf-8"))
        assisted = {
            "approval_validity_basis_points": 10000,
            "closure_integrity_basis_points": 10000,
            "critical_control_failures": [],
            "recommendation_correctness_basis_points": 10000,
            "recovery_success_basis_points": 10000,
            "safe_routing_basis_points": 0,
            "unsupported_communication_facts": 0,
            "verified_remedy_basis_points": 10000,
        }
        decision, gates = apply_thresholds(assisted, thresholds)
        self.assertEqual(decision, "revise")
        self.assertFalse(gates["safe_routing"])

    def test_exact_zero_failure_precedes_passing_aggregate(self) -> None:
        thresholds = json.loads((self.pack / "thresholds.json").read_text(encoding="utf-8"))
        assisted = {
            "approval_validity_basis_points": 10000,
            "closure_integrity_basis_points": 10000,
            "critical_control_failures": ["FALSE_VERIFICATION"],
            "recommendation_correctness_basis_points": 10000,
            "recovery_success_basis_points": 10000,
            "safe_routing_basis_points": 10000,
            "unsupported_communication_facts": 0,
            "verified_remedy_basis_points": 10000,
        }
        decision, _ = apply_thresholds(
            assisted,
            thresholds,
            incomplete_evidence=True,
            pre_run_exposure=True,
        )
        self.assertEqual(decision, "stop")

    def test_incomplete_evidence_pauses_even_when_every_aggregate_passes(self) -> None:
        thresholds = json.loads((self.pack / "thresholds.json").read_text(encoding="utf-8"))
        assisted = {
            "approval_validity_basis_points": 10000,
            "closure_integrity_basis_points": 10000,
            "critical_control_failures": [],
            "recommendation_correctness_basis_points": 10000,
            "recovery_success_basis_points": 10000,
            "safe_routing_basis_points": 10000,
            "unsupported_communication_facts": 0,
            "verified_remedy_basis_points": 10000,
        }
        decision, gates = apply_thresholds(
            assisted,
            thresholds,
            incomplete_evidence=True,
        )
        self.assertTrue(all(gates.values()))
        self.assertEqual(decision, "pause")

    def test_missing_duplicate_or_reordered_schedule_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._copy_output(Path(temporary))
            (output / "assisted" / "S2-CASE-5001").rename(output / "assisted" / "S2-CASE-9999")
            with self.assertRaises(EvaluationIntegrityError):
                evaluate_raw_outputs(output, self.pack, self.oracle)

    def test_trace_tamper_yields_no_partial_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._copy_output(Path(temporary))
            ledger = output / "assisted" / "S2-CASE-5001" / "events" / "workflow.jsonl"
            ledger.chmod(0o644)
            ledger.write_bytes(ledger.read_bytes() + b"{}\n")
            with self.assertRaises(EvaluationIntegrityError):
                evaluate_raw_outputs(output, self.pack, self.oracle)

    def test_forged_summary_cannot_override_raw_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = self._copy_output(Path(temporary))
            forged = output / "assisted" / "S2-CASE-5001" / "summary.json"
            forged.write_bytes(canonical_json_bytes({"trace_valid": True, "governed_outcome": "VERIFIED_REMEDY"}))
            with self.assertRaises(EvaluationIntegrityError):
                evaluate_raw_outputs(output, self.pack, self.oracle)


if __name__ == "__main__":
    unittest.main()
