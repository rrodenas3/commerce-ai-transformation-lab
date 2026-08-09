#!/usr/bin/env python3
"""Build and validate the public Stage 1 synthetic case and oracle system."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


GENERATOR_NAME = "scc-01-foundation-case-generator"
GENERATOR_VERSION = "1.0.0"
GENERATOR_SEED = 20260809
FOUNDATION_CASE_COUNT = 24
CASE_FAMILIES = (
    "delayed_reliable",
    "partial_stock_available",
    "partial_no_stock",
    "conflicting_evidence",
    "duplicate_or_stale",
    "authority_boundary",
    "retry_and_verification",
    "out_of_scope_risk",
)
SOURCE_NAMES = ("OMS", "WMS", "CARRIER", "INVENTORY", "PAYMENT", "CRM", "POLICY")


def load_stage1_policy(root: Path) -> dict[str, Any]:
    """Load and validate the fictional recovery policy."""
    path = root / "data" / "stage1" / "policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "policy_id",
        "version",
        "synthetic_only",
        "currency",
        "authority",
        "freshness_hours",
        "required_case_sources",
        "risk_stop_flags",
        "allowed_actions",
        "critical_zero_controls",
    }
    missing = sorted(required - policy.keys())
    if missing:
        raise ValueError(f"stage1 policy missing required keys: {', '.join(missing)}")
    if policy["synthetic_only"] is not True:
        raise ValueError("stage1 policy must remain synthetic_only")
    if set(policy["required_case_sources"]) != set(SOURCE_NAMES):
        raise ValueError("stage1 policy source inventory does not match the case contract")
    return policy


def _source_snapshot(
    observed_at: str,
    freshness_hours: dict[str, int | float],
    *,
    stale_sources: Iterable[str] = (),
    missing_sources: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    observed = _parse_aware_iso(observed_at, "observed_at")
    stale = set(stale_sources)
    missing = set(missing_sources)
    snapshots: dict[str, dict[str, Any]] = {}
    for source in SOURCE_NAMES:
        if source in missing:
            snapshots[source] = {"available": False, "as_of": None, "fresh": False}
        else:
            age = (
                timedelta(hours=freshness_hours[source], minutes=1)
                if source in stale
                else timedelta(0)
            )
            snapshots[source] = {
                "available": True,
                "as_of": _format_utc(observed - age),
                "fresh": age <= timedelta(hours=freshness_hours[source]),
            }
    return snapshots


def _parse_aware_iso(value: Any, field_name: str) -> datetime:
    """Parse an ISO-8601 instant and reject ambiguous timezone-free values."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be ISO-8601 with a timezone")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be ISO-8601 with a timezone") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be ISO-8601 with a timezone")
    return parsed


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _base_case(
    policy: dict[str, Any],
    number: int,
    family: str,
    title: str,
    *,
    ordered_qty: int = 2,
    delivered_qty: int = 0,
    affected_value_cents: int = 2000,
    order_value_cents: int = 8000,
    customer_preference: str = "fastest_recovery",
    carrier_status: str = "DELAYED",
    revised_eta_reliable: bool = True,
    replacement_stock_qty: int = 0,
    allow_partial: bool = True,
    source_conflict: bool = False,
    stale_sources: Iterable[str] = (),
    missing_sources: Iterable[str] = (),
    duplicate_signal: bool = False,
    risk_flags: Iterable[str] = (),
    prior_action: dict[str, Any] | None = None,
    repeat_recovery: bool = False,
    refunded_cents: int = 0,
    replacement_reservation: dict[str, Any] | None = None,
    active_chargeback: bool = False,
) -> dict[str, Any]:
    observed_at = "2026-08-09T10:00:00Z"
    remaining_qty = ordered_qty - delivered_qty
    case_id = f"SCC-01-FND-{number:03d}"
    return {
        "case_id": case_id,
        "case_family": family,
        "title": title,
        "synthetic": True,
        "dataset_role": "public-foundation-discovery",
        "generator": {
            "name": GENERATOR_NAME,
            "version": GENERATOR_VERSION,
            "seed": GENERATOR_SEED,
        },
        "observed_at": observed_at,
        "scope": {
            "market": "EU-DOMESTIC-LAB",
            "channel": "ONLINE",
            "currency": "EUR",
            "regulated_goods": False,
        },
        "trigger": {
            "signal_id": f"SYN-SIG-{number:03d}",
            "type": "PARTIAL_FULFILMENT" if delivered_qty else "DELAYED_FULFILMENT",
            "duplicate": duplicate_signal,
        },
        "order": {
            "order_id": f"SYN-O-{10000 + number}",
            "order_value_cents": order_value_cents,
            "ordered_qty": ordered_qty,
            "delivered_qty": delivered_qty,
            "remaining_qty": remaining_qty,
            "affected_value_cents": affected_value_cents,
            "allow_partial": allow_partial,
            "promise_version": 1,
            "promised_delivery_at": "2026-08-08T18:00:00Z",
            "cancelled": False,
        },
        "customer": {
            "customer_id": f"SYN-C-{20000 + number}",
            "preference": customer_preference,
            "contact_count": 0,
        },
        "warehouse": {
            "node_id": "SYN-WH-01",
            "status": "PARTIALLY_SHIPPED" if delivered_qty else "SHIPPED",
            "recorded_shipped_qty": ordered_qty,
        },
        "carrier": {
            "parcel_id": f"SYN-P-{30000 + number}",
            "status": carrier_status,
            "revised_eta_at": "2026-08-10T18:00:00Z",
            "revised_eta_reliable": revised_eta_reliable,
            "recorded_delivered_qty": delivered_qty,
        },
        "inventory": {
            "replacement_stock_qty": replacement_stock_qty,
            "reservable": replacement_stock_qty >= remaining_qty,
            "replacement_reservation": replacement_reservation,
        },
        "payment": {
            "captured": True,
            "refunded_cents": refunded_cents,
            "active_chargeback": active_chargeback,
        },
        "history": {
            "repeat_recovery": repeat_recovery,
            "prior_action": prior_action,
        },
        "evidence": {
            "source_conflict": source_conflict,
            "sources": _source_snapshot(
                observed_at,
                policy["freshness_hours"],
                stale_sources=stale_sources,
                missing_sources=missing_sources,
            ),
        },
        "risk_flags": sorted(risk_flags),
        "policy": {
            "policy_id": "SCC-01-RECOVERY-POLICY",
            "version": "1.0.0",
        },
    }


