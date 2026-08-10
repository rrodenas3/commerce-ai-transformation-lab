#!/usr/bin/env python3
"""Prepare a case-only Stage 1 held-out human evaluation run."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_heldout import HELDOUT_PUBLIC_PATH, prepare_heldout_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reviewer-code", required=True)
    parser.add_argument(
        "--operator-role", required=True, choices=("creator", "independent")
    )
    parser.add_argument("--public-pack", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    public = arguments.public_pack or root / HELDOUT_PUBLIC_PATH
    prepare_heldout_run(
        root,
        public,
        arguments.output,
        run_id=arguments.run_id,
        reviewer_code=arguments.reviewer_code,
        operator_role=arguments.operator_role,
        prepared_at=datetime.now(timezone.utc),
    )
    print(f"Prepared process-controlled held-out run at {arguments.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
