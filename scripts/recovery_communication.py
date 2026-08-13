#!/usr/bin/env python3
"""Evidence-bound, unsent communication records for synthetic recovery."""

from __future__ import annotations

import re
from typing import Any

from scripts.stage2_contracts import STAGE2_ID_PATTERN, validate_neutral_record


class CommunicationControlError(ValueError):
    """Raised when a message would overstate or disclose unsupported facts."""


FACTS_BY_CLASSIFICATION = {
    "VERIFIED_REMEDY": frozenset(
        {"REFUND_COMPLETED", "REPLACEMENT_OPERATIONAL_MILESTONE"}
    ),
    "VERIFIED_WAIT_CONDITION": frozenset({"ETA_ESTIMATE"}),
    "VERIFIED_NO_NEW_ACTION": frozenset({"NO_NEW_ACTION_REQUIRED"}),
}
MILESTONES_BY_FACT = {
    "REFUND_COMPLETED": "REFUND_COMMITTED_EXACT",
    "REPLACEMENT_OPERATIONAL_MILESTONE": "REPLACEMENT_CREATED_RESERVED_WMS_ACCEPTED",
    "ETA_ESTIMATE": "CURRENT_RELIABLE_ETA",
    "NO_NEW_ACTION_REQUIRED": "PRIOR_REMEDY_COVERS_QUANTITY",
}
PERSONAL_OR_SECRET = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|\bsk-[A-Za-z0-9_-]{8,}|password|secret|token)",
    re.IGNORECASE,
)


def _message(fact_code: str, estimate_at: str | None) -> str:
    if fact_code == "REFUND_COMPLETED":
        return "The exact eligible refund is recorded in the simulated payment source."
    if fact_code == "REPLACEMENT_OPERATIONAL_MILESTONE":
        return "A simulated replacement was created, its exact stock was reserved, and WMS accepted the work. Delivery is not observed."
    if fact_code == "ETA_ESTIMATE":
        if not isinstance(estimate_at, str) or not estimate_at:
            raise CommunicationControlError("an ETA estimate requires a current cited instant")
        return f"The current carrier estimate is {estimate_at}; this is an estimate, not a delivery promise."
    return "The authoritative synthetic record shows the eligible quantity is already covered, so no new action is required."


def create_unsent_communication(
    *,
    communication_id: str,
    case_id: str,
    case_revision: int,
    classification: str,
    milestone: str,
    fact_codes: tuple[str, ...],
    citations: tuple[str, ...],
    estimate_at: str | None = None,
    free_text: str | None = None,
) -> dict[str, Any]:
    """Create a local artifact from allow-listed facts; never send anything."""

    for value in (communication_id, case_id, *citations):
        if not isinstance(value, str) or not STAGE2_ID_PATTERN.fullmatch(value):
            raise CommunicationControlError("communication evidence identity is invalid")
    if isinstance(case_revision, bool) or not isinstance(case_revision, int) or case_revision < 1:
        raise CommunicationControlError("communication case revision is invalid")
    if classification not in FACTS_BY_CLASSIFICATION:
        raise CommunicationControlError("communication requires a verified supportable condition")
    if not fact_codes or len(set(fact_codes)) != len(fact_codes):
        raise CommunicationControlError("communication facts must be unique and nonempty")
    allowed = FACTS_BY_CLASSIFICATION[classification]
    if any(code not in allowed for code in fact_codes):
        raise CommunicationControlError("message contains an unsupported completion fact")
    if any(MILESTONES_BY_FACT[code] != milestone for code in fact_codes):
        raise CommunicationControlError("message fact is not bound to the verified milestone")
    if not citations:
        raise CommunicationControlError("communication requires trace-linked citations")
    if free_text is not None:
        if PERSONAL_OR_SECRET.search(free_text):
            raise CommunicationControlError("free text contains personal or secret-like material")
        raise CommunicationControlError("free-form customer text is outside the local MVP contract")
    texts = [_message(code, estimate_at) for code in fact_codes]
    record = {
        "payload": {
            "case_id": case_id,
            "case_revision": case_revision,
            "citations": list(citations),
            "classification": classification,
            "communication_channel": "LOCAL_ARTIFACT_ONLY",
            "consequential_action_count": 0 if classification != "VERIFIED_REMEDY" else 1,
            "delivery_observed": False,
            "evidence_label": "synthetic_unsent",
            "fact_codes": list(fact_codes),
            "message_text": " ".join(texts),
            "milestone": milestone,
            "send_capability": False,
            "synthetic": True,
            "unsent": True,
        },
        "record_id": communication_id,
        "record_type": "communication",
        "schema_version": "stage2-communication/v1",
    }
    try:
        return validate_neutral_record(record)
    except (TypeError, ValueError) as error:
        raise CommunicationControlError("communication record is invalid") from error
