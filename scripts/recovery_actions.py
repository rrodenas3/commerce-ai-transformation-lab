#!/usr/bin/env python3
"""Exact consequential-action contracts and guarded authority reservation.

This module defines intent.  It cannot mutate a source system; the only state
change it requests is the workspace's single atomic authority/idempotency
reservation transition.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.recovery_approval import (
    AuthorityBindingError,
    AuthorityExpectation,
    validate_authority_event,
)
from scripts.stage2_contracts import (
    STAGE2_ID_PATTERN,
    canonical_sha256,
    reject_evaluator_only_fields,
    validate_neutral_record,
)


ACTION_SCHEMA = "stage2-action/v1"
ACTION_CONTRACT_VERSION = "recovery-action/1.0.0"
ALLOWED_OPERATIONS = frozenset({"REFUND", "RESHIP"})
POSTCONDITION_BY_OPERATION = {
    "REFUND": "EXACT_ACTION_LINKED_PAYMENT_REFUND",
    "RESHIP": "REPLACEMENT_CREATED_EXACT_INVENTORY_RESERVED_WMS_ACCEPTED",
}
MINIMUM_PERMISSION_BY_OPERATION = {
    "REFUND": "SIMULATED_PAYMENT_REFUND_WRITE",
    "RESHIP": "SIMULATED_REPLACEMENT_RESERVATION_WMS_WRITE",
}
HEX64 = frozenset("0123456789abcdef")


class ActionControlError(ValueError):
    """Raised before reservation or mutation when an action is not exact."""


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not STAGE2_ID_PATTERN.fullmatch(value):
        raise ActionControlError(f"{name} must be a canonical synthetic ID")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in HEX64 for c in value):
        raise ActionControlError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _nonnegative(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ActionControlError(f"{name} must be a {qualifier} integer")
    return value


def _action_payload_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "amount_cents": payload["amount_cents"],
        "case_id": payload["case_id"],
        "case_revision": payload["case_revision"],
        "currency": payload["currency"],
        "eligible_business_key": payload["eligible_business_key"],
        "eligible_quantity": payload["eligible_quantity"],
        "operation": payload["operation"],
        "target": payload["target"],
    }


def _contract_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "action_contract_digest"}


def build_action_contract(
    *,
    action_id: str,
    case_id: str,
    case_revision: int,
    ledger_head_digest: str,
    policy_id: str,
    policy_version: str,
    operation: str,
    target: str,
    eligible_business_key: str,
    eligible_quantity: int,
    amount_cents: int,
    currency: str,
    before_state: Mapping[str, Any],
    authority_route: str,
    authority_reference: str,
    idempotency_key: str,
    timeout_milliseconds: int,
) -> dict[str, Any]:
    """Build and validate one immutable allow-listed local action contract."""

    _id(action_id, "action_id")
    _id(case_id, "case_id")
    _id(target, "target")
    _id(eligible_business_key, "eligible_business_key")
    _id(authority_reference, "authority_reference")
    _id(idempotency_key, "idempotency_key")
    _digest(ledger_head_digest, "ledger_head_digest")
    _nonnegative(case_revision, "case_revision", positive=True)
    _nonnegative(eligible_quantity, "eligible_quantity", positive=True)
    _nonnegative(amount_cents, "amount_cents", positive=True)
    _nonnegative(timeout_milliseconds, "timeout_milliseconds", positive=True)
    if operation not in ALLOWED_OPERATIONS:
        raise ActionControlError("operation is not an allow-listed consequential action")
    if policy_id != "SCC-01-RECOVERY-POLICY" or policy_version != "1.0.0":
        raise ActionControlError("action policy identity is not frozen")
    if authority_route not in {
        "DELEGATED_DECISION",
        "WORKFLOW_OWNER_APPROVAL",
        "FINANCE_APPROVAL",
    }:
        raise ActionControlError("action authority route is not consequential")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
        raise ActionControlError("currency must be a canonical three-letter code")
    if not isinstance(before_state, Mapping) or not before_state:
        raise ActionControlError("before_state must contain authoritative facts")
    reject_evaluator_only_fields(before_state, "action.before_state")
    payload: dict[str, Any] = {
        "action_contract_digest": "",
        "action_id": action_id,
        "action_payload_digest": "",
        "amount_cents": amount_cents,
        "authority_reference": authority_reference,
        "authority_route": authority_route,
        "before_state_digest": canonical_sha256(dict(before_state)),
        "case_id": case_id,
        "case_revision": case_revision,
        "compensation_posture": "FORWARD_ONLY_EXPLICIT_COMPENSATION",
        "contract_version": ACTION_CONTRACT_VERSION,
        "currency": currency,
        "eligible_business_key": eligible_business_key,
        "eligible_quantity": eligible_quantity,
        "escalation_route": "SYNTHETIC_RECOVERY_SPECIALIST",
        "expected_postcondition": POSTCONDITION_BY_OPERATION[operation],
        "idempotency_key": idempotency_key,
        "ledger_head_digest": ledger_head_digest,
        "minimum_permission": MINIMUM_PERMISSION_BY_OPERATION[operation],
        "operation": operation,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "retry_rule": "RECONCILE_AUTHORITATIVE_STATE_BEFORE_RETRY",
        "synthetic": True,
        "target": target,
        "timeout_milliseconds": timeout_milliseconds,
    }
    payload["action_payload_digest"] = canonical_sha256(_action_payload_material(payload))
    payload["action_contract_digest"] = canonical_sha256(_contract_material(payload))
    record = {
        "payload": payload,
        "record_id": action_id,
        "record_type": "action",
        "schema_version": ACTION_SCHEMA,
    }
    return validate_action_contract(record)


_PAYLOAD_FIELDS = frozenset(
    {
        "action_contract_digest",
        "action_id",
        "action_payload_digest",
        "amount_cents",
        "authority_reference",
        "authority_route",
        "before_state_digest",
        "case_id",
        "case_revision",
        "compensation_posture",
        "contract_version",
        "currency",
        "eligible_business_key",
        "eligible_quantity",
        "escalation_route",
        "expected_postcondition",
        "idempotency_key",
        "ledger_head_digest",
        "minimum_permission",
        "operation",
        "policy_id",
        "policy_version",
        "retry_rule",
        "synthetic",
        "target",
        "timeout_milliseconds",
    }
)


def validate_action_contract(value: Any) -> dict[str, Any]:
    try:
        record = validate_neutral_record(value)
    except (TypeError, ValueError) as error:
        raise ActionControlError("action record envelope is invalid") from error
    if record["record_type"] != "action" or record["schema_version"] != ACTION_SCHEMA:
        raise ActionControlError("action record type/version is invalid")
    payload = record["payload"]
    if set(payload) != _PAYLOAD_FIELDS or payload.get("action_id") != record["record_id"]:
        raise ActionControlError("action fields are not exact")
    if payload.get("operation") not in ALLOWED_OPERATIONS:
        raise ActionControlError("action operation is not allow-listed")
    if payload.get("contract_version") != ACTION_CONTRACT_VERSION or payload.get("synthetic") is not True:
        raise ActionControlError("action contract identity is invalid")
    if payload.get("policy_id") != "SCC-01-RECOVERY-POLICY" or payload.get("policy_version") != "1.0.0":
        raise ActionControlError("action policy identity is not frozen")
    if payload.get("authority_route") not in {
        "DELEGATED_DECISION",
        "WORKFLOW_OWNER_APPROVAL",
        "FINANCE_APPROVAL",
    }:
        raise ActionControlError("action authority route is invalid")
    if payload.get("retry_rule") != "RECONCILE_AUTHORITATIVE_STATE_BEFORE_RETRY":
        raise ActionControlError("action retry rule is not reconciliation-first")
    if payload.get("compensation_posture") != "FORWARD_ONLY_EXPLICIT_COMPENSATION":
        raise ActionControlError("action compensation posture is invalid")
    if payload.get("escalation_route") != "SYNTHETIC_RECOVERY_SPECIALIST":
        raise ActionControlError("action escalation route is invalid")
    currency = payload.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper():
        raise ActionControlError("action currency is invalid")
    for name in (
        "action_id",
        "case_id",
        "target",
        "eligible_business_key",
        "authority_reference",
        "idempotency_key",
    ):
        _id(payload.get(name), name)
    for name in ("ledger_head_digest", "before_state_digest", "action_payload_digest", "action_contract_digest"):
        _digest(payload.get(name), name)
    for name in ("case_revision", "eligible_quantity", "amount_cents", "timeout_milliseconds"):
        _nonnegative(payload.get(name), name, positive=True)
    expected_payload = canonical_sha256(_action_payload_material(payload))
    expected_contract = canonical_sha256(_contract_material(payload))
    if payload["action_payload_digest"] != expected_payload or payload["action_contract_digest"] != expected_contract:
        raise ActionControlError("action digest binding is invalid")
    if payload.get("expected_postcondition") != POSTCONDITION_BY_OPERATION[payload["operation"]]:
        raise ActionControlError("action postcondition is not preregistered")
    if payload.get("minimum_permission") != MINIMUM_PERMISSION_BY_OPERATION[payload["operation"]]:
        raise ActionControlError("action minimum permission is invalid")
    reject_evaluator_only_fields(record, "action")
    return record


def reserve_action_atomically(
    workspace: Any,
    action: Mapping[str, Any],
    authority_event: Mapping[str, Any],
    *,
    recommending_provider_id: str,
    now: str,
    command_id: str,
) -> Any:
    """Validate exact authority, then let the workspace consume/reserve once."""

    record = validate_action_contract(action)
    payload = record["payload"]
    expectation = AuthorityExpectation(
        case_id=payload["case_id"],
        case_revision=payload["case_revision"],
        ledger_head_digest=payload["ledger_head_digest"],
        policy_id=payload["policy_id"],
        policy_version=payload["policy_version"],
        operation=payload["operation"],
        payload_digest=payload["action_payload_digest"],
        authority_route=payload["authority_route"],
        recommending_provider_id=recommending_provider_id,
    )
    try:
        capability = validate_authority_event(authority_event, expectation, now=now)
    except AuthorityBindingError as error:
        raise ActionControlError("action authority does not bind the exact payload") from error
    if capability.authority_id != payload["authority_reference"]:
        raise ActionControlError("action references a different authority capability")
    return workspace.reserve_action(
        record,
        {
            "authority_id": capability.authority_id,
            "authority_route": capability.authority_route,
            "approver_role": capability.approver_role,
            "expires_at": capability.expires_at,
            "payload_digest": capability.payload_digest,
        },
        command_id=command_id,
    )
