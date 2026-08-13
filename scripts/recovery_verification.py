#!/usr/bin/env python3
"""Read-only authoritative postcondition verification for recovery actions.

This module deliberately has no dependency on the mutating adapter.  Adapter
receipts are accepted only as explicitly untrusted context and never affect a
classification.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from scripts.recovery_actions import validate_action_contract
from scripts.recovery_workspace import SafeFileAuthority, WorkspaceIntegrityError, _sha
from scripts.stage2_contracts import (
    ContractValidationError,
    STAGE2_ID_PATTERN,
    canonical_sha256,
    load_canonical_json,
    validate_neutral_record,
)


VERIFIER_VERSION = "recovery-verification/1.0.0"


class VerificationControlError(ValueError):
    """Raised when authoritative evidence cannot be safely inspected."""


class AuthoritativeEffectReader(Protocol):
    def read_committed_effects(self, source_name: str) -> list[Mapping[str, Any]]: ...


class FileAuthoritativeEffectReader:
    """Read committed source effects through a read-only workspace capability."""

    def __init__(self, run_root):
        self.authority = SafeFileAuthority(run_root)

    def read_committed_effects(self, source_name: str) -> list[Mapping[str, Any]]:
        if source_name not in {"PAYMENT", "OMS", "INVENTORY", "WMS"}:
            raise VerificationControlError("authoritative source is not allow-listed")
        try:
            raw = self.authority.read_bytes(f"source-effects/{source_name}.jsonl")
        except WorkspaceIntegrityError as error:
            raise VerificationControlError("authoritative source journal is unavailable") from error
        committed: list[Mapping[str, Any]] = []
        suffix_started = False
        seen_action_ids: set[str] = set()
        committed_marker_names: set[str] = set()
        for sequence, line in enumerate(raw.splitlines(keepends=True), 1):
            if not line.endswith(b"\n"):
                # No LF means no complete append boundary and therefore no evidence.
                suffix_started = True
                continue
            try:
                effect = load_canonical_json(line)
            except ContractValidationError as error:
                raise VerificationControlError("authoritative source journal is not canonical") from error
            if not isinstance(effect, Mapping) or effect.get("source_name") != source_name:
                raise VerificationControlError("authoritative source effect is invalid")
            action_id = effect.get("action_id")
            if not isinstance(action_id, str):
                raise VerificationControlError("authoritative effect action identity is invalid")
            if action_id in seen_action_ids:
                raise VerificationControlError("authoritative effect action identity is duplicated")
            seen_action_ids.add(action_id)
            marker_relative = f"source-effects/commits/{source_name}/{action_id}.json"
            if not (self.authority.root / marker_relative).exists():
                suffix_started = True
                continue
            if suffix_started:
                raise VerificationControlError(
                    "authoritative marker appears after an uncommitted suffix"
                )
            try:
                marker = load_canonical_json(self.authority.read_bytes(marker_relative))
            except (WorkspaceIntegrityError, ContractValidationError) as error:
                raise VerificationControlError("authoritative effect marker is invalid") from error
            expected = {
                "action_id": action_id,
                "effect_digest": _sha(dict(effect)),
                "effect_sequence": sequence,
                "schema_version": "stage2-source-effect-commit/v1",
                "source_name": source_name,
            }
            if effect.get("effect_sequence") != sequence or marker != expected:
                raise VerificationControlError("authoritative effect commit marker is invalid")
            committed.append(effect)
            committed_marker_names.add(f"{action_id}.json")
        actual_marker_names = {
            path.name
            for path in (
                self.authority.root / "source-effects" / "commits" / source_name
            ).glob("*.json")
        }
        if actual_marker_names != committed_marker_names:
            raise VerificationControlError(
                "authoritative effect marker inventory is not a committed prefix"
            )
        return committed


def _matching(action: Mapping[str, Any], effects: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    payload = action["payload"]
    return [
        effect
        for effect in effects
        if effect.get("action_id") == payload["action_id"]
        and effect.get("action_contract_digest") == payload["action_contract_digest"]
        and effect.get("case_id") == payload["case_id"]
        and effect.get("case_revision") == payload["case_revision"]
        and effect.get("eligible_business_key") == payload["eligible_business_key"]
    ]


def _exact_one(matches: list[Mapping[str, Any]], **expected: Any) -> bool:
    if len(matches) != 1:
        return False
    effect = matches[0]
    if effect.get("stale") is True or effect.get("conflicting") is True:
        return False
    return all(effect.get(name) == value for name, value in expected.items())


def verify_authoritative_postcondition(
    action: Mapping[str, Any],
    reader: AuthoritativeEffectReader,
    *,
    verification_id: str,
    untrusted_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify only committed authoritative source effects, never a receipt."""

    record = validate_action_contract(action)
    payload = record["payload"]
    if not isinstance(verification_id, str) or not STAGE2_ID_PATTERN.fullmatch(verification_id):
        raise VerificationControlError("verification ID is not canonical")
    observations: dict[str, list[Mapping[str, Any]]] = {}
    if payload["operation"] == "REFUND":
        observations["PAYMENT"] = _matching(record, reader.read_committed_effects("PAYMENT"))
        passed = _exact_one(
            observations["PAYMENT"],
            amount_cents=payload["amount_cents"],
            currency=payload["currency"],
            quantity=payload["eligible_quantity"],
            refund_committed=True,
        )
        milestone = "REFUND_COMMITTED_EXACT" if passed else "REFUND_POSTCONDITION_MISSING_OR_INEXACT"
    else:
        for source in ("OMS", "INVENTORY", "WMS"):
            observations[source] = _matching(record, reader.read_committed_effects(source))
        passed = (
            _exact_one(
                observations["OMS"],
                quantity=payload["eligible_quantity"],
                replacement_created=True,
            )
            and isinstance(observations["OMS"][0].get("replacement_order_id"), str)
            and _exact_one(
                observations["INVENTORY"],
                inventory_reserved=True,
                quantity=payload["eligible_quantity"],
            )
            and _exact_one(
                observations["WMS"],
                quantity=payload["eligible_quantity"],
                wms_accepted=True,
            )
        )
        milestone = (
            "REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED"
            if passed
            else "REPLACEMENT_POSTCONDITION_MISSING_OR_INEXACT"
        )
    evidence_digests = {
        source: [canonical_sha256(dict(effect)) for effect in values]
        for source, values in sorted(observations.items())
    }
    result = {
        "payload": {
            "action_contract_digest": payload["action_contract_digest"],
            "action_id": payload["action_id"],
            "adapter_receipt_present": untrusted_receipt is not None,
            "adapter_receipt_trusted": False,
            "authoritative_effect_digests": evidence_digests,
            "case_id": payload["case_id"],
            "case_revision": payload["case_revision"],
            "classification": "VERIFIED_REMEDY" if passed else "VERIFICATION_FAILED",
            "customer_delivery_observed": False,
            "milestone": milestone,
            "non_independent": True,
            "operation": payload["operation"],
            "synthetic": True,
            "verifier_version": VERIFIER_VERSION,
        },
        "record_id": verification_id,
        "record_type": "verification",
        "schema_version": "stage2-verification/v1",
    }
    try:
        return validate_neutral_record(result)
    except (TypeError, ValueError) as error:
        raise VerificationControlError("verification record is invalid") from error


def verify_no_action_condition(
    *,
    verification_id: str,
    case_id: str,
    case_revision: int,
    classification: str,
    milestone: str,
    cited_fact_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Record a distinct verified wait/no-new-action condition with zero action."""

    if classification not in {"VERIFIED_WAIT_CONDITION", "VERIFIED_NO_NEW_ACTION"}:
        raise VerificationControlError("classification is not a direct no-action condition")
    for value in (verification_id, case_id, *cited_fact_ids):
        if not isinstance(value, str) or not STAGE2_ID_PATTERN.fullmatch(value):
            raise VerificationControlError("no-action evidence identity is invalid")
    if isinstance(case_revision, bool) or not isinstance(case_revision, int) or case_revision < 1:
        raise VerificationControlError("case revision is invalid")
    return validate_neutral_record(
        {
            "payload": {
                "case_id": case_id,
                "case_revision": case_revision,
                "cited_fact_ids": list(cited_fact_ids),
                "classification": classification,
                "consequential_action_count": 0,
                "milestone": milestone,
                "non_independent": True,
                "synthetic": True,
                "verifier_version": VERIFIER_VERSION,
            },
            "record_id": verification_id,
            "record_type": "verification",
            "schema_version": "stage2-verification/v1",
        }
    )
