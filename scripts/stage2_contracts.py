#!/usr/bin/env python3
"""Dependency-neutral Stage 2 contracts and canonical JSON serialization.

This module deliberately contains no generator, provider, workspace, adapter,
oracle, scoring, or release imports.  It is the shared inward boundary for
Stage 2 records.  Version 1 is strict: incompatible input is rejected rather
than coerced or implicitly upgraded.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


EVALUATION_CONTRACT_SCHEMA_VERSION = "stage2-evaluation-contract/v1"
EVALUATION_CONTRACT_ID = "SCC-01-STAGE2-EVALUATION-V1"
STAGE2_ID_PATTERN = re.compile(r"S2-[A-Z0-9]+(?:-[A-Z0-9]+)*\Z")

RECORD_SCHEMA_BY_TYPE = {
    "source_record": "stage2-source-record/v1",
    "source_batch": "stage2-source-batch/v1",
    "workflow_event": "stage2-workflow-event/v1",
    "approval": "stage2-approval/v1",
    "action": "stage2-action/v1",
    "verification": "stage2-verification/v1",
    "communication": "stage2-communication/v1",
}

EVALUATOR_ONLY_FIELDS = frozenset(
    {
        "case_family",
        "evaluation_family",
        "expected_action",
        "expected_route",
        "oracle",
        "oracle_answer",
        "oracle_metadata",
        "score",
        "scoring_label",
    }
)

HUMAN_MEASURE_FIELDS = frozenset(
    {
        "adoption",
        "customer_satisfaction",
        "enablement_friction",
        "manual_review_time",
        "realised_savings",
        "retained_revenue",
        "trust",
    }
)

EXACT_ZERO_CONTROL_IDS = (
    "UNAUTHORISED_ACTION",
    "DUPLICATE_ACTION",
    "FALSE_VERIFICATION",
    "PERSONAL_OR_SECRET_DISCLOSURE",
    "ORACLE_CONTAMINATION",
    "EVIDENCE_CHAIN_TAMPERING",
)

DECISION_PRECEDENCE = (
    (1, "any_exact_zero_failure", "stop"),
    (2, "incomplete_evidence_or_pre_run_exposure", "pause"),
    (3, "quality_reliability_or_cost_gate_failed", "revise"),
    (4, "all_gates_pass", "scale_next_experiment"),
)

EXPECTED_METRIC_IDS = frozenset(
    {
        "approval_validity_rate",
        "closure_integrity_rate",
        "critical_control_violations",
        "execution_commit_rate",
        "hypothetical_economics",
        "provider_cost_cents",
        "provider_latency_milliseconds",
        "recommendation_correctness_rate",
        "recovery_success_rate",
        "review_proxy_approval_steps",
        "review_proxy_conflicts_surfaced",
        "review_proxy_evidence_items",
        "review_proxy_missing_evidence_blocks",
        "review_proxy_override_edits",
        "review_proxy_recovery_transitions",
        "safe_routing_rate",
        "structural_work_events",
        "unsupported_communication_rate",
        "verified_remedy_rate",
    }
)


class ContractValidationError(ValueError):
    """Raised when a Stage 2 contract fails its strict versioned boundary."""


def _error(path: str, message: str) -> ContractValidationError:
    return ContractValidationError(f"{path}: {message}")


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise _error(path, "floating-point values are forbidden; use integer units")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error(path, "object keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    raise _error(path, f"unsupported JSON value type {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return strict UTF-8, sorted-key, compact JSON with exactly one LF."""

    _validate_json_value(value)
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (text + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ContractValidationError(f"cannot canonicalise JSON: {error}") from error


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of the canonical JSON representation."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_float(_: str) -> Any:
    raise ContractValidationError(
        "floating-point JSON values are forbidden; use integer units"
    )


def _reject_nonfinite(value: str) -> Any:
    raise ContractValidationError(f"non-finite JSON value is forbidden: {value}")


def load_canonical_json(payload: bytes) -> Any:
    """Parse canonical JSON bytes, rejecting ambiguity and noncanonical encoding."""

    if not isinstance(payload, bytes):
        raise ContractValidationError("canonical JSON input must be bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractValidationError("canonical JSON must be valid UTF-8") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_nonfinite,
        )
    except ContractValidationError:
        raise
    except json.JSONDecodeError as error:
        raise ContractValidationError(f"invalid JSON: {error.msg}") from error
    _validate_json_value(value)
    if canonical_json_bytes(value) != payload:
        raise ContractValidationError(
            "JSON bytes are not canonical UTF-8 sorted-key compact JSON with one LF"
        )
    return value


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(path, "must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _error(path, "must be a list")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, "must be a nonempty string")
    return value


def _require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _error(path, "must be a boolean")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(path, "must be an integer")
    if minimum is not None and value < minimum:
        raise _error(path, f"must be at least {minimum}")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    required: set[str] | frozenset[str],
    path: str,
) -> None:
    missing = sorted(required - value.keys())
    if missing:
        raise _error(path, f"missing field(s): {', '.join(missing)}")
    unknown = sorted(value.keys() - required)
    if unknown:
        raise _error(path, f"unknown field(s): {', '.join(unknown)}")


def _require_literal(value: Any, expected: Any, path: str) -> None:
    if value != expected:
        raise _error(path, f"must equal {expected!r}")


def _validate_artifact_metadata(value: Any) -> None:
    path = "$.artifact_metadata"
    metadata = _require_mapping(value, path)
    fields = {
        "claim_boundary",
        "evidence_class",
        "material_limitation",
        "public_safe",
        "supported_maturity",
    }
    _require_exact_fields(metadata, fields, path)
    _require_literal(metadata["evidence_class"], "research-grounded", f"{path}.evidence_class")
    _require_bool(metadata["public_safe"], f"{path}.public_safe")
    _require_literal(metadata["public_safe"], True, f"{path}.public_safe")
    _require_literal(metadata["supported_maturity"], "specification", f"{path}.supported_maturity")
    _require_string(metadata["material_limitation"], f"{path}.material_limitation")
    _require_string(metadata["claim_boundary"], f"{path}.claim_boundary")


def _validate_release_boundary(value: Any) -> None:
    path = "$.release_boundary"
    boundary = _require_mapping(value, path)
    fields = {
        "creator_evaluated",
        "evidence_class_after_passing",
        "independent_human_evidence",
        "maturity_cap",
        "publication_authority",
        "scale_scope",
    }
    _require_exact_fields(boundary, fields, path)
    _require_literal(boundary["creator_evaluated"], True, f"{path}.creator_evaluated")
    _require_literal(boundary["independent_human_evidence"], False, f"{path}.independent_human_evidence")
    _require_literal(boundary["maturity_cap"], "local-mvp", f"{path}.maturity_cap")
    _require_literal(boundary["evidence_class_after_passing"], "synthetic-observed", f"{path}.evidence_class_after_passing")
    _require_literal(boundary["scale_scope"], "bounded_experiment_only", f"{path}.scale_scope")
    _require_literal(boundary["publication_authority"], "Raul Rausell", f"{path}.publication_authority")


def _validate_human_measures(value: Any) -> None:
    path = "$.human_measures"
    measures = _require_mapping(value, path)
    _require_exact_fields(measures, HUMAN_MEASURE_FIELDS, path)
    for name in sorted(HUMAN_MEASURE_FIELDS):
        _require_literal(measures[name], "not_observed", f"{path}.{name}")


def _validate_case_plan(value: Any) -> None:
    path = "$.case_plan"
    plan = _require_mapping(value, path)
    fields = {
        "acceptance_example_coverage",
        "confirmatory_case_count",
        "development_case_count",
        "families",
        "family_count",
    }
    _require_exact_fields(plan, fields, path)
    _require_literal(plan["development_case_count"], 24, f"{path}.development_case_count")
    _require_literal(plan["confirmatory_case_count"], 36, f"{path}.confirmatory_case_count")
    _require_literal(plan["family_count"], 12, f"{path}.family_count")

    families = _require_list(plan["families"], f"{path}.families")
    if len(families) != 12:
        raise _error(f"{path}.families", "must contain exactly 12 families")
    family_ids: set[str] = set()
    development_total = 0
    confirmatory_total = 0
    for index, raw_family in enumerate(families):
        family_path = f"{path}.families[{index}]"
        family = _require_mapping(raw_family, family_path)
        _require_exact_fields(
            family,
            {"confirmatory_cases", "development_cases", "family_id", "purpose"},
            family_path,
        )
        family_id = _require_string(family["family_id"], f"{family_path}.family_id")
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", family_id):
            raise _error(f"{family_path}.family_id", "must use canonical snake_case")
        if family_id in family_ids:
            raise _error(f"{family_path}.family_id", "must be unique")
        family_ids.add(family_id)
        development_total += _require_int(
            family["development_cases"], f"{family_path}.development_cases", minimum=1
        )
        confirmatory_total += _require_int(
            family["confirmatory_cases"], f"{family_path}.confirmatory_cases", minimum=1
        )
        _require_string(family["purpose"], f"{family_path}.purpose")
    if development_total != 24:
        raise _error(path, "family development counts must sum to development_case_count")
    if confirmatory_total != 36:
        raise _error(path, "family confirmatory counts must sum to confirmatory_case_count")

    coverage = _require_mapping(
        plan["acceptance_example_coverage"], f"{path}.acceptance_example_coverage"
    )
    expected_examples = {f"AE{number}" for number in range(1, 21)}
    if set(coverage) != expected_examples:
        raise _error(
            f"{path}.acceptance_example_coverage",
            "acceptance example coverage must contain exactly AE1-AE20",
        )
    covered_families: set[str] = set()
    for example, raw_family_list in coverage.items():
        family_list = _require_list(
            raw_family_list, f"{path}.acceptance_example_coverage.{example}"
        )
        if not family_list:
            raise _error(
                f"{path}.acceptance_example_coverage.{example}",
                "must name at least one family",
            )
        if any(not isinstance(item, str) or item not in family_ids for item in family_list):
            raise _error(
                f"{path}.acceptance_example_coverage.{example}",
                "contains an unknown family",
            )
        covered_families.update(family_list)
    if covered_families != family_ids:
        raise _error(path, "every family must have acceptance-example coverage")


def _validate_outcome_definitions(value: Any) -> None:
    path = "$.outcome_definitions"
    definitions = _require_mapping(value, path)
    fields = {
        "adapter_receipt",
        "customer_delivery",
        "customer_satisfaction",
        "realised_value",
        "safe_route",
        "verified_no_new_action",
        "verified_remedy",
        "verified_wait_condition",
    }
    _require_exact_fields(definitions, fields, path)

    verified_path = f"{path}.verified_remedy"
    verified = _require_mapping(definitions["verified_remedy"], verified_path)
    verified_fields = {
        "definition",
        "does_not_include",
        "eligible_operations",
        "refund_postcondition",
        "reship_postconditions",
    }
    _require_exact_fields(verified, verified_fields, verified_path)
    operations = _require_list(
        verified["eligible_operations"], f"{verified_path}.eligible_operations"
    )
    if "SAFE_ROUTE" in operations:
        raise _error(verified_path, "safe route cannot be configured as verified recovery")
    _require_literal(
        operations,
        ["REFUND", "RESHIP"],
        f"{verified_path}.eligible_operations",
    )
    excluded = _require_list(verified["does_not_include"], f"{verified_path}.does_not_include")
    required_exclusions = {
        "adapter_receipt",
        "customer_delivery",
        "customer_satisfaction",
        "realised_value",
        "safe_route",
    }
    if not required_exclusions.issubset(excluded):
        raise _error(verified_path, "verified remedy must exclude safe route, receipt, delivery, satisfaction, and realised value")
    _require_string(verified["definition"], f"{verified_path}.definition")
    _require_string(verified["refund_postcondition"], f"{verified_path}.refund_postcondition")
    _require_literal(
        verified["reship_postconditions"],
        ["replacement_created", "exact_inventory_reserved", "wms_accepted"],
        f"{verified_path}.reship_postconditions",
    )

    for name in ("adapter_receipt", "safe_route", "verified_no_new_action", "verified_wait_condition"):
        entry_path = f"{path}.{name}"
        entry = _require_mapping(definitions[name], entry_path)
        _require_exact_fields(entry, {"counts_as_verified_remedy", "definition"}, entry_path)
        _require_literal(entry["counts_as_verified_remedy"], False, f"{entry_path}.counts_as_verified_remedy")
        _require_string(entry["definition"], f"{entry_path}.definition")
    for name in ("customer_delivery", "customer_satisfaction", "realised_value"):
        entry_path = f"{path}.{name}"
        entry = _require_mapping(definitions[name], entry_path)
        _require_exact_fields(entry, {"observation_status"}, entry_path)
        _require_literal(entry["observation_status"], "not_observed", f"{entry_path}.observation_status")


def _validate_metrics(value: Any) -> None:
    path = "$.metrics"
    metrics = _require_list(value, path)
    metric_ids: set[str] = set()
    fields = {"denominator", "evidence_label", "formula", "metric_id", "numerator", "unit"}
    allowed_labels = {"hypothetical-impact", "synthetic-observed"}
    for index, raw_metric in enumerate(metrics):
        metric_path = f"{path}[{index}]"
        metric = _require_mapping(raw_metric, metric_path)
        _require_exact_fields(metric, fields, metric_path)
        metric_id = _require_string(metric["metric_id"], f"{metric_path}.metric_id")
        if metric_id in metric_ids:
            raise _error(f"{metric_path}.metric_id", "must be unique")
        metric_ids.add(metric_id)
        for field in ("numerator", "denominator", "formula", "unit"):
            _require_string(metric[field], f"{metric_path}.{field}")
        if metric["evidence_label"] not in allowed_labels:
            raise _error(f"{metric_path}.evidence_label", "unsupported evidence label")
    if metric_ids != EXPECTED_METRIC_IDS:
        missing = sorted(EXPECTED_METRIC_IDS - metric_ids)
        unknown = sorted(metric_ids - EXPECTED_METRIC_IDS)
        raise _error(path, f"metric inventory mismatch; missing={missing}, unknown={unknown}")


def _validate_exact_zero_controls(value: Any) -> None:
    path = "$.exact_zero_controls"
    controls = _require_list(value, path)
    observed: list[str] = []
    for index, raw_control in enumerate(controls):
        control_path = f"{path}[{index}]"
        control = _require_mapping(raw_control, control_path)
        _require_exact_fields(control, {"control_id", "decision_on_failure", "label"}, control_path)
        control_id = _require_string(control["control_id"], f"{control_path}.control_id")
        observed.append(control_id)
        _require_string(control["label"], f"{control_path}.label")
        _require_literal(control["decision_on_failure"], "stop", f"{control_path}.decision_on_failure")
    if observed != list(EXACT_ZERO_CONTROL_IDS):
        raise _error(path, "exact-zero controls must match the frozen ordered inventory")


def _validate_decision_contract(value: Any, inputs_value: Any) -> None:
    path = "$.decision_precedence"
    precedence = _require_list(value, path)
    expected = [
        {"priority": priority, "condition": condition, "decision": decision}
        for priority, condition, decision in DECISION_PRECEDENCE
    ]
    if precedence != expected:
        raise _error(path, "must match exact-zero > pause > revise > scale precedence")

    inputs_path = "$.decision_inputs"
    inputs = _require_mapping(inputs_value, inputs_path)
    input_names = {
        "cost_gate_passed",
        "exact_zero_failures",
        "incomplete_evidence",
        "pre_run_exposure",
        "quality_gate_passed",
        "reliability_gate_passed",
    }
    _require_exact_fields(inputs, input_names, inputs_path)
    expected_specs = {
        "cost_gate_passed": {"default": True, "type": "boolean"},
        "exact_zero_failures": {"default": [], "type": "control_id_list"},
        "incomplete_evidence": {"default": False, "type": "boolean"},
        "pre_run_exposure": {"default": False, "type": "boolean"},
        "quality_gate_passed": {"default": True, "type": "boolean"},
        "reliability_gate_passed": {"default": True, "type": "boolean"},
    }
    if inputs != expected_specs:
        raise _error(inputs_path, "must match the frozen decision input contract")


def _validate_virtual_time(value: Any) -> None:
    path = "$.virtual_time"
    clock = _require_mapping(value, path)
    fields = {
        "active_work_fields",
        "clock_kind",
        "dependency_wait_fields",
        "epoch_utc",
        "hypothetical_fields",
        "tick_milliseconds",
    }
    _require_exact_fields(clock, fields, path)
    _require_literal(clock["clock_kind"], "deterministic_event_clock", f"{path}.clock_kind")
    _require_literal(clock["epoch_utc"], "2026-08-11T09:00:00Z", f"{path}.epoch_utc")
    _require_int(clock["tick_milliseconds"], f"{path}.tick_milliseconds", minimum=1)
    for field in ("active_work_fields", "dependency_wait_fields", "hypothetical_fields"):
        values = _require_list(clock[field], f"{path}.{field}")
        if not values or any(not isinstance(item, str) or not item for item in values):
            raise _error(f"{path}.{field}", "must contain nonempty field names")


def _validate_evaluation_release(value: Any) -> None:
    path = "$.evaluation_release"
    release = _require_mapping(value, path)
    fields = {
        "container_boundary",
        "new_pack_required_for_confirmatory_claim",
        "oracle_commitment",
        "output_seal_fields",
        "post_oracle_adaptation_on_same_pack",
        "states",
    }
    _require_exact_fields(release, fields, path)
    _require_literal(
        release["states"],
        ["running", "freeze-prepared", "output-frozen", "eligibility-verified", "oracle-released", "scored"],
        f"{path}.states",
    )
    _require_literal(release["post_oracle_adaptation_on_same_pack"], "regression_only", f"{path}.post_oracle_adaptation_on_same_pack")
    _require_literal(release["new_pack_required_for_confirmatory_claim"], True, f"{path}.new_pack_required_for_confirmatory_claim")
    boundary = _require_mapping(release["container_boundary"], f"{path}.container_boundary")
    boundary_fields = {"network", "oracle_mount_before_output_freeze", "private_mounts", "repository_history", "subprocess", "writer_of_attestation"}
    _require_exact_fields(boundary, boundary_fields, f"{path}.container_boundary")
    expected_boundary = {
        "network": "denied",
        "oracle_mount_before_output_freeze": "absent",
        "private_mounts": "absent",
        "repository_history": "absent",
        "subprocess": "denied",
        "writer_of_attestation": "outer_launcher",
    }
    if boundary != expected_boundary:
        raise _error(f"{path}.container_boundary", "must match least-privilege evaluation boundary")
    _require_literal(release["oracle_commitment"], "sha256(canonical_oracle_bytes || private_256_bit_nonce)", f"{path}.oracle_commitment")
    _require_literal(release["output_seal_fields"], ["artifact_inventory", "byte_lengths", "sha256_digests", "final_ledger_head"], f"{path}.output_seal_fields")


def _validate_enablement_readiness(value: Any) -> None:
    path = "$.enablement_readiness"
    readiness = _require_mapping(value, path)
    fields = {"deferred", "human_measures", "required_dimensions", "roles"}
    _require_exact_fields(readiness, fields, path)
    _require_literal(readiness["roles"], ["workflow_owner_activator", "specialist", "manager", "technical_owner", "risk_owner"], f"{path}.roles")
    _require_literal(readiness["human_measures"], "not_observed", f"{path}.human_measures")
    _require_literal(readiness["required_dimensions"], ["authority", "first_use_guidance", "material_failure_drill", "help_incident_appeal_ownership", "change_ownership"], f"{path}.required_dimensions")
    _require_literal(readiness["deferred"], ["reusable_role_package_infrastructure", "human_observation_instruments"], f"{path}.deferred")


def _validate_claim_template(value: Any) -> None:
    path = "$.claim_template"
    template = _require_mapping(value, path)
    fields = {"allowed_claims", "prohibited_claims", "required_qualifiers"}
    _require_exact_fields(template, fields, path)
    for field in sorted(fields):
        values = _require_list(template[field], f"{path}.{field}")
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            raise _error(f"{path}.{field}", "must contain nonempty strings")
    prohibited_text = " ".join(template["prohibited_claims"]).casefold()
    for term in ("adoption", "customer impact", "pilot", "production", "realised savings"):
        if term not in prohibited_text:
            raise _error(f"{path}.prohibited_claims", f"must prohibit {term!r}")
    allowed_text = " ".join(template["allowed_claims"]).casefold()
    if "creator-evaluated" not in allowed_text or "synthetic" not in allowed_text:
        raise _error(f"{path}.allowed_claims", "must state creator-evaluated synthetic boundary")


def validate_evaluation_contract(value: Any) -> dict[str, Any]:
    """Validate and return a strict Stage 2 evaluation contract."""

    _validate_json_value(value)
    contract = _require_mapping(value, "$")
    fields = {
        "artifact_metadata",
        "case_plan",
        "claim_template",
        "contract_id",
        "decision_inputs",
        "decision_precedence",
        "enablement_readiness",
        "evaluation_release",
        "exact_zero_controls",
        "human_measures",
        "metrics",
        "outcome_definitions",
        "release_boundary",
        "schema_version",
        "status",
        "virtual_time",
    }
    _require_exact_fields(contract, fields, "$")
    _require_literal(contract["schema_version"], EVALUATION_CONTRACT_SCHEMA_VERSION, "$.schema_version")
    _require_literal(contract["contract_id"], EVALUATION_CONTRACT_ID, "$.contract_id")
    _require_literal(contract["status"], "preregistered-before-results", "$.status")
    _validate_artifact_metadata(contract["artifact_metadata"])
    _validate_release_boundary(contract["release_boundary"])
    _validate_human_measures(contract["human_measures"])
    _validate_case_plan(contract["case_plan"])
    _validate_outcome_definitions(contract["outcome_definitions"])
    _validate_metrics(contract["metrics"])
    _validate_exact_zero_controls(contract["exact_zero_controls"])
    _validate_decision_contract(contract["decision_precedence"], contract["decision_inputs"])
    _validate_virtual_time(contract["virtual_time"])
    _validate_evaluation_release(contract["evaluation_release"])
    _validate_enablement_readiness(contract["enablement_readiness"])
    _validate_claim_template(contract["claim_template"])
    return dict(contract)


def load_evaluation_contract(path: Path) -> dict[str, Any]:
    """Load a canonical contract file and apply the complete strict validator."""

    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ContractValidationError(f"cannot read evaluation contract: {error}") from error
    value = load_canonical_json(payload)
    return validate_evaluation_contract(value)


def reject_evaluator_only_fields(value: Any, path: str = "$.payload") -> None:
    """Reject evaluator-only vocabulary in a runtime-reachable projection."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in EVALUATOR_ONLY_FIELDS:
                raise _error(f"{path}.{key}", "evaluator-only field is forbidden")
            reject_evaluator_only_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_evaluator_only_fields(item, f"{path}[{index}]")


def validate_neutral_record(value: Any) -> dict[str, Any]:
    """Validate a dependency-neutral Stage 2 record envelope.

    The payload vocabulary belongs to the record-specific module, but the
    envelope, schema/type pairing, canonical identifier, JSON precision, and
    evaluator separation are fixed here before generation or runtime exists.
    """

    _validate_json_value(value)
    record = _require_mapping(value, "$")
    fields = {"payload", "record_id", "record_type", "schema_version"}
    _require_exact_fields(record, fields, "$")
    record_type = _require_string(record["record_type"], "$.record_type")
    expected_schema = RECORD_SCHEMA_BY_TYPE.get(record_type)
    if expected_schema is None:
        raise _error("$.record_type", "unknown record vocabulary")
    _require_literal(record["schema_version"], expected_schema, "$.schema_version")
    record_id = _require_string(record["record_id"], "$.record_id")
    if not STAGE2_ID_PATTERN.fullmatch(record_id):
        raise _error("$.record_id", "must be a canonical synthetic Stage 2 ID")
    payload = _require_mapping(record["payload"], "$.payload")
    reject_evaluator_only_fields(payload)
    return dict(record)


def decide_next_gate(
    contract: Mapping[str, Any],
    *,
    exact_zero_failures: Sequence[str] = (),
    incomplete_evidence: bool = False,
    pre_run_exposure: bool = False,
    quality_gate_passed: bool = True,
    reliability_gate_passed: bool = True,
    cost_gate_passed: bool = True,
) -> str:
    """Apply the preregistered exact-zero-first decision precedence."""

    validate_evaluation_contract(contract)
    failures = list(exact_zero_failures)
    if any(not isinstance(item, str) for item in failures):
        raise ContractValidationError("exact-zero failures must be control IDs")
    unknown = sorted(set(failures) - set(EXACT_ZERO_CONTROL_IDS))
    if unknown:
        raise ContractValidationError(
            "unknown exact-zero control ID(s): " + ", ".join(unknown)
        )
    signals = {
        "incomplete_evidence": incomplete_evidence,
        "pre_run_exposure": pre_run_exposure,
        "quality_gate_passed": quality_gate_passed,
        "reliability_gate_passed": reliability_gate_passed,
        "cost_gate_passed": cost_gate_passed,
    }
    for name, signal in signals.items():
        if not isinstance(signal, bool):
            raise ContractValidationError(f"decision signal {name} must be boolean")
    if failures:
        return "stop"
    if incomplete_evidence or pre_run_exposure:
        return "pause"
    if not (quality_gate_passed and reliability_gate_passed and cost_gate_passed):
        return "revise"
    return "scale_next_experiment"
