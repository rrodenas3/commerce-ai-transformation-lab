"""Generate and prepare the process-controlled Stage 1 held-out evaluation pack.

The public pack contains synthetic cases and hash commitments. The answer-bearing
oracle and deterministic generation material remain under an ignored private
directory until a completed human record has been frozen in Git.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.stage1_case_system import (
    CASE_FAMILIES,
    HELDOUT_DATASET_ROLE,
    _base_case,
    build_oracle,
    load_stage1_policy,
    read_jsonl,
    validate_case,
    validate_oracle,
    write_utf8_lf,
)
from scripts.stage1_scoring import (
    HELDOUT_ALLOWED_TOOLS,
    HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
    HELDOUT_ORACLE_EXPOSURE_PREPARED,
    HELDOUT_PROHIBITED_TOOLS,
    HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
    MANUAL_RUN_TYPE_BY_OPERATOR_ROLE,
    REVIEWER_CODE_PATTERN,
    RUN_ID_PATTERN,
    SAFE_MESSAGE_FACTS,
    write_manual_template,
)


HELDOUT_PACK_SCHEMA_VERSION = "1.1.0"
HELDOUT_PRIVATE_SCHEMA_VERSION = "1.1.0"
HELDOUT_GENERATOR_NAME = "scc-01-heldout-case-generator"
HELDOUT_GENERATOR_VERSION = "2.0.0"
HELDOUT_EVALUATION_PACK_ID = "SCC-01-HO-V2"
HELDOUT_CASE_COUNT = 32
HELDOUT_PUBLIC_PATH = "data/stage1/heldout/v2"
HELDOUT_PRIVATE_PATH = "artifacts/private/stage1/heldout/v2"
HELDOUT_ORACLE_RELEASE_PATH = "data/stage1/heldout/v2/oracle.released.jsonl"
HELDOUT_ORACLE_RELEASE_MANIFEST_PATH = (
    "data/stage1/heldout/v2/oracle-release-manifest.json"
)
OPERATOR_GUIDE_FILE = "operator-guide.json"
GENERATION_MATERIAL_FILE = "generation-material.json"
PRIVATE_ORACLE_FILE = "oracle.jsonl"
OPERATOR_HIDDEN_FIELDS = frozenset({"case_family", "generator", "title"})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, sort_keys=True) + "\n" for record in records
    ).encode("utf-8")


def _parse_jsonl_bytes(value: bytes, label: str) -> list[dict[str, Any]]:
    try:
        records = [
            json.loads(line) for line in value.decode("utf-8").splitlines() if line
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSONL") from error
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"{label} must contain JSON objects")
    return records


def build_operator_guide(policy: dict[str, Any]) -> dict[str, Any]:
    authority = policy["authority"]
    return {
        "schema_version": "1.0.0",
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "policy": {
            "policy_id": policy["policy_id"],
            "version": policy["version"],
        },
        "purpose": (
            "Apply the frozen synthetic recovery policy to each case. This guide "
            "defines worksheet codes and decision priority; it contains no case "
            "answers."
        ),
        "decision_priority": [
            {
                "priority": 1,
                "when": (
                    "risk_flags is non-empty or payment.active_chargeback is true"
                ),
                "action": "ESCALATE_SPECIALIST",
                "route": "specialist",
            },
            {
                "priority": 2,
                "when": (
                    "history.prior_action is VERIFIED and its authoritative refund "
                    "or replacement postcondition is complete"
                ),
                "action": "NO_ACTION_ALREADY_RECOVERED",
                "route": "delegated",
            },
            {
                "priority": 3,
                "when": (
                    "history.prior_action exists but is PENDING, FAILED_SAFE, "
                    "UNVERIFIED, or VERIFIED without its authoritative postcondition"
                ),
                "action": "ESCALATE_ACTION_RECOVERY",
                "route": "specialist",
            },
            {
                "priority": 4,
                "when": (
                    "evidence.source_conflict is true or any required source is "
                    "missing or stale"
                ),
                "action": "ESCALATE_EVIDENCE",
                "route": "specialist",
            },
            {
                "priority": 5,
                "when": "trigger.duplicate is true",
                "action": "NO_ACTION_DUPLICATE_SIGNAL",
                "route": "delegated",
            },
            {
                "priority": 6,
                "when": (
                    "carrier.status is IN_TRANSIT and customer.preference is wait"
                ),
                "action": "WAIT_VERIFIED_ETA",
                "route": "delegated",
            },
            {
                "priority": 7,
                "when": (
                    "inventory.reservable is true and customer.preference is not "
                    "refund_missing"
                ),
                "action": "RESHIP_MISSING",
                "route": "apply authority routing",
            },
            {
                "priority": 8,
                "when": "none of the earlier conditions applies",
                "action": "REFUND_MISSING",
                "route": "apply authority routing",
            },
        ],
        "authoritative_postconditions": {
            "common_requirement": "evidence.source_conflict is false",
            "selection_rule": "apply the rule matching history.prior_action.type",
            "by_prior_action_type": {
                "REFUND_MISSING": (
                    "PAYMENT is available and fresh, and payment.refunded_cents is at "
                    "least order.affected_value_cents"
                ),
                "RESHIP_MISSING": (
                    "INVENTORY is available and fresh; replacement_reservation has "
                    "the same action_id, VERIFIED status, and reserved_qty at least "
                    "order.remaining_qty"
                ),
            },
        },
        "authority_routing_priority": [
            {
                "priority": 1,
                "when": (
                    f"order.affected_value_cents > "
                    f"{authority['team_lead_max_exposure_cents']} or "
                    f"order.order_value_cents > "
                    f"{authority['finance_review_order_value_cents']}"
                ),
                "route": "approval",
                "decision_owner": "finance_duty_approver",
            },
            {
                "priority": 2,
                "when": (
                    f"order.affected_value_cents > "
                    f"{authority['delegated_max_exposure_cents']} or "
                    "history.repeat_recovery is true"
                ),
                "route": "approval",
                "decision_owner": "workflow_owner",
            },
            {
                "priority": 3,
                "when": "neither approval condition applies",
                "route": "delegated",
                "decision_owner": "customer_recovery_specialist",
            },
        ],
        "evidence_codes": list(policy["required_case_sources"]),
        "evidence_rules": {
            "WAIT_VERIFIED_ETA": ["OMS", "WMS", "CARRIER", "POLICY"],
            "RESHIP_MISSING": list(policy["required_case_sources"]),
            "REFUND_MISSING": list(policy["required_case_sources"]),
            "NO_ACTION_ALREADY_RECOVERED": [
                "OMS",
                "WMS",
                "CARRIER",
                "POLICY",
                "CRM",
                "PAYMENT",
            ],
            "NO_ACTION_DUPLICATE_SIGNAL": [
                "OMS",
                "WMS",
                "CARRIER",
                "POLICY",
                "CRM",
                "PAYMENT",
            ],
            "ESCALATION": "list every available source code",
        },
        "message_fact_codes": {
            "case_received": "The recovery case was received.",
            "no_new_action_created": "No new recovery action was created.",
            "order_reference_confirmed": "The synthetic order reference was confirmed.",
            "recovery_under_review": "Recovery remains under review.",
            "refund_requires_verified_execution": (
                "A refund recommendation is not a completed refund."
            ),
            "replacement_requires_verified_execution": (
                "A replacement recommendation is not a completed reshipment."
            ),
            "revised_eta_is_estimate": "A revised ETA remains an estimate.",
        },
        "message_fact_rules": {
            "always_allowed": [
                "case_received",
                "order_reference_confirmed",
                "recovery_under_review",
            ],
            "additional_by_action": {
                "WAIT_VERIFIED_ETA": ["revised_eta_is_estimate"],
                "RESHIP_MISSING": ["replacement_requires_verified_execution"],
                "REFUND_MISSING": ["refund_requires_verified_execution"],
                "NO_ACTION_ALREADY_RECOVERED": ["no_new_action_created"],
                "NO_ACTION_DUPLICATE_SIGNAL": ["no_new_action_created"],
                "ESCALATE_EVIDENCE": [],
                "ESCALATE_ACTION_RECOVERY": [],
                "ESCALATE_SPECIALIST": [],
            },
        },
        "worksheet_codes": {
            "recommended_action": list(policy["allowed_actions"]),
            "route": ["delegated", "approval", "specialist"],
            "message_facts": sorted(SAFE_MESSAGE_FACTS),
            "help_requested": ["true", "false"],
            "confidence_1_to_5": [1, 2, 3, 4, 5],
        },
        "worksheet_fields": {
            "case_id": (
                "Prefilled immutable case identifier. Do not edit or reorder rows."
            ),
            "reviewer_code": ("Prefilled pseudonymous operator code. Do not edit."),
            "run_type": "Prefilled evidence mode. Do not edit.",
            "started_at_utc": (
                "Use the allowed system clock immediately before reading the case; "
                "record UTC as YYYY-MM-DDTHH:MM:SSZ."
            ),
            "ended_at_utc": (
                "Use the allowed system clock immediately after completing the row; "
                "record UTC as YYYY-MM-DDTHH:MM:SSZ and not before started_at_utc."
            ),
            "active_handling_seconds": (
                "Whole seconds spent actively reading, deciding, and recording this "
                "case. Exclude interruptions; enter 0 or more and never exceed the "
                "elapsed time between the two UTC timestamps."
            ),
            "recommended_action": (
                "One exact recommended_action code from worksheet_codes. This is a "
                "recommendation, not proof of execution."
            ),
            "route": (
                "One exact route code from worksheet_codes. This is the required "
                "operational route, not an observed handoff."
            ),
            "evidence_used_pipe_delimited": (
                "One or more exact evidence codes actually used. Separate multiple "
                "codes with | and no surrounding spaces; never invent a source code."
            ),
            "message_facts_pipe_delimited": (
                "One or more supported message-fact codes. Separate multiple codes "
                "with | and no surrounding spaces; do not write customer prose."
            ),
            "confidence_1_to_5": (
                "Whole number from 1 (very uncertain) to 5 (very certain) describing "
                "confidence in the recorded decision, not confidence in an outcome."
            ),
            "help_requested": (
                "true only if interpretive help from another person was requested "
                "during this case; otherwise false. A prohibited tool or coaching "
                "exposure invalidates the run rather than counting as help."
            ),
            "handoff_count": (
                "Whole number 0 or greater counting actual transfers of handling "
                "responsibility during this case. A recommended approval or specialist "
                "route alone is not an observed handoff."
            ),
            "policy_lookup_count": (
                "Whole number 0 or greater counting distinct consultation episodes of "
                "the prepared policy or operator guide after case timing begins. "
                "Continuous reading in one episode counts once."
            ),
            "notes_without_personal_data": (
                "Optional brief observation about friction, ambiguity, or reasoning. "
                "Leave blank or use synthetic-safe text only; never add personal data, "
                "secrets, customer prose, or external content."
            ),
        },
        "worksheet_serialization": {
            "format": "CSV using the existing header and row order",
            "encoding": "UTF-8 without BOM",
            "line_ending": "LF only",
            "final_line_ending": "required",
            "column_delimiter": ",",
            "multi_value_delimiter": "|",
            "rules": [
                "Preserve every column, prefilled value, case ID, and row order.",
                "Use standard CSV quoting when a value contains a comma or quote.",
                "Do not add columns, comments, formulas, or spreadsheet metadata.",
            ],
        },
        "handling_rules": [
            "Apply decision_priority from lowest number to highest and stop at the first match.",
            "Use pipe characters between multiple evidence or message-fact codes.",
            "Record only supported message facts; a recommendation is never a verified outcome.",
            "Do not include personal data, secrets, or copied customer free text in notes.",
        ],
    }


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must be timezone-aware UTC")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _write_new_or_equal(
    path: Path, value: bytes, label: str, *, private: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600 if private else 0o644)
    except FileExistsError:
        if not path.is_file() or path.read_bytes() != value:
            raise ValueError(
                f"existing {label} differs; create a new held-out pack version"
            )
        return
    try:
        handle = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    try:
        with handle:
            handle.write(value)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _blueprints() -> list[tuple[str, str, dict[str, Any]]]:
    """Return balanced semantic variants without embedding their final order."""
    return [
        (
            "delayed_reliable",
            "Synthetic exception variant 01",
            {
                "customer_preference": "wait",
                "carrier_status": "IN_TRANSIT",
                "affected_value_cents": 2100,
            },
        ),
        (
            "delayed_reliable",
            "Synthetic exception variant 02",
            {"replacement_stock_qty": 2, "affected_value_cents": 2300},
        ),
        (
            "delayed_reliable",
            "Synthetic exception variant 03",
            {
                "carrier_status": "LOST_CONFIRMED",
                "revised_eta_reliable": False,
                "affected_value_cents": 7200,
                "order_value_cents": 18000,
            },
        ),
        (
            "delayed_reliable",
            "Synthetic exception variant 04",
            {
                "repeat_recovery": True,
                "replacement_stock_qty": 2,
                "affected_value_cents": 1900,
            },
        ),
        (
            "partial_stock_available",
            "Synthetic exception variant 05",
            {
                "ordered_qty": 4,
                "delivered_qty": 2,
                "replacement_stock_qty": 2,
                "affected_value_cents": 2400,
            },
        ),
        (
            "partial_stock_available",
            "Synthetic exception variant 06",
            {
                "ordered_qty": 5,
                "delivered_qty": 3,
                "replacement_stock_qty": 2,
                "affected_value_cents": 6800,
                "order_value_cents": 17000,
            },
        ),
        (
            "partial_stock_available",
            "Synthetic exception variant 07",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "customer_preference": "refund_missing",
                "affected_value_cents": 2200,
            },
        ),
        (
            "partial_stock_available",
            "Synthetic exception variant 08",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "affected_value_cents": 1800,
                "order_value_cents": 52000,
            },
        ),
        (
            "partial_no_stock",
            "Synthetic exception variant 09",
            {"delivered_qty": 1, "affected_value_cents": 2000},
        ),
        (
            "partial_no_stock",
            "Synthetic exception variant 10",
            {
                "ordered_qty": 3,
                "delivered_qty": 1,
                "affected_value_cents": 6400,
                "order_value_cents": 15000,
            },
        ),
        (
            "partial_no_stock",
            "Synthetic exception variant 11",
            {
                "delivered_qty": 1,
                "affected_value_cents": 11800,
                "order_value_cents": 42000,
            },
        ),
        (
            "partial_no_stock",
            "Synthetic exception variant 12",
            {"delivered_qty": 1, "affected_value_cents": 1800, "repeat_recovery": True},
        ),
        (
            "conflicting_evidence",
            "Synthetic exception variant 13",
            {
                "delivered_qty": 1,
                "source_conflict": True,
                "carrier_status": "DELIVERED",
            },
        ),
        (
            "conflicting_evidence",
            "Synthetic exception variant 14",
            {"delivered_qty": 1, "missing_sources": ("PAYMENT",)},
        ),
        (
            "conflicting_evidence",
            "Synthetic exception variant 15",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "stale_sources": ("INVENTORY",),
            },
        ),
        (
            "conflicting_evidence",
            "Synthetic exception variant 16",
            {"delivered_qty": 1, "missing_sources": ("CRM",)},
        ),
        (
            "duplicate_or_stale",
            "Synthetic exception variant 17",
            {
                "duplicate_signal": True,
                "customer_preference": "wait",
                "carrier_status": "IN_TRANSIT",
            },
        ),
        (
            "duplicate_or_stale",
            "Synthetic exception variant 18",
            {
                "delivered_qty": 1,
                "affected_value_cents": 2100,
                "refunded_cents": 2100,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-R1",
                    "type": "REFUND_MISSING",
                    "status": "VERIFIED",
                },
            },
        ),
        (
            "duplicate_or_stale",
            "Synthetic exception variant 19",
            {
                "delivered_qty": 1,
                "affected_value_cents": 2300,
                "refunded_cents": 1200,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-R2",
                    "type": "REFUND_MISSING",
                    "status": "VERIFIED",
                },
            },
        ),
        (
            "duplicate_or_stale",
            "Synthetic exception variant 20",
            {
                "delivered_qty": 1,
                "duplicate_signal": True,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-R3",
                    "type": "REFUND_MISSING",
                    "status": "PENDING",
                },
            },
        ),
        (
            "authority_boundary",
            "Synthetic exception variant 21",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "affected_value_cents": 2500,
            },
        ),
        (
            "authority_boundary",
            "Synthetic exception variant 22",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "affected_value_cents": 2501,
            },
        ),
        (
            "authority_boundary",
            "Synthetic exception variant 23",
            {
                "delivered_qty": 1,
                "affected_value_cents": 10000,
                "order_value_cents": 30000,
            },
        ),
        (
            "authority_boundary",
            "Synthetic exception variant 24",
            {
                "delivered_qty": 1,
                "affected_value_cents": 10001,
                "order_value_cents": 30000,
            },
        ),
        (
            "retry_and_verification",
            "Synthetic exception variant 25",
            {
                "delivered_qty": 1,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-T1",
                    "type": "REFUND_MISSING",
                    "status": "PENDING",
                },
            },
        ),
        (
            "retry_and_verification",
            "Synthetic exception variant 26",
            {
                "delivered_qty": 1,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-T2",
                    "type": "REFUND_MISSING",
                    "status": "FAILED_SAFE",
                },
            },
        ),
        (
            "retry_and_verification",
            "Synthetic exception variant 27",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "prior_action": {
                    "action_id": "SYN-HO-ACT-T3",
                    "type": "RESHIP_MISSING",
                    "status": "UNVERIFIED",
                },
            },
        ),
        (
            "retry_and_verification",
            "Synthetic exception variant 28",
            {
                "delivered_qty": 1,
                "replacement_stock_qty": 1,
                "replacement_reservation": {
                    "action_id": "SYN-HO-ACT-T4",
                    "status": "VERIFIED",
                    "reserved_qty": 1,
                },
                "prior_action": {
                    "action_id": "SYN-HO-ACT-T4",
                    "type": "RESHIP_MISSING",
                    "status": "VERIFIED",
                },
            },
        ),
        (
            "out_of_scope_risk",
            "Synthetic exception variant 29",
            {"risk_flags": ("safety",)},
        ),
        (
            "out_of_scope_risk",
            "Synthetic exception variant 30",
            {"risk_flags": ("privacy",)},
        ),
        (
            "out_of_scope_risk",
            "Synthetic exception variant 31",
            {"risk_flags": ("suspected_fraud",)},
        ),
        (
            "out_of_scope_risk",
            "Synthetic exception variant 32",
            {"active_chargeback": True},
        ),
    ]


def build_heldout_cases(
    policy: dict[str, Any], generation_material: str
) -> list[dict[str, Any]]:
    if not isinstance(generation_material, str) or len(generation_material) < 32:
        raise ValueError("generation material must contain at least 32 characters")
    if policy.get("version") != "1.0.0":
        raise ValueError("held-out generator supports only policy version 1.0.0")
    material_digest = _sha256_bytes(generation_material.encode("utf-8"))
    rng = random.Random(int(material_digest, 16))
    blueprints = _blueprints()
    if len(blueprints) != HELDOUT_CASE_COUNT:
        raise ValueError("held-out blueprint count is inconsistent")
    rng.shuffle(blueprints)

    cases: list[dict[str, Any]] = []
    used_tokens: set[int] = set()
    for sequence, (family, title, kwargs) in enumerate(blueprints, start=1):
        case = _base_case(policy, 1000 + sequence, family, title, **kwargs)
        token = rng.randrange(100000, 999999)
        while token in used_tokens:
            token = rng.randrange(100000, 999999)
        used_tokens.add(token)
        case_id = f"SCC-01-HO2-{sequence:03d}"
        case.update(
            {
                "case_id": case_id,
                "dataset_role": HELDOUT_DATASET_ROLE,
                "generator": {
                    "name": HELDOUT_GENERATOR_NAME,
                    "version": HELDOUT_GENERATOR_VERSION,
                    "seed_commitment_sha256": material_digest,
                },
            }
        )
        case["trigger"]["signal_id"] = f"SYN-HO-SIG-{token}"
        case["order"]["order_id"] = f"SYN-HO-O-{token}"
        case["customer"]["customer_id"] = f"SYN-HO-C-{token}"
        case["carrier"]["parcel_id"] = f"SYN-HO-P-{token}"
        cases.append(case)

    errors = [error for case in cases for error in validate_case(case, policy)]
    if errors:
        raise ValueError("invalid held-out cases: " + "; ".join(errors))
    return cases


def build_operator_case_pack(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove evaluator-only labels from the case facts shown to an operator."""
    return [
        {key: value for key, value in case.items() if key not in OPERATOR_HIDDEN_FIELDS}
        for case in cases
    ]


