#!/usr/bin/env python3
"""Pure legal-state semantics for the Stage 2 recovery workflow.

The module is deliberately filesystem, provider, policy, adapter, and evaluator
free.  Durable history is the authority; callers may request a transition but
cannot bypass the declared predecessor or the direct no-action guards.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class IllegalTransitionError(ValueError):
    """Raised before durable state changes when a transition is not legal."""


class WorkflowState(StrEnum):
    RECEIVED = "RECEIVED"
    DEDUPLICATED = "DEDUPLICATED"
    EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
    CONTEXT_READY = "CONTEXT_READY"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    CONTROL_STOPPED = "CONTROL_STOPPED"
    AWAITING_CHOICE = "AWAITING_CHOICE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ACTION_PREPARED = "ACTION_PREPARED"
    ACTION_RESERVED = "ACTION_RESERVED"
    ACTION_PENDING = "ACTION_PENDING"
    ACTION_RECOVERY = "ACTION_RECOVERY"
    VERIFYING = "VERIFYING"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFIED_REMEDY = "VERIFIED_REMEDY"
    COMMUNICATION_READY = "COMMUNICATION_READY"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


_LEGAL: Mapping[WorkflowState | None, tuple[WorkflowState, ...]] = {
    None: (WorkflowState.RECEIVED,),
    WorkflowState.RECEIVED: (WorkflowState.DEDUPLICATED,),
    WorkflowState.DEDUPLICATED: (
        WorkflowState.EVIDENCE_BLOCKED,
        WorkflowState.CONTEXT_READY,
    ),
    WorkflowState.EVIDENCE_BLOCKED: (WorkflowState.CONTEXT_READY,),
    WorkflowState.CONTEXT_READY: (WorkflowState.RECOMMENDATION_READY,),
    WorkflowState.RECOMMENDATION_READY: (
        WorkflowState.CONTROL_STOPPED,
        WorkflowState.AWAITING_CHOICE,
        WorkflowState.AWAITING_APPROVAL,
        WorkflowState.ACTION_PREPARED,
        WorkflowState.COMMUNICATION_READY,
    ),
    WorkflowState.AWAITING_CHOICE: (
        WorkflowState.RECOMMENDATION_READY,
        WorkflowState.CONTROL_STOPPED,
    ),
    WorkflowState.AWAITING_APPROVAL: (
        WorkflowState.ACTION_PREPARED,
        WorkflowState.CONTROL_STOPPED,
    ),
    WorkflowState.ACTION_PREPARED: (WorkflowState.ACTION_RESERVED,),
    WorkflowState.ACTION_RESERVED: (WorkflowState.ACTION_PENDING,),
    WorkflowState.ACTION_PENDING: (
        WorkflowState.ACTION_RECOVERY,
        WorkflowState.VERIFYING,
    ),
    WorkflowState.ACTION_RECOVERY: (WorkflowState.VERIFYING,),
    WorkflowState.VERIFYING: (
        WorkflowState.VERIFICATION_FAILED,
        WorkflowState.VERIFIED_REMEDY,
    ),
    WorkflowState.VERIFICATION_FAILED: (WorkflowState.ACTION_RECOVERY,),
    WorkflowState.VERIFIED_REMEDY: (WorkflowState.COMMUNICATION_READY,),
    WorkflowState.COMMUNICATION_READY: (WorkflowState.CLOSED,),
    # Reopen is deliberately not walkable as two generic state changes.  The
    # guarded workspace commits closure-preserving source revision and reopen
    # as one CASE_REOPENED_WITH_SOURCE_REVISION event.
    WorkflowState.CLOSED: (),
    WorkflowState.REOPENED: (),
    WorkflowState.CONTROL_STOPPED: (),
}

DIRECT_NO_ACTION_EVENTS = frozenset(
    {"VERIFIED_WAIT_CONDITION", "VERIFIED_NO_NEW_ACTION"}
)
NON_TRANSITION_EVENT_TYPES = frozenset(
    {
        "COMMUNICATION_RECORDED",
        "PARTIAL_TAIL_RECOVERED",
        "UNCOMMITTED_EVENT_RECOVERED",
        "WORKSPACE_CHECKPOINTED",
    }
)
REVISION_INVALIDATABLE_STATES = frozenset(
    state
    for state in WorkflowState
    if state
    not in {
        WorkflowState.RECEIVED,
        WorkflowState.CONTROL_STOPPED,
        WorkflowState.CLOSED,
        WorkflowState.REOPENED,
    }
)


def coerce_state(value: WorkflowState | str | None) -> WorkflowState | None:
    if value is None or isinstance(value, WorkflowState):
        return value
    try:
        return WorkflowState(value)
    except (TypeError, ValueError) as error:
        raise IllegalTransitionError("unknown workflow state") from error


def legal_next_states(state: WorkflowState | str | None) -> tuple[WorkflowState, ...]:
    """Return the declared state-machine successors, without inferred shortcuts."""

    current = coerce_state(state)
    return _LEGAL[current]


def transition_state(
    current: WorkflowState | str | None,
    target: WorkflowState | str,
    *,
    event_type: str,
    action_count: int = 0,
) -> WorkflowState:
    """Validate and return a requested transition without mutating anything."""

    source = coerce_state(current)
    destination = coerce_state(target)
    assert destination is not None
    if destination not in _LEGAL[source]:
        raise IllegalTransitionError(
            f"illegal workflow transition {source or 'START'} -> {destination}"
        )
    if (
        source is WorkflowState.RECOMMENDATION_READY
        and destination is WorkflowState.COMMUNICATION_READY
    ):
        if event_type not in DIRECT_NO_ACTION_EVENTS or action_count != 0:
            raise IllegalTransitionError(
                "direct communication requires a verified wait/no-new-action condition "
                "and zero consequential actions"
            )
    return destination


def revision_target(current: WorkflowState | str) -> WorkflowState:
    """Return the fail-closed state for a material source/choice/policy revision."""

    state = coerce_state(current)
    assert state is not None
    if state not in REVISION_INVALIDATABLE_STATES:
        raise IllegalTransitionError(
            f"material revision cannot directly invalidate terminal state {state}"
        )
    return WorkflowState.EVIDENCE_BLOCKED


def apply_event_state(
    current: WorkflowState | None,
    event_payload: Mapping[str, Any],
) -> WorkflowState:
    """Project state from a validated durable event payload."""

    event_type = event_payload["event_type"]
    target = coerce_state(event_payload["to_state"])
    assert target is not None
    if event_type in NON_TRANSITION_EVENT_TYPES:
        if current is None or target is not current:
            raise IllegalTransitionError("recovery/cache events cannot change workflow state")
        return current
    if event_type == "SOURCE_REVISION_CHANGED":
        expected = revision_target(current)  # type: ignore[arg-type]
        if target is not expected:
            raise IllegalTransitionError("material revision must enter EVIDENCE_BLOCKED")
        return target
    if event_type == "CASE_REOPENED_WITH_SOURCE_REVISION":
        if current is not WorkflowState.CLOSED or target is not WorkflowState.EVIDENCE_BLOCKED:
            raise IllegalTransitionError(
                "reopen requires one closure-preserving source revision event"
            )
        return target
    return transition_state(
        current,
        target,
        event_type=event_type,
        action_count=event_payload.get("action_count", 0),
    )


def next_action_labels(state: WorkflowState | str | None) -> tuple[str, ...]:
    """Expose stable operator/provider transition vocabulary from durable state."""

    labels = [target.value for target in legal_next_states(state)]
    current = coerce_state(state)
    if current is WorkflowState.RECOMMENDATION_READY:
        labels.extend(sorted(DIRECT_NO_ACTION_EVENTS))
    return tuple(labels)
