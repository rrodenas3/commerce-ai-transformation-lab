from __future__ import annotations

import unittest

from scripts.recovery_communication import CommunicationControlError, create_unsent_communication


class RecoveryCommunicationTests(unittest.TestCase):
    def test_verified_reship_message_uses_operational_milestone_not_delivery(self):
        record = create_unsent_communication(
            communication_id="S2-COMMUNICATION-0001",
            case_id="S2-CASE-0003",
            case_revision=1,
            classification="VERIFIED_REMEDY",
            milestone="REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED",
            fact_codes=("REPLACEMENT_OPERATIONAL_MILESTONE",),
            citations=("S2-VERIFICATION-0001",),
        )
        payload = record["payload"]
        self.assertTrue(payload["unsent"])
        self.assertFalse(payload["delivery_observed"])
        self.assertNotIn("delivered", payload["message_text"].lower())
        self.assertEqual("synthetic_unsent", payload["evidence_label"])

    def test_completion_before_verification_and_personal_or_secret_text_fail(self):
        invalid = (
            dict(classification="VERIFICATION_FAILED", milestone="PENDING", fact_codes=("REFUND_COMPLETED",)),
            dict(classification="VERIFIED_REMEDY", milestone="REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED", fact_codes=("DELIVERY_COMPLETED",)),
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(CommunicationControlError):
                    create_unsent_communication(
                        communication_id="S2-COMMUNICATION-0002",
                        case_id="S2-CASE-0003",
                        case_revision=1,
                        citations=("S2-VERIFICATION-0001",),
                        **values,
                    )
        with self.assertRaises(CommunicationControlError):
            create_unsent_communication(
                communication_id="S2-COMMUNICATION-0003",
                case_id="S2-CASE-0003",
                case_revision=1,
                classification="VERIFIED_REMEDY",
                milestone="REFUND_COMMITTED_EXACT",
                fact_codes=("REFUND_COMPLETED",),
                citations=("S2-VERIFICATION-0001",),
                free_text="Contact jane@example.com with token sk-secretvalue",
            )

    def test_wait_and_no_new_action_are_distinct_unsent_conditions(self):
        wait = create_unsent_communication(
            communication_id="S2-COMMUNICATION-WAIT-0001",
            case_id="S2-CASE-0001",
            case_revision=1,
            classification="VERIFIED_WAIT_CONDITION",
            milestone="CURRENT_RELIABLE_ETA",
            fact_codes=("ETA_ESTIMATE",),
            citations=("S2-RECORD-CARRIER-0001", "S2-RECORD-CRM-0001"),
            estimate_at="2026-08-12T17:00:00+02:00",
        )
        no_action = create_unsent_communication(
            communication_id="S2-COMMUNICATION-NO-ACTION-0001",
            case_id="S2-CASE-0013",
            case_revision=1,
            classification="VERIFIED_NO_NEW_ACTION",
            milestone="PRIOR_REMEDY_COVERS_QUANTITY",
            fact_codes=("NO_NEW_ACTION_REQUIRED",),
            citations=("S2-RECORD-OMS-0013",),
        )
        self.assertIn("estimate", wait["payload"]["message_text"].lower())
        self.assertIn("no new", no_action["payload"]["message_text"].lower())
        self.assertEqual(0, wait["payload"]["consequential_action_count"])
        self.assertEqual(0, no_action["payload"]["consequential_action_count"])


if __name__ == "__main__":
    unittest.main()
