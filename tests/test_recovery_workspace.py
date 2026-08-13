#!/usr/bin/env python3
"""Proof-first durability, replay, path and CLI tests for the recovery lab."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.recovery_services import RecoveryApplicationService, Stage2FactsPort, TransitionCommand
from scripts.recovery_state import WorkflowState
from scripts.recovery_workspace import (
    FileRecoveryWorkspace,
    FrozenWorkspaceError,
    SafeFileAuthority,
    StaleWorkspaceError,
    WorkspaceIntegrityError,
)
from scripts.stage2_contracts import canonical_json_bytes
from scripts.stage2_facts import derive_case_facts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = PROJECT_ROOT / "data" / "stage2" / "development" / "cases.jsonl"
CLI_PATH = PROJECT_ROOT / "scripts" / "run_recovery_lab.py"


def _case(index: int = 0) -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8").splitlines()[index])


class _Facts(Stage2FactsPort):
    def derive(self, source_batch):
        return derive_case_facts(source_batch)


class RecoveryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="recovery-workspace-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runs = self.root / "runs"
        self.workspace = FileRecoveryWorkspace.prepare(
            self.runs, "S2-RUN-WORKSPACE-01", _case()
        )
        self.service = RecoveryApplicationService(self.workspace, _Facts())

    def _advance(self, target: WorkflowState, *, command_id: str | None = None):
        before = self.workspace.replay()
        if target in {
            WorkflowState.DEDUPLICATED,
            WorkflowState.EVIDENCE_BLOCKED,
            WorkflowState.CONTEXT_READY,
        }:
            return self.service.advance(
                TransitionCommand(
                    target_state=target,
                    event_type="CALLER_IGNORED",
                    actor_kind="caller_ignored",
                    actor_id="S2-ACTOR-CALLER-IGNORED",
                    expected_case_revision=before.case_revision,
                    expected_ledger_head=before.ledger_head_digest,
                    command_id=command_id or f"S2-CMD-{before.sequence + 1:04d}",
                )
            )
        return self.workspace.append_transition(
            target_state=target,
            event_type="TEST_SETUP",
            actor_kind="test_fixture",
            actor_id="S2-ACTOR-TEST-FIXTURE",
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id=command_id or f"S2-CMD-{before.sequence + 1:04d}",
        )

    def test_prepare_creates_complete_isolated_run_layout_and_received_event(self):
        expected = {
            "checkpoints",
            "communication",
            "events",
            "metrics",
            "receipts",
            "source-snapshots",
            "verification",
        }
        self.assertTrue(expected.issubset({p.name for p in self.workspace.run_root.iterdir()}))
        projection = self.workspace.replay()
        self.assertEqual(WorkflowState.RECEIVED, projection.state)
        self.assertEqual(1, projection.sequence)
        self.assertRegex(projection.ledger_head_digest, r"^[0-9a-f]{64}$")

    def test_every_checkpoint_replays_to_same_final_truth(self):
        self._advance(WorkflowState.DEDUPLICATED)
        self._advance(WorkflowState.CONTEXT_READY)
        self._advance(WorkflowState.RECOMMENDATION_READY)
        final = self.workspace.replay()
        trace_before = (self.workspace.run_root / "events" / "workflow.jsonl").read_bytes()
        checkpoints = sorted((self.workspace.run_root / "checkpoints").glob("*.json"))
        self.assertEqual(final.sequence, len(checkpoints))
        for checkpoint in checkpoints:
            with self.subTest(checkpoint=checkpoint.name):
                resumed = self.workspace.resume(checkpoint=checkpoint)
                self.assertEqual(final, resumed)
                self.assertEqual(
                    trace_before,
                    (self.workspace.run_root / "events" / "workflow.jsonl").read_bytes(),
                )

    def test_duplicate_command_is_idempotent_and_stale_different_command_fails(self):
        before = self.service.inspect()
        command = TransitionCommand(
            target_state=WorkflowState.DEDUPLICATED,
            event_type="STATE_ADVANCED",
            actor_kind="operator",
            actor_id="S2-ACTOR-CREATOR",
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id="S2-CMD-IDEMPOTENT-01",
        )
        first = self.service.advance(command)
        second = self.service.advance(command)
        self.assertEqual(first, second)
        self.assertEqual(2, self.workspace.replay().sequence)
        changed = copy.replace(command, command_id="S2-CMD-STALE-02") if hasattr(copy, "replace") else TransitionCommand(
            target_state=command.target_state,
            event_type=command.event_type,
            actor_kind=command.actor_kind,
            actor_id=command.actor_id,
            expected_case_revision=command.expected_case_revision,
            expected_ledger_head=command.expected_ledger_head,
            command_id="S2-CMD-STALE-02",
        )
        with self.assertRaises(StaleWorkspaceError):
            self.service.advance(changed)

    def test_acknowledged_append_survives_abrupt_process_exit(self):
        before = self.service.inspect()
        code = (
            "import os; from pathlib import Path; "
            "from scripts.recovery_workspace import FileRecoveryWorkspace; "
            "from scripts.recovery_services import RecoveryApplicationService, Stage2FactsPort, TransitionCommand; "
            "from scripts.recovery_state import WorkflowState; "
            "from scripts.stage2_facts import derive_case_facts; "
            "P=type('P',(),{'derive':lambda self,b:derive_case_facts(b)}); "
            f"w=FileRecoveryWorkspace.open(Path({str(self.runs)!r}),'S2-RUN-WORKSPACE-01'); "
            "s=RecoveryApplicationService(w,P()); "
            f"s.advance(TransitionCommand(WorkflowState.DEDUPLICATED,'STATE_ADVANCED','operator','S2-ACTOR-CREATOR',{before.case_revision},{before.ledger_head_digest!r},'S2-CMD-CRASH-01')); "
            "os._exit(23)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=PROJECT_ROOT, check=False
        )
        self.assertEqual(23, completed.returncode)
        restarted = FileRecoveryWorkspace.open(self.runs, "S2-RUN-WORKSPACE-01")
        self.assertEqual(WorkflowState.DEDUPLICATED, restarted.replay().state)

    def test_partial_tail_is_quarantined_then_recovery_event_is_appended(self):
        before = self.workspace.replay()
        ledger = self.workspace.run_root / "events" / "workflow.jsonl"
        with ledger.open("ab") as stream:
            stream.write(b'{"partial":')
            stream.flush()
            os.fsync(stream.fileno())
        with self.assertRaisesRegex(WorkspaceIntegrityError, "partial"):
            self.workspace.replay()
        recovered = self.workspace.resume(recover_partial_tail=True)
        self.assertEqual(before.sequence + 1, recovered.sequence)
        events = self.workspace.read_events()
        self.assertEqual("PARTIAL_TAIL_RECOVERED", events[-1]["payload"]["event_type"])
        self.assertEqual(before.ledger_head_digest, events[-1]["payload"]["previous_event_digest"])
        quarantined = list((self.workspace.run_root / "quarantine").glob("*.bin"))
        self.assertEqual(1, len(quarantined))
        self.assertEqual(b'{"partial":', quarantined[0].read_bytes())

    def test_replay_detects_event_mutation_and_manifest_pin_change(self):
        ledger = self.workspace.run_root / "events" / "workflow.jsonl"
        original = ledger.read_bytes()
        event = json.loads(original)
        event["payload"]["actor_id"] = "S2-ACTOR-MUTATED"
        ledger.write_bytes(canonical_json_bytes(event))
        with self.assertRaises(WorkspaceIntegrityError):
            self.workspace.replay()
        ledger.write_bytes(original)
        manifest = self.workspace.run_root / "manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["source_snapshot_sha256"] = "0" * 64
        manifest.write_bytes(canonical_json_bytes(value))
        with self.assertRaises(WorkspaceIntegrityError):
            self.workspace.replay()

    def test_replay_detects_deletion_insertion_reorder_and_cross_case_linkage(self):
        self._advance(WorkflowState.DEDUPLICATED)
        self._advance(WorkflowState.CONTEXT_READY)
        self._advance(WorkflowState.RECOMMENDATION_READY)
        ledger = self.workspace.run_root / "events" / "workflow.jsonl"
        original = ledger.read_bytes()
        lines = original.splitlines(keepends=True)
        mutations = {
            "deletion": b"".join(lines[:-1]),
            "insertion": b"".join([lines[0], lines[0], *lines[1:]]),
            "reorder": b"".join([lines[0], lines[2], lines[1], *lines[3:]]),
        }
        cross_case = json.loads(lines[-1])
        cross_case["payload"]["links"] = {"case_id": "S2-CASE-9999"}
        mutations["cross_case"] = b"".join(lines[:-1]) + canonical_json_bytes(cross_case)
        for name, payload in mutations.items():
            with self.subTest(name=name):
                ledger.write_bytes(payload)
                with self.assertRaises(WorkspaceIntegrityError):
                    self.workspace.replay()
                ledger.write_bytes(original)

    def test_run_root_identity_substitution_and_symlink_ancestor_fail_closed(self):
        original = self.workspace.run_root
        moved = original.with_name(f"{original.name}-MOVED")
        original.rename(moved)
        original.mkdir()
        try:
            with self.assertRaises(WorkspaceIntegrityError):
                self.workspace.replay()
        finally:
            original.rmdir()
            moved.rename(original)

        outside = self.root / "outside"
        outside.mkdir()
        (outside / "payload.json").write_text("{}", encoding="utf-8")
        linked = original / "linked-ancestor"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symbolic links unavailable: {error}")
        with self.assertRaises(WorkspaceIntegrityError):
            self.workspace.authority.read_bytes("linked-ancestor/payload.json")

    def test_freeze_binds_inventory_and_prevents_overwrite_or_append(self):
        frozen = self.workspace.freeze()
        self.assertEqual(self.workspace.replay().ledger_head_digest, frozen["final_ledger_head"])
        self.assertEqual([], self.workspace.verify())
        with self.assertRaises(FrozenWorkspaceError):
            self._advance(WorkflowState.DEDUPLICATED)
        checkpoint = next((self.workspace.run_root / "checkpoints").glob("*.json"))
        checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
        with self.assertRaises(WorkspaceIntegrityError):
            self.workspace.verify()

    def test_path_traversal_foreign_run_and_hardlinked_ledger_fail_closed(self):
        authority = SafeFileAuthority(self.workspace.run_root)
        for unsafe in (Path("..") / "escape", Path("events") / ".." / ".." / "escape"):
            with self.subTest(path=unsafe):
                with self.assertRaises(WorkspaceIntegrityError):
                    authority.read_bytes(unsafe)
        with self.assertRaises(WorkspaceIntegrityError):
            FileRecoveryWorkspace.open(self.runs, "../S2-RUN-FOREIGN")
        private_request = Path("..") / ".." / "artifacts" / "private" / "oracle.json"
        with self.assertRaises(WorkspaceIntegrityError) as captured:
            authority.read_bytes(private_request)
        self.assertNotIn("oracle.json", str(captured.exception))
        ledger = self.workspace.run_root / "events" / "workflow.jsonl"
        alias = self.workspace.run_root / "events" / "workflow-alias.jsonl"
        try:
            os.link(ledger, alias)
        except OSError as error:
            self.skipTest(f"hard links unavailable: {error}")
        with self.assertRaisesRegex(WorkspaceIntegrityError, "hard link"):
            self.workspace.replay()

    def test_concurrent_cli_same_command_commits_once(self):
        before = self.service.inspect()
        args = [
            sys.executable,
            str(CLI_PATH),
            "advance",
            "--runs-root",
            str(self.runs),
            "--run-id",
            "S2-RUN-WORKSPACE-01",
            "--to-state",
            "DEDUPLICATED",
            "--expected-revision",
            str(before.case_revision),
            "--expected-head",
            before.ledger_head_digest,
            "--command-id",
            "S2-CMD-CONCURRENT-01",
        ]
        first = subprocess.Popen(args, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen(args, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        results = [first.communicate(timeout=20), second.communicate(timeout=20)]
        self.assertEqual([0, 0], [first.returncode, second.returncode], results)
        self.assertEqual(2, self.workspace.replay().sequence)


class RecoveryCliTests(unittest.TestCase):
    def test_prepare_inspect_advance_resume_freeze_verify_commands(self):
        with tempfile.TemporaryDirectory(prefix="recovery-cli-") as temporary:
            runs = Path(temporary) / "runs"
            base = ["--runs-root", str(runs), "--run-id", "S2-RUN-CLI-01"]

            def run(command, *extra):
                return subprocess.run(
                    [sys.executable, str(CLI_PATH), command, *base, *extra],
                    cwd=PROJECT_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            prepared = run("prepare", "--case-id", "S2-CASE-0001")
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            inspected = run("inspect")
            self.assertEqual(0, inspected.returncode, inspected.stderr)
            context = json.loads(inspected.stdout)
            advanced = run(
                "advance",
                "--to-state",
                "DEDUPLICATED",
                "--expected-revision",
                "1",
                "--expected-head",
                context["ledger_head_digest"],
                "--command-id",
                "S2-CMD-CLI-01",
            )
            self.assertEqual(0, advanced.returncode, advanced.stderr)
            self.assertEqual(0, run("resume").returncode)
            self.assertEqual(0, run("freeze").returncode)
            verified = run("verify")
            self.assertEqual(0, verified.returncode, verified.stderr)


if __name__ == "__main__":
    unittest.main()
