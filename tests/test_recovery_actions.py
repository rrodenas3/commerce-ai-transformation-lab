from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.recovery_actions import (
    ActionControlError,
    build_action_contract,
    reserve_action_atomically,
)
from scripts.recovery_adapters import (
    EffectOutcomeUnknown,
    LocalSimulatedActionAdapter,
)
from scripts.recovery_approval import (
    AuthorityExpectation,
    create_synthetic_authority_event,
)
from scripts.recovery_services import RecoveryApplicationService, Stage2FactsPort
from scripts.recovery_orchestration import GuardedRecoveryOrchestrator
from scripts.recovery_state import WorkflowState
from scripts.recovery_workspace import FileRecoveryWorkspace
from scripts.stage2_facts import derive_case_facts
from scripts.stage2_contracts import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES = PROJECT_ROOT / "data" / "stage2" / "development" / "cases.jsonl"
CLI = PROJECT_ROOT / "scripts" / "run_recovery_lab.py"
NOW = "2026-08-11T10:00:00Z"


class _Facts(Stage2FactsPort):
    def derive(self, source_batch):
        return derive_case_facts(source_batch)


def _case(case_id="S2-CASE-0003"):
    for line in CASES.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if value["payload"]["case_id"] == case_id:
            return value
    raise AssertionError(case_id)


class RecoveryActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recovery-action-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = FileRecoveryWorkspace.prepare(
            Path(self.temporary.name) / "runs", "S2-RUN-ACTION-01", _case()
        )
        self.service = RecoveryApplicationService(self.workspace, _Facts())
        for target in (
            WorkflowState.DEDUPLICATED,
            WorkflowState.CONTEXT_READY,
            WorkflowState.RECOMMENDATION_READY,
            WorkflowState.AWAITING_APPROVAL,
            WorkflowState.ACTION_PREPARED,
        ):
            before = self.workspace.replay()
            self.workspace.append_transition(
                target_state=target,
                event_type="TEST_SETUP",
                actor_kind="test_fixture",
                actor_id="S2-ACTOR-TEST-FIXTURE",
                expected_case_revision=before.case_revision,
                expected_ledger_head=before.ledger_head_digest,
                command_id=f"S2-CMD-SETUP-{before.sequence + 1:04d}",
            )
        self.orchestrator = GuardedRecoveryOrchestrator(self.workspace, _Facts())
        context = self.service.inspect()
        self.action = build_action_contract(
            action_id="S2-ACTION-GOLDEN-0001",
            case_id=context.case_id,
            case_revision=context.case_revision,
            ledger_head_digest=context.ledger_head_digest,
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
            authority_reference="S2-DECISION-GOLDEN-0001",
            idempotency_key="S2-IDEMPOTENCY-GOLDEN-0001",
            timeout_milliseconds=5000,
        )
        payload = self.action["payload"]
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
        self.authority = create_synthetic_authority_event(
            expectation,
            approval_id="S2-DECISION-GOLDEN-0001",
            issued_by="S2-ACTOR-RECOVERY-SPECIALIST",
            approver_role="SYNTHETIC_RECOVERY_SPECIALIST",
            decision="APPROVED",
            rationale_code="DELEGATED_POLICY_BOUND_RECOVERY",
            issued_at="2026-08-11T09:00:00Z",
            expires_at="2026-08-11T11:00:00Z",
        )
        self.workspace.authority.write_once(
            f"actions/{self.action['record_id']}.json",
            canonical_json_bytes(self.action),
        )
        self.workspace.authority.write_once(
            f"approvals/{self.authority['record_id']}.json",
            canonical_json_bytes(self.authority),
        )

    def test_reservation_consumes_exact_authority_and_owns_idempotency_atomically(self):
        reserved = reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        self.assertEqual(WorkflowState.ACTION_RESERVED, reserved.state)
        event = self.workspace.read_events()[-1]["payload"]
        self.assertEqual("ACTION_AUTHORITY_RESERVED", event["event_type"])
        self.assertEqual("S2-DECISION-GOLDEN-0001", event["decision_or_effect"]["authority_consumed"])
        self.assertEqual("S2-IDEMPOTENCY-GOLDEN-0001", event["decision_or_effect"]["idempotency_owner"])
        self.assertEqual(1, event["action_count"])

        second = reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-RETRY-0001",
        )
        self.assertEqual(reserved, second)
        self.assertEqual(7, len(self.workspace.read_events()))

    def test_changed_payload_or_second_remedy_fails_before_adapter_effect(self):
        reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        changed = json.loads(json.dumps(self.action))
        changed["payload"]["eligible_quantity"] = 2
        with self.assertRaises(ActionControlError):
            reserve_action_atomically(
                self.workspace,
                changed,
                self.authority,
                recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                now=NOW,
                command_id="S2-CMD-RESERVE-CHANGED",
            )
        self.assertEqual([], LocalSimulatedActionAdapter(self.workspace).committed_effects("OMS"))

    def test_barrier_double_submit_creates_one_reservation_and_one_authority_consumption(self):
        def submit(index):
            return reserve_action_atomically(
                self.workspace,
                self.action,
                self.authority,
                recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
                now=NOW,
                command_id=f"S2-CMD-RESERVE-CONCURRENT-{index:04d}",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (1, 2)))
        self.assertEqual([WorkflowState.ACTION_RESERVED] * 2, [item.state for item in results])
        reservations = [
            event
            for event in self.workspace.read_events()
            if event["payload"]["event_type"] == "ACTION_AUTHORITY_RESERVED"
        ]
        self.assertEqual(1, len(reservations))
        self.assertEqual("S2-DECISION-GOLDEN-0001", reservations[0]["payload"]["decision_or_effect"]["authority_consumed"])

    def test_lost_receipt_reconciles_before_retry_and_returns_one_canonical_effect(self):
        reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        adapter = LocalSimulatedActionAdapter(self.workspace)
        with self.assertRaises(EffectOutcomeUnknown):
            adapter.execute(self.action, fault="after_mutation_before_receipt")
        receipt = adapter.execute(self.action)
        repeated = adapter.execute(self.action)
        self.assertEqual(receipt, repeated)
        self.assertTrue(receipt["payload"]["reconciled_before_retry"])
        for source in ("OMS", "INVENTORY", "WMS"):
            effects = adapter.committed_effects(source)
            self.assertEqual(1, len(effects), source)
            self.assertEqual("S2-ACTION-GOLDEN-0001", effects[0]["action_id"])

    def test_partial_effect_is_preserved_unknown_and_not_silently_retried(self):
        reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        adapter = LocalSimulatedActionAdapter(self.workspace)
        with self.assertRaises(EffectOutcomeUnknown):
            adapter.execute(self.action, fault="after_first_effect")
        with self.assertRaises(EffectOutcomeUnknown):
            adapter.execute(self.action)
        self.assertEqual(1, len(adapter.committed_effects("OMS")))
        self.assertEqual([], adapter.committed_effects("INVENTORY"))

    def test_application_records_unknown_outcome_then_reconciles_before_verifying(self):
        reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        adapter = LocalSimulatedActionAdapter(self.workspace)
        with self.assertRaises(EffectOutcomeUnknown):
            self.orchestrator.execute_active_action(
                start_command_id="S2-CMD-EFFECT-START-0001",
                outcome_command_id="S2-CMD-EFFECT-UNKNOWN-0001",
                fault="after_mutation_before_receipt",
            )
        self.assertEqual(WorkflowState.ACTION_RECOVERY, self.workspace.replay().state)
        receipt = self.orchestrator.execute_active_action(
            start_command_id="S2-CMD-EFFECT-START-RETRY-0001",
            outcome_command_id="S2-CMD-EFFECT-RECONCILED-0001",
        )
        self.assertTrue(receipt["payload"]["reconciled_before_retry"])
        self.assertEqual(WorkflowState.VERIFYING, self.workspace.replay().state)

    def test_uncommitted_source_tail_is_ignored_then_explicitly_quarantined(self):
        reserve_action_atomically(
            self.workspace,
            self.action,
            self.authority,
            recommending_provider_id="S2-PROVIDER-RECORDED-AI-V1",
            now=NOW,
            command_id="S2-CMD-RESERVE-0001",
        )
        adapter = LocalSimulatedActionAdapter(self.workspace)
        self.workspace.authority.append_durable("source-effects/OMS.jsonl", b'{"partial":')
        self.assertEqual([], adapter.committed_effects("OMS"))
        # Guarded execution recovers the uncommitted suffix under the same
        # writer authority before it makes any retry/idempotency decision.
        receipt = adapter.execute(self.action)
        self.assertEqual("SIMULATED_EFFECT_COMMITTED", receipt["payload"]["status"])
        self.assertEqual(1, len(adapter.committed_effects("OMS")))
        self.assertEqual(1, len(list((self.workspace.run_root / "quarantine").glob("*.bin"))))

    def test_golden_cli_replays_complete_separated_chain_and_verifies(self):
        runs = Path(self.temporary.name) / "golden-runs"
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "golden",
                "--runs-root",
                str(runs),
                "--run-id",
                "S2-RUN-GOLDEN-CLI-01",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual("CLOSED", summary["state"])
        self.assertEqual("VERIFIED_REMEDY", summary["verification_classification"])
        self.assertTrue(summary["communication_unsent"])
        workspace = FileRecoveryWorkspace.open(runs, "S2-RUN-GOLDEN-CLI-01")
        self.assertEqual([], workspace.verify())
        self.assertEqual(1, len(LocalSimulatedActionAdapter(workspace).committed_effects("OMS")))


if __name__ == "__main__":
    unittest.main()
