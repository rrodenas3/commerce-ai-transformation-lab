#!/usr/bin/env python3
"""Allow-listed local simulated adapters with durable effect reconciliation.

The adapter has no network, subprocess, arbitrary path, customer-message, or
live-system surface.  Source effects are append-only and become authoritative
only when their separate write-once commit marker exists.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.recovery_actions import ActionControlError, validate_action_contract
from scripts.recovery_workspace import (
    FileRecoveryWorkspace,
    WorkspaceIntegrityError,
    _RunWriter,
    _sha,
)
from scripts.stage2_contracts import ContractValidationError, canonical_json_bytes, load_canonical_json


ADAPTER_VERSION = "local-simulated-recovery-adapter/1.0.0"
ALLOW_LIST = frozenset({"REFUND", "RESHIP"})
SOURCE_BY_OPERATION = {
    "REFUND": ("PAYMENT",),
    "RESHIP": ("OMS", "INVENTORY", "WMS"),
}


class AdapterControlError(RuntimeError):
    """Raised when an adapter request is not reserved, exact, or idempotent."""


class EffectOutcomeUnknown(AdapterControlError):
    """Raised when an effect may exist and reconciliation must precede retry."""


class LocalSimulatedActionAdapter:
    """Mutate only the isolated source-effect journals owned by one workspace."""

    def __init__(self, workspace: FileRecoveryWorkspace):
        if not isinstance(workspace, FileRecoveryWorkspace):
            raise AdapterControlError("local adapter requires an isolated file workspace")
        self.workspace = workspace

    @staticmethod
    def _lines(
        payload: bytes,
        label: str,
        *,
        ignore_uncommitted_partial_tail: bool = False,
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for line in payload.splitlines(keepends=True):
            if ignore_uncommitted_partial_tail and not line.endswith(b"\n"):
                continue
            try:
                value = load_canonical_json(line)
            except ContractValidationError as error:
                raise AdapterControlError(f"{label} journal is not canonical") from error
            if not isinstance(value, dict):
                raise AdapterControlError(f"{label} journal entry is invalid")
            values.append(value)
        return values

    def _attempts(self) -> list[dict[str, Any]]:
        values, _, suffix = self._attempt_journal_state()
        if suffix:
            raise AdapterControlError("action-attempt journal contains an uncommitted suffix")
        return values

    def _attempt_journal_state(self) -> tuple[list[dict[str, Any]], int, bytes]:
        relative = "action-attempts/journal.jsonl"
        raw = self.workspace.authority.read_bytes(relative)
        committed: list[dict[str, Any]] = []
        boundary = 0
        suffix_started = False
        for index, line in enumerate(raw.splitlines(keepends=True), 1):
            if not line.endswith(b"\n"):
                suffix_started = True
                break
            try:
                value = load_canonical_json(line)
            except ContractValidationError as error:
                raise AdapterControlError("action-attempt journal is not canonical") from error
            if not isinstance(value, dict) or value.get("attempt_sequence") != index:
                raise AdapterControlError("action-attempt sequence is invalid")
            marker_path = f"action-attempts/commits/{index:06d}.json"
            marker_exists = (self.workspace.run_root / marker_path).exists()
            if not marker_exists:
                suffix_started = True
            elif suffix_started:
                raise AdapterControlError("action-attempt marker appears after an uncommitted suffix")
            else:
                try:
                    marker = load_canonical_json(
                        self.workspace.authority.read_bytes(marker_path)
                    )
                except (WorkspaceIntegrityError, ContractValidationError) as error:
                    raise AdapterControlError("action-attempt marker is invalid") from error
                if marker != {
                    "attempt_digest": _sha(value),
                    "attempt_sequence": index,
                    "schema_version": "stage2-action-attempt-commit/v1",
                }:
                    raise AdapterControlError("action-attempt commit marker does not bind entry")
                committed.append(value)
                boundary += len(line)
        expected_markers = {
            f"{index:06d}.json" for index in range(1, len(committed) + 1)
        }
        actual_markers = {
            path.name for path in (self.workspace.run_root / "action-attempts" / "commits").glob("*.json")
        }
        if actual_markers != expected_markers:
            raise AdapterControlError("action-attempt marker inventory is not a committed prefix")
        return committed, boundary, raw[boundary:]

    def _recover_attempt_suffix_locked(self) -> str | None:
        _, boundary, suffix = self._attempt_journal_state()
        if not suffix:
            return None
        digest = _sha(suffix)
        (self.workspace.run_root / "quarantine").mkdir(exist_ok=True)
        self.workspace.authority.write_once(
            f"quarantine/action-attempt-tail-{digest[:16]}.bin", suffix
        )
        self.workspace.authority.truncate_durable(
            "action-attempts/journal.jsonl", boundary
        )
        return digest

    def _append_attempt(self, *, action: Mapping[str, Any], status: str, reconciled: bool) -> None:
        attempts = self._attempts()
        payload = action["payload"]
        entry = {
            "action_contract_digest": payload["action_contract_digest"],
            "action_id": payload["action_id"],
            "attempt_sequence": len(attempts) + 1,
            "idempotency_key": payload["idempotency_key"],
            "reconciled_before_retry": reconciled,
            "schema_version": "stage2-action-attempt/v1",
            "status": status,
            "synthetic": True,
        }
        self.workspace.authority.append_durable(
            "action-attempts/journal.jsonl", canonical_json_bytes(entry)
        )
        self.workspace.authority.write_once(
            f"action-attempts/commits/{entry['attempt_sequence']:06d}.json",
            canonical_json_bytes(
                {
                    "attempt_digest": _sha(entry),
                    "attempt_sequence": entry["attempt_sequence"],
                    "schema_version": "stage2-action-attempt-commit/v1",
                }
            ),
        )

    def committed_effects(self, source_name: str) -> list[dict[str, Any]]:
        committed, _, _ = self._effect_journal_state(source_name)
        return committed

    def _effect_journal_state(
        self, source_name: str
    ) -> tuple[list[dict[str, Any]], int, bytes]:
        if source_name not in {"PAYMENT", "OMS", "INVENTORY", "WMS"}:
            raise AdapterControlError("source is not allow-listed")
        relative = f"source-effects/{source_name}.jsonl"
        raw = self.workspace.authority.read_bytes(relative)
        committed: list[dict[str, Any]] = []
        boundary = 0
        suffix_started = False
        seen_action_ids: set[str] = set()
        committed_marker_names: set[str] = set()
        for sequence, line in enumerate(raw.splitlines(keepends=True), 1):
            if not line.endswith(b"\n"):
                suffix_started = True
                break
            try:
                value = load_canonical_json(line)
            except ContractValidationError as error:
                raise AdapterControlError("source-effect journal is not canonical") from error
            if not isinstance(value, dict) or value.get("source_name") != source_name:
                raise AdapterControlError("source-effect journal entry is invalid")
            action_id = value.get("action_id")
            if not isinstance(action_id, str):
                raise AdapterControlError("source-effect action identity is invalid")
            if action_id in seen_action_ids:
                raise AdapterControlError("source-effect action identity is duplicated")
            seen_action_ids.add(action_id)
            marker_path = f"source-effects/commits/{source_name}/{action_id}.json"
            marker_exists = (self.workspace.run_root / marker_path).exists()
            if not marker_exists:
                suffix_started = True
                continue
            if suffix_started:
                raise AdapterControlError("source-effect marker appears after an uncommitted suffix")
            try:
                marker = load_canonical_json(
                    self.workspace.authority.read_bytes(marker_path)
                )
            except (WorkspaceIntegrityError, ContractValidationError) as error:
                raise AdapterControlError("source-effect marker is invalid") from error
            expected = {
                "action_id": action_id,
                "effect_digest": _sha(value),
                "effect_sequence": sequence,
                "schema_version": "stage2-source-effect-commit/v1",
                "source_name": source_name,
            }
            if value.get("effect_sequence") != sequence or marker != expected:
                raise AdapterControlError("source-effect marker does not bind the effect")
            committed.append(value)
            committed_marker_names.add(f"{action_id}.json")
            boundary += len(line)
        actual_marker_names = {
            path.name
            for path in (
                self.workspace.run_root / "source-effects" / "commits" / source_name
            ).glob("*.json")
        }
        if actual_marker_names != committed_marker_names:
            raise AdapterControlError("source-effect marker inventory is not a committed prefix")
        return committed, boundary, raw[boundary:]

    def _reservation(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = action["payload"]
        matches = []
        for event in self.workspace.read_events():
            event_payload = event["payload"]
            material = event_payload["decision_or_effect"]
            if (
                event_payload["event_type"] == "ACTION_AUTHORITY_RESERVED"
                and event_payload["links"].get("action_id") == payload["action_id"]
            ):
                matches.append(material)
        if len(matches) != 1 or matches[0].get("action_contract_digest") != payload["action_contract_digest"]:
            raise AdapterControlError("adapter action lacks one exact atomic reservation")
        return matches[0]

    @staticmethod
    def _effect(action: Mapping[str, Any], source_name: str) -> dict[str, Any]:
        payload = action["payload"]
        base: dict[str, Any] = {
            "action_contract_digest": payload["action_contract_digest"],
            "action_id": payload["action_id"],
            "case_id": payload["case_id"],
            "case_revision": payload["case_revision"],
            "eligible_business_key": payload["eligible_business_key"],
            "source_name": source_name,
            "synthetic": True,
        }
        if source_name == "PAYMENT":
            base.update(
                amount_cents=payload["amount_cents"],
                currency=payload["currency"],
                quantity=payload["eligible_quantity"],
                refund_committed=True,
            )
        elif source_name == "OMS":
            base.update(
                quantity=payload["eligible_quantity"],
                replacement_created=True,
                replacement_order_id=f"S2-ORDER-REPLACEMENT-{payload['action_id'][10:]}",
            )
        elif source_name == "INVENTORY":
            base.update(inventory_reserved=True, quantity=payload["eligible_quantity"])
        elif source_name == "WMS":
            base.update(quantity=payload["eligible_quantity"], wms_accepted=True)
        return base

    def _append_effect(self, effect: Mapping[str, Any]) -> None:
        source = effect["source_name"]
        action_id = effect["action_id"]
        committed, _, suffix = self._effect_journal_state(source)
        if suffix:
            raise EffectOutcomeUnknown("source-effect suffix requires quarantine before retry")
        if any(item.get("action_id") == action_id for item in committed):
            return
        sequenced = dict(effect)
        sequenced["effect_sequence"] = len(committed) + 1
        self.workspace.authority.append_durable(
            f"source-effects/{source}.jsonl", canonical_json_bytes(sequenced)
        )
        self.workspace.authority.write_once(
            f"source-effects/commits/{source}/{action_id}.json",
            canonical_json_bytes(
                {
                    "action_id": action_id,
                    "effect_digest": _sha(sequenced),
                    "effect_sequence": sequenced["effect_sequence"],
                    "schema_version": "stage2-source-effect-commit/v1",
                    "source_name": source,
                }
            ),
        )

    def recover_source_effect_tail(self, source_name: str) -> str | None:
        """Quarantine one uncommitted effect suffix; never rewrite committed effects."""

        if source_name not in {"PAYMENT", "OMS", "INVENTORY", "WMS"}:
            raise AdapterControlError("source is not allow-listed")
        with _RunWriter(self.workspace.authority):
            return self._recover_source_effect_suffix_locked(source_name)

    def _recover_source_effect_suffix_locked(self, source_name: str) -> str | None:
        _, boundary, suffix = self._effect_journal_state(source_name)
        if not suffix:
            return None
        digest = _sha(suffix)
        (self.workspace.run_root / "quarantine").mkdir(exist_ok=True)
        self.workspace.authority.write_once(
            f"quarantine/{source_name.lower()}-effect-tail-{digest[:16]}.bin",
            suffix,
        )
        self.workspace.authority.truncate_durable(
            f"source-effects/{source_name}.jsonl", boundary
        )
        return digest

    def _receipt_path(self, action_id: str) -> str:
        return f"receipts/{action_id}.json"

    def _read_receipt(self, action_id: str) -> dict[str, Any] | None:
        path = self.workspace.run_root / self._receipt_path(action_id)
        if not path.exists():
            return None
        try:
            value = load_canonical_json(self.workspace.authority.read_bytes(self._receipt_path(action_id)))
        except (WorkspaceIntegrityError, ContractValidationError) as error:
            raise AdapterControlError("adapter receipt is not canonical") from error
        if not isinstance(value, dict):
            raise AdapterControlError("adapter receipt is invalid")
        return value

    @staticmethod
    def _receipt(action: Mapping[str, Any], *, reconciled: bool) -> dict[str, Any]:
        payload = action["payload"]
        return {
            "payload": {
                "action_contract_digest": payload["action_contract_digest"],
                "action_id": payload["action_id"],
                "adapter_version": ADAPTER_VERSION,
                "effect_sources": list(SOURCE_BY_OPERATION[payload["operation"]]),
                "operation": payload["operation"],
                "reconciled_before_retry": reconciled,
                "status": "SIMULATED_EFFECT_COMMITTED",
                "synthetic": True,
                "verification_authority": False,
            },
            "record_id": f"S2-RECEIPT-{payload['action_id'][10:]}",
            "record_type": "adapter_receipt",
            "schema_version": "stage2-adapter-receipt/v1",
        }

    def execute(self, action: Mapping[str, Any], *, fault: str | None = None) -> dict[str, Any]:
        """Execute or reconcile one reserved action without blind retry."""

        try:
            record = validate_action_contract(action)
        except ActionControlError as error:
            raise AdapterControlError("adapter action contract is invalid") from error
        payload = record["payload"]
        if payload["operation"] not in ALLOW_LIST:
            raise AdapterControlError("adapter operation is not allow-listed")
        allowed_faults = {
            None,
            "before_mutation",
            "timeout_before_mutation",
            "after_first_effect",
            "after_mutation_before_receipt",
            "timeout_after_mutation",
            "receipt_persistence_failure",
        }
        if fault not in allowed_faults:
            raise AdapterControlError("unknown injected adapter fault")
        with _RunWriter(self.workspace.authority):
            # Marker creation is the linearisation point.  A crash can leave a
            # complete or torn uncommitted suffix; recover it before any read,
            # idempotency decision, or retry is allowed to append.
            self._recover_attempt_suffix_locked()
            for source_name in ("PAYMENT", "OMS", "INVENTORY", "WMS"):
                self._recover_source_effect_suffix_locked(source_name)
            self._reservation(record)
            existing_receipt = self._read_receipt(payload["action_id"])
            if existing_receipt is not None:
                if existing_receipt["payload"].get("action_contract_digest") != payload["action_contract_digest"]:
                    raise AdapterControlError("receipt identity conflicts with action payload")
                return existing_receipt
            attempts = self._attempts()
            same_key = [item for item in attempts if item.get("idempotency_key") == payload["idempotency_key"]]
            if any(item.get("action_contract_digest") != payload["action_contract_digest"] for item in same_key):
                raise AdapterControlError("same idempotency key has a different payload")
            for source in ("PAYMENT", "OMS", "INVENTORY", "WMS"):
                for effect in self.committed_effects(source):
                    if (
                        effect.get("case_id") == payload["case_id"]
                        and effect.get("eligible_business_key") == payload["eligible_business_key"]
                        and effect.get("action_id") != payload["action_id"]
                    ):
                        raise AdapterControlError("eligible quantity already has a committed remedy")
            required_sources = SOURCE_BY_OPERATION[payload["operation"]]
            observed = {
                source: [item for item in self.committed_effects(source) if item.get("action_id") == payload["action_id"]]
                for source in required_sources
            }
            present = {source for source, values in observed.items() if values}
            if present and present != set(required_sources):
                self._append_attempt(action=record, status="PARTIAL_EFFECT_REQUIRES_RECOVERY", reconciled=True)
                raise EffectOutcomeUnknown("partial effect requires explicit compensation/recovery")
            reconciled = bool(same_key or present)
            if present == set(required_sources):
                receipt = self._receipt(record, reconciled=True)
                self.workspace.authority.write_once(
                    self._receipt_path(payload["action_id"]), canonical_json_bytes(receipt)
                )
                self._append_attempt(action=record, status="RECONCILED_COMMITTED_EFFECT", reconciled=True)
                return receipt
            self._append_attempt(action=record, status="EFFECT_STARTED", reconciled=reconciled)
            if fault in {"before_mutation", "timeout_before_mutation"}:
                self._append_attempt(action=record, status="FAILED_BEFORE_MUTATION", reconciled=False)
                raise AdapterControlError("injected failure before source mutation")
            for index, source in enumerate(required_sources):
                self._append_effect(self._effect(record, source))
                if fault == "after_first_effect" and index == 0:
                    self._append_attempt(action=record, status="EFFECT_UNKNOWN", reconciled=False)
                    raise EffectOutcomeUnknown("injected failure after a partial source effect")
            if fault in {
                "after_mutation_before_receipt",
                "timeout_after_mutation",
                "receipt_persistence_failure",
            }:
                self._append_attempt(action=record, status="EFFECT_UNKNOWN", reconciled=False)
                raise EffectOutcomeUnknown("injected lost receipt after committed source effect")
            receipt = self._receipt(record, reconciled=reconciled)
            self.workspace.authority.write_once(
                self._receipt_path(payload["action_id"]), canonical_json_bytes(receipt)
            )
            self._append_attempt(action=record, status="SIMULATED_EFFECT_COMMITTED", reconciled=reconciled)
            return receipt
