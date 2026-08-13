#!/usr/bin/env python3
"""Provider-neutral application ports, DTOs, and recovery use cases.

Concrete CLI, filesystem, provider, policy, adapter, and verifier code depends
on this inward boundary.  The core knows only durable workspace and pure-fact
ports and never imports an evaluator, generator, provider host, or action path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from scripts.recovery_state import WorkflowState, next_action_labels
from scripts.stage2_contracts import (
    canonical_sha256,
    reject_evaluator_only_fields,
    validate_neutral_record,
)


@dataclass(frozen=True)
class TransitionCommand:
    target_state: WorkflowState
    event_type: str
    actor_kind: str
    actor_id: str
    expected_case_revision: int
    expected_ledger_head: str
    command_id: str
    links: Mapping[str, str] = field(default_factory=dict)
    decision_or_effect: Mapping[str, Any] = field(default_factory=dict)
    action_count: int = 0


@dataclass(frozen=True)
class RevisionCommand:
    event_type: str
    actor_kind: str
    actor_id: str
    expected_case_revision: int
    expected_ledger_head: str
    command_id: str


@dataclass(frozen=True)
class RecommendationCommand:
    recommendation_id: str
    attempt_id: str
    expected_case_revision: int
    expected_ledger_head: str
    command_id: str
    route_command_id: str | None = None


@dataclass(frozen=True)
class ActionReservationCommand:
    action: Mapping[str, Any]
    authority_event: Mapping[str, Any]
    recommending_provider_id: str
    now: str
    command_id: str


@dataclass(frozen=True)
class ObjectCommand:
    record: Mapping[str, Any]
    command_id: str


@dataclass(frozen=True)
class RecommendationOutcomeDTO:
    recommendation_id: str
    provider_attempt_id: str
    provider_terminal_status: str
    provider_validation_result: str
    provider_candidate: Mapping[str, Any] | None
    governed_recommendation: Mapping[str, Any]
    fallback_used: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "fallback_used": self.fallback_used,
            "governed_recommendation": dict(self.governed_recommendation),
            "provider_attempt_id": self.provider_attempt_id,
            "provider_candidate": (
                dict(self.provider_candidate) if self.provider_candidate is not None else None
            ),
            "provider_terminal_status": self.provider_terminal_status,
            "provider_validation_result": self.provider_validation_result,
            "recommendation_id": self.recommendation_id,
        }


@dataclass(frozen=True)
class CaseContextDTO:
    run_id: str
    case_id: str
    case_revision: int
    state: WorkflowState
    sequence: int
    ledger_head_digest: str
    source_event_cut_sha256: str
    revision_pin_sha256: str
    permitted_facts: Mapping[str, Any]
    cited_sources: tuple[Mapping[str, Any], ...]
    evidence_gaps: tuple[str, ...]
    policy_authority_projection: Mapping[str, Any]
    allowed_next_transitions: tuple[str, ...]
    active_object_ids: Mapping[str, str]
    invalidated_object_ids: tuple[str, ...]
    frozen: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_object_ids": dict(sorted(self.active_object_ids.items())),
            "allowed_next_transitions": list(self.allowed_next_transitions),
            "case_id": self.case_id,
            "case_revision": self.case_revision,
            "cited_sources": [dict(item) for item in self.cited_sources],
            "evidence_gaps": list(self.evidence_gaps),
            "frozen": self.frozen,
            "invalidated_object_ids": list(self.invalidated_object_ids),
            "ledger_head_digest": self.ledger_head_digest,
            "permitted_facts": dict(self.permitted_facts),
            "policy_authority_projection": dict(self.policy_authority_projection),
            "revision_pin_sha256": self.revision_pin_sha256,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "source_event_cut_sha256": self.source_event_cut_sha256,
            "state": self.state.value,
        }


@runtime_checkable
class RunProjectionPort(Protocol):
    run_id: str
    case_id: str
    case_revision: int
    state: WorkflowState
    sequence: int
    ledger_head_digest: str
    source_event_cut_sha256: str
    revision_pin_sha256: str
    active_object_ids: Mapping[str, str]
    invalidated_object_ids: tuple[str, ...]
    frozen: bool


@runtime_checkable
class RecoveryWorkspacePort(Protocol):
    def replay(self) -> RunProjectionPort: ...

    def load_source_batch(self, revision: int | None = None) -> Mapping[str, Any]: ...

    def append_transition(
        self,
        *,
        target_state: WorkflowState,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
        links: Mapping[str, str] | None = None,
        decision_or_effect: Mapping[str, Any] | None = None,
        action_count: int = 0,
    ) -> RunProjectionPort: ...

    def reserve_action(
        self,
        action: Mapping[str, Any],
        authority: Mapping[str, Any],
        *,
        command_id: str,
    ) -> RunProjectionPort: ...

    def revise_source(
        self,
        source_batch: Mapping[str, Any],
        *,
        event_type: str,
        actor_kind: str,
        actor_id: str,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
    ) -> RunProjectionPort: ...

    def reopen_with_revision(
        self,
        source_batch: Mapping[str, Any],
        *,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
    ) -> RunProjectionPort: ...

    def resume(self, checkpoint=None, *, recover_partial_tail: bool = False) -> RunProjectionPort: ...

    def freeze(self) -> Mapping[str, Any]: ...

    def verify(self) -> list[str]: ...


@runtime_checkable
class Stage2FactsPort(Protocol):
    def derive(self, source_batch: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class RecommendationProviderPort(Protocol):
    def propose_context(
        self,
        context: Mapping[str, Any],
        *,
        attempt_id: str,
        deadline_monotonic: float | None = None,
    ) -> Any: ...


@runtime_checkable
class RecoveryPolicyPort(Protocol):
    def decide(self, context: Mapping[str, Any]) -> Any: ...

    def evaluate(self, context: Mapping[str, Any], candidate: Mapping[str, Any]) -> Any: ...


@runtime_checkable
class ActionAdapterPort(Protocol):
    def execute(self, action: Mapping[str, Any], *, fault: str | None = None) -> Mapping[str, Any]: ...


def _evidence_gaps(facts: Mapping[str, Any]) -> tuple[str, ...]:
    gaps: list[str] = []
    if facts.get("has_source_conflict"):
        gaps.append("SOURCE_CONFLICT")
    stale = sorted(
        name for name, fresh in facts.get("source_freshness", {}).items() if not fresh
    )
    gaps.extend(f"STALE_SOURCE:{name}" for name in stale)
    if facts.get("has_unresolved_action"):
        gaps.append("UNRESOLVED_ACTION")
    if facts.get("remaining_quantity", 0) and facts.get("customer_choice") is None:
        gaps.append("CUSTOMER_CHOICE_MISSING")
    return tuple(gaps)


def _permitted_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    permitted_names = (
        "active_chargeback",
        "affected_value_cents",
        "all_sources_fresh",
        "available_replacement_quantity",
        "captured_amount_cents",
        "currency",
        "customer_choice",
        "delivered_quantity",
        "duplicate_signal",
        "has_source_conflict",
        "has_unresolved_action",
        "lines",
        "order_value_cents",
        "ordered_quantity",
        "parcels",
        "policy_id",
        "policy_version",
        "prior_remedy_covers_quantity",
        "recovered_quantity",
        "refunded_cents",
        "refunded_quantity",
        "reliable_eta_at",
        "remaining_quantity",
        "replacement_quantity",
        "risk_stop_flags",
        "shipped_quantity",
        "source_freshness",
    )
    return {name: facts[name] for name in permitted_names if name in facts}


def _citations(source_batch: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        {
            "effective_at": record["payload"]["effective_at"],
            "observed_at": record["payload"]["observed_at"],
            "provenance_digest": record["payload"]["provenance_digest"],
            "record_id": record["record_id"],
            "source_name": record["payload"]["source_name"],
        }
        for record in source_batch["payload"]["records"]
    )


class RecoveryApplicationService:
    """One application surface shared by operator CLI and provider adapters."""

    def __init__(self, workspace: RecoveryWorkspacePort, facts: Stage2FactsPort):
        self.workspace = workspace
        self.facts = facts

    def _context_from_projection(self, projection: RunProjectionPort) -> CaseContextDTO:
        batch = self.workspace.load_source_batch(projection.case_revision)
        facts = dict(self.facts.derive(batch))
        permitted = _permitted_facts(facts)
        authority = {
            "authority_thresholds": dict(facts.get("authority", {})),
            "policy_id": facts.get("policy_id"),
            "policy_version": facts.get("policy_version"),
            "provider_has_authority": False,
        }
        context = CaseContextDTO(
            run_id=projection.run_id,
            case_id=projection.case_id,
            case_revision=projection.case_revision,
            state=projection.state,
            sequence=projection.sequence,
            ledger_head_digest=projection.ledger_head_digest,
            source_event_cut_sha256=projection.source_event_cut_sha256,
            revision_pin_sha256=projection.revision_pin_sha256,
            permitted_facts=permitted,
            cited_sources=_citations(batch),
            evidence_gaps=_evidence_gaps(facts),
            policy_authority_projection=authority,
            allowed_next_transitions=next_action_labels(projection.state),
            active_object_ids=dict(projection.active_object_ids),
            invalidated_object_ids=tuple(projection.invalidated_object_ids),
            frozen=projection.frozen,
        )
        reject_evaluator_only_fields(context.to_dict(), "context")
        return context

    def inspect(self) -> CaseContextDTO:
        return self._context_from_projection(self.workspace.replay())

    def provider_context(self) -> CaseContextDTO:
        """Return exactly the operator-permitted projection, with no hidden enrichment."""

        return self.inspect()

    def recommend(
        self,
        command: RecommendationCommand,
        provider: RecommendationProviderPort,
        policy: RecoveryPolicyPort,
    ) -> RecommendationOutcomeDTO:
        """Record the provider proposal and independently governed outcome."""

        context = self.provider_context()
        if context.state is not WorkflowState.CONTEXT_READY:
            raise ValueError("recommendation requires CONTEXT_READY state")
        if (
            context.case_revision != command.expected_case_revision
            or context.ledger_head_digest != command.expected_ledger_head
        ):
            raise ValueError("recommendation command is stale")
        result = provider.propose_context(
            context.to_dict(),
            attempt_id=command.attempt_id,
        )
        candidate = result.candidate
        if candidate is None:
            decision = policy.decide(context)
            governed = {
                "candidate_accepted": False,
                "decision": decision.to_dict(),
                "rejection_codes": [f"PROVIDER_{result.terminal_status}"],
            }
        else:
            governed = policy.evaluate(context, candidate).to_dict()
        fallback_used = result.terminal_status == "FALLBACK" or result.fallback_disposition != "NOT_USED"
        outcome = RecommendationOutcomeDTO(
            recommendation_id=command.recommendation_id,
            provider_attempt_id=result.attempt_id,
            provider_terminal_status=result.terminal_status,
            provider_validation_result=result.validation_result,
            provider_candidate=candidate,
            governed_recommendation=governed,
            fallback_used=fallback_used,
        )
        reject_evaluator_only_fields(outcome.to_dict(), "recommendation_outcome")
        recommendation_projection = self.workspace.append_transition(
            target_state=WorkflowState.RECOMMENDATION_READY,
            event_type="RECOMMENDATION_RECORDED",
            actor_kind="provider_boundary",
            actor_id="S2-ACTOR-RECOMMENDATION-BOUNDARY",
            expected_case_revision=command.expected_case_revision,
            expected_ledger_head=command.expected_ledger_head,
            command_id=command.command_id,
            links={"recommendation_id": command.recommendation_id},
            decision_or_effect={
                "governed_recommendation": governed,
                "provider_proposal": {
                    "attempt_id": result.attempt_id,
                    "candidate": dict(candidate) if candidate is not None else None,
                    "fallback_disposition": result.fallback_disposition,
                    "terminal_status": result.terminal_status,
                    "validation_result": result.validation_result,
                },
            },
            action_count=0,
        )
        if command.route_command_id is not None:
            decision = governed["decision"]
            action = decision["proposed_action"]
            route = decision["authority_route"]
            if route == "SPECIALIST_STOP":
                target = WorkflowState.CONTROL_STOPPED
                event_type = "CONTROL_STOP_APPLIED"
            elif route == "AWAITING_CHOICE":
                target = WorkflowState.AWAITING_CHOICE
                event_type = "CUSTOMER_CHOICE_REQUIRED"
            elif route in {
                "DELEGATED_DECISION",
                "WORKFLOW_OWNER_APPROVAL",
                "FINANCE_APPROVAL",
            }:
                target = WorkflowState.AWAITING_APPROVAL
                event_type = "AUTHORITY_DECISION_REQUIRED"
            elif route == "DIRECT_NO_ACTION" and action == "WAIT_VERIFIED_ETA":
                # A policy decision is not verification.  The guarded outward
                # orchestrator must independently derive the wait condition,
                # persist its verification, and derive the unsent message.
                return outcome
            elif route == "DIRECT_NO_ACTION" and action == "NO_NEW_ACTION":
                return outcome
            else:
                raise ValueError("governed outcome requires evidence/recovery handling before routing")
            self.workspace.append_transition(
                target_state=target,
                event_type=event_type,
                actor_kind="deterministic_control",
                actor_id="S2-ACTOR-POLICY-CONTROL",
                expected_case_revision=recommendation_projection.case_revision,
                expected_ledger_head=recommendation_projection.ledger_head_digest,
                command_id=command.route_command_id,
                links={"recommendation_id": command.recommendation_id},
                decision_or_effect={"governed_recommendation": governed},
                action_count=0,
            )
        return outcome

    def advance(self, command: TransitionCommand) -> CaseContextDTO:
        """Advance only the declared intake/context boundary.

        Consequential, recommendation, authority, verification, communication,
        closure, and reopen transitions belong to their guarded use cases.  The
        caller-supplied metadata retained on ``TransitionCommand`` is ignored
        for compatibility with the U3 inward DTO; canonical event metadata is
        derived here from the durable predecessor and requested early target.
        """

        current = self.workspace.replay()
        declared = {
            (WorkflowState.RECEIVED, WorkflowState.DEDUPLICATED): (
                "CASE_DEDUPLICATED",
                "system",
                "S2-ACTOR-DEDUP",
            ),
            (WorkflowState.DEDUPLICATED, WorkflowState.EVIDENCE_BLOCKED): (
                "INTAKE_EVIDENCE_BLOCKED",
                "system",
                "S2-ACTOR-CONTEXT-CONTROL",
            ),
            (WorkflowState.DEDUPLICATED, WorkflowState.CONTEXT_READY): (
                "CONTEXT_ASSEMBLED",
                "system",
                "S2-ACTOR-CONTEXT",
            ),
            (WorkflowState.EVIDENCE_BLOCKED, WorkflowState.CONTEXT_READY): (
                "CONTEXT_REASSEMBLED",
                "system",
                "S2-ACTOR-CONTEXT-CONTROL",
            ),
        }
        idempotent_retry = {
            WorkflowState.DEDUPLICATED: (
                "CASE_DEDUPLICATED",
                "system",
                "S2-ACTOR-DEDUP",
            ),
            WorkflowState.EVIDENCE_BLOCKED: (
                "INTAKE_EVIDENCE_BLOCKED",
                "system",
                "S2-ACTOR-CONTEXT-CONTROL",
            ),
            WorkflowState.CONTEXT_READY: (
                "CONTEXT_ASSEMBLED",
                "system",
                "S2-ACTOR-CONTEXT",
            ),
        }
        try:
            if current.state is command.target_state:
                event_type, actor_kind, actor_id = idempotent_retry[command.target_state]
            else:
                event_type, actor_kind, actor_id = declared[
                    (current.state, command.target_state)
                ]
        except KeyError as error:
            raise ValueError("public advance is limited to declared intake/context transitions") from error
        projection = self.workspace.append_transition(
            target_state=command.target_state,
            event_type=event_type,
            actor_kind=actor_kind,
            actor_id=actor_id,
            expected_case_revision=command.expected_case_revision,
            expected_ledger_head=command.expected_ledger_head,
            command_id=command.command_id,
            links={},
            decision_or_effect={},
            action_count=0,
        )
        return self._context_from_projection(projection)

    def reserve_action(self, command: ActionReservationCommand) -> CaseContextDTO:
        """Atomically consume exact authority and reserve one action/idempotency owner."""

        from scripts.recovery_actions import reserve_action_atomically

        projection = reserve_action_atomically(
            self.workspace,
            command.action,
            command.authority_event,
            recommending_provider_id=command.recommending_provider_id,
            now=command.now,
            command_id=command.command_id,
        )
        return self._context_from_projection(projection)

    def _record_action_execution(
        self,
        action: Mapping[str, Any],
        adapter: ActionAdapterPort,
        *,
        start_command_id: str,
        outcome_command_id: str,
        fault: str | None = None,
    ) -> Mapping[str, Any]:
        """Run only a reserved local action and preserve unknown effects for recovery."""

        current = self.workspace.replay()
        action_payload = action.get("payload")
        if not isinstance(action_payload, Mapping):
            raise ValueError("active action payload is unavailable")
        if (
            current.active_object_ids.get("action_id") != action.get("record_id")
            or action_payload.get("action_id") != action.get("record_id")
            or action_payload.get("case_id") != current.case_id
            or action_payload.get("case_revision") != current.case_revision
        ):
            raise ValueError("action execution is not bound to the active exact action")
        if current.state is WorkflowState.ACTION_RESERVED:
            current = self.workspace.append_transition(
                target_state=WorkflowState.ACTION_PENDING,
                event_type="ACTION_EFFECT_STARTED",
                actor_kind="deterministic_control",
                actor_id="S2-ACTOR-ACTION-CONTROL",
                expected_case_revision=current.case_revision,
                expected_ledger_head=current.ledger_head_digest,
                command_id=start_command_id,
                links={"action_id": action["record_id"]},
                decision_or_effect={"action_state": "effect_started"},
                action_count=1,
            )
        elif current.state is not WorkflowState.ACTION_RECOVERY:
            raise ValueError("action execution requires a reservation or recovery state")
        try:
            receipt = adapter.execute(action, fault=fault)
        except RuntimeError as error:
            latest = self.workspace.replay()
            if latest.state is WorkflowState.ACTION_PENDING:
                self.workspace.append_transition(
                    target_state=WorkflowState.ACTION_RECOVERY,
                    event_type="ACTION_EFFECT_OUTCOME_UNKNOWN",
                    actor_kind="simulated_adapter",
                    actor_id="S2-ACTOR-LOCAL-ADAPTER",
                    expected_case_revision=latest.case_revision,
                    expected_ledger_head=latest.ledger_head_digest,
                    command_id=outcome_command_id,
                    links={"action_id": action["record_id"]},
                    decision_or_effect={
                        "action_state": "effect_unknown",
                        "failure_class": type(error).__name__,
                        "reconciliation_required": True,
                    },
                    action_count=1,
                )
            raise
        latest = self.workspace.replay()
        if latest.state not in {WorkflowState.ACTION_PENDING, WorkflowState.ACTION_RECOVERY}:
            raise ValueError("receipt cannot advance the current action state")
        self.workspace.append_transition(
            target_state=WorkflowState.VERIFYING,
            event_type="ADAPTER_RECEIPT_RECORDED_AFTER_RECONCILIATION",
            actor_kind="simulated_adapter",
            actor_id="S2-ACTOR-LOCAL-ADAPTER",
            expected_case_revision=latest.case_revision,
            expected_ledger_head=latest.ledger_head_digest,
            command_id=outcome_command_id,
            links={
                "action_id": action["record_id"],
                "receipt_id": receipt["record_id"],
            },
            decision_or_effect={
                "action_contract_digest": action_payload["action_contract_digest"],
                "action_state": "effect_observed",
                "case_id": current.case_id,
                "case_revision": current.case_revision,
                "receipt_digest": canonical_sha256(dict(receipt)),
                "receipt_is_verification": False,
                "reconciled_before_retry": receipt["payload"]["reconciled_before_retry"],
            },
            action_count=1,
        )
        return receipt

    def _record_verification(self, command: ObjectCommand) -> CaseContextDTO:
        """Record a distinct system verification and advance only its classification."""

        try:
            record = validate_neutral_record(command.record)
        except (TypeError, ValueError) as error:
            raise ValueError("verification object is invalid") from error
        if (
            record.get("record_type") != "verification"
            or record.get("schema_version") != "stage2-verification/v1"
        ):
            raise ValueError("verification object has the wrong type")
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("verification payload is unavailable")
        classification = payload.get("classification")
        if classification == "VERIFIED_REMEDY":
            target = WorkflowState.VERIFIED_REMEDY
            event_type = "AUTHORITATIVE_REMEDY_VERIFIED"
        elif classification == "VERIFICATION_FAILED":
            target = WorkflowState.VERIFICATION_FAILED
            event_type = "AUTHORITATIVE_VERIFICATION_FAILED"
        else:
            raise ValueError("action verification classification is invalid")
        current = self.workspace.replay()
        if current.state is not WorkflowState.VERIFYING:
            raise ValueError("verification requires VERIFYING state")
        active_action_id = current.active_object_ids.get("action_id")
        if (
            payload.get("case_id") != current.case_id
            or payload.get("case_revision") != current.case_revision
            or payload.get("action_id") != active_action_id
            or not isinstance(payload.get("action_contract_digest"), str)
        ):
            raise ValueError("verification is not bound to the active exact action")
        expected_milestones = {
            "VERIFIED_REMEDY": {
                "REFUND_COMMITTED_EXACT",
                "REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED",
            },
            "VERIFICATION_FAILED": {
                "REFUND_POSTCONDITION_MISSING_OR_INEXACT",
                "REPLACEMENT_POSTCONDITION_MISSING_OR_INEXACT",
            },
        }
        if payload.get("milestone") not in expected_milestones[classification]:
            raise ValueError("verification milestone contradicts its classification")
        verification_digest = canonical_sha256(record)
        projection = self.workspace.append_transition(
            target_state=target,
            event_type=event_type,
            actor_kind="read_only_system_verifier",
            actor_id="S2-ACTOR-SYSTEM-VERIFIER",
            expected_case_revision=current.case_revision,
            expected_ledger_head=current.ledger_head_digest,
            command_id=command.command_id,
            links={
                "action_id": payload["action_id"],
                "verification_id": record["record_id"],
            },
            decision_or_effect={
                "action_contract_digest": payload["action_contract_digest"],
                "adapter_receipt_trusted": False,
                "authoritative_effect_digests": payload["authoritative_effect_digests"],
                "case_id": current.case_id,
                "case_revision": current.case_revision,
                "classification": classification,
                "milestone": payload["milestone"],
                "verification_digest": verification_digest,
            },
            action_count=1,
        )
        return self._context_from_projection(projection)

    def _record_no_action_verification(self, command: ObjectCommand) -> CaseContextDTO:
        """Bind one validated direct condition before any communication artifact."""

        try:
            record = validate_neutral_record(command.record)
        except (TypeError, ValueError) as error:
            raise ValueError("no-action verification object is invalid") from error
        payload = record.get("payload")
        if (
            record.get("record_type") != "verification"
            or record.get("schema_version") != "stage2-verification/v1"
            or not isinstance(payload, Mapping)
        ):
            raise ValueError("no-action verification object has the wrong type")
        current = self.workspace.replay()
        classification = payload.get("classification")
        event_by_classification = {
            "VERIFIED_WAIT_CONDITION": "VERIFIED_WAIT_CONDITION",
            "VERIFIED_NO_NEW_ACTION": "VERIFIED_NO_NEW_ACTION",
        }
        if current.state is not WorkflowState.RECOMMENDATION_READY:
            raise ValueError("no-action verification requires RECOMMENDATION_READY")
        if classification not in event_by_classification:
            raise ValueError("direct condition classification is invalid")
        if (
            payload.get("case_id") != current.case_id
            or payload.get("case_revision") != current.case_revision
            or payload.get("consequential_action_count") != 0
            or current.active_object_ids.get("action_id") is not None
            or not payload.get("cited_fact_ids")
        ):
            raise ValueError("direct condition is not bound to current zero-action evidence")
        recommendation_id = current.active_object_ids.get("recommendation_id")
        if not recommendation_id:
            raise ValueError("direct condition requires an active governed recommendation")
        projection = self.workspace.append_transition(
            target_state=WorkflowState.COMMUNICATION_READY,
            event_type=event_by_classification[classification],
            actor_kind="read_only_system_verifier",
            actor_id="S2-ACTOR-SYSTEM-VERIFIER",
            expected_case_revision=current.case_revision,
            expected_ledger_head=current.ledger_head_digest,
            command_id=command.command_id,
            links={
                "recommendation_id": recommendation_id,
                "verification_id": record["record_id"],
            },
            decision_or_effect={
                "case_id": current.case_id,
                "case_revision": current.case_revision,
                "classification": classification,
                "milestone": payload["milestone"],
                "verification_digest": canonical_sha256(record),
            },
            action_count=0,
        )
        return self._context_from_projection(projection)

    def _record_communication(
        self,
        command: ObjectCommand,
        verification: Mapping[str, Any],
    ) -> CaseContextDTO:
        """Record one unsent artifact after a supported verified condition."""

        try:
            record = validate_neutral_record(command.record)
            verified_record = validate_neutral_record(verification)
        except (TypeError, ValueError) as error:
            raise ValueError("communication binding is invalid") from error
        if (
            record.get("record_type") != "communication"
            or record.get("schema_version") != "stage2-communication/v1"
        ):
            raise ValueError("communication object has the wrong type")
        payload = record.get("payload")
        if not isinstance(payload, Mapping) or payload.get("unsent") is not True or payload.get("send_capability") is not False:
            raise ValueError("communication must be an unsent local artifact")
        current = self.workspace.replay()
        if current.state is WorkflowState.VERIFIED_REMEDY:
            target = WorkflowState.COMMUNICATION_READY
            event_type = "VERIFIED_COMMUNICATION_CREATED"
        elif current.state is WorkflowState.COMMUNICATION_READY:
            target = WorkflowState.COMMUNICATION_READY
            event_type = "COMMUNICATION_RECORDED"
        else:
            raise ValueError("communication requires a verified remedy/no-action condition")
        verification_payload = verified_record.get("payload")
        verification_id = verified_record.get("record_id")
        if not isinstance(verification_payload, Mapping):
            raise ValueError("active verification payload is unavailable")
        if (
            current.active_object_ids.get("verification_id") != verification_id
            or payload.get("case_id") != current.case_id
            or payload.get("case_revision") != current.case_revision
            or verification_payload.get("case_id") != current.case_id
            or verification_payload.get("case_revision") != current.case_revision
            or payload.get("classification") != verification_payload.get("classification")
            or payload.get("milestone") != verification_payload.get("milestone")
            or payload.get("citations") != [verification_id]
        ):
            raise ValueError("communication is not derived from the active verification")
        expected_action_count = (
            1 if payload.get("classification") == "VERIFIED_REMEDY" else 0
        )
        if payload.get("consequential_action_count") != expected_action_count:
            raise ValueError("communication consequential-action count is invalid")
        communication_digest = canonical_sha256(record)
        verification_digest = canonical_sha256(verified_record)
        projection = self.workspace.append_transition(
            target_state=target,
            event_type=event_type,
            actor_kind="deterministic_communication_gate",
            actor_id="S2-ACTOR-COMMUNICATION-GATE",
            expected_case_revision=current.case_revision,
            expected_ledger_head=current.ledger_head_digest,
            command_id=command.command_id,
            links={
                "communication_id": record["record_id"],
                "verification_id": verification_id,
            },
            decision_or_effect={
                "case_id": current.case_id,
                "case_revision": current.case_revision,
                "classification": payload["classification"],
                "communication_digest": communication_digest,
                "fact_codes": list(payload["fact_codes"]),
                "send_capability": False,
                "unsent": True,
                "verification_digest": verification_digest,
            },
            action_count=payload["consequential_action_count"],
        )
        return self._context_from_projection(projection)

    def _close(
        self,
        *,
        command_id: str,
        closure_id: str,
        verification: Mapping[str, Any],
        communication: Mapping[str, Any],
    ) -> CaseContextDTO:
        current = self.workspace.replay()
        if current.state is not WorkflowState.COMMUNICATION_READY:
            raise ValueError("closure requires a communication-ready verified condition")
        try:
            verified_record = validate_neutral_record(verification)
            communication_record = validate_neutral_record(communication)
        except (TypeError, ValueError) as error:
            raise ValueError("closure evidence is invalid") from error
        verification_payload = verified_record.get("payload")
        communication_payload = communication_record.get("payload")
        verification_id = verified_record.get("record_id")
        communication_id = communication_record.get("record_id")
        if not isinstance(verification_payload, Mapping) or not isinstance(
            communication_payload, Mapping
        ):
            raise ValueError("closure evidence payload is unavailable")
        if (
            current.active_object_ids.get("verification_id") != verification_id
            or current.active_object_ids.get("communication_id") != communication_id
            or verification_payload.get("case_id") != current.case_id
            or verification_payload.get("case_revision") != current.case_revision
            or communication_payload.get("case_id") != current.case_id
            or communication_payload.get("case_revision") != current.case_revision
            or communication_payload.get("classification")
            != verification_payload.get("classification")
            or communication_payload.get("milestone") != verification_payload.get("milestone")
            or communication_payload.get("citations") != [verification_id]
            or communication_payload.get("unsent") is not True
            or communication_payload.get("send_capability") is not False
        ):
            raise ValueError("closure is not bound to active verification and communication")
        projection = self.workspace.append_transition(
            target_state=WorkflowState.CLOSED,
            event_type="RECOVERY_WORKFLOW_CLOSED",
            actor_kind="deterministic_control",
            actor_id="S2-ACTOR-CLOSURE-CONTROL",
            expected_case_revision=current.case_revision,
            expected_ledger_head=current.ledger_head_digest,
            command_id=command_id,
            links={
                "closure_id": closure_id,
                "communication_id": communication_id,
                "verification_id": verification_id,
            },
            decision_or_effect={
                "closure_is_customer_outcome": False,
                "communication_digest": canonical_sha256(communication_record),
                "verification_digest": canonical_sha256(verified_record),
            },
            action_count=0,
        )
        return self._context_from_projection(projection)

    def revise_source(
        self,
        source_batch: Mapping[str, Any],
        command: RevisionCommand,
    ) -> CaseContextDTO:
        projection = self.workspace.revise_source(
            source_batch,
            event_type=command.event_type,
            actor_kind=command.actor_kind,
            actor_id=command.actor_id,
            expected_case_revision=command.expected_case_revision,
            expected_ledger_head=command.expected_ledger_head,
            command_id=command.command_id,
        )
        return self._context_from_projection(projection)

    def reopen_with_revision(
        self,
        source_batch: Mapping[str, Any],
        *,
        expected_case_revision: int,
        expected_ledger_head: str,
        command_id: str,
    ) -> CaseContextDTO:
        """Preserve the closure and atomically start a new evidence revision."""

        projection = self.workspace.reopen_with_revision(
            source_batch,
            expected_case_revision=expected_case_revision,
            expected_ledger_head=expected_ledger_head,
            command_id=command_id,
        )
        return self._context_from_projection(projection)

    def resume(self, *, recover_partial_tail: bool = False) -> CaseContextDTO:
        projection = self.workspace.resume(recover_partial_tail=recover_partial_tail)
        return self._context_from_projection(projection)

    def freeze(self) -> Mapping[str, Any]:
        return self.workspace.freeze()

    def verify(self) -> list[str]:
        return self.workspace.verify()
