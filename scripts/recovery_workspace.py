#!/usr/bin/env python3
"""Tamper-evident, single-writer Stage 2 recovery workspace.

The JSONL ledger is the workflow truth.  Checkpoints are derived caches, every
acknowledged event is a complete fsync'd canonical line, and only an incomplete
tail may be quarantined during explicit recovery.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from scripts.recovery_state import (
    IllegalTransitionError,
    NON_TRANSITION_EVENT_TYPES,
    WorkflowState,
    apply_event_state,
    revision_target,
    transition_state,
)
from scripts.stage2_contracts import (
    ContractValidationError,
    canonical_json_bytes,
    load_canonical_json,
    validate_neutral_record,
)
from scripts.stage2_facts import SourceValidationError, validate_source_batch


WORKSPACE_VERSION = "recovery-workspace/1.0.0"
RUN_MANIFEST_SCHEMA = "stage2-run-manifest/v1"
CHECKPOINT_SCHEMA = "stage2-workflow-checkpoint/v1"
COMMIT_MARKER_SCHEMA = "stage2-workflow-commit/v1"
FREEZE_SCHEMA = "stage2-run-freeze/v1"
RUN_ID_PATTERN = re.compile(r"S2-RUN-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")
HEX64_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
EVENT_RELATIVE = Path("events/workflow.jsonl")
LOCK_RELATIVE = Path(".writer.lock")
FREEZE_RELATIVE = Path("freeze.json")
OBJECT_LINK_KEYS = frozenset(
    {"recommendation_id", "approval_id", "action_id", "verification_id", "communication_id", "closure_id"}
)


class WorkspaceError(RuntimeError):
    """Base error for fail-closed workspace operations."""


class WorkspaceIntegrityError(WorkspaceError):
    """Raised when a path, pin, canonical byte, or ledger invariant fails."""


class StaleWorkspaceError(WorkspaceError):
    """Raised when a caller's durable revision/head precondition is stale."""


class FrozenWorkspaceError(WorkspaceError):
    """Raised when a mutation is requested after the run is frozen."""


def _sha(payload: bytes | Any) -> str:
    if not isinstance(payload, bytes):
        payload = canonical_json_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    # Windows reports different creation-time precision through stat() and
    # fstat() for the same open object, so the stable kernel identity is the
    # volume/device plus file ID (st_ino).  Content is separately hash-pinned.
    return (info.st_dev, info.st_ino)


def _is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


@dataclass(frozen=True)
class RunProjection:
    run_id: str
    case_id: str
    case_revision: int
    source_event_cut_sha256: str
    revision_pin_sha256: str
    source_snapshot_sha256: str
    state: WorkflowState
    sequence: int
    ledger_head_digest: str
    active_object_ids: Mapping[str, str] = field(default_factory=dict)
    invalidated_object_ids: tuple[str, ...] = ()
    frozen: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_object_ids": dict(sorted(self.active_object_ids.items())),
            "case_id": self.case_id,
            "case_revision": self.case_revision,
            "frozen": self.frozen,
            "invalidated_object_ids": list(self.invalidated_object_ids),
            "ledger_head_digest": self.ledger_head_digest,
            "revision_pin_sha256": self.revision_pin_sha256,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "source_event_cut_sha256": self.source_event_cut_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "state": self.state.value,
        }


