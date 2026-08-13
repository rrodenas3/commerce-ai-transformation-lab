#!/usr/bin/env python3
"""Outer-isolated Stage 2 execution over real U5 workspaces and Q1-Q8 replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.recovery_actions import build_action_contract
from scripts.recovery_approval import (
    ACTOR_BY_ROLE,
    AuthorityExpectation,
    create_synthetic_authority_event,
)
from scripts.recovery_orchestration import GuardedRecoveryOrchestrator
from scripts.recovery_policy import RecoveryPolicyAdapter
from scripts.recovery_services import (
    ActionReservationCommand,
    RecommendationCommand,
    RecoveryApplicationService,
    Stage2FactsPort,
    TransitionCommand,
)
from scripts.recovery_state import WorkflowState
from scripts.recovery_workspace import FileRecoveryWorkspace
from scripts.stage2_contracts import EVALUATOR_ONLY_FIELDS, canonical_json_bytes, canonical_sha256, load_canonical_json
from scripts.stage2_current_state import replay_current_state
from scripts.stage2_facts import derive_case_facts


BASE_IMAGE = "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
PACK_FILES = frozenset(
    {
        "acquisition-contract.json", "cases.jsonl", "manifest.json", "oracle-commitment.json",
        "pins.json", "provider-attempts.jsonl", "provider-requests.jsonl", "schedules.jsonl", "thresholds.json",
    }
)
RUNTIME_MODULES = (
    "__init__.py", "recovery_actions.py", "recovery_adapters.py", "recovery_approval.py",
    "recovery_communication.py", "recovery_orchestration.py", "recovery_policy.py",
    "recovery_services.py", "recovery_state.py", "recovery_verification.py",
    "recovery_workspace.py", "run_stage2_isolated.py", "stage2_contracts.py",
    "stage2_current_state.py", "stage2_facts.py",
)
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_FILES = 12000
IMAGE_ID_PREFIX = "sha256:"
SECCOMP_CONTEXT_PATH = "seccomp.json"
SECCOMP_REPOSITORY_PATH = "containers/stage2-evaluation/seccomp.json"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_bytes().splitlines(keepends=True):
        value = load_canonical_json(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}: record must be an object")
        rows.append(value)
    return rows


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _reject_mounted_evaluator_metadata(label: str, value: Any) -> None:
    keys = {str(key).casefold() for key in _walk_keys(value)}
    evaluator_fields = {field.casefold() for field in EVALUATOR_ONLY_FIELDS}
    if (
        keys & evaluator_fields
        or keys & {"answer", "scoring_family", "family_id"}
        or any("family" in key for key in keys)
    ):
        raise ValueError(f"{label} contains evaluator/oracle/family metadata")


def _verify_mounted_input(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if any(part in {".git", "private", "artifacts"} for part in (item.casefold() for item in root.parts)):
        raise ValueError("mounted input identity aliases repository or private material")
    actual = {path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()}
    if actual != PACK_FILES or any(path.is_dir() or path.is_symlink() for path in root.iterdir()):
        raise ValueError("mounted input contains missing, extra, directory, or link-like material")
    manifest = load_canonical_json((root / "manifest.json").read_bytes())
    if not isinstance(manifest, dict) or manifest.get("case_count") != 36:
        raise ValueError("mounted input manifest is invalid")
    _reject_mounted_evaluator_metadata("manifest", manifest)
    if (
        manifest.get("coverage_group_count") != 12
        or manifest.get("cases_per_coverage_group") != 3
        or manifest.get("coverage_mapping_status") != "private-until-oracle-release"
    ):
        raise ValueError("mounted input anonymous coverage declaration is invalid")
    expected_manifest_files = set(manifest.get("artifact_sha256", {})) | {"manifest.json"}
    if expected_manifest_files != PACK_FILES:
        raise ValueError("manifest inventory does not bind the exact mounted input")
    for relative, expected in manifest["artifact_sha256"].items():
        path = root / relative
        if _sha(path.read_bytes()) != expected:
            raise ValueError(f"mounted input artifact failed hash validation: {relative}")
        records = _read_jsonl(path) if relative.endswith(".jsonl") else [load_canonical_json(path.read_bytes())]
        _reject_mounted_evaluator_metadata(relative, records)
    cases = _read_jsonl(root / "cases.jsonl")
    schedules = _read_jsonl(root / "schedules.jsonl")
    attempts = _read_jsonl(root / "provider-attempts.jsonl")
    case_ids = [item["payload"]["case_id"] for item in cases]
    if len(case_ids) != 36 or len(set(case_ids)) != 36:
        raise ValueError("mounted case denominator is not 36 unique cases")
    if case_ids != [item.get("case_id") for item in schedules] or case_ids != [item.get("case_id") for item in attempts]:
        raise ValueError("mounted case/schedule/attempt denominators differ")
    return cases, schedules, attempts


class _FactsAdapter(Stage2FactsPort):
    def derive(self, source_batch: Mapping[str, Any]) -> Mapping[str, Any]:
        return derive_case_facts(source_batch)


class _PackProvider:
    def __init__(self, attempt: Mapping[str, Any], fault_code: str) -> None:
        self.original = dict(attempt)
        self.effective = dict(attempt)
        injected = {
            "PROMPT_INJECTION": "REJECTED",
            "PROVIDER_REFUSAL": "REFUSAL",
            "PROVIDER_TIMEOUT": "TIMEOUT",
        }.get(fault_code)
        if injected:
            self.effective.update(
                terminal_status=injected,
                validation_result="NO_ACCEPTED_CANDIDATE",
                fallback_disposition="DETERMINISTIC_GOVERNED_FALLBACK",
                recorded_candidate=None,
                response_sha256=None,
            )

    def propose_context(self, context: Mapping[str, Any], *, attempt_id: str) -> Any:
        if attempt_id != self.effective.get("attempt_id"):
            raise ValueError("attempt identity differs from frozen provider mapping")
        return SimpleNamespace(
            attempt_id=attempt_id,
            terminal_status=self.effective["terminal_status"],
            validation_result=self.effective["validation_result"],
            candidate=self.effective.get("recorded_candidate"),
            fallback_disposition=self.effective["fallback_disposition"],
            raw_response_sha256=self.effective.get("response_sha256"),
        )


def _id(case_id: str, kind: str, index: int = 1) -> str:
    return f"S2-{kind}-CF-{case_id.rsplit('-', 1)[-1]}-{index:02d}"


def _write_record(workspace: FileRecoveryWorkspace, collection: str, record: Mapping[str, Any]) -> None:
    workspace.authority.write_once(
        f"{collection}/{record['record_id']}.json", canonical_json_bytes(dict(record))
    )


def _prepare_and_reserve_action(
    workspace: FileRecoveryWorkspace,
    service: RecoveryApplicationService,
    decision: Mapping[str, Any],
    case_id: str,
) -> dict[str, Any]:
    before = workspace.replay()
    workspace.append_transition(
        target_state=WorkflowState.ACTION_PREPARED,
        event_type="EXACT_ACTION_PREPARATION_STARTED",
        actor_kind="deterministic_control",
        actor_id="S2-ACTOR-ACTION-PREPARATION",
        expected_case_revision=before.case_revision,
        expected_ledger_head=before.ledger_head_digest,
        command_id=_id(case_id, "CMD-ACTION-PREPARE"),
        links={"recommendation_id": before.active_object_ids["recommendation_id"]},
        decision_or_effect={"authority_route": decision["authority_route"]},
        action_count=0,
    )
    context = service.inspect()
    batch = workspace.load_source_batch()
    oms = next(item["payload"]["data"] for item in batch["payload"]["records"] if item["payload"]["source_name"] == "OMS")
    facts = context.permitted_facts
    roles = {
        "DELEGATED_DECISION": "SYNTHETIC_RECOVERY_SPECIALIST",
        "WORKFLOW_OWNER_APPROVAL": "SYNTHETIC_WORKFLOW_OWNER",
        "FINANCE_APPROVAL": "SYNTHETIC_FINANCE_APPROVER",
    }
    approver_role = roles[decision["authority_route"]]
    authority_kind = "DECISION" if decision["authority_route"] == "DELEGATED_DECISION" else "APPROVAL"
    approval_id = _id(case_id, authority_kind)
    action = build_action_contract(
        action_id=_id(case_id, "ACTION"),
        case_id=case_id,
        case_revision=context.case_revision,
        ledger_head_digest=context.ledger_head_digest,
        policy_id=decision["policy_id"],
        policy_version=decision["policy_version"],
        operation=decision["proposed_action"],
        target=oms["order_id"],
        eligible_business_key=facts["lines"][0]["line_id"],
        eligible_quantity=facts["remaining_quantity"],
        amount_cents=facts["affected_value_cents"],
        currency=facts["currency"],
        before_state={
            "available_replacement_quantity": facts["available_replacement_quantity"],
            "remaining_quantity": facts["remaining_quantity"],
        },
        authority_route=decision["authority_route"],
        authority_reference=approval_id,
        idempotency_key=_id(case_id, "IDEMPOTENCY"),
        timeout_milliseconds=5000,
    )
    _write_record(workspace, "actions", action)
    payload = action["payload"]
    authority = create_synthetic_authority_event(
        AuthorityExpectation(
            case_id=case_id,
            case_revision=payload["case_revision"],
            ledger_head_digest=payload["ledger_head_digest"],
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            operation=payload["operation"],
            payload_digest=payload["action_payload_digest"],
            authority_route=payload["authority_route"],
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
        ),
        approval_id=approval_id,
        issued_by=ACTOR_BY_ROLE[approver_role],
        approver_role=approver_role,
        decision="APPROVED",
        rationale_code="SYNTHETIC_POLICY_BOUND_RECOVERY",
        issued_at="2026-08-11T13:00:00Z",
        expires_at="2026-08-11T15:00:00Z",
    )
    _write_record(workspace, "approvals", authority)
    service.reserve_action(
        ActionReservationCommand(
            action=action,
            authority_event=authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now="2026-08-11T14:00:00Z",
            command_id=_id(case_id, "CMD-RESERVE"),
        )
    )
    return action


def _execute_assisted_case(
    case: Mapping[str, Any], schedule: Mapping[str, Any], attempt: Mapping[str, Any], runs_root: Path
) -> tuple[Path, dict[str, Any]]:
    case_id = case["payload"]["case_id"]
    run_id = f"S2-RUN-CF-{case_id.rsplit('-', 1)[-1]}"
    workspace = FileRecoveryWorkspace.prepare(runs_root, run_id, case)
    service = RecoveryApplicationService(workspace, _FactsAdapter())
    current = service.inspect()
    current = service.advance(
        TransitionCommand(
            target_state=WorkflowState.DEDUPLICATED,
            event_type="SERVER_DERIVED", actor_kind="server_derived", actor_id="S2-ACTOR-SERVER-DERIVED",
            expected_case_revision=current.case_revision, expected_ledger_head=current.ledger_head_digest,
            command_id=_id(case_id, "CMD-DEDUP"),
        )
    )
    current = service.advance(
        TransitionCommand(
            target_state=WorkflowState.CONTEXT_READY,
            event_type="SERVER_DERIVED", actor_kind="server_derived", actor_id="S2-ACTOR-SERVER-DERIVED",
            expected_case_revision=current.case_revision, expected_ledger_head=current.ledger_head_digest,
            command_id=_id(case_id, "CMD-CONTEXT"),
        )
    )
    policy = RecoveryPolicyAdapter()
    decision = policy.decide(current).to_dict()
    route_command = (
        _id(case_id, "CMD-ROUTE")
        if decision["authority_route"] in {
            "SPECIALIST_STOP", "AWAITING_CHOICE", "DELEGATED_DECISION",
            "WORKFLOW_OWNER_APPROVAL", "FINANCE_APPROVAL",
        }
        else None
    )
    provider = _PackProvider(attempt, str(schedule["fault_code"]))
    service.recommend(
        RecommendationCommand(
            recommendation_id=_id(case_id, "RECOMMENDATION"),
            attempt_id=attempt["attempt_id"],
            expected_case_revision=current.case_revision,
            expected_ledger_head=current.ledger_head_digest,
            command_id=_id(case_id, "CMD-RECOMMEND"),
            route_command_id=route_command,
        ),
        provider,
        policy,
    )
    orchestrator = GuardedRecoveryOrchestrator(workspace, _FactsAdapter())
    if decision["authority_route"] == "DIRECT_NO_ACTION":
        orchestrator.verify_direct_no_action(
            verification_id=_id(case_id, "VERIFICATION"), command_id=_id(case_id, "CMD-VERIFY")
        )
        orchestrator.communicate_active_verification(
            communication_id=_id(case_id, "COMMUNICATION"), command_id=_id(case_id, "CMD-COMMUNICATE")
        )
        orchestrator.close(closure_id=_id(case_id, "CLOSURE"), command_id=_id(case_id, "CMD-CLOSE"))
    elif decision["authority_route"] in {"DELEGATED_DECISION", "WORKFLOW_OWNER_APPROVAL", "FINANCE_APPROVAL"}:
        _prepare_and_reserve_action(workspace, service, decision, case_id)
        fault_code = str(schedule["fault_code"])
        if fault_code == "MISSING_AUTHORITATIVE_POSTCONDITION":
            try:
                orchestrator.execute_active_action(
                    start_command_id=_id(case_id, "CMD-EXECUTE"),
                    outcome_command_id=_id(case_id, "CMD-EXECUTE-OUTCOME"),
                    fault="before_mutation",
                )
            except RuntimeError:
                pass
        elif fault_code == "LOST_RECEIPT_AFTER_COMMIT":
            try:
                orchestrator.execute_active_action(
                    start_command_id=_id(case_id, "CMD-EXECUTE"),
                    outcome_command_id=_id(case_id, "CMD-EXECUTE-UNKNOWN"),
                    fault="after_mutation_before_receipt",
                )
            except RuntimeError:
                pass
            orchestrator.execute_active_action(
                start_command_id=_id(case_id, "CMD-RETRY"),
                outcome_command_id=_id(case_id, "CMD-RECONCILED"),
            )
            orchestrator.verify_active_action(
                verification_id=_id(case_id, "VERIFICATION"), command_id=_id(case_id, "CMD-VERIFY")
            )
            orchestrator.communicate_active_verification(
                communication_id=_id(case_id, "COMMUNICATION"), command_id=_id(case_id, "CMD-COMMUNICATE")
            )
            orchestrator.close(closure_id=_id(case_id, "CLOSURE"), command_id=_id(case_id, "CMD-CLOSE"))
        else:
            orchestrator.execute_active_action(
                start_command_id=_id(case_id, "CMD-EXECUTE"),
                outcome_command_id=_id(case_id, "CMD-EXECUTE-OUTCOME"),
            )
            orchestrator.verify_active_action(
                verification_id=_id(case_id, "VERIFICATION"), command_id=_id(case_id, "CMD-VERIFY")
            )
            orchestrator.communicate_active_verification(
                communication_id=_id(case_id, "COMMUNICATION"), command_id=_id(case_id, "CMD-COMMUNICATE")
            )
            orchestrator.close(closure_id=_id(case_id, "CLOSURE"), command_id=_id(case_id, "CMD-CLOSE"))
    workspace.authority.write_once(
        "schedule-binding.json",
        canonical_json_bytes(
            {
                "case_id": case_id,
                "case_sha256": canonical_sha256(case),
                "effective_provider_attempt": provider.effective,
                "original_provider_attempt_sha256": canonical_sha256(attempt),
                "schedule_sha256": schedule["schedule_sha256"],
                "schema_version": "stage2-assisted-schedule-binding/v1",
            }
        ),
    )
    service.verify()
    service.freeze()
    return workspace.run_root, provider.effective


def _copy_raw_workspace(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name in {".writer.lock", ".release-writer.lock"}:
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = path.read_bytes()
        payload.decode("utf-8")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)


def build_raw_evaluation_bundle(input_root: Path, bundle_root: Path, work_root: Path) -> None:
    cases, schedules, attempts = _verify_mounted_input(input_root)
    runs_root = work_root / "runs"
    for case, schedule, attempt in zip(cases, schedules, attempts, strict=True):
        case_id = case["payload"]["case_id"]
        raw_root, effective_attempt = _execute_assisted_case(case, schedule, attempt, runs_root)
        assisted = bundle_root / "assisted" / case_id
        _copy_raw_workspace(raw_root, assisted)
        if effective_attempt != load_canonical_json(
            (assisted / "schedule-binding.json").read_bytes()
        )["effective_provider_attempt"]:
            raise ValueError("copied workspace changed the effective provider attempt binding")

        comparator = replay_current_state(case)
        comparator_root = bundle_root / "comparator" / case_id
        comparator_root.mkdir(parents=True, exist_ok=True)
        (comparator_root / "source-batch.json").write_bytes(canonical_json_bytes(case))
        (comparator_root / "queue-events.jsonl").write_bytes(
            b"".join(canonical_json_bytes(item) for item in comparator["queue_trace"])
        )
        (comparator_root / "result.json").write_bytes(canonical_json_bytes(comparator))
        (comparator_root / "schedule-binding.json").write_bytes(
            canonical_json_bytes(
                {
                    "case_id": case_id,
                    "case_sha256": canonical_sha256(case),
                    "schedule_sha256": schedule["schedule_sha256"],
                    "schema_version": "stage2-comparator-schedule-binding/v1",
                }
            )
        )


def _probe_capabilities(*, canonical_container: bool) -> dict[str, Any]:
    def broker(relative: str) -> None:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or any(part in {".git", "private", "artifacts"} for part in path.parts):
            raise PermissionError

    probes: dict[str, Any] = {"schema_version": "stage2-inner-capability-probes/v2"}
    for name, value in (
        ("absolute_path_probe", "/etc/passwd"), ("parent_path_probe", "../oracle.jsonl"),
        ("home_path_probe", "home/private"), ("git_object_probe", ".git/objects"),
    ):
        try:
            broker(value)
        except PermissionError:
            probes[name] = "denied"
        else:
            probes[name] = "failed-open"
    probes["home_environment_probe"] = "denied" if "HOME" not in os.environ else "failed-open"
    if canonical_container:
        try:
            socket.create_connection(("example.invalid", 443), timeout=1)
        except OSError:
            probes["socket_probe"] = "denied"
        else:
            probes["socket_probe"] = "failed-open"
        try:
            subprocess.run(["/bin/true"], check=True, timeout=1)
        except (OSError, subprocess.SubprocessError):
            probes["subprocess_probe"] = "denied"
        else:
            probes["subprocess_probe"] = "failed-open"
    else:
        probes.update(socket_probe="development-only-not-probed", subprocess_probe="development-only-not-probed")
    return probes


def _bundle_inventory(root: Path) -> dict[str, dict[str, Any]]:
    inventory = {}
    root_resolved = root.resolve(strict=True)
    for path in sorted(root.rglob("*")):
        resolved = path.resolve(strict=True)
        if path.is_symlink() or (resolved != root_resolved and root_resolved not in resolved.parents):
            raise RuntimeError(f"bundle artifact escaped its authority root: {path}")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise RuntimeError(f"bundle artifact is not a sole regular file: {path}")
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        inventory[relative] = {"bytes": len(payload), "sha256": _sha(payload), "type": "regular-file"}
    return inventory


def _runtime_paths() -> dict[str, str]:
    paths = {
        "Dockerfile": "containers/stage2-evaluation/Dockerfile",
        SECCOMP_CONTEXT_PATH: SECCOMP_REPOSITORY_PATH,
    }
    paths.update({f"scripts/{name}": f"scripts/{name}" for name in RUNTIME_MODULES})
    return paths


def _git(project_root: Path, *arguments: str, binary: bool = False) -> bytes | str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *arguments],
            check=True,
            capture_output=True,
            text=not binary,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"cannot resolve committed runtime source: {error}") from error
    return result.stdout


def _committed_runtime_blobs(
    project_root: Path, *, require_clean: bool
) -> tuple[dict[str, str], dict[str, tuple[str, str, bytes]]]:
    """Load the minimal runtime only from committed Git objects, never checkout bytes."""

    if require_clean and str(_git(project_root, "status", "--porcelain=v1", "--untracked-files=all")):
        raise RuntimeError("runtime build context requires a clean source worktree")
    tracked_modes = str(_git(project_root, "ls-files", "-s"))
    if any(line.startswith(("120000 ", "160000 ")) for line in tracked_modes.splitlines()):
        raise RuntimeError("runtime source cannot contain symlinks or gitlinks")
    source = {
        "source_commit": str(_git(project_root, "rev-parse", "HEAD")).strip(),
        "source_tree": str(_git(project_root, "rev-parse", "HEAD^{tree}")).strip(),
    }
    blobs: dict[str, tuple[str, str, bytes]] = {}
    for context_path, repository_path in sorted(_runtime_paths().items()):
        entry = bytes(
            _git(project_root, "ls-tree", "-z", "HEAD", "--", repository_path, binary=True)
        )
        records = [record for record in entry.split(b"\0") if record]
        if len(records) != 1:
            raise RuntimeError(f"runtime build input is missing or ambiguous in HEAD: {repository_path}")
        try:
            header, returned_path = records[0].split(b"\t", 1)
            mode_raw, object_type, object_id_raw = header.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            object_id = object_id_raw.decode("ascii")
            exact_path = returned_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise RuntimeError(f"runtime Git entry is malformed: {repository_path}") from error
        if exact_path != repository_path or object_type != b"blob" or mode not in {"100644", "100755"}:
            raise RuntimeError(f"runtime build input is not a regular committed blob: {repository_path}")
        payload = bytes(_git(project_root, "cat-file", "blob", object_id, binary=True))
        blobs[context_path] = (mode, object_id, payload)
    return source, blobs


def _runtime_inventory(blobs: Mapping[str, tuple[str, str, bytes]]) -> dict[str, dict[str, Any]]:
    return {
        path: {
            "bytes": len(payload),
            "git_blob_oid": object_id,
            "git_mode": mode,
            "sha256": _sha(payload),
            "type": "regular-file",
        }
        for path, (mode, object_id, payload) in sorted(blobs.items())
    }


def runtime_build_context_inventory(project_root: Path) -> dict[str, dict[str, Any]]:
    """Derive the canonical minimal context from a clean checkout's committed HEAD blobs."""

    _, blobs = _committed_runtime_blobs(project_root, require_clean=True)
    return _runtime_inventory(blobs)


