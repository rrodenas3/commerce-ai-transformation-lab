#!/usr/bin/env python3
"""One-way Stage 2 output-freeze, oracle-release, and scoring controller."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import secrets
from pathlib import Path
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_recovery_workflow import evaluate_raw_outputs, validate_pre_oracle_outputs
from scripts.generate_stage2_evaluation import (
    PACK_ID,
    PACK_SCHEMA,
    resolve_clean_git_binding,
    verify_evaluation_pack,
)
from scripts.stage2_contracts import canonical_json_bytes, canonical_sha256, load_canonical_json
from scripts.run_stage2_isolated import BASE_IMAGE, runtime_build_context_inventory


STATE_ORDER = (
    "running",
    "freeze-prepared",
    "output-frozen",
    "eligibility-verified",
    "oracle-released",
    "scored",
)
ZERO = "0" * 64
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCK_MARKER = b"stage2-release-kernel-lock/v1\n"
PRIVATE_ORACLE_MAX_BYTES = 16 * 1024 * 1024
STATE_SCHEMA = "stage2-evaluation-state/v1"
PREPARATION_SCHEMA = "stage2-evaluation-preparation/v1"
OUTPUT_SEAL_SCHEMA = "stage2-output-seal/v1"
IMAGE_RECEIPT_SCHEMA = "stage2-image-build-receipt/v2"


class ReleaseIntegrityError(ValueError):
    """Raised when an irreversible release or evidence boundary is violated."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_file(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except OSError as error:
        raise ReleaseIntegrityError(f"cannot resolve release artifact: {path}") from error
    if resolved.parent != root_resolved and root_resolved not in resolved.parents:
        raise ReleaseIntegrityError(f"release artifact escaped its authority root: {path}")
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ReleaseIntegrityError(f"release artifact is link-like or non-regular: {path}")


def _path_stat_no_follow(path: Path) -> os.stat_result:
    return os.stat(path, follow_symlinks=False)


