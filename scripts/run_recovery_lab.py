#!/usr/bin/env python3
"""Safe noninteractive CLI for the local Stage 2 recovery workspace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.recovery_services import (  # noqa: E402
    ActionReservationCommand,
    RecommendationCommand,
    RecoveryApplicationService,
    Stage2FactsPort,
    TransitionCommand,
)
from scripts.recovery_state import WorkflowState  # noqa: E402
from scripts.recovery_workspace import (  # noqa: E402
    FileRecoveryWorkspace,
    WorkspaceError,
)
from scripts.stage2_contracts import canonical_json_bytes, load_canonical_json  # noqa: E402
from scripts.stage2_facts import derive_case_facts  # noqa: E402
from scripts.recovery_policy import RecoveryPolicyAdapter  # noqa: E402
from scripts.recovery_recommender import RecordedCandidateProvider  # noqa: E402
from scripts.recovery_actions import build_action_contract  # noqa: E402
from scripts.recovery_approval import (  # noqa: E402
    AuthorityExpectation,
    create_synthetic_authority_event,
)
from scripts.recovery_orchestration import GuardedRecoveryOrchestrator  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "data" / "stage2" / "runs"
DEFAULT_CASES_PATH = PROJECT_ROOT / "data" / "stage2" / "development" / "cases.jsonl"
DEFAULT_PROVIDER_ROOT = PROJECT_ROOT / "data" / "stage2" / "providers" / "recorded-ai-v1"


class _FactsAdapter(Stage2FactsPort):
    def derive(self, source_batch: Mapping[str, Any]) -> Mapping[str, Any]:
        return derive_case_facts(source_batch)


def _emit(value: Any) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value))


def _load_case(case_id: str, cases_path: Path) -> dict[str, Any]:
    try:
        payload = cases_path.read_bytes()
    except OSError as error:
        raise WorkspaceError("public synthetic case pack is unavailable") from error
    for line in payload.splitlines(keepends=True):
        value = load_canonical_json(line)
        if value.get("payload", {}).get("case_id") == case_id:
            return value
    raise WorkspaceError("requested synthetic case ID is absent from the public pack")


def _workspace(args: argparse.Namespace) -> FileRecoveryWorkspace:
    return FileRecoveryWorkspace.open(args.runs_root, args.run_id)


def _service(args: argparse.Namespace) -> RecoveryApplicationService:
    return RecoveryApplicationService(_workspace(args), _FactsAdapter())


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True, help="canonical public-safe S2-RUN-* ID")
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help="isolated local run directory root",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local synthetic recovery lab.")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="create a write-once synthetic run")
    _add_run_arguments(prepare)
    prepare.add_argument("--case-id", default="S2-CASE-0001")
    prepare.add_argument("--cases-path", type=Path, default=DEFAULT_CASES_PATH)

    inspect = commands.add_parser("inspect", help="show the permitted durable context")
    _add_run_arguments(inspect)
    inspect.add_argument("--surface", choices=("operator", "provider"), default="operator")

    advance = commands.add_parser(
        "advance", help="request one declared intake/context transition"
    )
    _add_run_arguments(advance)
    advance.add_argument(
        "--to-state",
        choices=("DEDUPLICATED", "EVIDENCE_BLOCKED", "CONTEXT_READY"),
        required=True,
    )
    advance.add_argument("--expected-revision", type=int, required=True)
    advance.add_argument("--expected-head", required=True)
    advance.add_argument("--command-id", required=True)

    recommend = commands.add_parser(
        "recommend", help="record one preregistered provider attempt and governed outcome"
    )
    _add_run_arguments(recommend)
    recommend.add_argument("--provider-root", type=Path, default=DEFAULT_PROVIDER_ROOT)
    recommend.add_argument("--attempt-id", required=True)
    recommend.add_argument("--recommendation-id", required=True)
    recommend.add_argument("--expected-revision", type=int, required=True)
    recommend.add_argument("--expected-head", required=True)
    recommend.add_argument("--command-id", required=True)
    recommend.add_argument(
        "--route-command-id",
        help="also apply the independently derived non-action/authority route",
    )

    resume = commands.add_parser("resume", help="reconstruct state from durable history")
    _add_run_arguments(resume)
    resume.add_argument("--recover-partial-tail", action="store_true")

    freeze = commands.add_parser("freeze", help="seal the current local artifact inventory")
    _add_run_arguments(freeze)

    verify = commands.add_parser("verify", help="verify ledger, checkpoints, pins, and freeze")
    _add_run_arguments(verify)

    golden = commands.add_parser(
        "golden",
        help="replay the complete public-safe S2-CASE-0003 development slice",
    )
    _add_run_arguments(golden)
    golden.add_argument("--cases-path", type=Path, default=DEFAULT_CASES_PATH)
    return parser.parse_args(argv)


def _golden(args: argparse.Namespace) -> dict[str, Any]:
    """Run one deterministic governed slice; no provider, oracle, or live capability."""

    workspace = FileRecoveryWorkspace.prepare(
        args.runs_root,
        args.run_id,
        _load_case("S2-CASE-0003", args.cases_path),
    )
    service = RecoveryApplicationService(workspace, _FactsAdapter())

    def commit_artifact(collection: str, record: Mapping[str, Any]) -> None:
        workspace.authority.write_once(
            f"{collection}/{record['record_id']}.json",
            canonical_json_bytes(dict(record)),
        )

    def advance(target: WorkflowState, event_type: str, command_id: str, **values: Any) -> None:
        before = workspace.replay()
        workspace.append_transition(
            target_state=target,
            event_type=event_type,
            actor_kind="deterministic_control",
            actor_id="S2-ACTOR-POLICY-CONTROL",
            expected_case_revision=before.case_revision,
            expected_ledger_head=before.ledger_head_digest,
            command_id=command_id,
            links=values.pop("links", {}),
            decision_or_effect=values,
            action_count=0,
        )

    advance(WorkflowState.DEDUPLICATED, "INTAKE_DEDUPLICATED", "S2-CMD-GOLDEN-0001")
    advance(WorkflowState.CONTEXT_READY, "CONTEXT_ASSEMBLED", "S2-CMD-GOLDEN-0002")
    decision = RecoveryPolicyAdapter().decide(service.inspect()).to_dict()
    recommendation = {
        "payload": {
            "case_id": "S2-CASE-0003",
            "case_revision": 1,
            "development_fixture": True,
            "governed_decision": decision,
            "provider_attempt": None,
            "synthetic": True,
        },
        "record_id": "S2-RECOMMENDATION-GOLDEN-0001",
        "record_type": "recommendation",
        "schema_version": "stage2-development-recommendation/v1",
    }
    commit_artifact("recommendations", recommendation)
    advance(
        WorkflowState.RECOMMENDATION_READY,
        "DETERMINISTIC_DEVELOPMENT_RECOMMENDATION_RECORDED",
        "S2-CMD-GOLDEN-0003",
        links={"recommendation_id": "S2-RECOMMENDATION-GOLDEN-0001"},
        governed_recommendation={
            "candidate_accepted": False,
            "decision": decision,
            "development_fixture": True,
            "provider_attempt": None,
        },
    )
    advance(
        WorkflowState.AWAITING_APPROVAL,
        "SYNTHETIC_AUTHORITY_DECISION_REQUIRED",
        "S2-CMD-GOLDEN-0004",
        authority_route=decision["authority_route"],
    )
    advance(
        WorkflowState.ACTION_PREPARED,
        "EXACT_ACTION_PREPARATION_STARTED",
        "S2-CMD-GOLDEN-0005",
    )
    context = service.inspect()
    source_batch = workspace.load_source_batch()
    oms = next(
        record["payload"]["data"]
        for record in source_batch["payload"]["records"]
        if record["payload"]["source_name"] == "OMS"
    )
    facts = context.permitted_facts
    action = build_action_contract(
        action_id="S2-ACTION-GOLDEN-0001",
        case_id=context.case_id,
        case_revision=context.case_revision,
        ledger_head_digest=context.ledger_head_digest,
        policy_id="SCC-01-RECOVERY-POLICY",
        policy_version="1.0.0",
        operation="RESHIP",
        target=oms["order_id"],
        eligible_business_key=facts["lines"][0]["line_id"],
        eligible_quantity=facts["remaining_quantity"],
        amount_cents=facts["affected_value_cents"],
        currency=facts["currency"],
        before_state={
            "available_replacement_quantity": facts["available_replacement_quantity"],
            "remaining_quantity": facts["remaining_quantity"],
        },
        authority_route=decision["authority_route"],
        authority_reference="S2-DECISION-GOLDEN-0001",
        idempotency_key="S2-IDEMPOTENCY-GOLDEN-0001",
        timeout_milliseconds=5000,
    )
    commit_artifact("actions", action)
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
        recommending_provider_id="S2-PROVIDER-DETERMINISTIC-DEV-01",
    )
    authority = create_synthetic_authority_event(
        expectation,
        approval_id="S2-DECISION-GOLDEN-0001",
        issued_by="S2-ACTOR-RECOVERY-SPECIALIST",
        approver_role="SYNTHETIC_RECOVERY_SPECIALIST",
        decision="APPROVED",
        rationale_code="DELEGATED_POLICY_BOUND_RECOVERY",
        issued_at="2026-08-11T09:00:00Z",
        expires_at="2026-08-11T11:00:00Z",
    )
    commit_artifact("approvals", authority)
    service.reserve_action(
        ActionReservationCommand(
            action=action,
            authority_event=authority,
            recommending_provider_id="S2-PROVIDER-DETERMINISTIC-DEV-01",
            now="2026-08-11T10:00:00Z",
            command_id="S2-CMD-GOLDEN-0006",
        )
    )
    orchestrator = GuardedRecoveryOrchestrator(workspace, _FactsAdapter())
    receipt = orchestrator.execute_active_action(
        start_command_id="S2-CMD-GOLDEN-0007",
        outcome_command_id="S2-CMD-GOLDEN-0008",
    )
    verification = orchestrator.verify_active_action(
        verification_id="S2-VERIFICATION-GOLDEN-0001",
        command_id="S2-CMD-GOLDEN-0009",
    )
    communication = orchestrator.communicate_active_verification(
        communication_id="S2-COMMUNICATION-GOLDEN-0001",
        command_id="S2-CMD-GOLDEN-0010",
    )
    closure, final = orchestrator.close(
        closure_id="S2-CLOSURE-GOLDEN-0001",
        command_id="S2-CMD-GOLDEN-0011",
    )
    service.verify()
    return {
        "case_id": final.case_id,
        "communication_unsent": communication["payload"]["unsent"],
        "ledger_head_digest": final.ledger_head_digest,
        "run_id": final.run_id,
        "state": final.state.value,
        "synthetic": True,
        "verification_classification": verification["payload"]["classification"],
        "verified_milestone": verification["payload"]["milestone"],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            batch = _load_case(args.case_id, args.cases_path)
            workspace = FileRecoveryWorkspace.prepare(args.runs_root, args.run_id, batch)
            _emit(RecoveryApplicationService(workspace, _FactsAdapter()).inspect().to_dict())
        elif args.command == "inspect":
            service = _service(args)
            context = service.provider_context() if args.surface == "provider" else service.inspect()
            _emit(context.to_dict())
        elif args.command == "advance":
            service = _service(args)
            context = service.advance(
                TransitionCommand(
                    target_state=WorkflowState(args.to_state),
                    event_type="SERVER_DERIVED",
                    actor_kind="server_derived",
                    actor_id="S2-ACTOR-SERVER-DERIVED",
                    expected_case_revision=args.expected_revision,
                    expected_ledger_head=args.expected_head,
                    command_id=args.command_id,
                )
            )
            _emit(context.to_dict())
        elif args.command == "recommend":
            outcome = _service(args).recommend(
                RecommendationCommand(
                    recommendation_id=args.recommendation_id,
                    attempt_id=args.attempt_id,
                    expected_case_revision=args.expected_revision,
                    expected_ledger_head=args.expected_head,
                    command_id=args.command_id,
                    route_command_id=args.route_command_id,
                ),
                RecordedCandidateProvider(args.provider_root),
                RecoveryPolicyAdapter(),
            )
            _emit(outcome.to_dict())
        elif args.command == "resume":
            _emit(_service(args).resume(recover_partial_tail=args.recover_partial_tail).to_dict())
        elif args.command == "freeze":
            _emit(dict(_service(args).freeze()))
        elif args.command == "verify":
            service = _service(args)
            service.verify()
            _emit({"run_id": args.run_id, "status": "verified"})
        elif args.command == "golden":
            _emit(_golden(args))
        return 0
    except (WorkspaceError, ValueError) as error:
        # Errors are deliberately generic and never echo paths, payloads, or private input.
        print(f"ERROR: {type(error).__name__}: operation failed closed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