def _test_only_runtime_build_context_inventory(project_root: Path) -> dict[str, dict[str, Any]]:
    """Derive noncanonical fixture pins from HEAD while the test checkout contains its own diff."""

    _, blobs = _committed_runtime_blobs(project_root, require_clean=False)
    return _runtime_inventory(blobs)


def _materialize_runtime_blobs(
    context: Path, blobs: Mapping[str, tuple[str, str, bytes]]
) -> dict[str, dict[str, Any]]:
    if any(context.iterdir()):
        raise RuntimeError("runtime build context destination must be empty")
    for relative, (mode, _, payload) in sorted(blobs.items()):
        destination = context / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755 if mode == "100755" else 0o644)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    inventory = _runtime_inventory(blobs)
    for relative, expected in inventory.items():
        payload = (context / PurePosixPath(relative)).read_bytes()
        if len(payload) != expected["bytes"] or _sha(payload) != expected["sha256"]:
            raise RuntimeError(f"materialized runtime blob differs from committed source: {relative}")
    return inventory


def materialize_runtime_build_context(
    project_root: Path, context: Path
) -> dict[str, dict[str, Any]]:
    """Materialize exact committed blobs into an empty Docker context."""

    _, blobs = _committed_runtime_blobs(project_root, require_clean=True)
    return _materialize_runtime_blobs(context, blobs)


