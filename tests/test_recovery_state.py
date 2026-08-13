#!/usr/bin/env python3
"""Proof-first tests for the Stage 2 workflow state and application boundary."""

from __future__ import annotations

import json
import copy
import hashlib
import ast
import tempfile
import unittest
from pathlib import Path

from scripts.recovery_services import (
    RecoveryApplicationService,
    RevisionCommand,
    Stage2FactsPort,
    TransitionCommand,
)
from scripts.recovery_state import (
    IllegalTransitionError,
    WorkflowState,
    legal_next_states,
    transition_state,
)
from scripts.recovery_workspace import FileRecoveryWorkspace
from scripts.stage2_facts import derive_case_facts
from scripts.stage2_contracts import canonical_json_bytes
from scripts.stage2_facts import (
    revision_pin_sha256,
    source_event_cut_sha256,
    source_provenance_material,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "stage2" / "development" / "cases.jsonl"


def _case(index: int = 0) -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8").splitlines()[index])


def _revised_case(batch: dict, ledger_head: str) -> dict:
    revised = copy.deepcopy(batch)
    payload = revised["payload"]
    payload["case_revision"] = 2
    payload["ledger_head_digest"] = ledger_head
    revised["record_id"] = f"{revised['record_id']}-R2"
    for record in payload["records"]:
        record["record_id"] = f"{record['record_id']}-R2"
        item = record["payload"]
        item["case_revision"] = 2
        if item["source_name"] == "CRM":
            item["data"]["customer_choice"] = "REFUND"
        item["provenance_digest"] = hashlib.sha256(
            canonical_json_bytes(source_provenance_material(item))
        ).hexdigest()
    payload["source_event_cut_sha256"] = source_event_cut_sha256(payload["records"])
    payload["revision_pin_sha256"] = revision_pin_sha256(
        payload["case_id"],
        2,
        payload["source_event_cut_sha256"],
        ledger_head,
    )
    return revised


class RecoveryStateTests(unittest.TestCase):
    def test_every_declared_state_edge_succeeds(self):
        edges = {
            None: (WorkflowState.RECEIVED,),
            WorkflowState.RECEIVED: (WorkflowState.DEDUPLICATED,),
            WorkflowState.DEDUPLICATED: (WorkflowState.EVIDENCE_BLOCKED, WorkflowState.CONTEXT_READY),
            WorkflowState.EVIDENCE_BLOCKED: (WorkflowState.CONTEXT_READY,),
            WorkflowState.CONTEXT_READY: (WorkflowState.RECOMMENDATION_READY,),
            WorkflowState.RECOMMENDATION_READY: (
                WorkflowState.CONTROL_STOPPED,
                WorkflowState.AWAITING_CHOICE,
                WorkflowState.AWAITING_APPROVAL,
                WorkflowState.ACTION_PREPARED,
                WorkflowState.COMMUNICATION_READY,
            ),
            WorkflowState.AWAITING_CHOICE: (WorkflowState.RECOMMENDATION_READY, WorkflowState.CONTROL_STOPPED),
            WorkflowState.AWAITING_APPROVAL: (WorkflowState.ACTION_PREPARED, WorkflowState.CONTROL_STOPPED),
            WorkflowState.ACTION_PREPARED: (WorkflowState.ACTION_RESERVED,),
            WorkflowState.ACTION_RESERVED: (WorkflowState.ACTION_PENDING,),
            WorkflowState.ACTION_PENDING: (WorkflowState.ACTION_RECOVERY, WorkflowState.VERIFYING),
            WorkflowState.ACTION_RECOVERY: (WorkflowState.VERIFYING,),
            WorkflowState.VERIFYING: (WorkflowState.VERIFICATION_FAILED, WorkflowState.VERIFIED_REMEDY),
            WorkflowState.VERIFICATION_FAILED: (WorkflowState.ACTION_RECOVERY,),
            WorkflowState.VERIFIED_REMEDY: (WorkflowState.COMMUNICATION_READY,),
            WorkflowState.COMMUNICATION_READY: (WorkflowState.CLOSED,),
            WorkflowState.CLOSED: (),
            WorkflowState.REOPENED: (),
            WorkflowState.CONTROL_STOPPED: (),
        }
        for source, targets in edges.items():
            self.assertEqual(targets, legal_next_states(source))
            for target in targets:
                event_type = "VERIFIED_NO_NEW_ACTION" if (
                    source is WorkflowState.RECOMMENDATION_READY
                    and target is WorkflowState.COMMUNICATION_READY
                ) else "STATE_ADVANCED"
                with self.subTest(source=source, target=target):
                    self.assertEqual(target, transition_state(source, target, event_type=event_type))

    def test_application_core_has_only_inward_runtime_dependencies(self):
        module = ast.parse(
            (PROJECT_ROOT / "scripts" / "recovery_services.py").read_text(encoding="utf-8")
        )
        imports = []
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        forbidden = (
            "run_recovery_lab",
            "recovery_workspace",
            "recovery_adapter",
            "recovery_recommender",
            "stage2_case_system",
            "oracle",
            "evaluator",
            "release",
        )
        self.assertFalse([name for name in imports if any(token in name for token in forbidden)])

    def test_declared_happy_path_and_recovery_path_are_legal(self):
        path = [
            WorkflowState.RECEIVED,
            WorkflowState.DEDUPLICATED,
            WorkflowState.CONTEXT_READY,
            WorkflowState.RECOMMENDATION_READY,
            WorkflowState.AWAITING_APPROVAL,
            WorkflowState.ACTION_PREPARED,
            WorkflowState.ACTION_RESERVED,
            WorkflowState.ACTION_PENDING,
            WorkflowState.ACTION_RECOVERY,
            WorkflowState.VERIFYING,
            WorkflowState.VERIFICATION_FAILED,
            WorkflowState.ACTION_RECOVERY,
            WorkflowState.VERIFYING,
            WorkflowState.VERIFIED_REMEDY,
            WorkflowState.COMMUNICATION_READY,
            WorkflowState.CLOSED,
        ]
        current = None
        for target in path:
            current = transition_state(current, target, event_type="STATE_ADVANCED")
        self.assertEqual(WorkflowState.CLOSED, current)

    def test_skipping_authority_action_verification_message_or_closure_fails(self):
        illegal = [
            (WorkflowState.RECOMMENDATION_READY, WorkflowState.ACTION_PENDING),
            (WorkflowState.ACTION_PREPARED, WorkflowState.VERIFYING),
            (WorkflowState.ACTION_PENDING, WorkflowState.VERIFIED_REMEDY),
            (WorkflowState.VERIFIED_REMEDY, WorkflowState.CLOSED),
            (WorkflowState.RECOMMENDATION_READY, WorkflowState.CLOSED),
        ]
        for source, target in illegal:
            with self.subTest(source=source, target=target):
                with self.assertRaises(IllegalTransitionError):
                    transition_state(source, target, event_type="STATE_ADVANCED")

    def test_verified_wait_and_no_new_action_have_direct_zero_action_path(self):
        for event_type in ("VERIFIED_WAIT_CONDITION", "VERIFIED_NO_NEW_ACTION"):
            with self.subTest(event_type=event_type):
                state = transition_state(
                    WorkflowState.RECOMMENDATION_READY,
                    WorkflowState.COMMUNICATION_READY,
                    event_type=event_type,
                    action_count=0,
                )
                self.assertEqual(WorkflowState.COMMUNICATION_READY, state)
                with self.assertRaises(IllegalTransitionError):
                    transition_state(
                        WorkflowState.RECOMMENDATION_READY,
                        WorkflowState.COMMUNICATION_READY,
                        event_type=event_type,
                        action_count=1,
                    )

    def test_direct_communication_rejects_unverified_label(self):
        with self.assertRaises(IllegalTransitionError):
            transition_state(
                WorkflowState.RECOMMENDATION_READY,
                WorkflowState.COMMUNICATION_READY,
                event_type="STATE_ADVANCED",
                action_count=0,
            )

    def test_terminal_and_reopen_semantics_are_explicit(self):
        self.assertEqual((), legal_next_states(WorkflowState.CONTROL_STOPPED))
        self.assertEqual((), legal_next_states(WorkflowState.CLOSED))
        with self.assertRaises(IllegalTransitionError):
            transition_state(
                WorkflowState.CLOSED,
                WorkflowState.REOPENED,
                event_type="CASE_REOPENED",
            )

    def test_expired_approval_and_synthetic_dispute_route_fail_closed(self):
        stopped = transition_state(
            WorkflowState.AWAITING_APPROVAL,
            WorkflowState.CONTROL_STOPPED,
            event_type="APPROVAL_EXPIRED",
        )
        self.assertEqual(WorkflowState.CONTROL_STOPPED, stopped)
        with self.assertRaises(IllegalTransitionError):
            transition_state(
                WorkflowState.CLOSED,
                WorkflowState.REOPENED,
                event_type="SYNTHETIC_DISPUTE_RECEIVED",
            )


class _Facts(Stage2FactsPort):
    def derive(self, source_batch):
        return derive_case_facts(source_batch)


class RecoveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recovery-state-")
        self.addCleanup(self.temporary.cleanup)
        self.runs = Path(self.temporary.name) / "runs"
        self.workspace = FileRecoveryWorkspace.prepare(
            self.runs, "S2-RUN-STATE-01", _case()
        )
        self.service = RecoveryApplicationService(self.workspace, _Facts())

    def _advance(self, target: WorkflowState, *, event_type="STATE_ADVANCED", links=None):
        before = self.workspace.replay()
        if target in {
            WorkflowState.DEDUPLICATED,
            WorkflowState.EVIDENCE_BLOCKED,
            WorkflowState.CONTEXT_READY,
        }:
            return self.service.advance(
                TransitionCommand(
                    target_state=target,
                    event_type=event_type,
                    actor_kind="caller_ignored",
                    actor_id="S2-ACTOR-CALLER-IGNORED",
                    expected_case_revision=before.case_revision,
                    expected_ledger_head=before.ledger_head_digest,
                    command_id=f"S2-CMD-{before.sequence + 1:04d}",
                    links=links or {},
                )
            )
        return self.workspace.append_transition(
            target_state=target,
            event_type=event_type,
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id=f"S2-CMD-{before.sequence + 1:04d}",
            links=links or {},
        )

    def test_cli_and_provider_queries_share_one_context_without_evaluator_fields(self):
        operator = self.service.inspect()
        provider = self.service.provider_context()
        self.assertEqual(operator.to_dict(), provider.to_dict())
        encoded = json.dumps(provider.to_dict(), sort_keys=True)
        for forbidden in (
            "expected_action",
            "expected_route",
            "evaluation_family",
            "oracle",
            "score",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(1, provider.case_revision)
        self.assertTrue(provider.permitted_facts)
        self.assertTrue(provider.policy_authority_projection)

    def test_illegal_service_transition_has_no_ledger_mutation(self):
        before = self.service.inspect()
        with self.assertRaises(ValueError):
            self.service.advance(
                TransitionCommand(
                    target_state=WorkflowState.CLOSED,
                    event_type="CALLER_FORGED",
                    actor_kind="caller",
                    actor_id="S2-ACTOR-CALLER",
                    expected_case_revision=before.case_revision,
                    expected_ledger_head=before.ledger_head_digest,
                    command_id="S2-CMD-PROTECTED-CLOSED",
                )
            )
        after = self.service.inspect()
        self.assertEqual(before, after)

    def test_material_revision_invalidates_bound_objects_and_stale_commands(self):
        self._advance(WorkflowState.DEDUPLICATED)
        self._advance(WorkflowState.CONTEXT_READY)
        self._advance(WorkflowState.RECOMMENDATION_READY)
        self._advance(
            WorkflowState.AWAITING_APPROVAL,
            links={"recommendation_id": "S2-REC-0001"},
        )
        before = self.service.inspect()
        revised = self.service.revise_source(
            _revised_case(_case(), before.ledger_head_digest),
            RevisionCommand(
                event_type="SOURCE_REVISION_CHANGED",
                actor_kind="source",
                actor_id="S2-ACTOR-SOURCE",
                expected_case_revision=before.case_revision,
                expected_ledger_head=before.ledger_head_digest,
                command_id="S2-CMD-REVISION-0001",
            ),
        )
        self.assertEqual(2, revised.case_revision)
        self.assertEqual(WorkflowState.EVIDENCE_BLOCKED, revised.state)
        self.assertIn("S2-REC-0001", revised.invalidated_object_ids)
        with self.assertRaisesRegex(Exception, "stale"):
            self.workspace.append_transition(
                target_state=WorkflowState.ACTION_PREPARED,
                event_type="TEST_STALE_SETUP",
                actor_kind="test_fixture",
                actor_id="S2-ACTOR-TEST-FIXTURE",
                expected_case_revision=before.case_revision,
                expected_ledger_head=before.ledger_head_digest,
                command_id="S2-CMD-STALE-0001",
            )


if __name__ == "__main__":
    unittest.main()
