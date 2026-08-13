#!/usr/bin/env python3
"""Deterministic Q1-Q8 replay for the synthetic proposed current state."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from scripts.stage2_facts import derive_case_facts


COMPARATOR_SCHEMA = "stage2-current-state-result/v1"
SUMMARY_SCHEMA = "stage2-current-state-summary/v1"
COMPARATOR_VERSION = "1.0.0"
ASSUMPTION_VERSION = "stage2-current-state-assumptions/v1"

# These are explicitly hypothetical handling/wait inputs, never human observations.
ACTIVE_WORK_MILLISECONDS = {
    "Q1": 25_000,
    "Q2": 20_000,
    "Q3": 210_000,
    "Q4": 90_000,
    "Q5": 45_000,
    "Q6": 30_000,
    "Q7": 105_000,
    "Q8": 35_000,
}
DEPENDENCY_WAIT_MILLISECONDS = {
    "clarification": 1_800_000,
    "approval": 1_200_000,
    "customer_choice": 3_600_000,
    "action_recovery": 900_000,
}
HUMAN_MEASURES = {
    "adoption": "not_observed",
    "customer_satisfaction": "not_observed",
    "enablement_friction": "not_observed",
    "manual_review_time": "not_observed",
    "realised_savings": "not_observed",
    "retained_revenue": "not_observed",
    "trust": "not_observed",
}


def _deterministic_outcome(facts: Mapping[str, Any]) -> str:
    if facts["risk_stop_flags"]:
        return "CONTROL_STOPPED"
    if facts["has_unresolved_action"]:
        return "ACTION_RECOVERY_REQUIRED"
    if facts["has_source_conflict"] or not facts["all_sources_fresh"]:
        return "EVIDENCE_BLOCKED"
    if facts["duplicate_signal"] or facts["prior_remedy_covers_quantity"]:
        return "VERIFIED_NO_NEW_ACTION"
    if facts["customer_choice"] == "WAIT" and facts["reliable_eta_at"]:
        return "VERIFIED_WAIT_CONDITION"
    if facts["customer_choice"] is None:
        return "AWAITING_CUSTOMER_CHOICE"
    if (
        facts["order_value_cents"]
        > facts["authority"]["finance_review_order_value_cents"]
        or facts["affected_value_cents"]
        > facts["authority"]["workflow_owner_max_exposure_cents"]
    ):
        return "FINANCE_APPROVAL_REQUIRED"
    if facts["affected_value_cents"] > facts["authority"]["delegated_max_exposure_cents"]:
        return "WORKFLOW_OWNER_APPROVAL_REQUIRED"
    return "DELEGATED_ACTION_READY"


def replay_current_state(batch: Mapping[str, Any]) -> dict[str, Any]:
    """Traverse all documented queues and report structure separately from time."""

    facts = derive_case_facts(batch)
    outcome = _deterministic_outcome(facts)
    duplicate = facts["duplicate_signal"]
    needs_clarification = outcome in {"EVIDENCE_BLOCKED", "ACTION_RECOVERY_REQUIRED"}
    needs_approval = outcome in {
        "WORKFLOW_OWNER_APPROVAL_REQUIRED",
        "FINANCE_APPROVAL_REQUIRED",
    }
    needs_choice = outcome == "AWAITING_CUSTOMER_CHOICE"
    action_path = outcome in {
        "DELEGATED_ACTION_READY",
        "WORKFLOW_OWNER_APPROVAL_REQUIRED",
        "FINANCE_APPROVAL_REQUIRED",
    }
    wait_path = outcome == "VERIFIED_WAIT_CONDITION"
    no_new_action_path = outcome == "VERIFIED_NO_NEW_ACTION"

    def q7_event() -> str:
        if action_path:
            return "ACTION_AND_VERIFICATION"
        if wait_path:
            return "WAIT_CONDITION_CHECKED"
        if no_new_action_path:
            return "PRIOR_REMEDY_CHECKED"
        return "NO_ACTION_PERMITTED"

    queue_trace = [
        {"queue": "Q1", "event": "OMS_EXCEPTION_OPENED", "evidence_label": "synthetic-observed"},
        {"queue": "Q2", "event": "CRM_INTAKE_DEDUPLICATED" if duplicate else "CRM_INTAKE_CHECKED", "evidence_label": "synthetic-observed"},
        {"queue": "Q3", "event": "SEVEN_SOURCE_INVESTIGATION", "evidence_label": "synthetic-observed"},
        {"queue": "Q4", "event": "CLARIFICATION_REQUESTED" if needs_clarification else "CLARIFICATION_NOT_REQUIRED", "evidence_label": "synthetic-observed"},
        {"queue": "Q5", "event": "APPROVAL_PENDING" if needs_approval else "APPROVAL_NOT_REQUIRED", "evidence_label": "synthetic-observed"},
        {"queue": "Q6", "event": "CUSTOMER_CHOICE_PENDING" if needs_choice else "CHOICE_RESOLVED_OR_NOT_REQUIRED", "evidence_label": "synthetic-observed"},
        {"queue": "Q7", "event": q7_event(), "evidence_label": "synthetic-observed"},
        {"queue": "Q8", "event": "CLOSED_OR_REOPEN_CONTROL", "evidence_label": "synthetic-observed"},
    ]

    structural_work = {
        "intake_signals": 2 if duplicate else 1,
        "canonical_cases": 1,
        "deduplication_events": 1 if duplicate else 0,
        "source_opens": 7,
        "policy_lookups": 1,
        "queue_transitions": 8,
        "handoffs": int(needs_clarification) + int(needs_approval),
        "clarification_requests": int(needs_clarification),
        "approval_steps": int(needs_approval),
        "customer_choice_steps": int(needs_choice),
        "action_attempts": int(action_path),
        "verification_steps": int(action_path or wait_path or no_new_action_path),
        "reopens": int(needs_clarification),
    }

    active_work = sum(ACTIVE_WORK_MILLISECONDS.values())
    dependency_wait = 0
    wait_components = {
        "clarification_wait_milliseconds": 0,
        "approval_wait_milliseconds": 0,
        "customer_choice_wait_milliseconds": 0,
        "action_recovery_wait_milliseconds": 0,
    }
    if needs_clarification:
        wait_components["clarification_wait_milliseconds"] = DEPENDENCY_WAIT_MILLISECONDS["clarification"]
    if needs_approval:
        wait_components["approval_wait_milliseconds"] = DEPENDENCY_WAIT_MILLISECONDS["approval"]
    if needs_choice:
        wait_components["customer_choice_wait_milliseconds"] = DEPENDENCY_WAIT_MILLISECONDS["customer_choice"]
    if outcome == "ACTION_RECOVERY_REQUIRED":
        wait_components["action_recovery_wait_milliseconds"] = DEPENDENCY_WAIT_MILLISECONDS["action_recovery"]
    dependency_wait = sum(wait_components.values())

    return {
        "schema_version": COMPARATOR_SCHEMA,
        "comparator_version": COMPARATOR_VERSION,
        "case_id": facts["case_id"],
        "case_revision": facts["case_revision"],
        "revision_pin_sha256": facts["revision_pin_sha256"],
        "source_event_cut_sha256": facts["source_event_cut_sha256"],
        "ledger_head_digest": facts["ledger_head_digest"],
        "synthetic": True,
        "claim_boundary": "Proposed synthetic current-state replay; not human observation or realised value.",
        "structural_evidence_class": "synthetic-observed",
        "deterministic_task_evidence_class": "synthetic-observed",
        "deterministic_outcome": outcome,
        "derived_state": {
            "all_sources_fresh": facts["all_sources_fresh"],
            "has_source_conflict": facts["has_source_conflict"],
            "duplicate_signal": facts["duplicate_signal"],
            "active_chargeback": facts["active_chargeback"],
            "prior_remedy_covers_quantity": facts["prior_remedy_covers_quantity"],
        },
        "structural_work": structural_work,
        "queue_trace": queue_trace,
        "virtual_time": {
            "assumption_version": ASSUMPTION_VERSION,
            "duration_evidence_label": "hypothetical-impact",
            "active_work_milliseconds": active_work,
            "dependency_wait_milliseconds": dependency_wait,
            "total_elapsed_milliseconds": active_work + dependency_wait,
            "active_work_assumptions": dict(ACTIVE_WORK_MILLISECONDS),
            "dependency_wait_assumptions": wait_components,
        },
        "human_measures": dict(HUMAN_MEASURES),
    }


def summarise_current_state(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    material = list(rows)
    outcomes = Counter(row["deterministic_outcome"] for row in material)
    structural_totals = Counter()
    active_work = 0
    dependency_wait = 0
    for row in material:
        structural_totals.update(row["structural_work"])
        active_work += row["virtual_time"]["active_work_milliseconds"]
        dependency_wait += row["virtual_time"]["dependency_wait_milliseconds"]
    return {
        "schema_version": SUMMARY_SCHEMA,
        "comparator_version": COMPARATOR_VERSION,
        "synthetic": True,
        "claim_boundary": "Structural and deterministic task behaviour is synthetic-observed; all duration inputs are hypothetical; human outcomes are not observed.",
        "denominator": {"scheduled_cases": len(material), "excluded_cases": 0},
        "deterministic_outcomes": dict(sorted(outcomes.items())),
        "structural_evidence_class": "synthetic-observed",
        "structural_totals": dict(sorted(structural_totals.items())),
        "duration_evidence_class": "hypothetical-impact",
        "virtual_time_totals": {
            "active_work_milliseconds": active_work,
            "dependency_wait_milliseconds": dependency_wait,
            "total_elapsed_milliseconds": active_work + dependency_wait,
        },
        "human_measures": dict(HUMAN_MEASURES),
        "limitations": [
            "No human handling, queue behaviour, trust, adoption, satisfaction, savings, or customer outcome was observed.",
            "Virtual handling and waiting values are versioned hypothetical assumptions, not measurements.",
            "The comparator is a reproducible proposed process baseline, not an existing-company benchmark.",
        ],
    }
