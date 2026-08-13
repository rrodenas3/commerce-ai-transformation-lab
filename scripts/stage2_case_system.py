#!/usr/bin/env python3
"""Deterministic Stage 2 development generator and evaluator projections.

This module is intentionally outside the runtime dependency graph.  It may
construct development expectations and invoke inward fact/comparator modules;
runtime modules must never import this generator or its evaluator projections.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from scripts.stage2_contracts import (
    canonical_json_bytes,
    canonical_sha256,
    load_evaluation_contract,
)
from scripts.stage2_current_state import (
    ACTIVE_WORK_MILLISECONDS,
    ASSUMPTION_VERSION,
    DEPENDENCY_WAIT_MILLISECONDS,
    replay_current_state,
    summarise_current_state,
)
from scripts.stage2_facts import (
    POLICY_ID,
    POLICY_VERSION,
    SENSITIVITY_LABEL,
    SOURCE_NAMES,
    revision_pin_sha256,
    source_event_cut_sha256,
    source_provenance_material,
    validate_source_batch,
)


GENERATOR_NAME = "scc-01-stage2-development-generator"
GENERATOR_VERSION = "1.0.0"
GENERATOR_SEED = 20260811
DEVELOPMENT_CASE_COUNT = 24
DATASET_ROLE = "public-development-and-regression"
FROZEN_CLOCK = "2026-08-11T09:00:00Z"
DEFAULT_EFFECTIVE = "2026-08-11T08:45:00Z"
DEFAULT_OBSERVED = "2026-08-11T08:50:00Z"
DEFAULT_INGESTED = "2026-08-11T08:55:00Z"
POLICY_EFFECTIVE = "2026-08-09T00:00:00Z"

FAMILY_SPECS = (
    {"id": "reliable_eta_wait", "choice": "WAIT", "eta": "RELIABLE", "expected": "VERIFIED_WAIT_CONDITION"},
    {"id": "delegated_reship", "choice": "RESHIP", "delivered": 1, "stock": 2, "unit": 1200, "expected": "DELEGATED_ACTION_READY"},
    {"id": "workflow_owner_approval", "choice": "REFUND", "ordered": 1, "unit": 2501, "expected": "WORKFLOW_OWNER_APPROVAL_REQUIRED"},
    {"id": "finance_approval", "choice": "RESHIP", "order_value": 50001, "unit": 5001, "expected": "FINANCE_APPROVAL_REQUIRED"},
    {"id": "evidence_conflict", "choice": "RESHIP", "membership_delta": -1, "expected": "EVIDENCE_BLOCKED"},
    {"id": "control_stop", "choice": "REFUND", "risk": ["active_chargeback"], "chargeback": True, "expected": "CONTROL_STOPPED"},
    {"id": "prior_remedy", "choice": "RESHIP", "prior_committed": True, "expected": "VERIFIED_NO_NEW_ACTION"},
    {"id": "adapter_verification", "choice": "RESHIP", "prior_pending": True, "expected": "ACTION_RECOVERY_REQUIRED"},
    {"id": "idempotent_recovery", "choice": "RESHIP", "prior_pending": True, "duplicate": True, "expected": "ACTION_RECOVERY_REQUIRED"},
    {"id": "revision_invalidation", "choice": "REFUND", "stale_source": "CARRIER", "expected": "EVIDENCE_BLOCKED"},
    {"id": "provider_safety", "choice": "REFUND", "unit": 900, "expected": "DELEGATED_ACTION_READY"},
    {"id": "evidence_integrity", "choice": "RESHIP", "membership_delta": -1, "expected": "EVIDENCE_BLOCKED"},
)


def _source_record(
    case_number: int,
    case_id: str,
    source_name: str,
    data: Mapping[str, Any],
    *,
    effective_at: str = DEFAULT_EFFECTIVE,
) -> dict[str, Any]:
    payload = {
        "case_id": case_id,
        "case_revision": 1,
        "data": dict(data),
        "effective_at": effective_at,
        "ingestion_at": DEFAULT_INGESTED,
        "observed_at": DEFAULT_OBSERVED,
        "sensitivity_label": SENSITIVITY_LABEL,
        "source_name": source_name,
    }
    payload["provenance_digest"] = canonical_sha256(source_provenance_material(payload))
    return {
        "schema_version": "stage2-source-record/v1",
        "record_type": "source_record",
        "record_id": f"S2-SRC-{case_number:04d}-{source_name}",
        "payload": payload,
    }


def _case_batch(case_number: int, spec: Mapping[str, Any], variant: int) -> dict[str, Any]:
    case_id = f"S2-CASE-{case_number:04d}"
    line_count = 1 if variant == 1 else 2
    base_ordered = int(spec.get("ordered", 2))
    delivered = int(spec.get("delivered", 0))
    unit = int(spec.get("unit", 1000 + case_number * 10))
    lines = []
    for line_index in range(1, line_count + 1):
        quantity = base_ordered if line_index == 1 else 1
        lines.append(
            {
                "line_id": f"S2-LINE-{case_number:04d}-{line_index}",
                "ordered_quantity": quantity,
                "unit_value_cents": unit if line_index == 1 else 500,
            }
        )
    order_value = int(
        spec.get(
            "order_value",
            sum(line["ordered_quantity"] * line["unit_value_cents"] for line in lines) + 2000,
        )
    )
    shipped = {line["line_id"]: line["ordered_quantity"] for line in lines}

    parcel_count = 1 if variant == 1 else 2
    parcel_ids = [f"S2-PARCEL-{case_number:04d}-{index}" for index in range(1, parcel_count + 1)]
    membership: dict[str, dict[str, int]] = {parcel_id: {} for parcel_id in parcel_ids}
    for index, line in enumerate(lines):
        membership[parcel_ids[index % parcel_count]][line["line_id"]] = line["ordered_quantity"]
    if spec.get("membership_delta"):
        first_line = lines[0]["line_id"]
        membership[parcel_ids[0]][first_line] = max(
            0, membership[parcel_ids[0]][first_line] + int(spec["membership_delta"])
        )

    parcels = []
    for parcel_index, parcel_id in enumerate(parcel_ids):
        delivered_quantities = {line_id: 0 for line_id in membership[parcel_id]}
        if parcel_index == 0 and delivered:
            delivered_quantities[lines[0]["line_id"]] = min(delivered, lines[0]["ordered_quantity"])
        parcels.append(
            {
                "parcel_id": parcel_id,
                "status": "PARTIAL" if delivered else "DELAYED",
                "delivered_quantities": delivered_quantities,
                "eta_at": "2026-08-12T17:00:00+02:00",
                "eta_reliability": spec.get("eta", "UNRELIABLE"),
            }
        )

    signals = [{"signal_id": f"S2-SIGNAL-{case_number:04d}", "signal_type": "OMS_EXCEPTION"}]
    if spec.get("duplicate"):
        signals.append({"signal_id": f"S2-SIGNAL-{case_number:04d}", "signal_type": "CRM_CONTACT"})
    attempts = []
    if spec.get("prior_committed"):
        attempts.append({"action_id": f"S2-ACTION-{case_number:04d}-1", "operation": "RESHIP", "quantity": sum(line["ordered_quantity"] for line in lines), "status": "COMMITTED"})
    if spec.get("prior_pending"):
        attempts.append({"action_id": f"S2-ACTION-{case_number:04d}-1", "operation": "RESHIP", "quantity": 1, "status": "PENDING"})

    policy = {
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "currency": "EUR",
        "operating_timezone": "Europe/Paris",
        "authority": {
            "delegated_max_exposure_cents": 2500,
            "workflow_owner_max_exposure_cents": 10000,
            "finance_review_order_value_cents": 50000,
        },
        "freshness_hours": {
            "OMS": 2,
            "WMS": 4,
            "CARRIER": 12,
            "INVENTORY": 1,
            "PAYMENT": 2,
            "CRM": 24,
            "POLICY": 8760,
        },
        "risk_stop_flags": ["suspected_fraud", "safety", "privacy", "legal", "active_chargeback"],
    }
    refund_entries = []
    if spec.get("prior_committed") and variant == 2:
        affected = sum(
            line["ordered_quantity"] * line["unit_value_cents"] for line in lines
        )
        refund_entries.append(
            {
                "action_id": f"S2-ACTION-{case_number:04d}-1",
                "amount_cents": affected,
                "currency": "EUR",
                "quantity": sum(line["ordered_quantity"] for line in lines),
            }
        )
        attempts = []

    data_by_source = {
        "OMS": {
            "order_id": f"S2-ORDER-{case_number:04d}",
            "currency": "EUR",
            "order_value_cents": order_value,
            "promised_delivery_at": "2026-08-10T18:00:00+02:00",
            "lines": lines,
            "prior_action_attempts": attempts,
        },
        "WMS": {
            "warehouse_id": "S2-WAREHOUSE-01",
            "line_shipments": shipped,
            "parcel_membership": membership,
        },
        "CARRIER": {"parcels": parcels},
        "INVENTORY": {
            "warehouse_id": "S2-WAREHOUSE-01",
            "available_replacement_quantity": int(spec.get("stock", sum(shipped.values()))),
        },
        "PAYMENT": {
            "currency": "EUR",
            "captured_amount_cents": order_value,
            "refund_entries": refund_entries,
            "chargeback_cases": [f"S2-CHARGEBACK-{case_number:04d}"] if spec.get("chargeback") else [],
        },
        "CRM": {
            "customer_choice": spec.get("choice"),
            "intake_signals": signals,
            "risk_signals": list(spec.get("risk", [])),
        },
        "POLICY": policy,
    }
    records = []
    for source_name in SOURCE_NAMES:
        effective = POLICY_EFFECTIVE if source_name == "POLICY" else DEFAULT_EFFECTIVE
        if spec.get("stale_source") == source_name:
            effective = "2026-08-10T18:00:00Z"
        records.append(
            _source_record(case_number, case_id, source_name, data_by_source[source_name], effective_at=effective)
        )
    source_cut = source_event_cut_sha256(records)
    ledger_head = canonical_sha256(
        {
            "case_id": case_id,
            "event": "SOURCE_BATCH_GENESIS",
            "source_event_cut_sha256": source_cut,
        }
    )
    payload = {
        "case_id": case_id,
        "case_revision": 1,
        "committed": True,
        "committed_at": FROZEN_CLOCK,
        "ledger_head_digest": ledger_head,
        "records": records,
        "source_event_cut_sha256": source_cut,
        "revision_pin_sha256": revision_pin_sha256(case_id, 1, source_cut, ledger_head),
        "synthetic": True,
    }
    batch = {
        "schema_version": "stage2-source-batch/v1",
        "record_type": "source_batch",
        "record_id": f"S2-BATCH-{case_number:04d}",
        "payload": payload,
    }
    return validate_source_batch(batch)


def build_development_case_material(project_root: Path) -> dict[str, Any]:
    """Build the fixed 24-case public development denominator and expectations."""

    contract_path = project_root / "data" / "stage2" / "evaluation-contract.json"
    contract = load_evaluation_contract(contract_path)
    planned_families = [
        family["family_id"] for family in contract["case_plan"]["families"]
    ]
    spec_by_family = {spec["id"]: spec for spec in FAMILY_SPECS}
    if planned_families != list(spec_by_family):
        raise ValueError("Stage 2 development families must match the frozen U1 order")
    coverage_by_family = {family_id: [] for family_id in planned_families}
    for acceptance_example, family_ids in contract["case_plan"][
        "acceptance_example_coverage"
    ].items():
        for family_id in family_ids:
            coverage_by_family[family_id].append(acceptance_example)
    batches = []
    projections = []
    case_number = 0
    for family_plan in contract["case_plan"]["families"]:
        spec = spec_by_family[family_plan["family_id"]]
        if family_plan["development_cases"] != 2:
            raise ValueError("U2 requires two development cases per frozen family")
        for variant in (1, 2):
            case_number += 1
            batch = _case_batch(case_number, spec, variant)
            batches.append(batch)
            projections.append(
                {
                    "schema_version": "stage2-development-evaluator-projection/v1",
                    "case_id": batch["payload"]["case_id"],
                    "case_revision": 1,
                    "evaluation_family": spec["id"],
                    "applicable_acceptance_examples": sorted(
                        coverage_by_family[spec["id"]],
                        key=lambda value: int(value[2:]),
                    ),
                    "expected_deterministic_outcome": spec["expected"],
                    "use_boundary": "development-and-regression-only",
                    "independent_validation": False,
                }
            )
    return {"case_batches": batches, "evaluator_projections": projections}


def _jsonl_bytes(records: list[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) for record in records)


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def generate_stage2_development_artifacts(project_root: Path, output_root: Path) -> dict[str, Any]:
    """Generate the public case, comparator, assumption, and manifest artifacts."""

    material = build_development_case_material(project_root)
    batches = material["case_batches"]
    evaluator = material["evaluator_projections"]
    current_rows = [replay_current_state(batch) for batch in batches]
    summary = summarise_current_state(current_rows)
    assumptions = {
        "schema_version": ASSUMPTION_VERSION,
        "evidence_label": "hypothetical-impact",
        "active_work_milliseconds": ACTIVE_WORK_MILLISECONDS,
        "dependency_wait_milliseconds": DEPENDENCY_WAIT_MILLISECONDS,
        "claim_boundary": "Versioned hypothetical process assumptions; no human time was observed.",
    }
    artifacts = {
        "cases.jsonl": _jsonl_bytes(batches),
        "evaluator-projections.jsonl": _jsonl_bytes(evaluator),
        "current-state-results.jsonl": _jsonl_bytes(current_rows),
        "current-state-summary.json": canonical_json_bytes(summary),
        "current-state-assumptions.json": canonical_json_bytes(assumptions),
    }
    for relative, payload in artifacts.items():
        _write(output_root / relative, payload)
    manifest = {
        "schema_version": "stage2-development-manifest/v1",
        "dataset_role": DATASET_ROLE,
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION, "seed": GENERATOR_SEED},
        "generated_at": FROZEN_CLOCK,
        "case_count": len(batches),
        "family_counts": dict(sorted(Counter(row["evaluation_family"] for row in evaluator).items())),
        "contains_real_data": False,
        "human_data": "not_observed",
        "independent_validation": False,
        "supported_maturity": "specification",
        "claim_boundary": "Public synthetic development/regression material; not confirmatory, human, pilot, or production evidence.",
        "artifacts_sha256": {
            relative: hashlib.sha256(payload).hexdigest()
            for relative, payload in sorted(artifacts.items())
        },
    }
    _write(output_root / "manifest.json", canonical_json_bytes(manifest))
    return manifest
