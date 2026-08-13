from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.build_stage2_decision_pack import (
    DECISION_PACK_ID,
    HUMAN_MEASURES,
    REQUIRED_ASSUMPTIONS,
    DecisionPackError,
    build_decision_pack,
    build_next_action,
    decide_from_signals,
    evaluate_economics,
    main,
    resolve_evidence_pointer,
    validate_assumptions,
    validate_evidence_index,
    verify_decision_pack,
    write_decision_pack,
)
from scripts.stage2_contracts import canonical_json_bytes, canonical_sha256
from scripts.stage2_decision_source import verify_and_replay_public_source
import scripts.stage2_decision_source as decision_source


ROOT = Path(__file__).resolve().parents[1]
ASSUMPTIONS_PATH = ROOT / "data/stage2/economics/assumptions.json"
SOURCE_LOCK_PATH = ROOT / "data/stage2/decision-source-lock.json"

EXPECTED_ASSUMPTIONS = frozenset(
    {
        "monthly_case_volume",
        "labour_cost_cents_per_hour",
        "assisted_review_minutes_per_case",
        "non_ai_review_minutes_per_case",
        "provider_cost_cents_per_case",
        "support_cost_cents_per_month",
        "infrastructure_cost_cents_per_month",
        "non_ai_support_cost_cents_per_month",
        "non_ai_infrastructure_cost_cents_per_month",
        "assisted_failure_rate_basis_points",
        "non_ai_failure_rate_basis_points",
        "failure_impact_cents_per_case",
        "capacity_realisation_basis_points",
    }
)
EXPECTED_HUMAN_MEASURES = frozenset(
    {
        "first_use",
        "comprehension",
        "help",
        "confidence",
        "review_time",
        "friction",
        "trust",
        "repeated_use",
        "adoption",
        "outcome_contribution",
    }
)


def _json(files: dict[str, bytes], relative: str) -> dict[str, object]:
    return json.loads(files[relative])