def validate_operator_case(case: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    case_id = case.get("case_id", "<missing>")
    exposed = sorted(OPERATOR_HIDDEN_FIELDS.intersection(case))
    if exposed:
        return [f"{case_id}: evaluator-only fields exposed: {', '.join(exposed)}"]
    validation_copy = dict(case)
    validation_copy["case_family"] = CASE_FAMILIES[0]
    validation_copy["generator"] = {
        "name": HELDOUT_GENERATOR_NAME,
        "version": HELDOUT_GENERATOR_VERSION,
    }
    return validate_case(validation_copy, policy)


def _load_or_create_generation_material(private_output: Path) -> str:
    path = private_output / GENERATION_MATERIAL_FILE
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("private generation material is invalid") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != HELDOUT_PRIVATE_SCHEMA_VERSION
            or value.get("evaluation_pack_id") != HELDOUT_EVALUATION_PACK_ID
            or not isinstance(value.get("generation_material"), str)
        ):
            raise ValueError("private generation material is inconsistent")
        return value["generation_material"]

    material = secrets.token_hex(32)
    value = {
        "schema_version": HELDOUT_PRIVATE_SCHEMA_VERSION,
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generation_material": material,
    }
    _write_new_or_equal(
        path,
        _canonical_json_bytes(value),
        "private generation material",
        private=True,
    )
    return material


