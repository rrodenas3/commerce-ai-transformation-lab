"""Public narrative, readiness, and dereferenceable evidence content for U7."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from scripts.stage2_contracts import load_canonical_json
from scripts.stage2_decision_contracts import DecisionPackError, HUMAN_MEASURES, NOT_OBSERVED
from scripts.stage2_decision_source import ASSUMPTIONS_PATH, SCORE_PATH, read_regular


def build_readiness_matrix() -> dict[str, Any]:
    drill = {
        "drill_id": "S2-DRILL-MATERIAL-FAILURE-01",
        "scenario": "a proposed recovery conflicts with authoritative OMS or payment state",
        "required_response": "stop mutation, preserve evidence, route to the named authority, and record appeal or incident disposition",
        "observation_status": "not_observed",
    }
    role_specs = (
        ("workflow_owner_activator", "workflow owner / AI Activator", "may accept, amend with rationale, pause, or reject the public memo; cannot authorise a company pilot", "walk through the frozen evidence index and rehearse the stop/pause precedence before any use", "workflow_owner_activator", "workflow_owner_activator", "risk_owner", "workflow_owner_activator", "review at decision expiry, evidence-class change, or exact-zero event", "no independent human acceptance, amendment, or operating judgment has been observed"),
        ("specialist", "commerce recovery specialist", "may inspect evidence, raise a help request, and appeal a recommendation; cannot approve restricted actions outside assigned authority", "practice one evidence inspection and one appeal using fictional cases only", "manager", "manager", "manager", "workflow_owner_activator", "review after first use, material failure, or changed workflow guidance", "no first-use comprehension, confidence, review time, friction, trust, or repeat-use evidence"),
        ("manager", "operations manager", "may own help triage, incident coordination, and bounded approval according to policy; cannot override exact-zero controls", "rehearse approval binding, incident escalation, and the shared material-failure drill", "manager", "manager", "risk_owner", "workflow_owner_activator", "review after an incident, policy change, recurring appeal, or expired authority", "no observed coaching burden, escalation quality, or sustained operating use"),
        ("technical_owner", "technical owner", "may change versioned implementation after tests and review; cannot rewrite frozen evidence or decision outputs", "verify source bindings, output seals, telemetry fields, rollback, and failure injection in a non-live environment", "technical_owner", "technical_owner", "risk_owner", "technical_owner", "review on dependency, provider, adapter, policy, schema, or security-boundary change", "no observed service reliability, support load, provider cost, provider latency, or live incident recovery"),
        ("risk_owner", "policy / risk owner", "may stop release, decide appeals, and require control redesign; cannot promote synthetic evidence to human, live, or realised-value evidence", "inspect exact-zero controls, limitations, evidence classes, and the shared failure/appeal path", "workflow_owner_activator", "risk_owner", "risk_owner", "risk_owner", "review at any exact-zero event, unresolved appeal, control change, or evidence-class promotion request", "no independent risk acceptance, policy-owner review, appeal outcome, or live-control evidence"),
    )
    roles: list[dict[str, Any]] = []
    for role_id, role_name, authority, first_use, help_owner, incident_owner, appeal_owner, change_owner, trigger, gap in role_specs:
        roles.append(
            {
                "appeal_owner": appeal_owner,
                "authority": authority,
                "change_owner": change_owner,
                "evidence_gap": gap,
                "first_use_guidance": first_use,
                "help_owner": help_owner,
                "human_measures": {measure: "not_observed" for measure in HUMAN_MEASURES},
                "human_observation_artifact": None,
                "incident_owner": incident_owner,
                "material_failure_drill": drill,
                "review_trigger": trigger,
                "role_id": role_id,
                "role_name": role_name,
            }
        )
    return {
        "claim_boundary": "designed readiness controls; no human participation or adoption observed",
        "human_evidence_status": "not_observed",
        "material_failure_drill_count": 1,
        "role_count": 5,
        "roles": roles,
        "schema_version": "stage2-enablement-readiness/v1",
        "status": "designed-not-human-validated",
    }


def build_evidence_index() -> dict[str, Any]:
    score_path = SCORE_PATH.as_posix()
    assumption_path = ASSUMPTIONS_PATH.as_posix()
    claims: list[dict[str, str]] = [
        {"claim_id": "S2-CLAIM-EXECUTION-COMMIT", "resolution_kind": "evidence", "source_path": score_path, "source_pointer": "/assisted/execution_commit_basis_points", "statement": "Eligible assisted execution committed at 8333 basis points."},
        {"claim_id": "S2-CLAIM-PENDING", "resolution_kind": "evidence", "source_path": score_path, "source_pointer": "/denominator_conservation/mutually_exclusive_outcomes/pending", "statement": "Three scheduled cases ended pending."},
        {"claim_id": "S2-CLAIM-EXACT-ZERO", "resolution_kind": "evidence", "source_path": score_path, "source_pointer": "/exact_zero/status", "statement": "The sealed V6 exact-zero status is passed."},
        {"claim_id": "S2-CLAIM-PROVIDER-COST", "resolution_kind": "evidence", "source_path": score_path, "source_pointer": "/assisted/provider_cost_cents/unknown_attempts", "statement": "Provider cost is unknown for 36 attempts."},
        {"claim_id": "S2-CLAIM-PROVIDER-LATENCY", "resolution_kind": "evidence", "source_path": score_path, "source_pointer": "/assisted/provider_latency_milliseconds/unknown_attempts", "statement": "Provider latency is unknown for 36 attempts."},
        {"claim_id": "S2-CLAIM-CAPACITY-REALISATION", "resolution_kind": "assumption", "source_path": assumption_path, "source_pointer": "/assumptions/capacity_realisation_basis_points", "statement": "Capacity realisation is an explicit hypothetical assumption."},
        {"claim_id": "S2-CLAIM-PROVIDER-UNIT-COST", "resolution_kind": "assumption", "source_path": assumption_path, "source_pointer": "/assumptions/provider_cost_cents_per_case", "statement": "Provider unit cost is an explicit hypothetical assumption."},
        {"claim_id": "S2-CLAIM-DECISION", "resolution_kind": "evidence", "source_path": "data/stage2/decision-pack/decision-input.json", "source_pointer": "/signals/incomplete_evidence", "statement": "Incomplete evidence is true and takes pause precedence."},
    ]
    for index, measure in enumerate(NOT_OBSERVED):
        claims.append(
            {
                "claim_id": f"S2-CLAIM-NOT-OBSERVED-{measure.upper().replace('_', '-')}",
                "resolution_kind": "not_observed",
                "source_path": "data/stage2/decision-pack/summary.json",
                "source_pointer": f"/not_observed/{index}",
                "statement": f"{measure.replace('_', ' ')} is not observed.",
            }
        )
    return {
        "claims": claims,
        "pointer_contract": "RFC6901 exact JSON pointer; wildcards forbidden",
        "resolution_kinds": ["assumption", "evidence", "not_observed"],
        "schema_version": "stage2-decision-evidence-index/v2",
    }


def _source_payload(
    source_path: str,
    project_root: Path,
    generated_files: Mapping[str, bytes],
) -> bytes:
    pure = PurePosixPath(source_path)
    if not source_path or pure.is_absolute() or ".." in pure.parts or "\\" in source_path or str(pure) != source_path:
        raise DecisionPackError(f"unsafe evidence source path: {source_path}")
    if source_path in generated_files:
        return generated_files[source_path]
    return read_regular(Path(project_root).resolve() / Path(*pure.parts))


def resolve_evidence_pointer(
    claim: Mapping[str, Any],
    project_root: Path,
    generated_files: Mapping[str, bytes],
) -> Any:
    """Resolve one exact RFC6901 pointer against its public JSON artifact."""

    if not isinstance(claim, Mapping):
        raise DecisionPackError("evidence claim must be an object")
    source_path = claim.get("source_path")
    pointer = claim.get("source_pointer")
    if not isinstance(source_path, str) or not isinstance(pointer, str):
        raise DecisionPackError("evidence claim source path and pointer must be strings")
    if not pointer.startswith("/") or "*" in pointer:
        raise DecisionPackError(f"evidence pointer must be exact RFC6901 without wildcard: {pointer}")
    try:
        current: Any = load_canonical_json(_source_payload(source_path, project_root, generated_files))
    except DecisionPackError:
        raise
    except Exception as error:
        raise DecisionPackError(f"evidence source is not canonical JSON: {source_path}") from error
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if "~" in raw_token.replace("~1", "").replace("~0", ""):
            raise DecisionPackError(f"evidence pointer has invalid escape: {pointer}")
        if isinstance(current, Mapping):
            if token not in current:
                raise DecisionPackError(f"evidence pointer does not resolve: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise DecisionPackError(f"evidence array pointer is invalid: {pointer}")
            index = int(token)
            if index >= len(current):
                raise DecisionPackError(f"evidence pointer does not resolve: {pointer}")
            current = current[index]
        else:
            raise DecisionPackError(f"evidence pointer traverses a scalar: {pointer}")
    return current


def validate_evidence_index(
    index: Mapping[str, Any],
    project_root: Path,
    generated_files: Mapping[str, bytes],
) -> None:
    if index.get("schema_version") != "stage2-decision-evidence-index/v2":
        raise DecisionPackError("evidence index schema is unsupported")
    claims = index.get("claims")
    if not isinstance(claims, list) or not claims:
        raise DecisionPackError("evidence index must contain claims")
    identifiers: set[str] = set()
    for claim in claims:
        if not isinstance(claim, Mapping) or set(claim) != {
            "claim_id", "resolution_kind", "source_path", "source_pointer", "statement"
        }:
            raise DecisionPackError("evidence claim fields mismatch")
        claim_id = claim["claim_id"]
        if not isinstance(claim_id, str) or not claim_id or claim_id in identifiers:
            raise DecisionPackError("evidence claim IDs must be unique nonempty strings")
        identifiers.add(claim_id)
        if claim["resolution_kind"] not in {"assumption", "evidence", "not_observed"}:
            raise DecisionPackError(f"evidence resolution kind is invalid: {claim_id}")
        resolved = resolve_evidence_pointer(claim, project_root, generated_files)
        if claim["resolution_kind"] == "not_observed" and resolved not in NOT_OBSERVED:
            raise DecisionPackError(f"not-observed claim does not resolve to a declared gap: {claim_id}")


def render_document(
    evaluation: Mapping[str, Any],
    economics: Mapping[str, Any],
    readiness: Mapping[str, Any],
    decision_output: Mapping[str, Any],
) -> bytes:
    scenario_lines = "\n".join(
        f"| {row['scenario']} | EUR {Decimal(row['assisted_total_operating_cost_cents']) / 100:.2f} | "
        f"EUR {Decimal(row['non_ai_total_operating_cost_cents']) / 100:.2f} | "
        f"EUR {Decimal(row['capacity_realised_value_cents']) / 100:.2f} | "
        f"EUR {Decimal(row['decision_net_benefit_cents']) / 100:.2f} | `{row['recommendation_class']}` |"
        for row in economics["scenarios"]
    )
    role_lines = "\n".join(
        f"| {role['role_name']} | {role['authority']} | {role['change_owner']} | {role['evidence_gap']} |"
        for role in readiness["roles"]
    )
    action = decision_output["next_action"]
    text = f"""---