def _copy_public_sources(destination: Path, *, include_outputs: bool = True) -> None:
    for relative in (
        "data/stage2/evaluation-contract.json",
        "data/stage2/decision-source-lock.json",
        "data/stage2/economics/assumptions.json",
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    shutil.copytree(ROOT / "data/stage2/evaluation/v6", destination / "data/stage2/evaluation/v6")
    shutil.copytree(
        ROOT / "data/stage2/runs/S2-CF-RUN-0005",
        destination / "data/stage2/runs/S2-CF-RUN-0005",
    )
    if include_outputs:
        shutil.copytree(ROOT / "data/stage2/decision-pack", destination / "data/stage2/decision-pack")
        doc = destination / "docs/STAGE2_BENEFITS_AND_DECISION.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "docs/STAGE2_BENEFITS_AND_DECISION.md", doc)


class Stage2DecisionPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assumptions = json.loads(ASSUMPTIONS_PATH.read_text(encoding="utf-8"))

    def test_required_contract_sets_are_independent_literals(self) -> None:
        self.assertEqual(frozenset(REQUIRED_ASSUMPTIONS), EXPECTED_ASSUMPTIONS)
        self.assertEqual(frozenset(HUMAN_MEASURES), EXPECTED_HUMAN_MEASURES)

    def test_five_role_readiness_has_every_required_control_and_no_human_claim(self) -> None:
        files = build_decision_pack(ROOT)
        readiness = _json(files, "data/stage2/decision-pack/enablement-readiness.json")
        roles = readiness["roles"]
        self.assertEqual(
            [role["role_id"] for role in roles],
            [
                "workflow_owner_activator",
                "specialist",
                "manager",
                "technical_owner",
                "risk_owner",
            ],
        )
        required = {
            "authority",
            "change_owner",
            "evidence_gap",
            "first_use_guidance",
            "help_owner",
            "incident_owner",
            "appeal_owner",
            "material_failure_drill",
            "review_trigger",
        }
        for role in roles:
            self.assertFalse(required - role.keys())
            self.assertEqual(
                role["human_measures"],
                {name: "not_observed" for name in EXPECTED_HUMAN_MEASURES},
            )
            self.assertIsNone(role["human_observation_artifact"])
        self.assertEqual({role["material_failure_drill"]["drill_id"] for role in roles}, {"S2-DRILL-MATERIAL-FAILURE-01"})

    def test_assumptions_require_versioned_units_currency_source_owner_and_evidence_class(self) -> None:
        validate_assumptions(self.assumptions)
        for assumption_id in EXPECTED_ASSUMPTIONS:
            with self.subTest(assumption_id=assumption_id):
                changed = copy.deepcopy(self.assumptions)
                del changed["assumptions"][assumption_id]
                with self.assertRaisesRegex(DecisionPackError, assumption_id):
                    validate_assumptions(changed)
        for field in ("currency", "evidence_class", "owner", "source", "version"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.assumptions)
                del changed[field]
                with self.assertRaisesRegex(DecisionPackError, field):
                    validate_assumptions(changed)

    def test_assumptions_reject_wrong_units_negative_values_and_nonmonotonic_ranges(self) -> None:
        changed = copy.deepcopy(self.assumptions)
        changed["assumptions"]["provider_cost_cents_per_case"]["unit"] = "minutes_per_case"
        with self.assertRaisesRegex(DecisionPackError, "unit"):
            validate_assumptions(changed)

        changed = copy.deepcopy(self.assumptions)
        changed["assumptions"]["monthly_case_volume"]["scenarios"]["base"] = -1
        with self.assertRaisesRegex(DecisionPackError, "nonnegative"):
            validate_assumptions(changed)

        changed = copy.deepcopy(self.assumptions)
        changed["assumptions"]["capacity_realisation_basis_points"]["scenarios"] = {
            "conservative": 1000,
            "base": 500,
            "upside": 2000,
        }
        with self.assertRaisesRegex(DecisionPackError, "monotonic"):
            validate_assumptions(changed)

    def test_economics_uses_integer_cents_full_cost_and_separate_capacity_realisation(self) -> None:
        economics = evaluate_economics(self.assumptions)
        self.assertEqual([row["scenario"] for row in economics["scenarios"]], ["conservative", "base", "upside"])
        for row in economics["scenarios"]:
            for field in (
                "assisted_total_operating_cost_cents",
                "non_ai_total_operating_cost_cents",
                "capacity_realised_value_cents",
                "decision_net_benefit_cents",
            ):
                self.assertIs(type(row[field]), int)
            self.assertEqual(row["currency"], "EUR")
            self.assertEqual(row["evidence_class"], "hypothetical-impact-not-realised-value")
            self.assertEqual(
                row["nominal_capacity_minutes"],
                (row["non_ai_review_minutes_per_case"] - row["assisted_review_minutes_per_case"])
                * row["monthly_case_volume"],
            )
            if row["capacity_realisation_basis_points"] == 0:
                self.assertEqual(row["capacity_realised_value_cents"], 0)
        self.assertTrue(all(check["passed"] for check in economics["monotonic_checks"]))

    def test_scenario_arithmetic_and_half_cent_rounding_are_exact(self) -> None:
        economics = evaluate_economics(self.assumptions)
        expected = {
            "conservative": (438000, 365000, 0, -115000),
            "base": (528000, 858000, 80000, 90000),
            "upside": (688000, 2198000, 734400, 1020400),
        }
        for row in economics["scenarios"]:
            self.assertEqual(
                (
                    row["assisted_total_operating_cost_cents"],
                    row["non_ai_total_operating_cost_cents"],
                    row["capacity_realised_value_cents"],
                    row["decision_net_benefit_cents"],
                ),
                expected[row["scenario"]],
            )

        rounding = copy.deepcopy(self.assumptions)
        for assumption in rounding["assumptions"].values():
            assumption["scenarios"] = {scenario: 0 for scenario in ("conservative", "base", "upside")}
        rounding["assumptions"]["monthly_case_volume"]["scenarios"] = {
            "conservative": 1,
            "base": 2,
            "upside": 3,
        }
        rounding["assumptions"]["labour_cost_cents_per_hour"]["scenarios"] = {
            "conservative": 1,
            "base": 1,
            "upside": 1,
        }
        for assumption_id in ("assisted_review_minutes_per_case", "non_ai_review_minutes_per_case"):
            rounding["assumptions"][assumption_id]["scenarios"] = {
                "conservative": 30,
                "base": 30,
                "upside": 30,
            }
        rows = evaluate_economics(rounding)["scenarios"]
        self.assertEqual([row["assisted_labour_cost_cents"] for row in rows], [1, 1, 2])

    def test_full_sensitivity_class_change_is_inconclusive_and_cannot_support_scale(self) -> None:
        economics = evaluate_economics(self.assumptions)
        self.assertGreater(len(set(economics["scenario_recommendation_classes"])), 1)
        self.assertEqual(economics["status"], "inconclusive")
        self.assertFalse(economics["supports_scale_next_experiment"])

    def test_decision_precedence_uses_contract_for_all_four_classes(self) -> None:
        self.assertEqual(decide_from_signals(exact_zero_failures=["ORACLE_CONTAMINATION"]), "stop")
        self.assertEqual(decide_from_signals(incomplete_evidence=True), "pause")
        self.assertEqual(decide_from_signals(quality_gate_passed=False), "revise")
        self.assertEqual(decide_from_signals(), "scale_next_experiment")

    def test_decision_precedence_collisions_and_each_gate_failure(self) -> None:
        self.assertEqual(
            decide_from_signals(
                exact_zero_failures=["ORACLE_CONTAMINATION"],
                incomplete_evidence=True,
                quality_gate_passed=False,
                reliability_gate_passed=False,
                cost_gate_passed=False,
            ),
            "stop",
        )
        self.assertEqual(
            decide_from_signals(incomplete_evidence=True, quality_gate_passed=False),
            "pause",
        )
        self.assertEqual(
            decide_from_signals(pre_run_exposure=True, cost_gate_passed=False),
            "pause",
        )
        for gate in ("quality_gate_passed", "reliability_gate_passed", "cost_gate_passed"):
            with self.subTest(gate=gate):
                self.assertEqual(decide_from_signals(**{gate: False}), "revise")

    def test_every_decision_has_exactly_one_owner_bound_capped_action(self) -> None:
        for decision in ("scale_next_experiment", "revise", "pause", "stop"):
            with self.subTest(decision=decision):
                action = build_next_action(decision)
                self.assertEqual(action["decision"], decision)
                self.assertIsInstance(action["owner"], str)
                self.assertTrue(action["owner"])
                self.assertEqual(set(action["cap"]), {"maximum_calendar_days", "maximum_provider_attempts", "maximum_synthetic_cases", "maximum_spend_cents", "currency"})
                self.assertTrue(action["evidence_question"])
                self.assertTrue(action["entry_conditions"])
                self.assertTrue(action["stop_conditions"])
                self.assertTrue(action["expiry_or_review_trigger"])
                self.assertFalse(action["authorises_company_pilot"])

    def test_v6_score_is_the_only_decision_source_and_truthful_gaps_are_foregrounded(self) -> None:
        files = build_decision_pack(ROOT)
        decision_input = _json(files, "data/stage2/decision-pack/decision-input.json")
        decision_output = _json(files, "data/stage2/decision-pack/decision-output.json")
        evaluation = _json(files, "data/stage2/decision-pack/evaluation-summary.json")
        self.assertEqual(decision_input["source_pack_id"], "S2-EVALUATION-20260812-V6")
        self.assertEqual(decision_input["source_run_id"], "S2-CF-RUN-0005")
        self.assertEqual(evaluation["execution_commit_basis_points"], 8333)
        self.assertEqual(evaluation["execution_commit_display"], "83.33%")
        self.assertEqual(evaluation["pending_cases"], 3)
        self.assertEqual(evaluation["provider_cost_unknown_attempts"], 36)
        self.assertEqual(evaluation["provider_latency_unknown_attempts"], 36)
        self.assertEqual(decision_output["recommendation"], "pause")
        self.assertEqual(decision_output["maturity_ceiling"], "local-mvp")
        self.assertFalse(decision_output["next_action"]["authorises_company_pilot"])
        self.assertEqual(decision_output["next_action"]["cap"]["maximum_synthetic_cases"], 36)

    def test_human_live_and_value_measures_remain_not_observed(self) -> None:
        files = build_decision_pack(ROOT)
        summary = _json(files, "data/stage2/decision-pack/summary.json")
        self.assertEqual(
            set(summary["not_observed"]),
            {
                "adoption",
                "customer_satisfaction",
                "human_comprehension",
                "human_first_use",
                "human_friction",
                "human_help_need",
                "human_review_time",
                "human_trust",
                "live_customer_outcome",
                "live_operational_reliability",
                "realised_savings",
                "retained_revenue",
            },
        )
        self.assertNotIn("pilot_authorised", json.dumps(summary))

    def test_every_decision_claim_resolves_to_evidence_assumption_or_not_observed(self) -> None:
        files = build_decision_pack(ROOT)
        index = _json(files, "data/stage2/decision-pack/evidence-index.json")
        claims = index["claims"]
        self.assertTrue(claims)
        self.assertEqual(
            {claim["resolution_kind"] for claim in claims},
            {"assumption", "evidence", "not_observed"},
        )
        for claim in claims:
            self.assertTrue(claim["source_path"])
            self.assertTrue(claim["source_pointer"])
            self.assertNotIn("*", claim["source_pointer"])
        self.assertNotEqual(
            next(claim for claim in claims if claim["claim_id"] == "S2-CLAIM-PROVIDER-COST")["source_pointer"],
            next(claim for claim in claims if claim["claim_id"] == "S2-CLAIM-PROVIDER-LATENCY")["source_pointer"],
        )
        validate_evidence_index(index, ROOT, files)
        for claim in claims:
            self.assertIsNotNone(resolve_evidence_pointer(claim, ROOT, files))

    def test_pointer_resolver_rejects_wildcard_missing_and_escaped_outside_reference(self) -> None:
        files = build_decision_pack(ROOT)
        index = _json(files, "data/stage2/decision-pack/evidence-index.json")
        claim = copy.deepcopy(index["claims"][0])
        for source_path, source_pointer in (
            (claim["source_path"], "/missing"),
            (claim["source_path"], "/*"),
            ("../outside.json", "/value"),
        ):
            changed = copy.deepcopy(claim)
            changed["source_path"] = source_path
            changed["source_pointer"] = source_pointer
            with self.assertRaises(DecisionPackError):
                resolve_evidence_pointer(changed, ROOT, files)

    def test_generated_files_are_canonical_byte_stable_and_bind_inputs(self) -> None:
        first = build_decision_pack(ROOT)
        second = build_decision_pack(ROOT)
        self.assertEqual(first, second)
        manifest = _json(first, "data/stage2/decision-pack/manifest.json")
        self.assertEqual(manifest["decision_pack_id"], DECISION_PACK_ID)
        self.assertEqual(
            manifest["source_lock_sha256"],
            hashlib.sha256(SOURCE_LOCK_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            manifest["source_score_sha256"],
            hashlib.sha256((ROOT / "data/stage2/runs/S2-CF-RUN-0005/score.json").read_bytes()).hexdigest(),
        )
        for relative, digest in manifest["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256(first[relative]).hexdigest(), digest)
            self.assertEqual(first[relative], canonical_json_bytes(json.loads(first[relative])))

    def test_manual_source_lock_pins_every_canonical_v6_u7_input_and_is_not_generated(self) -> None:
        lock = json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            set(lock),
            {
                "assumptions_sha256",
                "assumptions_version",
                "evaluation_contract_sha256",
                "output_seal_sha256",
                "pack_id",
                "pack_schema",
                "public_pack_manifest_sha256",
                "run_id",
                "schema_version",
                "score_sha256",
                "scored_release_head",
                "status",
            },
        )
        self.assertEqual(lock["pack_id"], "S2-EVALUATION-20260812-V6")
        self.assertEqual(lock["run_id"], "S2-CF-RUN-0005")
        self.assertNotIn("data/stage2/decision-source-lock.json", build_decision_pack(ROOT))

    def test_current_generated_pack_verifies_and_same_version_assumption_edit_fails_before_write(self) -> None:
        self.assertEqual(verify_decision_pack(ROOT), [])
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            copied.mkdir()
            _copy_public_sources(copied)
            before = {
                path.relative_to(copied).as_posix(): path.read_bytes()
                for path in copied.rglob("*")
                if path.is_file()
            }
            assumption_path = copied / "data/stage2/economics/assumptions.json"
            changed = json.loads(assumption_path.read_text(encoding="utf-8"))
            changed["assumptions"]["monthly_case_volume"]["scenarios"]["base"] += 1
            assumption_path.write_bytes(canonical_json_bytes(changed))
            with self.assertRaisesRegex(DecisionPackError, "assumptions changed without a new version"):
                write_decision_pack(copied)
            after = {
                path.relative_to(copied).as_posix(): path.read_bytes()
                for path in copied.rglob("*")
                if path.is_file() and path != assumption_path
            }
            self.assertEqual({key: value for key, value in before.items() if key != ASSUMPTIONS_PATH.relative_to(ROOT).as_posix()}, after)

    def test_assumption_drift_fails_before_raw_replay_is_called(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            copied.mkdir()
            _copy_public_sources(copied, include_outputs=False)
            assumption_path = copied / "data/stage2/economics/assumptions.json"
            assumption_path.chmod(0o644)
            changed = json.loads(assumption_path.read_text(encoding="utf-8"))
            changed["assumptions"]["monthly_case_volume"]["scenarios"]["base"] += 1
            assumption_path.write_bytes(canonical_json_bytes(changed))
            with patch("scripts.stage2_decision_source.evaluate_raw_outputs") as replay:
                with self.assertRaisesRegex(DecisionPackError, "assumptions changed without a new version"):
                    write_decision_pack(copied)
                replay.assert_not_called()
            self.assertFalse((copied / "data/stage2/decision-pack").exists())

    def test_raw_tamper_and_coherent_score_rewrite_fail_independent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "raw-tamper"
            copied.mkdir()
            _copy_public_sources(copied, include_outputs=False)
            raw = copied / "data/stage2/runs/S2-CF-RUN-0005/output/comparator/S2-CASE-5001/result.json"
            raw.chmod(0o644)
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(DecisionPackError, "sealed output"):
                build_decision_pack(copied)

        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "coherent-score"
            copied.mkdir()
            _copy_public_sources(copied, include_outputs=False)
            score_path = copied / "data/stage2/runs/S2-CF-RUN-0005/score.json"
            score_path.chmod(0o644)
            score = json.loads(score_path.read_text(encoding="utf-8"))
            score["assisted"]["execution_commit_basis_points"] = 10000
            score.pop("report_digest")
            score["report_digest"] = canonical_sha256(score)
            score_path.write_bytes(canonical_json_bytes(score))
            score_sha = hashlib.sha256(score_path.read_bytes()).hexdigest()
            state_path = copied / "data/stage2/runs/S2-CF-RUN-0005/release-states/0006-scored.json"
            state_path.chmod(0o644)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["bindings"]["score_sha256"] = score_sha
            state.pop("record_digest")
            state["record_digest"] = canonical_sha256(state)
            state_path.write_bytes(canonical_json_bytes(state))
            lock_path = copied / "data/stage2/decision-source-lock.json"
            lock_path.chmod(0o644)
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["score_sha256"] = score_sha
            lock["scored_release_head"] = state["record_digest"]
            lock_path.write_bytes(canonical_json_bytes(lock))
            with self.assertRaisesRegex(DecisionPackError, "recomputed score differs"):
                build_decision_pack(copied)

    def test_score_bytes_are_not_opened_until_sealed_raw_replay_completes(self) -> None:
        events: list[str] = []
        real_load = decision_source.load_public_json
        real_replay = decision_source.evaluate_raw_outputs

        def tracked_load(path: Path):
            if Path(path).name == "score.json":
                events.append("score-open")
            return real_load(path)

        def tracked_replay(*args, **kwargs):
            events.append("raw-replay")
            return real_replay(*args, **kwargs)

        with patch("scripts.stage2_decision_source.load_public_json", side_effect=tracked_load), patch(
            "scripts.stage2_decision_source.evaluate_raw_outputs", side_effect=tracked_replay
        ):
            verify_and_replay_public_source(ROOT)
        self.assertEqual(events, ["raw-replay", "score-open"])

    def test_lock_pinned_public_input_tamper_matrix_fails_before_replay(self) -> None:
        mutations = (
            ("data/stage2/economics/assumptions.json", "status", "tampered"),
            ("data/stage2/evaluation-contract.json", "status", "tampered"),
            ("data/stage2/evaluation/v6/manifest.json", "status", "tampered"),
            ("data/stage2/runs/S2-CF-RUN-0005/output-seal.json", "final_ledger_head", "0" * 64),
            ("data/stage2/runs/S2-CF-RUN-0005/score.json", "maturity_ceiling", "tampered"),
            ("data/stage2/runs/S2-CF-RUN-0005/release-states/0006-scored.json", "sequence", 99),
        )
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            copied.mkdir()
            _copy_public_sources(copied, include_outputs=False)
            for relative, field, value in mutations:
                with self.subTest(relative=relative):
                    path = copied / relative
                    original = path.read_bytes()
                    path.chmod(0o644)
                    changed = json.loads(original)
                    changed[field] = value
                    path.write_bytes(canonical_json_bytes(changed))
                    if relative.endswith("score.json"):
                        with self.assertRaises(DecisionPackError):
                            build_decision_pack(copied)
                    else:
                        with patch("scripts.stage2_decision_source.evaluate_raw_outputs") as replay:
                            with self.assertRaises(DecisionPackError):
                                build_decision_pack(copied)
                            replay.assert_not_called()
                    path.write_bytes(original)

    def test_cli_verify_never_writes_missing_outputs_or_source_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "repo"
            copied.mkdir()
            _copy_public_sources(copied, include_outputs=False)
            (copied / "data/stage2/decision-source-lock.json").unlink()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["--project-root", str(copied), "--verify"]), 1)
            self.assertFalse((copied / "data/stage2/decision-pack").exists())
            self.assertFalse((copied / "data/stage2/decision-source-lock.json").exists())

    def test_generated_document_states_boundary_and_source_metrics(self) -> None:
        files = build_decision_pack(ROOT)
        document = files["docs/STAGE2_BENEFITS_AND_DECISION.md"].decode("utf-8")
        self.assertIn("83.33%", document)
        self.assertIn("3 pending", document)
        self.assertIn("36 of 36 provider attempts", document)
        self.assertIn("not observed", document.lower())
        self.assertIn("PAUSE", document)
        self.assertIn("does not authorise a company pilot", document)


if __name__ == "__main__":
    unittest.main()
