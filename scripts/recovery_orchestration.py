#!/usr/bin/env python3
"""Guarded U5 orchestration for the only consequential local action path.

This outward layer owns the allow-listed simulator, read-only verifier, derived
communication, and closure binding.  Callers provide identifiers and command
IDs, never adapter implementations or success artifacts.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.recovery_adapters import LocalSimulatedActionAdapter
from scripts.recovery_communication import create_unsent_communication
from scripts.recovery_policy import RecoveryPolicyAdapter
from scripts.recovery_services import (
    ObjectCommand,
    RecoveryApplicationService,
    Stage2FactsPort,
)
from scripts.recovery_state import WorkflowState
from scripts.recovery_verification import (
    FileAuthoritativeEffectReader,
    verify_authoritative_postcondition,
    verify_no_action_condition,
)
from scripts.recovery_workspace import FileRecoveryWorkspace, WorkspaceIntegrityError
from scripts.stage2_contracts import (
    ContractValidationError,
    STAGE2_ID_PATTERN,
    canonical_json_bytes,
    canonical_sha256,
    load_canonical_json,
    validate_neutral_record,
)


class OrchestrationControlError(ValueError):
    """Raised when a caller attempts to cross a guarded evidence boundary."""


class GuardedRecoveryOrchestrator:
    """Own the exact local adapter -> verifier -> communication -> close chain."""

    def __init__(self, workspace: FileRecoveryWorkspace, facts: Stage2FactsPort):
        if not isinstance(workspace, FileRecoveryWorkspace):
            raise OrchestrationControlError("guarded orchestration requires a file workspace")
        self.workspace = workspace
        self.service = RecoveryApplicationService(workspace, facts)

    @staticmethod
    def _validate_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not STAGE2_ID_PATTERN.fullmatch(value):
            raise OrchestrationControlError(f"{label} is not a canonical synthetic ID")
        return value

    def _load_record(
        self,
        collection: str,
        record_id: str,
        record_type: str,
        *,
        filename_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_id(record_id, record_type)
        allowed = {
            "actions": "action",
            "closures": "closure",
            "communication": "communication",
            "receipts": "adapter_receipt",
            "verification": "verification",
        }
        if allowed.get(collection) != record_type:
            raise OrchestrationControlError("artifact collection is not allow-listed")
        try:
            raw = self.workspace.authority.read_bytes(
                f"{collection}/{filename_id or record_id}.json"
            )
            loaded = load_canonical_json(raw)
            record = (
                validate_neutral_record(loaded)
                if record_type != "adapter_receipt"
                else loaded
            )
        except (WorkspaceIntegrityError, ContractValidationError, TypeError, ValueError) as error:
            raise OrchestrationControlError("active artifact is unavailable or invalid") from error
        if (
            not isinstance(record, dict)
            or record.get("record_id") != record_id
            or record.get("record_type") != record_type
        ):
            raise OrchestrationControlError("active artifact identity is inconsistent")
        if record_type == "adapter_receipt":
            payload = record.get("payload")
            if (
                record.get("schema_version") != "stage2-adapter-receipt/v1"
                or not isinstance(payload, Mapping)
                or payload.get("verification_authority") is not False
                or payload.get("synthetic") is not True
            ):
                raise OrchestrationControlError("adapter receipt contract is invalid")
        return record

    def _write_record(self, collection: str, record: Mapping[str, Any]) -> None:
        record_id = record.get("record_id")
        if not isinstance(record_id, str):
            raise OrchestrationControlError("artifact ID is unavailable")
        self.workspace.authority.write_once(
            f"{collection}/{record_id}.json", canonical_json_bytes(dict(record))
        )

    def _active_action(self) -> dict[str, Any]:
        current = self.workspace.replay()
        action_id = current.active_object_ids.get("action_id")
        if not action_id:
            raise OrchestrationControlError("no exact action is active")
        action = self._load_record("actions", action_id, "action")
        payload = action["payload"]
        if (
            payload.get("case_id") != current.case_id
            or payload.get("case_revision") != current.case_revision
        ):
            raise OrchestrationControlError("active action crosses case revision")
        return action

    def execute_active_action(
        self,
        *,
        start_command_id: str,
        outcome_command_id: str,
        fault: str | None = None,
    ) -> Mapping[str, Any]:
        """Execute only the active reserved action through the local simulator."""

        action = self._active_action()
        adapter = LocalSimulatedActionAdapter(self.workspace)
        return self.service._record_action_execution(
            action,
            adapter,
            start_command_id=start_command_id,
            outcome_command_id=outcome_command_id,
            fault=fault,
        )

    def verify_active_action(
        self, *, verification_id: str, command_id: str
    ) -> dict[str, Any]:
        """Recompute a verification from exact committed source effects."""

        action = self._active_action()
        action_id = action["record_id"]
        receipt_ids = [
            event["payload"]["links"].get("receipt_id")
            for event in self.workspace.read_events()
            if event["payload"]["links"].get("action_id") == action_id
            and event["payload"]["links"].get("receipt_id") is not None
        ]
        if len(receipt_ids) != 1:
            raise OrchestrationControlError("active action lacks one exact adapter receipt")
        receipt = self._load_record(
            "receipts",
            receipt_ids[0],
            "adapter_receipt",
            filename_id=action_id,
        )
        if (
            receipt["payload"].get("action_id") != action_id
            or receipt["payload"].get("action_contract_digest")
            != action["payload"].get("action_contract_digest")
        ):
            raise OrchestrationControlError("adapter receipt does not bind the active action")
        verification = verify_authoritative_postcondition(
            action,
            FileAuthoritativeEffectReader(self.workspace.run_root),
            verification_id=self._validate_id(verification_id, "verification ID"),
            untrusted_receipt=receipt,
        )
        self._write_record("verification", verification)
        self.service._record_verification(
            ObjectCommand(record=verification, command_id=command_id)
        )
        return verification

    def verify_direct_no_action(
        self, *, verification_id: str, command_id: str
    ) -> dict[str, Any]:
        """Re-derive and bind a policy-supported wait/no-new-action condition."""

        context = self.service.inspect()
        if context.state is not WorkflowState.RECOMMENDATION_READY:
            raise OrchestrationControlError(
                "direct verification requires a governed recommendation"
            )
        decision = RecoveryPolicyAdapter().decide(context).to_dict()
        route = decision.get("authority_route")
        action = decision.get("proposed_action")
        if route != "DIRECT_NO_ACTION" or action not in {
            "WAIT_VERIFIED_ETA",
            "NO_NEW_ACTION",
        }:
            raise OrchestrationControlError("current facts do not support direct no-action")
        recommendation_id = context.active_object_ids.get("recommendation_id")
        recommendation_events = [
            event["payload"]
            for event in self.workspace.read_events()
            if event["payload"]["event_type"] == "RECOMMENDATION_RECORDED"
            and event["payload"]["links"].get("recommendation_id") == recommendation_id
        ]
        if len(recommendation_events) != 1:
            raise OrchestrationControlError("active governed recommendation is unavailable")
        recorded = recommendation_events[0]["decision_or_effect"].get(
            "governed_recommendation", {}
        )
        if recorded.get("decision") != decision:
            raise OrchestrationControlError("current facts disagree with the recorded decision")
        if action == "WAIT_VERIFIED_ETA":
            classification = "VERIFIED_WAIT_CONDITION"
            milestone = "CURRENT_RELIABLE_ETA"
            required_sources = {"CARRIER", "CRM", "POLICY"}
        else:
            classification = "VERIFIED_NO_NEW_ACTION"
            milestone = "PRIOR_REMEDY_COVERS_QUANTITY"
            required_sources = {"OMS", "PAYMENT", "POLICY"}
        citations = tuple(
            item["record_id"]
            for item in context.cited_sources
            if item["source_name"] in required_sources
        )
        if len(citations) != len(required_sources):
            raise OrchestrationControlError("direct condition lacks authoritative citations")
        verification = verify_no_action_condition(
            verification_id=self._validate_id(verification_id, "verification ID"),
            case_id=context.case_id,
            case_revision=context.case_revision,
            classification=classification,
            milestone=milestone,
            cited_fact_ids=citations,
        )
        self._write_record("verification", verification)
        self.service._record_no_action_verification(
            ObjectCommand(record=verification, command_id=command_id)
        )
        return verification

    @staticmethod
    def _communication_fact(classification: str, milestone: str) -> str:
        mapping = {
            ("VERIFIED_REMEDY", "REFUND_COMMITTED_EXACT"): "REFUND_COMPLETED",
            (
                "VERIFIED_REMEDY",
                "REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED",
            ): "REPLACEMENT_OPERATIONAL_MILESTONE",
            ("VERIFIED_WAIT_CONDITION", "CURRENT_RELIABLE_ETA"): "ETA_ESTIMATE",
            (
                "VERIFIED_NO_NEW_ACTION",
                "PRIOR_REMEDY_COVERS_QUANTITY",
            ): "NO_NEW_ACTION_REQUIRED",
        }
        try:
            return mapping[(classification, milestone)]
        except KeyError as error:
            raise OrchestrationControlError(
                "active verification has no permitted communication fact"
            ) from error

    def communicate_active_verification(
        self, *, communication_id: str, command_id: str
    ) -> dict[str, Any]:
        """Derive exactly one unsent message from the active verification."""

        context = self.service.inspect()
        verification_id = context.active_object_ids.get("verification_id")
        if not verification_id:
            raise OrchestrationControlError("no active verification is available")
        verification = self._load_record(
            "verification", verification_id, "verification"
        )
        payload = verification["payload"]
        fact_code = self._communication_fact(
            payload["classification"], payload["milestone"]
        )
        communication = create_unsent_communication(
            communication_id=self._validate_id(communication_id, "communication ID"),
            case_id=context.case_id,
            case_revision=context.case_revision,
            classification=payload["classification"],
            milestone=payload["milestone"],
            fact_codes=(fact_code,),
            citations=(verification_id,),
            estimate_at=(
                context.permitted_facts.get("reliable_eta_at")
                if fact_code == "ETA_ESTIMATE"
                else None
            ),
        )
        self._write_record("communication", communication)
        self.service._record_communication(
            ObjectCommand(record=communication, command_id=command_id), verification
        )
        return communication

    def close(
        self, *, closure_id: str, command_id: str
    ) -> tuple[dict[str, Any], Any]:
        """Close only the exact active verification/communication pair."""

        context = self.service.inspect()
        verification_id = context.active_object_ids.get("verification_id")
        communication_id = context.active_object_ids.get("communication_id")
        if not verification_id or not communication_id:
            raise OrchestrationControlError(
                "closure requires active verification and communication artifacts"
            )
        verification = self._load_record(
            "verification", verification_id, "verification"
        )
        communication = self._load_record(
            "communication", communication_id, "communication"
        )
        closure_id = self._validate_id(closure_id, "closure ID")
        final = self.service._close(
            command_id=command_id,
            closure_id=closure_id,
            verification=verification,
            communication=communication,
        )
        closure = {
                "payload": {
                    "case_id": final.case_id,
                    "case_revision": final.case_revision,
                    "closure_is_customer_outcome": False,
                    "communication_digest": canonical_sha256(communication),
                    "communication_id": communication_id,
                    "final_ledger_head_digest": final.ledger_head_digest,
                    "state": final.state.value,
                    "synthetic": True,
                    "verification_digest": canonical_sha256(verification),
                    "verification_id": verification_id,
                },
                "record_id": closure_id,
                "record_type": "closure",
                "schema_version": "stage2-closure/v1",
            }
        self._write_record("closures", closure)
        return closure, final
