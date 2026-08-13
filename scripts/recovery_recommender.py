#!/usr/bin/env python3
"""Bounded, provider-neutral recommendation parsing and recorded evidence.

This adapter owns no policy, authority, workspace, action, evaluator, or
publication capability.  Raw provider bytes are preserved by digest and must
pass one preregistered envelope before they can become a candidate.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.stage2_contracts import (
    EVALUATOR_ONLY_FIELDS,
    STAGE2_ID_PATTERN,
    ContractValidationError,
    canonical_json_bytes,
    load_canonical_json,
)


class ProviderBoundaryError(ValueError):
    """Provider bytes failed before entering governed policy."""


class ProviderTimeoutError(ProviderBoundaryError):
    """The frozen provider deadline elapsed before parsing began."""


class CandidateValidationError(ProviderBoundaryError):
    """A structurally parsed candidate violated the provider schema."""


class AttemptLedgerError(ProviderBoundaryError):
    """Recorded provider evidence is incomplete, reordered, or changed."""


@dataclass(frozen=True)
class ProviderEnvelope:
    max_request_bytes: int = 32768
    max_response_bytes: int = 16384
    max_nesting_depth: int = 8
    max_collection_items: int = 32
    max_string_characters: int = 1024
    timeout_milliseconds: int = 10000

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderEnvelope":
        expected = {
            "max_collection_items",
            "max_nesting_depth",
            "max_request_bytes",
            "max_response_bytes",
            "max_string_characters",
            "timeout_milliseconds",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ProviderBoundaryError("provider envelope fields are not exact")
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value.values()):
            raise ProviderBoundaryError("provider envelope limits must be positive integers")
        return cls(**value)

    def to_dict(self) -> dict[str, int]:
        return {
            "max_collection_items": self.max_collection_items,
            "max_nesting_depth": self.max_nesting_depth,
            "max_request_bytes": self.max_request_bytes,
            "max_response_bytes": self.max_response_bytes,
            "max_string_characters": self.max_string_characters,
            "timeout_milliseconds": self.timeout_milliseconds,
        }


DEFAULT_PROVIDER_ENVELOPE = ProviderEnvelope()

_CANDIDATE_FIELDS = {
    "candidate_id",
    "case_id",
    "case_revision",
    "cited_evidence",
    "material_limitations",
    "message_fact_candidates",
    "proposed_action",
    "proposed_route",
    "rejected_alternatives",
    "schema_version",
    "uncertainty",
}
_ACTIONS = {
    "ACTION_RECOVERY",
    "AWAITING_CHOICE",
    "CONTROL_STOP",
    "EVIDENCE_BLOCKED",
    "NO_NEW_ACTION",
    "REFUND",
    "RESHIP",
    "WAIT_VERIFIED_ETA",
}
_ROUTES = {
    "AWAITING_CHOICE",
    "DELEGATED_DECISION",
    "DIRECT_NO_ACTION",
    "EVIDENCE_REVIEW",
    "FINANCE_APPROVAL",
    "RECOVERY_RECONCILIATION",
    "SPECIALIST_STOP",
    "WORKFLOW_OWNER_APPROVAL",
}
_MESSAGE_FACTS = {
    "ACTION_STATUS_UNRESOLVED",
    "CUSTOMER_CHOICE_REQUIRED",
    "DUPLICATE_SIGNAL_SUPPRESSED",
    "EVIDENCE_UNRESOLVED",
    "PRIOR_REMEDY_CONFIRMED",
    "NO_ELIGIBLE_QUANTITY_REMAINS",
    "REFUND_PROPOSED",
    "RESHIP_PROPOSED",
    "SPECIALIST_REVIEW_REQUIRED",
    "WAIT_ESTIMATE_QUALIFIED",
}
_TERMINAL_STATUSES = {
    "SUCCESS",
    "REFUSAL",
    "TIMEOUT",
    "MALFORMED",
    "REJECTED",
    "FALLBACK",
    "UNAVAILABLE",
}
_BIDI_PATTERN = re.compile("[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_INSTRUCTION_PATTERNS = (
    "ignore previous",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "tool_call",
    "invoke adapter",
    "call adapter",
    "change policy",
    "modify policy",
    "mint approval",
    "approve this action",
    "read oracle",
    "access oracle",
    "publish externally",
)
_REQUEST_FIELDS = {
    "active_object_ids",
    "allowed_next_transitions",
    "case_id",
    "case_revision",
    "cited_sources",
    "evidence_gaps",
    "ledger_head_digest",
    "permitted_facts",
    "policy_authority_projection",
    "revision_pin_sha256",
    "run_id",
    "source_event_cut_sha256",
    "state",
}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _deadline(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise ProviderTimeoutError("provider deadline elapsed before parsing")


def _reject_evaluator(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in EVALUATOR_ONLY_FIELDS:
                raise ProviderBoundaryError("provider context contains evaluator-only data")
            _reject_evaluator(item)
    elif isinstance(value, list):
        for item in value:
            _reject_evaluator(item)


def _preflight_json_text(payload: bytes, envelope: ProviderEnvelope) -> str:
    """Bound raw bytes and JSON lexical complexity before object construction."""

    if not isinstance(payload, bytes):
        raise ProviderBoundaryError("provider response must be bytes")
    if len(payload) > envelope.max_response_bytes:
        raise ProviderBoundaryError("provider response exceeds byte envelope")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderBoundaryError("provider response is not valid UTF-8") from error
    if _BIDI_PATTERN.search(text):
        raise ProviderBoundaryError("provider response contains bidirectional control text")

    stack: list[dict[str, int | bool]] = []
    in_string = False
    escaped = False
    string_length = 0
    index = 0
    while index < len(text):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
                if character == "u":
                    digits = text[index + 1 : index + 5]
                    if len(digits) != 4 or any(item not in "0123456789abcdefABCDEF" for item in digits):
                        raise ProviderBoundaryError("provider response contains an invalid Unicode escape")
                    decoded = chr(int(digits, 16))
                    if ord(decoded) < 32 or _BIDI_PATTERN.search(decoded):
                        raise ProviderBoundaryError("provider string contains a disallowed control character")
                    index += 4
                elif character in "bfnrt":
                    raise ProviderBoundaryError("provider string contains an escaped control character")
                elif character not in {'"', "\\", "/"}:
                    raise ProviderBoundaryError("provider string contains an invalid escape")
                string_length += 1
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            else:
                if ord(character) < 32:
                    raise ProviderBoundaryError("provider string contains a raw control character")
                string_length += 1
            if string_length > envelope.max_string_characters:
                raise ProviderBoundaryError("provider string exceeds character envelope")
            index += 1
            continue
        if character == '"':
            in_string = True
            string_length = 0
        elif character in "[{":
            stack.append({"commas": 0, "content": False})
            if len(stack) > envelope.max_nesting_depth:
                raise ProviderBoundaryError("provider response exceeds nesting envelope")
        elif character in "]}":
            if not stack:
                raise ProviderBoundaryError("provider response has unbalanced JSON structure")
            frame = stack.pop()
            item_count = int(frame["commas"]) + (1 if frame["content"] else 0)
            if item_count > envelope.max_collection_items:
                raise ProviderBoundaryError("provider collection exceeds cardinality envelope")
        elif character == "," and stack:
            stack[-1]["commas"] = int(stack[-1]["commas"]) + 1
            if int(stack[-1]["commas"]) + 1 > envelope.max_collection_items:
                raise ProviderBoundaryError("provider collection exceeds cardinality envelope")
        elif not character.isspace() and stack:
            stack[-1]["content"] = True
        index += 1
    if in_string or stack:
        raise ProviderBoundaryError("provider response has incomplete JSON structure")
    return text


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CandidateValidationError("provider response contains a duplicate key")
        result[key] = value
    return result


def _reject_float(_: str) -> Any:
    raise CandidateValidationError("provider response contains a floating-point number")


def _reject_nonfinite(_: str) -> Any:
    raise CandidateValidationError("provider response contains a non-finite number")


def _check_constructed_limits(value: Any, envelope: ProviderEnvelope) -> None:
    if isinstance(value, str):
        if len(value) > envelope.max_string_characters:
            raise ProviderBoundaryError("provider string exceeds character envelope")
        if any(ord(character) < 32 for character in value) or _BIDI_PATTERN.search(value):
            raise ProviderBoundaryError("provider string contains disallowed control text")
    elif isinstance(value, Mapping):
        if len(value) > envelope.max_collection_items:
            raise ProviderBoundaryError("provider object exceeds cardinality envelope")
        for key, item in value.items():
            _check_constructed_limits(key, envelope)
            _check_constructed_limits(item, envelope)
    elif isinstance(value, list):
        if len(value) > envelope.max_collection_items:
            raise ProviderBoundaryError("provider list exceeds cardinality envelope")
        for item in value:
            _check_constructed_limits(item, envelope)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateValidationError(f"{field} must be a nonempty string")
    lowered = value.casefold()
    if any(pattern in lowered for pattern in _INSTRUCTION_PATTERNS):
        raise CandidateValidationError(f"{field} contains instruction or excessive-agency text")
    return value


def _code_list(value: Any, field: str, allowed: set[str] | None = None, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise CandidateValidationError(f"{field} must be a{' nonempty' if nonempty else ''} list")
    if any(not isinstance(item, str) or not item for item in value):
        raise CandidateValidationError(f"{field} must contain nonempty strings")
    if len(value) != len(set(value)):
        raise CandidateValidationError(f"{field} cannot contain duplicates")
    for item in value:
        _string(item, field)
    if allowed is not None and not set(value).issubset(allowed):
        raise CandidateValidationError(f"{field} contains an unknown code")
    return value


def _validate_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CANDIDATE_FIELDS:
        raise CandidateValidationError("provider candidate fields are not exact")
    if value["schema_version"] != "stage2-provider-candidate/v1":
        raise CandidateValidationError("provider candidate schema version is unsupported")
    for field in ("candidate_id", "case_id"):
        item = _string(value[field], field)
        if not STAGE2_ID_PATTERN.fullmatch(item):
            raise CandidateValidationError(f"{field} is not a canonical synthetic ID")
    revision = value["case_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise CandidateValidationError("case_revision must be a positive integer")
    citations = _code_list(value["cited_evidence"], "cited_evidence", nonempty=True)
    if any(not STAGE2_ID_PATTERN.fullmatch(item) or not item.startswith("S2-SRC-") for item in citations):
        raise CandidateValidationError("cited_evidence contains a noncanonical source ID")
    _code_list(value["material_limitations"], "material_limitations", nonempty=True)
    _code_list(value["message_fact_candidates"], "message_fact_candidates", _MESSAGE_FACTS, nonempty=True)
    _code_list(value["rejected_alternatives"], "rejected_alternatives", _ACTIONS)
    if value["proposed_action"] not in _ACTIONS:
        raise CandidateValidationError("proposed_action is unknown")
    if value["proposed_route"] not in _ROUTES:
        raise CandidateValidationError("proposed_route is unknown")
    if value["uncertainty"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise CandidateValidationError("uncertainty is unknown")
    _reject_evaluator(value)
    return dict(value)


def validate_candidate_context(
    candidate: Mapping[str, Any], context: Mapping[str, Any]
) -> None:
    """Bind a parsed proposal to the exact case and cited permitted evidence."""

    if not isinstance(candidate, Mapping) or not isinstance(context, Mapping):
        raise CandidateValidationError("candidate context binding requires objects")
    if (
        candidate.get("case_id") != context.get("case_id")
        or candidate.get("case_revision") != context.get("case_revision")
    ):
        raise CandidateValidationError("provider candidate belongs to a foreign case revision")
    sources = context.get("cited_sources")
    if not isinstance(sources, list):
        raise CandidateValidationError("permitted citation set is unavailable")
    permitted_ids = {
        source.get("record_id") for source in sources if isinstance(source, Mapping)
    }
    citations = candidate.get("cited_evidence")
    if not isinstance(citations, list) or not citations or not set(citations).issubset(permitted_ids):
        raise CandidateValidationError("provider candidate cites unsupported evidence")


def _validate_request_value(
    value: Any, envelope: ProviderEnvelope, *, depth: int = 1
) -> None:
    if depth > envelope.max_nesting_depth:
        raise ProviderBoundaryError("provider request exceeds nesting envelope")
    if isinstance(value, str):
        if len(value) > envelope.max_string_characters:
            raise ProviderBoundaryError("provider request string exceeds character envelope")
        if any(ord(character) < 32 for character in value) or _BIDI_PATTERN.search(value):
            raise ProviderBoundaryError("provider request contains disallowed control text")
        lowered = value.casefold()
        if any(pattern in lowered for pattern in _INSTRUCTION_PATTERNS):
            raise ProviderBoundaryError("provider request contains instruction/data ambiguity")
    elif isinstance(value, Mapping):
        if len(value) > envelope.max_collection_items:
            raise ProviderBoundaryError("provider request object exceeds cardinality envelope")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderBoundaryError("provider request keys must be strings")
            _validate_request_value(key, envelope, depth=depth + 1)
            _validate_request_value(item, envelope, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > envelope.max_collection_items:
            raise ProviderBoundaryError("provider request list exceeds cardinality envelope")
        for item in value:
            _validate_request_value(item, envelope, depth=depth + 1)
    elif value is not None and not isinstance(value, (bool, int)):
        raise ProviderBoundaryError("provider request contains unsupported numeric or object data")


def parse_candidate(
    payload: bytes,
    envelope: ProviderEnvelope = DEFAULT_PROVIDER_ENVELOPE,
    *,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Parse bounded bytes with timeout, ambiguity, and schema rejection."""

    _deadline(deadline_monotonic)
    text = _preflight_json_text(payload, envelope)
    _deadline(deadline_monotonic)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_nonfinite,
        )
    except ProviderBoundaryError:
        raise
    except (json.JSONDecodeError, RecursionError) as error:
        raise CandidateValidationError("provider response is malformed JSON") from error
    _deadline(deadline_monotonic)
    _check_constructed_limits(value, envelope)
    return _validate_candidate(value)


