#!/usr/bin/env python3
"""Build the public-safe projection of the canonical V6 isolation evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATTESTATION = Path("data/stage2/runs/S2-CF-RUN-0005/outer/isolation-attestation.json")
ELIGIBILITY = Path("data/stage2/runs/S2-CF-RUN-0005/release-states/0004-eligibility-verified.json")
OUTPUT = Path("data/stage2/development/evaluation-v6-isolation-summary.json")
PUBLIC_CONTROLS = {
    "capabilities": "ALL_DROPPED",
    "container_user": "65532:65532",
    "home_mount": "absent",
    "network": "none",
    "no_new_privileges": True,
    "oracle_mount": "absent",
    "pids_limit": 1,
    "private_mount": "absent",
    "repository_mount": "absent",
    "root_filesystem": "read-only",
    "seccomp_profile_sha256": "bc51914289c5ba59019055f6f77ed493ef398eeba8aca18e25d430e0b568b955",
    "workspace_mount": "isolated-tmpfs-rw",
}


class IsolationSummaryError(RuntimeError):
    """Raised when the public projection cannot be reproduced exactly."""


def _canonical(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_json(root: Path, relative: Path) -> tuple[dict[str, Any], bytes]:
    raw = (root / relative).read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise IsolationSummaryError(f"isolation source is not an object: {relative.as_posix()}")
    return payload, raw


def build_isolation_summary(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    attestation, attestation_bytes = _read_json(root, ATTESTATION)
    eligibility, _eligibility_bytes = _read_json(root, ELIGIBILITY)
    digest = hashlib.sha256(attestation_bytes).hexdigest()
    bindings = eligibility.get("bindings")
    if (
        not isinstance(bindings, dict)
        or bindings.get("attestation_sha256") != digest
        or bindings.get("canonical_eligible") is not True
    ):
        raise IsolationSummaryError("eligibility state does not bind the isolation attestation")
    if attestation.get("canonical_run") is not True:
        raise IsolationSummaryError("isolation attestation is not canonical")
    return {
        "canonical_eligible": True,
        "contains_real_data": False,
        "evidence_boundary": (
            "public-safe projection of the canonical outer isolation attestation; "
            "host, transient, container and image identifiers omitted from the publication bundle"
        ),
        "omitted_field_classes": [
            "container_identity", "host_paths", "image_identity", "transient_paths"
        ],
        "pack_id": eligibility["pack_id"],
        "publication_note": (
            "The immutable source attestation remains digest-bound by the eligibility state but "
            "is excluded from the public release inventory because it contains local machine paths."
        ),
        "run_id": "S2-CF-RUN-0005",
        "schema_version": "stage2-public-isolation-summary/v1",
        "source_attestation_sha256": digest,
        "source_eligibility_state": ELIGIBILITY.as_posix(),
        "verified_controls": {
            key: attestation[key]
            for key in (
                "capabilities", "container_user", "home_mount", "network",
                "no_new_privileges", "oracle_mount", "pids_limit", "private_mount",
                "repository_mount", "root_filesystem", "seccomp_profile_sha256",
                "workspace_mount",
            )
        },
    }


def verify_isolation_summary(
    root: Path = PROJECT_ROOT, *, public_projection_only: bool = False
) -> None:
    root = Path(root).resolve()
    committed = (root / OUTPUT).read_bytes()
    if public_projection_only:
        summary = json.loads(committed.decode("utf-8"))
        eligibility, _eligibility_bytes = _read_json(root, ELIGIBILITY)
        bindings = eligibility.get("bindings")
        if (
            not isinstance(summary, dict)
            or not isinstance(bindings, dict)
            or summary.get("schema_version") != "stage2-public-isolation-summary/v1"
            or summary.get("canonical_eligible") is not True
            or summary.get("contains_real_data") is not False
            or summary.get("pack_id") != eligibility.get("pack_id")
            or summary.get("source_attestation_sha256")
            != bindings.get("attestation_sha256")
            or summary.get("source_eligibility_state") != ELIGIBILITY.as_posix()
            or summary.get("run_id") != "S2-CF-RUN-0005"
            or summary.get("verified_controls") != PUBLIC_CONTROLS
            or bindings.get("canonical_eligible") is not True
            or committed != _canonical(summary)
        ):
            raise IsolationSummaryError(
                "public isolation summary does not bind the preserved eligibility state"
            )
        return
    if committed != _canonical(build_isolation_summary(root)):
        raise IsolationSummaryError("public isolation summary differs from canonical sources")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--public-projection-only", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify:
            verify_isolation_summary(
                public_projection_only=arguments.public_projection_only
            )
            print("Stage 2 public isolation summary verified.")
        else:
            expected = _canonical(build_isolation_summary())
            (PROJECT_ROOT / OUTPUT).write_bytes(expected)
            print("Stage 2 public isolation summary written.")
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IsolationSummaryError) as error:
        print(f"Stage 2 public isolation summary failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
