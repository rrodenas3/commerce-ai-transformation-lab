#!/usr/bin/env python3
"""Generate the explicitly AI-assisted Stage 1 multi-persona practice dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_case_system import read_jsonl
from scripts.stage1_heldout import validate_heldout_invalidation


SCHEMA_VERSION = "1.0.0"
PRACTICE_ID = "SCC-01-MP-V1"
GENERATED_AT_UTC = "2026-08-11T04:05:13Z"
EVIDENCE_CLASS = "ai-assisted-synthetic-practice"
INDEPENDENCE_STATUS = "simulated-lenses-not-independent-reviewers"
TIMING_STATUS = "not-human-observed"
CLAIM_BOUNDARY = (
    "Shows policy application and adversarial design practice; does not show human "
    "performance, independent review, adoption, execution, or business outcomes."
)
PRIVATE_ORACLE_PUBLICATION_STATUS = (
    "performed-before-publication-against-the-pre-exposure-commitment; "
    "not-repeated-by-public-byte-verification"
)
OUTPUT_RELATIVE = Path("data/stage1/practice/multi-persona-v1")
CASES_RELATIVE = Path("data/stage1/heldout/v2/cases.jsonl")
GUIDE_RELATIVE = Path("data/stage1/heldout/v2/operator-guide.json")
PUBLIC_MANIFEST_RELATIVE = Path("data/stage1/heldout/v2/manifest.json")
PRIVATE_ORACLE_RELATIVE = Path("artifacts/private/stage1/heldout/v2/oracle.jsonl")
RUN_RELATIVE = Path(
    "data/stage1/heldout/runs/scc-01-heldout-v2-creator-001"
)

PERSONAS = (
    {
        "persona_code": "P-CUST",
        "persona_name": "Customer Recovery Advocate",
        "operating_role": "customer_recovery_specialist",
        "primary_objective": "Restore customer clarity and momentum with the least avoidable friction.",
        "privileged_signal": "Customer preference, contact burden, and message certainty.",
        "characteristic_question": "What can we safely tell and do for the customer now?",
        "failure_tendency": "May promise or remedy too early when empathy outruns evidence and authority.",
        "success_measure": "Supported recovery decision with clear, non-promissory communication.",
        "decision_rights": "Delegated low-risk recommendations; no authority to override evidence, risk, or approval stops.",
    },
    {
        "persona_code": "P-OPS",
        "persona_name": "Fulfilment Operations Coordinator",
        "operating_role": "fulfilment_operations_coordinator",
        "primary_objective": "Maintain case flow while reconciling OMS, WMS, carrier, and inventory evidence.",
        "privileged_signal": "Source availability, freshness, conflict, and operational handoffs.",
        "characteristic_question": "Is the operational evidence coherent enough to move the case?",
        "failure_tendency": "May optimise queue flow or stock availability before checking customer preference and authority.",
        "success_measure": "Evidence-complete routing with low rework and no unsupported recovery.",
        "decision_rights": "Owns evidence escalation; cannot approve high-value remedies or resolve risk stops.",
    },
    {
        "persona_code": "P-WORK",
        "persona_name": "Workflow Owner",
        "operating_role": "workflow_owner",
        "primary_objective": "Balance customer outcome, flow, unit economics, and explicit decision rights.",
        "privileged_signal": "Authority thresholds, repeat recovery, and end-to-end workflow performance.",
        "characteristic_question": "Who owns this decision, and what downstream cost or queue will it create?",
        "failure_tendency": "May let throughput or economics create a quiet policy override.",
        "success_measure": "Correct owner, bounded autonomy, and a defensible scale-or-stop learning signal.",
        "decision_rights": "Approves mid-band or repeat recoveries; escalates finance, risk, evidence, and action-state exceptions.",
    },
    {
        "persona_code": "P-TECH",
        "persona_name": "Technical Reliability Owner",
        "operating_role": "technical_owner",
        "primary_objective": "Prevent duplicate actions and distinguish recommendations from verified postconditions.",
        "privileged_signal": "Prior-action state, action IDs, idempotency, receipts, and authoritative postconditions.",
        "characteristic_question": "Did the action actually complete, and can we prove its postcondition?",
        "failure_tendency": "May trust a platform status label or over-focus on system state at the expense of the whole journey.",
        "success_measure": "Zero duplicate consequential actions and reconstructable verified state transitions.",
        "decision_rights": "Owns action-recovery escalation; does not select customer remedy outside policy.",
    },
    {
        "persona_code": "P-RISK",
        "persona_name": "Policy and Risk Owner",
        "operating_role": "policy_and_risk_owner",
        "primary_objective": "Stop unsafe, fraudulent, privacy-sensitive, or chargeback-conflicted action.",
        "privileged_signal": "Risk flags, chargeback state, policy constraints, and consequential-action exposure.",
        "characteristic_question": "What must stop before speed, cost, or empathy can influence the decision?",
        "failure_tendency": "May convert ordinary ambiguity into excessive specialist escalation.",
        "success_measure": "Exact-zero critical violations with proportionate, explainable stops.",
        "decision_rights": "Owns specialist risk stops; cannot use general caution as a substitute for evidence routing.",
    },
)

PERSONA_BY_CODE = {persona["persona_code"]: persona for persona in PERSONAS}

ADVERSARIAL_OVERRIDES = {
    "SCC-01-HO2-001": ("REFUND_MISSING", "delegated", "empathy-before-postcondition", "The customer has already waited; issue the missing-value refund now."),
    "SCC-01-HO2-002": ("REFUND_MISSING", "delegated", "queue-speed-over-authority", "The evidence supports a refund, so keep it in the frontline queue."),
    "SCC-01-HO2-005": ("ESCALATE_SPECIALIST", "specialist", "value-treated-as-risk", "The exposure is material; stop it with a specialist rather than approve a refund."),
    "SCC-01-HO2-008": ("REFUND_MISSING", "delegated", "throughput-over-evidence", "The missing quantity is clear enough; avoid another evidence handoff."),
    "SCC-01-HO2-010": ("ESCALATE_SPECIALIST", "specialist", "generic-caution-over-specific-route", "Stale inventory makes the case risky, so send it to the broad risk queue."),
    "SCC-01-HO2-011": ("REFUND_MISSING", "delegated", "customer-relief-over-safety-stop", "A prompt refund is the cleanest way to reduce further customer harm."),
    "SCC-01-HO2-013": ("REFUND_MISSING", "delegated", "commercial-recovery-over-fraud-stop", "Resolve the missing value now and investigate the fraud signal afterward."),
    "SCC-01-HO2-016": ("REFUND_MISSING", "delegated", "correct-remedy-wrong-authority", "The remedy is obvious; asking finance will only prolong the recovery."),
    "SCC-01-HO2-017": ("RESHIP_MISSING", "delegated", "item-value-tunnel-vision", "The missing item is low value and stock exists, so the frontline can reship."),
    "SCC-01-HO2-019": ("NO_ACTION_ALREADY_RECOVERED", "delegated", "status-label-trust", "The prior refund says VERIFIED; close the case without another action."),
    "SCC-01-HO2-020": ("ESCALATE_SPECIALIST", "specialist", "duplicate-signal-overreaction", "A repeated signal may hide abuse; let a specialist investigate it."),
    "SCC-01-HO2-022": ("RESHIP_MISSING", "delegated", "stock-availability-over-preference", "Stock is reservable, so a reship is faster than refunding."),
    "SCC-01-HO2-023": ("RESHIP_MISSING", "approval", "new-remedy-over-action-recovery", "The previous reship is unverified; approve a replacement reship to restore momentum."),
    "SCC-01-HO2-024": ("NO_ACTION_DUPLICATE_SIGNAL", "delegated", "lower-priority-rule-first", "The trigger is a duplicate, so suppress it without investigating the pending refund."),
    "SCC-01-HO2-027": ("RESHIP_MISSING", "approval", "inclusive-threshold-error", "Treat the exact threshold as approval territory to be conservative."),
    "SCC-01-HO2-029": ("REFUND_MISSING", "delegated", "repeat-recovery-missed", "The affected value is small enough for delegated refund handling."),
    "SCC-01-HO2-030": ("ESCALATE_SPECIALIST", "specialist", "conflict-treated-as-risk", "Conflicting delivery evidence is broadly risky; route it to Policy and Risk."),
    "SCC-01-HO2-031": ("REFUND_MISSING", "delegated", "customer-remedy-over-chargeback-stop", "Refund the missing value now so the customer is not forced to wait for dispute handling."),
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_bytes(fieldnames: list[str], rows: list[dict[str, Any]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _authoritative_postcondition_verified(case: dict[str, Any]) -> bool:
    prior = case["history"]["prior_action"]
    if not prior or prior["status"] != "VERIFIED" or case["evidence"]["source_conflict"]:
        return False
    if prior["type"] == "REFUND_MISSING":
        source = case["evidence"]["sources"]["PAYMENT"]
        return bool(source["available"] and source["fresh"] and case["payment"]["refunded_cents"] >= case["order"]["affected_value_cents"])
    if prior["type"] == "RESHIP_MISSING":
        source = case["evidence"]["sources"]["INVENTORY"]
        reservation = case["inventory"]["replacement_reservation"]
        return bool(
            source["available"]
            and source["fresh"]
            and isinstance(reservation, dict)
            and reservation.get("action_id") == prior["action_id"]
            and reservation.get("status") == "VERIFIED"
            and reservation.get("reserved_qty", 0) >= case["order"]["remaining_qty"]
        )
    return False


def _authority(case: dict[str, Any]) -> tuple[str, str]:
    if case["order"]["affected_value_cents"] > 10000 or case["order"]["order_value_cents"] > 50000:
        return "approval", "finance_duty_approver"
    if case["order"]["affected_value_cents"] > 2500 or case["history"]["repeat_recovery"]:
        return "approval", "workflow_owner"
    return "delegated", "customer_recovery_specialist"


def derive_governed_decision(case: dict[str, Any]) -> dict[str, Any]:
    route, owner = _authority(case)
    sources = case["evidence"]["sources"]
    unavailable = sorted(name for name, record in sources.items() if not record["available"])
    stale = sorted(name for name, record in sources.items() if record["available"] and not record["fresh"])
    risks = sorted(set(case["risk_flags"]) | ({"active_chargeback"} if case["payment"]["active_chargeback"] else set()))
    prior = case["history"]["prior_action"]
    rationale: list[str]
    if risks:
        action, route, owner = "ESCALATE_SPECIALIST", "specialist", "policy_and_risk_owner"
        rationale = [f"risk_stop:{risk}" for risk in risks]
    elif prior and _authoritative_postcondition_verified(case):
        action, route, owner = "NO_ACTION_ALREADY_RECOVERED", "delegated", "customer_recovery_specialist"
        rationale = ["verified_prior_action"]
    elif prior and prior["status"] in {"VERIFIED", "PENDING", "FAILED_SAFE", "UNVERIFIED"}:
        action, route, owner = "ESCALATE_ACTION_RECOVERY", "specialist", "technical_owner"
        rationale = ["prior_action:verified_without_authoritative_postcondition" if prior["status"] == "VERIFIED" else f"prior_action:{prior['status'].lower()}"]
    elif case["evidence"]["source_conflict"] or unavailable or stale:
        action, route, owner = "ESCALATE_EVIDENCE", "specialist", "fulfilment_operations_coordinator"
        rationale = (["source_conflict"] if case["evidence"]["source_conflict"] else []) + [f"missing:{name}" for name in unavailable] + [f"stale:{name}" for name in stale]
    elif case["trigger"]["duplicate"]:
        action, route, owner = "NO_ACTION_DUPLICATE_SIGNAL", "delegated", "customer_recovery_specialist"
        rationale = ["duplicate_signal_without_new_state"]
    elif case["carrier"]["status"] == "IN_TRANSIT" and case["customer"]["preference"] == "wait":
        action, route, owner = "WAIT_VERIFIED_ETA", "delegated", "customer_recovery_specialist"
        rationale = ["customer_prefers_wait", "reliable_revised_eta"]
    elif case["inventory"]["reservable"] and case["customer"]["preference"] != "refund_missing":
        action = "RESHIP_MISSING"
        rationale = ["missing_quantity_confirmed", "replacement_stock_reservable"]
    else:
        action = "REFUND_MISSING"
        rationale = ["missing_quantity_confirmed", "replacement_not_selected_or_unavailable"]

    evidence = ["OMS", "WMS", "CARRIER", "POLICY"]
    if action in {"RESHIP_MISSING", "REFUND_MISSING"}:
        evidence += ["INVENTORY", "PAYMENT", "CRM"]
    elif action.startswith("NO_ACTION"):
        evidence += ["CRM", "PAYMENT"]
    if action.startswith("ESCALATE"):
        evidence = sorted(name for name, record in sources.items() if record["available"])

    message_facts = ["case_received", "order_reference_confirmed", "recovery_under_review"]
    message_facts += {
        "WAIT_VERIFIED_ETA": ["revised_eta_is_estimate"],
        "RESHIP_MISSING": ["replacement_requires_verified_execution"],
        "REFUND_MISSING": ["refund_requires_verified_execution"],
        "NO_ACTION_ALREADY_RECOVERED": ["no_new_action_created"],
        "NO_ACTION_DUPLICATE_SIGNAL": ["no_new_action_created"],
    }.get(action, [])
    return {
        "action": action,
        "route": route,
        "decision_owner": owner,
        "required_evidence": sorted(set(evidence)),
        "message_facts": sorted(message_facts),
        "rationale_codes": sorted(rationale),
    }


def _scenario_category(action: str) -> str:
    return {
        "ESCALATE_SPECIALIST": "risk-or-chargeback-stop",
        "NO_ACTION_ALREADY_RECOVERED": "verified-prior-recovery",
        "ESCALATE_ACTION_RECOVERY": "unresolved-action-state",
        "ESCALATE_EVIDENCE": "evidence-integrity-stop",
        "NO_ACTION_DUPLICATE_SIGNAL": "duplicate-signal-control",
        "WAIT_VERIFIED_ETA": "delayed-with-customer-wait-preference",
        "RESHIP_MISSING": "partial-fulfilment-with-stock",
        "REFUND_MISSING": "partial-fulfilment-refund",
    }[action]


def _scenario_summary(case: dict[str, Any], final: dict[str, Any]) -> str:
    amount = case["order"]["affected_value_cents"] / 100
    order_value = case["order"]["order_value_cents"] / 100
    details = [f"EUR {amount:.2f} affected on EUR {order_value:.2f} order", f"carrier {case['carrier']['status']}", f"preference {case['customer']['preference']}"]
    if case["history"]["repeat_recovery"]:
        details.append("repeat recovery")
    if case["history"]["prior_action"]:
        prior = case["history"]["prior_action"]
        details.append(f"prior {prior['type']} is {prior['status']}")
    if case["trigger"]["duplicate"]:
        details.append("duplicate trigger")
    if case["risk_flags"]:
        details.append("risk " + "/".join(case["risk_flags"]))
    if case["payment"]["active_chargeback"]:
        details.append("active chargeback")
    if case["evidence"]["source_conflict"]:
        details.append("source conflict")
    bad = [f"{name} {'missing' if not state['available'] else 'stale'}" for name, state in case["evidence"]["sources"].items() if not state["available"] or not state["fresh"]]
    details.extend(bad)
    details.append(f"policy result {final['action']} via {final['route']}")
    return "; ".join(details) + "."


def _challenge_persona(primary: str, action: str) -> dict[str, str]:
    preferred = {
        "ESCALATE_SPECIALIST": "P-RISK",
        "ESCALATE_ACTION_RECOVERY": "P-TECH",
        "ESCALATE_EVIDENCE": "P-OPS",
        "NO_ACTION_ALREADY_RECOVERED": "P-TECH",
        "NO_ACTION_DUPLICATE_SIGNAL": "P-WORK",
        "WAIT_VERIFIED_ETA": "P-CUST",
        "RESHIP_MISSING": "P-WORK",
        "REFUND_MISSING": "P-WORK",
    }[action]
    if preferred == primary:
        preferred = {"P-RISK": "P-CUST", "P-TECH": "P-WORK", "P-OPS": "P-TECH", "P-WORK": "P-RISK", "P-CUST": "P-OPS"}[preferred]
    return PERSONA_BY_CODE[preferred]


def _initial_owner(case: dict[str, Any], action: str, route: str) -> str:
    if action == "ESCALATE_SPECIALIST":
        return "policy_and_risk_owner"
    if action == "ESCALATE_EVIDENCE":
        return "fulfilment_operations_coordinator"
    if action == "ESCALATE_ACTION_RECOVERY":
        return "technical_owner"
    if route == "approval":
        authority_route, authority_owner = _authority(case)
        return authority_owner if authority_route == "approval" else "workflow_owner"
    return "customer_recovery_specialist"


def _pressure(persona: dict[str, str]) -> str:
    return {
        "P-CUST": "A frustrated customer needs a clear answer now; delay may create another contact.",
        "P-OPS": "The exception queue is growing; another handoff threatens cycle time and rework.",
        "P-WORK": "The workflow must protect both customer value and sustainable unit economics.",
        "P-TECH": "A misleading status or duplicate write could create an irreversible second remedy.",
        "P-RISK": "A consequential action must remain stopped if the risk classification is unresolved.",
    }[persona["persona_code"]]


def _control(action: str) -> str:
    if action == "ESCALATE_SPECIALIST":
        return "Risk-stop priority, least authority, and specialist ownership."
    if action == "ESCALATE_ACTION_RECOVERY":
        return "Action-state recovery, authoritative postcondition, and duplicate-action prevention."
    if action == "ESCALATE_EVIDENCE":
        return "Evidence completeness, freshness, conflict detection, and fail-closed routing."
    if action.startswith("NO_ACTION"):
        return "Duplicate-action prevention and supported no-new-action communication."
    if action == "WAIT_VERIFIED_ETA":
        return "Customer preference, reliable ETA evidence, and estimate-not-outcome messaging."
    return "Authority threshold, exact confirmation before execution, and postcondition verification."


def _tradeoff(action: str) -> str:
    return {
        "ESCALATE_SPECIALIST": "Slower recovery is accepted to prevent unsafe, fraudulent, privacy-sensitive, or chargeback-conflicted action.",
        "ESCALATE_ACTION_RECOVERY": "Extra investigation is accepted to avoid a duplicate refund or replacement.",
        "ESCALATE_EVIDENCE": "Queue delay is accepted to prevent an unsupported remedy from incomplete or conflicting evidence.",
        "NO_ACTION_ALREADY_RECOVERED": "Duplicate cost is avoided while the customer receives a supported no-new-action explanation.",
        "NO_ACTION_DUPLICATE_SIGNAL": "Unnecessary work and duplicate cost are avoided without hiding that no new action was created.",
        "WAIT_VERIFIED_ETA": "Customer choice is preserved without converting an estimated arrival into a verified outcome.",
        "RESHIP_MISSING": "Faster physical recovery consumes stock and requires verified reservation and delivery follow-through.",
        "REFUND_MISSING": "Cash is returned instead of consuming stock, but execution must be separately verified.",
    }[action]


def _leader_response(case: dict[str, Any], final: dict[str, Any]) -> str:
    action, owner = final["action"], final["decision_owner"]
    amount = case["order"]["affected_value_cents"] / 100
    if action == "ESCALATE_SPECIALIST":
        signal = final["rationale_codes"][0].replace("risk_stop:", "")
        return f"I stop remedy selection before speed or empathy can override the {signal} signal. I route the EUR {amount:.2f} case to {owner} and allow only supported under-review messaging."
    if action == "NO_ACTION_ALREADY_RECOVERED":
        return f"I prevent a duplicate recovery. The prior action is VERIFIED and its authoritative postcondition covers the EUR {amount:.2f} affected value, so I create no new action and keep delegated ownership."
    if action == "ESCALATE_ACTION_RECOVERY":
        state = case["history"]["prior_action"]["status"]
        return f"I separate a status label from proven execution. The prior action is {state}, so I route to {owner} for action recovery instead of creating a second remedy."
    if action == "ESCALATE_EVIDENCE":
        issue = ", ".join(final["rationale_codes"])
        return f"I do not turn queue pressure into an unsupported financial or stock action. Because {issue}, I route to {owner} and preserve an evidence-first stop."
    if action == "NO_ACTION_DUPLICATE_SIGNAL":
        return "I apply the duplicate-signal rule after confirming no higher-priority action-state issue exists. I create no new recovery and keep the case delegated with supported no-action messaging."
    if action == "WAIT_VERIFIED_ETA":
        return "I respect the customer's preference to wait because the parcel is in transit with a reliable revised ETA. I communicate that the ETA remains an estimate and create no irreversible remedy."
    if action == "RESHIP_MISSING":
        return f"I select reship because replacement stock is reservable and the customer did not request a refund. I route the EUR {amount:.2f} exposure to {owner}, with execution and reservation still requiring verification."
    return f"I select refund because replacement is unavailable or not the chosen recovery. I route the EUR {amount:.2f} exposure to {owner} and state clearly that recommendation is not verified payment execution."


def build_rows(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    persona_rows = [
        {
            **persona,
            "is_human_reviewer": "false",
            "evidence_class": EVIDENCE_CLASS,
            "independence_status": INDEPENDENCE_STATUS,
        }
        for persona in PERSONAS
    ]
    decision_rows: list[dict[str, Any]] = []
    manual_rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        final = derive_governed_decision(case)
        primary = PERSONAS[(index - 1) % len(PERSONAS)]
        challenger = _challenge_persona(primary["persona_code"], final["action"])
        override = ADVERSARIAL_OVERRIDES.get(case["case_id"])
        if override:
            initial_action, initial_route, bias, initial_reasoning = override
        else:
            initial_action, initial_route = final["action"], final["route"]
            bias = "policy-aligned-persona-lens"
            initial_reasoning = f"The {primary['persona_name']} lens identifies {final['rationale_codes'][0]} and selects the policy route."
        changed = initial_action != final["action"] or initial_route != final["route"]
        challenge = f"{challenger['persona_name']} tests decision priority, evidence, authority, and verified-outcome boundaries before the leader adjudicates."
        change_reason = (
            f"Changed after the {challenger['persona_name']} challenge exposed {bias}; the frozen policy requires {final['action']} via {final['route']}."
            if changed
            else f"Retained after the {challenger['persona_name']} challenge confirmed the first decision against the frozen policy."
        )
        confidence = 3 if changed else (4 if final["action"].startswith("ESCALATE") else 5)
        decision_rows.append(
            {
                "case_id": case["case_id"],
                "scenario_category": _scenario_category(final["action"]),
                "scenario_summary": _scenario_summary(case, final),
                "primary_persona_code": primary["persona_code"],
                "primary_persona_name": primary["persona_name"],
                "primary_priority": primary["primary_objective"],
                "primary_failure_tendency": primary["failure_tendency"],
                "challenger_persona_code": challenger["persona_code"],
                "challenger_persona_name": challenger["persona_name"],
                "adversarial_pressure": _pressure(primary),
                "challenger_argument": challenge,
                "initial_recommended_action": initial_action,
                "initial_route": initial_route,
                "initial_decision_owner": _initial_owner(case, initial_action, initial_route),
                "initial_reasoning": initial_reasoning,
                "initial_bias": bias,
                "governed_final_action": final["action"],
                "governed_final_route": final["route"],
                "governed_decision_owner": final["decision_owner"],
                "decision_changed": str(changed).lower(),
                "change_reason": change_reason,
                "required_evidence_pipe_delimited": "|".join(final["required_evidence"]),
                "message_facts_pipe_delimited": "|".join(final["message_facts"]),
                "confidence_1_to_5": str(confidence),
                "leader_response_first_person": _leader_response(case, final),
                "simulated_handoff_target": final["decision_owner"],
                "economic_or_customer_tradeoff": _tradeoff(final["action"]),
                "control_applied": _control(final["action"]),
                "evidence_class": EVIDENCE_CLASS,
                "independence_status": INDEPENDENCE_STATUS,
                "timing_status": TIMING_STATUS,
                "claim_boundary": CLAIM_BOUNDARY,
            }
        )
        manual_rows.append(
            {
                "case_id": case["case_id"],
                "reviewer_code": f"SIM-{primary['persona_code']}",
                "run_type": "ai-assisted-multi-persona",
                "started_at_utc": GENERATED_AT_UTC,
                "ended_at_utc": GENERATED_AT_UTC,
                "active_handling_seconds": "0",
                "recommended_action": final["action"],
                "route": final["route"],
                "evidence_used_pipe_delimited": "|".join(final["required_evidence"]),
                "message_facts_pipe_delimited": "|".join(final["message_facts"]),
                "confidence_1_to_5": str(confidence),
                "help_requested": "true",
                "handoff_count": "0",
                "policy_lookup_count": "0",
                "notes_without_personal_data": f"AI batch simulation using {primary['persona_name']} and {challenger['persona_name']}; no human handling, timing, handoff, independence, or outcome is claimed.",
            }
        )
    return persona_rows, decision_rows, manual_rows


def _validate_private_oracle(root: Path, decisions: list[dict[str, Any]]) -> None:
    oracle_path = root / PRIVATE_ORACLE_RELATIVE
    if not oracle_path.exists():
        raise ValueError("private oracle is required for the initial adversarial construction check")
    public_manifest = json.loads((root / PUBLIC_MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    expected_hash = public_manifest["artifacts_sha256"]["oracle.released.jsonl"]
    if _hash(oracle_path.read_bytes()) != expected_hash:
        raise ValueError("private oracle does not match the pre-exposure public commitment")
    expected = {row["case_id"]: row for row in read_jsonl(oracle_path)}
    for decision in decisions:
        oracle = expected[decision["case_id"]]
        comparisons = {
            "governed_final_action": oracle["preferred_action"],
            "governed_final_route": oracle["required_route"],
            "governed_decision_owner": oracle["decision_owner"],
            "required_evidence_pipe_delimited": "|".join(oracle["required_evidence"]),
            "message_facts_pipe_delimited": "|".join(oracle["allowed_message_facts"]),
        }
        for field, expected_value in comparisons.items():
            if decision[field] != expected_value:
                raise ValueError(f"{decision['case_id']}: {field} diverges from committed oracle")


def _validate_blank_human_instrument(root: Path) -> None:
    run = root / RUN_RELATIVE
    validate_heldout_invalidation(
        root,
        run_directory=run,
        require=True,
    )
    records_bytes = (run / "manual-records.csv").read_bytes()
    manifest = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
    if _hash(records_bytes) != manifest["run_files"]["records_template"]["sha256"]:
        raise ValueError("the original blank human worksheet changed")
    rows = list(csv.DictReader(io.StringIO(records_bytes.decode("utf-8"))))
    answer_fields = {"started_at_utc", "ended_at_utc", "recommended_action", "route", "evidence_used_pipe_delimited", "message_facts_pipe_delimited"}
    if any(any(row[field] for field in answer_fields) for row in rows):
        raise ValueError("the original human worksheet is no longer blank")


def generate(root: Path, output: Path, *, validate_private_oracle: bool = True) -> dict[str, Any]:
    _validate_blank_human_instrument(root)
    cases = read_jsonl(root / CASES_RELATIVE)
    if len(cases) != 32:
        raise ValueError("the persona simulation requires exactly 32 V2 cases")
    persona_rows, decision_rows, manual_rows = build_rows(cases)
    if validate_private_oracle:
        _validate_private_oracle(root, decision_rows)
    if any(not str(value).strip() for rows in (persona_rows, decision_rows, manual_rows) for row in rows for value in row.values()):
        raise ValueError("practice CSVs must not contain blank cells")

    persona_fields = list(persona_rows[0])
    decision_fields = list(decision_rows[0])
    manual_fields = list(manual_rows[0])
    files = {
        "personas.csv": _csv_bytes(persona_fields, persona_rows),
        "multi-persona-decisions.csv": _csv_bytes(decision_fields, decision_rows),
        "manual-records.ai-assisted.csv": _csv_bytes(manual_fields, manual_rows),
    }
    adapted_decision_count = sum(
        row["decision_changed"] == "true" for row in decision_rows
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "practice_id": PRACTICE_ID,
        "generated_at_utc": GENERATED_AT_UTC,
        "case_count": len(decision_rows),
        "simulated_persona_count": len(persona_rows),
        "adapted_decision_count": adapted_decision_count,
        "adapted_decision_rate": round(adapted_decision_count / len(decision_rows), 4),
        "counts_by_final_action": dict(sorted(Counter(row["governed_final_action"] for row in decision_rows).items())),
        "counts_by_final_route": dict(sorted(Counter(row["governed_final_route"] for row in decision_rows).items())),
        "counts_by_primary_persona": dict(sorted(Counter(row["primary_persona_code"] for row in decision_rows).items())),
        "counts_by_scenario_category": dict(sorted(Counter(row["scenario_category"] for row in decision_rows).items())),
        "evidence_class": EVIDENCE_CLASS,
        "independence_status": INDEPENDENCE_STATUS,
        "timing_status": TIMING_STATUS,
        "claim_boundary": CLAIM_BOUNDARY,
    }
    files["summary.json"] = _json_bytes(summary)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "practice_id": PRACTICE_ID,
        "generated_at_utc": GENERATED_AT_UTC,
        "source_pack_id": "SCC-01-HO-V2",
        "source_pack_status": "invalidated-before-human-handling",
        "construction_method": "deterministic multi-persona adversarial simulation with policy-derived final decisions",
        "private_oracle_validation": PRIVATE_ORACLE_PUBLICATION_STATUS,
        "case_count": len(decision_rows),
        "simulated_persona_count": len(persona_rows),
        "contains_real_data": False,
        "evidence_class": EVIDENCE_CLASS,
        "independence_status": INDEPENDENCE_STATUS,
        "timing_status": TIMING_STATUS,
        "claim_boundary": CLAIM_BOUNDARY,
        "artifacts_sha256": {name: _hash(value) for name, value in sorted(files.items())},
    }
    files["manifest.json"] = _json_bytes(manifest)
    output.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (output / name).write_bytes(value)
    return manifest


def _verify_artifacts(
    root: Path,
    output: Path,
    *,
    validate_private_oracle: bool,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        regenerated = Path(directory) / "multi-persona-v1"
        generate(
            root,
            regenerated,
            validate_private_oracle=validate_private_oracle,
        )
        expected = sorted(path.name for path in output.iterdir() if path.is_file() and path.name != "README.md")
        actual = sorted(path.name for path in regenerated.iterdir() if path.is_file())
        if actual != expected:
            raise ValueError(f"artifact set drift: expected {expected}, regenerated {actual}")
        for name in actual:
            if (output / name).read_bytes() != (regenerated / name).read_bytes():
                raise ValueError(f"committed practice artifact drift: {name}")


def verify(root: Path, output: Path) -> None:
    """Verify artifact bytes and repeat the private-oracle agreement check."""
    _verify_artifacts(root, output, validate_private_oracle=True)


def verify_public(root: Path, output: Path) -> None:
    """Verify public artifact bytes without claiming oracle revalidation."""
    _verify_artifacts(root, output, validate_private_oracle=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument("--verify", action="store_true")
    verification.add_argument("--verify-public", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / OUTPUT_RELATIVE
    if args.verify:
        verify(root, output)
        print(
            f"Verified {PRACTICE_ID} committed artifacts and repeated the "
            "private-oracle agreement check."
        )
        return
    if args.verify_public:
        verify_public(root, output)
        print(
            f"Verified {PRACTICE_ID} public artifact bytes only; private-oracle "
            "agreement was not repeated."
        )
        return
    manifest = generate(root, output)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
