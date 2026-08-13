#!/usr/bin/env python3
"""Pure validation and fact derivation for Stage 2 synthetic source batches.

This inward module knows only the neutral Stage 2 contracts.  It never imports
generation, evaluator, oracle, release, provider, adapter, or workspace code.
Caller-supplied outcome labels are rejected: freshness, conflict, shortfall,
exposure, chargeback, and recovery state are derived from source records.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from scripts.stage2_contracts import (
    ContractValidationError,
    canonical_json_bytes,
    canonical_sha256,
    validate_neutral_record,
)


SOURCE_NAMES = ("OMS", "WMS", "CARRIER", "INVENTORY", "PAYMENT", "CRM", "POLICY")
SOURCE_BATCH_SCHEMA = "stage2-source-batch/v1"
SOURCE_RECORD_SCHEMA = "stage2-source-record/v1"
POLICY_ID = "SCC-01-RECOVERY-POLICY"
POLICY_VERSION = "1.0.0"
SENSITIVITY_LABEL = "synthetic-public"
DERIVED_ONLY_FIELDS = frozenset(
    {
        "active_chargeback",
        "affected_value_cents",
        "conflict",
        "duplicate_coverage",
        "fresh",
        "has_source_conflict",
        "remaining_quantity",
        "recovered_quantity",
        "recovery_state",
        "shortfall",
        "source_conflict",
    }
)


class SourceValidationError(ValueError):
    """Raised when a generated source batch violates its runtime contract."""


def _fail(path: str, message: str) -> SourceValidationError:
    return SourceValidationError(f"{path}: {message}")


def _exact_fields(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing:
        raise _fail(path, f"missing field(s): {', '.join(missing)}")
    if unknown:
        raise _fail(path, f"unknown field(s): {', '.join(unknown)}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(path, "must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _fail(path, "must be a list")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(path, "must be a nonempty string")
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(path, f"must be an integer greater than or equal to {minimum}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "must be a boolean")
    return value


def parse_instant(value: Any, field_name: str) -> datetime:
    """Parse an explicit-offset ISO instant and normalise it to UTC."""

    if not isinstance(value, str) or "T" not in value:
        raise _fail(field_name, "must be ISO-8601 with an explicit timezone")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise _fail(field_name, "must be valid ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail(field_name, "must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def source_provenance_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable source-record material covered by provenance."""

    return {key: value for key, value in payload.items() if key != "provenance_digest"}


