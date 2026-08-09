#!/usr/bin/env python3
"""Run and score the deliberately bounded Stage 1 rules baseline."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_case_system import (
    load_stage1_policy,
    read_jsonl,
    write_jsonl,
    write_utf8_lf,
)
from scripts.score_stage1_manual import (
    PUBLIC_ORACLE_EXPOSURE_STATUS,
    RUN_MANIFEST_SCHEMA_VERSION,
)
from scripts.stage1_scoring import (
    ESCALATION_ACTIONS,
    evaluate_decisions,
    write_manual_template,
)


BASELINE_ID = "scc-01-deterministic-baseline-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manual_run_manifest_template(
    root: Path,
    generated: Path,
    cases: list[dict[str, Any]],
    oracles: list[dict[str, Any]],
) -> None:
    """Write a deterministic, public-safe evidence contract for a manual run."""
    source_manifest_path = generated / "manifest.json"
    policy_path = root / "data" / "stage1" / "policy.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    oracle_versions = {oracle.get("oracle_version") for oracle in oracles}
    if len(oracle_versions) != 1 or None in oracle_versions:
        raise ValueError("manual run manifest requires exactly one oracle version")

    def artifact_pin(path: Path, public_path: str) -> dict[str, str]:
        return {
            "path": public_path,
            "sha256": _sha256(path),
        }

    run_manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "dataset_role": source_manifest["dataset_role"],
        "run_type": "manual-no-ai",
        "oracle_exposure_status": PUBLIC_ORACLE_EXPOSURE_STATUS,
        "policy": {
            "policy_id": policy["policy_id"],
            "version": policy["version"],
        },
        "oracle": {"version": oracle_versions.pop()},
        "assigned_case_ids": [case["case_id"] for case in cases],
        "artifacts": {
            "cases": artifact_pin(
                generated / "cases.jsonl",
                "data/stage1/generated/cases.jsonl",
            ),
            "oracle": artifact_pin(
                generated / "oracle.jsonl",
                "data/stage1/generated/oracle.jsonl",
            ),
            "policy": artifact_pin(policy_path, "data/stage1/policy.json"),
            "artifact_manifest": artifact_pin(
                source_manifest_path,
                "data/stage1/generated/manifest.json",
            ),
        },
    }
    write_utf8_lf(
        generated / "manual-run-manifest-template.json",
        json.dumps(run_manifest, indent=2, sort_keys=True) + "\n",
    )


def _authority(case: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str]:
    exposure = case["order"]["affected_value_cents"]
    authority = policy["authority"]
    if (
        exposure > authority["team_lead_max_exposure_cents"]
        or case["order"]["order_value_cents"] > authority["finance_review_order_value_cents"]
    ):
        return "approval", "finance_duty_approver"
    if (
        exposure > authority["delegated_max_exposure_cents"]
        or (
            authority["repeat_recovery_requires_approval"]
            and case["history"]["repeat_recovery"]
        )
    ):
        return "approval", "workflow_owner"
    return "delegated", "customer_recovery_specialist"


def decide_case(case: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Apply transparent rules without consulting the oracle implementation."""
    sources = case["evidence"]["sources"]
    evidence_used = sorted(name for name, record in sources.items() if record["available"])
    unavailable = [name for name, record in sources.items() if not record["available"]]
    stale = [name for name, record in sources.items() if record["available"] and not record["fresh"]]
    prior_action = case["history"]["prior_action"]
    route, owner = _authority(case, policy)

    if case["risk_flags"]:
        action = "ESCALATE_SPECIALIST"
        route, owner = "specialist", "policy_and_risk_owner"
    elif prior_action and prior_action["status"] == "VERIFIED":
        action = "NO_ACTION_ALREADY_RECOVERED"
        route, owner = "delegated", "customer_recovery_specialist"
    elif prior_action and prior_action["status"] in {"PENDING", "FAILED_SAFE", "UNVERIFIED"}:
        action = "ESCALATE_ACTION_RECOVERY"
        route, owner = "specialist", "technical_owner"
    elif case["evidence"]["source_conflict"] or unavailable or stale:
        action = "ESCALATE_EVIDENCE"
        route, owner = "specialist", "fulfilment_operations_coordinator"
    elif case["trigger"]["duplicate"]:
        action = "NO_ACTION_DUPLICATE_SIGNAL"
        route, owner = "delegated", "customer_recovery_specialist"
    elif case["carrier"]["status"] == "IN_TRANSIT" and case["customer"]["preference"] == "wait":
        action = "WAIT_VERIFIED_ETA"
        route, owner = "delegated", "customer_recovery_specialist"
    elif case["inventory"]["reservable"] and case["customer"]["preference"] != "refund_missing":
        action = "RESHIP_MISSING"
    else:
        action = "REFUND_MISSING"

    message_facts = [
        "case_received",
        "order_reference_confirmed",
        "recovery_under_review",
    ]
    if action == "WAIT_VERIFIED_ETA":
        message_facts.append("revised_eta_is_estimate")
    elif action == "RESHIP_MISSING":
        message_facts.append("replacement_requires_verified_execution")
    elif action == "REFUND_MISSING":
        message_facts.append("refund_requires_verified_execution")
    elif action.startswith("NO_ACTION"):
        message_facts.append("no_new_action_created")

    return {
        "case_id": case["case_id"],
        "baseline_id": BASELINE_ID,
        "recommended_action": action,
        "route": route,
        "decision_owner": owner,
        "evidence_used": evidence_used,
        "message_facts": sorted(message_facts),
        "abstained": action in ESCALATION_ACTIONS,
        "executed_action": False,
        "postcondition_verified": False,
    }


def run_generated_baseline(root: Path, generated: Path) -> dict[str, Any]:
    """Generate every downstream baseline artifact in the supplied directory."""
    cases = read_jsonl(generated / "cases.jsonl")
    oracles = read_jsonl(generated / "oracle.jsonl")
    policy = load_stage1_policy(root)
    decisions = [decide_case(case, policy) for case in cases]
    summary = evaluate_decisions(cases, decisions, oracles, baseline_id=BASELINE_ID)
    write_jsonl(generated / "deterministic-decisions.jsonl", decisions)
    write_utf8_lf(
        generated / "deterministic-summary.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    write_manual_template(generated / "manual-baseline-template.csv", cases)
    write_manual_run_manifest_template(root, generated, cases, oracles)
    return summary


def run_committed_baseline(root: Path) -> dict[str, Any]:
    generated = root / "data" / "stage1" / "generated"
    return run_generated_baseline(root, generated)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    summary = run_committed_baseline(root)
    print(
        f"Scored {summary['case_count']} cases: "
        f"coverage={summary['decision_coverage_rate']:.1%}, "
        f"safe_or_correct={summary['successful_or_safe_escalation_rate']:.1%}, "
        f"critical_violations={summary['critical_violation_count']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
