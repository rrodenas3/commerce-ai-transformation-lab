from __future__ import annotations

import unittest

from scripts.recovery_policy import (
    PolicyControlError,
    RecoveryPolicyAdapter,
    decide_policy,
    govern_candidate,
)
from scripts.recovery_recommender import DeterministicFixtureProvider
from scripts.recovery_services import RecommendationCommand, RecoveryApplicationService
from scripts.recovery_state import WorkflowState


def facts(**changes):
    value = {
        "active_chargeback": False,
        "affected_value_cents": 2500,
        "all_sources_fresh": True,
        "authority": {
            "delegated_max_exposure_cents": 2500,
            "finance_review_order_value_cents": 50000,
            "workflow_owner_max_exposure_cents": 10000,
        },
        "available_replacement_quantity": 2,
        "customer_choice": "RESHIP",
        "duplicate_signal": False,
        "has_source_conflict": False,
        "has_unresolved_action": False,
        "order_value_cents": 4000,
        "policy_id": "SCC-01-RECOVERY-POLICY",
        "policy_version": "1.0.0",
        "prior_remedy_covers_quantity": False,
        "reliable_eta_at": "2026-08-12T17:00:00+02:00",
        "remaining_quantity": 2,
        "risk_stop_flags": [],
        "source_freshness": {
            "OMS": True,
            "WMS": True,
            "CARRIER": True,
            "INVENTORY": True,
            "PAYMENT": True,
            "CRM": True,
            "POLICY": True,
        },
    }
    value.update(changes)
    return value


