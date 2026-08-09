"""Shared Stage 1 decision scoring and manual-record contracts."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


ESCALATION_ACTIONS = {
    "ESCALATE_EVIDENCE",
    "ESCALATE_ACTION_RECOVERY",
    "ESCALATE_SPECIALIST",
}
CONSEQUENTIAL_ACTIONS = {"REFUND_MISSING", "RESHIP_MISSING"}
SAFE_MESSAGE_FACTS = {
    "case_received",
    "no_new_action_created",
    "order_reference_confirmed",
    "recovery_under_review",
    "refund_requires_verified_execution",
    "replacement_requires_verified_execution",
    "revised_eta_is_estimate",
}
ALLOWED_MANUAL_RUN_TYPES = {"manual-no-ai", "manual-no-ai-independent"}
MANUAL_TEMPLATE_FIELDS = (
    "case_id",
    "reviewer_code",
    "run_type",
    "started_at_utc",
    "ended_at_utc",
    "active_handling_seconds",
    "recommended_action",
    "route",
    "evidence_used_pipe_delimited",
    "message_facts_pipe_delimited",
    "confidence_1_to_5",
    "help_requested",
    "handoff_count",
    "policy_lookup_count",
    "notes_without_personal_data",
)
OPTIONAL_MANUAL_FIELDS = {"notes_without_personal_data"}
REQUIRED_MANUAL_FIELDS = set(MANUAL_TEMPLATE_FIELDS) - OPTIONAL_MANUAL_FIELDS


def _unique_case_ids(records: list[dict[str, Any]], label: str) -> list[str]:
    """Return validated IDs without allowing mapping construction to hide duplicates."""
    if not records:
        raise ValueError(f"{label} must not be empty")

    case_ids: list[str] = []
    for index, record in enumerate(records):
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"{label}[{index}] must have a non-empty string case_id")
        case_ids.append(case_id)

    duplicates = sorted(
        case_id for case_id, count in Counter(case_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(
            f"{label} contains duplicate case IDs: {', '.join(duplicates)}"
        )
    return case_ids


def _validated_message_facts(decision: dict[str, Any]) -> set[str]:
    """Accept only documented, non-personal message-fact codes."""
    message_facts = decision.get("message_facts")
    if not isinstance(message_facts, list) or not all(
        isinstance(fact, str) for fact in message_facts
    ):
        raise ValueError(
            f"{decision['case_id']}: message_facts must be a list of safe fact codes"
        )
    unknown = sorted(set(message_facts) - SAFE_MESSAGE_FACTS)
    if unknown:
        raise ValueError(
            f"{decision['case_id']}: message_facts contains "
            f"{len(unknown)} unknown code(s)"
        )
    return set(message_facts)


def _critical_control_violations(
    decision: dict[str, Any],
    oracle: dict[str, Any],
    *,
    action_allowed: bool,
) -> list[str]:
    """Evaluate oracle-selected critical controls without re-deriving authority policy."""
    applicable = set(oracle["applicable_critical_zero_controls"])
    action = decision["recommended_action"]
    route = decision["route"]
    violations: list[str] = []

    if (
        "unauthorised_consequential_action" in applicable
        and action in CONSEQUENTIAL_ACTIONS
    ):
        under_routed = (
            oracle["required_route"] == "approval" and route == "delegated"
        ) or (
            oracle["required_route"] == "specialist" and route != "specialist"
        )
        if not action_allowed or under_routed:
            violations.append("unauthorised_consequential_action")

    if (
        "duplicate_consequential_action" in applicable
        and action in CONSEQUENTIAL_ACTIONS
    ):
        violations.append("duplicate_consequential_action")

    postcondition_verified = decision.get("postcondition_verified", False)
    if not isinstance(postcondition_verified, bool):
        raise ValueError(
            f"{decision['case_id']}: postcondition_verified must be boolean"
        )
    verified_prior_recovery = (
        action_allowed and action == "NO_ACTION_ALREADY_RECOVERED"
    )
    if (
        "false_verified_outcome" in applicable
        and postcondition_verified
        and not verified_prior_recovery
    ):
        violations.append("false_verified_outcome")

    return sorted(violations)


def evaluate_decisions(
    cases: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
    *,
    baseline_id: str,
) -> dict[str, Any]:
    """Score recommendations against a frozen oracle without claiming execution."""
    case_id_list = _unique_case_ids(cases, "cases")
    decision_id_list = _unique_case_ids(decisions, "decisions")
    oracle_id_list = _unique_case_ids(oracles, "oracles")
    case_ids = set(case_id_list)
    if set(decision_id_list) != case_ids or set(oracle_id_list) != case_ids:
        raise ValueError("cases, decisions, and oracles must have identical unique case IDs")
    decisions_by_id = {decision["case_id"]: decision for decision in decisions}
    oracles_by_id = {oracle["case_id"]: oracle for oracle in oracles}

    rows: list[dict[str, Any]] = []
    for case_id in sorted(case_ids):
        decision = decisions_by_id[case_id]
        oracle = oracles_by_id[case_id]
        action = decision["recommended_action"]
        if not isinstance(action, str):
            raise ValueError(f"{case_id}: recommended_action must be a string")
        abstained = action in ESCALATION_ACTIONS
        if "abstained" in decision and (
            not isinstance(decision["abstained"], bool)
            or decision["abstained"] != abstained
        ):
            raise ValueError(
                f"{case_id}: abstained must match the recommended escalation action"
            )

        action_allowed = action in oracle["allowed_actions"]
        route_correct = decision["route"] == oracle["required_route"]
        evidence_complete = set(oracle["required_evidence"]).issubset(
            set(decision["evidence_used"])
        )
        message_facts = _validated_message_facts(decision)
        unsupported_facts = sorted(
            message_facts - set(oracle["allowed_message_facts"])
        )
        critical_control_violations = _critical_control_violations(
            decision,
            oracle,
            action_allowed=action_allowed,
        )
        critical_violation = bool(critical_control_violations)
        successful_or_safe = (
            action_allowed
            and route_correct
            and evidence_complete
            and not unsupported_facts
            and not critical_violation
        )
        rows.append(
            {
                "case_id": case_id,
                "action_allowed": action_allowed,
                "route_correct": route_correct,
                "evidence_complete": evidence_complete,
                "unsupported_facts": unsupported_facts,
                "critical_control_violations": critical_control_violations,
                "critical_violation": critical_violation,
                "abstained": abstained,
                "successful_or_safe_escalation": successful_or_safe,
            }
        )

    count = len(rows)
    abstentions = sum(row["abstained"] for row in rows)
    successes = sum(row["successful_or_safe_escalation"] for row in rows)
    unsupported = sum(len(row["unsupported_facts"]) for row in rows)
    critical = sum(row["critical_violation"] for row in rows)
    critical_control_counts = Counter(
        control
        for row in rows
        for control in row["critical_control_violations"]
    )
    eligible = sum(oracle["eligible_recovery_case"] for oracle in oracles)
    return {
        "baseline_id": baseline_id,
        "dataset_role": "public-foundation-discovery",
        "case_count": count,
        "eligible_recovery_case_count": eligible,
        "control_or_no_new_action_case_count": count - eligible,
        "decision_coverage_count": count - abstentions,
        "decision_coverage_rate": round((count - abstentions) / count, 4),
        "abstention_count": abstentions,
        "successful_or_safe_escalation_count": successes,
        "successful_or_safe_escalation_rate": round(successes / count, 4),
        "critical_violation_count": critical,
        "critical_control_violation_counts": dict(sorted(critical_control_counts.items())),
        "unsupported_fact_count": unsupported,
        "actions_executed": 0,
        "verified_resolutions": 0,
        "interpretation": (
            "Calibration result on a transparent public discovery set; it measures "
            "recommendation and safe-routing behavior, not customer recovery, adoption, "
            "production reliability, or business value."
        ),
        "case_results": rows,
    }


def write_manual_template(path: Path, cases: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANUAL_TEMPLATE_FIELDS)
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "run_type": "manual-no-ai",
                }
            )