def canonical_provider_request(
    context: Mapping[str, Any],
    envelope: ProviderEnvelope = DEFAULT_PROVIDER_ENVELOPE,
) -> bytes:
    """Project only operator-permitted context into the provider request."""

    if hasattr(context, "to_dict"):
        context = context.to_dict()  # type: ignore[assignment]
    if not isinstance(context, Mapping):
        raise ProviderBoundaryError("provider context must be an object")
    _reject_evaluator(context)
    required = {
        "allowed_next_transitions",
        "case_id",
        "case_revision",
        "cited_sources",
        "evidence_gaps",
        "ledger_head_digest",
        "permitted_facts",
        "policy_authority_projection",
        "revision_pin_sha256",
        "source_event_cut_sha256",
    }
    if not required.issubset(context) or not set(context).issubset(_REQUEST_FIELDS | {"frozen", "invalidated_object_ids", "sequence"}):
        raise ProviderBoundaryError("provider context fields are incomplete or excessive")
    projection = {key: context[key] for key in sorted(set(context) & _REQUEST_FIELDS)}
    _validate_request_value(projection, envelope)
    try:
        payload = canonical_json_bytes(
            {
                "context": projection,
                "provider_capabilities": {
                    "action_adapter": False,
                    "approval_minting": False,
                    "evaluation_material": False,
                    "external_publication": False,
                    "policy_change": False,
                    "source_mutation": False,
                },
                "schema_version": "stage2-provider-request/v1",
            }
        )
    except ContractValidationError as error:
        raise ProviderBoundaryError("provider request cannot be canonicalised") from error
    if len(payload) > envelope.max_request_bytes:
        raise ProviderBoundaryError("provider request exceeds byte envelope")
    return payload


