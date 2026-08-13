"""Public-only source locking, seal verification, and raw-score replay for U7."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from scripts.evaluate_recovery_workflow import EvaluationIntegrityError, evaluate_raw_outputs
from scripts.stage2_contracts import canonical_json_bytes, canonical_sha256, load_canonical_json
from scripts.stage2_decision_contracts import DecisionPackError, validate_assumptions


PACK_ID = "S2-EVALUATION-20260812-V6"
PACK_SCHEMA = "stage2-confirmatory-pack/v6"
RUN_ID = "S2-CF-RUN-0005"
SOURCE_LOCK_PATH = Path("data/stage2/decision-source-lock.json")
ASSUMPTIONS_PATH = Path("data/stage2/economics/assumptions.json")
PACK_ROOT = Path("data/stage2/evaluation/v6")
PACK_MANIFEST_PATH = PACK_ROOT / "manifest.json"
RUN_DIRECTORY = Path(f"data/stage2/runs/{RUN_ID}")
SCORE_PATH = RUN_DIRECTORY / "score.json"
OUTPUT_SEAL_PATH = RUN_DIRECTORY / "output-seal.json"
CONTRACT_PATH = Path("data/stage2/evaluation-contract.json")

STATE_SEQUENCE = (
    ("0001-running.json", "running"),
    ("0002-freeze-prepared.json", "freeze-prepared"),
    ("0003-output-frozen.json", "output-frozen"),
    ("0004-eligibility-verified.json", "eligibility-verified"),
    ("0005-oracle-released.json", "oracle-released"),
    ("0006-scored.json", "scored"),
)

LOCK_FIELDS = {
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
}


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_regular(path: Path, *, maximum_bytes: int = 16_000_000) -> bytes:
    """Read one bounded, sole-linked regular file from the opened descriptor."""

    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise DecisionPackError(f"required public source is missing: {path}") from error
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise DecisionPackError(f"required public source is not a regular file: {path}")
    if before.st_nlink != 1:
        raise DecisionPackError(f"required public source must have one hard link: {path}")
    if before.st_size > maximum_bytes:
        raise DecisionPackError(f"required public source exceeds size bound: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise DecisionPackError(f"opened public source is unsafe: {path}")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise DecisionPackError(f"public source changed while opening: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise DecisionPackError(f"required public source exceeds size bound: {path}")
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
            raise DecisionPackError(f"public source changed while reading: {path}")
        return payload
    finally:
        os.close(descriptor)


def load_public_json(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = read_regular(path)
    try:
        value = load_canonical_json(payload)
    except Exception as error:
        raise DecisionPackError(f"invalid canonical public JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise DecisionPackError(f"public JSON must be an object: {path}")
    return value, payload


def _safe_relative(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or "\\" in relative or str(path) != relative:
        raise DecisionPackError(f"unsafe sealed inventory path: {relative}")
    return path


def _verified_inventory_bytes(
    root: Path,
    inventory: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, bytes]:
    expected = set(inventory)
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise DecisionPackError(f"{label} contains a symlink: {relative}")
        if path.is_file():
            actual.add(relative)
        elif not path.is_dir():
            raise DecisionPackError(f"{label} contains a non-regular entry: {relative}")
    if actual != expected:
        raise DecisionPackError(
            f"{label} inventory differs; missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    verified: dict[str, bytes] = {}
    for relative in sorted(expected):
        _safe_relative(relative)
        record = inventory[relative]
        if not isinstance(record, Mapping) or set(record) != {"bytes", "sha256", "type"}:
            raise DecisionPackError(f"{label} inventory record is malformed: {relative}")
        if record["type"] != "regular-file":
            raise DecisionPackError(f"{label} inventory type is not regular-file: {relative}")
        payload = read_regular(root / Path(*PurePosixPath(relative).parts))
        if len(payload) != record["bytes"] or sha256(payload) != record["sha256"]:
            raise DecisionPackError(f"{label} sealed output differs: {relative}")
        verified[relative] = payload
    return verified


def _parse_jsonl(payload: bytes, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(payload.splitlines(keepends=True), start=1):
        try:
            value = load_canonical_json(line)
        except Exception as error:
            raise DecisionPackError(f"{label} line {index} is not canonical") from error
        if not isinstance(value, dict):
            raise DecisionPackError(f"{label} line {index} must be an object")
        rows.append(value)
    return rows


def _copy_verified(files: Mapping[str, bytes], destination: Path) -> None:
    for relative, payload in files.items():
        target = destination / Path(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def verify_and_replay_public_source(project_root: Path) -> dict[str, Any]:
    """Validate the manual lock, materialized seal, and independently replay score."""

    project_root = Path(project_root).resolve()
    lock, lock_bytes = load_public_json(project_root / SOURCE_LOCK_PATH)
    if set(lock) != LOCK_FIELDS:
        raise DecisionPackError("manual decision source lock fields mismatch")
    if lock.get("schema_version") != "stage2-decision-source-lock/v1":
        raise DecisionPackError("manual decision source lock schema is unsupported")
    if lock.get("status") != "manually-authored-canonical-public-v6-u7-input-lock":
        raise DecisionPackError("manual decision source lock status is invalid")
    if lock.get("pack_id") != PACK_ID or lock.get("pack_schema") != PACK_SCHEMA:
        raise DecisionPackError("manual decision source lock is not bound to V6")
    if lock.get("run_id") != RUN_ID:
        raise DecisionPackError("manual decision source lock is not bound to the scored V6 run")

    # Assumption drift is checked before opening or replaying the large sealed
    # run, and therefore before any caller can reach an output write loop.
    assumptions, assumption_bytes = load_public_json(project_root / ASSUMPTIONS_PATH)
    validate_assumptions(assumptions)
    if assumptions.get("version") == lock["assumptions_version"] and sha256(assumption_bytes) != lock["assumptions_sha256"]:
        raise DecisionPackError("assumptions changed without a new version")
    if assumptions.get("version") != lock["assumptions_version"]:
        raise DecisionPackError("assumptions version differs from manual source lock")

    contract, contract_bytes = load_public_json(project_root / CONTRACT_PATH)
    if sha256(contract_bytes) != lock["evaluation_contract_sha256"]:
        raise DecisionPackError("evaluation contract differs from manual source lock")

    manifest, manifest_bytes = load_public_json(project_root / PACK_MANIFEST_PATH)
    if sha256(manifest_bytes) != lock["public_pack_manifest_sha256"]:
        raise DecisionPackError("public pack manifest differs from manual source lock")
    if manifest.get("pack_id") != PACK_ID or manifest.get("schema_version") != PACK_SCHEMA:
        raise DecisionPackError("decision pack requires the canonical public V6 pack")
    if manifest.get("case_count") != 36 or manifest.get("provider_attempt_count") != 36:
        raise DecisionPackError("V6 manifest denominators must remain 36 cases and 36 attempts")

    pack_inventory = manifest.get("artifact_sha256")
    if not isinstance(pack_inventory, Mapping):
        raise DecisionPackError("public V6 pack inventory is missing")
    pack_files: dict[str, bytes] = {}
    expected_pack = set(pack_inventory) | {"manifest.json"}
    actual_pack = {path.name for path in (project_root / PACK_ROOT).iterdir() if path.is_file()}
    if actual_pack != expected_pack:
        raise DecisionPackError("public V6 pack file set differs from manifest")
    for relative, digest in sorted(pack_inventory.items()):
        _safe_relative(str(relative))
        payload = read_regular(project_root / PACK_ROOT / str(relative))
        if sha256(payload) != digest:
            raise DecisionPackError(f"public V6 pack artifact differs: {relative}")
        pack_files[str(relative)] = payload
    pack_files["manifest.json"] = manifest_bytes

    previous = "0" * 64
    states: list[dict[str, Any]] = []
    state_bytes: list[bytes] = []
    for sequence, (filename, expected_state) in enumerate(STATE_SEQUENCE, start=1):
        record, payload = load_public_json(project_root / RUN_DIRECTORY / "release-states" / filename)
        if record.get("pack_id") != PACK_ID or record.get("pack_schema") != PACK_SCHEMA:
            raise DecisionPackError(f"release state is not bound to V6: {filename}")
        if record.get("state") != expected_state or record.get("sequence") != sequence:
            raise DecisionPackError(f"release state sequence mismatch: {filename}")
        if record.get("previous_record_digest") != previous:
            raise DecisionPackError(f"release state chain predecessor mismatch: {filename}")
        recorded = record.get("record_digest")
        material = {key: value for key, value in record.items() if key != "record_digest"}
        if recorded != canonical_sha256(material):
            raise DecisionPackError(f"release state digest mismatch: {filename}")
        previous = str(recorded)
        states.append(record)
        state_bytes.append(payload)
    if previous != lock["scored_release_head"]:
        raise DecisionPackError("scored release head differs from manual source lock")

    seal, seal_bytes = load_public_json(project_root / OUTPUT_SEAL_PATH)
    if sha256(seal_bytes) != lock["output_seal_sha256"]:
        raise DecisionPackError("output seal differs from manual source lock")
    if seal.get("pack_id") != PACK_ID or seal.get("pack_schema") != PACK_SCHEMA:
        raise DecisionPackError("output seal is not bound to the canonical V6 pack")
    inventory = seal.get("artifact_inventory")
    if not isinstance(inventory, Mapping) or canonical_sha256(inventory) != seal.get("artifact_inventory_sha256"):
        raise DecisionPackError("sealed output inventory digest is invalid")
    if states[2].get("bindings", {}).get("output_seal_sha256") != sha256(seal_bytes):
        raise DecisionPackError("output-frozen state does not bind the public output seal")
    if states[3].get("bindings", {}).get("canonical_eligible") is not True:
        raise DecisionPackError("V6 release was not canonically eligible")
    if states[3].get("bindings", {}).get("raw_critical_control_failures") != []:
        raise DecisionPackError("V6 eligibility contains critical control failures")
    output_files = _verified_inventory_bytes(
        project_root / RUN_DIRECTORY / "output", inventory, label="sealed output"
    )

    oracle_release, oracle_release_bytes = load_public_json(project_root / RUN_DIRECTORY / "oracle-release.json")
    if states[4].get("bindings", {}).get("oracle_release_sha256") != canonical_sha256(oracle_release):
        raise DecisionPackError("oracle-released state does not bind the public disclosure")
    oracle_relative = str(oracle_release.get("oracle_artifact", ""))
    _safe_relative(oracle_relative)
    oracle_bytes = read_regular(project_root / RUN_DIRECTORY / Path(*PurePosixPath(oracle_relative).parts))
    if sha256(oracle_bytes) != oracle_release.get("oracle_sha256"):
        raise DecisionPackError("released public oracle differs from disclosure")
    oracle = _parse_jsonl(oracle_bytes, "released public oracle")

    with tempfile.TemporaryDirectory(prefix="stage2-u7-public-replay-") as temporary:
        snapshot = Path(temporary)
        output_snapshot = snapshot / "output"
        pack_snapshot = snapshot / "pack"
        _copy_verified(output_files, output_snapshot)
        _copy_verified(pack_files, pack_snapshot)
        try:
            recomputed = evaluate_raw_outputs(output_snapshot, pack_snapshot, oracle)
        except EvaluationIntegrityError as error:
            raise DecisionPackError(f"sealed raw-byte replay failed: {error}") from error
    recomputed_bytes = canonical_json_bytes(recomputed)

    # The committed score is deliberately opened only after the independent
    # raw-byte replay has produced its own canonical result.
    score, score_bytes = load_public_json(project_root / SCORE_PATH)
    if sha256(score_bytes) != lock["score_sha256"]:
        raise DecisionPackError("score differs from manual source lock")
    if states[-1].get("bindings", {}).get("score_sha256") != sha256(score_bytes):
        raise DecisionPackError("scored release state does not bind score.json")
    if recomputed_bytes != score_bytes:
        raise DecisionPackError("recomputed score differs from committed canonical score")

    return {
        "assumptions": assumptions,
        "assumptions_bytes": assumption_bytes,
        "contract": contract,
        "contract_bytes": contract_bytes,
        "lock": lock,
        "lock_bytes": lock_bytes,
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "oracle_release_bytes": oracle_release_bytes,
        "output_seal_bytes": seal_bytes,
        "score": score,
        "score_bytes": score_bytes,
        "state_bytes": state_bytes,
        "states": states,
    }