def _canonical_image_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(IMAGE_ID_PREFIX)
        or len(value) != 71
        or any(character not in "0123456789abcdef" for character in value[len(IMAGE_ID_PREFIX):])
    ):
        raise RuntimeError("Docker image identity is not an immutable sha256 ID")
    return value


def _run_docker_text(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a Docker command whose stdout is textual under one explicit codec."""

    if not command or command[0] != "docker":
        raise ValueError("UTF-8 Docker runner accepts Docker commands only")
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )


def _image_build_receipt(
    *,
    image_id: str,
    build_input_sha256: str,
    seccomp_profile_sha256: str,
    source_commit: str,
    source_tree: str,
) -> dict[str, Any]:
    if (
        len(build_input_sha256) != 64
        or any(character not in "0123456789abcdef" for character in build_input_sha256)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or len(source_tree) != 40
        or any(character not in "0123456789abcdef" for character in source_tree)
        or len(seccomp_profile_sha256) != 64
        or any(character not in "0123456789abcdef" for character in seccomp_profile_sha256)
    ):
        raise RuntimeError("image build receipt source/build pins are noncanonical")
    material = {
        "base_image_digest": BASE_IMAGE.split("@", 1)[1],
        "build_input_sha256": build_input_sha256,
        "image_id": _canonical_image_id(image_id),
        "schema_version": "stage2-image-build-receipt/v2",
        "seccomp_profile_sha256": seccomp_profile_sha256,
        "source_commit": source_commit,
        "source_tree": source_tree,
    }
    return {**material, "receipt_sha256": canonical_sha256(material)}


def verify_image_info(
    image_info: Mapping[str, Any], pins: Mapping[str, Any]
) -> dict[str, Any]:
    image_id = _canonical_image_id(image_info.get("Id"))
    config = image_info.get("Config")
    if not isinstance(config, Mapping):
        raise RuntimeError("Docker image inspect lacks canonical configuration")
    labels = config.get("Labels") or {}
    if not isinstance(labels, Mapping):
        raise RuntimeError("Docker image labels are unavailable")
    expected = {
        "stage2.base_image_digest": BASE_IMAGE.split("@", 1)[1],
        "stage2.build_input_sha256": pins.get("runtime_build_input_sha256"),
        "stage2.seccomp_profile_sha256": pins.get("seccomp_profile_sha256"),
        "stage2.source_commit": pins.get("source_commit"),
        "stage2.source_tree": pins.get("source_tree"),
    }
    if any(labels.get(key) != value for key, value in expected.items()):
        raise RuntimeError("evaluation image labels differ from the frozen build/source pins")
    return _image_build_receipt(
        image_id=image_id,
        build_input_sha256=str(expected["stage2.build_input_sha256"]),
        seccomp_profile_sha256=str(expected["stage2.seccomp_profile_sha256"]),
        source_commit=str(expected["stage2.source_commit"]),
        source_tree=str(expected["stage2.source_tree"]),
    )


def resolve_image_receipt(image: str, pins: Mapping[str, Any]) -> dict[str, Any]:
    inspected = json.loads(
        _run_docker_text(["docker", "image", "inspect", image], timeout=30).stdout
    )
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise RuntimeError("Docker image reference did not resolve exactly once")
    return verify_image_info(inspected[0], pins)


def frame_bundle(root: Path) -> bytes:
    inventory = _bundle_inventory(root)
    output = bytearray()
    for relative, metadata in inventory.items():
        output.extend(canonical_json_bytes({"bytes": metadata["bytes"], "path": relative, "sha256": metadata["sha256"]}))
        output.extend((root / PurePosixPath(relative)).read_bytes())
    output.extend(canonical_json_bytes({"end": True, "file_count": len(inventory), "inventory_sha256": canonical_sha256(inventory)}))
    if len(output) > MAX_BUNDLE_BYTES or len(inventory) > MAX_BUNDLE_FILES:
        raise ValueError("framed output bundle exceeds frozen bounds")
    return bytes(output)


def parse_framed_bundle(payload: bytes) -> dict[str, bytes]:
    if len(payload) > MAX_BUNDLE_BYTES:
        raise ValueError("framed output exceeds byte bound")
    offset = 0; files: dict[str, bytes] = {}; inventory: dict[str, dict[str, Any]] = {}
    while offset < len(payload):
        end = payload.find(b"\n", offset)
        if end < 0:
            raise ValueError("framed output header is truncated")
        header = load_canonical_json(payload[offset : end + 1]); offset = end + 1
        if not isinstance(header, dict):
            raise ValueError("framed output header is invalid")
        if header.get("end") is True:
            if offset != len(payload) or header.get("file_count") != len(files) or header.get("inventory_sha256") != canonical_sha256(inventory):
                raise ValueError("framed output footer does not bind exact inventory")
            return files
        relative = header.get("path"); size = header.get("bytes")
        if not isinstance(relative, str) or not isinstance(size, int) or size < 0:
            raise ValueError("framed output file header is invalid")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or relative in files:
            raise ValueError("framed output path is unsafe or duplicated")
        if not (relative == "capability-probes.json" or relative.startswith("assisted/S2-CASE-") or relative.startswith("comparator/S2-CASE-")):
            raise ValueError("framed output path is outside allow-list")
        content = payload[offset : offset + size]; offset += size
        if len(content) != size or _sha(content) != header.get("sha256"):
            raise ValueError("framed output content digest/length mismatch")
        content.decode("utf-8")
        files[relative] = content
        inventory[relative] = {"bytes": size, "sha256": header["sha256"], "type": "regular-file"}
        if len(files) > MAX_BUNDLE_FILES:
            raise ValueError("framed output file count exceeds bound")
    raise ValueError("framed output footer is absent")


def materialize_bundle(files: Mapping[str, bytes], output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError("outer output directory must be empty")
    for relative, payload in sorted(files.items()):
        target = output_root / PurePosixPath(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())


def run_inner_evaluation(input_root: Path, output_root: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="stage2-inner-test-") as temporary:
        temp = Path(temporary)
        bundle = temp / "bundle"; bundle.mkdir()
        build_raw_evaluation_bundle(input_root, bundle, temp / "work")
        (bundle / "capability-probes.json").write_bytes(canonical_json_bytes(_probe_capabilities(canonical_container=False)))
        materialize_bundle({key: (bundle / PurePosixPath(key)).read_bytes() for key in _bundle_inventory(bundle)}, output_root)


def _read_sole_regular_file_once(path: Path, *, max_bytes: int = 64 * 1024) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.stat(path, follow_symlinks=False)
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError("seccomp profile cannot be opened as a frozen regular file") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not os.path.samestat(before, opened)
        ):
            raise RuntimeError("seccomp profile is not the frozen sole regular file")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise RuntimeError("seccomp profile exceeds the frozen byte bound")
        after = os.stat(path, follow_symlinks=False)
        if not os.path.samestat(opened, after):
            raise RuntimeError("seccomp profile identity changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def _verify_held_seccomp_profile(path: Path, descriptor: int, expected_bytes: bytes) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise RuntimeError("materialized seccomp profile identity changed") from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or not os.path.samestat(opened, current)
    ):
        raise RuntimeError("materialized seccomp profile identity changed")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = len(expected_bytes) + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if b"".join(chunks) != expected_bytes:
        raise RuntimeError("materialized seccomp profile bytes changed")


@contextmanager
def _materialized_frozen_seccomp_profile(
    expected_bytes: bytes, expected_sha256: str
) -> Iterable[tuple[Path, int]]:
    if _sha(expected_bytes) != expected_sha256:
        raise RuntimeError("seccomp source bytes differ from the frozen committed pin")
    with tempfile.TemporaryDirectory(prefix="stage2-seccomp-") as temporary:
        path = Path(temporary) / "seccomp.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(expected_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        read_descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            _verify_held_seccomp_profile(path, read_descriptor, expected_bytes)
            yield path.resolve(), read_descriptor
            _verify_held_seccomp_profile(path, read_descriptor, expected_bytes)
        finally:
            os.close(read_descriptor)


def build_docker_command(*, image: str, input_root: Path, output_root: Path | None = None, seccomp_profile: Path | None = None) -> list[str]:
    image_id = _canonical_image_id(image)
    candidate = seccomp_profile or Path(__file__).resolve().parents[1] / SECCOMP_REPOSITORY_PATH
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_nlink != 1:
        raise RuntimeError("seccomp profile is not the frozen sole regular file")
    profile = candidate.resolve()
    return [
        "docker", "create", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--security-opt", f"seccomp={profile}",
        "--pids-limit", "1", "--memory", "256m", "--cpus", "0.5", "--read-only",
        "--user", "65532:65532", "--ipc", "none", "--ulimit", "nofile=64:64",
        "--tmpfs", "/work:rw,noexec,nosuid,nodev,size=256m",
        "--mount", f"type=bind,source={input_root},target=/input,readonly",
        image_id,
    ]


def validate_docker_inspect(
    inspected: Mapping[str, Any],
    input_root: Path,
    output_root: Path | None = None,
    *,
    expected_image_id: str,
    expected_labels: Mapping[str, Any],
    seccomp_profile: Path,
    expected_seccomp_bytes: bytes | None = None,
) -> None:
    host = inspected.get("HostConfig"); config = inspected.get("Config"); mounts = inspected.get("Mounts")
    if not isinstance(host, Mapping) or not isinstance(config, Mapping) or not isinstance(mounts, list):
        raise RuntimeError("Docker inspect response is incomplete")
    for key, value in {"NetworkMode": "none", "ReadonlyRootfs": True, "PidsLimit": 1, "Memory": 268435456, "NanoCpus": 500000000}.items():
        if host.get(key) != value:
            raise RuntimeError(f"Docker inspect does not prove {key}={value!r}")
    if "ALL" not in (host.get("CapDrop") or []):
        raise RuntimeError("Docker inspect does not prove all capabilities dropped")
    image_id = _canonical_image_id(expected_image_id)
    if inspected.get("Image") != image_id:
        raise RuntimeError("Docker container image differs from the pre-create immutable image ID")
    if seccomp_profile.is_symlink() or not seccomp_profile.is_file() or seccomp_profile.stat().st_nlink != 1:
        raise RuntimeError("seccomp profile is not the frozen sole regular file")
    profile = seccomp_profile.resolve()
    frozen_bytes = (
        _read_sole_regular_file_once(profile)
        if expected_seccomp_bytes is None
        else bytes(expected_seccomp_bytes)
    )
    if _read_sole_regular_file_once(profile) != frozen_bytes:
        raise RuntimeError("Docker inspect does not prove the exact frozen seccomp profile")
    security = host.get("SecurityOpt") or []
    if not isinstance(security, list) or len(security) != 2 or security[0] != "no-new-privileges:true":
        raise RuntimeError("Docker inspect does not prove the exact frozen seccomp profile")
    seccomp_option = security[1]
    if not isinstance(seccomp_option, str) or not seccomp_option.startswith("seccomp="):
        raise RuntimeError("Docker inspect does not prove the exact frozen seccomp profile")
    applied_profile = seccomp_option.removeprefix("seccomp=")
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        frozen = json.loads(
            frozen_bytes,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
        applied = json.loads(
            applied_profile,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Docker inspect does not prove the exact frozen seccomp profile") from exc
    if (
        not isinstance(frozen, Mapping)
        or not isinstance(applied, Mapping)
        or canonical_json_bytes(applied) != canonical_json_bytes(frozen)
    ):
        raise RuntimeError("Docker inspect does not prove the exact frozen seccomp profile")
    labels = config.get("Labels") or {}
    if not isinstance(labels, Mapping) or any(labels.get(key) != value for key, value in expected_labels.items()):
        raise RuntimeError("Docker container labels differ from the verified image build receipt")
    if config.get("User") != "65532:65532":
        raise RuntimeError("Docker inspect does not prove non-root evaluated user")
    expected_entrypoint = ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"]
    if config.get("Entrypoint") != expected_entrypoint:
        raise RuntimeError("Docker inspect entrypoint differs from frozen empty-environment launcher")
    if config.get("Cmd") not in (None, []):
        raise RuntimeError("Docker inspect adds arguments to the frozen inner entrypoint")
    if host.get("IpcMode") != "none":
        raise RuntimeError("Docker inspect does not prove isolated IPC")
    if host.get("Ulimits") != [{"Hard": 64, "Name": "nofile", "Soft": 64}]:
        raise RuntimeError("Docker inspect does not prove the frozen file-descriptor limit")
    if host.get("Tmpfs") != {"/work": "rw,noexec,nosuid,nodev,size=256m"}:
        raise RuntimeError("Docker inspect does not prove the sole isolated writable tmpfs")
    bind_mounts = [item for item in mounts if item.get("Type") == "bind"]
    if len(bind_mounts) != 1 or len(mounts) != 1:
        raise RuntimeError("Docker inspect must expose exactly one read-only input mount")
    item = bind_mounts[0]
    if item.get("Destination") != "/input" or item.get("RW") is not False:
        raise RuntimeError("Docker inspect input mount is not exact/read-only")
    source = item.get("Source")
    if not isinstance(source, str) or not source:
        raise RuntimeError("Docker inspect input source identity is absent")
    try:
        same_input = os.path.samefile(source, input_root)
    except OSError:
        same_input = os.path.normcase(os.path.abspath(source)) == os.path.normcase(str(input_root.resolve()))
    if not same_input:
        raise RuntimeError("Docker inspect input source differs from the prepared host identity")


def launch_container(input_root: Path, output_root: Path, *, image: str, dry_run: bool = False) -> list[str] | dict[str, Any]:
    module_profile = (Path(__file__).resolve().parents[1] / SECCOMP_REPOSITORY_PATH).resolve()
    if dry_run:
        return build_docker_command(
            image=image,
            input_root=input_root.resolve(),
            seccomp_profile=module_profile,
        )
    _verify_mounted_input(input_root)
    pins = load_canonical_json((input_root / "pins.json").read_bytes())
    if not isinstance(pins, Mapping):
        raise RuntimeError("frozen source/build pins are unavailable")
    expected_seccomp_sha256 = pins.get("seccomp_profile_sha256")
    if (
        not isinstance(expected_seccomp_sha256, str)
        or len(expected_seccomp_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_seccomp_sha256)
    ):
        raise RuntimeError("frozen committed seccomp digest is unavailable")
    seccomp_bytes = _read_sole_regular_file_once(module_profile)
    if _sha(seccomp_bytes) != expected_seccomp_sha256:
        raise RuntimeError("launcher seccomp source differs from the frozen committed pin")
    receipt = resolve_image_receipt(image, pins)
    preparation_path = output_root.parent / "preparation.json"
    preparation = load_canonical_json(preparation_path.read_bytes())
    expected_preparation = {
        "expected_build_input_sha256": receipt["build_input_sha256"],
        "expected_image_id": receipt["image_id"],
        "expected_seccomp_profile_sha256": receipt["seccomp_profile_sha256"],
        "image_build_receipt_sha256": receipt["receipt_sha256"],
    }
    if not isinstance(preparation, Mapping) or any(
        preparation.get(key) != value for key, value in expected_preparation.items()
    ):
        raise RuntimeError("resolved immutable image differs from the frozen preparation receipt")
    with _materialized_frozen_seccomp_profile(
        seccomp_bytes, expected_seccomp_sha256
    ) as (seccomp_profile, seccomp_descriptor):
        _verify_held_seccomp_profile(seccomp_profile, seccomp_descriptor, seccomp_bytes)
        command = build_docker_command(
            image=receipt["image_id"],
            input_root=input_root.resolve(),
            seccomp_profile=seccomp_profile,
        )
        created = _run_docker_text(command, timeout=60)
        container_id = created.stdout.strip()
        if not container_id:
            raise RuntimeError("Docker did not return a container ID")
        try:
            _verify_held_seccomp_profile(seccomp_profile, seccomp_descriptor, seccomp_bytes)
            inspected = json.loads(
                _run_docker_text(["docker", "inspect", container_id], timeout=30).stdout
            )[0]
            expected_labels = {
                "stage2.base_image_digest": BASE_IMAGE.split("@", 1)[1],
                "stage2.build_input_sha256": receipt["build_input_sha256"],
                "stage2.seccomp_profile_sha256": receipt["seccomp_profile_sha256"],
                "stage2.source_commit": receipt["source_commit"],
                "stage2.source_tree": receipt["source_tree"],
            }
            validate_docker_inspect(
                inspected,
                input_root,
                expected_image_id=receipt["image_id"],
                expected_labels=expected_labels,
                seccomp_profile=seccomp_profile,
                expected_seccomp_bytes=seccomp_bytes,
            )
            _verify_held_seccomp_profile(seccomp_profile, seccomp_descriptor, seccomp_bytes)
            completed = subprocess.run(["docker", "start", "-a", container_id], check=True, capture_output=True, timeout=180)
            files = parse_framed_bundle(completed.stdout)
            materialize_bundle(files, output_root)
            probes = load_canonical_json(files["capability-probes.json"])
            if probes.get("subprocess_probe") != "denied" or probes.get("socket_probe") != "denied":
                raise RuntimeError("inner capability probes did not fail closed")
            inventory = _bundle_inventory(output_root)
            attestation = {
                "absolute_path_probe": probes["absolute_path_probe"],
                "base_image_digest": BASE_IMAGE.split("@", 1)[1],
                "build_input_sha256": receipt["build_input_sha256"],
                "canonical_run": True,
                "capabilities": "ALL_DROPPED",
                "completed_output_inventory_sha256": canonical_sha256(inventory),
                "container_id": container_id,
                "container_user": "65532:65532",
                "cpu_limit": "0.5",
                "evaluated_environment": ["PYTHONPATH=/app", "TMPDIR=/work"],
                "git_object_probe": probes["git_object_probe"],
                "home_mount": "absent",
                "home_environment_probe": probes["home_environment_probe"],
                "home_path_probe": probes["home_path_probe"],
                "image_build_receipt_sha256": receipt["receipt_sha256"],
                "image_id": receipt["image_id"],
                "image_source_commit": receipt["source_commit"],
                "image_source_tree": receipt["source_tree"],
                "input_manifest_sha256": _sha((input_root / "manifest.json").read_bytes()),
                "memory_limit_bytes": 268435456,
                "mounts": [{"access": "read-only", "source_identity": str(input_root.resolve()), "target": "/input"}],
                "network": "none",
                "no_new_privileges": True,
                "oracle_mount": "absent",
                "outer_materialized_output": True,
                "parent_path_probe": probes["parent_path_probe"],
                "pids_limit": 1,
                "private_mount": "absent",
                "repository_mount": "absent",
                "root_filesystem": "read-only",
                "schema_version": "stage2-isolation-attestation/v2",
                "seccomp_denials": ["clone", "clone3", "execveat", "fork", "socket", "socketpair", "vfork"],
                "seccomp_profile_identity_verified_through_create": True,
                "seccomp_profile_path": str(seccomp_profile),
                "seccomp_profile_sha256": expected_seccomp_sha256,
                "seccomp_profile_source": "outer-materialized-from-frozen-committed-bytes",
                "socket_probe": probes["socket_probe"],
                "subprocess_probe": probes["subprocess_probe"],
                "workspace_mount": "isolated-tmpfs-rw",
                "wall_time_limit_seconds": 180,
                "writer": "outer-launcher-after-container-exit",
            }
            path = output_root.parent / "outer" / "isolation-attestation.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(canonical_json_bytes(attestation)); stream.flush(); os.fsync(stream.fileno())
            return attestation
        finally:
            subprocess.run(["docker", "rm", container_id], check=False, capture_output=True, timeout=30)


def build_image(project_root: Path, image: str) -> dict[str, Any]:
    source, blobs = _committed_runtime_blobs(project_root, require_clean=True)
    expected_build_inventory = _runtime_inventory(blobs)
    with tempfile.TemporaryDirectory(prefix="stage2-eval-build-") as temporary:
        context = Path(temporary)
        build_inventory = _materialize_runtime_blobs(context, blobs)
        if build_inventory != expected_build_inventory:
            raise RuntimeError("materialized image context differs from the frozen minimal runtime inputs")
        build_input_sha256 = canonical_sha256(build_inventory)
        seccomp_profile_sha256 = build_inventory[SECCOMP_CONTEXT_PATH]["sha256"]
        labels = {
            "stage2.base_image_digest": BASE_IMAGE.split("@", 1)[1],
            "stage2.build_input_sha256": build_input_sha256,
            "stage2.seccomp_profile_sha256": seccomp_profile_sha256,
            "stage2.source_commit": source["source_commit"],
            "stage2.source_tree": source["source_tree"],
        }
        command = ["docker", "build", "--pull=false", "--tag", image]
        for key, value in sorted(labels.items()):
            command.extend(["--label", f"{key}={value}"])
        command.append(str(context))
        result = _run_docker_text(command, timeout=300)
    pins = {
        "runtime_build_input_sha256": build_input_sha256,
        "seccomp_profile_sha256": seccomp_profile_sha256,
        "source_commit": source["source_commit"],
        "source_tree": source["source_tree"],
    }
    receipt = resolve_image_receipt(image, pins)
    return {
        **receipt,
        "build_output_sha256": _sha(result.stdout.encode()),
        "image": image,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or run the canonical isolated Stage 2 evaluator.")
    parser.add_argument("--inner", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--image", default="commerce-stage2-evaluation:v1")
    args = parser.parse_args(argv)
    if args.inner:
        work = Path("/work"); bundle = work / "bundle"; bundle.mkdir()
        build_raw_evaluation_bundle(Path("/input"), bundle, work / "runtime")
        (bundle / "capability-probes.json").write_bytes(canonical_json_bytes(_probe_capabilities(canonical_container=True)))
        sys.stdout.buffer.write(frame_bundle(bundle)); sys.stdout.buffer.flush()
        return 0
    project_root = Path(__file__).resolve().parents[1]
    if args.build:
        print(json.dumps(build_image(project_root, args.image), sort_keys=True)); return 0
    if not args.input_root or not args.output_root:
        raise SystemExit("outer execution requires --input-root and --output-root")
    print(json.dumps(launch_container(args.input_root, args.output_root, image=args.image), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
