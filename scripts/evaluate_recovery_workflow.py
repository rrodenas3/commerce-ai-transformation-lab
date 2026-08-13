#!/usr/bin/env python3
"""Independently replay sealed Stage 2 raw artifacts after oracle release.

The evaluated process emits no trusted score booleans.  This module reopens
each frozen U5 workspace, verifies its ledger and artifact inventory, replays
the Q1-Q8 comparator, and derives every numerator from those raw bytes.  The
oracle and schedule supply denominator membership only after output freeze.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.recovery_approval import AuthorityExpectation, validate_authority_event
from scripts.recovery_communication import create_unsent_communication
from scripts.recovery_policy import RecoveryPolicyAdapter
from scripts.recovery_services import RecoveryApplicationService, Stage2FactsPort
from scripts.recovery_state import WorkflowState
from scripts.recovery_verification import (
    FileAuthoritativeEffectReader,
    verify_authoritative_postcondition,
    verify_no_action_condition,
)
from scripts.recovery_workspace import FileRecoveryWorkspace
from scripts.stage2_contracts import (
    EVALUATOR_ONLY_FIELDS,
    EXACT_ZERO_CONTROL_IDS,
    canonical_json_bytes,
    canonical_sha256,
    decide_next_gate,
    load_evaluation_contract,
    load_canonical_json,
)
from scripts.stage2_current_state import replay_current_state
from scripts.stage2_facts import derive_case_facts


class EvaluationIntegrityError(ValueError):
    """Raised when sealed bytes cannot yield one complete trustworthy score."""


THRESHOLD_FIELDS = frozenset(
    {
        "critical_controls_maximum",
        "denominator_case_count",
        "minimum_approval_validity_basis_points",
        "minimum_closure_integrity_basis_points",
        "minimum_recommendation_correctness_basis_points",
        "minimum_recovery_success_basis_points",
        "minimum_safe_routing_basis_points",
        "minimum_verified_remedy_basis_points",
        "pack_id",
        "schema_version",
        "unsupported_communication_maximum",
    }
)
OUTCOME_BUCKET = {
    "VERIFIED_REMEDY": "verified_remedy",
    "VERIFIED_WAIT_CONDITION": "verified_wait",
    "VERIFIED_NO_NEW_ACTION": "verified_no_new_action",
    "CONTROL_STOPPED": "control_stop",
    "EVIDENCE_BLOCKED": "blocked",
    "VERIFICATION_FAILED": "failed",
    "ACTION_RECOVERY": "pending",
    "SAFE_ESCALATION": "escalated",
    "EXCLUDED_INTEGRITY_CONTROL": "excluded",
}
EXPECTED_COVERAGE_GROUPS = frozenset(
    {
        "adapter_verification", "control_stop", "delegated_reship", "evidence_conflict",
        "evidence_integrity", "finance_approval", "idempotent_recovery", "prior_remedy",
        "provider_safety", "reliable_eta_wait", "revision_invalidation", "workflow_owner_approval",
    }
)
TERMINAL_LEGAL_STATES = {
    "VERIFIED_REMEDY": WorkflowState.CLOSED,
    "VERIFIED_WAIT_CONDITION": WorkflowState.CLOSED,
    "VERIFIED_NO_NEW_ACTION": WorkflowState.CLOSED,
    "CONTROL_STOPPED": WorkflowState.CONTROL_STOPPED,
    "EVIDENCE_BLOCKED": WorkflowState.RECOMMENDATION_READY,
    "ACTION_RECOVERY": WorkflowState.ACTION_RECOVERY,
}
APPROVAL_ROUTES = {"WORKFLOW_OWNER_APPROVAL", "FINANCE_APPROVAL"}
ACTION_ROUTES = {"DELEGATED_DECISION", *APPROVAL_ROUTES}
PERSONAL_OR_SECRET = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\bsk-[A-Za-z0-9_-]{8,}|password|secret|token)",
    re.IGNORECASE,
)


class _FactsAdapter(Stage2FactsPort):
    def derive(self, source_batch: Mapping[str, Any]) -> Mapping[str, Any]:
        return derive_case_facts(source_batch)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = load_canonical_json(path.read_bytes())
    except Exception as error:
        raise EvaluationIntegrityError(f"{path}: canonical object unavailable") from error
    if not isinstance(value, dict):
        raise EvaluationIntegrityError(f"{path}: record must be an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise EvaluationIntegrityError(f"{path}: journal unavailable") from error
    for number, line in enumerate(payload.splitlines(keepends=True), 1):
        if not line.endswith(b"\n"):
            raise EvaluationIntegrityError(f"{path}:{number}: incomplete journal append")
        try:
            value = load_canonical_json(line)
        except Exception as error:
            raise EvaluationIntegrityError(f"{path}:{number}: invalid canonical record") from error
        if not isinstance(value, dict):
            raise EvaluationIntegrityError(f"{path}:{number}: record must be an object")
        rows.append(value)
    return rows


def _only_records(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    paths = sorted(root.glob("*.json"))
    if any(path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 for path in paths):
        raise EvaluationIntegrityError(f"{root}: record inventory contains link-like material")
    return [_read_json(path) for path in paths]


def _walk_keys(value: Any):
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _basis_points(numerator: int, denominator: int) -> int | None:
    return None if denominator == 0 else numerator * 10000 // denominator


def _validate_attempt_journal(case_root: Path) -> list[dict[str, Any]]:
    attempts = _read_jsonl(case_root / "action-attempts/journal.jsonl")
    markers = _only_records(case_root / "action-attempts/commits")
    if len(markers) != len(attempts):
        raise EvaluationIntegrityError("action-attempt marker denominator differs from journal")
    for sequence, (attempt, marker) in enumerate(zip(attempts, markers, strict=True), 1):
        if attempt.get("attempt_sequence") != sequence or marker != {
            "attempt_digest": canonical_sha256(attempt),
            "attempt_sequence": sequence,
            "schema_version": "stage2-action-attempt-commit/v1",
        }:
            raise EvaluationIntegrityError("action-attempt journal/marker binding is invalid")
    return attempts


def _independent_governed(
    workspace: FileRecoveryWorkspace,
    provider_proposal: Mapping[str, Any],
) -> dict[str, Any]:
    context = RecoveryApplicationService(workspace, _FactsAdapter()).inspect()
    policy = RecoveryPolicyAdapter()
    candidate = provider_proposal.get("candidate")
    if candidate is None:
        return {
            "candidate_accepted": False,
            "decision": policy.decide(context).to_dict(),
            "rejection_codes": [f"PROVIDER_{provider_proposal.get('terminal_status')}"],
        }
    if not isinstance(candidate, Mapping):
        raise EvaluationIntegrityError("provider candidate is not an object")
    try:
        return policy.evaluate(context, candidate).to_dict()
    except Exception as error:
        raise EvaluationIntegrityError("provider candidate cannot be independently governed") from error


def _expected_no_action_verification(
    workspace: FileRecoveryWorkspace,
    stored: Mapping[str, Any],
) -> dict[str, Any]:
    payload = stored.get("payload")
    if not isinstance(payload, Mapping):
        raise EvaluationIntegrityError("no-action verification payload is absent")
    classification = payload.get("classification")
    required = {
        "VERIFIED_WAIT_CONDITION": {"CARRIER", "CRM", "POLICY"},
        "VERIFIED_NO_NEW_ACTION": {"OMS", "PAYMENT", "POLICY"},
    }.get(str(classification))
    if required is None:
        raise EvaluationIntegrityError("direct no-action classification is invalid")
    source = workspace.load_source_batch()
    citations = tuple(
        item["record_id"]
        for item in source["payload"]["records"]
        if item["payload"]["source_name"] in required
    )
    try:
        return verify_no_action_condition(
            verification_id=str(stored.get("record_id")),
            case_id=workspace.replay().case_id,
            case_revision=workspace.replay().case_revision,
            classification=str(classification),
            milestone=str(payload.get("milestone")),
            cited_fact_ids=citations,
        )
    except Exception as error:
        raise EvaluationIntegrityError("direct no-action verification cannot be independently derived") from error


def _expected_communication(
    workspace: FileRecoveryWorkspace,
    verification: Mapping[str, Any],
    stored: Mapping[str, Any],
) -> dict[str, Any]:
    verification_payload = verification["payload"]
    classification = verification_payload["classification"]
    milestone = verification_payload["milestone"]
    fact_code = {
        ("VERIFIED_REMEDY", "REFUND_COMMITTED_EXACT"): "REFUND_COMPLETED",
        ("VERIFIED_REMEDY", "REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED"): "REPLACEMENT_OPERATIONAL_MILESTONE",
        ("VERIFIED_WAIT_CONDITION", "CURRENT_RELIABLE_ETA"): "ETA_ESTIMATE",
        ("VERIFIED_NO_NEW_ACTION", "PRIOR_REMEDY_COVERS_QUANTITY"): "NO_NEW_ACTION_REQUIRED",
    }.get((classification, milestone))
    if fact_code is None:
        raise EvaluationIntegrityError("verification cannot support an allow-listed communication")
    facts = derive_case_facts(workspace.load_source_batch())
    try:
        return create_unsent_communication(
            communication_id=str(stored.get("record_id")),
            case_id=workspace.replay().case_id,
            case_revision=workspace.replay().case_revision,
            classification=classification,
            milestone=milestone,
            fact_codes=(fact_code,),
            citations=(str(verification.get("record_id")),),
            estimate_at=facts.get("reliable_eta_at") if fact_code == "ETA_ESTIMATE" else None,
        )
    except Exception as error:
        raise EvaluationIntegrityError("communication cannot be independently derived") from error


def _derive_assisted(
    case_root: Path,
    case: Mapping[str, Any],
    schedule: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        workspace = FileRecoveryWorkspace(case_root)
        workspace.verify()
        projection = workspace.replay()
        events = workspace.read_events()
    except Exception as error:
        raise EvaluationIntegrityError(f"{case_root.name}: frozen U5 workspace replay failed") from error
    case_id = case["payload"]["case_id"]
    if projection.case_id != case_id or projection.case_revision != case["payload"]["case_revision"]:
        raise EvaluationIntegrityError(f"{case_id}: workspace identity differs from frozen case")
    binding = _read_json(case_root / "schedule-binding.json")
    if binding != {
        "case_id": case_id,
        "case_sha256": canonical_sha256(case),
        "effective_provider_attempt": binding.get("effective_provider_attempt"),
        "original_provider_attempt_sha256": canonical_sha256(attempt),
        "schedule_sha256": schedule["schedule_sha256"],
        "schema_version": "stage2-assisted-schedule-binding/v1",
    }:
        raise EvaluationIntegrityError(f"{case_id}: schedule/provider binding is invalid")
    effective_attempt = binding["effective_provider_attempt"]
    if not isinstance(effective_attempt, Mapping):
        raise EvaluationIntegrityError(f"{case_id}: effective provider attempt is invalid")
    expected_effective = dict(attempt)
    injected = {
        "PROMPT_INJECTION": "REJECTED",
        "PROVIDER_REFUSAL": "REFUSAL",
        "PROVIDER_TIMEOUT": "TIMEOUT",
    }.get(str(schedule["fault_code"]))
    if injected:
        expected_effective.update(
            terminal_status=injected,
            validation_result="NO_ACCEPTED_CANDIDATE",
            fallback_disposition="DETERMINISTIC_GOVERNED_FALLBACK",
            recorded_candidate=None,
            response_sha256=None,
        )
    if effective_attempt != expected_effective:
        raise EvaluationIntegrityError(f"{case_id}: effective attempt differs from the frozen fault schedule")
    recommendation_events = [
        event["payload"] for event in events if event["payload"]["event_type"] == "RECOMMENDATION_RECORDED"
    ]
    if len(recommendation_events) != 1:
        raise EvaluationIntegrityError(f"{case_id}: recommendation event denominator is not one")
    if set(_walk_keys(events)) & EVALUATOR_ONLY_FIELDS:
        raise EvaluationIntegrityError(f"{case_id}: evaluator-only fields leaked into raw workflow bytes")
    recorded = recommendation_events[0]["decision_or_effect"]
    proposal = recorded.get("provider_proposal")
    governed = recorded.get("governed_recommendation")
    expected_proposal = {
        "attempt_id": effective_attempt.get("attempt_id"),
        "candidate": effective_attempt.get("recorded_candidate"),
        "fallback_disposition": effective_attempt.get("fallback_disposition"),
        "terminal_status": effective_attempt.get("terminal_status"),
        "validation_result": effective_attempt.get("validation_result"),
    }
    if proposal != expected_proposal or governed != _independent_governed(workspace, proposal):
        raise EvaluationIntegrityError(f"{case_id}: recommendation is not bound to the frozen attempt/policy")
    decision = governed["decision"]
    candidate = proposal.get("candidate")
    candidate_was_accepted = bool(
        isinstance(candidate, Mapping) and governed.get("candidate_accepted") is True
    )
    recorded_recommendation = {
        "expected_action": (
            candidate.get("proposed_action")
            if candidate_was_accepted
            else decision.get("proposed_action")
        ),
        "expected_governed_outcome": None,
        "expected_route": (
            candidate.get("proposed_route")
            if candidate_was_accepted
            else decision.get("authority_route")
        ),
    }

    failures: set[str] = set()
    actions = _only_records(case_root / "actions")
    approvals = _only_records(case_root / "approvals")
    verifications = _only_records(case_root / "verification")
    communications = _only_records(case_root / "communication")
    closures = _only_records(case_root / "closures")
    attempts = _validate_attempt_journal(case_root)
    if len(actions) > 1:
        failures.add("DUPLICATE_ACTION")
    action = actions[0] if len(actions) == 1 else None
    authority_valid = False
    recomputed_verification: dict[str, Any] | None = None
    if action is not None:
        action_payload = action.get("payload")
        if not isinstance(action_payload, Mapping) or len(approvals) != 1:
            failures.add("UNAUTHORISED_ACTION")
        else:
            try:
                validate_authority_event(
                    approvals[0],
                    AuthorityExpectation(
                        case_id=case_id,
                        case_revision=action_payload["case_revision"],
                        ledger_head_digest=action_payload["ledger_head_digest"],
                        policy_id=action_payload["policy_id"],
                        policy_version=action_payload["policy_version"],
                        operation=action_payload["operation"],
                        payload_digest=action_payload["action_payload_digest"],
                        authority_route=action_payload["authority_route"],
                        recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                    ),
                    now="2026-08-11T14:00:00Z",
                )
                authority_valid = True
            except Exception:
                failures.add("UNAUTHORISED_ACTION")
        if len(verifications) == 1:
            receipts = _only_records(case_root / "receipts")
            try:
                recomputed_verification = verify_authoritative_postcondition(
                    action,
                    FileAuthoritativeEffectReader(case_root),
                    verification_id=verifications[0]["record_id"],
                    untrusted_receipt=receipts[0] if len(receipts) == 1 else None,
                )
            except Exception as error:
                raise EvaluationIntegrityError(f"{case_id}: authoritative effects cannot be replayed") from error
            if recomputed_verification != verifications[0]:
                failures.add("FALSE_VERIFICATION")
    elif approvals:
        failures.add("UNAUTHORISED_ACTION")

    if action is None and len(verifications) == 1:
        recomputed_verification = _expected_no_action_verification(workspace, verifications[0])
        if recomputed_verification != verifications[0]:
            failures.add("FALSE_VERIFICATION")
    if len(verifications) > 1:
        failures.add("FALSE_VERIFICATION")

    unsupported_facts = 0
    if recomputed_verification is not None:
        if len(communications) != 1:
            unsupported_facts += max(1, len(communications))
        else:
            expected_communication = _expected_communication(
                workspace, recomputed_verification, communications[0]
            )
            if communications[0] != expected_communication:
                unsupported_facts += 1
            message = str(communications[0].get("payload", {}).get("message_text", ""))
            if PERSONAL_OR_SECRET.search(message):
                failures.add("PERSONAL_OR_SECRET_DISCLOSURE")
        classification = recomputed_verification["payload"]["classification"]
    else:
        classification = {
            WorkflowState.CONTROL_STOPPED: "CONTROL_STOPPED",
            WorkflowState.ACTION_RECOVERY: "ACTION_RECOVERY",
        }.get(projection.state, decision.get("outcome_code"))
    recorded_recommendation["expected_governed_outcome"] = classification

    closure_legal = projection.state == TERMINAL_LEGAL_STATES.get(str(classification))
    if projection.state is WorkflowState.CLOSED:
        if len(closures) != 1 or len(communications) != 1 or recomputed_verification is None:
            closure_legal = False
        else:
            payload = closures[0].get("payload", {})
            closure_legal = closure_legal and all(
                (
                    payload.get("case_id") == case_id,
                    payload.get("case_revision") == projection.case_revision,
                    payload.get("verification_id") == recomputed_verification["record_id"],
                    payload.get("verification_digest") == canonical_sha256(recomputed_verification),
                    payload.get("communication_id") == communications[0]["record_id"],
                    payload.get("communication_digest") == canonical_sha256(communications[0]),
                    payload.get("final_ledger_head_digest") == projection.ledger_head_digest,
                    payload.get("state") == "CLOSED",
                )
            )

    recovery_reconciled = (
        len(attempts) == 3
        and [item.get("status") for item in attempts]
        == ["EFFECT_STARTED", "EFFECT_UNKNOWN", "RECONCILED_COMMITTED_EFFECT"]
        and attempts[-1].get("reconciled_before_retry") is True
        and classification == "VERIFIED_REMEDY"
    )
    source_record_count = len(workspace.load_source_batch()["payload"]["records"])
    reason_codes = set(decision.get("reason_codes", []))
    review_proxies = {
        "approval_steps": int(decision.get("authority_route") in APPROVAL_ROUTES),
        "conflicts_surfaced": int("MATERIAL_SOURCE_CONFLICT" in reason_codes),
        "evidence_items_inspected": source_record_count,
        "missing_evidence_blocks": len(decision.get("missing_or_stale_sources", [])),
        "override_edits": int(candidate is not None and governed.get("candidate_accepted") is not True),
        "recovery_transitions": sum(
            event["payload"]["event_type"]
            in {"ACTION_EFFECT_OUTCOME_UNKNOWN", "ACTION_EFFECT_RECONCILED"}
            for event in events
        ),
    }
    return {
        "action": decision.get("proposed_action"),
        "approval_valid": authority_valid,
        "case_id": case_id,
        "classification": classification,
        "closure_legal": closure_legal,
        "committed_effect": bool(action is not None and classification == "VERIFIED_REMEDY"),
        "critical_failures": sorted(failures),
        "provider_cost_cents": effective_attempt.get("cost_cents"),
        "provider_latency_milliseconds": effective_attempt.get("latency_milliseconds"),
        "recovery_reconciled": recovery_reconciled,
        "recorded_recommendation": recorded_recommendation,
        "review_proxies": review_proxies,
        "route": decision.get("authority_route"),
        "schedule_sha256": binding["schedule_sha256"],
        "structural_work_events": len(events),
        "unsupported_facts": unsupported_facts,
    }


def _derive_comparator(
    case_root: Path,
    case: Mapping[str, Any],
    schedule: Mapping[str, Any],
) -> dict[str, Any]:
    source = _read_json(case_root / "source-batch.json")
    result = _read_json(case_root / "result.json")
    queue = _read_jsonl(case_root / "queue-events.jsonl")
    binding = _read_json(case_root / "schedule-binding.json")
    if source != case or result != replay_current_state(source):
        raise EvaluationIntegrityError(f"{case_root.name}: comparator replay differs from frozen Q1-Q8 result")
    if queue != result.get("queue_trace") or [item.get("queue") for item in queue] != [f"Q{index}" for index in range(1, 9)]:
        raise EvaluationIntegrityError(f"{case_root.name}: comparator queue schedule is not exact Q1-Q8")
    if binding != {
        "case_id": case["payload"]["case_id"],
        "case_sha256": canonical_sha256(case),
        "schedule_sha256": schedule["schedule_sha256"],
        "schema_version": "stage2-comparator-schedule-binding/v1",
    }:
        raise EvaluationIntegrityError(f"{case_root.name}: comparator schedule binding is invalid")
    return {
        "active_work_milliseconds": result["virtual_time"]["active_work_milliseconds"],
        "case_id": result["case_id"],
        "deterministic_outcome": result["deterministic_outcome"],
        "queue_transitions": len(queue),
        "schedule_sha256": schedule["schedule_sha256"],
        "structural_work": result["structural_work"],
    }


def _variant_report(
    derived: list[Mapping[str, Any]],
    oracle_by_id: Mapping[str, Mapping[str, Any]],
    schedule_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    approval_ids = {case_id for case_id, item in oracle_by_id.items() if item["expected_route"] in APPROVAL_ROUTES}
    action_ids = {case_id for case_id, item in oracle_by_id.items() if item["expected_action"] in {"REFUND", "RESHIP"}}
    remedy_ids = {case_id for case_id, item in oracle_by_id.items() if item["expected_governed_outcome"] == "VERIFIED_REMEDY"}
    safe_ids = {
        case_id for case_id, item in oracle_by_id.items()
        if item["expected_governed_outcome"] in {"CONTROL_STOPPED", "SAFE_ESCALATION"}
    }
    recovery_ids = {
        case_id for case_id, item in schedule_by_id.items()
        if item["fault_code"] == "LOST_RECEIPT_AFTER_COMMIT"
    }
    by_id = {item["case_id"]: item for item in derived}
    critical = sorted({failure for item in derived for failure in item["critical_failures"]})
    proxy_formulas = {
        "approval_steps": "count of oracle-eligible approval routes",
        "conflicts_surfaced": "count of governed MATERIAL_SOURCE_CONFLICT reasons",
        "evidence_items_inspected": "count of frozen authoritative source records replayed",
        "missing_evidence_blocks": "count of governed missing-or-stale source codes",
        "override_edits": "count of present recorded candidates rejected by deterministic governance",
        "recovery_transitions": "count of raw unknown-effect or reconciliation workflow events",
    }
    return {
        "approval_validity_basis_points": _basis_points(sum(by_id[case_id]["approval_valid"] for case_id in approval_ids), len(approval_ids)),
        "closure_integrity_basis_points": _basis_points(sum(item["closure_legal"] for item in derived), len(derived)),
        "critical_control_failures": critical,
        "execution_commit_basis_points": _basis_points(sum(by_id[case_id]["committed_effect"] for case_id in action_ids), len(action_ids)),
        "metric_denominators": {
            "approval_validity": len(approval_ids),
            "closure_integrity": len(derived),
            "execution_commit": len(action_ids),
            "recommendation_correctness": len(derived),
            "recovery_success": len(recovery_ids),
            "safe_routing": len(safe_ids),
            "verified_remedy": len(remedy_ids),
        },
        "metric_evidence_class": "synthetic-observed-raw-artifact-replay-not-human-observation",
        "provider_cost_cents": {
            "known_attempts": sum(isinstance(item["provider_cost_cents"], int) and not isinstance(item["provider_cost_cents"], bool) for item in derived),
            "sum": sum(value for item in derived if isinstance((value := item["provider_cost_cents"]), int) and not isinstance(value, bool)),
            "unknown_attempts": sum(item["provider_cost_cents"] is None for item in derived),
        },
        "provider_latency_milliseconds": {
            "known_attempts": sum(isinstance(item["provider_latency_milliseconds"], int) and not isinstance(item["provider_latency_milliseconds"], bool) for item in derived),
            "sum": sum(value for item in derived if isinstance((value := item["provider_latency_milliseconds"]), int) and not isinstance(value, bool)),
            "unknown_attempts": sum(item["provider_latency_milliseconds"] is None for item in derived),
        },
        "recommendation_correctness_basis_points": _basis_points(
            sum(
                by_id[case_id]["recorded_recommendation"]
                == {
                    "expected_action": oracle_by_id[case_id]["expected_action"],
                    "expected_governed_outcome": oracle_by_id[case_id]["expected_governed_outcome"],
                    "expected_route": oracle_by_id[case_id]["expected_route"],
                }
                for case_id in oracle_by_id
            ),
            len(oracle_by_id),
        ),
        "recovery_success_basis_points": _basis_points(sum(by_id[case_id]["recovery_reconciled"] for case_id in recovery_ids), len(recovery_ids)),
        "review_burden_proxies": {
            key: {
                "denominator": len(derived),
                "evidence_label": "synthetic-structural-proxy-not-observed-human-burden",
                "formula": proxy_formulas[key],
                "sum": sum(int(item["review_proxies"][key]) for item in derived),
            }
            for key in sorted(proxy_formulas)
        },
        "safe_escalations_in_verified_remedy_numerator": 0,
        "safe_routing_basis_points": _basis_points(sum(by_id[case_id]["classification"] in {"CONTROL_STOPPED", "SAFE_ESCALATION"} for case_id in safe_ids), len(safe_ids)),
        "structural_work_events": sum(item["structural_work_events"] for item in derived),
        "unsupported_communication_facts": sum(item["unsupported_facts"] for item in derived),
        "verified_remedy_basis_points": _basis_points(sum(by_id[case_id]["classification"] == "VERIFIED_REMEDY" for case_id in remedy_ids), len(remedy_ids)),
    }


def apply_thresholds(
    assisted: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    *,
    incomplete_evidence: bool = False,
    pre_run_exposure: bool = False,
) -> tuple[str, dict[str, bool]]:
    if set(thresholds) != THRESHOLD_FIELDS or thresholds.get("schema_version") != "stage2-evaluation-thresholds/v1":
        raise EvaluationIntegrityError("threshold contract fields are not exact")
    failures = assisted.get("critical_control_failures")
    if not isinstance(failures, list):
        raise EvaluationIntegrityError("critical-control results are unavailable")
    gates = {
        "critical_controls": len(failures) <= thresholds["critical_controls_maximum"],
        "approval_validity": assisted["approval_validity_basis_points"] is not None and assisted["approval_validity_basis_points"] >= thresholds["minimum_approval_validity_basis_points"],
        "closure_integrity": assisted["closure_integrity_basis_points"] is not None and assisted["closure_integrity_basis_points"] >= thresholds["minimum_closure_integrity_basis_points"],
        "recommendation_correctness": assisted["recommendation_correctness_basis_points"] is not None and assisted["recommendation_correctness_basis_points"] >= thresholds["minimum_recommendation_correctness_basis_points"],
        "recovery_success": assisted["recovery_success_basis_points"] is not None and assisted["recovery_success_basis_points"] >= thresholds["minimum_recovery_success_basis_points"],
        "safe_routing": assisted["safe_routing_basis_points"] is not None and assisted["safe_routing_basis_points"] >= thresholds["minimum_safe_routing_basis_points"],
        "verified_remedy": assisted["verified_remedy_basis_points"] is not None and assisted["verified_remedy_basis_points"] >= thresholds["minimum_verified_remedy_basis_points"],
        "unsupported_communication": assisted["unsupported_communication_facts"] <= thresholds["unsupported_communication_maximum"],
    }
    contract = load_evaluation_contract(
        Path(__file__).resolve().parents[1] / "data/stage2/evaluation-contract.json"
    )
    decision = decide_next_gate(
        contract,
        exact_zero_failures=failures,
        incomplete_evidence=incomplete_evidence,
        pre_run_exposure=pre_run_exposure,
        quality_gate_passed=all(gates.values()),
        reliability_gate_passed=True,
        cost_gate_passed=True,
    )
    return decision, gates


def validate_pre_oracle_outputs(output_root: Path, pack_root: Path) -> dict[str, Any]:
    """Replay all public/raw bytes without consulting denominator labels."""

    cases = _read_jsonl(pack_root / "cases.jsonl")
    schedules = _read_jsonl(pack_root / "schedules.jsonl")
    attempts = _read_jsonl(pack_root / "provider-attempts.jsonl")
    case_ids = [str(item["payload"].get("case_id")) for item in cases]
    if (
        len(case_ids) != 36
        or len(set(case_ids)) != 36
        or case_ids != [item.get("case_id") for item in schedules]
        or case_ids != [item.get("case_id") for item in attempts]
    ):
        raise EvaluationIntegrityError("pre-oracle case/schedule/attempt denominator is invalid")
    if set(path.name for path in output_root.iterdir()) != {"assisted", "capability-probes.json", "comparator"}:
        raise EvaluationIntegrityError("raw output top-level inventory is not exact")
    assisted_dirs = sorted(path.name for path in (output_root / "assisted").iterdir() if path.is_dir())
    comparator_dirs = sorted(path.name for path in (output_root / "comparator").iterdir() if path.is_dir())
    if assisted_dirs != sorted(case_ids) or comparator_dirs != sorted(case_ids):
        raise EvaluationIntegrityError("pre-oracle assisted/comparator denominator differs")
    assisted = [
        _derive_assisted(output_root / "assisted" / case_id, case, schedule, attempt)
        for case_id, case, schedule, attempt in zip(case_ids, cases, schedules, attempts, strict=True)
    ]
    comparator = [
        _derive_comparator(output_root / "comparator" / case_id, case, schedule)
        for case_id, case, schedule in zip(case_ids, cases, schedules, strict=True)
    ]
    critical = sorted({failure for item in assisted for failure in item["critical_failures"]})
    result = {
        "case_count": 36,
        "comparator_case_count": len(comparator),
        "critical_control_failures": critical,
        "schema_version": "stage2-pre-oracle-raw-validation/v1",
        "unsupported_communication_facts": sum(item["unsupported_facts"] for item in assisted),
    }
    result["validation_digest"] = canonical_sha256(result)
    return result


def evaluate_raw_outputs(
    output_root: Path,
    pack_root: Path,
    oracle: list[Mapping[str, Any]],
) -> dict[str, Any]:
    cases = _read_jsonl(pack_root / "cases.jsonl")
    schedules = _read_jsonl(pack_root / "schedules.jsonl")
    attempts = _read_jsonl(pack_root / "provider-attempts.jsonl")
    thresholds = _read_json(pack_root / "thresholds.json")
    if len(oracle) != 36 or len(cases) != 36 or len(schedules) != 36 or len(attempts) != 36:
        raise EvaluationIntegrityError("frozen denominator must contain exactly 36 cases")
    oracle_ids = [str(item.get("case_id")) for item in oracle]
    case_ids = [str(item["payload"].get("case_id")) for item in cases]
    if len(set(oracle_ids)) != 36 or oracle_ids != case_ids:
        raise EvaluationIntegrityError("oracle/case identity or order differs from frozen denominator")
    coverage_counts = Counter(str(item.get("family_id")) for item in oracle)
    if set(coverage_counts) != EXPECTED_COVERAGE_GROUPS or set(coverage_counts.values()) != {3}:
        raise EvaluationIntegrityError("released oracle named coverage mapping is not the frozen 12 x 3 design")
    if case_ids != [item.get("case_id") for item in schedules] or case_ids != [item.get("case_id") for item in attempts]:
        raise EvaluationIntegrityError("case/schedule/attempt order differs")
    if thresholds.get("denominator_case_count") != 36:
        raise EvaluationIntegrityError("threshold denominator is not 36")
    assisted_dirs = sorted(path.name for path in (output_root / "assisted").iterdir() if path.is_dir())
    comparator_dirs = sorted(path.name for path in (output_root / "comparator").iterdir() if path.is_dir())
    if assisted_dirs != sorted(case_ids) or comparator_dirs != sorted(case_ids):
        raise EvaluationIntegrityError("assisted/comparator raw-tree denominator differs")
    assisted = [
        _derive_assisted(output_root / "assisted" / case_id, case, schedule, attempt)
        for case_id, case, schedule, attempt in zip(case_ids, cases, schedules, attempts, strict=True)
    ]
    comparator = [
        _derive_comparator(output_root / "comparator" / case_id, case, schedule)
        for case_id, case, schedule in zip(case_ids, cases, schedules, strict=True)
    ]
    oracle_by_id = {str(item["case_id"]): item for item in oracle}
    schedule_by_id = {str(item["case_id"]): item for item in schedules}
    for item in assisted:
        expected = oracle_by_id[item["case_id"]]
        if item["schedule_sha256"] != expected["schedule_sha256"]:
            raise EvaluationIntegrityError("assisted schedule differs from released oracle binding")
    counts = Counter(OUTCOME_BUCKET.get(str(item["classification"]), "unknown") for item in assisted)
    if counts.get("unknown") or sum(counts.values()) != 36:
        raise EvaluationIntegrityError("outcome denominator is not conserved")
    assisted_report = _variant_report(assisted, oracle_by_id, schedule_by_id)
    decision, gates = apply_thresholds(assisted_report, thresholds)
    failures = assisted_report["critical_control_failures"]
    comparator_report = {
        "active_work_milliseconds": sum(item["active_work_milliseconds"] for item in comparator),
        "case_count": len(comparator),
        "queue_transitions": sum(item["queue_transitions"] for item in comparator),
        "structural_work": {
            key: sum(int(item["structural_work"].get(key, 0)) for item in comparator)
            for key in sorted({key for item in comparator for key in item["structural_work"]})
        },
    }
    report = {
        "assisted": assisted_report,
        "comparator": comparator_report,
        "creator_evaluated": True,
        "decision_input": decision,
        "denominator_conservation": {
            "all_scheduled_cases": 36,
            "mutually_exclusive_outcomes": {key: counts.get(key, 0) for key in sorted(set(OUTCOME_BUCKET.values()))},
        },
        "evidence_class": "synthetic-observed-creator-evaluated",
        "exact_zero": {"failures": failures, "status": "failed" if failures else "passed"},
        "human_evidence": "not_observed",
        "hypothetical_economics": {
            "evidence_class": "not_evaluated",
            "status": "deferred_to_U7",
        },
        "maturity_ceiling": "local-mvp",
        "oracle_case_count": 36,
        "schema_version": "stage2-layered-evaluation/v2",
        "threshold_gates": gates,
        "thresholds_sha256": canonical_sha256(thresholds),
    }
    report["report_digest"] = canonical_sha256(report)
    return report


def evaluate_run(run_root: Path, pack_root: Path | None = None) -> dict[str, Any]:
    oracle_release = _read_json(run_root / "oracle-release.json")
    oracle = _read_jsonl(run_root / str(oracle_release["oracle_artifact"]))
    resolved_pack = pack_root or Path(_read_json(run_root / "preparation.json")["input_root_identity"])
    return evaluate_raw_outputs(run_root / "output", resolved_pack, oracle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score or verify one sealed Stage 2 raw evaluation run.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate_run(args.run_root, args.pack_root)
    target = args.run_root / "score.json"
    expected = canonical_json_bytes(report)
    if args.verify:
        if not target.is_file() or target.read_bytes() != expected:
            print("ERROR: committed score differs from sealed bytes", file=sys.stderr)
            return 1
        print(json.dumps({"case_count": 36, "status": "verified"}, sort_keys=True))
        return 0
    if target.exists():
        raise SystemExit("score is write-once; existing score cannot be replaced")
    target.write_bytes(expected)
    print(json.dumps({"decision_input": report["decision_input"], "status": "scored"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
