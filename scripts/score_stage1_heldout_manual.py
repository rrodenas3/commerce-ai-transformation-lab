#!/usr/bin/env python3
"""Score a held-out manual run against an oracle released after record freeze."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.score_stage1_manual import (
    _load_git_preparation_anchor,
    _parse_manual_records_snapshot,
    _pinned_path,
    _read_named_snapshots,
    _read_snapshot,
    _reject_output_alias,
    _run_file_path,
    _same_path,
    _snapshot_json,
    _snapshot_jsonl,
    _validated_artifact_pins,
    _validated_run_files,
    _verify_snapshot,
    _utc_timestamp,
    score_manual_records,
)
from scripts.stage1_case_system import HELDOUT_DATASET_ROLE, write_utf8_lf
from scripts.stage1_heldout import (
    HELDOUT_EVALUATION_PACK_ID,
    HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
    HELDOUT_ORACLE_RELEASE_PATH,
    HELDOUT_PACK_SCHEMA_VERSION,
    _sha256_bytes,
    validate_heldout_run_bindings,
)
from scripts.stage1_heldout_release import (
    ORACLE_RELEASE_SCHEMA_VERSION,
    load_records_freeze_anchor,
)
from scripts.stage1_scoring import (
    HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
    HELDOUT_ORACLE_EXPOSURE_PREPARED,
    HELDOUT_ORACLE_EXPOSURE_RELEASED,
    HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
)


def _validate_release_manifest(
    release: dict[str, Any],
    run_metadata: dict[str, Any],
    public_manifest: dict[str, Any],
    oracle_snapshot: bytes,
    records_snapshot: bytes,
) -> dict[str, Any]:
    if release.get("schema_version") != ORACLE_RELEASE_SCHEMA_VERSION:
        raise ValueError("unsupported oracle release manifest schema")
    if release.get("evaluation_pack_id") != HELDOUT_EVALUATION_PACK_ID:
        raise ValueError("oracle release evaluation pack is inconsistent")
    if release.get("run_id") != run_metadata["run_provenance"]["run_id"]:
        raise ValueError("oracle release run_id is inconsistent")
    transition = release.get("state_transition")
    if (
        not isinstance(transition, dict)
        or transition.get("from") != (HELDOUT_ORACLE_EXPOSURE_PREPARED)
        or transition.get("to") != HELDOUT_ORACLE_EXPOSURE_RELEASED
    ):
        raise ValueError("oracle release state transition is invalid")
    oracle = release.get("oracle")
    oracle_hash = _sha256_bytes(oracle_snapshot)
    if not isinstance(oracle, dict) or oracle != {
        "path": HELDOUT_ORACLE_RELEASE_PATH,
        "sha256": oracle_hash,
        "version": run_metadata["oracle"]["version"],
    }:
        raise ValueError("oracle release pin is inconsistent")
    if oracle_hash != run_metadata["oracle"]["sha256_commitment"]:
        raise ValueError("released oracle does not match the run commitment")
    reproducibility = release.get("generator_reproducibility")
    if (
        not isinstance(reproducibility, dict)
        or reproducibility.get("name") != public_manifest.get("generator_name")
        or reproducibility.get("version") != public_manifest.get("generator_version")
        or reproducibility.get("seed_commitment_sha256")
        != public_manifest.get("generator_seed_commitment_sha256")
    ):
        raise ValueError("released generator seed does not match its commitment")
    released_seed = reproducibility.get("seed_released_after_record_freeze")
    if (
        not isinstance(released_seed, str)
        or _sha256_bytes(released_seed.encode("utf-8"))
        != reproducibility["seed_commitment_sha256"]
    ):
        raise ValueError("released generator seed is invalid")
    records_freeze = release.get("records_freeze")
    if not isinstance(records_freeze, dict) or records_freeze.get(
        "records_sha256"
    ) != _sha256_bytes(records_snapshot):
        raise ValueError("current records do not match the pre-release freeze")
    records_commit_time = _utc_timestamp(
        records_freeze.get("records_commit_timestamp_utc"),
        "records_freeze.records_commit_timestamp_utc",
    )
    release_time = _utc_timestamp(
        transition.get("released_at_utc"),
        "state_transition.released_at_utc",
    )
    if release_time < records_commit_time:
        raise ValueError("oracle release timestamp must follow the records commit")
    return records_freeze


def score_heldout_run(
    project_root: Path,
    *,
    input_path: Path,
    output_path: Path,
    cases_path: Path,
    oracle_path: Path,
    run_manifest_path: Path,
    release_manifest_path: Path,
) -> dict[str, Any]:
    root = project_root.resolve()
    run_manifest_snapshot = _read_snapshot(run_manifest_path, "held-out run manifest")
    run_metadata = _snapshot_json(run_manifest_snapshot, "held-out run manifest")
    if (
        run_metadata.get("schema_version") != HELDOUT_RUN_MANIFEST_SCHEMA_VERSION
        or run_metadata.get("dataset_role") != HELDOUT_DATASET_ROLE
        or run_metadata.get("oracle_exposure_status")
        != HELDOUT_ORACLE_EXPOSURE_PREPARED
    ):
        raise ValueError("run manifest is not a process-controlled held-out run")
    pins = _validated_artifact_pins(run_metadata)
    run_files = _validated_run_files(run_metadata)
    case_pack_path = _run_file_path(run_manifest_path, run_files["case_pack"])
    records_path = _run_file_path(run_manifest_path, run_files["records_template"])
    policy_path = _run_file_path(run_manifest_path, run_files["policy_copy"])
    if not _same_path(cases_path, case_pack_path):
        raise ValueError("--cases must use the prepared held-out case pack")
    if not _same_path(input_path, records_path):
        raise ValueError("--input must use the frozen completed records file")
    expected_oracle = _pinned_path(root, pins["oracle"])
    if not _same_path(oracle_path, expected_oracle):
        raise ValueError("--oracle must use the released held-out oracle")
    expected_release_manifest = root / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH
    if not _same_path(release_manifest_path, expected_release_manifest):
        raise ValueError("--release-manifest must use the held-out release record")
    public_manifest_path = _pinned_path(root, pins["artifact_manifest"])
    public_cases_path = _pinned_path(root, pins["cases"])
    source_policy_path = _pinned_path(root, pins["policy"])
    instructions_path = root / HELDOUT_INSTRUCTIONS_PUBLIC_PATH
    source_paths = [
        input_path,
        cases_path,
        oracle_path,
        run_manifest_path,
        release_manifest_path,
        policy_path,
        public_cases_path,
        source_policy_path,
        public_manifest_path,
        instructions_path,
    ]
    _reject_output_alias(output_path, source_paths)
    snapshots = _read_named_snapshots(
        {
            "records": input_path,
            "cases": cases_path,
            "oracle": oracle_path,
            "release_manifest": release_manifest_path,
            "policy": policy_path,
            "public_cases": public_cases_path,
            "source_policy": source_policy_path,
            "artifact_manifest": public_manifest_path,
            "instructions": instructions_path,
        }
    )
    for name in ("cases", "oracle", "policy", "artifact_manifest"):
        _verify_snapshot(snapshots[name], pins[name], name)
    _verify_snapshot(snapshots["public_cases"], pins["cases"], "public cases")
    _verify_snapshot(snapshots["source_policy"], pins["policy"], "source policy")
    _verify_snapshot(snapshots["cases"], run_files["case_pack"], "prepared case pack")
    _verify_snapshot(
        snapshots["policy"], run_files["policy_copy"], "prepared policy copy"
    )
    _verify_snapshot(
        snapshots["instructions"], run_metadata["instructions"], "instructions"
    )
    public_manifest = _snapshot_json(
        snapshots["artifact_manifest"], "held-out public manifest"
    )
    if (
        public_manifest.get("schema_version") != HELDOUT_PACK_SCHEMA_VERSION
        or public_manifest.get("evaluation_pack_id") != HELDOUT_EVALUATION_PACK_ID
        or public_manifest.get("dataset_role") != HELDOUT_DATASET_ROLE
        or public_manifest.get("oracle_release_status") != "answer-file-not-published"
    ):
        raise ValueError("held-out public manifest is inconsistent")
    validate_heldout_run_bindings(
        run_metadata,
        public_manifest,
        public_manifest_bytes=snapshots["artifact_manifest"],
        public_cases_bytes=snapshots["public_cases"],
        prepared_case_pack_bytes=snapshots["cases"],
        policy_bytes=snapshots["source_policy"],
        prepared_policy_bytes=snapshots["policy"],
        instructions_bytes=snapshots["instructions"],
    )
    release = _snapshot_json(snapshots["release_manifest"], "oracle release manifest")
    records_freeze = _validate_release_manifest(
        release,
        run_metadata,
        public_manifest,
        snapshots["oracle"],
        snapshots["records"],
    )
    anchor = load_records_freeze_anchor(
        root,
        run_manifest_path,
        run_metadata,
        preparation_ref=records_freeze["preparation_commit_sha"],
        records_ref=records_freeze["records_commit_sha"],
    )
    for key in (
        "preparation_commit_sha",
        "preparation_commit_timestamp_utc",
        "records_commit_sha",
        "records_commit_timestamp_utc",
        "records_sha256",
        "run_manifest_sha256",
    ):
        if records_freeze.get(key) != anchor.get(key):
            raise ValueError(f"oracle release records freeze has stale {key}")
    preparation_anchor = _load_git_preparation_anchor(
        root, records_freeze["preparation_commit_sha"], run_manifest_path
    )
    summary = score_manual_records(
        _snapshot_jsonl(snapshots["cases"], "held-out cases"),
        _snapshot_jsonl(snapshots["oracle"], "released oracle"),
        _parse_manual_records_snapshot(snapshots["records"]),
        run_metadata=run_metadata,
        preparation_anchor=preparation_anchor,
        run_manifest_snapshot=run_manifest_snapshot,
    )
    provenance = summary["manual_run_provenance"]
    provenance.update(
        {
            "oracle_exposure_status_at_preparation": (HELDOUT_ORACLE_EXPOSURE_PREPARED),
            "oracle_exposure_status": HELDOUT_ORACLE_EXPOSURE_RELEASED,
            "records_commit_sha": records_freeze["records_commit_sha"],
            "records_commit_timestamp_utc": records_freeze[
                "records_commit_timestamp_utc"
            ],
            "records_sha256": records_freeze["records_sha256"],
            "run_manifest_sha256": records_freeze["run_manifest_sha256"],
            "oracle_release_manifest_sha256": _sha256_bytes(
                snapshots["release_manifest"]
            ),
            "oracle_released_at_utc": release["state_transition"]["released_at_utc"],
        }
    )
    summary["interpretation"] = (
        "Held-out synthetic-case result scored only after the completed human "
        "record was frozen and the oracle was released. It measures decision and "
        "safe-routing performance within this laboratory boundary, not customer "
        "recovery, adoption, production reliability, or business value."
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_utf8_lf(
        output_path,
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--release-manifest", required=True, type=Path)
    arguments = parser.parse_args()
    summary = score_heldout_run(
        Path(__file__).resolve().parents[1],
        input_path=arguments.input,
        output_path=arguments.output,
        cases_path=arguments.cases,
        oracle_path=arguments.oracle,
        run_manifest_path=arguments.run_manifest,
        release_manifest_path=arguments.release_manifest,
    )
    print(f"Scored {summary['case_count']} frozen held-out manual records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