def source_event_cut_sha256(records: list[Mapping[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for record in records:
        hasher.update(canonical_json_bytes(record))
    return hasher.hexdigest()


def revision_pin_sha256(
    case_id: str,
    case_revision: int,
    source_event_cut: str,
    ledger_head_digest: str,
) -> str:
    return canonical_sha256(
        {
            "case_id": case_id,
            "case_revision": case_revision,
            "ledger_head_digest": ledger_head_digest,
            "source_event_cut_sha256": source_event_cut,
        }
    )


def _reject_derived_labels(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in DERIVED_ONLY_FIELDS:
                raise _fail(f"{path}.{key}", "derived field must not be supplied")
            _reject_derived_labels(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_derived_labels(item, f"{path}[{index}]")


def _validate_source_data(source_name: str, value: Any, path: str) -> None:
    data = _mapping(value, path)
    field_sets = {
        "OMS": {
            "currency",
            "lines",
            "order_id",
            "order_value_cents",
            "prior_action_attempts",
            "promised_delivery_at",
        },
        "WMS": {"line_shipments", "parcel_membership", "warehouse_id"},
        "CARRIER": {"parcels"},
        "INVENTORY": {"available_replacement_quantity", "warehouse_id"},
        "PAYMENT": {
            "captured_amount_cents",
            "chargeback_cases",
            "currency",
            "refund_entries",
        },
        "CRM": {"customer_choice", "intake_signals", "risk_signals"},
        "POLICY": {
            "authority",
            "currency",
            "freshness_hours",
            "policy_id",
            "policy_version",
            "operating_timezone",
            "risk_stop_flags",
        },
    }
    _exact_fields(data, field_sets[source_name], path)

    if source_name == "OMS":
        _string(data["order_id"], f"{path}.order_id")
        if not data["order_id"].startswith("S2-ORDER-"):
            raise _fail(f"{path}.order_id", "must be a synthetic Stage 2 ID")
        if data["currency"] != "EUR":
            raise _fail(f"{path}.currency", "must be EUR")
        _integer(data["order_value_cents"], f"{path}.order_value_cents", minimum=1)
        parse_instant(data["promised_delivery_at"], f"{path}.promised_delivery_at")
        lines = _list(data["lines"], f"{path}.lines")
        if not 1 <= len(lines) <= 5:
            raise _fail(f"{path}.lines", "must contain one to five lines")
        line_ids: set[str] = set()
        for index, raw_line in enumerate(lines):
            line_path = f"{path}.lines[{index}]"
            line = _mapping(raw_line, line_path)
            _exact_fields(line, {"line_id", "ordered_quantity", "unit_value_cents"}, line_path)
            line_id = _string(line["line_id"], f"{line_path}.line_id")
            if not line_id.startswith("S2-LINE-") or line_id in line_ids:
                raise _fail(f"{line_path}.line_id", "must be a unique synthetic line ID")
            line_ids.add(line_id)
            _integer(line["ordered_quantity"], f"{line_path}.ordered_quantity", minimum=1)
            _integer(line["unit_value_cents"], f"{line_path}.unit_value_cents", minimum=1)
        attempts = _list(data["prior_action_attempts"], f"{path}.prior_action_attempts")
        for index, raw_attempt in enumerate(attempts):
            attempt = _mapping(raw_attempt, f"{path}.prior_action_attempts[{index}]")
            _exact_fields(
                attempt,
                {"action_id", "operation", "quantity", "status"},
                f"{path}.prior_action_attempts[{index}]",
            )
            if attempt["operation"] not in {"REFUND", "RESHIP"}:
                raise _fail(f"{path}.prior_action_attempts[{index}].operation", "unsupported operation")
            _integer(attempt["quantity"], f"{path}.prior_action_attempts[{index}].quantity")
            if attempt["status"] not in {"COMMITTED", "PENDING", "FAILED"}:
                raise _fail(f"{path}.prior_action_attempts[{index}].status", "unsupported status")
    elif source_name == "WMS":
        _string(data["warehouse_id"], f"{path}.warehouse_id")
        shipments = _mapping(data["line_shipments"], f"{path}.line_shipments")
        for line_id, quantity in shipments.items():
            if not line_id.startswith("S2-LINE-"):
                raise _fail(f"{path}.line_shipments", "contains non-synthetic line ID")
            _integer(quantity, f"{path}.line_shipments.{line_id}")
        membership = _mapping(data["parcel_membership"], f"{path}.parcel_membership")
        for parcel_id, line_quantities in membership.items():
            if not parcel_id.startswith("S2-PARCEL-"):
                raise _fail(f"{path}.parcel_membership", "contains non-synthetic parcel ID")
            for line_id, quantity in _mapping(line_quantities, f"{path}.parcel_membership.{parcel_id}").items():
                _integer(quantity, f"{path}.parcel_membership.{parcel_id}.{line_id}")
    elif source_name == "CARRIER":
        parcels = _list(data["parcels"], f"{path}.parcels")
        if not 1 <= len(parcels) <= 2:
            raise _fail(f"{path}.parcels", "must contain one or two parcels")
        observed_ids: set[str] = set()
        for index, raw_parcel in enumerate(parcels):
            parcel_path = f"{path}.parcels[{index}]"
            parcel = _mapping(raw_parcel, parcel_path)
            _exact_fields(
                parcel,
                {"delivered_quantities", "eta_at", "eta_reliability", "parcel_id", "status"},
                parcel_path,
            )
            parcel_id = _string(parcel["parcel_id"], f"{parcel_path}.parcel_id")
            if not parcel_id.startswith("S2-PARCEL-") or parcel_id in observed_ids:
                raise _fail(f"{parcel_path}.parcel_id", "must be a unique synthetic parcel ID")
            observed_ids.add(parcel_id)
            if parcel["status"] not in {"IN_TRANSIT", "DELAYED", "PARTIAL", "LOST_CONFIRMED"}:
                raise _fail(f"{parcel_path}.status", "unsupported carrier status")
            if parcel["eta_at"] is not None:
                parse_instant(parcel["eta_at"], f"{parcel_path}.eta_at")
            if parcel["eta_reliability"] not in {"RELIABLE", "UNRELIABLE", "NONE"}:
                raise _fail(f"{parcel_path}.eta_reliability", "unsupported reliability")
            for line_id, quantity in _mapping(
                parcel["delivered_quantities"], f"{parcel_path}.delivered_quantities"
            ).items():
                _integer(quantity, f"{parcel_path}.delivered_quantities.{line_id}")
    elif source_name == "INVENTORY":
        _string(data["warehouse_id"], f"{path}.warehouse_id")
        _integer(
            data["available_replacement_quantity"],
            f"{path}.available_replacement_quantity",
        )
    elif source_name == "PAYMENT":
        if data["currency"] != "EUR":
            raise _fail(f"{path}.currency", "must be EUR")
        _integer(data["captured_amount_cents"], f"{path}.captured_amount_cents", minimum=1)
        refunds = _list(data["refund_entries"], f"{path}.refund_entries")
        for index, raw_refund in enumerate(refunds):
            refund = _mapping(raw_refund, f"{path}.refund_entries[{index}]")
            _exact_fields(refund, {"action_id", "amount_cents", "currency", "quantity"}, f"{path}.refund_entries[{index}]")
            _integer(refund["amount_cents"], f"{path}.refund_entries[{index}].amount_cents", minimum=1)
            _integer(refund["quantity"], f"{path}.refund_entries[{index}].quantity", minimum=1)
            if refund["currency"] != "EUR":
                raise _fail(f"{path}.refund_entries[{index}].currency", "must be EUR")
        _list(data["chargeback_cases"], f"{path}.chargeback_cases")
    elif source_name == "CRM":
        choice = data["customer_choice"]
        if choice is not None and choice not in {"WAIT", "RESHIP", "REFUND"}:
            raise _fail(f"{path}.customer_choice", "unsupported synthetic choice")
        signals = _list(data["intake_signals"], f"{path}.intake_signals")
        if not signals:
            raise _fail(f"{path}.intake_signals", "must not be empty")
        for index, raw_signal in enumerate(signals):
            signal = _mapping(raw_signal, f"{path}.intake_signals[{index}]")
            _exact_fields(signal, {"signal_id", "signal_type"}, f"{path}.intake_signals[{index}]")
        _list(data["risk_signals"], f"{path}.risk_signals")
    elif source_name == "POLICY":
        if data["policy_id"] != POLICY_ID or data["policy_version"] != POLICY_VERSION:
            raise _fail(path, "wrong policy identity")
        if data["currency"] != "EUR":
            raise _fail(f"{path}.currency", "must be EUR")
        if data["operating_timezone"] != "Europe/Paris":
            raise _fail(f"{path}.operating_timezone", "must be Europe/Paris")
        freshness = _mapping(data["freshness_hours"], f"{path}.freshness_hours")
        if set(freshness) != set(SOURCE_NAMES):
            raise _fail(f"{path}.freshness_hours", "must cover the seven-source vocabulary")
        for source, hours in freshness.items():
            _integer(hours, f"{path}.freshness_hours.{source}", minimum=1)
        authority = _mapping(data["authority"], f"{path}.authority")
        _exact_fields(
            authority,
            {
                "delegated_max_exposure_cents",
                "finance_review_order_value_cents",
                "workflow_owner_max_exposure_cents",
            },
            f"{path}.authority",
        )
        for key, amount in authority.items():
            _integer(amount, f"{path}.authority.{key}", minimum=1)
        _list(data["risk_stop_flags"], f"{path}.risk_stop_flags")


def validate_source_batch(value: Any) -> dict[str, Any]:
    """Validate one committed, immutable source-event cut."""

    try:
        batch = validate_neutral_record(value)
    except ContractValidationError as error:
        raise SourceValidationError(str(error)) from error
    if batch["record_type"] != "source_batch" or batch["schema_version"] != SOURCE_BATCH_SCHEMA:
        raise _fail("$", "must be a Stage 2 source batch")
    payload = _mapping(batch["payload"], "$.payload")
    _exact_fields(
        payload,
        {
            "case_id",
            "case_revision",
            "committed",
            "committed_at",
            "ledger_head_digest",
            "records",
            "revision_pin_sha256",
            "source_event_cut_sha256",
            "synthetic",
        },
        "$.payload",
    )
    _reject_derived_labels(payload, "$.payload")
    case_id = _string(payload["case_id"], "$.payload.case_id")
    if not case_id.startswith("S2-CASE-"):
        raise _fail("$.payload.case_id", "must be a synthetic Stage 2 case ID")
    case_revision = _integer(payload["case_revision"], "$.payload.case_revision", minimum=1)
    if payload["synthetic"] is not True or payload["committed"] is not True:
        raise _fail("$.payload", "source batch must be synthetic and committed")
    committed_at = parse_instant(payload["committed_at"], "$.payload.committed_at")
    records = _list(payload["records"], "$.payload.records")
    if len(records) != len(SOURCE_NAMES):
        raise _fail("$.payload.records", "must contain exactly one record per source")

    record_ids: set[str] = set()
    observed_sources: list[str] = []
    validated_records: list[dict[str, Any]] = []
    for index, raw_record in enumerate(records):
        path = f"$.payload.records[{index}]"
        try:
            record = validate_neutral_record(raw_record)
        except ContractValidationError as error:
            raise SourceValidationError(str(error)) from error
        if record["record_type"] != "source_record" or record["schema_version"] != SOURCE_RECORD_SCHEMA:
            raise _fail(path, "must be a Stage 2 source record")
        if record["record_id"] in record_ids:
            raise _fail(f"{path}.record_id", "duplicate source record ID")
        record_ids.add(record["record_id"])
        item = _mapping(record["payload"], f"{path}.payload")
        _exact_fields(
            item,
            {
                "case_id",
                "case_revision",
                "data",
                "effective_at",
                "ingestion_at",
                "observed_at",
                "provenance_digest",
                "sensitivity_label",
                "source_name",
            },
            f"{path}.payload",
        )
        if item["case_id"] != case_id or item["case_revision"] != case_revision:
            raise _fail(f"{path}.payload", "cross-case or cross-revision source record")
        source_name = _string(item["source_name"], f"{path}.payload.source_name")
        if source_name not in SOURCE_NAMES:
            raise _fail(f"{path}.payload.source_name", "unknown source")
        observed_sources.append(source_name)
        if item["sensitivity_label"] != SENSITIVITY_LABEL:
            raise _fail(f"{path}.payload.sensitivity_label", "must be synthetic-public")
        effective = parse_instant(item["effective_at"], f"{path}.payload.effective_at")
        observed = parse_instant(item["observed_at"], f"{path}.payload.observed_at")
        ingested = parse_instant(item["ingestion_at"], f"{path}.payload.ingestion_at")
        if not effective <= observed <= ingested <= committed_at:
            raise _fail(f"{path}.payload", "future or reversed source timestamps")
        expected_provenance = canonical_sha256(source_provenance_material(item))
        if item["provenance_digest"] != expected_provenance:
            raise _fail(f"{path}.payload.provenance_digest", "provenance digest mismatch")
        _validate_source_data(source_name, item["data"], f"{path}.payload.data")
        validated_records.append(record)

    if observed_sources != list(SOURCE_NAMES):
        raise _fail("$.payload.records", "sources must use the canonical seven-source order")
    policy_record = validated_records[-1]["payload"]
    if parse_instant(policy_record["effective_at"], "policy.effective_at") > committed_at:
        raise _fail("policy.effective_at", "policy cannot become effective after the source cut")
    expected_cut = source_event_cut_sha256(validated_records)
    if payload["source_event_cut_sha256"] != expected_cut:
        raise _fail("$.payload.source_event_cut_sha256", "source event cut digest mismatch")
    ledger_head = _string(payload["ledger_head_digest"], "$.payload.ledger_head_digest")
    if len(ledger_head) != 64 or any(character not in "0123456789abcdef" for character in ledger_head):
        raise _fail("$.payload.ledger_head_digest", "must be a SHA-256 digest")
    expected_pin = revision_pin_sha256(case_id, case_revision, expected_cut, ledger_head)
    if payload["revision_pin_sha256"] != expected_pin:
        raise _fail("$.payload.revision_pin_sha256", "revision pin digest mismatch")
    return dict(batch)


def _records_by_source(batch: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        record["payload"]["source_name"]: record["payload"]
        for record in batch["payload"]["records"]
    }


def derive_case_facts(value: Any) -> dict[str, Any]:
    """Derive operational facts from a validated immutable source cut."""

    batch = validate_source_batch(value)
    payload = batch["payload"]
    records = _records_by_source(batch)
    oms = records["OMS"]["data"]
    wms = records["WMS"]["data"]
    carrier = records["CARRIER"]["data"]
    inventory = records["INVENTORY"]["data"]
    payment = records["PAYMENT"]["data"]
    crm = records["CRM"]["data"]
    policy = records["POLICY"]["data"]
    committed_at = parse_instant(payload["committed_at"], "committed_at")

    line_by_id = {line["line_id"]: dict(line) for line in oms["lines"]}
    shipped_by_line = dict(wms["line_shipments"])
    delivered_by_line = {line_id: 0 for line_id in line_by_id}
    parcel_membership_totals = {line_id: 0 for line_id in line_by_id}
    for line_quantities in wms["parcel_membership"].values():
        for line_id, quantity in line_quantities.items():
            if line_id not in line_by_id:
                raise _fail("WMS.parcel_membership", "references an unknown order line")
            parcel_membership_totals[line_id] += quantity
    for parcel in carrier["parcels"]:
        if parcel["parcel_id"] not in wms["parcel_membership"]:
            raise _fail("CARRIER.parcels", "references a parcel absent from WMS")
        for line_id, quantity in parcel["delivered_quantities"].items():
            if line_id not in line_by_id:
                raise _fail("CARRIER.delivered_quantities", "references an unknown order line")
            delivered_by_line[line_id] += quantity

    has_quantity_conflict = False
    for line_id, line in line_by_id.items():
        ordered = line["ordered_quantity"]
        shipped = shipped_by_line.get(line_id, 0)
        parcelled = parcel_membership_totals[line_id]
        delivered = delivered_by_line[line_id]
        if shipped > ordered or delivered > shipped or parcelled != shipped:
            has_quantity_conflict = True

    source_freshness: dict[str, bool] = {}
    for source_name, record in records.items():
        age_seconds = (committed_at - parse_instant(record["effective_at"], "effective_at")).total_seconds()
        source_freshness[source_name] = age_seconds <= policy["freshness_hours"][source_name] * 3600

    ordered_quantity = sum(line["ordered_quantity"] for line in line_by_id.values())
    shipped_quantity = sum(shipped_by_line.values())
    delivered_quantity = sum(delivered_by_line.values())
    remaining_by_line = {
        line_id: max(line["ordered_quantity"] - delivered_by_line[line_id], 0)
        for line_id, line in line_by_id.items()
    }
    remaining_quantity = sum(remaining_by_line.values())
    affected_value_cents = sum(
        remaining_by_line[line_id] * line["unit_value_cents"]
        for line_id, line in line_by_id.items()
    )
    refunded_cents = sum(entry["amount_cents"] for entry in payment["refund_entries"])
    committed_reship_quantity = sum(
        attempt["quantity"]
        for attempt in oms["prior_action_attempts"]
        if attempt["operation"] == "RESHIP" and attempt["status"] == "COMMITTED"
    )
    has_unresolved_action = any(
        attempt["status"] == "PENDING" for attempt in oms["prior_action_attempts"]
    )
    refunded_quantity = sum(entry["quantity"] for entry in payment["refund_entries"])
    recovered_quantity = committed_reship_quantity + refunded_quantity
    if refunded_cents > affected_value_cents or recovered_quantity > remaining_quantity:
        raise _fail("PAYMENT/OMS", "prior remedy over-recovers the eligible quantity or value")
    signal_ids = [signal["signal_id"] for signal in crm["intake_signals"]]
    duplicate_signal = len(signal_ids) != len(set(signal_ids)) or len(signal_ids) > 1
    risk_signals = sorted(set(crm["risk_signals"]))
    active_chargeback = bool(payment["chargeback_cases"])
    if active_chargeback and "active_chargeback" not in risk_signals:
        risk_signals.append("active_chargeback")
    stop_flags = sorted(set(risk_signals) & set(policy["risk_stop_flags"]))
    reliable_etas = [
        parcel["eta_at"]
        for parcel in carrier["parcels"]
        if parcel["eta_at"] is not None and parcel["eta_reliability"] == "RELIABLE"
    ]

    return {
        "case_id": payload["case_id"],
        "case_revision": payload["case_revision"],
        "source_event_cut_sha256": payload["source_event_cut_sha256"],
        "ledger_head_digest": payload["ledger_head_digest"],
        "revision_pin_sha256": payload["revision_pin_sha256"],
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "currency": oms["currency"],
        "order_value_cents": oms["order_value_cents"],
        "lines": list(line_by_id.values()),
        "parcels": list(carrier["parcels"]),
        "ordered_quantity": ordered_quantity,
        "shipped_quantity": shipped_quantity,
        "delivered_quantity": delivered_quantity,
        "remaining_quantity": remaining_quantity,
        "affected_value_cents": affected_value_cents,
        "captured_amount_cents": payment["captured_amount_cents"],
        "available_replacement_quantity": inventory["available_replacement_quantity"],
        "replacement_quantity": committed_reship_quantity,
        "refunded_quantity": refunded_quantity,
        "refunded_cents": refunded_cents,
        "recovered_quantity": recovered_quantity,
        "prior_remedy_covers_quantity": remaining_quantity > 0 and recovered_quantity >= remaining_quantity,
        "has_unresolved_action": has_unresolved_action,
        "duplicate_signal": duplicate_signal,
        "source_freshness": source_freshness,
        "all_sources_fresh": all(source_freshness.values()),
        "has_source_conflict": has_quantity_conflict
        or payment["captured_amount_cents"] != oms["order_value_cents"],
        "active_chargeback": active_chargeback,
        "risk_stop_flags": stop_flags,
        "customer_choice": crm["customer_choice"],
        "reliable_eta_at": min(reliable_etas) if reliable_etas else None,
        "authority": dict(policy["authority"]),
    }