class RecoveryPolicyTests(unittest.TestCase):
    def test_decision_priority_is_fail_closed(self):
        decision = decide_policy(
            facts(
                risk_stop_flags=["active_chargeback"],
                active_chargeback=True,
                prior_remedy_covers_quantity=True,
                has_source_conflict=True,
            )
        )
        self.assertEqual("CONTROL_STOPPED", decision.outcome_code)
        self.assertEqual("SPECIALIST_STOP", decision.authority_route)
        self.assertFalse(decision.consequential_action_permitted)

    def test_prior_remedy_unresolved_conflict_and_duplicate_precede_choice(self):
        matrix = (
            ({"prior_remedy_covers_quantity": True}, "VERIFIED_NO_NEW_ACTION"),
            ({"has_unresolved_action": True}, "ACTION_RECOVERY_REQUIRED"),
            ({"has_source_conflict": True}, "EVIDENCE_BLOCKED"),
            ({"duplicate_signal": True}, "VERIFIED_NO_NEW_ACTION"),
        )
        for changes, expected in matrix:
            with self.subTest(expected=expected):
                self.assertEqual(expected, decide_policy(facts(**changes)).outcome_code)
        duplicate = decide_policy(facts(duplicate_signal=True))
        self.assertEqual(("DUPLICATE_SIGNAL_SUPPRESSED",), duplicate.required_message_facts)
        self.assertNotIn("PRIOR_REMEDY_CONFIRMED", duplicate.required_message_facts)

    def test_wait_requires_current_reliable_eta(self):
        allowed = decide_policy(facts(customer_choice="WAIT"))
        self.assertEqual("WAIT_VERIFIED_ETA", allowed.proposed_action)
        self.assertEqual("DIRECT_NO_ACTION", allowed.authority_route)
        blocked = decide_policy(facts(customer_choice="WAIT", reliable_eta_at=None))
        self.assertEqual("EVIDENCE_BLOCKED", blocked.outcome_code)

    def test_action_specific_source_freshness(self):
        freshness = dict(facts()["source_freshness"])
        freshness["INVENTORY"] = False
        self.assertEqual(
            "EVIDENCE_BLOCKED",
            decide_policy(facts(customer_choice="RESHIP", source_freshness=freshness)).outcome_code,
        )
        self.assertEqual(
            "DELEGATED_ACTION_READY",
            decide_policy(facts(customer_choice="REFUND", source_freshness=freshness)).outcome_code,
        )

    def test_authority_boundaries_are_exact(self):
        cases = (
            (2500, 50000, "DELEGATED_DECISION"),
            (2501, 50000, "WORKFLOW_OWNER_APPROVAL"),
            (10001, 50000, "FINANCE_APPROVAL"),
            (2500, 50001, "FINANCE_APPROVAL"),
        )
        for exposure, order_value, expected in cases:
            with self.subTest(exposure=exposure, order_value=order_value):
                decision = decide_policy(
                    facts(affected_value_cents=exposure, order_value_cents=order_value)
                )
                self.assertEqual(expected, decision.authority_route)

    def test_provider_cannot_change_policy_route_or_unsupported_message_fact(self):
        governed = govern_candidate(
            facts(affected_value_cents=2501),
            {
                "proposed_action": "RESHIP",
                "proposed_route": "DELEGATED_DECISION",
                "message_fact_candidates": ["RESHIP_PROPOSED"],
            },
        )
        self.assertFalse(governed.candidate_accepted)
        self.assertEqual("WORKFLOW_OWNER_APPROVAL", governed.decision.authority_route)
        self.assertIn("PROVIDER_ROUTE_DISAGREES_WITH_POLICY", governed.rejection_codes)
        with self.assertRaises(PolicyControlError):
            govern_candidate(
                facts(),
                {
                    "proposed_action": "RESHIP",
                    "proposed_route": "DELEGATED_DECISION",
                    "message_fact_candidates": ["REFUND_COMPLETED"],
                },
            )

    def test_application_records_provider_proposal_and_separate_governed_outcome(self):
        class Projection:
            run_id = "S2-RUN-TEST"
            case_id = "S2-CASE-0001"
            case_revision = 1
            state = WorkflowState.CONTEXT_READY
            sequence = 3
            ledger_head_digest = "a" * 64
            source_event_cut_sha256 = "b" * 64
            revision_pin_sha256 = "c" * 64
            active_object_ids = {}
            invalidated_object_ids = ()
            frozen = False

        class Workspace:
            def __init__(self):
                self.projection = Projection()
                self.appended = None

            def replay(self):
                return self.projection

            def load_source_batch(self, revision=None):
                return {
                    "payload": {
                        "records": [
                            {
                                "record_id": "S2-SRC-0001-OMS",
                                "payload": {
                                    "effective_at": "2026-08-11T09:00:00Z",
                                    "observed_at": "2026-08-11T09:00:00Z",
                                    "provenance_digest": "e" * 64,
                                    "source_name": "OMS",
                                },
                            }
                        ]
                    }
                }

            def append_transition(self, **kwargs):
                self.appended = kwargs
                self.projection.state = kwargs["target_state"]
                self.projection.sequence += 1
                self.projection.ledger_head_digest = "d" * 64
                return self.projection

        class Facts:
            def derive(self, source_batch):
                return facts()

        workspace = Workspace()
        service = RecoveryApplicationService(workspace, Facts())
        provider = DeterministicFixtureProvider(
            {
                "candidate_id": "S2-CANDIDATE-FIXTURE-0001",
                "case_id": "S2-CASE-0001",
                "case_revision": 1,
                "cited_evidence": ["S2-SRC-0001-OMS"],
                "material_limitations": ["Deterministic CI fixture only."],
                "message_fact_candidates": ["RESHIP_PROPOSED"],
                "proposed_action": "RESHIP",
                "proposed_route": "DELEGATED_DECISION",
                "rejected_alternatives": ["REFUND"],
                "schema_version": "stage2-provider-candidate/v1",
                "uncertainty": "LOW",
            }
        )
        outcome = service.recommend(
            RecommendationCommand(
                recommendation_id="S2-RECOMMENDATION-0001",
                attempt_id="S2-ATTEMPT-FIXTURE-0001",
                expected_case_revision=1,
                expected_ledger_head="a" * 64,
                command_id="S2-CMD-RECOMMEND-0001",
            ),
            provider,
            RecoveryPolicyAdapter(),
        )
        self.assertTrue(outcome.governed_recommendation["candidate_accepted"])
        self.assertEqual("FALLBACK", outcome.provider_terminal_status)
        self.assertTrue(outcome.fallback_used)
        self.assertEqual(WorkflowState.RECOMMENDATION_READY, workspace.appended["target_state"])
        effect = workspace.appended["decision_or_effect"]
        self.assertIn("provider_proposal", effect)
        self.assertIn("governed_recommendation", effect)


if __name__ == "__main__":
    unittest.main()
