#!/usr/bin/env python3
"""Build the final public explorer projection from frozen V6 and U7 evidence."""

from __future__ import annotations

import argparse
import contextvars
import json
import os
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate_recovery_workflow import OUTCOME_BUCKET
from scripts.stage2_contracts import canonical_json_bytes, canonical_sha256, load_canonical_json
from scripts.stage2_decision_source import (
    PACK_ID,
    RUN_DIRECTORY,
    RUN_ID,
    read_regular,
    sha256,
    verify_and_replay_public_source,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DECISION_ROOT = Path("data/stage2/decision-pack")
PACK_ROOT = Path("data/stage2/evaluation/v6")
PUBLIC_PACK_ID = "S2-PUBLIC-EVIDENCE-20260812-V2"


class EvidenceProjectionError(ValueError):
    """Raised when frozen public evidence and its projection differ."""


class _EvidenceSnapshot:
    """Cache one build's public sources, then prove they stayed unchanged."""

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.payloads: dict[str, bytes] = {}
        self.json_objects: dict[str, dict[str, Any]] = {}
        self.jsonl_rows: dict[str, list[dict[str, Any]]] = {}

    def read(self, relative: str) -> bytes:
        if relative not in self.payloads:
            self.payloads[relative] = _read_public_bytes_uncached(
                self.project_root, relative
            )
        return self.payloads[relative]

    def verify_unchanged(self) -> None:
        for relative, expected in sorted(self.payloads.items()):
            if _read_public_bytes_uncached(self.project_root, relative) != expected:
                raise EvidenceProjectionError(
                    f"public evidence changed during projection: {relative}"
                )


_ACTIVE_SNAPSHOT: contextvars.ContextVar[_EvidenceSnapshot | None] = (
    contextvars.ContextVar("stage2_evidence_snapshot", default=None)
)


@contextmanager
def _snapshot_scope(project_root: Path):
    root = project_root.resolve()
    active = _ACTIVE_SNAPSHOT.get()
    if active is not None and active.project_root == root:
        yield active
        return
    snapshot = _EvidenceSnapshot(root)
    token = _ACTIVE_SNAPSHOT.set(snapshot)
    try:
        yield snapshot
        snapshot.verify_unchanged()
    finally:
        _ACTIVE_SNAPSHOT.reset(token)


def _public_path(project_root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if (
        not relative
        or posix.is_absolute()
        or "\\" in relative
        or ".." in posix.parts
        or str(posix) != relative
        or ".git" in posix.parts
        or "private" in posix.parts
    ):
        raise EvidenceProjectionError(f"unsafe or non-public evidence path: {relative}")
    root = project_root.resolve()
    candidate = root.joinpath(*posix.parts)
    try:
        candidate.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError) as error:
        raise EvidenceProjectionError(f"public evidence path is missing or escapes the project: {relative}") from error
    current = root
    for part in posix.parts:
        current = current / part
        if current.is_symlink():
            raise EvidenceProjectionError(f"public evidence path contains a symlink: {relative}")
    return candidate


def _read_public_bytes_uncached(project_root: Path, relative: str) -> bytes:
    try:
        return read_regular(_public_path(project_root, relative))
    except Exception as error:
        if isinstance(error, EvidenceProjectionError):
            raise
        raise EvidenceProjectionError(f"cannot read public evidence: {relative}: {error}") from error


def _read_public_bytes(project_root: Path, relative: str) -> bytes:
    active = _ACTIVE_SNAPSHOT.get()
    if active is not None and active.project_root == project_root.resolve():
        return active.read(relative)
    return _read_public_bytes_uncached(project_root, relative)


def _read_json(project_root: Path, relative: str) -> dict[str, Any]:
    active = _ACTIVE_SNAPSHOT.get()
    if active is not None and active.project_root == project_root.resolve():
        if relative not in active.json_objects:
            active.json_objects[relative] = _parse_json_object(
                active.read(relative), relative
            )
        return active.json_objects[relative]
    return _parse_json_object(_read_public_bytes(project_root, relative), relative)


def _parse_json_object(payload: bytes, relative: str) -> dict[str, Any]:
    try:
        value = load_canonical_json(payload)
    except Exception as error:
        raise EvidenceProjectionError(f"invalid canonical public JSON: {relative}") from error
    if not isinstance(value, dict):
        raise EvidenceProjectionError(f"public JSON is not an object: {relative}")
    return value


def _read_jsonl(project_root: Path, relative: str) -> list[dict[str, Any]]:
    active = _ACTIVE_SNAPSHOT.get()
    if active is not None and active.project_root == project_root.resolve():
        if relative not in active.jsonl_rows:
            active.jsonl_rows[relative] = _parse_jsonl(active.read(relative), relative)
        return active.jsonl_rows[relative]
    return _parse_jsonl(_read_public_bytes(project_root, relative), relative)


def _parse_jsonl(payload: bytes, relative: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(payload.splitlines(keepends=True), start=1):
        try:
            value = load_canonical_json(line)
        except Exception as error:
            raise EvidenceProjectionError(f"invalid canonical public JSONL: {relative}:{index}") from error
        if not isinstance(value, dict):
            raise EvidenceProjectionError(f"public JSONL row is not an object: {relative}:{index}")
        rows.append(value)
    return rows


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise EvidenceProjectionError("evidence pointer must be an exact RFC6901 pointer")
    current = value
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdigit() or (key.startswith("0") and key != "0"):
                raise EvidenceProjectionError(f"invalid evidence array pointer token: {key}")
            index = int(key)
            if index >= len(current):
                raise EvidenceProjectionError(f"evidence array pointer is out of range: {key}")
            current = current[index]
        elif isinstance(current, Mapping):
            if key not in current:
                raise EvidenceProjectionError(f"evidence object pointer is missing: {key}")
            current = current[key]
        else:
            raise EvidenceProjectionError("evidence pointer traverses a scalar")
    return current


def _reference(
    project_root: Path,
    path: str,
    pointer: str,
    *,
    record_id: str | None = None,
) -> dict[str, Any]:
    payload = _read_public_bytes(project_root, path)
    reference: dict[str, Any] = {
        "artifact_sha256": sha256(payload),
        "kind": "jsonl-record-pointer" if record_id else "json-pointer",
        "path": path,
        "pointer": pointer,
    }
    if record_id:
        reference["record_id"] = record_id
    return reference


def _resolve_public_reference(project_root: Path, reference: Mapping[str, Any]) -> Any:

    expected = {"artifact_sha256", "kind", "path", "pointer"}
    if reference.get("kind") == "jsonl-record-pointer":
        expected.add("record_id")
    if set(reference) != expected:
        raise EvidenceProjectionError("evidence reference fields are not exact")
    path = reference.get("path")
    pointer = reference.get("pointer")
    if not isinstance(path, str) or not isinstance(pointer, str):
        raise EvidenceProjectionError("evidence reference path and pointer must be strings")
    payload = _read_public_bytes(project_root, path)
    if sha256(payload) != reference.get("artifact_sha256"):
        raise EvidenceProjectionError(f"evidence artifact digest mismatch: {path}")
    if reference["kind"] == "json-pointer":
        value = _read_json(project_root, path)
    elif reference["kind"] == "jsonl-record-pointer":
        record_id = reference.get("record_id")
        matches = [row for row in _read_jsonl(project_root, path) if row.get("record_id") == record_id]
        if len(matches) != 1:
            raise EvidenceProjectionError(f"evidence JSONL record does not resolve exactly once: {path}:{record_id}")
        value = matches[0]
    else:
        raise EvidenceProjectionError("evidence reference kind is unsupported")
    return _pointer(value, pointer)


def resolve_public_reference(project_root: Path, reference: Mapping[str, Any]) -> Any:
    """Resolve one digest-pinned public JSON or JSONL record field."""

    root = Path(project_root).resolve()
    with _snapshot_scope(root):
        return _resolve_public_reference(root, reference)


def _load_decision_pack(project_root: Path) -> dict[str, dict[str, Any]]:
    manifest_path = (DECISION_ROOT / "manifest.json").as_posix()
    manifest = _read_json(project_root, manifest_path)
    if (
        manifest.get("schema_version") != "stage2-decision-pack/v2"
        or manifest.get("source_pack_id") != PACK_ID
        or manifest.get("source_run_id") != RUN_ID
        or manifest.get("public_only_inputs") is not True
    ):
        raise EvidenceProjectionError("U7 decision manifest is not the public V6 decision authority")
    inventory = manifest.get("artifact_sha256")
    if not isinstance(inventory, Mapping):
        raise EvidenceProjectionError("U7 decision manifest artifact inventory is missing")
    expected = {PurePosixPath(str(path)).name for path in inventory} | {"manifest.json"}
    actual = {path.name for path in (project_root / DECISION_ROOT).glob("*.json") if path.is_file()}
    if actual != expected:
        raise EvidenceProjectionError("U7 decision-pack JSON inventory differs")
    loaded: dict[str, dict[str, Any]] = {"manifest": manifest}
    for relative, digest in sorted(inventory.items()):
        path = str(relative)
        payload = _read_public_bytes(project_root, path)
        if sha256(payload) != digest:
            raise EvidenceProjectionError(f"U7 decision artifact differs from manifest: {path}")
        loaded[PurePosixPath(path).stem] = _read_json(project_root, path)
    documents = manifest.get("document_sha256")
    if not isinstance(documents, Mapping):
        raise EvidenceProjectionError("U7 decision document inventory is missing")
    for relative, digest in documents.items():
        if sha256(_read_public_bytes(project_root, str(relative))) != digest:
            raise EvidenceProjectionError(f"U7 decision document differs from manifest: {relative}")
    return loaded


def _single_record(project_root: Path, directory: str) -> tuple[str, dict[str, Any]] | None:
    root = project_root / Path(*PurePosixPath(directory).parts)
    if not root.exists():
        return None
    paths = sorted(path for path in root.glob("*.json") if path.is_file())
    if len(paths) != 1:
        raise EvidenceProjectionError(f"case artifact directory does not contain one record: {directory}")
    relative = paths[0].relative_to(project_root).as_posix()
    return relative, _read_json(project_root, relative)


def _chain_step(
    project_root: Path,
    *,
    stage: str,
    evidence_label: str,
    path: str,
    pointer: str,
    summary: str,
    record_id: str | None = None,
) -> dict[str, Any]:
    reference = _reference(project_root, path, pointer, record_id=record_id)
    return {
        "evidence_label": evidence_label,
        "evidence_ref": reference,
        "stage": stage,
        "summary": summary,
        "value": _resolve_public_reference(project_root, reference),
    }


def _case_projection(project_root: Path, case_id: str) -> dict[str, Any]:
    relative_root = f"{RUN_DIRECTORY.as_posix()}/output/assisted/{case_id}"
    events_path = f"{relative_root}/events/workflow.jsonl"
    events = _read_jsonl(project_root, events_path)
    if not events:
        raise EvidenceProjectionError(f"case trace is empty: {case_id}")
    recommendation_events = [
        event for event in events if event.get("payload", {}).get("event_type") == "RECOMMENDATION_RECORDED"
    ]
    if len(recommendation_events) != 1:
        raise EvidenceProjectionError(f"case recommendation does not resolve exactly once: {case_id}")
    recommendation_event = recommendation_events[0]
    recommendation_id = str(recommendation_event["record_id"])
    recorded = recommendation_event["payload"]["decision_or_effect"]
    decision = recorded["governed_recommendation"]["decision"]
    proposal = recorded["provider_proposal"]
    final_event = events[-1]
    final_state = str(final_event["payload"]["to_state"])

    snapshot_path = f"{relative_root}/source-snapshots/revision-0001.json"
    snapshot = _read_json(project_root, snapshot_path)
    if snapshot.get("payload", {}).get("case_id") != case_id:
        raise EvidenceProjectionError(f"case snapshot identity differs: {case_id}")

    optional = {
        name: _single_record(project_root, f"{relative_root}/{directory}")
        for name, directory in (
            ("authority", "approvals"),
            ("action", "actions"),
            ("adapter_receipt", "receipts"),
            ("verification", "verification"),
            ("communication", "communication"),
            ("closure", "closures"),
        )
    }
    verification = optional["verification"]
    if verification:
        classification = str(verification[1]["payload"]["classification"])
    elif final_state in {"CONTROL_STOPPED", "ACTION_RECOVERY"}:
        classification = final_state
    else:
        classification = str(decision["outcome_code"])
    bucket = OUTCOME_BUCKET.get(classification)
    if bucket is None:
        raise EvidenceProjectionError(f"case outcome is not in the frozen denominator contract: {case_id}")

    context_events = [event for event in events if event.get("payload", {}).get("event_type") == "CONTEXT_ASSEMBLED"]
    if len(context_events) != 1:
        raise EvidenceProjectionError(f"case context does not resolve exactly once: {case_id}")
    chain = [
        _chain_step(
            project_root,
            stage="source_revision",
            evidence_label="synthetic-generated-source-records",
            path=snapshot_path,
            pointer="/payload/revision_pin_sha256",
            summary="The generated source revision is pinned before recommendation.",
        ),
        _chain_step(
            project_root,
            stage="context",
            evidence_label="synthetic-system-context",
            path=events_path,
            record_id=str(context_events[0]["record_id"]),
            pointer="/payload/output_digest",
            summary="The application records one bounded context assembly.",
        ),
        _chain_step(
            project_root,
            stage="ai_recommendation",
            evidence_label="ai-generated-recorded-candidate-non-independent",
            path=events_path,
            record_id=recommendation_id,
            pointer="/payload/decision_or_effect/provider_proposal/candidate/proposed_action"
            if isinstance(proposal.get("candidate"), Mapping)
            else "/payload/decision_or_effect/provider_proposal/terminal_status",
            summary="The recorded provider candidate proposes; deterministic governance retains authority.",
        ),
        _chain_step(
            project_root,
            stage="governed_route",
            evidence_label="synthetic-deterministic-governance",
            path=events_path,
            record_id=recommendation_id,
            pointer="/payload/decision_or_effect/governed_recommendation/decision/authority_route",
            summary="Policy and evidence determine the route independently of provider authority.",
        ),
    ]
    optional_steps = (
        ("authority", "synthetic_approval", "synthetic-role-fixture-not-human-approval", "/payload/decision", "A simulated role event binds the exact payload."),
        ("action", "simulated_action", "simulated-local-action-no-live-system", "/payload/operation", "An allow-listed adapter can change generated local source state only."),
        ("adapter_receipt", "adapter_receipt", "simulated-receipt-not-verification", "/payload/status", "The adapter receipt is recorded but is not accepted as proof."),
        ("verification", "system_verification", "synthetic-system-verification-non-independent", "/payload/classification", "A structurally separate read-only verifier derives the postcondition."),
        ("communication", "communication", "synthetic-unsent-communication", "/payload/unsent", "The evidence-bound communication remains unsent."),
        ("closure", "workflow_closure", "synthetic-workflow-closure-not-customer-outcome", "/payload/state", "Workflow closure is not a realised customer outcome."),
    )
    for name, stage, label, pointer, summary in optional_steps:
        item = optional[name]
        if item:
            path, _record = item
            chain.append(
                _chain_step(
                    project_root,
                    stage=stage,
                    evidence_label=label,
                    path=path,
                    pointer=pointer,
                    summary=summary,
                )
            )
    chain.append(
        _chain_step(
            project_root,
            stage="trace_head",
            evidence_label="tamper-evident-local-trace",
            path=events_path,
            record_id=str(final_event["record_id"]),
            pointer="/payload/event_digest",
            summary="The case projection ends at the frozen trace head.",
        )
    )
    return {
        "action_label": "simulated" if optional["action"] else "not_applicable",
        "approval_label": "simulated" if optional["authority"] else "not_applicable",
        "case_id": case_id,
        "case_revision": snapshot["payload"]["case_revision"],
        "communication_label": "unsent" if optional["communication"] else "not_applicable",
        "evidence_chain": chain,
        "final_state": final_state,
        "human_reviewed": False,
        "no_realised_value": True,
        "outcome_bucket": bucket,
        "outcome_classification": classification,
        "provider_terminal_status": proposal["terminal_status"],
        "recommended_action": decision["proposed_action"],
        "route": decision["authority_route"],
        "synthetic": True,
        "validation_label": "non-independent",
    }


def _metric(
    project_root: Path,
    *,
    metric_id: str,
    label: str,
    path: str,
    pointer: str,
    denominator: int,
    denominator_pointer: str,
    denominator_path: str | None = None,
    unit: str,
    evidence_class: str,
) -> dict[str, Any]:
    value_ref = _reference(project_root, path, pointer)
    denominator_ref = _reference(
        project_root, denominator_path or path, denominator_pointer
    )
    return {
        "denominator": denominator,
        "denominator_evidence_ref": denominator_ref,
        "evidence_class": evidence_class,
        "evidence_ref": value_ref,
        "label": label,
        "metric_id": metric_id,
        "unit": unit,
        "value": _resolve_public_reference(project_root, value_ref),
    }


def _claim(project_root: Path, claim_id: str, statement: str, path: str, pointer: str) -> dict[str, Any]:
    reference = _reference(project_root, path, pointer)
    return {
        "claim_id": claim_id,
        "evidence_ref": reference,
        "statement": statement,
        "value": _resolve_public_reference(project_root, reference),
    }


def _validate_final_evidence_pack(pack: Mapping[str, Any], project_root: Path) -> None:
    """Fail closed if projection values or denominator boundaries drift."""

    if pack.get("schema_version") != "stage2-public-evidence-pack/v2":
        raise EvidenceProjectionError("final evidence schema is unsupported")
    required_fields = {
        "cases", "claim_boundary", "claims", "comparison", "decision",
        "economics", "enablement", "evidence_boundary", "generated_at",
        "human_measures", "maturity", "metrics", "outcomes", "pack_digest",
        "pack_id", "public_safe", "read_only", "schema_version",
        "source_bindings",
    }
    if set(pack) != required_fields:
        raise EvidenceProjectionError("final evidence pack fields mismatch")
    material = {key: value for key, value in pack.items() if key != "pack_digest"}
    if pack.get("pack_digest") != canonical_sha256(material):
        raise EvidenceProjectionError("final evidence pack digest mismatch")
    cases = pack.get("cases")
    if not isinstance(cases, list) or len(cases) != 36:
        raise EvidenceProjectionError("case denominator must contain exactly 36 cases")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, Mapping)]
    if len(case_ids) != 36 or len(set(case_ids)) != 36:
        raise EvidenceProjectionError("case denominator identity is not unique")
    claims = pack.get("claims")
    metrics = pack.get("metrics")
    if not isinstance(claims, list) or not claims:
        raise EvidenceProjectionError("claims are missing")
    if not isinstance(metrics, list) or not metrics:
        raise EvidenceProjectionError("metrics are missing")
    for claim in claims:
        if _resolve_public_reference(project_root, claim["evidence_ref"]) != claim.get("value"):
            raise EvidenceProjectionError(f"claim evidence mismatch: {claim.get('claim_id')}")
    for metric in metrics:
        if _resolve_public_reference(project_root, metric["evidence_ref"]) != metric.get("value"):
            raise EvidenceProjectionError(f"metric evidence mismatch: {metric.get('metric_id')}")
        if _resolve_public_reference(project_root, metric["denominator_evidence_ref"]) != metric.get("denominator"):
            raise EvidenceProjectionError(f"metric denominator mismatch: {metric.get('metric_id')}")
    for case in cases:
        chain = case.get("evidence_chain")
        if not isinstance(chain, list) or len(chain) < 3:
            raise EvidenceProjectionError(
                f"case evidence chain is incomplete: {case.get('case_id')}"
            )
        for step in chain:
            if _resolve_public_reference(project_root, step["evidence_ref"]) != step.get("value"):
                raise EvidenceProjectionError(f"case evidence mismatch: {case.get('case_id')}:{step.get('stage')}")
    expected_counts = pack.get("outcomes", {}).get("counts")
    expected_buckets = set(OUTCOME_BUCKET.values())
    if not isinstance(expected_counts, Mapping) or set(expected_counts) != expected_buckets:
        raise EvidenceProjectionError("case outcome counts are missing")
    outcome_refs = pack.get("outcomes", {}).get("evidence_refs")
    if not isinstance(outcome_refs, Mapping) or set(outcome_refs) != expected_buckets:
        raise EvidenceProjectionError("case outcome evidence references are missing")
    for bucket, reference in outcome_refs.items():
        if _resolve_public_reference(project_root, reference) != expected_counts[bucket]:
            raise EvidenceProjectionError(f"case outcome evidence mismatch: {bucket}")
    for case in cases:
        classification = str(case.get("outcome_classification"))
        if OUTCOME_BUCKET.get(classification) != case.get("outcome_bucket"):
            raise EvidenceProjectionError(
                f"case outcome classification mismatch: {case.get('case_id')}"
            )
        authoritative = _case_projection(project_root, str(case.get("case_id")))
        if (
            authoritative["outcome_classification"] != classification
            or authoritative["outcome_bucket"] != case.get("outcome_bucket")
        ):
            raise EvidenceProjectionError(
                f"case outcome source mismatch: {case.get('case_id')}"
            )
    observed = Counter(str(case["outcome_bucket"]) for case in cases)
    counts = {key: observed.get(key, 0) for key in expected_counts}
    if counts != expected_counts or sum(counts.values()) != 36:
        raise EvidenceProjectionError("case outcomes do not conserve the frozen denominator")


