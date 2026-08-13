"""Contracts, exact economics, and bounded decisions for the Stage 2 pack."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.stage2_contracts import decide_next_gate, load_canonical_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSUMPTION_VERSION = "S2-ECON-20260812-V1"
SCENARIOS = ("conservative", "base", "upside")
CONTRACT_PATH = Path("data/stage2/evaluation-contract.json")

HUMAN_MEASURES = (
    "first_use",
    "comprehension",
    "help",
    "confidence",
    "review_time",
    "friction",
    "trust",
    "repeated_use",
    "adoption",
    "outcome_contribution",
)

NOT_OBSERVED = (
    "adoption",
    "customer_satisfaction",
    "human_comprehension",
    "human_first_use",
    "human_friction",
    "human_help_need",
    "human_review_time",
    "human_trust",
    "live_customer_outcome",
    "live_operational_reliability",
    "realised_savings",
    "retained_revenue",
)

REQUIRED_ASSUMPTIONS = (
    "monthly_case_volume",
    "labour_cost_cents_per_hour",
    "assisted_review_minutes_per_case",
    "non_ai_review_minutes_per_case",
    "provider_cost_cents_per_case",
    "support_cost_cents_per_month",
    "infrastructure_cost_cents_per_month",
    "non_ai_support_cost_cents_per_month",
    "non_ai_infrastructure_cost_cents_per_month",
    "assisted_failure_rate_basis_points",
    "non_ai_failure_rate_basis_points",
    "failure_impact_cents_per_case",
    "capacity_realisation_basis_points",
)

EXPECTED_UNITS = {
    "monthly_case_volume": "cases_per_month",
    "labour_cost_cents_per_hour": "integer_cents_per_hour",
    "assisted_review_minutes_per_case": "minutes_per_case",
    "non_ai_review_minutes_per_case": "minutes_per_case",
    "provider_cost_cents_per_case": "integer_cents_per_case",
    "support_cost_cents_per_month": "integer_cents_per_month",
    "infrastructure_cost_cents_per_month": "integer_cents_per_month",
    "non_ai_support_cost_cents_per_month": "integer_cents_per_month",
    "non_ai_infrastructure_cost_cents_per_month": "integer_cents_per_month",
    "assisted_failure_rate_basis_points": "basis_points",
    "non_ai_failure_rate_basis_points": "basis_points",
    "failure_impact_cents_per_case": "integer_cents_per_case",
    "capacity_realisation_basis_points": "basis_points",
}


class DecisionPackError(ValueError):
    """Raised when a source, assumption, reference, or decision is invalid."""


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise DecisionPackError(f"{path} fields mismatch; missing={missing}, extra={extra}")


def validate_assumptions(material: Mapping[str, Any]) -> None:
    """Validate the complete, versioned, exact-unit sensitivity envelope."""

    if not isinstance(material, Mapping):
        raise DecisionPackError("assumptions must be an object")
    _require_exact_keys(
        material,
        {
            "assumptions",
            "currency",
            "evidence_class",
            "owner",
            "scenario_order",
            "schema_version",
            "source",
            "status",
            "version",
        },
        "assumptions",
    )
    for field in ("currency", "evidence_class", "owner", "source", "status", "version"):
        if not isinstance(material.get(field), str) or not material[field]:
            raise DecisionPackError(f"assumptions {field} must be a nonempty string")
    if material["schema_version"] != "stage2-economics-assumptions/v1":
        raise DecisionPackError("assumptions schema_version is not supported")
    if material["version"] != ASSUMPTION_VERSION:
        raise DecisionPackError(f"assumptions version must be {ASSUMPTION_VERSION}")
    if material["currency"] != "EUR":
        raise DecisionPackError("assumptions currency must be EUR")
    if material["evidence_class"] != "hypothetical-impact-not-realised-value":
        raise DecisionPackError("assumptions evidence_class must remain hypothetical")
    if list(material["scenario_order"]) != list(SCENARIOS):
        raise DecisionPackError("assumptions scenario_order must be conservative/base/upside")
    assumptions = material.get("assumptions")
    if not isinstance(assumptions, Mapping):
        raise DecisionPackError("assumptions.assumptions must be an object")
    if set(assumptions) != set(REQUIRED_ASSUMPTIONS):
        missing = sorted(set(REQUIRED_ASSUMPTIONS) - set(assumptions))
        extra = sorted(set(assumptions) - set(REQUIRED_ASSUMPTIONS))
        detail = missing[0] if missing else (extra[0] if extra else "unknown")
        raise DecisionPackError(f"assumption set mismatch at {detail}; missing={missing}, extra={extra}")

    fields = {
        "currency",
        "evidence_class",
        "monotonic_direction",
        "owner",
        "scenarios",
        "source",
        "unit",
        "version",
    }
    for assumption_id in REQUIRED_ASSUMPTIONS:
        record = assumptions[assumption_id]
        if not isinstance(record, Mapping):
            raise DecisionPackError(f"{assumption_id} must be an object")
        _require_exact_keys(record, fields, assumption_id)
        if record["unit"] != EXPECTED_UNITS[assumption_id]:
            raise DecisionPackError(f"{assumption_id} unit must be {EXPECTED_UNITS[assumption_id]}")
        if record["version"] != material["version"]:
            raise DecisionPackError(f"{assumption_id} version must match the envelope")
        if record["owner"] != material["owner"]:
            raise DecisionPackError(f"{assumption_id} owner must match the envelope")
        expected_currency = "EUR" if "cents" in record["unit"] else "not_applicable"
        if record["currency"] != expected_currency:
            raise DecisionPackError(f"{assumption_id} currency is inconsistent with its unit")
        if record["evidence_class"] != "hypothetical-assumption":
            raise DecisionPackError(f"{assumption_id} evidence_class must be hypothetical-assumption")
        if not isinstance(record["source"], str) or not record["source"]:
            raise DecisionPackError(f"{assumption_id} source must be nonempty")
        scenarios = record["scenarios"]
        if not isinstance(scenarios, Mapping) or set(scenarios) != set(SCENARIOS):
            raise DecisionPackError(f"{assumption_id} scenarios must contain the full envelope")
        values: list[int] = []
        for scenario in SCENARIOS:
            value = scenarios[scenario]
            if type(value) is not int or value < 0:
                raise DecisionPackError(f"{assumption_id}.{scenario} must be a nonnegative integer")
            if record["unit"] == "basis_points" and value > 10_000:
                raise DecisionPackError(f"{assumption_id}.{scenario} basis points exceed 10000")
            values.append(value)
        direction = record["monotonic_direction"]
        valid = (
            direction == "nondecreasing" and values[0] <= values[1] <= values[2]
        ) or (
            direction == "nonincreasing" and values[0] >= values[1] >= values[2]
        ) or (direction == "constant" and values[0] == values[1] == values[2])
        if direction not in {"nondecreasing", "nonincreasing", "constant"}:
            raise DecisionPackError(f"{assumption_id} monotonic_direction is invalid")
        if not valid:
            raise DecisionPackError(f"{assumption_id} scenarios violate monotonic direction")


def _value(material: Mapping[str, Any], assumption_id: str, scenario: str) -> int:
    return int(material["assumptions"][assumption_id]["scenarios"][scenario])


def _cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def evaluate_economics(material: Mapping[str, Any]) -> dict[str, Any]:
    """Compute an exact-cent, full-cost scenario envelope and its stability."""

    validate_assumptions(material)
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        volume = _value(material, "monthly_case_volume", scenario)
        labour = _value(material, "labour_cost_cents_per_hour", scenario)
        assisted_minutes = _value(material, "assisted_review_minutes_per_case", scenario)
        non_ai_minutes = _value(material, "non_ai_review_minutes_per_case", scenario)
        provider_per_case = _value(material, "provider_cost_cents_per_case", scenario)
        support = _value(material, "support_cost_cents_per_month", scenario)
        infrastructure = _value(material, "infrastructure_cost_cents_per_month", scenario)
        non_ai_support = _value(material, "non_ai_support_cost_cents_per_month", scenario)
        non_ai_infrastructure = _value(material, "non_ai_infrastructure_cost_cents_per_month", scenario)
        assisted_failure = _value(material, "assisted_failure_rate_basis_points", scenario)
        non_ai_failure = _value(material, "non_ai_failure_rate_basis_points", scenario)
        failure_impact = _value(material, "failure_impact_cents_per_case", scenario)
        realisation = _value(material, "capacity_realisation_basis_points", scenario)

        assisted_labour = _cents(Decimal(volume * assisted_minutes * labour) / Decimal(60))
        non_ai_labour = _cents(Decimal(volume * non_ai_minutes * labour) / Decimal(60))
        provider = volume * provider_per_case
        assisted_failure_cost = _cents(Decimal(volume * assisted_failure * failure_impact) / Decimal(10_000))
        non_ai_failure_cost = _cents(Decimal(volume * non_ai_failure * failure_impact) / Decimal(10_000))
        assisted_total = assisted_labour + provider + support + infrastructure + assisted_failure_cost
        non_ai_total = non_ai_labour + non_ai_support + non_ai_infrastructure + non_ai_failure_cost
        nominal_capacity_minutes = (non_ai_minutes - assisted_minutes) * volume
        capacity_value = _cents(
            Decimal(nominal_capacity_minutes * labour * realisation) / Decimal(60 * 10_000)
        )
        incremental_nonlabour = (
            provider + support + infrastructure + assisted_failure_cost
            - non_ai_support - non_ai_infrastructure - non_ai_failure_cost
        )
        net_benefit = capacity_value - incremental_nonlabour
        rows.append(
            {
                "assisted_failure_cost_cents": assisted_failure_cost,
                "assisted_failure_rate_basis_points": assisted_failure,
                "assisted_labour_cost_cents": assisted_labour,
                "assisted_review_minutes_per_case": assisted_minutes,
                "assisted_total_operating_cost_cents": assisted_total,
                "capacity_realisation_basis_points": realisation,
                "capacity_realised_value_cents": capacity_value,
                "currency": "EUR",
                "decision_net_benefit_cents": net_benefit,
                "evidence_class": "hypothetical-impact-not-realised-value",
                "failure_impact_cents_per_case": failure_impact,
                "incremental_nonlabour_cost_cents": incremental_nonlabour,
                "infrastructure_cost_cents_per_month": infrastructure,
                "labour_cost_cents_per_hour": labour,
                "monthly_case_volume": volume,
                "nominal_capacity_minutes": nominal_capacity_minutes,
                "non_ai_failure_cost_cents": non_ai_failure_cost,
                "non_ai_failure_rate_basis_points": non_ai_failure,
                "non_ai_infrastructure_cost_cents_per_month": non_ai_infrastructure,
                "non_ai_labour_cost_cents": non_ai_labour,
                "non_ai_review_minutes_per_case": non_ai_minutes,
                "non_ai_support_cost_cents_per_month": non_ai_support,
                "non_ai_total_operating_cost_cents": non_ai_total,
                "provider_cost_cents": provider,
                "provider_cost_cents_per_case": provider_per_case,
                "recommendation_class": "scale_next_experiment" if net_benefit >= 0 else "revise",
                "scenario": scenario,
                "support_cost_cents_per_month": support,
            }
        )

    checks = [
        {
            "assumption_id": assumption_id,
            "direction": material["assumptions"][assumption_id]["monotonic_direction"],
            "passed": True,
            "values": [material["assumptions"][assumption_id]["scenarios"][scenario] for scenario in SCENARIOS],
        }
        for assumption_id in REQUIRED_ASSUMPTIONS
    ]
    classes = [row["recommendation_class"] for row in rows]
    stable = len(set(classes)) == 1
    supports_scale = stable and classes[0] == "scale_next_experiment"
    return {
        "arithmetic": "Decimal inputs with ROUND_HALF_UP to integer cents",
        "assumption_version": material["version"],
        "capacity_realisation_is_separate": True,
        "currency": "EUR",
        "evidence_class": "hypothetical-impact-not-realised-value",
        "monotonic_checks": checks,
        "non_ai_process_alternative_included": True,
        "scenario_class_stable": stable,
        "scenario_recommendation_classes": classes,
        "scenarios": rows,
        "schema_version": "stage2-economics-sensitivity/v1",
        "status": "conclusive" if stable else "inconclusive",
        "supports_scale_next_experiment": supports_scale,
        "total_operating_cost_included": True,
        "value_status": "hypothetical-not-realised",
    }


def decide_from_signals(
    *,
    exact_zero_failures: Sequence[str] = (),
    incomplete_evidence: bool = False,
    pre_run_exposure: bool = False,
    quality_gate_passed: bool = True,
    reliability_gate_passed: bool = True,
    cost_gate_passed: bool = True,
    contract: Mapping[str, Any] | None = None,
) -> str:
    """Apply the U1 precedence contract, never a parallel local rule."""

    if contract is None:
        value = load_canonical_json((PROJECT_ROOT / CONTRACT_PATH).read_bytes())
        if not isinstance(value, Mapping):
            raise DecisionPackError("evaluation contract must be an object")
        contract = value
    return decide_next_gate(
        contract,
        exact_zero_failures=exact_zero_failures,
        incomplete_evidence=incomplete_evidence,
        pre_run_exposure=pre_run_exposure,
        quality_gate_passed=quality_gate_passed,
        reliability_gate_passed=reliability_gate_passed,
        cost_gate_passed=cost_gate_passed,
    )


def build_next_action(decision: str) -> dict[str, Any]:
    """Return exactly one bounded, owner-bound action for a decision class."""

    shared_caps = {
        "pause": (7, 36, 5000, 36),
        "scale_next_experiment": (7, 36, 5000, 36),
        "revise": (7, 12, 2000, 12),
        "stop": (3, 0, 0, 0),
    }
    if decision not in shared_caps:
        raise DecisionPackError(f"unsupported decision class: {decision}")
    days, attempts, spend, cases = shared_caps[decision]
    details = {
        "scale_next_experiment": (
            "Can one new 36-case synthetic evaluation preserve every exact-zero control while completing all eligible commits and telemetry?",
            ["a new frozen synthetic pack and acquisition contract are preregistered", "provider cost and latency capture are mandatory fields"],
            ["any exact-zero control failure", "any oracle exposure before output freeze", "the capped spend or attempt count is reached"],
            "expires after 7 calendar days or at the first exact-zero failure",
        ),
        "revise": (
            "Does the smallest regression set show that the named failed gate is repaired without weakening exact-zero controls?",
            ["one named failed gate has a testable, versioned adaptation"],
            ["any exact-zero control failure", "the adaptation changes a frozen confirmatory result", "the capped spend or attempt count is reached"],
            "review after 12 synthetic cases or 7 calendar days",
        ),
        "pause": (
            "Can a new capped synthetic run achieve 18 of 18 eligible execution commits and complete cost/latency evidence while preserving every exact-zero control?",
            ["the next pack is a new immutable identity", "all 36 provider attempts capture cost and latency or explicit terminal unavailability", "the action remains synthetic and local-MVP only"],
            ["any exact-zero control failure", "any pre-run oracle exposure or source substitution", "provider telemetry remains structurally absent after the first three attempts", "the capped spend or attempt count is reached"],
            "expires after 7 calendar days, 36 attempts, or the first stop condition",
        ),
        "stop": (
            "What root cause and control redesign are required before any new evaluation may be proposed?",
            ["the exact-zero failure record and affected evidence are preserved read-only"],
            ["any attempt to rerun, replace, or suppress the failed evidence", "root cause remains unbounded"],
            "risk-owner review within 3 calendar days",
        ),
    }
    question, entry, stop, expiry = details[decision]
    return {
        "action_count": 1,
        "authorises_company_pilot": False,
        "cap": {
            "currency": "EUR",
            "maximum_calendar_days": days,
            "maximum_provider_attempts": attempts,
            "maximum_spend_cents": spend,
            "maximum_synthetic_cases": cases,
        },
        "decision": decision,
        "entry_conditions": entry,
        "evidence_question": question,
        "expiry_or_review_trigger": expiry,
        "owner": "Raul Rausell",
        "scope": "one public, synthetic, local-MVP evidence action only",
        "stop_conditions": stop,
    }
