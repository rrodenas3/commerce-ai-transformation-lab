"""Shared Stage 1 decision scoring and manual-record contracts."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.stage1_case_system import PUBLIC_DATASET_ROLE, write_utf8_lf


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
MANUAL_RUN_TYPE_BY_OPERATOR_ROLE = {
    "creator": "manual-no-ai",
    "independent": "manual-no-ai-independent",
}
RUN_MANIFEST_SCHEMA_VERSION = "1.1.0"
PUBLIC_ORACLE_EXPOSURE_STATUS = "public-oracle-available"
HELDOUT_RUN_MANIFEST_SCHEMA_VERSION = "2.1.0"
HELDOUT_ORACLE_EXPOSURE_PREPARED = "oracle-file-withheld-at-preparation"
HELDOUT_ORACLE_EXPOSURE_RELEASED = "oracle-file-released-after-record-freeze"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
REVIEWER_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")
INSTRUCTIONS_PUBLIC_PATH = "docs/STAGE1_MANUAL_BASELINE_PROTOCOL.md"
HELDOUT_INSTRUCTIONS_PUBLIC_PATH = "docs/STAGE1_HELDOUT_EVALUATION_PROTOCOL.md"
DEFAULT_ALLOWED_TOOLS = (
    "provided case pack",
    "provided policy",
    "calculator",
    "ordinary non-generative search",
)
DEFAULT_PROHIBITED_TOOLS = (
    "generative AI",
    "oracle consultation during handling",
    "deterministic answer consultation during handling",
    "creator coaching during an independent run",
)
HELDOUT_ALLOWED_TOOLS = (
    "provided held-out case pack",
    "provided policy",
    "provided operator guide",
    "plain-text editor with AI features disabled",
    "calculator",
    "system clock",
)
HELDOUT_PROHIBITED_TOOLS = (
    "generative AI",
    "oracle or oracle-generating code consultation during handling",
    "deterministic answer consultation during handling",
    "public repository consultation during handling",
    "creator coaching during an independent run",
)
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
        ) or (oracle["required_route"] == "specialist" and route != "specialist")
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
    verified_prior_recovery = action_allowed and action == "NO_ACTION_ALREADY_RECOVERED"
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
        raise ValueError(
            "cases, decisions, and oracles must have identical unique case IDs"
        )
    declared_roles = [case.get("dataset_role") for case in cases]
    if any(not isinstance(role, str) or not role for role in declared_roles):
        raise ValueError("cases must declare one non-empty dataset_role")
    dataset_roles = set(declared_roles)
    if len(dataset_roles) != 1:
        raise ValueError("cases must declare one non-empty dataset_role")
    dataset_role = next(iter(dataset_roles))
    cases_by_id = {case["case_id"]: case for case in cases}
    decisions_by_id = {decision["case_id"]: decision for decision in decisions}
    oracles_by_id = {oracle["case_id"]: oracle for oracle in oracles}

    rows: list[dict[str, Any]] = []
    for case_id in sorted(case_ids):
        case = cases_by_id[case_id]
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
        evidence_used = decision["evidence_used"]
        if not isinstance(evidence_used, list) or any(
            not isinstance(code, str) or not code for code in evidence_used
        ):
            raise ValueError(f"{case_id}: evidence_used must contain source codes")
        if set(evidence_used) - set(case["evidence"]["sources"]):
            raise ValueError(
                f"{case_id}: evidence_used contains an unknown source code"
            )
        evidence_complete = set(oracle["required_evidence"]).issubset(
            set(evidence_used)
        )
        message_facts = _validated_message_facts(decision)
        unsupported_facts = sorted(message_facts - set(oracle["allowed_message_facts"]))
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
        control for row in rows for control in row["critical_control_violations"]
    )
    eligible = sum(oracle["eligible_recovery_case"] for oracle in oracles)
    return {
        "baseline_id": baseline_id,
        "dataset_role": dataset_role,
        "case_count": count,
        "eligible_recovery_case_count": eligible,
        "control_or_no_new_action_case_count": count - eligible,
        "decision_coverage_count": count - abstentions,
        "decision_coverage_rate": round((count - abstentions) / count, 4),
        "abstention_count": abstentions,
        "successful_or_safe_escalation_count": successes,
        "successful_or_safe_escalation_rate": round(successes / count, 4),
        "critical_violation_count": critical,
        "critical_control_violation_counts": dict(
            sorted(critical_control_counts.items())
        ),
        "unsupported_fact_count": unsupported,
        "actions_executed": 0,
        "verified_resolutions": 0,
        "interpretation": (
            (
                "Calibration result on a transparent public discovery set; it measures "
                "recommendation and safe-routing behavior"
            )
            if dataset_role == PUBLIC_DATASET_ROLE
            else (
                "Synthetic held-out result; it measures recommendation and safe-routing "
                "behavior within the recorded exposure boundary"
            )
        )
        + (
            ", not customer recovery, adoption, production reliability, or business "
            "value."
        ),
        "case_results": rows,
    }


def write_manual_template(
    path: Path,
    cases: list[dict[str, Any]],
    *,
    reviewer_code: str = "",
    run_type: str = "manual-no-ai",
) -> None:
    if run_type not in ALLOWED_MANUAL_RUN_TYPES:
        raise ValueError("unsupported manual run_type")
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=MANUAL_TEMPLATE_FIELDS,
        lineterminator="\n",
    )
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                "case_id": case["case_id"],
                "reviewer_code": reviewer_code,
                "run_type": run_type,
            }
        )
    write_utf8_lf(path, handle.getvalue())
