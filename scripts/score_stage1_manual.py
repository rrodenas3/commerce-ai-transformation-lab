#!/usr/bin/env python3
"""Validate and score completed SCC-01 manual baseline records."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_case_system import write_utf8_lf
from scripts.stage1_scoring import (
    ALLOWED_MANUAL_RUN_TYPES,
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_PROHIBITED_TOOLS,
    ESCALATION_ACTIONS,
    HELDOUT_ALLOWED_TOOLS,
    HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
    HELDOUT_ORACLE_EXPOSURE_PREPARED,
    HELDOUT_PROHIBITED_TOOLS,
    HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
    INSTRUCTIONS_PUBLIC_PATH,
    MANUAL_RUN_TYPE_BY_OPERATOR_ROLE,
    PUBLIC_ORACLE_EXPOSURE_STATUS,
    REQUIRED_MANUAL_FIELDS,
    REVIEWER_CODE_PATTERN,
    RUN_ID_PATTERN,
    RUN_MANIFEST_SCHEMA_VERSION,
    evaluate_decisions,
)


REQUIRED_PINNED_ARTIFACTS = {"cases", "oracle", "policy", "artifact_manifest"}
REQUIRED_RUN_FILES = {"case_pack", "policy_copy", "records_template"}


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_snapshot(path: Path, name: str) -> bytes:
    if not path.is_file():
        raise ValueError(f"required {name} artifact does not exist: {path}")
    return path.read_bytes()


def _snapshot_text(value: bytes, name: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{name} must be valid UTF-8") from error


def _snapshot_json(value: bytes, name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(_snapshot_text(value, name))
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def _snapshot_jsonl(value: bytes, name: str) -> list[dict[str, Any]]:
    try:
        parsed = [
            json.loads(line)
            for line in _snapshot_text(value, name).splitlines()
            if line
        ]
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be valid JSONL") from error
    if any(not isinstance(item, dict) for item in parsed):
        raise ValueError(f"{name} JSONL rows must be objects")
    return parsed


def _parse_manual_records_snapshot(value: bytes) -> list[dict[str, str]]:
    if value.startswith(b"\xef\xbb\xbf"):
        raise ValueError("manual records CSV must not contain a UTF-8 BOM")
    if b"\r" in value:
        raise ValueError("manual records CSV must use LF-only line endings")
    if not value.endswith(b"\n"):
        raise ValueError("manual records CSV must end with a final LF")
    text = _snapshot_text(value, "manual records CSV")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _validated_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} requires a lowercase SHA-256 digest")
    return value


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
        validated[name] = {
            "path": path,
            "sha256": _validated_sha256(
                digest, f"run manifest artifact '{name}'"
            ),
        }
    return validated


def _validated_run_provenance(run_metadata: dict[str, Any]) -> dict[str, Any]:
    provenance = run_metadata.get("run_provenance")
    required = {
        "status",
        "run_id",
        "reviewer_code",
        "operator_role",
        "prepared_at_utc",
    }
    if not isinstance(provenance, dict) or set(provenance) != required:
        raise ValueError("run manifest requires complete run_provenance")
    if provenance.get("status") != "prepared":
        raise ValueError("manual run manifest must have prepared status")
    run_id = provenance.get("run_id")
    reviewer_code = provenance.get("reviewer_code")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not isinstance(reviewer_code, str) or not REVIEWER_CODE_PATTERN.fullmatch(
        reviewer_code
    ):
        raise ValueError("invalid reviewer_code")
    role = provenance.get("operator_role")
    expected_type = MANUAL_RUN_TYPE_BY_OPERATOR_ROLE.get(role)
    if expected_type is None:
        raise ValueError("unsupported operator_role")
    if run_metadata.get("run_type") != expected_type:
        raise ValueError("operator_role does not match run_type")
    prepared_at = _utc_timestamp(
        provenance.get("prepared_at_utc"), "run_provenance.prepared_at_utc"
    )

    heldout = (
        run_metadata.get("schema_version") == HELDOUT_RUN_MANIFEST_SCHEMA_VERSION
    )
    expected_instructions_path = (
        HELDOUT_INSTRUCTIONS_PUBLIC_PATH if heldout else INSTRUCTIONS_PUBLIC_PATH
    )
    expected_tool_policy = {
        "allowed": list(HELDOUT_ALLOWED_TOOLS if heldout else DEFAULT_ALLOWED_TOOLS),
        "prohibited": list(
            HELDOUT_PROHIBITED_TOOLS if heldout else DEFAULT_PROHIBITED_TOOLS
        ),
    }
    instructions = run_metadata.get("instructions")
    if not isinstance(instructions, dict) or set(instructions) != {"path", "sha256"}:
        raise ValueError("run manifest requires an instructions pin")
    if instructions.get("path") != expected_instructions_path:
        raise ValueError("run manifest instructions path is unsupported")
    _validated_sha256(instructions.get("sha256"), "run manifest instructions")

    tool_policy = run_metadata.get("tool_policy")
    if tool_policy != expected_tool_policy:
        raise ValueError("run manifest tool_policy does not match the protocol")
    return {**provenance, "prepared_at": prepared_at}


def _validated_run_files(run_metadata: dict[str, Any]) -> dict[str, dict[str, str]]:
    run_files = run_metadata.get("run_files")
    if not isinstance(run_files, dict) or set(run_files) != REQUIRED_RUN_FILES:
        raise ValueError(
            "run manifest run_files must pin case_pack, policy_copy, and records_template"
        )
    validated: dict[str, dict[str, str]] = {}
    for name in sorted(REQUIRED_RUN_FILES):
        pin = run_files.get(name)
        if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
            raise ValueError(f"run file '{name}' must pin path and sha256")
        path = pin.get("path")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ValueError(f"run file '{name}' requires a safe relative path")
        validated[name] = {
            "path": path,
            "sha256": _validated_sha256(pin.get("sha256"), f"run file '{name}'"),
        }
    return validated


def _validate_run_metadata(
    run_metadata: dict[str, Any],
    cases: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, str]], dict[str, Any]]:
    if not isinstance(run_metadata, dict):
        raise ValueError("manual run manifest must be a JSON object")
    schema_version = run_metadata.get("schema_version")
    if schema_version not in {
        RUN_MANIFEST_SCHEMA_VERSION,
        HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported manual run manifest schema_version")
    if run_metadata.get("run_type") not in ALLOWED_MANUAL_RUN_TYPES:
        raise ValueError("unsupported manual run manifest run_type")
    expected_exposure_status = (
        HELDOUT_ORACLE_EXPOSURE_PREPARED
        if schema_version == HELDOUT_RUN_MANIFEST_SCHEMA_VERSION
        else PUBLIC_ORACLE_EXPOSURE_STATUS
    )
    if run_metadata.get("oracle_exposure_status") != expected_exposure_status:
        raise ValueError(
            "run manifest oracle_exposure_status must match its evidence protocol "
            f"('{expected_exposure_status}')"
        )
    if not isinstance(run_metadata.get("dataset_role"), str) or not run_metadata[
        "dataset_role"
    ]:
        raise ValueError("run manifest requires dataset_role")
    run_provenance = _validated_run_provenance(run_metadata)

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
    case_id_set = set(case_ids)
    oracle_id_set = set(oracle_ids)
    missing_cases = [case_id for case_id in assigned if case_id not in case_id_set]
    missing_oracles = [case_id for case_id in assigned if case_id not in oracle_id_set]
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
    return assigned, pins, run_provenance


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
        "_started_at": started,
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


def _validate_preparation_anchor(
    anchor: dict[str, Any],
    current_manifest_snapshot: bytes,
    earliest_handling_start: datetime,
) -> dict[str, str]:
    required = {
        "commit_sha",
        "commit_timestamp_utc",
        "manifest_snapshot",
        "records_template_snapshot",
    }
    if not isinstance(anchor, dict) or set(anchor) != required:
        raise ValueError("preparation anchor is incomplete")
    commit_sha = anchor.get("commit_sha")
    if not isinstance(commit_sha, str) or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha
    ):
        raise ValueError("preparation anchor commit SHA is invalid")
    manifest_snapshot = anchor.get("manifest_snapshot")
    records_template_snapshot = anchor.get("records_template_snapshot")
    if not isinstance(manifest_snapshot, bytes) or not isinstance(
        records_template_snapshot, bytes
    ):
        raise ValueError("preparation anchor snapshots must be bytes")
    if manifest_snapshot != current_manifest_snapshot:
        raise ValueError(
            "committed preparation manifest does not exactly match the current manifest"
        )
    anchored_manifest = _snapshot_json(
        manifest_snapshot, "committed preparation manifest"
    )
    anchored_run_files = _validated_run_files(anchored_manifest)
    expected_template_digest = anchored_run_files["records_template"]["sha256"]
    if _sha256_bytes(records_template_snapshot) != expected_template_digest:
        raise ValueError(
            "committed blank records template does not match the anchored manifest"
        )
    commit_timestamp = _utc_timestamp(
        anchor.get("commit_timestamp_utc"), "preparation commit timestamp"
    )
    if commit_timestamp > earliest_handling_start:
        raise ValueError("preparation commit must precede handling starts")
    return {
        "preparation_commit_sha": commit_sha,
        "preparation_commit_timestamp_utc": anchor["commit_timestamp_utc"],
        "anchored_run_manifest_sha256": _sha256_bytes(manifest_snapshot),
    }


def score_manual_records(
    cases: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
    records: list[dict[str, str]],
    *,
    run_metadata: dict[str, Any],
    preparation_anchor: dict[str, Any] | None = None,
    run_manifest_snapshot: bytes | None = None,
) -> dict[str, Any]:
    if not records:
        raise ValueError("at least one completed manual record is required")
    assigned_case_ids, artifact_pins, run_provenance = _validate_run_metadata(
        run_metadata, cases, oracles
    )
    normalized = [_normalize_record(record) for record in records]
    anchor_provenance: dict[str, str] = {}
    if preparation_anchor is not None:
        if run_manifest_snapshot is None:
            raise ValueError(
                "run manifest snapshot is required with a preparation anchor"
            )
        anchor_provenance = _validate_preparation_anchor(
            preparation_anchor,
            run_manifest_snapshot,
            min(record["_started_at"] for record in normalized),
        )
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
    reviewer_codes = {record["reviewer_code"] for record in normalized}
    if reviewer_codes != {run_provenance["reviewer_code"]}:
        raise ValueError("every manual record reviewer_code must match the run manifest")
    if min(record["_started_at"] for record in normalized) < run_provenance[
        "prepared_at"
    ]:
        raise ValueError("run manifest must be prepared before handling starts")
    baseline_id = run_metadata["run_type"]
    summary = evaluate_decisions(
        selected_cases,
        decisions,
        selected_oracles,
        baseline_id=baseline_id,
    )
    handling = [record["active_handling_seconds"] for record in normalized]
    independent_review = run_provenance["operator_role"] == "independent"
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
            "reviewer_count": len(reviewer_codes),
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
                "run_id": run_provenance["run_id"],
                "reviewer_code": run_provenance["reviewer_code"],
                "operator_role": run_provenance["operator_role"],
                "prepared_at_utc": run_provenance["prepared_at_utc"],
                "oracle_exposure_status": run_metadata["oracle_exposure_status"],
                "policy_id": run_metadata["policy"]["policy_id"],
                "policy_version": run_metadata["policy"]["version"],
                "oracle_version": run_metadata["oracle"]["version"],
                "artifact_sha256": {
                    name: artifact_pins[name]["sha256"]
                    for name in sorted(artifact_pins)
                },
                **anchor_provenance,
            },
            "human_evidence_boundary": (
                "Independent synthetic-case review; not organisational adoption "
                "or realised business impact."
                if independent_review
                else "Creator-run synthetic-case observation; not independent review, "
                "organisational adoption, or realised business impact."
            ),
            "evidence_class": (
                "human-reviewed" if independent_review else "synthetic-observed"
            ),
            "independent_review": independent_review,
        }
    )
    return summary


def _pinned_path(root: Path, pin: dict[str, str]) -> Path:
    path = Path(pin["path"])
    return path if path.is_absolute() else root / path


def _run_file_path(run_manifest: Path, pin: dict[str, str]) -> Path:
    run_root = run_manifest.resolve().parent
    path = (run_root / pin["path"]).resolve()
    if os.path.commonpath((str(run_root), str(path))) != str(run_root):
        raise ValueError("run file path escapes the prepared run directory")
    return path


def _git_output(root: Path, arguments: list[str], label: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise ValueError(f"could not use Git to resolve {label}") from error
    if result.returncode != 0:
        raise ValueError(f"could not resolve {label} from Git")
    return result.stdout


def _load_git_preparation_anchor(
    root: Path,
    preparation_ref: str,
    run_manifest: Path,
) -> dict[str, Any]:
    if not preparation_ref or preparation_ref.isspace():
        raise ValueError("--preparation-ref must identify a Git commit")
    repository_root = root.resolve()
    try:
        manifest_repo_path = run_manifest.resolve().relative_to(repository_root)
    except ValueError as error:
        raise ValueError("run manifest must be inside the Git repository") from error
    resolved = _git_output(
        repository_root,
        [
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{preparation_ref}^{{commit}}",
        ],
        "preparation commit",
    ).decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved):
        raise ValueError("Git returned an invalid preparation commit SHA")

    timestamp_text = _git_output(
        repository_root,
        ["show", "-s", "--format=%cI", resolved],
        "preparation commit timestamp",
    ).decode("ascii", errors="strict").strip()
    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError as error:
        raise ValueError("Git returned an invalid preparation commit timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Git returned a timezone-free preparation commit timestamp")
    timestamp_utc = (
        timestamp.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    manifest_snapshot = _git_output(
        repository_root,
        ["cat-file", "blob", f"{resolved}:{manifest_repo_path.as_posix()}"],
        "committed preparation manifest",
    )
    anchored_manifest = _snapshot_json(
        manifest_snapshot, "committed preparation manifest"
    )
    anchored_run_files = _validated_run_files(anchored_manifest)
    records_repo_path = (
        manifest_repo_path.parent
        / Path(anchored_run_files["records_template"]["path"])
    )
    records_template_snapshot = _git_output(
        repository_root,
        ["cat-file", "blob", f"{resolved}:{records_repo_path.as_posix()}"],
        "committed blank records template",
    )
    return {
        "commit_sha": resolved,
        "commit_timestamp_utc": timestamp_utc,
        "manifest_snapshot": manifest_snapshot,
        "records_template_snapshot": records_template_snapshot,
    }


def _verify_artifact(path: Path, pin: dict[str, str], name: str) -> None:
    if not path.is_file():
        raise ValueError(f"pinned {name} artifact does not exist: {path}")
    actual = _sha256(path)
    if actual != pin["sha256"]:
        raise ValueError(
            f"pinned {name} SHA-256 mismatch: expected {pin['sha256']}, got {actual}"
        )


def _verify_snapshot(
    value: bytes, pin: dict[str, str], name: str
) -> None:
    actual = _sha256_bytes(value)
    if actual != pin["sha256"]:
        raise ValueError(
            f"pinned {name} SHA-256 mismatch: expected {pin['sha256']}, got {actual}"
        )


def _read_named_snapshots(paths: dict[str, Path]) -> dict[str, bytes]:
    cache: dict[str, bytes] = {}
    snapshots: dict[str, bytes] = {}
    for name, path in paths.items():
        key = os.path.normcase(str(path.resolve()))
        if key not in cache:
            cache[key] = _read_snapshot(path, name)
        snapshots[name] = cache[key]
    return snapshots


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
        "--preparation-ref",
        required=True,
        help="Git ref whose commit contains the frozen manifest and blank records template",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="Prepared policy copy; defaults to the run-manifest run_files pin",
    )
    parser.add_argument(
        "--artifact-manifest",
        type=Path,
        help="Pinned source artifact manifest; defaults to the run-manifest path pin",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_manifest_snapshot = _read_snapshot(
        arguments.run_manifest, "manual run manifest"
    )
    run_metadata = _snapshot_json(run_manifest_snapshot, "manual run manifest")
    pins = _validated_artifact_pins(run_metadata)
    run_files = _validated_run_files(run_metadata)
    case_pack_path = _run_file_path(arguments.run_manifest, run_files["case_pack"])
    records_path = _run_file_path(arguments.run_manifest, run_files["records_template"])
    policy_copy_path = _run_file_path(arguments.run_manifest, run_files["policy_copy"])
    if not _same_path(arguments.cases, case_pack_path):
        raise ValueError("--cases must use the prepared run case pack")
    if not _same_path(arguments.input, records_path):
        raise ValueError("--input must use the prepared run records file")
    policy_path = arguments.policy or policy_copy_path
    if not _same_path(policy_path, policy_copy_path):
        raise ValueError("--policy must use the prepared run policy copy")
    artifact_manifest_path = arguments.artifact_manifest or _pinned_path(
        root, pins["artifact_manifest"]
    )
    instructions_path = root / run_metadata["instructions"]["path"]
    source_paths = [
        arguments.input,
        arguments.cases,
        arguments.oracle,
        arguments.run_manifest,
        policy_path,
        artifact_manifest_path,
        instructions_path,
    ]
    _reject_output_alias(arguments.output, source_paths)
    snapshots = _read_named_snapshots(
        {
            "records": arguments.input,
            "cases": arguments.cases,
            "oracle": arguments.oracle,
            "policy": policy_path,
            "artifact_manifest": artifact_manifest_path,
            "instructions": instructions_path,
        }
    )
    for name in (
        "cases",
        "oracle",
        "policy",
        "artifact_manifest",
    ):
        _verify_snapshot(snapshots[name], pins[name], name)
    _verify_snapshot(
        snapshots["cases"], run_files["case_pack"], "prepared case_pack"
    )
    _verify_snapshot(
        snapshots["policy"], run_files["policy_copy"], "prepared policy_copy"
    )
    _verify_snapshot(
        snapshots["instructions"], run_metadata["instructions"], "instructions"
    )
    if run_files["case_pack"]["sha256"] != pins["cases"]["sha256"]:
        raise ValueError("prepared case_pack hash does not match pinned cases")
    if run_files["policy_copy"]["sha256"] != pins["policy"]["sha256"]:
        raise ValueError("prepared policy_copy hash does not match pinned policy")

    policy = _snapshot_json(snapshots["policy"], "policy")
    artifact_manifest = _snapshot_json(
        snapshots["artifact_manifest"], "source artifact manifest"
    )
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

    records = _parse_manual_records_snapshot(snapshots["records"])
    preparation_anchor = _load_git_preparation_anchor(
        root, arguments.preparation_ref, arguments.run_manifest
    )
    summary = score_manual_records(
        _snapshot_jsonl(snapshots["cases"], "cases"),
        _snapshot_jsonl(snapshots["oracle"], "oracle"),
        records,
        run_metadata=run_metadata,
        preparation_anchor=preparation_anchor,
        run_manifest_snapshot=run_manifest_snapshot,
    )
    summary["manual_run_provenance"].update(
        {
            "records_sha256": _sha256_bytes(snapshots["records"]),
            "records_template_sha256": run_files["records_template"]["sha256"],
            "run_manifest_sha256": _sha256_bytes(run_manifest_snapshot),
        }
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_lf(
        arguments.output,
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    print(f"Scored {summary['case_count']} completed manual records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
