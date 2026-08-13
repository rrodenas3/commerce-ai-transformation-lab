"""Build and verify the public Stage 2 enablement/economics decision pack."""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage2_contracts import canonical_json_bytes
from scripts.stage2_decision_content import (
    build_evidence_index,
    build_readiness_matrix,
    render_document,
    resolve_evidence_pointer,
    validate_evidence_index,
)
from scripts.stage2_decision_contracts import (
    HUMAN_MEASURES,
    NOT_OBSERVED,
    REQUIRED_ASSUMPTIONS,
    DecisionPackError,
    build_next_action,
    decide_from_signals,
    evaluate_economics,
    validate_assumptions,
)
from scripts.stage2_decision_source import (
    PACK_ID,
    RUN_ID,
    SCORE_PATH,
    SOURCE_LOCK_PATH,
    read_regular,
    sha256,
    verify_and_replay_public_source,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DECISION_PACK_ID = "S2-DECISION-PACK-20260812-V1"
DECISION_PACK_SCHEMA = "stage2-decision-pack/v2"
OUTPUT_DIRECTORY = Path("data/stage2/decision-pack")
DOCUMENT_PATH = Path("docs/STAGE2_BENEFITS_AND_DECISION.md")


def _evaluation_summary(score: Mapping[str, Any]) -> dict[str, Any]:
    assisted = score["assisted"]
    outcomes = score["denominator_conservation"]["mutually_exclusive_outcomes"]
    return {
        "assisted_case_count": score["oracle_case_count"],
        "creator_evaluated": score["creator_evaluated"],
        "evidence_class": score["evidence_class"],
        "exact_zero_failures": score["exact_zero"]["failures"],
        "exact_zero_status": score["exact_zero"]["status"],
        "execution_commit_basis_points": assisted["execution_commit_basis_points"],
        "execution_commit_denominator": assisted["metric_denominators"]["execution_commit"],
        "execution_commit_display": f"{Decimal(assisted['execution_commit_basis_points']) / Decimal(100):.2f}%",
        "human_evidence": "not_observed",
        "maturity_ceiling": score["maturity_ceiling"],
        "pending_cases": outcomes["pending"],
        "provider_cost_known_attempts": assisted["provider_cost_cents"]["known_attempts"],
        "provider_cost_unknown_attempts": assisted["provider_cost_cents"]["unknown_attempts"],
        "provider_latency_known_attempts": assisted["provider_latency_milliseconds"]["known_attempts"],
        "provider_latency_unknown_attempts": assisted["provider_latency_milliseconds"]["unknown_attempts"],
        "schema_version": "stage2-evaluation-summary/v1",
        "source_pack_id": PACK_ID,
        "source_run_id": RUN_ID,
        "threshold_gates": score["threshold_gates"],
    }


def build_decision_pack(project_root: Path = PROJECT_ROOT) -> dict[str, bytes]:
    """Build all U7 outputs in memory from locked, replayed public inputs."""

    project_root = Path(project_root).resolve()
    source = verify_and_replay_public_source(project_root)
    evaluation = _evaluation_summary(source["score"])
    readiness = build_readiness_matrix()
    economics = evaluate_economics(source["assumptions"])

    incomplete_reasons: list[str] = []
    if evaluation["pending_cases"]:
        incomplete_reasons.append("3 eligible execution cases remain pending")
    if evaluation["provider_cost_unknown_attempts"]:
        incomplete_reasons.append("provider cost is unknown for 36 of 36 attempts")
    if evaluation["provider_latency_unknown_attempts"]:
        incomplete_reasons.append("provider latency is unknown for 36 of 36 attempts")
    signals = {
        "cost_gate_passed": economics["supports_scale_next_experiment"]
        and evaluation["provider_cost_unknown_attempts"] == 0
        and evaluation["provider_latency_unknown_attempts"] == 0,
        "exact_zero_failures": list(evaluation["exact_zero_failures"]),
        "incomplete_evidence": bool(incomplete_reasons),
        "pre_run_exposure": False,
        "quality_gate_passed": all(evaluation["threshold_gates"].values()),
        "reliability_gate_passed": evaluation["execution_commit_basis_points"] == 10_000
        and evaluation["pending_cases"] == 0,
    }
    recommendation = decide_from_signals(contract=source["contract"], **signals)
    decision_input = {
        "economics_status": economics["status"],
        "incomplete_evidence_reasons": incomplete_reasons,
        "schema_version": "stage2-decision-input/v1",
        "signals": signals,
        "source_pack_id": PACK_ID,
        "source_run_id": RUN_ID,
        "source_score_sha256": sha256(source["score_bytes"]),
    }
    decision_output = {
        "authorises_company_pilot": False,
        "decision_precedence": ["stop", "pause", "revise", "scale_next_experiment"],
        "economics_supports_scale_next_experiment": economics["supports_scale_next_experiment"],
        "maturity_ceiling": "local-mvp",
        "next_action": build_next_action(recommendation),
        "recommendation": recommendation,
        "schema_version": "stage2-decision-output/v1",
        "scope": "synthetic creator-evaluated evidence; no live or company-pilot authorisation",
    }
    economics_summary = {
        "assumption_version": economics["assumption_version"],
        "capacity_realisation_is_separate": True,
        "currency": "EUR",
        "evidence_class": economics["evidence_class"],
        "non_ai_process_alternative_included": True,
        "scenario_class_stable": economics["scenario_class_stable"],
        "scenario_recommendation_classes": economics["scenario_recommendation_classes"],
        "schema_version": "stage2-economics-summary/v1",
        "status": economics["status"],
        "supports_scale_next_experiment": economics["supports_scale_next_experiment"],
        "value_status": "hypothetical-not-realised",
    }
    summary = {
        "claim_boundary": "synthetic observed evaluation plus hypothetical economics; human, live, and realised-value evidence absent",
        "decision": recommendation,
        "decision_pack_id": DECISION_PACK_ID,
        "economics_status": economics["status"],
        "human_evidence": "not_observed",
        "maturity_ceiling": "local-mvp",
        "next_action_count": 1,
        "not_observed": list(NOT_OBSERVED),
        "schema_version": "stage2-decision-summary/v1",
        "source_pack_id": PACK_ID,
        "source_run_id": RUN_ID,
    }
    evidence_index = build_evidence_index()

    material: dict[str, Mapping[str, Any]] = {
        (OUTPUT_DIRECTORY / "decision-input.json").as_posix(): decision_input,
        (OUTPUT_DIRECTORY / "decision-output.json").as_posix(): decision_output,
        (OUTPUT_DIRECTORY / "economics-summary.json").as_posix(): economics_summary,
        (OUTPUT_DIRECTORY / "enablement-readiness.json").as_posix(): readiness,
        (OUTPUT_DIRECTORY / "evaluation-summary.json").as_posix(): evaluation,
        (OUTPUT_DIRECTORY / "sensitivity-table.json").as_posix(): economics,
        (OUTPUT_DIRECTORY / "summary.json").as_posix(): summary,
    }
    files = {relative: canonical_json_bytes(value) for relative, value in material.items()}
    validate_evidence_index(evidence_index, project_root, files)
    files[(OUTPUT_DIRECTORY / "evidence-index.json").as_posix()] = canonical_json_bytes(evidence_index)
    document = render_document(evaluation, economics, readiness, decision_output)
    files[DOCUMENT_PATH.as_posix()] = document
    manifest = {
        "artifact_sha256": {
            relative: sha256(payload)
            for relative, payload in sorted(files.items())
            if relative.endswith(".json")
        },
        "assumptions_sha256": sha256(source["assumptions_bytes"]),
        "assumptions_version": source["assumptions"]["version"],
        "decision_pack_id": DECISION_PACK_ID,
        "document_sha256": {DOCUMENT_PATH.as_posix(): sha256(document)},
        "evaluation_contract_sha256": sha256(source["contract_bytes"]),
        "generated_at": "2026-08-12T00:00:00Z",
        "output_seal_sha256": sha256(source["output_seal_bytes"]),
        "public_only_inputs": True,
        "schema_version": DECISION_PACK_SCHEMA,
        "source_lock_sha256": sha256(source["lock_bytes"]),
        "source_pack_id": PACK_ID,
        "source_pack_manifest_sha256": sha256(source["manifest_bytes"]),
        "source_release_state_head": source["states"][-1]["record_digest"],
        "source_replayed_from_sealed_raw_bytes": True,
        "source_run_id": RUN_ID,
        "source_score_sha256": sha256(source["score_bytes"]),
    }
    files[(OUTPUT_DIRECTORY / "manifest.json").as_posix()] = canonical_json_bytes(manifest)
    return dict(sorted(files.items()))


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_decision_pack(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Validate every lock/replay boundary before materializing any output."""

    project_root = Path(project_root).resolve()
    files = build_decision_pack(project_root)
    for relative, payload in files.items():
        _write_atomic(project_root / relative, payload)
    return sorted(files)


def verify_decision_pack(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Return fail-closed verification errors without writing any bytes."""

    project_root = Path(project_root).resolve()
    try:
        expected = build_decision_pack(project_root)
    except Exception as error:
        return [str(error)]
    errors: list[str] = []
    for relative, payload in expected.items():
        path = project_root / relative
        if not path.exists():
            errors.append(f"missing generated decision-pack artifact: {relative}")
            continue
        try:
            actual = read_regular(path)
        except Exception as error:
            errors.append(str(error))
            continue
        if actual != payload:
            errors.append(f"generated decision-pack artifact differs: {relative}")
    output_root = project_root / OUTPUT_DIRECTORY
    if output_root.exists():
        expected_json = {
            Path(relative).name for relative in expected if relative.startswith(OUTPUT_DIRECTORY.as_posix())
        }
        actual_json = {path.name for path in output_root.glob("*.json") if path.is_file()}
        for extra in sorted(actual_json - expected_json):
            errors.append(f"unexpected generated decision-pack artifact: {OUTPUT_DIRECTORY.as_posix()}/{extra}")
    return list(dict.fromkeys(errors))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--verify", action="store_true", help="verify materialised bytes without writing")
    arguments = parser.parse_args(argv)
    if arguments.verify:
        errors = verify_decision_pack(arguments.project_root)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Stage 2 decision pack verified")
        return 0
    written = write_decision_pack(arguments.project_root)
    print(f"Wrote {len(written)} deterministic Stage 2 decision-pack artifacts")
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DECISION_PACK_ID",
    "HUMAN_MEASURES",
    "REQUIRED_ASSUMPTIONS",
    "DecisionPackError",
    "build_decision_pack",
    "build_next_action",
    "decide_from_signals",
    "evaluate_economics",
    "main",
    "resolve_evidence_pointer",
    "validate_assumptions",
    "validate_evidence_index",
    "verify_decision_pack",
    "write_decision_pack",
]