@dataclass(frozen=True)
class ProviderAttemptResult:
    attempt_id: str
    terminal_status: str
    validation_result: str
    candidate: Mapping[str, Any] | None
    fallback_disposition: str
    raw_response_sha256: str | None


def _load(path: Path, name: str) -> Any:
    try:
        return load_canonical_json(path.read_bytes())
    except (OSError, ContractValidationError) as error:
        raise AttemptLedgerError(f"{name} is unavailable or noncanonical") from error


def _safe_artifact(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str):
        raise AttemptLedgerError("recorded artifact path must be a relative string")
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise AttemptLedgerError("recorded artifact path escapes the provider set")
    candidate = root.joinpath(path)
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise AttemptLedgerError("recorded artifact is unavailable or outside the provider set") from error
    if candidate.is_symlink() or not candidate.is_file():
        raise AttemptLedgerError("recorded artifact must be a regular file")
    return candidate


def _read_attempts(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise AttemptLedgerError("attempt ledger is unavailable") from error
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines(keepends=True):
        try:
            row = load_canonical_json(line)
        except ContractValidationError as error:
            raise AttemptLedgerError("attempt ledger contains a noncanonical row") from error
        if not isinstance(row, dict):
            raise AttemptLedgerError("attempt ledger rows must be objects")
        rows.append(row)
    return rows, raw


def validate_attempt_set(root: Path) -> dict[str, Any]:
    """Prove exact permitted-attempt accounting and artifact provenance."""

    root = Path(root)
    contract_path = _safe_artifact(root, "acquisition-contract.json")
    manifest_path = _safe_artifact(root, "manifest.json")
    attempts_path = _safe_artifact(root, "attempts.jsonl")
    contract = _load(contract_path, "acquisition contract")
    manifest = _load(manifest_path, "provider manifest")
    if not isinstance(contract, dict) or set(contract) != {
        "assisted_denominator",
        "attempt_policy",
        "authorship_disclosure",
        "canonical_assisted_evidence",
        "contract_frozen_at",
        "contract_id",
        "deterministic_fixtures_are_canonical",
        "envelope",
        "instruction_digest",
        "parameters",
        "provider",
        "schema_version",
    }:
        raise AttemptLedgerError("acquisition contract fields are not exact")
    if contract["schema_version"] != "stage2-provider-acquisition/v1" or contract["contract_id"] != "S2-PROVIDER-CONTRACT-RECORDED-AI-V1":
        raise AttemptLedgerError("acquisition contract identity is invalid")
    if contract["canonical_assisted_evidence"] is not True or contract["deterministic_fixtures_are_canonical"] is not False:
        raise AttemptLedgerError("canonical and deterministic evidence classes are conflated")
    envelope = ProviderEnvelope.from_mapping(contract["envelope"])
    attempt_policy = contract["attempt_policy"]
    if not isinstance(attempt_policy, dict) or set(attempt_policy) != {
        "input_digest_mapping",
        "permitted_attempt_ids",
        "retries_allowed",
        "terminal_statuses",
    }:
        raise AttemptLedgerError("attempt policy fields are not exact")
    permitted = attempt_policy["permitted_attempt_ids"]
    mapping = attempt_policy["input_digest_mapping"]
    if not isinstance(permitted, list) or not permitted or len(permitted) != len(set(permitted)):
        raise AttemptLedgerError("permitted attempt IDs must be a nonempty ordered set")
    if any(not isinstance(item, str) or not STAGE2_ID_PATTERN.fullmatch(item) for item in permitted):
        raise AttemptLedgerError("permitted attempt ID is noncanonical")
    if attempt_policy["retries_allowed"] is not False or set(attempt_policy["terminal_statuses"]) != _TERMINAL_STATUSES:
        raise AttemptLedgerError("retry or terminal-status policy changed")
    if not isinstance(mapping, list) or [entry.get("attempt_id") for entry in mapping if isinstance(entry, dict)] != permitted:
        raise AttemptLedgerError("input digest mapping is incomplete or reordered")
    input_by_attempt: dict[str, str] = {}
    case_by_attempt: dict[str, str] = {}
    for entry in mapping:
        if not isinstance(entry, dict) or set(entry) != {"attempt_id", "case_id", "input_sha256"}:
            raise AttemptLedgerError("input digest mapping fields are invalid")
        digest = entry["input_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise AttemptLedgerError("input digest mapping contains an invalid digest")
        input_by_attempt[entry["attempt_id"]] = digest
        case_id = entry["case_id"]
        if not isinstance(case_id, str) or not STAGE2_ID_PATTERN.fullmatch(case_id) or not case_id.startswith("S2-CASE-"):
            raise AttemptLedgerError("input digest mapping contains an invalid case ID")
        case_by_attempt[entry["attempt_id"]] = case_id

    rows, attempts_raw = _read_attempts(attempts_path)
    if [row.get("attempt_id") for row in rows] != permitted:
        raise AttemptLedgerError("attempt ledger is missing, added, or reordered")
    attempt_fields = {
        "acquired_at",
        "attempt_id",
        "cost_cents",
        "fallback_disposition",
        "input_sha256",
        "latency_milliseconds",
        "metadata_limitations",
        "response_artifact",
        "response_bytes",
        "response_sha256",
        "retry_of",
        "terminal_status",
        "token_usage",
        "validation_result",
    }
    for row in rows:
        if set(row) != attempt_fields or row["input_sha256"] != input_by_attempt[row["attempt_id"]]:
            raise AttemptLedgerError("attempt row fields or input binding are invalid")
        if row["terminal_status"] not in _TERMINAL_STATUSES or row["retry_of"] is not None:
            raise AttemptLedgerError("attempt terminal status or retry is outside contract")
        for field in ("cost_cents", "latency_milliseconds"):
            measure = row[field]
            if measure is not None and (isinstance(measure, bool) or not isinstance(measure, int) or measure < 0):
                raise AttemptLedgerError("attempt measurements must be null or nonnegative integers")
        if row["token_usage"] is not None and not isinstance(row["token_usage"], dict):
            raise AttemptLedgerError("token usage must be an object or explicitly unavailable")
        if not isinstance(row["metadata_limitations"], list) or not row["metadata_limitations"]:
            raise AttemptLedgerError("attempt metadata limitations must remain explicit")
        if row["terminal_status"] == "SUCCESS":
            response_path = _safe_artifact(root, row["response_artifact"])
            response = response_path.read_bytes()
            if row["response_bytes"] != len(response) or row["response_sha256"] != _sha(response):
                raise AttemptLedgerError("recorded response bytes changed")
            if response_path.parts[-2] == "fixtures":
                raise AttemptLedgerError("deterministic fixture cannot satisfy an assisted attempt")
            try:
                parsed_candidate = parse_candidate(response, envelope)
            except ProviderBoundaryError as error:
                raise AttemptLedgerError("recorded successful candidate is invalid") from error
            if parsed_candidate["case_id"] != case_by_attempt[row["attempt_id"]]:
                raise AttemptLedgerError("recorded candidate belongs to a foreign case")
            if row["validation_result"] != "ACCEPTED":
                raise AttemptLedgerError("successful candidate must record accepted validation")
        elif any(row[field] is not None for field in ("response_artifact", "response_bytes", "response_sha256")):
            raise AttemptLedgerError("non-success attempt cannot hide a response artifact")

    expected_manifest_fields = {
        "acquisition_contract_sha256",
        "attempt_ledger_sha256",
        "candidate_artifacts_sha256",
        "fixture_artifacts_sha256",
        "instruction_artifact_sha256",
        "manifest_self_exclusion",
        "request_artifacts_sha256",
        "schema_version",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest_fields or manifest["schema_version"] != "stage2-provider-manifest/v1":
        raise AttemptLedgerError("provider manifest fields are invalid")
    if manifest["manifest_self_exclusion"] != "manifest.json":
        raise AttemptLedgerError("provider manifest self-exclusion is invalid")
    if manifest["acquisition_contract_sha256"] != _sha(contract_path.read_bytes()) or manifest["attempt_ledger_sha256"] != _sha(attempts_raw):
        raise AttemptLedgerError("provider contract or attempt ledger digest changed")
    instruction_path = _safe_artifact(root, "instruction.json")
    if manifest["instruction_artifact_sha256"] != _sha(instruction_path.read_bytes()):
        raise AttemptLedgerError("provider instruction artifact changed")
    if contract["instruction_digest"] != manifest["instruction_artifact_sha256"]:
        raise AttemptLedgerError("acquisition contract does not bind the public instruction")
    artifact_groups = (
        "candidate_artifacts_sha256",
        "fixture_artifacts_sha256",
        "request_artifacts_sha256",
    )
    if any(not isinstance(manifest[group], dict) for group in artifact_groups):
        raise AttemptLedgerError("provider artifact manifest must use digest objects")
    expected_candidates = {
        row["response_artifact"]
        for row in rows
        if row["terminal_status"] == "SUCCESS"
    }
    if set(manifest["candidate_artifacts_sha256"]) != expected_candidates:
        raise AttemptLedgerError("candidate manifest does not exactly conserve successful attempts")
    expected_requests = {f"requests/{attempt_id}.json" for attempt_id in permitted}
    if set(manifest["request_artifacts_sha256"]) != expected_requests:
        raise AttemptLedgerError("request manifest does not exactly conserve permitted attempts")
    if not manifest["fixture_artifacts_sha256"]:
        raise AttemptLedgerError("deterministic CI fixtures must remain separately visible")
    for attempt_id in permitted:
        request_relative = f"requests/{attempt_id}.json"
        request_bytes = _safe_artifact(root, request_relative).read_bytes()
        if _sha(request_bytes) != input_by_attempt[attempt_id]:
            raise AttemptLedgerError("recorded request does not match its preregistered input digest")
    for group in artifact_groups:
        artifacts = manifest[group]
        for relative, digest in artifacts.items():
            if _sha(_safe_artifact(root, relative).read_bytes()) != digest:
                raise AttemptLedgerError("provider candidate or fixture digest changed")
    expected_files = {
        "acquisition-contract.json",
        "attempts.jsonl",
        "instruction.json",
        "manifest.json",
        *manifest["candidate_artifacts_sha256"],
        *manifest["fixture_artifacts_sha256"],
        *manifest["request_artifacts_sha256"],
    }
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise AttemptLedgerError("provider set contains an unmanifested or missing artifact")
    denominator = contract["assisted_denominator"]
    if not isinstance(denominator, dict) or denominator.get("attempt_count") != len(permitted):
        raise AttemptLedgerError("assisted denominator does not conserve attempts")
    return {
        "canonical_recorded_ai_complete": True,
        "deterministic_fixture_is_canonical": False,
        "permitted_attempt_count": len(permitted),
        "recorded_attempt_count": len(rows),
        "terminal_status_counts": {
            status: sum(row["terminal_status"] == status for row in rows)
            for status in sorted(_TERMINAL_STATUSES)
        },
    }


class RecordedCandidateProvider:
    """Read one preregistered recorded attempt; never call a live network."""

    def __init__(self, root: Path):
        self.root = Path(root)
        validate_attempt_set(self.root)
        self.contract = _load(self.root / "acquisition-contract.json", "acquisition contract")
        self.rows, _ = _read_attempts(self.root / "attempts.jsonl")
        self.envelope = ProviderEnvelope.from_mapping(self.contract["envelope"])

    def propose(
        self,
        request: bytes,
        *,
        attempt_id: str,
        deadline_monotonic: float | None = None,
    ) -> ProviderAttemptResult:
        _deadline(deadline_monotonic)
        if len(request) > self.envelope.max_request_bytes:
            raise ProviderBoundaryError("provider request exceeds byte envelope")
        rows = {row["attempt_id"]: row for row in self.rows}
        if attempt_id not in rows:
            raise AttemptLedgerError("attempt ID was not preregistered")
        row = rows[attempt_id]
        if row["input_sha256"] != _sha(request):
            raise AttemptLedgerError("request does not match preregistered input digest")
        if row["terminal_status"] != "SUCCESS":
            return ProviderAttemptResult(
                attempt_id=attempt_id,
                terminal_status=row["terminal_status"],
                validation_result=row["validation_result"],
                candidate=None,
                fallback_disposition=row["fallback_disposition"],
                raw_response_sha256=None,
            )
        response = _safe_artifact(self.root, row["response_artifact"]).read_bytes()
        candidate = parse_candidate(response, self.envelope, deadline_monotonic=deadline_monotonic)
        return ProviderAttemptResult(
            attempt_id=attempt_id,
            terminal_status="SUCCESS",
            validation_result="ACCEPTED",
            candidate=candidate,
            fallback_disposition=row["fallback_disposition"],
            raw_response_sha256=row["response_sha256"],
        )

    def propose_context(
        self,
        context: Mapping[str, Any],
        *,
        attempt_id: str,
        deadline_monotonic: float | None = None,
    ) -> ProviderAttemptResult:
        result = self.propose(
            canonical_provider_request(context, self.envelope),
            attempt_id=attempt_id,
            deadline_monotonic=deadline_monotonic,
        )
        if result.candidate is not None:
            validate_candidate_context(result.candidate, context)
        return result


class DeterministicFixtureProvider:
    """Explicit secondary CI fixture; never canonical recorded-AI evidence."""

    evidence_class = "deterministic-ci-fixture"
    canonical_assisted_evidence = False

    def __init__(self, candidate: Mapping[str, Any]):
        self.candidate = parse_candidate(canonical_json_bytes(candidate))

    def propose(self, request: bytes, *, attempt_id: str, deadline_monotonic: float | None = None) -> ProviderAttemptResult:
        _deadline(deadline_monotonic)
        if len(request) > DEFAULT_PROVIDER_ENVELOPE.max_request_bytes:
            raise ProviderBoundaryError("provider request exceeds byte envelope")
        return ProviderAttemptResult(
            attempt_id=attempt_id,
            terminal_status="FALLBACK",
            validation_result="FIXTURE_ACCEPTED",
            candidate=self.candidate,
            fallback_disposition="DETERMINISTIC_FIXTURE_ONLY",
            raw_response_sha256=_sha(canonical_json_bytes(self.candidate)),
        )

    def propose_context(
        self,
        context: Mapping[str, Any],
        *,
        attempt_id: str,
        deadline_monotonic: float | None = None,
    ) -> ProviderAttemptResult:
        result = self.propose(
            canonical_provider_request(context),
            attempt_id=attempt_id,
            deadline_monotonic=deadline_monotonic,
        )
        if result.candidate is not None:
            validate_candidate_context(result.candidate, context)
        return result