title: Stage 2 benefits and next-gate decision
evidence_status: synthetic-observed
public_safe: true
maturity: foundation
limitations: creator-evaluated synthetic evidence only; human use, live operations, provider cost and latency, and realised value remain unobserved
---

# Stage 2 benefits and next-gate decision

## Decision: {str(decision_output['recommendation']).upper()}

The U1 precedence contract recommends **{str(decision_output['recommendation']).upper()}**. This is a creator-evaluated, synthetic local-MVP result. It does not authorise a company pilot, a live deployment, or any customer-facing action.

The sealed V6 evaluation committed **{evaluation['execution_commit_display']}** of eligible assisted executions (15 of 18), with **{evaluation['pending_cases']} pending**. Provider cost and latency remain unknown for **36 of 36 provider attempts**. Exact-zero controls passed, but incomplete execution and telemetry evidence take precedence and require a pause.

## What the evidence does and does not show

- Observed in sealed synthetic evidence: deterministic threshold results, 83.33% execution commit, 3 pending cases, and no recorded exact-zero failure.
- Not observed: human first use, comprehension, help need, confidence, review time, friction, trust, repeated use, adoption, or outcome contribution.
- Not observed: live operational reliability, live customer outcomes, customer satisfaction, retained revenue, or realised savings.
- Provider cost and latency are unknown for all attempts; the scenario values below are assumptions, never substitutions for those missing observations.

