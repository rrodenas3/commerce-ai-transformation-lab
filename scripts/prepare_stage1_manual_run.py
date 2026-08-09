#!/usr/bin/env python3
"""Prepare a frozen, case-only SCC-01 manual baseline evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_scoring import (
    DEFAULT_ALLOWED_TOOLS,
    DEFAULT_PROHIBITED_TOOLS,
    INSTRUCTIONS_PUBLIC_PATH,
    MANUAL_RUN_TYPE_BY_OPERATOR_ROLE,
    REVIEWER_CODE_PATTERN,
    RUN_ID_PATTERN,
    RUN_MANIFEST_SCHEMA_VERSION,
    write_manual_template,
)
from scripts.stage1_case_system import write_utf8_lf


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("prepared_at must be timezone-aware UTC")
    normalized = value.astimezone(timezone.utc)
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("prepared_at must be timezone-aware UTC")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _file_pin(path: Path, public_path: str) -> dict[str, str]:
    return {"path": public_path, "sha256": _sha256_bytes(path.read_bytes())}


def _snapshot_json(value: bytes, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} snapshot is not valid UTF-8 JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} snapshot must be a JSON object")
    return parsed


def _snapshot_jsonl(value: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = value.decode("utf-8")
        parsed = [json.loads(line) for line in text.splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} snapshot is not valid UTF-8 JSONL") from error
    if not parsed or any(not isinstance(item, dict) for item in parsed):
        raise ValueError(f"{label} snapshot must contain JSON objects")
    return parsed


def _validate_source_snapshots(
    manifest: dict[str, Any],
    snapshots: dict[str, bytes],
) -> list[dict[str, Any]]:
    expected_artifact_paths = {
        "cases": "data/stage1/generated/cases.jsonl",
        "oracle": "data/stage1/generated/oracle.jsonl",
        "policy": "data/stage1/policy.json",
        "artifact_manifest": "data/stage1/generated/manifest.json",
    }
    pins = manifest.get("artifacts")
    if not isinstance(pins, dict) or set(pins) != set(expected_artifact_paths):
        raise ValueError("manual run manifest template artifact pins are incomplete")
    for name, expected_path in expected_artifact_paths.items():
        pin = pins.get(name)
        if not isinstance(pin, dict) or pin.get("path") != expected_path:
            raise ValueError(f"manual run manifest template {name} path is stale")
        if pin.get("sha256") != _sha256_bytes(snapshots[name]):
            raise ValueError(f"manual run manifest template {name} SHA-256 is stale")

    instructions = manifest.get("instructions")
    if not isinstance(instructions, dict) or instructions.get(
        "path"
    ) != INSTRUCTIONS_PUBLIC_PATH:
        raise ValueError("manual run manifest template instructions path is stale")
    if instructions.get("sha256") != _sha256_bytes(snapshots["instructions"]):
        raise ValueError("manual run manifest template instructions SHA-256 is stale")

    cases = _snapshot_jsonl(snapshots["cases"], "cases")
    oracles = _snapshot_jsonl(snapshots["oracle"], "oracle")
    policy = _snapshot_json(snapshots["policy"], "policy")
    source_manifest = _snapshot_json(
        snapshots["artifact_manifest"], "source artifact manifest"
    )
    source_hashes = source_manifest.get("artifacts_sha256")
    if not isinstance(source_hashes, dict):
        raise ValueError("source artifact manifest hashes are incomplete")
    for filename, snapshot_name in {
        "cases.jsonl": "cases",
        "oracle.jsonl": "oracle",
    }.items():
        if source_hashes.get(filename) != _sha256_bytes(snapshots[snapshot_name]):
            raise ValueError(f"source artifact manifest {filename} SHA-256 is stale")

    case_ids = [case.get("case_id") for case in cases]
    oracle_ids = [oracle.get("case_id") for oracle in oracles]
    if (
        any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
        or len(set(case_ids)) != len(case_ids)
        or oracle_ids != case_ids
        or manifest.get("assigned_case_ids") != case_ids
    ):
        raise ValueError("manual run manifest assigned case IDs or order is stale")
    if source_manifest.get("case_count") != len(cases):
        raise ValueError("source artifact manifest case count is stale")

    dataset_role = manifest.get("dataset_role")
    if (
        not isinstance(dataset_role, str)
        or not dataset_role
        or source_manifest.get("dataset_role") != dataset_role
        or any(case.get("dataset_role") != dataset_role for case in cases)
    ):
        raise ValueError("manual run dataset role is inconsistent")

    policy_pin = manifest.get("policy")
    if not isinstance(policy_pin, dict):
        raise ValueError("manual run policy pin is missing")
    policy_id = policy.get("policy_id")
    policy_version = policy.get("version")
    if (
        policy_pin != {"policy_id": policy_id, "version": policy_version}
        or source_manifest.get("policy_id") != policy_id
        or source_manifest.get("policy_version") != policy_version
        or any(
            case.get("policy")
            != {"policy_id": policy_id, "version": policy_version}
            for case in cases
        )
        or any(oracle.get("policy_version") != policy_version for oracle in oracles)
    ):
        raise ValueError("manual run policy identity or version is inconsistent")

    oracle_versions = {oracle.get("oracle_version") for oracle in oracles}
    oracle_pin = manifest.get("oracle")
    if (
        len(oracle_versions) != 1
        or None in oracle_versions
        or not isinstance(oracle_pin, dict)
        or oracle_pin.get("version") != next(iter(oracle_versions))
    ):
        raise ValueError("manual run oracle version is inconsistent")
    return cases


def prepare_manual_run(
    project_root: Path,
    output_dir: Path,
    *,
    run_id: str,
    reviewer_code: str,
    operator_role: str,
    prepared_at: datetime,
) -> dict[str, Any]:
    """Create a new immutable preparation pack without oracle answer files."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not REVIEWER_CODE_PATTERN.fullmatch(reviewer_code):
        raise ValueError("invalid reviewer_code")
    run_type = MANUAL_RUN_TYPE_BY_OPERATOR_ROLE.get(operator_role)
    if run_type is None:
        raise ValueError("unsupported operator_role")
    prepared_at_utc = _utc_text(prepared_at)

    root = project_root.resolve()
    output = output_dir.resolve()
    if output.exists():
        raise ValueError("manual run output directory must not already exist")
    if output.name != run_id:
        raise ValueError("manual run output directory name must match run_id")
    output.parent.mkdir(parents=True, exist_ok=True)

    generated = root / "data" / "stage1" / "generated"
    cases_source = generated / "cases.jsonl"
    policy_source = root / "data" / "stage1" / "policy.json"
    template_source = generated / "manual-run-manifest-template.json"
    oracle_source = generated / "oracle.jsonl"
    artifact_manifest_source = generated / "manifest.json"
    instructions_source = root / INSTRUCTIONS_PUBLIC_PATH
    for label, path in {
        "cases": cases_source,
        "oracle": oracle_source,
        "policy": policy_source,
        "source artifact manifest": artifact_manifest_source,
        "manifest template": template_source,
        "instructions": instructions_source,
    }.items():
        if not path.is_file():
            raise ValueError(f"required {label} artifact does not exist: {path}")

    source_paths = {
        "template": template_source,
        "cases": cases_source,
        "oracle": oracle_source,
        "policy": policy_source,
        "artifact_manifest": artifact_manifest_source,
        "instructions": instructions_source,
    }
    snapshots = {name: path.read_bytes() for name, path in source_paths.items()}
    manifest = _snapshot_json(snapshots["template"], "manifest template")
    if manifest.get("schema_version") != RUN_MANIFEST_SCHEMA_VERSION:
        raise ValueError("manual run manifest template schema is stale")
    cases = _validate_source_snapshots(manifest, snapshots)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-preparing-", dir=output.parent)
    )
    try:
        case_pack = staging / "case-pack.jsonl"
        policy_copy = staging / "policy.json"
        records = staging / "manual-records.csv"
        case_pack.write_bytes(snapshots["cases"])
        policy_copy.write_bytes(snapshots["policy"])
        write_manual_template(
            records,
            cases,
            reviewer_code=reviewer_code,
            run_type=run_type,
        )

        manifest.update(
            {
                "run_type": run_type,
                "run_provenance": {
                    "status": "prepared",
                    "run_id": run_id,
                    "reviewer_code": reviewer_code,
                    "operator_role": operator_role,
                    "prepared_at_utc": prepared_at_utc,
                },
                "instructions": {
                    "path": INSTRUCTIONS_PUBLIC_PATH,
                    "sha256": _sha256_bytes(snapshots["instructions"]),
                },
                "tool_policy": {
                    "allowed": list(DEFAULT_ALLOWED_TOOLS),
                    "prohibited": list(DEFAULT_PROHIBITED_TOOLS),
                },
                "run_files": {
                    "case_pack": _file_pin(case_pack, "case-pack.jsonl"),
                    "policy_copy": _file_pin(policy_copy, "policy.json"),
                    "records_template": _file_pin(
                        records, "manual-records.csv"
                    ),
                },
            }
        )
        write_utf8_lf(
            staging / "run-manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        staging.replace(output)
    finally:
        if staging.exists() and staging.parent.resolve() == output.parent.resolve():
            shutil.rmtree(staging)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reviewer-code", required=True)
    parser.add_argument(
        "--operator-role", required=True, choices=("creator", "independent")
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prepare_manual_run(
        root,
        arguments.output,
        run_id=arguments.run_id,
        reviewer_code=arguments.reviewer_code,
        operator_role=arguments.operator_role,
        prepared_at=datetime.now(timezone.utc),
    )
    print(f"Prepared case-only manual run at {arguments.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