class SafeFileAuthority:
    """Central path capability with containment and link/identity checks."""

    def __init__(self, root: Path):
        raw = Path(root)
        try:
            root_lstat = os.lstat(raw)
        except OSError as error:
            raise WorkspaceIntegrityError("workspace root is unavailable") from error
        if stat.S_ISLNK(root_lstat.st_mode) or _is_reparse(root_lstat):
            raise WorkspaceIntegrityError("workspace root cannot be a link or reparse point")
        if not stat.S_ISDIR(root_lstat.st_mode):
            raise WorkspaceIntegrityError("workspace root must be a directory")
        self.root = raw.resolve(strict=True)
        self._root_identity = _file_identity(os.stat(self.root, follow_symlinks=False))

    def _assert_root_identity(self) -> None:
        try:
            current = os.stat(self.root, follow_symlinks=False)
        except OSError as error:
            raise WorkspaceIntegrityError("workspace root identity is unavailable") from error
        if _file_identity(current) != self._root_identity:
            raise WorkspaceIntegrityError("workspace root identity changed")

    @staticmethod
    def _normalise(relative: Path | str) -> Path:
        value = Path(relative)
        if value.is_absolute() or not value.parts or any(part in {"", ".", ".."} for part in value.parts):
            raise WorkspaceIntegrityError("requested path is outside the workspace authority")
        return value

    def _ancestor_snapshot(self, relative: Path | str, *, include_final: bool) -> tuple[tuple[Path, tuple[int, int]], ...]:
        self._assert_root_identity()
        value = self._normalise(relative)
        parts = value.parts if include_final else value.parts[:-1]
        snapshots: list[tuple[Path, tuple[int, int]]] = []
        current = self.root
        for part in parts:
            current = current / part
            try:
                info = os.lstat(current)
            except OSError as error:
                raise WorkspaceIntegrityError("workspace path component is unavailable") from error
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise WorkspaceIntegrityError("workspace paths cannot contain links or reparse points")
            if current != self.root / value and not stat.S_ISDIR(info.st_mode):
                raise WorkspaceIntegrityError("workspace ancestor is not a directory")
            snapshots.append((current, _file_identity(info)))
        try:
            resolved_parent = (self.root / value).parent.resolve(strict=True)
            resolved_parent.relative_to(self.root)
        except (OSError, ValueError) as error:
            raise WorkspaceIntegrityError("requested path is outside the workspace authority") from error
        return tuple(snapshots)

    @staticmethod
    def _assert_snapshot(snapshot: tuple[tuple[Path, tuple[int, int]], ...]) -> None:
        for path, identity in snapshot:
            try:
                info = os.lstat(path)
            except OSError as error:
                raise WorkspaceIntegrityError("workspace path identity changed") from error
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or _file_identity(info) != identity:
                raise WorkspaceIntegrityError("workspace path identity changed")

    @staticmethod
    def _assert_regular(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceIntegrityError("workspace artifact must be a regular file")
        if info.st_nlink != 1:
            raise WorkspaceIntegrityError("workspace artifact hard link count must equal one")
        if _is_reparse(info):
            raise WorkspaceIntegrityError("workspace artifact cannot be a reparse point")

    def absolute(self, relative: Path | str) -> Path:
        value = self._normalise(relative)
        self._ancestor_snapshot(value, include_final=False)
        return self.root / value

    def read_bytes(self, relative: Path | str) -> bytes:
        value = self._normalise(relative)
        snapshot = self._ancestor_snapshot(value, include_final=False)
        path = self.root / value
        try:
            before = os.lstat(path)
            self._assert_regular(before)
            with path.open("rb") as stream:
                self._assert_regular(os.fstat(stream.fileno()))
                payload = stream.read()
                after_fd = os.fstat(stream.fileno())
            after = os.lstat(path)
        except OSError as error:
            raise WorkspaceIntegrityError("workspace artifact cannot be read safely") from error
        self._assert_regular(after)
        if _file_identity(before) != _file_identity(after_fd) or _file_identity(before) != _file_identity(after):
            raise WorkspaceIntegrityError("workspace artifact identity changed during read")
        self._assert_snapshot(snapshot)
        return payload

    def write_once(self, relative: Path | str, payload: bytes) -> Path:
        value = self._normalise(relative)
        snapshot = self._ancestor_snapshot(value, include_final=False)
        path = self.root / value
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags, 0o600)
            self._assert_regular(os.fstat(descriptor))
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(descriptor)
            self._assert_regular(os.fstat(descriptor))
        except FileExistsError as error:
            raise WorkspaceIntegrityError("write-once workspace artifact already exists") from error
        except OSError as error:
            raise WorkspaceIntegrityError("workspace artifact cannot be committed safely") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._assert_snapshot(snapshot)
        self._assert_regular(os.lstat(path))
        return path

    def append_durable(self, relative: Path | str, payload: bytes) -> None:
        value = self._normalise(relative)
        snapshot = self._ancestor_snapshot(value, include_final=False)
        path = self.root / value
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            before = os.lstat(path)
            self._assert_regular(before)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            self._assert_regular(opened)
            if _file_identity(before) != _file_identity(opened):
                raise WorkspaceIntegrityError("workspace artifact identity changed before append")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short append")
                view = view[written:]
            os.fsync(descriptor)
            self._assert_regular(os.fstat(descriptor))
        except WorkspaceIntegrityError:
            raise
        except OSError as error:
            raise WorkspaceIntegrityError("workspace event could not be durably appended") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        self._assert_snapshot(snapshot)
        after = os.lstat(path)
        self._assert_regular(after)
        if _file_identity(before) != _file_identity(after):
            raise WorkspaceIntegrityError("workspace artifact identity changed during append")

    def truncate_durable(self, relative: Path | str, length: int) -> None:
        value = self._normalise(relative)
        snapshot = self._ancestor_snapshot(value, include_final=False)
        path = self.root / value
        try:
            before = os.lstat(path)
            self._assert_regular(before)
            with path.open("r+b") as stream:
                opened = os.fstat(stream.fileno())
                self._assert_regular(opened)
                if _file_identity(before) != _file_identity(opened):
                    raise WorkspaceIntegrityError("workspace artifact identity changed before recovery")
                stream.truncate(length)
                stream.flush()
                os.fsync(stream.fileno())
        except WorkspaceIntegrityError:
            raise
        except OSError as error:
            raise WorkspaceIntegrityError("workspace partial tail cannot be quarantined safely") from error
        self._assert_snapshot(snapshot)


class _RunWriter(AbstractContextManager[None]):
    """Portable exclusive per-run process lock."""

    def __init__(self, authority: SafeFileAuthority, timeout_seconds: float = 10.0):
        self.authority = authority
        self.timeout_seconds = timeout_seconds
        self.stream = None

    def __enter__(self) -> None:
        path = self.authority.absolute(LOCK_RELATIVE)
        try:
            before = os.lstat(path)
            SafeFileAuthority._assert_regular(before)
            self.stream = path.open("r+b", buffering=0)
            SafeFileAuthority._assert_regular(os.fstat(self.stream.fileno()))
        except OSError as error:
            raise WorkspaceIntegrityError("run writer authority is unavailable") from error
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise WorkspaceError("timed out waiting for run writer authority") from error
                time.sleep(0.01)
        return None

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.stream is None:
            return None
        try:
            if os.name == "nt":
                import msvcrt

                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None
        return None


def _validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise WorkspaceIntegrityError("run ID is not a canonical synthetic Stage 2 identifier")
    return run_id