def build_foundation_cases(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the frozen 24-case discovery set.

    The set is deliberately transparent and is not the future held-out evaluation set.
    The policy argument is checked so generator and oracle versions cannot silently drift.
    """
    if policy["version"] != "1.0.0":
        raise ValueError("generator supports only recovery policy version 1.0.0")

    cases = [
        _base_case(policy, 1, "delayed_reliable", "Reliable short delay; customer accepts wait", customer_preference="wait", carrier_status="IN_TRANSIT"),
        _base_case(policy, 2, "delayed_reliable", "Reliable delay; replacement is available", replacement_stock_qty=2, affected_value_cents=2400),
        _base_case(policy, 3, "delayed_reliable", "Confirmed loss; no replacement stock", carrier_status="LOST_CONFIRMED", revised_eta_reliable=False, affected_value_cents=1800),
        _base_case(policy, 4, "partial_stock_available", "One of two units missing; delegated reship", delivered_qty=1, replacement_stock_qty=1, affected_value_cents=2000),
        _base_case(policy, 5, "partial_stock_available", "Two of four units missing; approval reship", ordered_qty=4, delivered_qty=2, replacement_stock_qty=2, affected_value_cents=6000),
        _base_case(policy, 6, "partial_stock_available", "Missing unit available; customer prefers refund", delivered_qty=1, replacement_stock_qty=1, affected_value_cents=2000, customer_preference="refund_missing"),
        _base_case(policy, 7, "partial_no_stock", "Missing unit unavailable; delegated refund", delivered_qty=1, affected_value_cents=2000),
        _base_case(policy, 8, "partial_no_stock", "Missing quantity unavailable; approval refund", ordered_qty=3, delivered_qty=1, affected_value_cents=7000),
        _base_case(policy, 9, "partial_no_stock", "High-value missing quantity; finance review", delivered_qty=1, affected_value_cents=12500, order_value_cents=65000),
        _base_case(policy, 10, "conflicting_evidence", "OMS and carrier quantities conflict", delivered_qty=1, source_conflict=True, carrier_status="DELIVERED"),
        _base_case(policy, 11, "conflicting_evidence", "Warehouse shipment and carrier state conflict", delivered_qty=0, source_conflict=True, carrier_status="DELIVERED"),
        _base_case(policy, 12, "conflicting_evidence", "Required payment evidence is unavailable", delivered_qty=1, missing_sources=("PAYMENT",)),
        _base_case(policy, 13, "duplicate_or_stale", "Duplicate signal after verified refund", delivered_qty=1, duplicate_signal=True, refunded_cents=2000, prior_action={"action_id": "SYN-ACT-013", "type": "REFUND_MISSING", "status": "VERIFIED"}),
        _base_case(policy, 14, "duplicate_or_stale", "Duplicate signal with no consequential action", duplicate_signal=True, customer_preference="wait", carrier_status="IN_TRANSIT"),
        _base_case(policy, 15, "duplicate_or_stale", "Carrier evidence exceeds freshness policy", delivered_qty=1, stale_sources=("CARRIER",)),
        _base_case(policy, 16, "authority_boundary", "Exact delegated boundary for reship", delivered_qty=1, replacement_stock_qty=1, affected_value_cents=2500),
        _base_case(policy, 17, "authority_boundary", "One cent above delegated boundary", delivered_qty=1, replacement_stock_qty=1, affected_value_cents=2501),
        _base_case(policy, 18, "authority_boundary", "One cent above team-lead boundary", delivered_qty=1, affected_value_cents=10001, order_value_cents=55000),
        _base_case(policy, 19, "retry_and_verification", "Refund submitted but postcondition pending", delivered_qty=1, prior_action={"action_id": "SYN-ACT-019", "type": "REFUND_MISSING", "status": "PENDING"}),
        _base_case(policy, 20, "retry_and_verification", "Refund failed safely with no state change", delivered_qty=1, prior_action={"action_id": "SYN-ACT-020", "type": "REFUND_MISSING", "status": "FAILED_SAFE"}),
        _base_case(policy, 21, "retry_and_verification", "Replacement exists without inventory reservation", delivered_qty=1, replacement_stock_qty=1, prior_action={"action_id": "SYN-ACT-021", "type": "RESHIP_MISSING", "status": "UNVERIFIED"}),
        _base_case(policy, 22, "out_of_scope_risk", "Safety concern requires specialist stop", risk_flags=("safety",)),
        _base_case(policy, 23, "out_of_scope_risk", "Privacy concern requires specialist stop", risk_flags=("privacy",)),
        _base_case(policy, 24, "out_of_scope_risk", "Suspected fraud requires specialist stop", risk_flags=("suspected_fraud",)),
    ]
    errors = [error for case in cases for error in validate_case(case, policy)]
    if errors:
        raise ValueError("invalid generated cases: " + "; ".join(errors))
    return cases


def _authority_for_exposure(case: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str]:
    exposure = case["order"]["affected_value_cents"]
    authority = policy["authority"]
    if (
        exposure > authority["team_lead_max_exposure_cents"]
        or case["order"]["order_value_cents"] > authority["finance_review_order_value_cents"]
    ):
        return "approval", "finance_duty_approver"
    if (
        exposure > authority["delegated_max_exposure_cents"]
        or (
            authority["repeat_recovery_requires_approval"]
            and case["history"]["repeat_recovery"]
        )
    ):
        return "approval", "workflow_owner"
    return "delegated", "customer_recovery_specialist"


def _authoritative_postcondition_verified(case: dict[str, Any]) -> bool:
    """Require action-specific system evidence, not a self-reported action status."""
    prior_action = case["history"]["prior_action"]
    if not prior_action or prior_action.get("status") != "VERIFIED":
        return False

    sources = case["evidence"]["sources"]
    if case["evidence"]["source_conflict"]:
        return False
    if prior_action.get("type") == "REFUND_MISSING":
        payment_source = sources["PAYMENT"]
        return bool(
            payment_source["available"]
            and payment_source["fresh"]
            and case["payment"]["refunded_cents"]
            >= case["order"]["affected_value_cents"]
        )
    if prior_action.get("type") == "RESHIP_MISSING":
        inventory_source = sources["INVENTORY"]
        reservation = case["inventory"].get("replacement_reservation")
        return bool(
            inventory_source["available"]
            and inventory_source["fresh"]
            and isinstance(reservation, dict)
            and reservation.get("action_id") == prior_action.get("action_id")
            and reservation.get("status") == "VERIFIED"
            and reservation.get("reserved_qty", 0) >= case["order"]["remaining_qty"]
        )
    return False


def build_oracle(case: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Derive the independent lab oracle from case facts and frozen policy only."""
    facts = case
    route, decision_owner = _authority_for_exposure(facts, policy)
    action: str
    rationale: list[str] = []
    critical_under_escalation = False

    sources = facts["evidence"]["sources"]
    unavailable = sorted(name for name, record in sources.items() if not record["available"])
    stale = sorted(name for name, record in sources.items() if record["available"] and not record["fresh"])
    prior_action = facts["history"]["prior_action"]

    active_risk_flags = set(facts["risk_flags"])
    if facts["payment"]["active_chargeback"]:
        active_risk_flags.add("active_chargeback")

    if active_risk_flags:
        action = "ESCALATE_SPECIALIST"
        route = "specialist"
        decision_owner = "policy_and_risk_owner"
        rationale = [f"risk_stop:{flag}" for flag in sorted(active_risk_flags)]
        critical_under_escalation = True
    elif prior_action and _authoritative_postcondition_verified(facts):
        action = "NO_ACTION_ALREADY_RECOVERED"
        route = "delegated"
        decision_owner = "customer_recovery_specialist"
        rationale = ["verified_prior_action"]
        critical_under_escalation = True
    elif prior_action and prior_action["status"] in {
        "VERIFIED",
        "PENDING",
        "FAILED_SAFE",
        "UNVERIFIED",
    }:
        action = "ESCALATE_ACTION_RECOVERY"
        route = "specialist"
        decision_owner = "technical_owner"
        rationale = [
            (
                "prior_action:verified_without_authoritative_postcondition"
                if prior_action["status"] == "VERIFIED"
                else f"prior_action:{prior_action['status'].lower()}"
            )
        ]
        critical_under_escalation = True
    elif facts["evidence"]["source_conflict"] or unavailable or stale:
        action = "ESCALATE_EVIDENCE"
        route = "specialist"
        decision_owner = "fulfilment_operations_coordinator"
        rationale = ["source_conflict"] if facts["evidence"]["source_conflict"] else []
        rationale.extend(f"missing:{source}" for source in unavailable)
        rationale.extend(f"stale:{source}" for source in stale)
        critical_under_escalation = True
    elif facts["trigger"]["duplicate"]:
        action = "NO_ACTION_DUPLICATE_SIGNAL"
        route = "delegated"
        decision_owner = "customer_recovery_specialist"
        rationale = ["duplicate_signal_without_new_state"]
    elif facts["carrier"]["status"] == "IN_TRANSIT" and facts["customer"]["preference"] == "wait":
        action = "WAIT_VERIFIED_ETA"
        route = "delegated"
        decision_owner = "customer_recovery_specialist"
        rationale = ["reliable_revised_eta", "customer_prefers_wait"]
    elif facts["inventory"]["reservable"] and facts["customer"]["preference"] != "refund_missing":
        action = "RESHIP_MISSING"
        rationale = ["missing_quantity_confirmed", "replacement_stock_reservable"]
    else:
        action = "REFUND_MISSING"
        rationale = ["missing_quantity_confirmed", "replacement_not_selected_or_unavailable"]

    required_evidence = ["OMS", "WMS", "CARRIER", "POLICY"]
    if action in {"RESHIP_MISSING", "REFUND_MISSING"}:
        required_evidence.extend(["INVENTORY", "PAYMENT", "CRM"])
    elif action.startswith("NO_ACTION"):
        required_evidence.extend(["CRM", "PAYMENT"])
    if action.startswith("ESCALATE"):
        required_evidence = sorted(name for name, record in sources.items() if record["available"])

    allowed_message_facts = [
        "case_received",
        "order_reference_confirmed",
        "recovery_under_review",
    ]
    if action == "WAIT_VERIFIED_ETA":
        allowed_message_facts.append("revised_eta_is_estimate")
    if action == "RESHIP_MISSING":
        allowed_message_facts.append("replacement_requires_verified_execution")
    if action == "REFUND_MISSING":
        allowed_message_facts.append("refund_requires_verified_execution")
    if action.startswith("NO_ACTION"):
        allowed_message_facts.append("no_new_action_created")

    applicable_critical_zero_controls = {
        "false_verified_outcome",
        "personal_or_secret_data_disclosure",
        "unauthorised_consequential_action",
    }
    if prior_action or facts["trigger"]["duplicate"]:
        applicable_critical_zero_controls.add("duplicate_consequential_action")

    return {
        "case_id": facts["case_id"],
        "oracle_version": "1.0.0",
        "policy_version": policy["version"],
        "eligible_recovery_case": (
            not bool(active_risk_flags) and not action.startswith("NO_ACTION")
        ),
        "allowed_actions": [action],
        "preferred_action": action,
        "required_route": route,
        "decision_owner": decision_owner,
        "required_evidence": sorted(set(required_evidence)),
        "allowed_message_facts": sorted(allowed_message_facts),
        "rationale_codes": sorted(rationale),
        "critical_if_under_escalated": critical_under_escalation,
        "applicable_critical_zero_controls": sorted(
            applicable_critical_zero_controls
        ),
        "prohibited_actions": sorted(
            action_name for action_name in policy["allowed_actions"] if action_name != action
        ),
    }


def validate_case(case: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = case.get("case_id", "<missing>")
    required = {
        "case_id",
        "case_family",
        "synthetic",
        "generator",
        "observed_at",
        "scope",
        "trigger",
        "order",
        "customer",
        "warehouse",
        "carrier",
        "inventory",
        "payment",
        "history",
        "evidence",
        "risk_flags",
        "policy",
    }
    missing = sorted(required - case.keys())
    if missing:
        errors.append(f"{case_id}: missing fields {', '.join(missing)}")
        return errors
    if case["synthetic"] is not True:
        errors.append(f"{case_id}: synthetic must be true")
    if case["case_family"] not in CASE_FAMILIES:
        errors.append(f"{case_id}: unsupported case_family")
    order = case["order"]
    if order["remaining_qty"] != order["ordered_qty"] - order["delivered_qty"]:
        errors.append(f"{case_id}: quantity invariant failed")
    if order["remaining_qty"] <= 0:
        errors.append(f"{case_id}: foundation exception must have remaining quantity")
    if order["affected_value_cents"] < 0 or order["order_value_cents"] < order["affected_value_cents"]:
        errors.append(f"{case_id}: invalid monetary values")
    sources = case["evidence"].get("sources", {})
    if set(sources) != set(policy["required_case_sources"]):
        errors.append(f"{case_id}: source inventory mismatch")
    if not set(case["risk_flags"]).issubset(set(policy["risk_stop_flags"])):
        errors.append(f"{case_id}: unsupported risk flag")
    if case["policy"]["version"] != policy["version"]:
        errors.append(f"{case_id}: policy version mismatch")
    try:
        observed_at = _parse_aware_iso(case["observed_at"], "observed_at")
    except ValueError as error:
        errors.append(f"{case_id}: {error}")
        observed_at = None
    if observed_at is not None and set(sources) == set(policy["required_case_sources"]):
        for source_name, source in sources.items():
            if not source.get("available"):
                if source.get("as_of") is not None or source.get("fresh") is not False:
                    errors.append(
                        f"{case_id}: unavailable {source_name} must have null as_of and fresh false"
                    )
                continue
            try:
                as_of = _parse_aware_iso(source.get("as_of"), f"{source_name}.as_of")
            except ValueError as error:
                errors.append(f"{case_id}: {error}")
                continue
            if as_of > observed_at:
                errors.append(f"{case_id}: {source_name}.as_of cannot be after observed_at")
                continue
            expected_fresh = observed_at - as_of <= timedelta(
                hours=policy["freshness_hours"][source_name]
            )
            if source.get("fresh") is not expected_fresh:
                errors.append(
                    f"{case_id}: {source_name}.fresh does not match policy-derived source age"
                )

    if not isinstance(case["payment"].get("active_chargeback"), bool):
        errors.append(f"{case_id}: payment.active_chargeback must be boolean")
    reservation = case["inventory"].get("replacement_reservation")
    if reservation is not None:
        required_reservation_fields = {"action_id", "status", "reserved_qty"}
        if not isinstance(reservation, dict) or not required_reservation_fields.issubset(
            reservation
        ):
            errors.append(f"{case_id}: invalid replacement reservation postcondition")
        elif (
            reservation["status"] != "VERIFIED"
            or not isinstance(reservation["reserved_qty"], int)
            or reservation["reserved_qty"] < 0
        ):
            errors.append(f"{case_id}: invalid replacement reservation postcondition")
    return errors


def validate_oracle(oracle: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    case_id = oracle.get("case_id", "<missing>")
    required = {
        "case_id",
        "oracle_version",
        "policy_version",
        "eligible_recovery_case",
        "allowed_actions",
        "preferred_action",
        "required_route",
        "decision_owner",
        "required_evidence",
        "allowed_message_facts",
        "rationale_codes",
        "critical_if_under_escalated",
        "applicable_critical_zero_controls",
        "prohibited_actions",
    }
    missing = sorted(required - oracle.keys())
    if missing:
        errors.append(f"{case_id}: oracle missing fields {', '.join(missing)}")
        return errors
    if oracle["preferred_action"] not in oracle["allowed_actions"]:
        errors.append(f"{case_id}: preferred action is not allowed")
    if not set(oracle["allowed_actions"]).issubset(set(policy["allowed_actions"])):
        errors.append(f"{case_id}: oracle action is outside policy")
    if set(oracle["allowed_actions"]) & set(oracle["prohibited_actions"]):
        errors.append(f"{case_id}: action is both allowed and prohibited")
    if oracle["required_route"] not in {"delegated", "approval", "specialist"}:
        errors.append(f"{case_id}: unsupported required route")
    if not set(oracle["applicable_critical_zero_controls"]).issubset(
        set(policy["critical_zero_controls"])
    ):
        errors.append(f"{case_id}: unsupported critical-zero control")
    return errors


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    text = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    path.write_text(text, encoding="utf-8")


def generate_stage1_artifacts(project_root: Path, output_root: Path) -> dict[str, Any]:
    policy = load_stage1_policy(project_root)
    cases = build_foundation_cases(policy)
    oracles = [build_oracle(case, policy) for case in cases]
    oracle_errors = [error for oracle in oracles for error in validate_oracle(oracle, policy)]
    if oracle_errors:
        raise ValueError("invalid generated oracles: " + "; ".join(oracle_errors))
    output_root.mkdir(parents=True, exist_ok=True)
    case_path = output_root / "cases.jsonl"
    oracle_path = output_root / "oracle.jsonl"
    write_jsonl(case_path, cases)
    write_jsonl(oracle_path, oracles)
    manifest = {
        "generated_at": max(case["observed_at"] for case in cases),
        "generator_name": GENERATOR_NAME,
        "generator_version": GENERATOR_VERSION,
        "generator_seed": GENERATOR_SEED,
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "case_count": len(cases),
        "case_families": dict(
            sorted(Counter(case["case_family"] for case in cases).items())
        ),
        "dataset_role": "public-foundation-discovery",
        "held_out_evaluation_set": False,
        "contains_real_data": False,
        "artifacts_sha256": {
            "cases.jsonl": hashlib.sha256(case_path.read_bytes()).hexdigest(),
            "oracle.jsonl": hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
        },
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
