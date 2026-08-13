#!/usr/bin/env python3
"""Adversarial regressions for the guarded U5 integrity boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.recovery_actions import build_action_contract, reserve_action_atomically
from scripts.recovery_adapters import AdapterControlError, LocalSimulatedActionAdapter
from scripts.recovery_approval import AuthorityExpectation, create_synthetic_authority_event
from scripts.recovery_services import RecoveryApplicationService, Stage2FactsPort
from scripts.recovery_orchestration import GuardedRecoveryOrchestrator
from scripts.recovery_policy import RecoveryPolicyAdapter
from scripts.recovery_state import WorkflowState
from scripts.recovery_workspace import FileRecoveryWorkspace, WorkspaceIntegrityError
from scripts.stage2_contracts import canonical_json_bytes
from scripts.stage2_facts import (
    derive_case_facts,
    revision_pin_sha256,
    source_event_cut_sha256,
    source_provenance_material,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES = PROJECT_ROOT / "data" / "stage2" / "development" / "cases.jsonl"
CLI = PROJECT_ROOT / "scripts" / "run_recovery_lab.py"


class _Facts(Stage2FactsPort):
    def derive(self, source_batch):
        return derive_case_facts(source_batch)


def _case(case_id: str = "S2-CASE-0003") -> dict:
    for line in CASES.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["payload"]["case_id"] == case_id:
            return value
    raise AssertionError(case_id)


def _revised_case(batch: dict, ledger_head: str) -> dict:
    revised = copy.deepcopy(batch)
    payload = revised["payload"]
    payload["case_revision"] += 1
    payload["ledger_head_digest"] = ledger_head
    revised["record_id"] = f"{revised['record_id']}-R{payload['case_revision']}"
    for record in payload["records"]:
        record["record_id"] = f"{record['record_id']}-R{payload['case_revision']}"
        item = record["payload"]
        item["case_revision"] = payload["case_revision"]
        item["provenance_digest"] = hashlib.sha256(
            canonical_json_bytes(source_provenance_material(item))
        ).hexdigest()
    payload["source_event_cut_sha256"] = source_event_cut_sha256(payload["records"])
    payload["revision_pin_sha256"] = revision_pin_sha256(
        payload["case_id"],
        payload["case_revision"],
        payload["source_event_cut_sha256"],
        ledger_head,
    )
    return revised


class RecoveryIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recovery-integrity-")
        self.addCleanup(self.temporary.cleanup)
        self.runs = Path(self.temporary.name) / "runs"
        self.workspace = FileRecoveryWorkspace.prepare(
            self.runs, "S2-RUN-INTEGRITY-01", _case()
        )
        self.service = RecoveryApplicationService(self.workspace, _Facts())

    def _raw_advance(self, target: WorkflowState, event_type: str = "TEST_SETUP"):
        before = self.workspace.replay()
        return self.workspace.append_transition(
            target_state=target,
            event_type=event_type,
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id=f"S2-CMD-INTEGRITY-SETUP-{before.sequence + 1:04d}",
        )

    def _prepare_action(self):
        for target in (
            WorkflowState.DEDUPLICATED,
            WorkflowState.CONTEXT_READY,
            WorkflowState.RECOMMENDATION_READY,
            WorkflowState.AWAITING_APPROVAL,
            WorkflowState.ACTION_PREPARED,
        ):
            self._raw_advance(target)
        current = self.workspace.replay()
        action = build_action_contract(
            action_id="S2-ACTION-INTEGRITY-0001",
            case_id=current.case_id,
            case_revision=current.case_revision,
            ledger_head_digest=current.ledger_head_digest,
            policy_id="SCC-01-RECOVERY-POLICY",
            policy_version="1.0.0",
            operation="RESHIP",
            target="S2-ORDER-0003",
            eligible_business_key="S2-LINE-0003-1",
            eligible_quantity=1,
            amount_cents=1200,
            currency="EUR",
            before_state={"available_replacement_quantity": 2, "remaining_quantity": 1},
            authority_route="DELEGATED_DECISION",
            authority_reference="S2-DECISION-INTEGRITY-0001",
            idempotency_key="S2-IDEMPOTENCY-INTEGRITY-0001",
            timeout_milliseconds=5000,
        )
        payload = action["payload"]
        expectation = AuthorityExpectation(
            case_id=payload["case_id"],
            case_revision=payload["case_revision"],
            ledger_head_digest=payload["ledger_head_digest"],
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            operation=payload["operation"],
            payload_digest=payload["action_payload_digest"],
            authority_route=payload["authority_route"],
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
        )
        authority = create_synthetic_authority_event(
            expectation,
            approval_id="S2-DECISION-INTEGRITY-0001",
            issued_by="S2-ACTOR-RECOVERY-SPECIALIST",
            approver_role="SYNTHETIC_RECOVERY_SPECIALIST",
            decision="APPROVED",
            rationale_code="DELEGATED_POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        self.workspace.authority.write_once(
            f"actions/{action['record_id']}.json", canonical_json_bytes(action)
        )
        self.workspace.authority.write_once(
            f"approvals/{authority['record_id']}.json", canonical_json_bytes(authority)
        )
        return action, authority

    def _reserve(self):
        action, authority = self._prepare_action()
        reserve_action_atomically(
            self.workspace,
            action,
            authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now="2026-08-11T10:00:00Z",
            command_id="S2-CMD-INTEGRITY-RESERVE-0001",
        )
        return action, authority

    def test_public_cli_advance_rejects_protected_state_and_caller_metadata(self):
        before = self.workspace.replay()
        protected = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "advance",
                "--runs-root",
                str(self.runs),
                "--run-id",
                before.run_id,
                "--to-state",
                "DEDUPLICATED",
                "--event-type",
                "CALLER_FORGED_EVENT",
                "--actor-kind",
                "caller",
                "--actor-id",
                "S2-ACTOR-CALLER",
                "--expected-revision",
                str(before.case_revision),
                "--expected-head",
                before.ledger_head_digest,
                "--command-id",
                "S2-CMD-INTEGRITY-CLI-0001",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, protected.returncode)
        self.assertEqual(WorkflowState.RECEIVED, self.workspace.replay().state)
        protected_state = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "advance",
                "--runs-root",
                str(self.runs),
                "--run-id",
                before.run_id,
                "--to-state",
                "RECOMMENDATION_READY",
                "--expected-revision",
                str(before.case_revision),
                "--expected-head",
                before.ledger_head_digest,
                "--command-id",
                "S2-CMD-INTEGRITY-CLI-PROTECTED",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, protected_state.returncode)
        self.assertEqual(WorkflowState.RECEIVED, self.workspace.replay().state)

    def test_public_service_exposes_no_caller_supplied_effect_or_success_record_surface(self):
        for name in ("execute_action", "record_verification", "record_communication"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.service, name))

    def test_workflow_complete_unmarked_final_event_is_quarantined_and_retryable(self):
        action, authority = self._prepare_action()
        original = self.workspace._write_commit_marker

        def crash_before_marker(event):
            raise OSError("fault injected before workflow marker")

        self.workspace._write_commit_marker = crash_before_marker
        with self.assertRaises(OSError):
            reserve_action_atomically(
                self.workspace,
                action,
                authority,
                recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                now="2026-08-11T10:00:00Z",
                command_id="S2-CMD-INTEGRITY-RESERVE-CRASH",
            )
        self.workspace._write_commit_marker = original
        recovered = self.workspace.resume(recover_partial_tail=True)
        self.assertEqual(WorkflowState.ACTION_PREPARED, recovered.state)
        self.assertEqual("UNCOMMITTED_EVENT_RECOVERED", self.workspace.read_events()[-1]["payload"]["event_type"])
        retried = reserve_action_atomically(
            self.workspace,
            action,
            authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now="2026-08-11T10:00:00Z",
            command_id="S2-CMD-INTEGRITY-RESERVE-RETRY",
        )
        self.assertEqual(WorkflowState.ACTION_RESERVED, retried.state)

    def test_workflow_event_committed_before_checkpoint_crash_remains_committed(self):
        action, authority = self._prepare_action()
        original = self.workspace._write_checkpoint
        self.workspace._write_checkpoint = lambda projection: (_ for _ in ()).throw(
            OSError("fault injected after workflow marker")
        )
        with self.assertRaises(OSError):
            reserve_action_atomically(
                self.workspace,
                action,
                authority,
                recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                now="2026-08-11T10:00:00Z",
                command_id="S2-CMD-INTEGRITY-RESERVE-AFTER-MARKER",
            )
        self.workspace._write_checkpoint = original
        self.assertEqual(WorkflowState.ACTION_RESERVED, self.workspace.replay().state)
        self.assertEqual(1, len([e for e in self.workspace.read_events() if e["payload"]["event_type"] == "ACTION_AUTHORITY_RESERVED"]))

    def test_workflow_torn_reservation_event_is_quarantined_and_retryable(self):
        action, authority = self._prepare_action()
        original = self.workspace.authority.append_durable

        def tear_reservation(relative, payload):
            if b'ACTION_AUTHORITY_RESERVED' in payload:
                original(relative, payload[: len(payload) // 2])
                raise OSError("fault injected during workflow append")
            return original(relative, payload)

        self.workspace.authority.append_durable = tear_reservation
        with self.assertRaises(OSError):
            reserve_action_atomically(
                self.workspace,
                action,
                authority,
                recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                now="2026-08-11T10:00:00Z",
                command_id="S2-CMD-INTEGRITY-RESERVE-TORN",
            )
        self.workspace.authority.append_durable = original
        recovered = self.workspace.resume(recover_partial_tail=True)
        self.assertEqual("PARTIAL_TAIL_RECOVERED", self.workspace.read_events()[-1]["payload"]["event_type"])
        self.assertEqual(WorkflowState.ACTION_PREPARED, recovered.state)
        retried = reserve_action_atomically(
            self.workspace,
            action,
            authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now="2026-08-11T10:00:00Z",
            command_id="S2-CMD-INTEGRITY-RESERVE-TORN-RETRY",
        )
        self.assertEqual(WorkflowState.ACTION_RESERVED, retried.state)

    def test_complete_unmarked_effect_is_quarantined_before_retry_without_duplicate(self):
        action, _ = self._reserve()
        adapter = LocalSimulatedActionAdapter(self.workspace)
        effect = adapter._effect(action, "OMS")
        self.workspace.authority.append_durable(
            "source-effects/OMS.jsonl", canonical_json_bytes(effect)
        )
        adapter.execute(action)
        self.assertEqual(1, len(adapter.committed_effects("OMS")))
        quarantined = list((self.workspace.run_root / "quarantine").glob("oms-effect-tail-*.bin"))
        self.assertEqual(1, len(quarantined))

    def test_complete_unmarked_attempt_is_quarantined_before_retry(self):
        action, _ = self._reserve()
        adapter = LocalSimulatedActionAdapter(self.workspace)
        original = self.workspace.authority.write_once
        failed = False

        def fail_first_attempt_marker(relative, payload):
            nonlocal failed
            if str(relative).replace("\\", "/").startswith("action-attempts/commits/") and not failed:
                failed = True
                raise WorkspaceIntegrityError("fault injected before attempt marker")
            return original(relative, payload)

        self.workspace.authority.write_once = fail_first_attempt_marker
        with self.assertRaises(WorkspaceIntegrityError):
            adapter.execute(action)
        self.workspace.authority.write_once = original
        receipt = adapter.execute(action)
        self.assertEqual("SIMULATED_EFFECT_COMMITTED", receipt["payload"]["status"])
        self.assertTrue(list((self.workspace.run_root / "quarantine").glob("action-attempt-tail-*.bin")))

    def test_every_effect_source_recovers_before_marker_and_torn_suffix_but_preserves_marker_commit(self):
        action, _ = self._reserve()
        adapter = LocalSimulatedActionAdapter(self.workspace)
        for source in ("PAYMENT", "OMS", "INVENTORY", "WMS"):
            with self.subTest(source=source):
                effect = adapter._effect(action, source)
                relative = f"source-effects/{source}.jsonl"

                # Crash before marker: a complete LF entry is not authoritative.
                self.workspace.authority.append_durable(
                    relative, canonical_json_bytes(effect)
                )
                self.assertEqual([], adapter.committed_effects(source))
                self.assertRegex(
                    adapter.recover_source_effect_tail(source) or "", r"^[0-9a-f]{64}$"
                )
                self.assertEqual([], adapter.committed_effects(source))

                # After marker: the exact sequence-bound effect survives recovery scans.
                adapter._append_effect(effect)
                committed = adapter.committed_effects(source)
                self.assertEqual(1, len(committed))
                self.assertEqual(1, committed[0]["effect_sequence"])

                # Torn suffix: quarantine only the suffix and preserve the prefix.
                self.workspace.authority.append_durable(relative, b'{"torn":')
                self.assertRegex(
                    adapter.recover_source_effect_tail(source) or "", r"^[0-9a-f]{64}$"
                )
                self.assertEqual(committed, adapter.committed_effects(source))

                # One action-ID marker cannot validate a duplicated line.
                self.workspace.authority.append_durable(
                    relative, canonical_json_bytes(committed[0])
                )
                with self.assertRaises(AdapterControlError):
                    adapter.committed_effects(source)

    def test_attempt_marker_commit_survives_torn_suffix_recovery(self):
        action, _ = self._reserve()
        adapter = LocalSimulatedActionAdapter(self.workspace)
        receipt = adapter.execute(action)
        committed_attempts = adapter._attempts()
        self.assertGreaterEqual(len(committed_attempts), 2)
        self.assertEqual(
            list(range(1, len(committed_attempts) + 1)),
            [item["attempt_sequence"] for item in committed_attempts],
        )
        self.workspace.authority.append_durable(
            "action-attempts/journal.jsonl", b'{"torn":'
        )
        self.assertEqual(receipt, adapter.execute(action))
        self.assertEqual(committed_attempts, adapter._attempts())
        self.assertTrue(
            list((self.workspace.run_root / "quarantine").glob("action-attempt-tail-*.bin"))
        )

    def test_guarded_reopen_preserves_closure_and_creates_new_revision_atomically(self):
        for target in (
            WorkflowState.DEDUPLICATED,
            WorkflowState.CONTEXT_READY,
            WorkflowState.RECOMMENDATION_READY,
            WorkflowState.COMMUNICATION_READY,
        ):
            event_type = "VERIFIED_NO_NEW_ACTION" if target is WorkflowState.COMMUNICATION_READY else "TEST_SETUP"
            self._raw_advance(target, event_type)
        communication_ready = self.workspace.replay()
        self.workspace.append_transition(
            target_state=WorkflowState.CLOSED,
            event_type="TEST_CLOSURE_SETUP",
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=communication_ready.case_revision,
            expected_ledger_head=communication_ready.ledger_head_digest,
            command_id="S2-CMD-INTEGRITY-SETUP-CLOSURE",
            links={"closure_id": "S2-CLOSURE-INTEGRITY-PREVIOUS-0001"},
        )
        before = self.workspace.replay()
        self.assertTrue(hasattr(self.service, "reopen_with_revision"))
        reopened = self.service.reopen_with_revision(
            _revised_case(_case(), before.ledger_head_digest),
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id="S2-CMD-INTEGRITY-REOPEN-0001",
        )
        self.assertEqual(2, reopened.case_revision)
        self.assertEqual(WorkflowState.EVIDENCE_BLOCKED, reopened.state)
        self.assertIn(
            "S2-CLOSURE-INTEGRITY-PREVIOUS-0001", reopened.invalidated_object_ids
        )
        self.assertEqual("CLOSED", self.workspace.read_events()[-2]["payload"]["to_state"])
        reopen_event = self.workspace.read_events()[-1]["payload"]
        self.assertEqual("CASE_REOPENED_WITH_SOURCE_REVISION", reopen_event["event_type"])
        self.assertEqual(
            "S2-CLOSURE-INTEGRITY-PREVIOUS-0001",
            reopen_event["decision_or_effect"]["preserved_closure_id"],
        )

    def test_direct_wait_creates_linked_verification_and_communication_with_zero_action(self):
        wait_workspace = FileRecoveryWorkspace.prepare(
            self.runs, "S2-RUN-INTEGRITY-WAIT-01", _case("S2-CASE-0001")
        )
        wait_service = RecoveryApplicationService(wait_workspace, _Facts())
        for target in (WorkflowState.DEDUPLICATED, WorkflowState.CONTEXT_READY):
            before = wait_service.inspect()
            from scripts.recovery_services import TransitionCommand

            wait_service.advance(
                TransitionCommand(
                    target_state=target,
                    event_type="CALLER_IGNORED",
                    actor_kind="caller_ignored",
                    actor_id="S2-ACTOR-CALLER-IGNORED",
                    expected_case_revision=before.case_revision,
                    expected_ledger_head=before.ledger_head_digest,
                    command_id=f"S2-CMD-INTEGRITY-WAIT-{before.sequence + 1:04d}",
                )
            )
        context = wait_service.inspect()
        decision = RecoveryPolicyAdapter().decide(context).to_dict()
        wait_workspace.append_transition(
            target_state=WorkflowState.RECOMMENDATION_READY,
            event_type="RECOMMENDATION_RECORDED",
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=context.case_revision,
            expected_ledger_head=context.ledger_head_digest,
            command_id="S2-CMD-INTEGRITY-WAIT-RECOMMEND",
            links={"recommendation_id": "S2-RECOMMENDATION-INTEGRITY-WAIT-0001"},
            decision_or_effect={"governed_recommendation": {"decision": decision}},
            action_count=0,
        )
        orchestrator = GuardedRecoveryOrchestrator(wait_workspace, _Facts())
        verification = orchestrator.verify_direct_no_action(
            verification_id="S2-VERIFICATION-INTEGRITY-WAIT-0001",
            command_id="S2-CMD-INTEGRITY-WAIT-VERIFY",
        )
        communication = orchestrator.communicate_active_verification(
            communication_id="S2-COMMUNICATION-INTEGRITY-WAIT-0001",
            command_id="S2-CMD-INTEGRITY-WAIT-COMMUNICATE",
        )
        _, final = orchestrator.close(
            closure_id="S2-CLOSURE-INTEGRITY-WAIT-0001",
            command_id="S2-CMD-INTEGRITY-WAIT-CLOSE",
        )
        self.assertEqual("VERIFIED_WAIT_CONDITION", verification["payload"]["classification"])
        self.assertEqual([verification["record_id"]], communication["payload"]["citations"])
        self.assertEqual(WorkflowState.CLOSED, final.state)
        self.assertEqual(b"", wait_workspace.authority.read_bytes("action-attempts/journal.jsonl"))
        for source in ("PAYMENT", "OMS", "INVENTORY", "WMS"):
            self.assertEqual(b"", wait_workspace.authority.read_bytes(f"source-effects/{source}.jsonl"))
        consequential_events = wait_workspace.read_events()[-3:]
        self.assertEqual([0, 0, 0], [event["payload"]["action_count"] for event in consequential_events])

    def test_direct_prior_remedy_creates_linked_no_new_action_evidence_with_zero_action(self):
        workspace = FileRecoveryWorkspace.prepare(
            self.runs, "S2-RUN-INTEGRITY-NO-ACTION-01", _case("S2-CASE-0013")
        )
        service = RecoveryApplicationService(workspace, _Facts())
        from scripts.recovery_services import TransitionCommand

        for target in (WorkflowState.DEDUPLICATED, WorkflowState.CONTEXT_READY):
            before = service.inspect()
            service.advance(
                TransitionCommand(
                    target_state=target,
                    event_type="CALLER_IGNORED",
                    actor_kind="caller_ignored",
                    actor_id="S2-ACTOR-CALLER-IGNORED",
                    expected_case_revision=before.case_revision,
                    expected_ledger_head=before.ledger_head_digest,
                    command_id=f"S2-CMD-INTEGRITY-NO-ACTION-{before.sequence + 1:04d}",
                )
            )
        context = service.inspect()
        decision = RecoveryPolicyAdapter().decide(context).to_dict()
        self.assertEqual("NO_NEW_ACTION", decision["proposed_action"])
        workspace.append_transition(
            target_state=WorkflowState.RECOMMENDATION_READY,
            event_type="RECOMMENDATION_RECORDED",
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=context.case_revision,
            expected_ledger_head=context.ledger_head_digest,
            command_id="S2-CMD-INTEGRITY-NO-ACTION-RECOMMEND",
            links={"recommendation_id": "S2-RECOMMENDATION-INTEGRITY-NO-ACTION-0001"},
            decision_or_effect={"governed_recommendation": {"decision": decision}},
            action_count=0,
        )
        orchestrator = GuardedRecoveryOrchestrator(workspace, _Facts())
        verification = orchestrator.verify_direct_no_action(
            verification_id="S2-VERIFICATION-INTEGRITY-NO-ACTION-0001",
            command_id="S2-CMD-INTEGRITY-NO-ACTION-VERIFY",
        )
        communication = orchestrator.communicate_active_verification(
            communication_id="S2-COMMUNICATION-INTEGRITY-NO-ACTION-0001",
            command_id="S2-CMD-INTEGRITY-NO-ACTION-COMMUNICATE",
        )
        _, final = orchestrator.close(
            closure_id="S2-CLOSURE-INTEGRITY-NO-ACTION-0001",
            command_id="S2-CMD-INTEGRITY-NO-ACTION-CLOSE",
        )
        self.assertEqual(
            "VERIFIED_NO_NEW_ACTION", verification["payload"]["classification"]
        )
        self.assertEqual(
            ["NO_NEW_ACTION_REQUIRED"], communication["payload"]["fact_codes"]
        )
        self.assertEqual(WorkflowState.CLOSED, final.state)
        self.assertEqual(
            b"", workspace.authority.read_bytes("action-attempts/journal.jsonl")
        )
        self.assertEqual(
            [0, 0, 0],
            [event["payload"]["action_count"] for event in workspace.read_events()[-3:]],
        )


if __name__ == "__main__":
    unittest.main()
