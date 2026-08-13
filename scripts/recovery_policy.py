#!/usr/bin/env python3
"""Deterministic evidence, policy, and authority controls for recovery.

Provider output is an input to this module, never a source of authority.  The
decision priority and amount boundaries are intentionally explicit so the
same facts always produce the same governed route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class PolicyControlError(ValueError):
    """Raised when an input cannot safely enter deterministic policy."""


RISK_OWNER = "SYNTHETIC_POLICY_RISK_OWNER"
RECOVERY_SPECIALIST = "SYNTHETIC_RECOVERY_SPECIALIST"
WORKFLOW_OWNER = "SYNTHETIC_WORKFLOW_OWNER"
FINANCE_APPROVER = "SYNTHETIC_FINANCE_APPROVER"
SYSTEM_OWNER = "WORKFLOW_RUNTIME"

REQUIRED_SOURCES = {
    "WAIT_VERIFIED_ETA": ("CARRIER", "CRM", "POLICY"),
    "RESHIP": ("OMS", "WMS", "CARRIER", "INVENTORY", "CRM", "POLICY"),
    "REFUND": ("OMS", "WMS", "CARRIER", "PAYMENT", "CRM", "POLICY"),
}
MESSAGE_FACTS = {
    "WAIT_VERIFIED_ETA": ("WAIT_ESTIMATE_QUALIFIED",),
    "RESHIP": ("RESHIP_PROPOSED",),
    "REFUND": ("REFUND_PROPOSED",),
    "NO_NEW_ACTION": ("PRIOR_REMEDY_CONFIRMED",),
    "EVIDENCE_BLOCKED": ("EVIDENCE_UNRESOLVED",),
    "ACTION_RECOVERY": ("ACTION_STATUS_UNRESOLVED",),
    "CONTROL_STOP": ("SPECIALIST_REVIEW_REQUIRED",),
    "AWAITING_CHOICE": ("CUSTOMER_CHOICE_REQUIRED",),
}
ADDITIONAL_NO_ACTION_FACTS = frozenset(
    {"DUPLICATE_SIGNAL_SUPPRESSED", "NO_ELIGIBLE_QUANTITY_REMAINS"}
)
ALL_MESSAGE_FACTS = frozenset(code for codes in MESSAGE_FACTS.values() for code in codes) | ADDITIONAL_NO_ACTION_FACTS
PROHIBITED_COMPLETION_FACTS = (
    "DELIVERY_COMPLETED",
    "REFUND_COMPLETED",
    "RESHIP_COMPLETED",
    "CUSTOMER_SATISFIED",
    "REALISED_VALUE",
)


@dataclass(frozen=True)
class PolicyDecision:
    outcome_code: str
    proposed_action: str
    authority_route: str
    decision_owner: str
    allowed_actions: tuple[str, ...]
    required_sources: tuple[str, ...]
    missing_or_stale_sources: tuple[str, ...]
    required_message_facts: tuple[str, ...]
    prohibited_message_facts: tuple[str, ...]
    consequential_action_permitted: bool
    policy_id: str
    policy_version: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_actions": list(self.allowed_actions),
            "authority_route": self.authority_route,
            "consequential_action_permitted": self.consequential_action_permitted,
            "decision_owner": self.decision_owner,
            "missing_or_stale_sources": list(self.missing_or_stale_sources),
            "outcome_code": self.outcome_code,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "prohibited_message_facts": list(self.prohibited_message_facts),
            "proposed_action": self.proposed_action,
            "reason_codes": list(self.reason_codes),
            "required_message_facts": list(self.required_message_facts),
            "required_sources": list(self.required_sources),
        }


@dataclass(frozen=True)
class GovernedRecommendation:
    decision: PolicyDecision
    candidate_accepted: bool
    rejection_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_accepted": self.candidate_accepted,
            "decision": self.decision.to_dict(),
            "rejection_codes": list(self.rejection_codes),
        }


class RecoveryPolicyAdapter:
    """Apply pure policy to the permitted application-service projection."""

    @staticmethod
    def _facts(context: Any) -> dict[str, Any]:
        if hasattr(context, "permitted_facts"):
            permitted = context.permitted_facts
            authority_projection = context.policy_authority_projection
        elif isinstance(context, Mapping):
            permitted = context.get("permitted_facts")
            authority_projection = context.get("policy_authority_projection")
        else:
            raise PolicyControlError("application context is unavailable")
        if not isinstance(permitted, Mapping) or not isinstance(authority_projection, Mapping):
            raise PolicyControlError("application context omits governed facts")
        thresholds = authority_projection.get("authority_thresholds")
        if not isinstance(thresholds, Mapping):
            raise PolicyControlError("application context omits authority thresholds")
        projected = dict(permitted)
        projected["authority"] = dict(thresholds)
        return projected

    def decide(self, context: Any) -> PolicyDecision:
        return decide_policy(self._facts(context))

    def evaluate(self, context: Any, candidate: Mapping[str, Any]) -> GovernedRecommendation:
        return govern_candidate(self._facts(context), candidate)


def _require_int(facts: Mapping[str, Any], name: str) -> int:
    value = facts.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyControlError(f"{name} must be a nonnegative integer")
    return value


def _identity(facts: Mapping[str, Any]) -> tuple[str, str]:
    policy_id = facts.get("policy_id")
    policy_version = facts.get("policy_version")
    if policy_id != "SCC-01-RECOVERY-POLICY" or policy_version != "1.0.0":
        raise PolicyControlError("unknown or non-effective recovery policy")
    return policy_id, policy_version


def _decision(
    facts: Mapping[str, Any],
    *,
    outcome: str,
    action: str,
    route: str,
    owner: str,
    reasons: tuple[str, ...],
    required_sources: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    consequential: bool = False,
    message_facts: tuple[str, ...] | None = None,
) -> PolicyDecision:
    policy_id, policy_version = _identity(facts)
    return PolicyDecision(
        outcome_code=outcome,
        proposed_action=action,
        authority_route=route,
        decision_owner=owner,
        allowed_actions=(action,),
        required_sources=required_sources,
        missing_or_stale_sources=missing,
        required_message_facts=message_facts or MESSAGE_FACTS[action],
        prohibited_message_facts=PROHIBITED_COMPLETION_FACTS,
        consequential_action_permitted=consequential,
        policy_id=policy_id,
        policy_version=policy_version,
        reason_codes=reasons,
    )


def _missing_sources(facts: Mapping[str, Any], action: str) -> tuple[str, ...]:
    freshness = facts.get("source_freshness")
    if not isinstance(freshness, Mapping):
        raise PolicyControlError("source freshness is unavailable")
    required = REQUIRED_SOURCES[action]
    return tuple(source for source in required if freshness.get(source) is not True)


def decide_policy(facts: Mapping[str, Any]) -> PolicyDecision:
    """Apply the frozen, first-match decision matrix over derived facts."""

    if not isinstance(facts, Mapping):
        raise PolicyControlError("derived facts must be an object")
    _identity(facts)
    risk_flags = facts.get("risk_stop_flags")
    if not isinstance(risk_flags, list) or any(not isinstance(flag, str) for flag in risk_flags):
        raise PolicyControlError("risk stop flags must be a list of codes")
    if facts.get("active_chargeback") is True or risk_flags:
        flags = tuple(sorted(set(risk_flags) | ({"active_chargeback"} if facts.get("active_chargeback") else set())))
        return _decision(
            facts,
            outcome="CONTROL_STOPPED",
            action="CONTROL_STOP",
            route="SPECIALIST_STOP",
            owner=RISK_OWNER,
            reasons=tuple(f"RISK_STOP:{flag}" for flag in flags),
        )
    if facts.get("prior_remedy_covers_quantity") is True:
        return _decision(
            facts,
            outcome="VERIFIED_NO_NEW_ACTION",
            action="NO_NEW_ACTION",
            route="DIRECT_NO_ACTION",
            owner=SYSTEM_OWNER,
            reasons=("AUTHORITATIVE_PRIOR_REMEDY",),
        )
    if facts.get("has_unresolved_action") is True:
        return _decision(
            facts,
            outcome="ACTION_RECOVERY_REQUIRED",
            action="ACTION_RECOVERY",
            route="RECOVERY_RECONCILIATION",
            owner=RECOVERY_SPECIALIST,
            reasons=("UNRESOLVED_PRIOR_ACTION",),
        )
    if facts.get("has_source_conflict") is True:
        return _decision(
            facts,
            outcome="EVIDENCE_BLOCKED",
            action="EVIDENCE_BLOCKED",
            route="EVIDENCE_REVIEW",
            owner=RECOVERY_SPECIALIST,
            reasons=("MATERIAL_SOURCE_CONFLICT",),
        )
    if facts.get("duplicate_signal") is True:
        return _decision(
            facts,
            outcome="VERIFIED_NO_NEW_ACTION",
            action="NO_NEW_ACTION",
            route="DIRECT_NO_ACTION",
            owner=SYSTEM_OWNER,
            reasons=("DUPLICATE_INTAKE_SIGNAL",),
            message_facts=("DUPLICATE_SIGNAL_SUPPRESSED",),
        )

    choice = facts.get("customer_choice")
    if choice is None:
        return _decision(
            facts,
            outcome="AWAITING_CHOICE",
            action="AWAITING_CHOICE",
            route="AWAITING_CHOICE",
            owner=RECOVERY_SPECIALIST,
            reasons=("CUSTOMER_CHOICE_MISSING",),
        )
    if choice not in {"WAIT", "RESHIP", "REFUND"}:
        raise PolicyControlError("customer choice is outside policy vocabulary")
    action = "WAIT_VERIFIED_ETA" if choice == "WAIT" else choice
    required = REQUIRED_SOURCES[action]
    missing = _missing_sources(facts, action)
    if missing:
        return _decision(
            facts,
            outcome="EVIDENCE_BLOCKED",
            action="EVIDENCE_BLOCKED",
            route="EVIDENCE_REVIEW",
            owner=RECOVERY_SPECIALIST,
            reasons=tuple(f"MISSING_OR_STALE:{source}" for source in missing),
            required_sources=required,
            missing=missing,
        )
    if action == "WAIT_VERIFIED_ETA":
        if not isinstance(facts.get("reliable_eta_at"), str):
            return _decision(
                facts,
                outcome="EVIDENCE_BLOCKED",
                action="EVIDENCE_BLOCKED",
                route="EVIDENCE_REVIEW",
                owner=RECOVERY_SPECIALIST,
                reasons=("RELIABLE_ETA_UNAVAILABLE",),
                required_sources=required,
            )
        return _decision(
            facts,
            outcome="WAIT_VERIFIED_ETA",
            action=action,
            route="DIRECT_NO_ACTION",
            owner=SYSTEM_OWNER,
            reasons=("CUSTOMER_SELECTED_WAIT_WITH_RELIABLE_ETA",),
            required_sources=required,
        )

    remaining = _require_int(facts, "remaining_quantity")
    if remaining == 0:
        return _decision(
            facts,
            outcome="VERIFIED_NO_NEW_ACTION",
            action="NO_NEW_ACTION",
            route="DIRECT_NO_ACTION",
            owner=SYSTEM_OWNER,
            reasons=("NO_ELIGIBLE_QUANTITY_REMAINS",),
            message_facts=("NO_ELIGIBLE_QUANTITY_REMAINS",),
        )
    if action == "RESHIP" and _require_int(facts, "available_replacement_quantity") < remaining:
        return _decision(
            facts,
            outcome="EVIDENCE_BLOCKED",
            action="EVIDENCE_BLOCKED",
            route="EVIDENCE_REVIEW",
            owner=RECOVERY_SPECIALIST,
            reasons=("INSUFFICIENT_REPLACEMENT_STOCK",),
            required_sources=required,
        )

    exposure = _require_int(facts, "affected_value_cents")
    order_value = _require_int(facts, "order_value_cents")
    authority = facts.get("authority")
    if not isinstance(authority, Mapping):
        raise PolicyControlError("authority thresholds are unavailable")
    delegated = authority.get("delegated_max_exposure_cents")
    owner_max = authority.get("workflow_owner_max_exposure_cents")
    finance_order = authority.get("finance_review_order_value_cents")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (delegated, owner_max, finance_order)):
        raise PolicyControlError("authority thresholds must be positive integer cents")
    if not delegated < owner_max <= finance_order:
        raise PolicyControlError("authority thresholds are inconsistent")
    if exposure > owner_max or order_value > finance_order:
        route, owner = "FINANCE_APPROVAL", FINANCE_APPROVER
        outcome = "FINANCE_APPROVAL_REQUIRED"
    elif exposure > delegated:
        route, owner = "WORKFLOW_OWNER_APPROVAL", WORKFLOW_OWNER
        outcome = "WORKFLOW_OWNER_APPROVAL_REQUIRED"
    else:
        route, owner = "DELEGATED_DECISION", RECOVERY_SPECIALIST
        outcome = "DELEGATED_ACTION_READY"
    return _decision(
        facts,
        outcome=outcome,
        action=action,
        route=route,
        owner=owner,
        reasons=("POLICY_ELIGIBLE_RECOVERY", route),
        required_sources=required,
        consequential=True,
    )


def govern_candidate(
    facts: Mapping[str, Any], candidate: Mapping[str, Any]
) -> GovernedRecommendation:
    """Compare a provider proposal with policy without delegating authority."""

    if not isinstance(candidate, Mapping):
        raise PolicyControlError("provider candidate must be an object")
    decision = decide_policy(facts)
    proposed_action = candidate.get("proposed_action")
    proposed_route = candidate.get("proposed_route")
    message_facts = candidate.get("message_fact_candidates")
    if not isinstance(message_facts, list) or any(not isinstance(code, str) for code in message_facts):
        raise PolicyControlError("message fact candidates must be a list of codes")
    unknown = sorted(set(message_facts) - ALL_MESSAGE_FACTS)
    if unknown:
        raise PolicyControlError("provider proposed an unknown or unsupported message fact")
    prohibited = sorted(set(message_facts) & set(PROHIBITED_COMPLETION_FACTS))
    if prohibited:
        raise PolicyControlError("provider proposed a prohibited completion fact")
    rejection_codes: list[str] = []
    if proposed_action != decision.proposed_action:
        rejection_codes.append("PROVIDER_ACTION_DISAGREES_WITH_POLICY")
    if proposed_route != decision.authority_route:
        rejection_codes.append("PROVIDER_ROUTE_DISAGREES_WITH_POLICY")
    if not set(message_facts).issubset(decision.required_message_facts):
        rejection_codes.append("PROVIDER_MESSAGE_FACT_UNSUPPORTED")
    return GovernedRecommendation(
        decision=decision,
        candidate_accepted=not rejection_codes,
        rejection_codes=tuple(rejection_codes),
    )
