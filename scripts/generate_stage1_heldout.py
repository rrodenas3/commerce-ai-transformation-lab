#!/usr/bin/env python3
"""Generate the public held-out case pack and its private sealed oracle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_heldout import (
    GENERATION_MATERIAL_FILE,
    HELDOUT_PRIVATE_PATH,
    HELDOUT_PUBLIC_PATH,
    generate_heldout_artifacts,
    validate_heldout_public_pack,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-output", type=Path)
    parser.add_argument("--private-output", type=Path)
    parser.add_argument(
        "--verify-public",
        action="store_true",
        help="verify committed public cases and commitments without private material",
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    public = arguments.public_output or root / HELDOUT_PUBLIC_PATH
    private = arguments.private_output or root / HELDOUT_PRIVATE_PATH
    if arguments.verify_public:
        manifest, _, _, _, _, _ = validate_heldout_public_pack(
            root, public, require_unreleased=False
        )
        print(
            f"Verified {manifest['case_count']} public held-out cases; "
            "the answer file is not published."
        )
        return 0
    if (public / "manifest.json").exists() and not (
        private / GENERATION_MATERIAL_FILE
    ).is_file():
        raise ValueError(
            "the committed pack cannot be regenerated without its private material; "
            "use --verify-public"
        )
    manifest = generate_heldout_artifacts(root, public, private)
    print(
        f"Generated {manifest['case_count']} held-out cases; "
        "the oracle remains in the ignored private evidence store."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
