"""Release the held-out oracle only after a completed record is frozen in Git."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.score_stage1_manual import (
    _git_output,
    _normalize_record,
    _parse_manual_records_snapshot,
    _sha256_bytes,
    _utc_timestamp,
    _validate_run_metadata,
    _validated_artifact_pins,
    _validated_run_files,
)
from scripts.stage1_case_system import (
    HELDOUT_DATASET_ROLE,
    build_oracle,
    load_stage1_policy,
    read_jsonl,
)
from scripts.stage1_heldout import (
    HELDOUT_EVALUATION_PACK_ID,
    HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
    HELDOUT_ORACLE_RELEASE_PATH,
    HELDOUT_PACK_SCHEMA_VERSION,
    HELDOUT_PUBLIC_PATH,
    OPERATOR_GUIDE_FILE,
    _canonical_json_bytes,
    _canonical_jsonl_bytes,
    build_heldout_cases,
    build_operator_guide,
    build_operator_case_pack,
    load_private_generation_material,
    load_private_oracle,
    validate_heldout_run_bindings,
)
from scripts.stage1_scoring import (
    HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
    HELDOUT_ORACLE_EXPOSURE_PREPARED,
    HELDOUT_ORACLE_EXPOSURE_RELEASED,
    HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
)


ORACLE_RELEASE_SCHEMA_VERSION = "1.0.0"


def _write_release_pair(outputs: tuple[tuple[Path, bytes], ...]) -> None:
    installed: list[Path] = []
    try:
        for path, value in outputs:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            installed.append(path)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                handle.write(value)
    except FileExistsError as error:
        for path in installed:
            path.unlink(missing_ok=True)
        raise ValueError("held-out oracle release artifacts already exist") from error
    except BaseException:
        for path in installed:
            path.unlink(missing_ok=True)
        raise


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("release timestamp must be timezone-aware UTC")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _resolve_commit(root: Path, ref: str, label: str) -> str:
    if not isinstance(ref, str) or not ref or ref.isspace():
        raise ValueError(f"{label} must identify a Git commit")
    resolved = (
        _git_output(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            label,
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", resolved):
        raise ValueError(f"Git returned an invalid {label} SHA")
    return resolved


def _commit_timestamp(root: Path, commit_sha: str, label: str) -> tuple[str, datetime]:
    text = (
        _git_output(
            root,
            ["show", "-s", "--format=%cI", commit_sha],
            f"{label} timestamp",
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"Git returned an invalid {label} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Git returned a timezone-free {label} timestamp")
    normalized = parsed.astimezone(timezone.utc)
    return (
        normalized.isoformat(timespec="seconds").replace("+00:00", "Z"),
        normalized,
    )


def _git_blob(root: Path, commit_sha: str, relative: Path, label: str) -> bytes:
    return _git_output(
        root,
        ["cat-file", "blob", f"{commit_sha}:{relative.as_posix()}"],
        label,
    )


def _require_git_path_absent(
    root: Path, commit_sha: str, relative: Path, label: str
) -> None:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit_sha}:{relative.as_posix()}"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        raise ValueError(f"{label} must be absent before oracle release")
    if result.returncode not in {1, 128}:
        raise ValueError(f"could not verify absence of {label} from Git")


def _require_ancestor(root: Path, ancestor: str, descendant: str) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ValueError("preparation commit must be an ancestor of records commit")


def _require_immediate_records_commit(
    root: Path, preparation_sha: str, records_sha: str
) -> None:
    count = (
        _git_output(
            root,
            ["rev-list", "--count", f"{preparation_sha}..{records_sha}"],
            "preparation-to-records commit range",
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    if count != "1":
        raise ValueError("records commit must be the immediate child of preparation")


def _run_paths(
    root: Path, run_manifest_path: Path, run_metadata: dict[str, Any]
) -> tuple[Path, Path]:
    try:
        manifest_relative = run_manifest_path.resolve().relative_to(root)
    except ValueError as error:
        raise ValueError("run manifest must be inside the Git repository") from error
    run_files = _validated_run_files(run_metadata)
    records_relative = manifest_relative.parent / Path(
        run_files["records_template"]["path"]
    )
    return manifest_relative, records_relative


def _prepared_file_path(
    run_manifest_path: Path, run_metadata: dict[str, Any], name: str
) -> Path:
    run_root = run_manifest_path.resolve().parent
    run_files = _validated_run_files(run_metadata)
    path = (run_root / run_files[name]["path"]).resolve()
    if os.path.commonpath((str(run_root), str(path))) != str(run_root):
        raise ValueError("prepared run file escapes the run directory")
    if not path.is_file():
        raise ValueError(f"prepared run file does not exist: {name}")
    return path


def validate_completed_records_for_release(
    records_snapshot: bytes,
    run_metadata: dict[str, Any],
) -> tuple[list[dict[str, Any]], datetime, datetime]:
    records = _parse_manual_records_snapshot(records_snapshot)
    if not records:
        raise ValueError("completed records commit must contain records")
    normalized = [_normalize_record(record) for record in records]
    assigned = run_metadata.get("assigned_case_ids")
    completed = [record["case_id"] for record in normalized]
    if completed != assigned:
        raise ValueError(
            "completed records must preserve the complete assigned case order"
        )
    provenance = run_metadata.get("run_provenance", {})
    if {record["reviewer_code"] for record in normalized} != {
        provenance.get("reviewer_code")
    }:
        raise ValueError("completed records reviewer code does not match the run")
    if {record["run_type"] for record in normalized} != {run_metadata.get("run_type")}:
        raise ValueError("completed records run type does not match the run")
    prepared_at = _utc_timestamp(
        provenance.get("prepared_at_utc"), "run_provenance.prepared_at_utc"
    )
    starts = [record["_started_at"] for record in normalized]
    ends = [
        _utc_timestamp(record["ended_at_utc"], "ended_at_utc") for record in normalized
    ]
    if min(starts) < prepared_at:
        raise ValueError("held-out run must be prepared before handling starts")
    return normalized, min(starts), max(ends)


def load_records_freeze_anchor(
    project_root: Path,
    run_manifest_path: Path,
    run_metadata: dict[str, Any],
    *,
    preparation_ref: str,
    records_ref: str,
) -> dict[str, Any]:
    root = project_root.resolve()
    current_manifest = run_manifest_path.read_bytes()
    prep_sha = _resolve_commit(root, preparation_ref, "preparation commit")
    records_sha = _resolve_commit(root, records_ref, "records commit")
    _require_ancestor(root, prep_sha, records_sha)
    _require_immediate_records_commit(root, prep_sha, records_sha)
    manifest_relative, records_relative = _run_paths(
        root, run_manifest_path, run_metadata
    )
    run_files = _validated_run_files(run_metadata)
    case_pack_relative = manifest_relative.parent / Path(run_files["case_pack"]["path"])
    policy_copy_relative = manifest_relative.parent / Path(
        run_files["policy_copy"]["path"]
    )
    operator_guide_copy_relative = manifest_relative.parent / Path(
        run_files["operator_guide_copy"]["path"]
    )
    anchored_files = {
        manifest_relative: run_manifest_path.read_bytes(),
        case_pack_relative: _prepared_file_path(
            run_manifest_path, run_metadata, "case_pack"
        ).read_bytes(),
        policy_copy_relative: _prepared_file_path(
            run_manifest_path, run_metadata, "policy_copy"
        ).read_bytes(),
        operator_guide_copy_relative: _prepared_file_path(
            run_manifest_path, run_metadata, "operator_guide_copy"
        ).read_bytes(),
        Path(HELDOUT_PUBLIC_PATH) / "cases.jsonl": (
            root / HELDOUT_PUBLIC_PATH / "cases.jsonl"
        ).read_bytes(),
        Path(HELDOUT_PUBLIC_PATH) / "manifest.json": (
            root / HELDOUT_PUBLIC_PATH / "manifest.json"
        ).read_bytes(),
        Path(HELDOUT_PUBLIC_PATH) / OPERATOR_GUIDE_FILE: (
            root / HELDOUT_PUBLIC_PATH / OPERATOR_GUIDE_FILE
        ).read_bytes(),
        Path("data/stage1/policy.json"): (
            root / "data" / "stage1" / "policy.json"
        ).read_bytes(),
        Path(HELDOUT_INSTRUCTIONS_PUBLIC_PATH): (
            root / HELDOUT_INSTRUCTIONS_PUBLIC_PATH
        ).read_bytes(),
    }
    for commit_sha, label in (
        (prep_sha, "preparation commit"),
        (records_sha, "records commit"),
    ):
        for relative, expected_snapshot in anchored_files.items():
            if _git_blob(root, commit_sha, relative, f"{label} {relative}") != (
                expected_snapshot
            ):
                raise ValueError(
                    f"{label} does not contain the frozen {relative.as_posix()} bytes"
                )
        _require_git_path_absent(
            root,
            commit_sha,
            Path(HELDOUT_ORACLE_RELEASE_PATH),
            f"released oracle in {label}",
        )
        _require_git_path_absent(
            root,
            commit_sha,
            Path(HELDOUT_ORACLE_RELEASE_MANIFEST_PATH),
            f"oracle release manifest in {label}",
        )
    changed_paths = [
        path.decode("utf-8")
        for path in _git_output(
            root,
            ["diff", "--name-only", "-z", prep_sha, records_sha],
            "preparation-to-records tree delta",
        ).split(b"\0")
        if path
    ]
    if changed_paths != [records_relative.as_posix()]:
        raise ValueError(
            "records commit may change only the prepared manual records file"
        )
    prep_manifest = _git_blob(
        root, prep_sha, manifest_relative, "prepared run manifest"
    )
    records_manifest = _git_blob(
        root, records_sha, manifest_relative, "records run manifest"
    )
    if prep_manifest != current_manifest or records_manifest != current_manifest:
        raise ValueError("frozen run manifest must be identical at both anchors")
    blank_records = _git_blob(
        root, prep_sha, records_relative, "blank records template"
    )
    completed_records = _git_blob(
        root, records_sha, records_relative, "completed records"
    )
    if _sha256_bytes(blank_records) != run_files["records_template"]["sha256"]:
        raise ValueError(
            "preparation commit does not contain the pinned blank template"
        )
    if completed_records == blank_records:
        raise ValueError("records commit still contains the blank template")
    _, earliest_start, latest_end = validate_completed_records_for_release(
        completed_records, run_metadata
    )
    prep_time_text, prep_time = _commit_timestamp(root, prep_sha, "preparation commit")
    records_time_text, records_time = _commit_timestamp(
        root, records_sha, "records commit"
    )
    if prep_time > earliest_start:
        raise ValueError("preparation commit must precede handling starts")
    if records_time < latest_end:
        raise ValueError("records commit timestamp must follow handling completion")
    return {
        "preparation_commit_sha": prep_sha,
        "preparation_commit_timestamp_utc": prep_time_text,
        "records_commit_sha": records_sha,
        "records_commit_timestamp_utc": records_time_text,
        "records_sha256": _sha256_bytes(completed_records),
        "run_manifest_sha256": _sha256_bytes(current_manifest),
        "records_commit_time": records_time,
    }


def release_heldout_oracle(
    project_root: Path,
    run_manifest_path: Path,
    private_output: Path,
    *,
    preparation_ref: str,
    records_ref: str,
    released_at: datetime,
) -> dict[str, Any]:
    root = project_root.resolve()
    released_at_text = _utc_text(released_at)
    run_manifest_snapshot = run_manifest_path.read_bytes()
    try:
        run_metadata = json.loads(run_manifest_snapshot.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("held-out run manifest is invalid") from error
    if (
        not isinstance(run_metadata, dict)
        or run_metadata.get("schema_version") != HELDOUT_RUN_MANIFEST_SCHEMA_VERSION
        or run_metadata.get("dataset_role") != HELDOUT_DATASET_ROLE
        or run_metadata.get("oracle_exposure_status")
        != HELDOUT_ORACLE_EXPOSURE_PREPARED
    ):
        raise ValueError("run manifest is not a process-controlled held-out run")

    public = root / HELDOUT_PUBLIC_PATH
    public_manifest_path = public / "manifest.json"
    cases_path = public / "cases.jsonl"
    operator_guide_path = public / OPERATOR_GUIDE_FILE
    policy_path = root / "data" / "stage1" / "policy.json"
    for path, label in (
        (public_manifest_path, "public manifest"),
        (cases_path, "held-out cases"),
        (operator_guide_path, "held-out operator guide"),
        (policy_path, "policy"),
    ):
        if not path.is_file():
            raise ValueError(f"required {label} does not exist")
    public_manifest_bytes = public_manifest_path.read_bytes()
    public_cases_bytes = cases_path.read_bytes()
    public_operator_guide_bytes = operator_guide_path.read_bytes()
    policy_bytes = policy_path.read_bytes()
    public_manifest = json.loads(public_manifest_bytes.decode("utf-8"))
    if (
        public_manifest.get("schema_version") != HELDOUT_PACK_SCHEMA_VERSION
        or public_manifest.get("evaluation_pack_id") != HELDOUT_EVALUATION_PACK_ID
        or public_manifest.get("oracle_release_status") != "answer-file-not-published"
    ):
        raise ValueError("public held-out manifest is not in a releasable state")
    prepared_case_path = _prepared_file_path(
        run_manifest_path, run_metadata, "case_pack"
    )
    prepared_policy_path = _prepared_file_path(
        run_manifest_path, run_metadata, "policy_copy"
    )
    prepared_operator_guide_path = _prepared_file_path(
        run_manifest_path, run_metadata, "operator_guide_copy"
    )
    instructions_path = root / HELDOUT_INSTRUCTIONS_PUBLIC_PATH
    if not instructions_path.is_file():
        raise ValueError("held-out protocol does not exist")
    validate_heldout_run_bindings(
        run_metadata,
        public_manifest,
        public_manifest_bytes=public_manifest_bytes,
        public_cases_bytes=public_cases_bytes,
        public_operator_guide_bytes=public_operator_guide_bytes,
        prepared_case_pack_bytes=prepared_case_path.read_bytes(),
        prepared_operator_guide_bytes=prepared_operator_guide_path.read_bytes(),
        policy_bytes=policy_bytes,
        prepared_policy_bytes=prepared_policy_path.read_bytes(),
        instructions_bytes=instructions_path.read_bytes(),
    )

    generation_material = load_private_generation_material(private_output)
    private_oracles = load_private_oracle(private_output)
    policy = load_stage1_policy(root)
    if public_operator_guide_bytes != _canonical_json_bytes(
        build_operator_guide(policy)
    ):
        raise ValueError("held-out operator guide does not match the frozen policy")
    regenerated_internal_cases = build_heldout_cases(policy, generation_material)
    regenerated_cases = build_operator_case_pack(regenerated_internal_cases)
    regenerated_oracles = [
        build_oracle(case, policy, heldout_release_material=generation_material)
        for case in regenerated_internal_cases
    ]
    public_cases = read_jsonl(cases_path)
    if regenerated_cases != public_cases or regenerated_oracles != private_oracles:
        raise ValueError(
            "private generation material does not reproduce the frozen pack"
        )
    oracle_bytes = _canonical_jsonl_bytes(private_oracles)
    oracle_hash = _sha256_bytes(oracle_bytes)
    pins = _validated_artifact_pins(run_metadata)
    if (
        pins["oracle"]["path"] != HELDOUT_ORACLE_RELEASE_PATH
        or pins["oracle"]["sha256"] != oracle_hash
        or run_metadata.get("oracle", {}).get("sha256_commitment") != oracle_hash
        or public_manifest.get("artifacts_sha256", {}).get("oracle.released.jsonl")
        != oracle_hash
    ):
        raise ValueError("private oracle does not match the frozen public commitment")
    _validate_run_metadata(run_metadata, public_cases, private_oracles)

    anchor = load_records_freeze_anchor(
        root,
        run_manifest_path,
        run_metadata,
        preparation_ref=preparation_ref,
        records_ref=records_ref,
    )
    if released_at < anchor["records_commit_time"]:
        raise ValueError("oracle release must follow the records commit")

    oracle_output = root / HELDOUT_ORACLE_RELEASE_PATH
    release_manifest_output = root / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH
    material_commitment = _sha256_bytes(generation_material.encode("utf-8"))
    release_manifest = {
        "schema_version": ORACLE_RELEASE_SCHEMA_VERSION,
        "release_id": f"{HELDOUT_EVALUATION_PACK_ID}-ORACLE-RELEASE",
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "run_id": run_metadata["run_provenance"]["run_id"],
        "state_transition": {
            "from": HELDOUT_ORACLE_EXPOSURE_PREPARED,
            "to": HELDOUT_ORACLE_EXPOSURE_RELEASED,
            "released_at_utc": released_at_text,
        },
        "records_freeze": {
            key: anchor[key]
            for key in (
                "preparation_commit_sha",
                "preparation_commit_timestamp_utc",
                "records_commit_sha",
                "records_commit_timestamp_utc",
                "records_sha256",
                "run_manifest_sha256",
            )
        },
        "oracle": {
            "path": HELDOUT_ORACLE_RELEASE_PATH,
            "sha256": oracle_hash,
            "version": run_metadata["oracle"]["version"],
        },
        "generator_reproducibility": {
            "name": public_manifest["generator_name"],
            "version": public_manifest["generator_version"],
            "seed_commitment_sha256": material_commitment,
            "seed_released_after_record_freeze": generation_material,
        },
        "claim_boundary": (
            "The release proves ordering and byte identity within the Git evidence "
            "chain. It does not independently prove that the operator avoided "
            "prohibited tools or prior knowledge."
        ),
    }
    release_bytes = _canonical_json_bytes(release_manifest)
    oracle_output.parent.mkdir(parents=True, exist_ok=True)
    _write_release_pair(
        (
            (oracle_output, oracle_bytes),
            (release_manifest_output, release_bytes),
        )
    )
    return release_manifest