def _open_private_descriptor(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _descriptor_stat(descriptor: int) -> os.stat_result:
    return os.fstat(descriptor)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    try:
        return os.path.samestat(left, right)
    except (AttributeError, OSError):
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _regular_single_link(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def _read_private_file_once(path: Path, root: Path, *, max_bytes: int) -> bytes:
    """Open once, bind the descriptor to its path, and read only that descriptor."""
    if max_bytes < 0:
        raise ReleaseIntegrityError("private release artifact size bound is invalid")
    try:
        root_metadata = _path_stat_no_follow(root)
        root_resolved = root.resolve(strict=True)
        parent_resolved = path.parent.resolve(strict=True)
        before = _path_stat_no_follow(path)
    except OSError as error:
        raise ReleaseIntegrityError(f"cannot resolve private release artifact: {path}") from error
    if (
        root.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or parent_resolved != root_resolved
        or not _regular_single_link(before)
    ):
        raise ReleaseIntegrityError(f"private release artifact is outside its authority or link-like: {path}")

    descriptor: int | None = None
    try:
        try:
            descriptor = _open_private_descriptor(path)
        except OSError as error:
            raise ReleaseIntegrityError(f"cannot open private release artifact safely: {path}") from error
        opened = _descriptor_stat(descriptor)
        current = _path_stat_no_follow(path)
        if (
            not _regular_single_link(opened)
            or not _regular_single_link(current)
            or not _same_file(before, opened)
            or not _same_file(opened, current)
        ):
            raise ReleaseIntegrityError(f"private release artifact changed during safe open: {path}")
        if opened.st_size < 0 or opened.st_size > max_bytes:
            raise ReleaseIntegrityError(f"private release artifact exceeds its size bound: {path}")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ReleaseIntegrityError(f"private release artifact exceeds its size bound: {path}")
            chunks.append(chunk)

        after = _descriptor_stat(descriptor)
        path_after = _path_stat_no_follow(path)
        if (
            not _regular_single_link(after)
            or not _regular_single_link(path_after)
            or not _same_file(opened, after)
            or not _same_file(opened, path_after)
            or after.st_size != total
            or after.st_size != opened.st_size
            or getattr(after, "st_mtime_ns", None) != getattr(opened, "st_mtime_ns", None)
        ):
            raise ReleaseIntegrityError(f"private release artifact changed during descriptor read: {path}")
        return b"".join(chunks)
    except OSError as error:
        raise ReleaseIntegrityError(f"cannot read private release artifact safely: {path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_once(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1 and path.read_bytes() == payload:
            return
        raise ReleaseIntegrityError(f"write-once release artifact already exists: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    value = load_canonical_json(path.read_bytes())
    if not isinstance(value, dict):
        raise ReleaseIntegrityError(f"release artifact is not an object: {path.name}")
    return value


def _inventory(root: Path, names: Iterable[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for name in sorted(names):
        path = root / name
        _safe_file(path, root)
        payload = path.read_bytes()
        result[name] = {"bytes": len(payload), "sha256": _sha(payload), "type": "regular-file"}
    return result


def _inventory_tree(root: Path) -> dict[str, dict[str, Any]]:
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as error:
        raise ReleaseIntegrityError("output authority root is unavailable") from error
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ReleaseIntegrityError(f"cannot resolve output artifact: {path}") from error
        if path.is_symlink() or (resolved != root_resolved and root_resolved not in resolved.parents):
            raise ReleaseIntegrityError(f"output artifact escapes authority root: {path}")
        if path.is_dir():
            continue
        _safe_file(path, root)
        payload = path.read_bytes()
        result[path.relative_to(root).as_posix()] = {
            "bytes": len(payload),
            "sha256": _sha(payload),
            "type": "regular-file",
        }
    if not result:
        raise ReleaseIntegrityError("output artifact inventory is empty")
    return result


def _snapshot_validated_bytes(
    source_root: Path,
    inventory: Mapping[str, Mapping[str, Any]],
    destination_root: Path,
) -> None:
    destination_root.mkdir(parents=True, exist_ok=False)
    for relative, metadata in sorted(inventory.items()):
        source = source_root / relative
        _safe_file(source, source_root)
        payload = source.read_bytes()
        if len(payload) != metadata.get("bytes") or _sha(payload) != metadata.get("sha256"):
            raise ReleaseIntegrityError(f"sealed snapshot source changed: {relative}")
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


def validate_outer_attestation(
    attestation: Mapping[str, Any],
    preparation: Mapping[str, Any],
    output_seal: Mapping[str, Any],
) -> None:
    exact = {
        "writer": "outer-launcher-after-container-exit",
        "canonical_run": True,
        "network": "none",
        "capabilities": "ALL_DROPPED",
        "no_new_privileges": True,
        "root_filesystem": "read-only",
        "container_user": "65532:65532",
        "pids_limit": 1,
        "memory_limit_bytes": 268435456,
        "cpu_limit": "0.5",
        "evaluated_environment": ["PYTHONPATH=/app", "TMPDIR=/work"],
        "repository_mount": "absent",
        "home_mount": "absent",
        "home_environment_probe": "denied",
        "private_mount": "absent",
        "oracle_mount": "absent",
        "subprocess_probe": "denied",
        "socket_probe": "denied",
        "parent_path_probe": "denied",
        "absolute_path_probe": "denied",
        "home_path_probe": "denied",
        "git_object_probe": "denied",
        "outer_materialized_output": True,
        "workspace_mount": "isolated-tmpfs-rw",
        "wall_time_limit_seconds": 180,
        "seccomp_denials": ["clone", "clone3", "execveat", "fork", "socket", "socketpair", "vfork"],
        "seccomp_profile_identity_verified_through_create": True,
        "seccomp_profile_source": "outer-materialized-from-frozen-committed-bytes",
    }
    for key, expected in exact.items():
        if attestation.get(key) != expected:
            raise ReleaseIntegrityError(f"outer attestation does not prove {key}={expected!r}")
    if attestation.get("schema_version") != "stage2-isolation-attestation/v2":
        raise ReleaseIntegrityError("outer attestation schema is not v2")
    image_id = attestation.get("image_id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ReleaseIntegrityError("outer attestation lacks canonical image ID")
    if attestation.get("base_image_digest") != BASE_IMAGE.split("@", 1)[1]:
        raise ReleaseIntegrityError("outer attestation does not bind the pinned base image digest")
    expected_seccomp = preparation.get("expected_seccomp_profile_sha256")
    if (
        not isinstance(expected_seccomp, str)
        or len(expected_seccomp) != 64
        or any(character not in "0123456789abcdef" for character in expected_seccomp)
    ):
        raise ReleaseIntegrityError("preparation does not bind a frozen seccomp profile")
    profile_path = attestation.get("seccomp_profile_path")
    if not isinstance(profile_path, str) or not profile_path:
        raise ReleaseIntegrityError("outer attestation lacks the materialized seccomp profile identity")
    if attestation.get("seccomp_profile_sha256") != expected_seccomp:
        raise ReleaseIntegrityError("outer attestation does not bind the frozen seccomp profile")
    build_input = attestation.get("build_input_sha256")
    if not isinstance(build_input, str) or len(build_input) != 64 or any(char not in "0123456789abcdef" for char in build_input):
        raise ReleaseIntegrityError("outer attestation lacks the image build-input digest")
    if (
        attestation.get("image_source_commit") != preparation.get("source_commit")
        or attestation.get("image_source_tree") != preparation.get("source_tree")
    ):
        raise ReleaseIntegrityError("outer attestation image differs from the prepared source identity")
    if image_id != preparation.get("expected_image_id"):
        raise ReleaseIntegrityError("outer attestation image differs from the prepared immutable image ID")
    if build_input != preparation.get("expected_build_input_sha256"):
        raise ReleaseIntegrityError("outer attestation build input differs from preparation")
    receipt_material = {
        "base_image_digest": attestation.get("base_image_digest"),
        "build_input_sha256": build_input,
        "image_id": image_id,
        "schema_version": IMAGE_RECEIPT_SCHEMA,
        "seccomp_profile_sha256": expected_seccomp,
        "source_commit": attestation.get("image_source_commit"),
        "source_tree": attestation.get("image_source_tree"),
    }
    receipt_sha256 = canonical_sha256(receipt_material)
    if (
        attestation.get("image_build_receipt_sha256") != receipt_sha256
        or preparation.get("image_build_receipt_sha256") != receipt_sha256
    ):
        raise ReleaseIntegrityError("outer attestation does not open the prepared image build receipt")
    mounts = attestation.get("mounts")
    expected_mount = {
        "access": "read-only",
        "source_identity": preparation.get("input_root_identity"),
        "target": "/input",
    }
    if mounts != [expected_mount]:
        raise ReleaseIntegrityError("outer attestation must prove the sole exact read-only input bind")
    if attestation.get("input_manifest_sha256") != preparation.get("input_manifest_sha256"):
        raise ReleaseIntegrityError("outer attestation does not bind frozen input manifest")
    if attestation.get("completed_output_inventory_sha256") != output_seal.get("artifact_inventory_sha256"):
        raise ReleaseIntegrityError("outer attestation does not bind sealed output inventory")


def _validate_image_build_receipt(
    receipt: Mapping[str, Any], pins: Mapping[str, Any]
) -> dict[str, Any]:
    material = {
        "base_image_digest": BASE_IMAGE.split("@", 1)[1],
        "build_input_sha256": pins.get("runtime_build_input_sha256"),
        "image_id": receipt.get("image_id"),
        "schema_version": IMAGE_RECEIPT_SCHEMA,
        "seccomp_profile_sha256": pins.get("seccomp_profile_sha256"),
        "source_commit": pins.get("source_commit"),
        "source_tree": pins.get("source_tree"),
    }
    image_id = material["image_id"]
    build_input = material["build_input_sha256"]
    seccomp_profile_sha256 = material["seccomp_profile_sha256"]
    if (
        not isinstance(image_id, str)
        or not image_id.startswith("sha256:")
        or len(image_id) != 71
        or any(character not in "0123456789abcdef" for character in image_id[7:])
    ):
        raise ReleaseIntegrityError("image build receipt lacks an immutable image ID")
    if (
        not isinstance(build_input, str)
        or len(build_input) != 64
        or any(character not in "0123456789abcdef" for character in build_input)
    ):
        raise ReleaseIntegrityError("pack does not pin the minimal runtime build context")
    if (
        not isinstance(seccomp_profile_sha256, str)
        or len(seccomp_profile_sha256) != 64
        or any(character not in "0123456789abcdef" for character in seccomp_profile_sha256)
    ):
        raise ReleaseIntegrityError("pack does not pin the committed seccomp profile")
    expected_digest = canonical_sha256(material)
    if receipt.get("receipt_sha256") != expected_digest:
        raise ReleaseIntegrityError("image build receipt does not open against frozen pins")
    for key, value in material.items():
        if receipt.get(key) != value:
            raise ReleaseIntegrityError(f"image build receipt differs at {key}")
    return {**material, "receipt_sha256": expected_digest}


class EvaluationRelease:
    def __init__(
        self,
        pack_root: Path,
        private_root: Path,
        run_root: Path,
        *,
        source_root: Path | None = None,
        test_fixture_mode: bool = False,
    ) -> None:
        self.pack_root = pack_root.resolve()
        self.private_root = private_root.resolve()
        self.run_root = run_root.resolve()
        self.output_root = self.run_root / "output"
        self.source_root = source_root.resolve() if source_root else None
        self.test_fixture_mode = test_fixture_mode
        self.state_dir = self.run_root / "release-states"
        self.journal_path = self.state_dir
        self.attestation_path = self.run_root / "outer" / "isolation-attestation.json"

    def _locked(self):
        class Lock:
            def __init__(inner, path: Path) -> None:
                inner.path = path
                inner.fd: int | None = None

            @staticmethod
            def _publish_complete(path: Path) -> None:
                pending = path.parent / f".release-lock-pending-{os.getpid()}-{secrets.token_hex(8)}"
                descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as stream:
                        stream.write(LOCK_MARKER)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(pending, path)
                    except FileExistsError:
                        pass
                finally:
                    pending.unlink(missing_ok=True)

            @staticmethod
            def _acquire(descriptor: int) -> None:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise ReleaseIntegrityError("release writer authority is already held") from error

            @staticmethod
            def _release(descriptor: int) -> None:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)

            def __enter__(inner):
                self.run_root.mkdir(parents=True, exist_ok=True)
                inner._publish_complete(inner.path)
                if inner.path.is_symlink() or not inner.path.is_file():
                    raise ReleaseIntegrityError("release writer lock is link-like or non-regular")
                try:
                    inner.fd = os.open(inner.path, os.O_RDWR)
                    observed = os.read(inner.fd, len(LOCK_MARKER) + 1)
                except OSError as error:
                    if inner.fd is not None:
                        os.close(inner.fd)
                        inner.fd = None
                    raise ReleaseIntegrityError("release writer authority is already held") from error
                if observed != LOCK_MARKER:
                    os.close(inner.fd)
                    inner.fd = None
                    raise ReleaseIntegrityError("release writer lock marker is invalid; refusing reclamation")
                try:
                    inner._acquire(inner.fd)
                except Exception:
                    os.close(inner.fd)
                    inner.fd = None
                    raise
                return inner

            def __exit__(inner, exc_type, exc, traceback):
                if inner.fd is not None:
                    try:
                        inner._release(inner.fd)
                    finally:
                        os.close(inner.fd)
                        inner.fd = None

        return Lock(self.run_root / ".release-writer.lock")

    def _states(self) -> list[dict[str, Any]]:
        if not self.state_dir.exists():
            return []
        if self.state_dir.is_symlink() or not self.state_dir.is_dir():
            raise ReleaseIntegrityError("release state authority is not a directory")
        paths = sorted(self.state_dir.glob("[0-9][0-9][0-9][0-9]-*.json"))
        rows = []
        previous = ZERO
        terminal = False
        for number, path in enumerate(paths, 1):
            _safe_file(path, self.state_dir)
            value = load_canonical_json(path.read_bytes())
            if not isinstance(value, dict):
                raise ReleaseIntegrityError("release state record is not an object")
            if (
                value.get("pack_id") != PACK_ID
                or value.get("pack_schema") != PACK_SCHEMA
                or value.get("schema_version") != STATE_SCHEMA
            ):
                raise ReleaseIntegrityError("release state does not bind the current pack identity")
            material = {key: item for key, item in value.items() if key != "record_digest"}
            if value.get("sequence") != number or value.get("previous_record_digest") != previous:
                raise ReleaseIntegrityError("release state sequence/digest chain is invalid")
            if value.get("record_digest") != canonical_sha256(material):
                raise ReleaseIntegrityError("release state record digest is invalid")
            state = value.get("state")
            expected_state = STATE_ORDER[number - 1] if number <= len(STATE_ORDER) else None
            if terminal or (state != expected_state and not (number == 4 and state == "invalidated")):
                raise ReleaseIntegrityError("release state skipped or rolled back")
            expected_name = f"{number:04d}-{state}.json"
            if path.name != expected_name:
                raise ReleaseIntegrityError("release state filename/record binding is invalid")
            terminal = state == "invalidated"
            previous = value["record_digest"]
            rows.append(value)
        return rows

    def state(self) -> dict[str, Any]:
        states = self._states()
        return states[-1] if states else {"state": "not-started"}

    def _append_state(self, target: str, bindings: Mapping[str, Any]) -> dict[str, Any]:
        states = self._states()
        expected = STATE_ORDER[len(states)] if len(states) < len(STATE_ORDER) else None
        allowed = {expected} if expected is not None else set()
        if expected == "eligibility-verified":
            allowed.add("invalidated")
        if target not in allowed:
            raise ReleaseIntegrityError(f"release transition must be one of {sorted(allowed)!r}, not {target!r}")
        previous = states[-1]["record_digest"] if states else ZERO
        material = {
            "bindings": dict(bindings),
            "pack_id": PACK_ID,
            "pack_schema": PACK_SCHEMA,
            "previous_record_digest": previous,
            "schema_version": STATE_SCHEMA,
            "sequence": len(states) + 1,
            "state": target,
        }
        record = {**material, "record_digest": canonical_sha256(material)}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        target_path = self.state_dir / f"{len(states) + 1:04d}-{target}.json"
        pending = self.state_dir / f".pending-{len(states) + 1:04d}-{os.getpid()}-{secrets.token_hex(8)}"
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(canonical_json_bytes(record))
                stream.flush(); os.fsync(stream.fileno())
            if target_path.exists():
                raise ReleaseIntegrityError("release state target already exists")
            os.replace(pending, target_path)
            target_path.chmod(0o444)
        finally:
            pending.unlink(missing_ok=True)
        return record

    def preparation(self) -> dict[str, Any]:
        return _read_json(self.run_root / "preparation.json")

    def output_seal(self) -> dict[str, Any]:
        return _read_json(self.run_root / "output-seal.json")

    def _verify_pack_binding(self) -> None:
        preparation = self.preparation()
        if (
            preparation.get("pack_id") != PACK_ID
            or preparation.get("pack_schema") != PACK_SCHEMA
            or preparation.get("schema_version") != PREPARATION_SCHEMA
        ):
            raise ReleaseIntegrityError("preparation does not bind the current pack identity")
        states = self._states()
        if (
            not states
            or states[0].get("state") != "running"
            or states[0].get("bindings", {}).get("preparation_sha256")
            != canonical_sha256(preparation)
        ):
            raise ReleaseIntegrityError("running state does not bind the current preparation")
        manifest_path = self.pack_root / "manifest.json"
        _safe_file(manifest_path, self.pack_root)
        if _sha(manifest_path.read_bytes()) != preparation["input_manifest_sha256"]:
            raise ReleaseIntegrityError("frozen input manifest changed after preparation")
        manifest = _read_json(manifest_path)
        if manifest.get("pack_id") != PACK_ID or manifest.get("schema_version") != PACK_SCHEMA:
            raise ReleaseIntegrityError("manifest does not bind the current pack identity")
        pins = _read_json(self.pack_root / "pins.json")
        if pins.get("pack_id") != PACK_ID or pins.get("pack_schema") != PACK_SCHEMA:
            raise ReleaseIntegrityError("pins do not bind the current pack identity")
        if preparation.get("expected_seccomp_profile_sha256") != pins.get(
            "seccomp_profile_sha256"
        ):
            raise ReleaseIntegrityError("preparation seccomp digest differs from frozen pins")
        expected_names = set(manifest.get("artifact_sha256", {})) | {"manifest.json"}
        actual_names = {path.name for path in self.pack_root.iterdir() if path.is_file() and not path.is_symlink()}
        if actual_names != expected_names or any(path.is_dir() or path.is_symlink() for path in self.pack_root.iterdir()):
            raise ReleaseIntegrityError("frozen input root contains missing, extra, directory, or link-like material")
        inventory = _inventory(self.pack_root, manifest.get("artifact_sha256", {}).keys())
        if canonical_sha256(inventory) != preparation["pack_artifact_inventory_sha256"]:
            raise ReleaseIntegrityError("frozen input pack changed after preparation")

    def _require_current_continuation(
        self, expected_state: str, *, require_seal: bool = False
    ) -> dict[str, Any]:
        state = self.state()
        if state.get("state") != expected_state:
            raise ReleaseIntegrityError(
                f"release continuation requires {expected_state} state"
            )
        self._verify_pack_binding()
        if require_seal:
            self._verify_seal()
        return state

    def prepare(
        self,
        *,
        source_commit: str,
        source_tree: str,
        image_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            if self.state()["state"] != "not-started":
                raise ReleaseIntegrityError("release preparation is write-once")
            manifest_path = self.pack_root / "manifest.json"
            _safe_file(manifest_path, self.pack_root)
            manifest = _read_json(manifest_path)
            canonical = manifest.get("source_binding_status") == "verified-clean-git-export"
            if canonical and self.source_root is None:
                raise ReleaseIntegrityError("canonical preparation requires an explicit clean source checkout")
            verifier_root = self.source_root if canonical else PROJECT_ROOT
            if verifier_root is None:  # narrowed above; retained as a fail-closed type/runtime guard
                raise ReleaseIntegrityError("canonical preparation has no source authority")
            errors = verify_evaluation_pack(verifier_root, self.pack_root, None)
            if errors:
                raise ReleaseIntegrityError("evaluation pack failed preparation: " + "; ".join(errors))
            pins = _read_json(self.pack_root / "pins.json")
            if source_commit != pins.get("source_commit") or source_tree != pins.get("source_tree"):
                raise ReleaseIntegrityError("requested source identity differs from frozen pins")
            if canonical:
                actual = resolve_clean_git_binding(self.source_root)
                for key in ("source_commit", "source_tree", "source_export_sha256"):
                    if actual[key] != pins.get(key):
                        raise ReleaseIntegrityError(f"clean source checkout differs at {key}")
                if canonical_sha256(runtime_build_context_inventory(self.source_root)) != pins.get(
                    "runtime_build_input_sha256"
                ):
                    raise ReleaseIntegrityError("clean source runtime build context differs from frozen pin")
            elif not self.test_fixture_mode:
                raise ReleaseIntegrityError("unverified test fixture cannot enter canonical preparation")
            if image_receipt is None:
                if canonical:
                    raise ReleaseIntegrityError("canonical preparation requires a verified immutable image receipt")
                fixture_material = {
                    "base_image_digest": BASE_IMAGE.split("@", 1)[1],
                    "build_input_sha256": pins.get("runtime_build_input_sha256"),
                    "image_id": "sha256:" + "a" * 64,
                    "schema_version": IMAGE_RECEIPT_SCHEMA,
                    "seccomp_profile_sha256": pins.get("seccomp_profile_sha256"),
                    "source_commit": pins.get("source_commit"),
                    "source_tree": pins.get("source_tree"),
                }
                image_receipt = {
                    **fixture_material,
                    "receipt_sha256": canonical_sha256(fixture_material),
                }
            verified_receipt = _validate_image_build_receipt(image_receipt, pins)
            pack_inventory = _inventory(self.pack_root, manifest["artifact_sha256"].keys())
            preparation = {
                "canonical_eligible": canonical,
                "input_manifest_sha256": _sha((self.pack_root / "manifest.json").read_bytes()),
                "input_root_identity": str(self.pack_root.resolve()),
                "expected_build_input_sha256": verified_receipt["build_input_sha256"],
                "expected_image_id": verified_receipt["image_id"],
                "expected_seccomp_profile_sha256": verified_receipt["seccomp_profile_sha256"],
                "image_build_receipt_sha256": verified_receipt["receipt_sha256"],
                "outer_output_root_identity": str(self.output_root.resolve()),
                "pack_artifact_inventory_sha256": canonical_sha256(pack_inventory),
                "pack_id": PACK_ID,
                "pack_schema": PACK_SCHEMA,
                "schema_version": PREPARATION_SCHEMA,
                "source_commit": source_commit,
                "source_tree": source_tree,
                "test_fixture_mode": self.test_fixture_mode,
            }
            _write_once(self.run_root / "preparation.json", canonical_json_bytes(preparation))
            self.output_root.mkdir(parents=True, exist_ok=True)
            self._append_state("running", {"preparation_sha256": canonical_sha256(preparation)})
            return preparation

    def prepare_freeze(self) -> dict[str, Any]:
        with self._locked():
            self._require_current_continuation("running")
            inventory = _inventory_tree(self.output_root)
            return self._append_state("freeze-prepared", {"candidate_inventory_sha256": canonical_sha256(inventory)})

    def freeze_outputs(self) -> dict[str, Any]:
        with self._locked():
            current_state = self._require_current_continuation("freeze-prepared")
            inventory = _inventory_tree(self.output_root)
            prepared = current_state["bindings"]["candidate_inventory_sha256"]
            if canonical_sha256(inventory) != prepared:
                raise ReleaseIntegrityError("output changed after freeze preparation")
            case_ids = sorted(path.name for path in (self.output_root / "assisted").iterdir() if path.is_dir())
            if len(case_ids) != 36:
                raise ReleaseIntegrityError("output freeze requires exactly 36 assisted raw trees")
            heads = []
            for case_id in case_ids:
                freeze = _read_json(self.output_root / "assisted" / case_id / "freeze.json")
                head = freeze.get("final_ledger_head")
                if not isinstance(head, str) or len(head) != 64:
                    raise ReleaseIntegrityError("assisted freeze record lacks final ledger head")
                heads.append({"case_id": case_id, "final_ledger_head": head})
            seal = {
                "artifact_inventory": inventory,
                "artifact_inventory_sha256": canonical_sha256(inventory),
                "final_ledger_head": canonical_sha256(heads),
                "pack_id": PACK_ID,
                "pack_schema": PACK_SCHEMA,
                "schema_version": OUTPUT_SEAL_SCHEMA,
            }
            _write_once(self.run_root / "output-seal.json", canonical_json_bytes(seal))
            self._append_state("output-frozen", {"output_seal_sha256": canonical_sha256(seal)})
            return seal

    def _verify_seal(self) -> None:
        seal = self.output_seal()
        if (
            seal.get("pack_id") != PACK_ID
            or seal.get("pack_schema") != PACK_SCHEMA
            or seal.get("schema_version") != OUTPUT_SEAL_SCHEMA
        ):
            raise ReleaseIntegrityError("output seal does not bind the current pack identity")
        frozen_states = [item for item in self._states() if item.get("state") == "output-frozen"]
        if (
            len(frozen_states) != 1
            or frozen_states[0].get("bindings", {}).get("output_seal_sha256")
            != canonical_sha256(seal)
        ):
            raise ReleaseIntegrityError("output-frozen state does not bind the current seal")
        actual = _inventory_tree(self.output_root)
        if actual != seal["artifact_inventory"] or canonical_sha256(actual) != seal["artifact_inventory_sha256"]:
            raise ReleaseIntegrityError("sealed output bytes were mutated")

    def verify_materialized(self) -> dict[str, Any]:
        """Re-prove current pack/output/attestation bytes without advancing state."""

        state = self.state()["state"]
        if state not in {"output-frozen", "eligibility-verified", "oracle-released", "scored"}:
            raise ReleaseIntegrityError("materialized verification requires a frozen non-invalidated run")
        self._require_current_continuation(state, require_seal=True)
        preparation = self.preparation()
        if preparation.get("input_root_identity") != str(self.pack_root):
            raise ReleaseIntegrityError("current input root differs from preparation")
        if preparation.get("outer_output_root_identity") != str(self.output_root):
            raise ReleaseIntegrityError("current output root differs from preparation")
        if not self.attestation_path.is_file():
            raise ReleaseIntegrityError("outer-launcher attestation is unavailable")
        _safe_file(self.attestation_path, self.run_root)
        attestation = _read_json(self.attestation_path)
        validate_outer_attestation(attestation, preparation, self.output_seal())
        return {
            "artifact_inventory_sha256": self.output_seal()["artifact_inventory_sha256"],
            "image_id": attestation["image_id"],
            "input_manifest_sha256": preparation["input_manifest_sha256"],
            "status": "verified",
        }

    def verify_eligibility(self) -> dict[str, Any]:
        with self._locked():
            self._require_current_continuation("output-frozen", require_seal=True)
            if not self.attestation_path.is_file():
                raise ReleaseIntegrityError("outer-launcher attestation is unavailable")
            _safe_file(self.attestation_path, self.run_root)
            attestation = _read_json(self.attestation_path)
            preparation = self.preparation()
            if not preparation["canonical_eligible"] and not self.test_fixture_mode:
                raise ReleaseIntegrityError("test fixture cannot become release eligible")
            validate_outer_attestation(attestation, preparation, self.output_seal())
            if (self.run_root / "oracle-release.json").exists():
                raise ReleaseIntegrityError("oracle material appeared before eligibility")
            try:
                raw_validation = validate_pre_oracle_outputs(self.output_root, self.pack_root)
            except Exception as error:
                raise ReleaseIntegrityError("sealed raw artifacts failed independent pre-oracle replay") from error
            raw_failures = raw_validation["critical_control_failures"]
            if raw_failures:
                self._append_state(
                    "invalidated",
                    {
                        "attestation_sha256": canonical_sha256(attestation),
                        "invalidation_reason": "raw-exact-zero-control-failure",
                        "pre_oracle_validation_digest": raw_validation["validation_digest"],
                        "raw_critical_control_failures": raw_failures,
                    },
                )
                raise ReleaseIntegrityError(
                    "raw exact-zero control failure permanently invalidated the release before oracle access"
                )
            return self._append_state(
                "eligibility-verified",
                {
                    "attestation_sha256": canonical_sha256(attestation),
                    "canonical_eligible": preparation["canonical_eligible"],
                    "pre_oracle_validation_digest": raw_validation["validation_digest"],
                    "raw_critical_control_failures": raw_failures,
                },
            )

    def release_oracle(self) -> dict[str, Any]:
        with self._locked():
            self._require_current_continuation("eligibility-verified", require_seal=True)
            oracle_path = self.private_root / "oracle.jsonl"
            nonce_path = self.private_root / "oracle-nonce.bin"
            oracle = _read_private_file_once(
                oracle_path,
                self.private_root,
                max_bytes=PRIVATE_ORACLE_MAX_BYTES,
            )
            nonce = _read_private_file_once(nonce_path, self.private_root, max_bytes=32)
            manifest = _read_json(self.pack_root / "manifest.json")
            if len(nonce) != 32 or _sha(oracle + nonce) != manifest["oracle_commitment_sha256"]:
                raise ReleaseIntegrityError("private oracle material does not open commitment")
            released = self.run_root / "released"
            _write_once(released / "oracle.jsonl", oracle)
            disclosure = {
                "commitment_sha256": manifest["oracle_commitment_sha256"],
                "nonce_hex": nonce.hex(),
                "oracle_artifact": "released/oracle.jsonl",
                "oracle_sha256": _sha(oracle),
                "pack_id": PACK_ID,
                "schema_version": "stage2-oracle-release/v1",
            }
            _write_once(self.run_root / "oracle-release.json", canonical_json_bytes(disclosure))
            self._append_state("oracle-released", {"oracle_release_sha256": canonical_sha256(disclosure)})
            return disclosure

    def score(self) -> dict[str, Any]:
        with self._locked():
            self._require_current_continuation("oracle-released", require_seal=True)
            seal = self.output_seal()
            manifest = _read_json(self.pack_root / "manifest.json")
            pack_inventory = _inventory(self.pack_root, manifest["artifact_sha256"].keys())
            manifest_payload = (self.pack_root / "manifest.json").read_bytes()
            pack_inventory["manifest.json"] = {
                "bytes": len(manifest_payload),
                "sha256": _sha(manifest_payload),
                "type": "regular-file",
            }
            disclosure = _read_json(self.run_root / "oracle-release.json")
            oracle_path = self.run_root / str(disclosure["oracle_artifact"])
            _safe_file(oracle_path, self.run_root)
            oracle_bytes = oracle_path.read_bytes()
            if _sha(oracle_bytes) != disclosure.get("oracle_sha256"):
                raise ReleaseIntegrityError("released oracle bytes changed before scoring")
            oracle = []
            for line in oracle_bytes.splitlines(keepends=True):
                value = load_canonical_json(line)
                if not isinstance(value, dict):
                    raise ReleaseIntegrityError("released oracle contains a non-object record")
                oracle.append(value)
            with tempfile.TemporaryDirectory(prefix="stage2-score-snapshot-") as temporary:
                snapshot = Path(temporary)
                output_snapshot = snapshot / "output"
                pack_snapshot = snapshot / "pack"
                _snapshot_validated_bytes(self.output_root, seal["artifact_inventory"], output_snapshot)
                _snapshot_validated_bytes(self.pack_root, pack_inventory, pack_snapshot)
                report = evaluate_raw_outputs(output_snapshot, pack_snapshot, oracle)
            if self.test_fixture_mode:
                report["release_eligibility"] = "test-fixture-never-canonical"
                report["report_digest"] = canonical_sha256({key: value for key, value in report.items() if key != "report_digest"})
            _write_once(self.run_root / "score.json", canonical_json_bytes(report))
            self._append_state("scored", {"score_sha256": canonical_sha256(report)})
            return report

    def record_regression_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        with self._locked():
            self._require_current_continuation("scored", require_seal=True)
            record = {
                "evidence_class": "regression-only-post-oracle",
                "new_confirmatory_pack_required": True,
                "original_score_sha256": _sha((self.run_root / "score.json").read_bytes()),
                "pack_id": PACK_ID,
                "result": dict(result),
                "schema_version": "stage2-post-oracle-regression/v1",
            }
            path = self.run_root / "regression" / f"regression-{canonical_sha256(record)[:16]}.json"
            _write_once(path, canonical_json_bytes(record))
            return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance a Stage 2 evaluation release one irreversible state.")
    parser.add_argument("command", choices=("prepare", "prepare-freeze", "freeze", "verify-materialized", "verify-eligibility", "release-oracle", "score", "status"))
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--source-tree")
    parser.add_argument("--image-receipt", type=Path)
    args = parser.parse_args(argv)
    release = EvaluationRelease(args.pack_root, args.private_root, args.run_root, source_root=args.source_root)
    if args.command == "prepare":
        if not args.source_commit or not args.source_tree:
            raise SystemExit("prepare requires --source-commit and --source-tree")
        receipt = _read_json(args.image_receipt) if args.image_receipt else None
        result = release.prepare(
            source_commit=args.source_commit,
            source_tree=args.source_tree,
            image_receipt=receipt,
        )
    elif args.command == "prepare-freeze":
        result = release.prepare_freeze()
    elif args.command == "freeze":
        result = release.freeze_outputs()
    elif args.command == "verify-materialized":
        result = release.verify_materialized()
    elif args.command == "verify-eligibility":
        result = release.verify_eligibility()
    elif args.command == "release-oracle":
        result = release.release_oracle()
    elif args.command == "score":
        result = release.score()
    else:
        result = release.state()
    print(canonical_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