def generate_heldout_artifacts(
    project_root: Path,
    public_output: Path,
    private_output: Path,
    *,
    generation_material: str | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    public = public_output.resolve()
    private = private_output.resolve()
    material = generation_material or _load_or_create_generation_material(private)
    policy = load_stage1_policy(root)
    internal_cases = build_heldout_cases(policy, material)
    cases = build_operator_case_pack(internal_cases)
    operator_guide = build_operator_guide(policy)
    oracles = [
        build_oracle(case, policy, heldout_release_material=material)
        for case in internal_cases
    ]
    oracle_errors = [
        error for oracle in oracles for error in validate_oracle(oracle, policy)
    ]
    if oracle_errors:
        raise ValueError("invalid held-out oracles: " + "; ".join(oracle_errors))

    cases_bytes = _canonical_jsonl_bytes(cases)
    operator_guide_bytes = _canonical_json_bytes(operator_guide)
    oracle_bytes = _canonical_jsonl_bytes(oracles)
    policy_bytes = (root / "data" / "stage1" / "policy.json").read_bytes()
    material_commitment = _sha256_bytes(material.encode("utf-8"))
    manifest = {
        "schema_version": HELDOUT_PACK_SCHEMA_VERSION,
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generated_at": max(case["observed_at"] for case in internal_cases),
        "generator_name": HELDOUT_GENERATOR_NAME,
        "generator_version": HELDOUT_GENERATOR_VERSION,
        "generator_seed_status": "withheld-until-record-freeze",
        "generator_seed_commitment_sha256": material_commitment,
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "policy_sha256": _sha256_bytes(policy_bytes),
        "oracle_version": "1.0.0",
        "oracle_release_status": "answer-file-not-published",
        "oracle_release_path": HELDOUT_ORACLE_RELEASE_PATH,
        "oracle_release_manifest_path": HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
        "case_count": len(cases),
        "case_families": dict(
            sorted(Counter(case["case_family"] for case in internal_cases).items())
        ),
        "dataset_role": HELDOUT_DATASET_ROLE,
        "held_out_evaluation_set": True,
        "contains_real_data": False,
        "artifacts_sha256": {
            "cases.jsonl": _sha256_bytes(cases_bytes),
            OPERATOR_GUIDE_FILE: _sha256_bytes(operator_guide_bytes),
            "oracle.released.jsonl": _sha256_bytes(oracle_bytes),
        },
    }
    private_manifest = {
        "schema_version": HELDOUT_PRIVATE_SCHEMA_VERSION,
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generator_seed_commitment_sha256": material_commitment,
        "cases_sha256": _sha256_bytes(cases_bytes),
        "operator_guide_sha256": _sha256_bytes(operator_guide_bytes),
        "oracle_sha256": _sha256_bytes(oracle_bytes),
        "public_manifest_sha256": _sha256_bytes(_canonical_json_bytes(manifest)),
    }

    _write_new_or_equal(public / "cases.jsonl", cases_bytes, "held-out cases")
    _write_new_or_equal(
        public / OPERATOR_GUIDE_FILE,
        operator_guide_bytes,
        "held-out operator guide",
    )
    _write_new_or_equal(
        public / "manifest.json",
        _canonical_json_bytes(manifest),
        "held-out public manifest",
    )
    if generation_material is not None:
        _write_new_or_equal(
            private / GENERATION_MATERIAL_FILE,
            _canonical_json_bytes(
                {
                    "schema_version": HELDOUT_PRIVATE_SCHEMA_VERSION,
                    "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
                    "generation_material": material,
                }
            ),
            "private generation material",
            private=True,
        )
    _write_new_or_equal(
        private / PRIVATE_ORACLE_FILE,
        oracle_bytes,
        "private held-out oracle",
        private=True,
    )
    _write_new_or_equal(
        private / "manifest.json",
        _canonical_json_bytes(private_manifest),
        "private held-out manifest",
        private=True,
    )
    return manifest


def validate_heldout_public_pack(
    project_root: Path,
    public_output: Path,
    *,
    require_unreleased: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes, bytes, bytes, bytes]:
    root = project_root.resolve()
    public = public_output.resolve()
    manifest_path = public / "manifest.json"
    cases_path = public / "cases.jsonl"
    operator_guide_path = public / OPERATOR_GUIDE_FILE
    if (
        not manifest_path.is_file()
        or not cases_path.is_file()
        or not operator_guide_path.is_file()
    ):
        raise ValueError("held-out public pack is incomplete")
    manifest_bytes = manifest_path.read_bytes()
    cases_bytes = cases_path.read_bytes()
    operator_guide_bytes = operator_guide_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("held-out public manifest is invalid") from error
    if not isinstance(manifest, dict):
        raise ValueError("held-out public manifest must be an object")
    expected = {
        "schema_version": HELDOUT_PACK_SCHEMA_VERSION,
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generator_name": HELDOUT_GENERATOR_NAME,
        "generator_version": HELDOUT_GENERATOR_VERSION,
        "generator_seed_status": "withheld-until-record-freeze",
        "dataset_role": HELDOUT_DATASET_ROLE,
        "held_out_evaluation_set": True,
        "contains_real_data": False,
        "oracle_release_status": "answer-file-not-published",
        "oracle_release_path": HELDOUT_ORACLE_RELEASE_PATH,
        "oracle_release_manifest_path": HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
        "case_count": HELDOUT_CASE_COUNT,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"held-out public manifest has stale {key}")
    policy_path = root / "data" / "stage1" / "policy.json"
    policy_bytes = policy_path.read_bytes()
    if manifest.get("policy_sha256") != _sha256_bytes(policy_bytes):
        raise ValueError("held-out public manifest policy hash is stale")
    hashes = manifest.get("artifacts_sha256")
    if not isinstance(hashes, dict) or hashes.get("cases.jsonl") != _sha256_bytes(
        cases_bytes
    ):
        raise ValueError("held-out public cases hash is stale")
    if not isinstance(hashes.get("oracle.released.jsonl"), str):
        raise ValueError("held-out oracle commitment is missing")
    if hashes.get(OPERATOR_GUIDE_FILE) != _sha256_bytes(operator_guide_bytes):
        raise ValueError("held-out operator guide hash is stale")
    if require_unreleased and (root / HELDOUT_ORACLE_RELEASE_PATH).exists():
        raise ValueError(
            "held-out oracle is already released; prepare a new pack version"
        )
    cases = _parse_jsonl_bytes(cases_bytes, "held-out public cases")
    if len(cases) != HELDOUT_CASE_COUNT:
        raise ValueError("held-out case count is stale")
    case_ids = [case.get("case_id") for case in cases]
    if len(set(case_ids)) != len(case_ids) or any(
        not isinstance(case_id, str) or not case_id for case_id in case_ids
    ):
        raise ValueError("held-out cases require unique IDs")
    policy = load_stage1_policy(root)
    try:
        operator_guide = json.loads(operator_guide_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("held-out operator guide is invalid") from error
    if operator_guide != build_operator_guide(policy):
        raise ValueError("held-out operator guide is stale")
    errors = [error for case in cases for error in validate_operator_case(case, policy)]
    if errors:
        raise ValueError("invalid held-out cases: " + "; ".join(errors))
    for case in cases:
        if case.get("dataset_role") != HELDOUT_DATASET_ROLE:
            raise ValueError("held-out case dataset role is stale")
    return (
        manifest,
        cases,
        manifest_bytes,
        cases_bytes,
        policy_bytes,
        operator_guide_bytes,
    )


def _relative_public_path(root: Path, path: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"{label} must be inside the project root") from error


def _file_pin(path: Path, public_path: str) -> dict[str, str]:
    return {"path": public_path, "sha256": _sha256_bytes(path.read_bytes())}


def validate_heldout_run_bindings(
    run_metadata: dict[str, Any],
    public_manifest: dict[str, Any],
    *,
    public_manifest_bytes: bytes,
    public_cases_bytes: bytes,
    public_operator_guide_bytes: bytes,
    prepared_case_pack_bytes: bytes,
    prepared_operator_guide_bytes: bytes,
    policy_bytes: bytes,
    prepared_policy_bytes: bytes,
    instructions_bytes: bytes,
) -> None:
    """Bind a prepared run to the current committed pack and protocol."""
    if (
        run_metadata.get("schema_version") != HELDOUT_RUN_MANIFEST_SCHEMA_VERSION
        or run_metadata.get("dataset_role") != HELDOUT_DATASET_ROLE
        or run_metadata.get("oracle_exposure_status")
        != HELDOUT_ORACLE_EXPOSURE_PREPARED
    ):
        raise ValueError("run manifest is not a process-controlled held-out run")
    expected_public = {
        "schema_version": HELDOUT_PACK_SCHEMA_VERSION,
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generator_name": HELDOUT_GENERATOR_NAME,
        "generator_version": HELDOUT_GENERATOR_VERSION,
        "generator_seed_status": "withheld-until-record-freeze",
        "dataset_role": HELDOUT_DATASET_ROLE,
        "held_out_evaluation_set": True,
        "contains_real_data": False,
        "oracle_release_status": "answer-file-not-published",
        "oracle_release_path": HELDOUT_ORACLE_RELEASE_PATH,
        "oracle_release_manifest_path": HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
        "case_count": HELDOUT_CASE_COUNT,
    }
    for key, expected in expected_public.items():
        if public_manifest.get(key) != expected:
            raise ValueError(f"held-out public manifest has stale {key}")
    public_hashes = public_manifest.get("artifacts_sha256")
    if not isinstance(public_hashes, dict):
        raise ValueError("held-out public manifest hashes are incomplete")
    cases_hash = _sha256_bytes(public_cases_bytes)
    oracle_hash = public_hashes.get("oracle.released.jsonl")
    if public_hashes.get("cases.jsonl") != cases_hash or not isinstance(
        oracle_hash, str
    ):
        raise ValueError("held-out public artifact commitments are stale")
    operator_guide_hash = _sha256_bytes(public_operator_guide_bytes)
    if public_hashes.get(OPERATOR_GUIDE_FILE) != operator_guide_hash:
        raise ValueError("held-out public operator guide commitment is stale")
    if public_manifest.get("policy_sha256") != _sha256_bytes(policy_bytes):
        raise ValueError("held-out public policy commitment is stale")
    if prepared_case_pack_bytes != public_cases_bytes:
        raise ValueError("prepared case pack differs from the public held-out cases")
    if prepared_operator_guide_bytes != public_operator_guide_bytes:
        raise ValueError(
            "prepared operator guide differs from the public held-out guide"
        )
    if prepared_policy_bytes != policy_bytes:
        raise ValueError("prepared policy differs from the held-out policy")

    cases = _parse_jsonl_bytes(public_cases_bytes, "held-out public cases")
    if len(cases) != HELDOUT_CASE_COUNT or any(
        not isinstance(case, dict) for case in cases
    ):
        raise ValueError("held-out public case count is stale")
    case_ids = [case.get("case_id") for case in cases]
    if (
        any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
        or len(set(case_ids)) != len(case_ids)
        or run_metadata.get("assigned_case_ids") != case_ids
    ):
        raise ValueError(
            "held-out assignment must match the complete public case order"
        )

    expected_pack = {
        "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
        "generator_name": HELDOUT_GENERATOR_NAME,
        "generator_version": HELDOUT_GENERATOR_VERSION,
        "generator_seed_commitment_sha256": public_manifest.get(
            "generator_seed_commitment_sha256"
        ),
    }
    if run_metadata.get("evaluation_pack") != expected_pack:
        raise ValueError("run manifest evaluation pack identity is stale")
    if run_metadata.get("policy") != {
        "policy_id": public_manifest.get("policy_id"),
        "version": public_manifest.get("policy_version"),
    }:
        raise ValueError("run manifest policy identity is stale")
    if run_metadata.get("oracle") != {
        "version": public_manifest.get("oracle_version"),
        "sha256_commitment": oracle_hash,
        "release_path": HELDOUT_ORACLE_RELEASE_PATH,
    }:
        raise ValueError("run manifest oracle commitment is stale")

    expected_artifacts = {
        "cases": {"path": f"{HELDOUT_PUBLIC_PATH}/cases.jsonl", "sha256": cases_hash},
        "oracle": {"path": HELDOUT_ORACLE_RELEASE_PATH, "sha256": oracle_hash},
        "policy": {
            "path": "data/stage1/policy.json",
            "sha256": _sha256_bytes(policy_bytes),
        },
        "artifact_manifest": {
            "path": f"{HELDOUT_PUBLIC_PATH}/manifest.json",
            "sha256": _sha256_bytes(public_manifest_bytes),
        },
        "operator_guide": {
            "path": f"{HELDOUT_PUBLIC_PATH}/{OPERATOR_GUIDE_FILE}",
            "sha256": operator_guide_hash,
        },
    }
    if run_metadata.get("artifacts") != expected_artifacts:
        raise ValueError("run manifest artifact bindings are stale")
    if run_metadata.get("instructions") != {
        "path": HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
        "sha256": _sha256_bytes(instructions_bytes),
    }:
        raise ValueError("run manifest protocol binding is stale")
    if run_metadata.get("tool_policy") != {
        "allowed": list(HELDOUT_ALLOWED_TOOLS),
        "prohibited": list(HELDOUT_PROHIBITED_TOOLS),
    }:
        raise ValueError("run manifest held-out tool policy is stale")
    if run_metadata.get("release_gate") != {
        "required_transition": "completed-records-commit-before-oracle-release",
        "release_manifest_path": HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
    }:
        raise ValueError("run manifest release gate is stale")
    run_files = run_metadata.get("run_files")
    if not isinstance(run_files, dict):
        raise ValueError("run manifest run files are incomplete")
    expected_run_files = {
        "case_pack": {
            "path": "case-pack.jsonl",
            "sha256": _sha256_bytes(prepared_case_pack_bytes),
        },
        "policy_copy": {
            "path": "policy.json",
            "sha256": _sha256_bytes(prepared_policy_bytes),
        },
        "operator_guide_copy": {
            "path": OPERATOR_GUIDE_FILE,
            "sha256": _sha256_bytes(prepared_operator_guide_bytes),
        },
    }
    for name, expected in expected_run_files.items():
        if run_files.get(name) != expected:
            raise ValueError(f"run manifest {name} binding is stale")


def prepare_heldout_run(
    project_root: Path,
    public_output: Path,
    output_dir: Path,
    *,
    run_id: str,
    reviewer_code: str,
    operator_role: str,
    prepared_at: datetime,
) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("invalid run_id")
    if not REVIEWER_CODE_PATTERN.fullmatch(reviewer_code):
        raise ValueError("invalid reviewer_code")
    run_type = MANUAL_RUN_TYPE_BY_OPERATOR_ROLE.get(operator_role)
    if run_type is None:
        raise ValueError("unsupported operator_role")
    prepared_at_utc = _utc_text(prepared_at)

    root = project_root.resolve()
    public = public_output.resolve()
    output = output_dir.resolve()
    if output.exists():
        raise ValueError("held-out run output directory must not already exist")
    if output.name != run_id:
        raise ValueError("held-out run output directory name must match run_id")
    (
        manifest,
        cases,
        manifest_bytes,
        cases_bytes,
        policy_bytes,
        operator_guide_bytes,
    ) = validate_heldout_public_pack(root, public)
    output.parent.mkdir(parents=True, exist_ok=True)
    instructions_path = root / HELDOUT_INSTRUCTIONS_PUBLIC_PATH
    if not instructions_path.is_file():
        raise ValueError("held-out evaluation protocol does not exist")
    instructions_bytes = instructions_path.read_bytes()
    public_manifest_path = public / "manifest.json"
    policy_path = root / "data" / "stage1" / "policy.json"
    oracle_release_path = root / manifest["oracle_release_path"]
    assigned_case_ids = [case["case_id"] for case in cases]

    staging = output.parent / (f".{output.name}-preparing-{secrets.token_hex(8)}")
    staging.mkdir()
    try:
        case_pack = staging / "case-pack.jsonl"
        policy_copy = staging / "policy.json"
        operator_guide_copy = staging / OPERATOR_GUIDE_FILE
        records = staging / "manual-records.csv"
        case_pack.write_bytes(cases_bytes)
        policy_copy.write_bytes(policy_bytes)
        operator_guide_copy.write_bytes(operator_guide_bytes)
        write_manual_template(
            records,
            cases,
            reviewer_code=reviewer_code,
            run_type=run_type,
        )
        run_manifest = {
            "schema_version": HELDOUT_RUN_MANIFEST_SCHEMA_VERSION,
            "dataset_role": HELDOUT_DATASET_ROLE,
            "run_type": run_type,
            "oracle_exposure_status": HELDOUT_ORACLE_EXPOSURE_PREPARED,
            "evaluation_pack": {
                "evaluation_pack_id": HELDOUT_EVALUATION_PACK_ID,
                "generator_name": HELDOUT_GENERATOR_NAME,
                "generator_version": HELDOUT_GENERATOR_VERSION,
                "generator_seed_commitment_sha256": manifest[
                    "generator_seed_commitment_sha256"
                ],
            },
            "assigned_case_ids": assigned_case_ids,
            "policy": {
                "policy_id": manifest["policy_id"],
                "version": manifest["policy_version"],
            },
            "oracle": {
                "version": manifest["oracle_version"],
                "sha256_commitment": manifest["artifacts_sha256"][
                    "oracle.released.jsonl"
                ],
                "release_path": manifest["oracle_release_path"],
            },
            "artifacts": {
                "cases": {
                    "path": _relative_public_path(
                        root, public / "cases.jsonl", "cases"
                    ),
                    "sha256": _sha256_bytes(cases_bytes),
                },
                "oracle": {
                    "path": _relative_public_path(root, oracle_release_path, "oracle"),
                    "sha256": manifest["artifacts_sha256"]["oracle.released.jsonl"],
                },
                "policy": {
                    "path": _relative_public_path(root, policy_path, "policy"),
                    "sha256": _sha256_bytes(policy_bytes),
                },
                "artifact_manifest": {
                    "path": _relative_public_path(
                        root, public_manifest_path, "artifact manifest"
                    ),
                    "sha256": _sha256_bytes(manifest_bytes),
                },
                "operator_guide": {
                    "path": _relative_public_path(
                        root,
                        public / OPERATOR_GUIDE_FILE,
                        "operator guide",
                    ),
                    "sha256": _sha256_bytes(operator_guide_bytes),
                },
            },
            "instructions": {
                "path": HELDOUT_INSTRUCTIONS_PUBLIC_PATH,
                "sha256": _sha256_bytes(instructions_bytes),
            },
            "tool_policy": {
                "allowed": list(HELDOUT_ALLOWED_TOOLS),
                "prohibited": list(HELDOUT_PROHIBITED_TOOLS),
            },
            "run_provenance": {
                "status": "prepared",
                "run_id": run_id,
                "reviewer_code": reviewer_code,
                "operator_role": operator_role,
                "prepared_at_utc": prepared_at_utc,
            },
            "run_files": {
                "case_pack": _file_pin(case_pack, "case-pack.jsonl"),
                "policy_copy": _file_pin(policy_copy, "policy.json"),
                "operator_guide_copy": _file_pin(
                    operator_guide_copy, OPERATOR_GUIDE_FILE
                ),
                "records_template": _file_pin(records, "manual-records.csv"),
            },
            "release_gate": {
                "required_transition": "completed-records-commit-before-oracle-release",
                "release_manifest_path": manifest["oracle_release_manifest_path"],
            },
        }
        write_utf8_lf(
            staging / "run-manifest.json",
            json.dumps(run_manifest, indent=2, sort_keys=True) + "\n",
        )
        staging.replace(output)
    finally:
        if staging.exists() and staging.parent.resolve() == output.parent.resolve():
            shutil.rmtree(staging)
    return run_manifest


def load_private_generation_material(private_output: Path) -> str:
    path = private_output.resolve() / GENERATION_MATERIAL_FILE
    if not path.is_file():
        raise ValueError("private generation material does not exist")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("private generation material is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != HELDOUT_PRIVATE_SCHEMA_VERSION
        or value.get("evaluation_pack_id") != HELDOUT_EVALUATION_PACK_ID
        or not isinstance(value.get("generation_material"), str)
    ):
        raise ValueError("private generation material is inconsistent")
    return value["generation_material"]


def load_private_oracle(private_output: Path) -> list[dict[str, Any]]:
    path = private_output.resolve() / PRIVATE_ORACLE_FILE
    if not path.is_file():
        raise ValueError("private held-out oracle does not exist")
    return read_jsonl(path)