class FileRecoveryWorkspace:
    """Concrete local workspace satisfying the application ledger/source port."""

    def __init__(self, run_root: Path):
        self.run_root = Path(run_root)
        self.authority = SafeFileAuthority(self.run_root)
        self._manifest = self._load_manifest()

    @classmethod
    def prepare(cls, runs_root: Path, run_id: str, source_batch: Mapping[str, Any]) -> "FileRecoveryWorkspace":
        run_id = _validate_run_id(run_id)
        try:
            batch = validate_source_batch(source_batch)
        except SourceValidationError as error:
            raise WorkspaceIntegrityError("source batch is not a valid committed Stage 2 cut") from error
        runs_root = Path(runs_root)
        runs_root.mkdir(parents=True, exist_ok=True)
        info = os.lstat(runs_root)
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise WorkspaceIntegrityError("runs root must be a real directory")
        run_root = runs_root / run_id
        try:
            run_root.mkdir()
            for relative in (
                "action-attempts",
                "action-attempts/commits",
                "actions",
                "approvals",
                "checkpoints",
                "communication",
                "commits",
                "events",
                "metrics",
                "recommendations",
                "receipts",
                "source-effects",
                "source-effects/commits",
                "source-effects/commits/INVENTORY",
                "source-effects/commits/OMS",
                "source-effects/commits/PAYMENT",
                "source-effects/commits/WMS",
                "source-snapshots",
                "verification",
                "closures",
            ):
                (run_root / relative).mkdir()
        except FileExistsError as error:
            raise WorkspaceIntegrityError("run workspace already exists") from error
        authority = SafeFileAuthority(run_root)
        snapshot_relative = Path("source-snapshots/revision-0001.json")
        snapshot_bytes = canonical_json_bytes(batch)
        authority.write_once(snapshot_relative, snapshot_bytes)
        payload = batch["payload"]
        manifest = {
            "case_id": payload["case_id"],
            "contains_real_data": False,
            "created_at": payload["committed_at"],
            "event_genesis_digest": payload["ledger_head_digest"],
            "initial_case_revision": payload["case_revision"],
            "initial_revision_pin_sha256": payload["revision_pin_sha256"],
            "initial_source_event_cut_sha256": payload["source_event_cut_sha256"],
            "run_id": run_id,
            "schema_version": RUN_MANIFEST_SCHEMA,
            "source_snapshot_path": snapshot_relative.as_posix(),
            "source_snapshot_sha256": _sha(snapshot_bytes),
            "status": "development-mutable",
            "synthetic": True,
            "workspace_version": WORKSPACE_VERSION,
        }
        authority.write_once("manifest.json", canonical_json_bytes(manifest))
        authority.write_once("action-attempts/journal.jsonl", b"")
        authority.write_once(EVENT_RELATIVE, b"")
        for source_name in ("INVENTORY", "OMS", "PAYMENT", "WMS"):
            authority.write_once(f"source-effects/{source_name}.jsonl", b"")
        authority.write_once(LOCK_RELATIVE, b"0")
        workspace = cls(run_root)
        workspace._append_transition(
            target_state=WorkflowState.RECEIVED,
            event_type="CASE_RECEIVED",
            actor_kind="system",
            actor_id="S2-ACTOR-WORKSPACE",
            expected_case_revision=payload["case_revision"],
            expected_ledger_head=payload["ledger_head_digest"],
            command_id=f"S2-CMD-{run_id[7:]}-PREPARE",
            links={},
            decision_or_effect={"source_batch_record_id": batch["record_id"]},
            action_count=0,
            already_locked=False,
        )
        return workspace

    @classmethod
    def open(cls, runs_root: Path, run_id: str) -> "FileRecoveryWorkspace":
        run_id = _validate_run_id(run_id)
        root = Path(runs_root)
        try:
            root_info = os.lstat(root)
        except OSError as error:
            raise WorkspaceIntegrityError("runs root is unavailable") from error
        if stat.S_ISLNK(root_info.st_mode) or _is_reparse(root_info):
            raise WorkspaceIntegrityError("runs root cannot be a link")
        return cls(root / run_id)

    def _load_manifest(self) -> dict[str, Any]:
        try:
            manifest = load_canonical_json(self.authority.read_bytes("manifest.json"))
        except (ContractValidationError, WorkspaceIntegrityError) as error:
            raise WorkspaceIntegrityError("run manifest is invalid") from error
        required = {
            "case_id",
            "contains_real_data",
            "created_at",
            "event_genesis_digest",
            "initial_case_revision",
            "initial_revision_pin_sha256",
            "initial_source_event_cut_sha256",
            "run_id",
            "schema_version",
            "source_snapshot_path",
            "source_snapshot_sha256",
            "status",
            "synthetic",
            "workspace_version",
        }
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise WorkspaceIntegrityError("run manifest fields are invalid")
        if (
            manifest["schema_version"] != RUN_MANIFEST_SCHEMA
            or manifest["workspace_version"] != WORKSPACE_VERSION
            or manifest["synthetic"] is not True
            or manifest["contains_real_data"] is not False
            or manifest["status"] != "development-mutable"
            or not HEX64_PATTERN.fullmatch(manifest["event_genesis_digest"])
        ):
            raise WorkspaceIntegrityError("run manifest boundary is invalid")
        _validate_run_id(manifest["run_id"])
        return manifest

    @property
    def run_id(self) -> str:
        return self._manifest["run_id"]

    def _load_snapshot_relative(self, relative: str, expected_sha: str | None = None) -> dict[str, Any]:
        payload = self.authority.read_bytes(relative)
        if expected_sha is not None and _sha(payload) != expected_sha:
            raise WorkspaceIntegrityError("source snapshot digest does not match its immutable pin")
        try:
            value = load_canonical_json(payload)
            batch = validate_source_batch(value)
        except (ContractValidationError, SourceValidationError) as error:
            raise WorkspaceIntegrityError("source snapshot is invalid") from error
        return batch

    def load_source_batch(self, revision: int | None = None) -> dict[str, Any]:
        if revision is None:
            revision = self.replay().case_revision
        if revision == self._manifest["initial_case_revision"]:
            relative = self._manifest["source_snapshot_path"]
            expected = self._manifest["source_snapshot_sha256"]
        else:
            relative = f"source-snapshots/revision-{revision:04d}.json"
            expected = None
        batch = self._load_snapshot_relative(relative, expected)
        if batch["payload"]["case_id"] != self._manifest["case_id"] or batch["payload"]["case_revision"] != revision:
            raise WorkspaceIntegrityError("source snapshot crosses case or revision authority")
        return batch

    def _event_material(self, event: Mapping[str, Any]) -> dict[str, Any]:
        material = {key: value for key, value in event.items()}
        material["payload"] = {
            key: value for key, value in event["payload"].items() if key != "event_digest"
        }
        return material

    def _validate_event(self, raw: Any) -> dict[str, Any]:
        try:
            event = validate_neutral_record(raw)
        except ContractValidationError as error:
            raise WorkspaceIntegrityError("workflow event violates the neutral contract") from error
        if event["record_type"] != "workflow_event" or event["schema_version"] != "stage2-workflow-event/v1":
            raise WorkspaceIntegrityError("workflow ledger contains a non-event record")
        required = {
            "action_count",
            "actor_id",
            "actor_kind",
            "case_id",
            "case_revision",
            "command_digest",
            "command_id",
            "committed",
            "component_version",
            "contract_version",
            "decision_or_effect",
            "event_digest",
            "event_type",
            "from_state",
            "input_digest",
            "links",
            "occurred_at",
            "output_digest",
            "previous_case_revision",
            "previous_event_digest",
            "revision_pin_sha256",
            "run_id",
            "sequence",
            "source_event_cut_sha256",
            "source_snapshot_sha256",
            "to_state",
        }
        payload = event["payload"]
        if set(payload) != required:
            raise WorkspaceIntegrityError("workflow event fields are invalid")
        if payload["committed"] is not True:
            raise WorkspaceIntegrityError("complete uncommitted workflow events are forbidden")
        for name in (
            "command_digest",
            "event_digest",
            "input_digest",
            "output_digest",
            "previous_event_digest",
            "revision_pin_sha256",
            "source_event_cut_sha256",
            "source_snapshot_sha256",
        ):
            if not isinstance(payload[name], str) or not HEX64_PATTERN.fullmatch(payload[name]):
                raise WorkspaceIntegrityError("workflow event digest is invalid")
        if payload["event_digest"] != _sha(self._event_material(event)):
            raise WorkspaceIntegrityError("workflow event content digest mismatch")
        if payload["run_id"] != self.run_id or payload["case_id"] != self._manifest["case_id"]:
            raise WorkspaceIntegrityError("workflow event crosses run or case authority")
        if not isinstance(payload["sequence"], int) or payload["sequence"] < 1:
            raise WorkspaceIntegrityError("workflow event sequence is invalid")
        if not isinstance(payload["links"], dict):
            raise WorkspaceIntegrityError("workflow event links are invalid")
        for value in payload["links"].values():
            if not isinstance(value, str) or not value.startswith("S2-"):
                raise WorkspaceIntegrityError("workflow event contains a non-synthetic object link")
            if value.startswith("S2-CASE-") and value != self._manifest["case_id"]:
                raise WorkspaceIntegrityError("workflow event contains a cross-case link")
        return event

    def read_events(self) -> list[dict[str, Any]]:
        payload = self.authority.read_bytes(EVENT_RELATIVE)
        if payload and not payload.endswith(b"\n"):
            raise WorkspaceIntegrityError("workflow ledger has an uncommitted partial tail")
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
            try:
                raw = load_canonical_json(line)
            except ContractValidationError as error:
                raise WorkspaceIntegrityError(
                    f"workflow ledger line {line_number} is not canonical"
                ) from error
            events.append(self._validate_event(raw))
        self._validate_commit_markers(events)
        return events

    def _commit_marker_relative(self, sequence: int) -> Path:
        return Path(f"commits/{sequence:06d}.json")

    def _write_commit_marker(self, event: Mapping[str, Any]) -> None:
        payload = event["payload"]
        marker = {
            "case_revision": payload["case_revision"],
            "event_digest": payload["event_digest"],
            "record_id": event["record_id"],
            "run_id": self.run_id,
            "schema_version": COMMIT_MARKER_SCHEMA,
            "sequence": payload["sequence"],
        }
        self.authority.write_once(
            self._commit_marker_relative(payload["sequence"]),
            canonical_json_bytes(marker),
        )

    def _validate_commit_markers(self, events: list[Mapping[str, Any]]) -> None:
        commit_root = self.run_root / "commits"
        paths = sorted(commit_root.glob("*.json"))
        if len(paths) != len(events):
            raise WorkspaceIntegrityError("workflow ledger truncation or uncommitted event detected")
        for sequence, (event, path) in enumerate(zip(events, paths), 1):
            expected_relative = self._commit_marker_relative(sequence)
            if path.relative_to(self.run_root) != expected_relative:
                raise WorkspaceIntegrityError("workflow commit marker sequence is invalid")
            try:
                marker = load_canonical_json(self.authority.read_bytes(expected_relative))
            except ContractValidationError as error:
                raise WorkspaceIntegrityError("workflow commit marker is not canonical") from error
            payload = event["payload"]
            expected = {
                "case_revision": payload["case_revision"],
                "event_digest": payload["event_digest"],
                "record_id": event["record_id"],
                "run_id": self.run_id,
                "schema_version": COMMIT_MARKER_SCHEMA,
                "sequence": sequence,
            }
            if marker != expected:
                raise WorkspaceIntegrityError("workflow commit marker does not bind the event")

    def _project(self, events: list[Mapping[str, Any]]) -> RunProjection:
        initial = self.load_source_batch(self._manifest["initial_case_revision"])
        case_revision = initial["payload"]["case_revision"]
        source_cut = initial["payload"]["source_event_cut_sha256"]
        revision_pin = initial["payload"]["revision_pin_sha256"]
        snapshot_sha = self._manifest["source_snapshot_sha256"]
        previous_digest = self._manifest["event_genesis_digest"]
        state: WorkflowState | None = None
        active: dict[str, str] = {}
        invalidated: list[str] = []
        command_ids: set[str] = set()
        for expected_sequence, event in enumerate(events, 1):
            payload = event["payload"]
            if payload["sequence"] != expected_sequence:
                raise WorkspaceIntegrityError("workflow event sequence deletion, insertion, or reorder detected")
            if payload["previous_event_digest"] != previous_digest:
                raise WorkspaceIntegrityError("workflow event previous digest mismatch")
            if payload["command_id"] in command_ids:
                raise WorkspaceIntegrityError("workflow event command ID is duplicated")
            command_ids.add(payload["command_id"])
            if payload["previous_case_revision"] != case_revision:
                raise WorkspaceIntegrityError("workflow event previous case revision mismatch")
            if payload["from_state"] != (state.value if state else None):
                raise WorkspaceIntegrityError("workflow event predecessor state mismatch")
            if payload["event_type"] in {
                "SOURCE_REVISION_CHANGED",
                "CASE_REOPENED_WITH_SOURCE_REVISION",
            }:
                if payload["case_revision"] != case_revision + 1:
                    raise WorkspaceIntegrityError("source revision must increase by exactly one")
                batch = self.load_source_batch(payload["case_revision"])
                new_bytes = canonical_json_bytes(batch)
                if _sha(new_bytes) != payload["source_snapshot_sha256"]:
                    raise WorkspaceIntegrityError("source revision snapshot digest changed")
                if batch["payload"]["ledger_head_digest"] != previous_digest:
                    raise WorkspaceIntegrityError("source revision pin is not bound to the prior ledger head")
                case_revision = payload["case_revision"]
                source_cut = batch["payload"]["source_event_cut_sha256"]
                revision_pin = batch["payload"]["revision_pin_sha256"]
                snapshot_sha = payload["source_snapshot_sha256"]
                if active:
                    expected_invalidated = sorted(active.values())
                    if payload["decision_or_effect"].get("invalidated_object_ids") != expected_invalidated:
                        raise WorkspaceIntegrityError("source revision invalidation inventory is incomplete")
                    invalidated.extend(expected_invalidated)
                    active.clear()
            elif payload["case_revision"] != case_revision:
                raise WorkspaceIntegrityError("workflow event uses a stale or future case revision")
            if (
                payload["source_event_cut_sha256"] != source_cut
                or payload["revision_pin_sha256"] != revision_pin
                or payload["source_snapshot_sha256"] != snapshot_sha
            ):
                raise WorkspaceIntegrityError("workflow event source/revision pin changed")
            try:
                state = apply_event_state(state, payload)
            except IllegalTransitionError as error:
                raise WorkspaceIntegrityError("workflow ledger contains an illegal transition") from error
            for key, value in payload["links"].items():
                if key in OBJECT_LINK_KEYS:
                    active[key] = value
            previous_digest = payload["event_digest"]
        if state is None:
            raise WorkspaceIntegrityError("workflow ledger has no received event")
        return RunProjection(
            run_id=self.run_id,
            case_id=self._manifest["case_id"],
            case_revision=case_revision,
            source_event_cut_sha256=source_cut,
            revision_pin_sha256=revision_pin,
            source_snapshot_sha256=snapshot_sha,
            state=state,
            sequence=len(events),
            ledger_head_digest=previous_digest,
            active_object_ids=dict(sorted(active.items())),
            invalidated_object_ids=tuple(invalidated),
            frozen=(self.run_root / FREEZE_RELATIVE).exists(),
        )

    def replay(self) -> RunProjection:
        self._manifest = self._load_manifest()
        return self._project(self.read_events())

    @staticmethod
    def _command_material(
        *,
        target_state: WorkflowState,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
        links: Mapping[str, str],
        decision_or_effect: Mapping[str, Any],
        action_count: int,
    ) -> dict[str, Any]:
        return {
            "action_count": action_count,
            "actor_id": actor_id,
            "actor_kind": actor_kind,
            "command_id": command_id,
            "decision_or_effect": dict(decision_or_effect),
            "event_type": event_type,
            "expected_case_revision": expected_case_revision,
            "expected_ledger_head": expected_ledger_head,
            "links": dict(sorted(links.items())),
            "target_state": target_state.value,
        }

    def _append_transition(
        self,
        *,
        target_state: WorkflowState,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
        links: Mapping[str, str],
        decision_or_effect: Mapping[str, Any],
        action_count: int,
        already_locked: bool,
    ) -> RunProjection:
        command = self._command_material(
            target_state=target_state,
            event_type=event_type,
            actor_kind=actor_kind,
            actor_id=actor_id,
            expected_case_revision=expected_case_revision,
            expected_ledger_head=expected_ledger_head,
            command_id=command_id,
            links=links,
            decision_or_effect=decision_or_effect,
            action_count=action_count,
        )
        command_digest = _sha(command)

        def append_locked() -> RunProjection:
            if (self.run_root / FREEZE_RELATIVE).exists():
                raise FrozenWorkspaceError("frozen runs reject all workflow mutation")
            events = self.read_events()
            current = self._project(events) if events else None
            for event in events:
                payload = event["payload"]
                if payload["command_id"] == command_id:
                    if payload["command_digest"] != command_digest:
                        raise StaleWorkspaceError("command ID was replayed with changed input")
                    assert current is not None
                    return current
            if current is None:
                initial = self.load_source_batch(self._manifest["initial_case_revision"])
                initial_payload = initial["payload"]
                current_case_id = initial_payload["case_id"]
                current_case_revision = initial_payload["case_revision"]
                current_source_cut = initial_payload["source_event_cut_sha256"]
                current_revision_pin = initial_payload["revision_pin_sha256"]
                current_snapshot_sha = self._manifest["source_snapshot_sha256"]
                current_state = None
                current_sequence = 0
                current_head = self._manifest["event_genesis_digest"]
                current_run_id = self.run_id
            else:
                current_case_id = current.case_id
                current_case_revision = current.case_revision
                current_source_cut = current.source_event_cut_sha256
                current_revision_pin = current.revision_pin_sha256
                current_snapshot_sha = current.source_snapshot_sha256
                current_state = current.state
                current_sequence = current.sequence
                current_head = current.ledger_head_digest
                current_run_id = current.run_id
            if current_case_revision != expected_case_revision:
                raise StaleWorkspaceError("stale case revision")
            if current_head != expected_ledger_head:
                raise StaleWorkspaceError("stale ledger head")
            if event_type in NON_TRANSITION_EVENT_TYPES:
                if current_state is None or target_state is not current_state:
                    raise IllegalTransitionError("recovery/cache event cannot change state")
                destination = current_state
            else:
                destination = transition_state(
                    current_state,
                    target_state,
                    event_type=event_type,
                    action_count=action_count,
                )
            sequence = current_sequence + 1
            occurred_at = self.load_source_batch(current_case_revision)["payload"]["committed_at"]
            output = {
                "case_revision": current_case_revision,
                "sequence": sequence,
                "state": destination.value,
            }
            payload = {
                "action_count": action_count,
                "actor_id": actor_id,
                "actor_kind": actor_kind,
                "case_id": current_case_id,
                "case_revision": current_case_revision,
                "command_digest": command_digest,
                "command_id": command_id,
                "committed": True,
                "component_version": WORKSPACE_VERSION,
                "contract_version": "stage2-workflow-event/v1",
                "decision_or_effect": dict(decision_or_effect),
                "event_digest": "",
                "event_type": event_type,
                "from_state": current_state.value if current_state else None,
                "input_digest": command_digest,
                "links": dict(sorted(links.items())),
                "occurred_at": occurred_at,
                "output_digest": _sha(output),
                "previous_case_revision": current_case_revision,
                "previous_event_digest": current_head,
                "revision_pin_sha256": current_revision_pin,
                "run_id": current_run_id,
                "sequence": sequence,
                "source_event_cut_sha256": current_source_cut,
                "source_snapshot_sha256": current_snapshot_sha,
                "to_state": destination.value,
            }
            event = {
                "payload": payload,
                "record_id": f"S2-EVENT-{self.run_id[7:]}-{sequence:06d}",
                "record_type": "workflow_event",
                "schema_version": "stage2-workflow-event/v1",
            }
            payload["event_digest"] = _sha(self._event_material(event))
            encoded = canonical_json_bytes(event)
            self._validate_event(event)
            self.authority.append_durable(EVENT_RELATIVE, encoded)
            self._write_commit_marker(event)
            committed = self._project(events + [event])
            self._write_checkpoint(committed)
            return committed

        if already_locked:
            return append_locked()
        with _RunWriter(self.authority):
            return append_locked()

    def append_transition(
        self,
        *,
        target_state: WorkflowState,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
        links: Mapping[str, str] | None = None,
        decision_or_effect: Mapping[str, Any] | None = None,
        action_count: int = 0,
    ) -> RunProjection:
        return self._append_transition(
            target_state=target_state,
            event_type=event_type,
            actor_kind=actor_kind,
            actor_id=actor_id,
            expected_case_revision=expected_case_revision,
            expected_ledger_head=expected_ledger_head,
            command_id=command_id,
            links=links or {},
            decision_or_effect=decision_or_effect or {},
            action_count=action_count,
            already_locked=False,
        )

    def reserve_action(
        self,
        action: Mapping[str, Any],
        authority: Mapping[str, Any],
        *,
        command_id: str,
    ) -> RunProjection:
        """Consume exact authority and own one idempotency key in one event append."""

        payload = action.get("payload")
        if not isinstance(payload, Mapping) or action.get("record_id") != payload.get("action_id"):
            raise WorkspaceIntegrityError("action reservation input is invalid")
        required = {
            "action_contract_digest",
            "action_payload_digest",
            "authority_reference",
            "case_id",
            "case_revision",
            "eligible_business_key",
            "idempotency_key",
            "ledger_head_digest",
            "operation",
        }
        if not required.issubset(payload):
            raise WorkspaceIntegrityError("action reservation omits an exact binding")
        authority_id = authority.get("authority_id")
        if authority_id != payload["authority_reference"] or authority.get("payload_digest") != payload["action_payload_digest"]:
            raise WorkspaceIntegrityError("authority capability is not bound to the action")
        with _RunWriter(self.authority):
            current = self.replay()
            reservations = [
                event["payload"]
                for event in self.read_events()
                if event["payload"]["event_type"] == "ACTION_AUTHORITY_RESERVED"
            ]
            for reserved in reservations:
                material = reserved["decision_or_effect"]
                same_key = material.get("idempotency_owner") == payload["idempotency_key"]
                same_business = material.get("eligible_business_key") == payload["eligible_business_key"]
                same_action = material.get("action_contract_digest") == payload["action_contract_digest"]
                if same_key:
                    if same_action and material.get("authority_consumed") == authority_id:
                        return current
                    raise WorkspaceIntegrityError("idempotency key is owned by a different payload")
                if same_business:
                    raise WorkspaceIntegrityError("eligible quantity already has a reserved remedy")
                if material.get("authority_consumed") == authority_id:
                    raise WorkspaceIntegrityError("authority capability was already consumed")
            if current.state is not WorkflowState.ACTION_PREPARED:
                raise WorkspaceIntegrityError("action reservation requires ACTION_PREPARED")
            if current.case_id != payload["case_id"] or current.case_revision != payload["case_revision"]:
                raise StaleWorkspaceError("action case revision is stale")
            acceptable_action_heads = {current.ledger_head_digest}
            last_event = self.read_events()[-1]["payload"]
            if last_event["event_type"] in {
                "PARTIAL_TAIL_RECOVERED",
                "UNCOMMITTED_EVENT_RECOVERED",
            }:
                # The recovery trace is system evidence about an uncommitted
                # suffix, not a material change to the exact prepared action.
                acceptable_action_heads.add(last_event["previous_event_digest"])
            if payload["ledger_head_digest"] not in acceptable_action_heads:
                raise StaleWorkspaceError("action ledger-head binding is stale")
            return self._append_transition(
                target_state=WorkflowState.ACTION_RESERVED,
                event_type="ACTION_AUTHORITY_RESERVED",
                actor_kind="deterministic_control",
                actor_id="S2-ACTOR-ACTION-CONTROL",
                expected_case_revision=current.case_revision,
                expected_ledger_head=current.ledger_head_digest,
                command_id=command_id,
                links={
                    "action_id": payload["action_id"],
                    "approval_id": authority_id,
                },
                decision_or_effect={
                    "action_contract_digest": payload["action_contract_digest"],
                    "action_payload_digest": payload["action_payload_digest"],
                    "authority_consumed": authority_id,
                    "authority_route": authority.get("authority_route"),
                    "eligible_business_key": payload["eligible_business_key"],
                    "idempotency_owner": payload["idempotency_key"],
                    "operation": payload["operation"],
                    "reservation_state": "reserved",
                },
                action_count=1,
                already_locked=True,
            )

    def revise_source(
        self,
        source_batch: Mapping[str, Any],
        *,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
    ) -> RunProjection:
        try:
            batch = validate_source_batch(source_batch)
        except SourceValidationError as error:
            raise WorkspaceIntegrityError("revised source batch is invalid") from error
        with _RunWriter(self.authority):
            if (self.run_root / FREEZE_RELATIVE).exists():
                raise FrozenWorkspaceError("frozen runs reject source revision")
            current = self.replay()
            if current.case_revision != expected_case_revision or current.ledger_head_digest != expected_ledger_head:
                raise StaleWorkspaceError("stale source revision or ledger head")
            new_payload = batch["payload"]
            if new_payload["case_id"] != current.case_id or new_payload["case_revision"] != current.case_revision + 1:
                raise WorkspaceIntegrityError("source revision must advance the same case by one")
            if new_payload["ledger_head_digest"] != current.ledger_head_digest:
                raise WorkspaceIntegrityError("source revision pin must bind the current ledger head")
            target = revision_target(current.state)
            snapshot = canonical_json_bytes(batch)
            relative = Path(f"source-snapshots/revision-{new_payload['case_revision']:04d}.json")
            self.authority.write_once(relative, snapshot)
            invalidated = sorted(current.active_object_ids.values())
            command = self._command_material(
                target_state=target,
                event_type=event_type,
                actor_kind=actor_kind,
                actor_id=actor_id,
                expected_case_revision=expected_case_revision,
                expected_ledger_head=expected_ledger_head,
                command_id=command_id,
                links={},
                decision_or_effect={"invalidated_object_ids": invalidated},
                action_count=0,
            )
            command_digest = _sha(command)
            sequence = current.sequence + 1
            payload = {
                "action_count": 0,
                "actor_id": actor_id,
                "actor_kind": actor_kind,
                "case_id": current.case_id,
                "case_revision": new_payload["case_revision"],
                "command_digest": command_digest,
                "command_id": command_id,
                "committed": True,
                "component_version": WORKSPACE_VERSION,
                "contract_version": "stage2-workflow-event/v1",
                "decision_or_effect": {"invalidated_object_ids": invalidated},
                "event_digest": "",
                "event_type": event_type,
                "from_state": current.state.value,
                "input_digest": command_digest,
                "links": {},
                "occurred_at": new_payload["committed_at"],
                "output_digest": _sha({"case_revision": new_payload["case_revision"], "state": target.value}),
                "previous_case_revision": current.case_revision,
                "previous_event_digest": current.ledger_head_digest,
                "revision_pin_sha256": new_payload["revision_pin_sha256"],
                "run_id": current.run_id,
                "sequence": sequence,
                "source_event_cut_sha256": new_payload["source_event_cut_sha256"],
                "source_snapshot_sha256": _sha(snapshot),
                "to_state": target.value,
            }
            event = {
                "payload": payload,
                "record_id": f"S2-EVENT-{self.run_id[7:]}-{sequence:06d}",
                "record_type": "workflow_event",
                "schema_version": "stage2-workflow-event/v1",
            }
            payload["event_digest"] = _sha(self._event_material(event))
            self._validate_event(event)
            self.authority.append_durable(EVENT_RELATIVE, canonical_json_bytes(event))
            self._write_commit_marker(event)
            result = self.replay()
            self._write_checkpoint(result)
            return result

    def reopen_with_revision(
        self,
        source_batch: Mapping[str, Any],
        *,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
    ) -> RunProjection:
        """Commit one new source revision while preserving the prior closure."""

        try:
            batch = validate_source_batch(source_batch)
        except SourceValidationError as error:
            raise WorkspaceIntegrityError("reopen source batch is invalid") from error
        with _RunWriter(self.authority):
            if (self.run_root / FREEZE_RELATIVE).exists():
                raise FrozenWorkspaceError("frozen runs reject reopen")
            current = self.replay()
            if current.state is not WorkflowState.CLOSED:
                raise WorkspaceIntegrityError("reopen requires a preserved closed revision")
            if (
                current.case_revision != expected_case_revision
                or current.ledger_head_digest != expected_ledger_head
            ):
                raise StaleWorkspaceError("stale reopen revision or ledger head")
            new_payload = batch["payload"]
            if (
                new_payload["case_id"] != current.case_id
                or new_payload["case_revision"] != current.case_revision + 1
            ):
                raise WorkspaceIntegrityError("reopen must advance the same case by one revision")
            if new_payload["ledger_head_digest"] != current.ledger_head_digest:
                raise WorkspaceIntegrityError("reopen revision must bind the preserved closure head")
            target = WorkflowState.EVIDENCE_BLOCKED
            snapshot = canonical_json_bytes(batch)
            relative = Path(
                f"source-snapshots/revision-{new_payload['case_revision']:04d}.json"
            )
            self.authority.write_once(relative, snapshot)
            invalidated = sorted(current.active_object_ids.values())
            event_type = "CASE_REOPENED_WITH_SOURCE_REVISION"
            actor_kind = "deterministic_control"
            actor_id = "S2-ACTOR-REOPEN-CONTROL"
            decision = {
                "invalidated_object_ids": invalidated,
                "preserved_closure_id": current.active_object_ids.get("closure_id"),
                "prior_closed_revision": current.case_revision,
            }
            command = self._command_material(
                target_state=target,
                event_type=event_type,
                actor_kind=actor_kind,
                actor_id=actor_id,
                expected_case_revision=expected_case_revision,
                expected_ledger_head=expected_ledger_head,
                command_id=command_id,
                links={},
                decision_or_effect=decision,
                action_count=0,
            )
            command_digest = _sha(command)
            sequence = current.sequence + 1
            payload = {
                "action_count": 0,
                "actor_id": actor_id,
                "actor_kind": actor_kind,
                "case_id": current.case_id,
                "case_revision": new_payload["case_revision"],
                "command_digest": command_digest,
                "command_id": command_id,
                "committed": True,
                "component_version": WORKSPACE_VERSION,
                "contract_version": "stage2-workflow-event/v1",
                "decision_or_effect": decision,
                "event_digest": "",
                "event_type": event_type,
                "from_state": current.state.value,
                "input_digest": command_digest,
                "links": {},
                "occurred_at": new_payload["committed_at"],
                "output_digest": _sha(
                    {"case_revision": new_payload["case_revision"], "state": target.value}
                ),
                "previous_case_revision": current.case_revision,
                "previous_event_digest": current.ledger_head_digest,
                "revision_pin_sha256": new_payload["revision_pin_sha256"],
                "run_id": current.run_id,
                "sequence": sequence,
                "source_event_cut_sha256": new_payload["source_event_cut_sha256"],
                "source_snapshot_sha256": _sha(snapshot),
                "to_state": target.value,
            }
            event = {
                "payload": payload,
                "record_id": f"S2-EVENT-{self.run_id[7:]}-{sequence:06d}",
                "record_type": "workflow_event",
                "schema_version": "stage2-workflow-event/v1",
            }
            payload["event_digest"] = _sha(self._event_material(event))
            self._validate_event(event)
            self.authority.append_durable(EVENT_RELATIVE, canonical_json_bytes(event))
            self._write_commit_marker(event)
            result = self.replay()
            self._write_checkpoint(result)
            return result

    def _write_checkpoint(self, projection: RunProjection) -> None:
        body = projection.to_dict()
        checkpoint = {
            "projection": body,
            "projection_sha256": _sha(body),
            "schema_version": CHECKPOINT_SCHEMA,
        }
        self.authority.write_once(
            f"checkpoints/{projection.sequence:06d}.json",
            canonical_json_bytes(checkpoint),
        )

    def _validate_checkpoint(self, checkpoint: Path, events: list[dict[str, Any]]) -> None:
        try:
            relative = checkpoint.relative_to(self.run_root)
        except ValueError as error:
            raise WorkspaceIntegrityError("checkpoint is outside the run") from error
        try:
            value = load_canonical_json(self.authority.read_bytes(relative))
        except ContractValidationError as error:
            raise WorkspaceIntegrityError("checkpoint is not canonical") from error
        if not isinstance(value, dict) or set(value) != {"projection", "projection_sha256", "schema_version"}:
            raise WorkspaceIntegrityError("checkpoint fields are invalid")
        if value["schema_version"] != CHECKPOINT_SCHEMA or value["projection_sha256"] != _sha(value["projection"]):
            raise WorkspaceIntegrityError("checkpoint digest is invalid")
        sequence = value["projection"].get("sequence")
        if not isinstance(sequence, int) or sequence < 1 or sequence > len(events):
            raise WorkspaceIntegrityError("checkpoint sequence is invalid")
        replayed = self._project(events[:sequence]).to_dict()
        # Freeze is an outer write-once seal, not a workflow-ledger transition;
        # a pre-freeze checkpoint therefore remains a valid cache after sealing.
        replayed["frozen"] = value["projection"].get("frozen")
        if value["projection"] != replayed:
            raise WorkspaceIntegrityError("checkpoint disagrees with durable ledger truth")

    def resume(self, checkpoint: Path | None = None, *, recover_partial_tail: bool = False) -> RunProjection:
        if recover_partial_tail:
            self.recover_partial_tail()
        events = self.read_events()
        if checkpoint is not None:
            self._validate_checkpoint(Path(checkpoint), events)
        else:
            for path in sorted((self.run_root / "checkpoints").glob("*.json")):
                self._validate_checkpoint(path, events)
        return self._project(events)

    def recover_partial_tail(self) -> RunProjection:
        with _RunWriter(self.authority):
            if (self.run_root / FREEZE_RELATIVE).exists():
                raise FrozenWorkspaceError("frozen runs reject tail recovery")
            raw = self.authority.read_bytes(EVENT_RELATIVE)
            if not raw:
                raise WorkspaceIntegrityError("workflow ledger has no committed prefix")
            if raw.endswith(b"\n"):
                lines = raw.splitlines(keepends=True)
                all_events = self._parse_committed_bytes(raw)
                marker_paths = sorted((self.run_root / "commits").glob("*.json"))
                if len(marker_paths) == len(all_events):
                    self._validate_commit_markers(all_events)
                    return self._project(all_events)
                if len(all_events) < 2 or len(marker_paths) != len(all_events) - 1:
                    raise WorkspaceIntegrityError(
                        "workflow recovery permits exactly one final complete unmarked event"
                    )
                committed_events = all_events[:-1]
                self._validate_commit_markers(committed_events)
                # Validate the complete suffix against the committed prefix before
                # removing it; this rejects forged sequence, digest, state, or pins.
                self._project(all_events)
                boundary = sum(len(line) for line in lines[:-1])
                tail = lines[-1]
                recovery_event_type = "UNCOMMITTED_EVENT_RECOVERED"
                recovery_kind = "complete_event_without_commit_marker"
            else:
                boundary = raw.rfind(b"\n") + 1
                committed = raw[:boundary]
                tail = raw[boundary:]
                # A torn suffix is recoverable only after the whole complete prefix
                # is independently shown to have one marker per event.
                committed_events = self._parse_committed_bytes(committed)
                if not committed_events:
                    raise WorkspaceIntegrityError("workflow partial tail has no committed prefix")
                self._validate_commit_markers(committed_events)
                recovery_event_type = "PARTIAL_TAIL_RECOVERED"
                recovery_kind = "non_lf_partial_tail"
            previous = self._project(committed_events)
            quarantine = f"quarantine/partial-tail-{_sha(tail)[:16]}.bin"
            quarantine_dir = self.run_root / "quarantine"
            quarantine_dir.mkdir(exist_ok=True)
            self.authority.write_once(quarantine, tail)
            self.authority.truncate_durable(EVENT_RELATIVE, boundary)
            return self._append_transition(
                target_state=previous.state,
                event_type=recovery_event_type,
                actor_kind="system",
                actor_id="S2-ACTOR-RECOVERY",
                expected_case_revision=previous.case_revision,
                expected_ledger_head=previous.ledger_head_digest,
                command_id=f"S2-CMD-RECOVER-{_sha(tail)[:16].upper()}",
                links={},
                decision_or_effect={
                    "quarantined_tail_sha256": _sha(tail),
                    "recovery_kind": recovery_kind,
                },
                action_count=0,
                already_locked=True,
            )

    def _parse_committed_bytes(self, payload: bytes) -> list[dict[str, Any]]:
        events = []
        for line in payload.splitlines(keepends=True):
            try:
                events.append(self._validate_event(load_canonical_json(line)))
            except ContractValidationError as error:
                raise WorkspaceIntegrityError("committed ledger prefix is invalid") from error
        return events

    def _inventory(self) -> dict[str, dict[str, Any]]:
        inventory: dict[str, dict[str, Any]] = {}
        for path in sorted(self.run_root.rglob("*")):
            relative = path.relative_to(self.run_root)
            if relative in {LOCK_RELATIVE, FREEZE_RELATIVE}:
                continue
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise WorkspaceIntegrityError("workspace inventory contains a link or reparse point")
            if path.is_dir():
                continue
            SafeFileAuthority._assert_regular(info)
            payload = self.authority.read_bytes(relative)
            inventory[relative.as_posix()] = {"byte_length": len(payload), "sha256": _sha(payload)}
        return inventory

    def freeze(self) -> dict[str, Any]:
        with _RunWriter(self.authority):
            if (self.run_root / FREEZE_RELATIVE).exists():
                raise FrozenWorkspaceError("run is already frozen")
            projection = self.replay()
            record = {
                "artifact_inventory": self._inventory(),
                "case_revision": projection.case_revision,
                "final_ledger_head": projection.ledger_head_digest,
                "run_id": self.run_id,
                "schema_version": FREEZE_SCHEMA,
                "self_exclusion": FREEZE_RELATIVE.as_posix(),
            }
            self.authority.write_once(FREEZE_RELATIVE, canonical_json_bytes(record))
            return record

    def verify(self) -> list[str]:
        self._manifest = self._load_manifest()
        events = self.read_events()
        projection = self._project(events)
        for path in sorted((self.run_root / "checkpoints").glob("*.json")):
            self._validate_checkpoint(path, events)
        freeze_path = self.run_root / FREEZE_RELATIVE
        if freeze_path.exists():
            try:
                record = load_canonical_json(self.authority.read_bytes(FREEZE_RELATIVE))
            except ContractValidationError as error:
                raise WorkspaceIntegrityError("freeze record is not canonical") from error
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != FREEZE_SCHEMA
                or record.get("run_id") != self.run_id
                or record.get("final_ledger_head") != projection.ledger_head_digest
                or record.get("case_revision") != projection.case_revision
                or record.get("self_exclusion") != FREEZE_RELATIVE.as_posix()
                or record.get("artifact_inventory") != self._inventory()
            ):
                raise WorkspaceIntegrityError("frozen artifact inventory or ledger pin changed")
        return []
