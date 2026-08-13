from __future__ import annotations

import unittest
from copy import deepcopy

from scripts.recovery_approval import (
    AuthorityBindingError,
    AuthorityExpectation,
    create_synthetic_authority_event,
    validate_authority_event,
)


NOW = "2026-08-11T10:00:00Z"


def expectation(route="WORKFLOW_OWNER_APPROVAL"):
    return AuthorityExpectation(
        case_id="S2-CASE-0001",
        case_revision=1,
        ledger_head_digest="a" * 64,
        policy_id="SCC-01-RECOVERY-POLICY",
        policy_version="1.0.0",
        operation="REFUND",
        payload_digest="b" * 64,
        authority_route=route,
        recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
    )


class RecoveryApprovalTests(unittest.TestCase):
    def test_exact_workflow_owner_binding_validates(self):
        event = create_synthetic_authority_event(
            expectation(),
            approval_id="S2-APPROVAL-0001",
            issued_by="S2-ACTOR-WORKFLOW-OWNER",
            approver_role="SYNTHETIC_WORKFLOW_OWNER",
            decision="APPROVED",
            rationale_code="POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        capability = validate_authority_event(event, expectation(), now=NOW)
        self.assertEqual("S2-APPROVAL-0001", capability.authority_id)
        self.assertFalse(capability.consumed)

    def test_every_bound_field_mutation_fails(self):
        event = create_synthetic_authority_event(
            expectation(),
            approval_id="S2-APPROVAL-0001",
            issued_by="S2-ACTOR-WORKFLOW-OWNER",
            approver_role="SYNTHETIC_WORKFLOW_OWNER",
            decision="APPROVED",
            rationale_code="POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        mutations = {
            "case_id": "S2-CASE-9999",
            "case_revision": 2,
            "ledger_head_digest": "c" * 64,
            "policy_version": "1.0.1",
            "operation": "RESHIP",
            "payload_digest": "d" * 64,
            "authority_route": "FINANCE_APPROVAL",
            "approver_role": "SYNTHETIC_FINANCE_APPROVER",
        }
        for field, value in mutations.items():
            changed = deepcopy(event)
            changed["payload"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(AuthorityBindingError):
                    validate_authority_event(changed, expectation(), now=NOW)

    def test_wrong_role_rejection_expiry_revocation_replay_and_self_approval_fail(self):
        base_kwargs = dict(
            expectation=expectation(),
            approval_id="S2-APPROVAL-0001",
            issued_by="S2-ACTOR-WORKFLOW-OWNER",
            approver_role="SYNTHETIC_WORKFLOW_OWNER",
            decision="APPROVED",
            rationale_code="POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        invalid_events = []
        wrong = create_synthetic_authority_event(**base_kwargs)
        wrong["payload"]["approver_role"] = "SYNTHETIC_FINANCE_APPROVER"
        invalid_events.append(wrong)
        rejected = create_synthetic_authority_event(**{**base_kwargs, "decision": "REJECTED"})
        invalid_events.append(rejected)
        amended = create_synthetic_authority_event(**{**base_kwargs, "decision": "AMENDED"})
        invalid_events.append(amended)
        expired = create_synthetic_authority_event(**{**base_kwargs, "expires_at": "2026-08-11T09:30:00Z"})
        invalid_events.append(expired)
        revoked = create_synthetic_authority_event(**{**base_kwargs, "revoked_at": "2026-08-11T09:30:00Z"})
        invalid_events.append(revoked)
        consumed = create_synthetic_authority_event(**{**base_kwargs, "consumed_at": "2026-08-11T09:45:00Z"})
        invalid_events.append(consumed)
        provider = create_synthetic_authority_event(
            **{**base_kwargs, "issued_by": "S2-PROVIDER-RECORDED-AI-V1", "issuer_kind": "provider"}
        )
        invalid_events.append(provider)
        for event in invalid_events:
            with self.subTest(event=event["payload"]):
                with self.assertRaises(AuthorityBindingError):
                    validate_authority_event(event, expectation(), now=NOW)

    def test_delegated_decision_is_exact_and_fail_closed(self):
        delegated = expectation("DELEGATED_DECISION")
        event = create_synthetic_authority_event(
            delegated,
            approval_id="S2-DECISION-0001",
            issued_by="S2-ACTOR-RECOVERY-SPECIALIST",
            approver_role="SYNTHETIC_RECOVERY_SPECIALIST",
            decision="APPROVED",
            rationale_code="DELEGATED_POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        self.assertEqual(
            "DELEGATED_DECISION",
            validate_authority_event(event, delegated, now=NOW).authority_route,
        )
        changed = deepcopy(event)
        changed["payload"]["payload_digest"] = "c" * 64
        with self.assertRaises(AuthorityBindingError):
            validate_authority_event(changed, delegated, now=NOW)


if __name__ == "__main__":
    unittest.main()