def validate_final_evidence_pack(
    pack: Mapping[str, Any], project_root: Path = PROJECT_ROOT
) -> None:
    """Fail closed if projection values or denominator boundaries drift."""

    root = Path(project_root).resolve()
    with _snapshot_scope(root):
        _validate_final_evidence_pack(pack, root)


def _build_final_evidence_pack(project_root: Path) -> dict[str, Any]:
    source = verify_and_replay_public_source(project_root)
    decision = _load_decision_pack(project_root)
    manifest = decision["manifest"]
    if manifest["source_score_sha256"] != sha256(source["score_bytes"]):
        raise EvidenceProjectionError("U7 decision pack is not bound to the replayed V6 score")

    pack_cases_path = (PACK_ROOT / "cases.jsonl").as_posix()
    case_records = _read_jsonl(project_root, pack_cases_path)
    case_ids = [str(record.get("payload", {}).get("case_id")) for record in case_records]
    if len(case_ids) != 36 or len(set(case_ids)) != 36:
        raise EvidenceProjectionError("frozen V6 case denominator is invalid")
    cases = [_case_projection(project_root, case_id) for case_id in case_ids]
    score_path = (RUN_DIRECTORY / "score.json").as_posix()
    pack_manifest_path = (PACK_ROOT / "manifest.json").as_posix()
    evaluation_path = (DECISION_ROOT / "evaluation-summary.json").as_posix()
    decision_output_path = (DECISION_ROOT / "decision-output.json").as_posix()
    economics_path = (DECISION_ROOT / "economics-summary.json").as_posix()
    readiness_path = (DECISION_ROOT / "enablement-readiness.json").as_posix()
    summary_path = (DECISION_ROOT / "summary.json").as_posix()
    score = source["score"]
    frozen_counts = score["denominator_conservation"]["mutually_exclusive_outcomes"]
    observed = Counter(case["outcome_bucket"] for case in cases)
    outcome_counts = {key: observed.get(key, 0) for key in frozen_counts}
    if outcome_counts != frozen_counts:
        raise EvidenceProjectionError("projected case outcomes differ from the scored denominator")

    denominator_path = "/oracle_case_count"
    metrics = [
        _metric(project_root, metric_id="recommendation_correctness", label="Recommendation correctness", path=score_path, pointer="/assisted/recommendation_correctness_basis_points", denominator=36, denominator_pointer="/assisted/metric_denominators/recommendation_correctness", unit="basis_points", evidence_class="synthetic-observed-creator-evaluated"),
        _metric(project_root, metric_id="safe_routing", label="Safe routing", path=score_path, pointer="/assisted/safe_routing_basis_points", denominator=3, denominator_pointer="/assisted/metric_denominators/safe_routing", unit="basis_points", evidence_class="synthetic-observed-creator-evaluated"),
        _metric(project_root, metric_id="approval_validity", label="Approval validity", path=score_path, pointer="/assisted/approval_validity_basis_points", denominator=6, denominator_pointer="/assisted/metric_denominators/approval_validity", unit="basis_points", evidence_class="synthetic-observed-simulated-approval"),
        _metric(project_root, metric_id="execution_commit", label="Eligible execution committed", path=score_path, pointer="/assisted/execution_commit_basis_points", denominator=18, denominator_pointer="/assisted/metric_denominators/execution_commit", unit="basis_points", evidence_class="synthetic-observed-simulated-action"),
        _metric(project_root, metric_id="verified_remedy", label="Verified operational remedy milestone", path=score_path, pointer="/assisted/verified_remedy_basis_points", denominator=15, denominator_pointer="/assisted/metric_denominators/verified_remedy", unit="basis_points", evidence_class="synthetic-system-verification-non-independent"),
        _metric(project_root, metric_id="recovery_success", label="Injected recovery success", path=score_path, pointer="/assisted/recovery_success_basis_points", denominator=3, denominator_pointer="/assisted/metric_denominators/recovery_success", unit="basis_points", evidence_class="synthetic-observed-fault-recovery"),
        _metric(project_root, metric_id="closure_integrity", label="Workflow closure integrity", path=score_path, pointer="/assisted/closure_integrity_basis_points", denominator=36, denominator_pointer="/assisted/metric_denominators/closure_integrity", unit="basis_points", evidence_class="synthetic-observed-workflow-control"),
        _metric(project_root, metric_id="unsupported_communication_facts", label="Unsupported communication facts", path=score_path, pointer="/assisted/unsupported_communication_facts", denominator=36, denominator_pointer=denominator_path, unit="count", evidence_class="synthetic-observed-unsent-communication"),
        _metric(project_root, metric_id="provider_cost_unknown", label="Provider attempts with unknown cost", path=score_path, pointer="/assisted/provider_cost_cents/unknown_attempts", denominator=36, denominator_pointer="/provider_attempt_count", denominator_path=pack_manifest_path, unit="attempt_count", evidence_class="metadata-not-observed"),
        _metric(project_root, metric_id="provider_latency_unknown", label="Provider attempts with unknown latency", path=score_path, pointer="/assisted/provider_latency_milliseconds/unknown_attempts", denominator=36, denominator_pointer="/provider_attempt_count", denominator_path=pack_manifest_path, unit="attempt_count", evidence_class="metadata-not-observed"),
    ]
    claims = [
        _claim(project_root, "S2-PUBLIC-CLAIM-DENOMINATOR", "The frozen creator-evaluated denominator contains 36 synthetic cases.", evaluation_path, "/assisted_case_count"),
        _claim(project_root, "S2-PUBLIC-CLAIM-EXECUTION", "Eligible simulated execution committed at 83.33% (15 of 18); this is not live reliability.", evaluation_path, "/execution_commit_display"),
        _claim(project_root, "S2-PUBLIC-CLAIM-PENDING", "Three cases remain pending because authoritative postconditions were absent.", evaluation_path, "/pending_cases"),
        _claim(project_root, "S2-PUBLIC-CLAIM-EXACT-ZERO", "No preregistered exact-zero violation was observed in the sealed V6 run.", evaluation_path, "/exact_zero_status"),
        _claim(project_root, "S2-PUBLIC-CLAIM-COST", "Provider cost was unavailable for every recorded attempt.", evaluation_path, "/provider_cost_unknown_attempts"),
        _claim(project_root, "S2-PUBLIC-CLAIM-LATENCY", "Provider latency was unavailable for every recorded attempt.", evaluation_path, "/provider_latency_unknown_attempts"),
        _claim(project_root, "S2-PUBLIC-CLAIM-ECONOMICS", "Hypothetical economics are inconclusive and do not support scale.", economics_path, "/status"),
        _claim(project_root, "S2-PUBLIC-CLAIM-DECISION", "The evidence-led next-gate recommendation is PAUSE.", decision_output_path, "/recommendation"),
        _claim(project_root, "S2-PUBLIC-CLAIM-ENABLEMENT", "Five role-readiness packages are designed but not human validated.", readiness_path, "/role_count"),
        _claim(project_root, "S2-PUBLIC-CLAIM-HUMAN", "Human evidence is not observed.", summary_path, "/human_evidence"),
    ]
    outcome_refs = {
        key: _reference(project_root, score_path, f"/denominator_conservation/mutually_exclusive_outcomes/{key}")
        for key in sorted(frozen_counts)
    }
    decision_output = decision["decision-output"]
    economics_summary = decision["economics-summary"]
    readiness = decision["enablement-readiness"]
    summary = decision["summary"]
    pack: dict[str, Any] = {
        "cases": cases,
        "claim_boundary": {
            "may_say": "A creator-evaluated local MVP executed a governed recovery workflow across 36 generated cases and produced reproducible synthetic evidence.",
            "must_not_say": "Humans adopted or improved with the workflow, real customers recovered, value was realised, independent validation occurred, or a pilot or production deployment is authorised.",
        },
        "claims": claims,
        "comparison": {
            "assisted_structural_work_events": score["assisted"]["structural_work_events"],
            "comparator_active_work_milliseconds": score["comparator"]["active_work_milliseconds"],
            "comparator_case_count": score["comparator"]["case_count"],
            "comparator_queue_transitions": score["comparator"]["queue_transitions"],
            "evidence_class": "synthetic-structural-comparator-not-human-observation",
            "limitation": "Structural counts and virtual-time assumptions are not measured human effort or realised savings.",
        },
        "decision": {
            "authorises_company_pilot": decision_output["authorises_company_pilot"],
            "evidence_ref": _reference(project_root, decision_output_path, "/recommendation"),
            "next_action": decision_output["next_action"],
            "recommendation": decision_output["recommendation"],
            "scope": decision_output["scope"],
        },
        "economics": {
            "evidence_class": economics_summary["evidence_class"],
            "non_ai_process_alternative_included": economics_summary["non_ai_process_alternative_included"],
            "scenario_class_stable": economics_summary["scenario_class_stable"],
            "status": economics_summary["status"],
            "supports_scale_next_experiment": economics_summary["supports_scale_next_experiment"],
            "value_status": economics_summary["value_status"],
        },
        "enablement": {
            "claim_boundary": readiness["claim_boundary"],
            "human_evidence_status": readiness["human_evidence_status"],
            "role_count": readiness["role_count"],
            "roles": readiness["roles"],
            "status": readiness["status"],
        },
        "evidence_boundary": {
            "human_evidence": "not_observed",
            "independent_validation": False,
            "live_customer_outcome": "not_observed",
            "realised_value": "not_observed",
            "simulated_actions": True,
            "simulated_approvals": True,
            "synthetic": True,
            "unsent_communications": True,
        },
        "generated_at": "2026-08-12T13:00:00Z",
        "human_measures": {name: "not_observed" for name in summary["not_observed"]},
        "maturity": {
            "evaluation_status": "creator-evaluated",
            "publication_authority": "Raul Rausell",
            "publication_status": "not_authorised_until_valid_signed_release_tag",
            "supported_ceiling": "local-mvp",
        },
        "metrics": metrics,
        "outcomes": {
            "counts": frozen_counts,
            "denominator": score["oracle_case_count"],
            "evidence_class": "synthetic-observed-creator-evaluated",
            "evidence_refs": outcome_refs,
            "safe_escalations_in_verified_remedy_numerator": score["assisted"]["safe_escalations_in_verified_remedy_numerator"],
        },
        "pack_id": PUBLIC_PACK_ID,
        "public_safe": True,
        "read_only": True,
        "schema_version": "stage2-public-evidence-pack/v2",
        "source_bindings": {
            "decision_pack_id": manifest["decision_pack_id"],
            "decision_pack_manifest_sha256": sha256(_read_public_bytes(project_root, (DECISION_ROOT / "manifest.json").as_posix())),
            "evaluation_pack_id": PACK_ID,
            "evaluation_pack_manifest_sha256": manifest["source_pack_manifest_sha256"],
            "evaluation_run_id": RUN_ID,
            "score_sha256": manifest["source_score_sha256"],
        },
    }
    pack["pack_digest"] = canonical_sha256(pack)
    _validate_final_evidence_pack(pack, project_root)
    return pack


def build_final_evidence_pack(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Build one deterministic, public-only portfolio projection in memory."""

    root = Path(project_root).resolve()
    with _snapshot_scope(root):
        return _build_final_evidence_pack(root)


def write_final_evidence_pack(
    project_root: Path = PROJECT_ROOT,
    output: Path | None = None,
    *,
    payload: bytes | None = None,
) -> bytes:
    project_root = Path(project_root).resolve()
    destination = output or project_root / "demo/data/evidence-pack.json"
    payload = payload or canonical_json_bytes(build_final_evidence_pack(project_root))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    project_root = arguments.project_root.resolve()
    output = arguments.output or project_root / "demo/data/evidence-pack.json"
    try:
        expected = canonical_json_bytes(build_final_evidence_pack(project_root))
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if arguments.verify:
        if not output.exists() or read_regular(output) != expected:
            print("ERROR: committed final evidence projection is stale", file=sys.stderr)
            return 1
        print(json.dumps({"path": output.relative_to(project_root).as_posix(), "status": "verified"}, sort_keys=True))
        return 0
    write_final_evidence_pack(project_root, output, payload=expected)
    print(json.dumps({"path": output.relative_to(project_root).as_posix(), "status": "written"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
