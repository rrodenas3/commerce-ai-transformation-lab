#!/usr/bin/env python3
"""Synthetic, exact-bound authority capabilities for Stage 2 recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from scripts.stage2_contracts import STAGE2_ID_PATTERN, validate_neutral_record


class AuthorityBindingError(ValueError):
    """Raised when a decision cannot confer the exact expected capability."""


ROLE_BY_ROUTE = {
    "DELEGATED_DECISION": "SYNTHETIC_RECOVERY_SPECIALIST",
    "WORKFLOW_OWNER_APPROVAL": "SYNTHETIC_WORKFLOW_OWNER",
    "FINANCE_APPROVAL": "SYNTHETIC_FINANCE_APPROVER",
}
ACTOR_BY_ROLE = {
    "SYNTHETIC_RECOVERY_SPECIALIST": "S2-ACTOR-RECOVERY-SPECIALIST",
    "SYNTHETIC_WORKFLOW_OWNER": "S2-ACTOR-WORKFLOW-OWNER",
    "SYNTHETIC_FINANCE_APPROVER": "S2-ACTOR-FINANCE-APPROVER",
}
HEX = frozenset("0123456789abcdef")


def _instant(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AuthorityBindingError(f"{name} must be a canonical UTC instant")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise AuthorityBindingError(f"{name} is invalid") from error
    if parsed.tzinfo != timezone.utc or parsed.microsecond:
        raise AuthorityBindingError(f"{name} must be a whole-second UTC instant")
    return parsed


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in HEX for char in value):
        raise AuthorityBindingError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class AuthorityExpectation:
    case_id: str
    case_revision: int
    ledger_head_digest: str
    policy_id: str
    policy_version: str
    operation: str
    payload_digest: str
    authority_route: str
    recommending_provider_id: str

    def __post_init__(self) -> None:
        for name in ("case_id", "recommending_provider_id"):
            if not isinstance(getattr(self, name), str) or not STAGE2_ID_PATTERN.fullmatch(getattr(self, name)):
                raise AuthorityBindingError(f"{name} must be a canonical synthetic ID")
        if isinstance(self.case_revision, bool) or not isinstance(self.case_revision, int) or self.case_revision < 1:
            raise AuthorityBindingError("case_revision must be a positive integer")
        _digest(self.ledger_head_digest, "ledger_head_digest")
        _digest(self.payload_digest, "payload_digest")
        if self.operation not in {"REFUND", "RESHIP"}:
            raise AuthorityBindingError("operation is not consequential recovery")
        if self.authority_route not in ROLE_BY_ROUTE:
            raise AuthorityBindingError("authority route cannot create a capability")
        if self.policy_id != "SCC-01-RECOVERY-POLICY" or self.policy_version != "1.0.0":
            raise AuthorityBindingError("policy identity is not frozen")


@dataclass(frozen=True)
class AuthorityCapability:
    authority_id: str
    authority_route: str
    operation: str
    payload_digest: str
    approver_role: str
    expires_at: str
    consumed: bool


_PAYLOAD_FIELDS = {
    "approval_id",
    "approver_role",
    "authority_route",
    "case_id",
    "case_revision",
    "consumed_at",
    "decision",
    "expires_at",
    "issued_at",
    "issued_by",
    "issuer_kind",
    "ledger_head_digest",
    "operation",
    "payload_digest",
    "policy_id",
    "policy_version",
    "rationale_code",
    "recommending_provider_id",
    "revoked_at",
    "synthetic",
}


def create_synthetic_authority_event(
    expectation: AuthorityExpectation,
    *,
    approval_id: str,
    issued_by: str,
    approver_role: str,
    decision: str,
    rationale_code: str,
    issued_at: str,
    expires_at: str,
    revoked_at: str | None = None,
    consumed_at: str | None = None,
    issuer_kind: str = "synthetic_role_fixture",
) -> dict[str, Any]:
    """Create a role fixture record; validation still occurs separately."""

    payload = {
        "approval_id": approval_id,
        "approver_role": approver_role,
        "authority_route": expectation.authority_route,
        "case_id": expectation.case_id,
        "case_revision": expectation.case_revision,
        "consumed_at": consumed_at,
        "decision": decision,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "issued_by": issued_by,
        "issuer_kind": issuer_kind,
        "ledger_head_digest": expectation.ledger_head_digest,
        "operation": expectation.operation,
        "payload_digest": expectation.payload_digest,
        "policy_id": expectation.policy_id,
        "policy_version": expectation.policy_version,
        "rationale_code": rationale_code,
        "recommending_provider_id": expectation.recommending_provider_id,
        "revoked_at": revoked_at,
        "synthetic": True,
    }
    return {
        "payload": payload,
        "record_id": approval_id,
        "record_type": "approval",
        "schema_version": "stage2-approval/v1",
    }


def validate_authority_event(
    value: Any,
    expectation: AuthorityExpectation,
    *,
    now: str,
) -> AuthorityCapability:
    """Validate an unused exact capability without consuming it (U5 owns use)."""

    try:
        record = validate_neutral_record(value)
    except (TypeError, ValueError) as error:
        raise AuthorityBindingError("authority record envelope is invalid") from error
    if record["record_type"] != "approval":
        raise AuthorityBindingError("authority record has the wrong type")
    payload = record["payload"]
    if set(payload) != _PAYLOAD_FIELDS:
        raise AuthorityBindingError("authority record fields are not exact")
    approval_id = payload.get("approval_id")
    if approval_id != record["record_id"] or not isinstance(approval_id, str) or not STAGE2_ID_PATTERN.fullmatch(approval_id):
        raise AuthorityBindingError("authority identity is invalid")
    expected_prefix = (
        "S2-DECISION-" if expectation.authority_route == "DELEGATED_DECISION" else "S2-APPROVAL-"
    )
    if not approval_id.startswith(expected_prefix):
        raise AuthorityBindingError("authority identity does not match the route kind")
    expected_fields = {
        "authority_route": expectation.authority_route,
        "case_id": expectation.case_id,
        "case_revision": expectation.case_revision,
        "ledger_head_digest": expectation.ledger_head_digest,
        "operation": expectation.operation,
        "payload_digest": expectation.payload_digest,
        "policy_id": expectation.policy_id,
        "policy_version": expectation.policy_version,
        "recommending_provider_id": expectation.recommending_provider_id,
    }
    if any(payload.get(name) != expected for name, expected in expected_fields.items()):
        raise AuthorityBindingError("authority capability does not bind the expected operation")
    if payload.get("approver_role") != ROLE_BY_ROUTE[expectation.authority_route]:
        raise AuthorityBindingError("approver role is not authorised for the route")
    if payload.get("synthetic") is not True or payload.get("issuer_kind") != "synthetic_role_fixture":
        raise AuthorityBindingError("only a declared synthetic role fixture may issue this event")
    issued_by = payload.get("issued_by")
    if not isinstance(issued_by, str) or not STAGE2_ID_PATTERN.fullmatch(issued_by):
        raise AuthorityBindingError("issuer identity is invalid")
    if issued_by == expectation.recommending_provider_id:
        raise AuthorityBindingError("recommending provider cannot self-approve")
    if issued_by != ACTOR_BY_ROLE[payload["approver_role"]]:
        raise AuthorityBindingError("issuer identity does not own the declared synthetic role")
    if payload.get("decision") != "APPROVED":
        raise AuthorityBindingError("rejected or amended decisions confer no capability")
    rationale = payload.get("rationale_code")
    if not isinstance(rationale, str) or not rationale or len(rationale) > 128:
        raise AuthorityBindingError("decision rationale is invalid")
    issued = _instant(payload.get("issued_at"), "issued_at")
    expires = _instant(payload.get("expires_at"), "expires_at")
    observed = _instant(now, "now")
    if not issued <= observed < expires:
        raise AuthorityBindingError("authority capability is not currently valid")
    if payload.get("revoked_at") is not None:
        _instant(payload["revoked_at"], "revoked_at")
        raise AuthorityBindingError("revoked authority capability is invalid")
    if payload.get("consumed_at") is not None:
        _instant(payload["consumed_at"], "consumed_at")
        raise AuthorityBindingError("replayed authority capability is invalid")
    return AuthorityCapability(
        authority_id=approval_id,
        authority_route=expectation.authority_route,
        operation=expectation.operation,
        payload_digest=expectation.payload_digest,
        approver_role=payload["approver_role"],
        expires_at=payload["expires_at"],
        consumed=False,
    )
