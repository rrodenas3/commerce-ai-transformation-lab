from __future__ import annotations

import inspect
import unittest

import scripts.recovery_verification as verification_module
from scripts.recovery_actions import build_action_contract
from scripts.recovery_verification import verify_authoritative_postcondition


def _action(operation="RESHIP", *, quantity=2, amount=2500):
    return build_action_contract(
        action_id="S2-ACTION-VERIFY-0001",
        case_id="S2-CASE-VERIFY-0001",
        case_revision=1,
        ledger_head_digest="a" * 64,
        policy_id="SCC-01-RECOVERY-POLICY",
        policy_version="1.0.0",
        operation=operation,
        target="S2-ORDER-VERIFY-0001",
        eligible_business_key="S2-LINE-VERIFY-0001",
        eligible_quantity=quantity,
        amount_cents=amount,
        currency="EUR",
        before_state={"remaining_quantity": quantity},
        authority_route="DELEGATED_DECISION",
        authority_reference="S2-DECISION-VERIFY-0001",
        idempotency_key="S2-IDEMPOTENCY-VERIFY-0001",
        timeout_milliseconds=5000,
    )


class _Reader:
    def __init__(self, effects):
        self.effects = effects

    def read_committed_effects(self, source_name):
        return list(self.effects.get(source_name, ()))


def _effect(action, source, **values):
    return {
        "action_contract_digest": action["payload"]["action_contract_digest"],
        "action_id": action["record_id"],
        "case_id": action["payload"]["case_id"],
        "case_revision": action["payload"]["case_revision"],
        "eligible_business_key": action["payload"]["eligible_business_key"],
        "source_name": source,
        **values,
    }


class RecoveryVerificationTests(unittest.TestCase):
    def test_reship_requires_replacement_exact_reservation_and_wms_acceptance(self):
        action = _action()
        effects = {
            "OMS": [_effect(action, "OMS", replacement_created=True, replacement_order_id="S2-ORDER-REPLACEMENT-0001", quantity=2)],
            "INVENTORY": [_effect(action, "INVENTORY", inventory_reserved=True, quantity=2)],
            "WMS": [_effect(action, "WMS", wms_accepted=True, quantity=2)],
        }
        verified = verify_authoritative_postcondition(
            action, _Reader(effects), verification_id="S2-VERIFICATION-0001"
        )
        self.assertEqual("VERIFIED_REMEDY", verified["payload"]["classification"])
        self.assertEqual("REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED", verified["payload"]["milestone"])
        self.assertFalse(verified["payload"]["customer_delivery_observed"])

        effects["WMS"][0]["quantity"] = 1
        failed = verify_authoritative_postcondition(
            action, _Reader(effects), verification_id="S2-VERIFICATION-0002"
        )
        self.assertEqual("VERIFICATION_FAILED", failed["payload"]["classification"])

    def test_refund_rejects_wrong_currency_amount_action_or_duplicate(self):
        action = _action("REFUND", quantity=1, amount=2501)
        valid = _effect(action, "PAYMENT", amount_cents=2501, currency="EUR", quantity=1, refund_committed=True)
        verified = verify_authoritative_postcondition(
            action, _Reader({"PAYMENT": [valid]}), verification_id="S2-VERIFICATION-0003"
        )
        self.assertEqual("VERIFIED_REMEDY", verified["payload"]["classification"])
        for change in (
            {"currency": "USD"},
            {"amount_cents": 2500},
            {"action_id": "S2-ACTION-OTHER-0001"},
        ):
            changed = {**valid, **change}
            result = verify_authoritative_postcondition(
                action, _Reader({"PAYMENT": [changed]}), verification_id="S2-VERIFICATION-0004"
            )
            self.assertEqual("VERIFICATION_FAILED", result["payload"]["classification"])
        duplicate = verify_authoritative_postcondition(
            action, _Reader({"PAYMENT": [valid, dict(valid)]}), verification_id="S2-VERIFICATION-0005"
        )
        self.assertEqual("VERIFICATION_FAILED", duplicate["payload"]["classification"])

    def test_verifier_has_no_mutating_adapter_dependency_and_ignores_receipts(self):
        source = inspect.getsource(verification_module)
        self.assertNotIn("recovery_adapters", source)
        action = _action()
        forged_receipt = {"status": "SUCCESS", "verified": True}
        result = verify_authoritative_postcondition(
            action,
            _Reader({}),
            verification_id="S2-VERIFICATION-0006",
            untrusted_receipt=forged_receipt,
        )
        self.assertEqual("VERIFICATION_FAILED", result["payload"]["classification"])
        self.assertFalse(result["payload"]["adapter_receipt_trusted"])

    def test_verifier_outage_produces_no_false_verification_record(self):
        class _UnavailableReader:
            def read_committed_effects(self, source_name):
                raise RuntimeError("injected read-only verifier outage")

        with self.assertRaisesRegex(RuntimeError, "outage"):
            verify_authoritative_postcondition(
                _action(),
                _UnavailableReader(),
                verification_id="S2-VERIFICATION-OUTAGE-0001",
                untrusted_receipt={"status": "SUCCESS"},
            )


if __name__ == "__main__":
    unittest.main()