## Transparent hypothetical economics

All amounts use exact integer euro cents. Total operating cost includes labour, provider, support, infrastructure, and expected failure cost. The non-AI process alternative is calculated in parallel. Nominal capacity is not converted to money until the separate capacity-realisation assumption is applied.

| Scenario | Assisted total cost | Non-AI total cost | Capacity-realised value | Decision net benefit | Scenario class |
|---|---:|---:|---:|---:|---|
{scenario_lines}

The recommendation class changes inside the preregistered envelope, so economics is **{economics['status']}** and cannot support scaling. These are hypothetical impacts, not realised ROI or value.

## Five-role enablement readiness

Every role uses the same `S2-DRILL-MATERIAL-FAILURE-01` drill: stop mutation, preserve evidence, route to named authority, and record the incident or appeal. All human measures remain not observed.

| Role | Authority | Change owner | Evidence gap |
|---|---|---|---|
{role_lines}

The machine-readable matrix also records first-use guidance, help ownership, incident ownership, appeal ownership, review triggers, and every unobserved human measure for each role.

## One bounded next action

Owner: **{action['owner']}**. Evidence question: {action['evidence_question']}

Cap: {action['cap']['maximum_synthetic_cases']} synthetic cases, {action['cap']['maximum_provider_attempts']} provider attempts, {action['cap']['maximum_calendar_days']} calendar days, and EUR {Decimal(action['cap']['maximum_spend_cents']) / 100:.2f}. The action expires or is reviewed when: {action['expiry_or_review_trigger']}.

This action remains synthetic and local-MVP only. It **does not authorise a company pilot**.
"""
    return text.encode("utf-8")
