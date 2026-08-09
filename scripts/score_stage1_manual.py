#!/usr/bin/env python3
"""Validate and score completed SCC-01 manual baseline records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_case_system import read_jsonl
from scripts.stage1_scoring import (
    ALLOWED_MANUAL_RUN_TYPES,
    ESCALATION_ACTIONS,
    REQUIRED_MANUAL_FIELDS,
    evaluate_decisions,
)


RUN_MANIFEST_SCHEMA_VERSION = "1.0.0"
PUBLIC_ORACLE_EXPOSURE_STATUS = "public-oracle-available"
REQUIRED_PINNED_ARTIFACTS = {"cases", "oracle", "policy", "artifact_manifest"}


def _utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be an ISO-8601 UTC timestamp")
    return parsed


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_artifact_pins(run_metadata: dict[str, Any]) -> dict[str, dict[str, str]]:
    pins = run_metadata.get("artifacts")
    if not isinstance(pins, dict) or set(pins) != REQUIRED_PINNED_ARTIFACTS:
        raise ValueError(
            "run manifest artifacts must pin cases, oracle, policy, and artifact_manifest"
        )
    validated: dict[str, dict[str, str]] = {}
    for name in sorted(REQUIRED_PINNED_ARTIFACTS):
        pin = pins.get(name)
        if not isinstance(pin, dict):
            raise ValueError(f"run manifest artifact '{name}' must be an object")
        path = pin.get("path")
        digest = pin.get("sha256")
        if not isinstance(path, str) or not path:
            raise ValueError(f"run manifest artifact '{name}' requires a path")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(
                f"run manifest artifact '{name}' requires a lowercase SHA-256 digest"
            )
        validated[name] = {"path": path, "sha256": digest}
    return validated


def _validate_run_metadata(
    run_metadata: dict[str, Any],
    cases: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, str]]]:
    if not isinstance(run_metadata, dict):
        raise ValueError("manual run manifest must be a JSON object")
    if run_metadata.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported manual run manifest schema_version")
    if run_metadata.get("run_type") not in ALLOWED_MANUAL_RUN_TYPES:
        raise ValueError("unsupported manual run manifest run_type")
    if run_metadata.get("oracle_exposure_status") != PUBLIC_ORACLE_EXPOSURE_STATUS:
        raise ValueError(
            "public discovery runs must declare oracle_exposure_status "
            f"as '{PUBLIC_ORACLE_EXPOSURE_STATUS}'"
        )
    if not isinstance(run_metadata.get("dataset_role"), str) or not run_metadata[
        "dataset_role"
    ]:
        raise ValueError("run manifest requires dataset_role")

    policy = run_metadata.get("policy")
    oracle = run_metadata.get("oracle")
    if not isinstance(policy, dict) or not policy.get("policy_id") or not policy.get("version"):
        raise ValueError("run manifest requires policy_id and policy version")
    if not isinstance(oracle, dict) or not oracle.get("version"):
        raise ValueError("run manifest requires oracle version")
    pins = _validated_artifact_pins(run_metadata)

    assigned = run_metadata.get("assigned_case_ids")
    if (
        not isinstance(assigned, list)
        or not assigned
        or any(not isinstance(case_id, str) or not case_id for case_id in assigned)
    ):
        raise ValueError("run manifest requires a non-empty assigned_case_ids order")
    if len(set(assigned)) != len(assigned):
        raise ValueError("run manifest assigned_case_ids must be unique")

    case_ids = [case.get("case_id") for case in cases]
    oracle_ids = [item.get("case_id") for item in oracles]
    if len(set(case_ids)) != len(case_ids) or len(set(oracle_ids)) != len(oracle_ids):
        raise ValueError("cases and oracles must contain unique case IDs")
    missing_cases = [case_id for case_id in assigned if case_id not in set(case_ids)]
    missing_oracles = [case_id for case_id in assigned if case_id not in set(oracle_ids)]
    if missing_cases or missing_oracles:
        raise ValueError(
            "assigned cases are absent from pinned artifacts: "
            f"cases={missing_cases}, oracles={missing_oracles}"
        )

    cases_by_id = {case["case_id"]: case for case in cases}
    oracles_by_id = {item["case_id"]: item for item in oracles}
    for case_id in assigned:
        case = cases_by_id[case_id]
        case_policy = case.get("policy", {})
        case_oracle = oracles_by_id[case_id]
        if case.get("dataset_role") != run_metadata["dataset_role"]:
            raise ValueError(f"case {case_id} does not match pinned dataset_role")
        if case_policy.get("policy_id") != policy["policy_id"]:
            raise ValueError(f"case {case_id} does not match pinned policy_id")
        if case_policy.get("version") != policy["version"]:
            raise ValueError(f"case {case_id} does not match pinned policy version")
        if case_oracle.get("policy_version") != policy["version"]:
            raise ValueError(f"oracle {case_id} does not match pinned policy version")
        if case_oracle.get("oracle_version") != oracle["version"]:
            raise ValueError(f"oracle {case_id} does not match pinned oracle version")
    return assigned, pins


def _integer(record: dict[str, Any], field: str, *, minimum: int = 0) -> int:
    try:
        value = int(record[field])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid integer manual field '{field}'") from error
    if value < minimum:
        raise ValueError(f"manual field '{field}' must be at least {minimum}")
    return value


def _normalize_record(record: dict[str, str]) -> dict[str, Any]:
    missing = sorted(field for field in REQUIRED_MANUAL_FIELDS if not record.get(field))
    if missing:
        raise ValueError("missing required manual field(s): " + ", ".join(missing))
    if record["run_type"] not in ALLOWED_MANUAL_RUN_TYPES:
        raise ValueError("unsupported manual run_type")
    if record["help_requested"].lower() not in {"true", "false"}:
        raise ValueError("help_requested must be true or false")
    confidence = _integer(record, "confidence_1_to_5", minimum=1)
    if confidence > 5:
        raise ValueError("confidence_1_to_5 must not exceed 5")
    started = _utc_timestamp(record["started_at_utc"], "started_at_utc")
    ended = _utc_timestamp(record["ended_at_utc"], "ended_at_utc")
    if ended < started:
        raise ValueError("ended_at_utc must not be before started_at_utc")
    active_handling_seconds = _integer(record, "active_handling_seconds")
    if active_handling_seconds > (ended - started).total_seconds():
        raise ValueError(
            "active_handling_seconds must not exceed elapsed wall-clock time"
        )
    return {
        **record,
        "active_handling_seconds": active_handling_seconds,
        "confidence_1_to_5": confidence,
        "handoff_count": _integer(record, "handoff_count"),
        "policy_lookup_count": _integer(record, "policy_lookup_count"),
        "help_requested": record["help_requested"].lower() == "true",
        "evidence_used": sorted(
            value for value in record["evidence_used_pipe_delimited"].split("|") if value
        ),
        "message_facts": sorted(
            value for value in record["message_facts_pipe_delimited"].split("|") if value
        ),
    }


def _decision_from_record(record: dict[str, Any]) -> dict[str, Any]:
    action = record["recommended_action"]
    return {
        "case_id": record["case_id"],
        "recommended_action": action,
        "route": record["route"],
        "evidence_used": record["evidence_used"],
        "message_facts": record["message_facts"],
        "abstained": action in ESCALATION_ACTIONS,
        "executed_action": False,
        "postcondition_verified": False,
    }


def score_manual_records(
    cases: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
    records: list[dict[str, str]],
    *,
    run_metadata: dict[str, Any],
) -> dict[str, Any]:
    if not records:
        raise ValueError("at least one completed manual record is required")
    assigned_case_ids, artifact_pins = _validate_run_metadata(
        run_metadata, cases, oracles
    )
    normalized = [_normalize_record(record) for record in records]
    completed_case_ids = [record["case_id"] for record in normalized]
    if len(set(completed_case_ids)) != len(normalized):
        raise ValueError("manual records must contain unique case IDs")
    assigned_set = set(assigned_case_ids)
    completed_set = set(completed_case_ids)
    unresolved_case_ids = [
        case_id for case_id in assigned_case_ids if case_id not in completed_set
    ]
    unexpected_case_ids = [
        case_id for case_id in completed_case_ids if case_id not in assigned_set
    ]
    if unresolved_case_ids or unexpected_case_ids:
        raise ValueError(
            "manual run assignment is incomplete or contains unassigned cases: "
            f"unresolved={unresolved_case_ids}, unexpected={unexpected_case_ids}"
        )

    records_by_id = {record["case_id"]: record for record in normalized}
    cases_by_id = {case["case_id"]: case for case in cases}
    oracles_by_id = {oracle["case_id"]: oracle for oracle in oracles}
    normalized = [records_by_id[case_id] for case_id in assigned_case_ids]
    selected_cases = [cases_by_id[case_id] for case_id in assigned_case_ids]
    selected_oracles = [oracles_by_id[case_id] for case_id in assigned_case_ids]

    decisions = [_decision_from_record(record) for record in normalized]
    run_types = {record["run_type"] for record in normalized}
    if run_types != {run_metadata["run_type"]}:
        raise ValueError("every manual record run_type must match the run manifest")
    baseline_id = run_metadata["run_type"]
    summary = evaluate_decisions(
        selected_cases,
        decisions,
        selected_oracles,
        baseline_id=baseline_id,
    )
    handling = [record["active_handling_seconds"] for record in normalized]
    summary.update(
        {
            "active_handling_seconds": {
                "count": len(handling),
                "minimum": min(handling),
                "median": statistics.median(handling),
                "mean": round(statistics.mean(handling), 2),
                "maximum": max(handling),
            },
            "handoff_count": sum(record["handoff_count"] for record in normalized),
            "policy_lookup_count": sum(
                record["policy_lookup_count"] for record in normalized
            ),
            "help_requested_count": sum(record["help_requested"] for record in normalized),
            "reviewer_count": len({record["reviewer_code"] for record in normalized}),
            "assigned_case_count": len(assigned_case_ids),
            "completed_case_count": len(completed_case_ids),
            "unresolved_case_count": 0,
            "assigned_case_ids": assigned_case_ids,
            "completed_case_ids": assigned_case_ids,
            "unresolved_case_ids": [],
            "manual_run_provenance": {
                "schema_version": run_metadata["schema_version"],
                "dataset_role": run_metadata.get("dataset_role"),
                "run_type": run_metadata["run_type"],
                "oracle_exposure_status": run_metadata["oracle_exposure_status"],
                "policy_id": run_metadata["policy"]["policy_id"],
                "policy_version": run_metadata["policy"]["version"],
                "oracle_version": run_metadata["oracle"]["version"],
                "artifact_sha256": {
                    name: artifact_pins[name]["sha256"]
                    for name in sorted(artifact_pins)
                },
            },
            "human_evidence_boundary": (
                "Creator-run or independent synthetic-case observation according to "
                "run_type; not organisational adoption or realised business impact."
            ),
        }
    )
    return summary


def _pinned_path(root: Path, pin: dict[str, str]) -> Path:
    path = Path(pin["path"])
    return path if path.is_absolute() else root / path


def _verify_artifact(path: Path, pin: dict[str, str], name: str) -> None:
    if not path.is_file():
        raise ValueError(f"pinned {name} artifact does not exist: {path}")
    actual = _sha256(path)
    if actual != pin["sha256"]:
        raise ValueError(
            f"pinned {name} SHA-256 mismatch: expected {pin['sha256']}, got {actual}"
        )


def _same_path(left: Path, right: Path) -> bool:
    if os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve())):
        return True
    return left.exists() and right.exists() and left.samefile(right)


def _reject_output_alias(output: Path, source_paths: list[Path]) -> None:
    for source in source_paths:
        if _same_path(output, source):
            raise ValueError(f"output path must not overwrite input artifact: {source}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Completed manual CSV")
    parser.add_argument("--output", required=True, type=Path, help="Summary JSON path")
    parser.add_argument("--cases", required=True, type=Path, help="Pinned cases JSONL")
    parser.add_argument("--oracle", required=True, type=Path, help="Pinned oracle JSONL")
    parser.add_argument(
        "--run-manifest", required=True, type=Path, help="Frozen manual run manifest JSON"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="Pinned policy JSON; defaults to the run-manifest path pin",
    )
    parser.add_argument(
        "--artifact-manifest",
        type=Path,
        help="Pinned source artifact manifest; defaults to the run-manifest path pin",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_metadata = json.loads(arguments.run_manifest.read_text(encoding="utf-8"))
    pins = _validated_artifact_pins(run_metadata)
    policy_path = arguments.policy or _pinned_path(root, pins["policy"])
    artifact_manifest_path = arguments.artifact_manifest or _pinned_path(
        root, pins["artifact_manifest"]
    )
    source_paths = [
        arguments.input,
        arguments.cases,
        arguments.oracle,
        arguments.run_manifest,
        policy_path,
        artifact_manifest_path,
    ]
    _reject_output_alias(arguments.output, source_paths)
    for name, path in {
        "cases": arguments.cases,
        "oracle": arguments.oracle,
        "policy": policy_path,
        "artifact_manifest": artifact_manifest_path,
    }.items():
        _verify_artifact(path, pins[name], name)

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    if policy.get("policy_id") != run_metadata.get("policy", {}).get("policy_id"):
        raise ValueError("pinned policy_id does not match the run manifest")
    if policy.get("version") != run_metadata.get("policy", {}).get("version"):
        raise ValueError("pinned policy version does not match the run manifest")
    if artifact_manifest.get("policy_id") != policy.get("policy_id"):
        raise ValueError("artifact manifest policy_id does not match pinned policy")
    if artifact_manifest.get("policy_version") != policy.get("version"):
        raise ValueError("artifact manifest policy version does not match pinned policy")
    if artifact_manifest.get("dataset_role") != run_metadata.get("dataset_role"):
        raise ValueError("artifact manifest dataset_role does not match run manifest")
    manifest_hashes = artifact_manifest.get("artifacts_sha256", {})
    if manifest_hashes.get("cases.jsonl") != pins["cases"]["sha256"]:
        raise ValueError("artifact manifest cases hash does not match run manifest")
    if manifest_hashes.get("oracle.jsonl") != pins["oracle"]["sha256"]:
        raise ValueError("artifact manifest oracle hash does not match run manifest")

    with arguments.input.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    summary = score_manual_records(
        read_jsonl(arguments.cases),
        read_jsonl(arguments.oracle),
        records,
        run_metadata=run_metadata,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Scored {summary['case_count']} completed manual records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
