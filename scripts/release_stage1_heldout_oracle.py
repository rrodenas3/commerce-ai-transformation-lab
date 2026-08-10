#!/usr/bin/env python3
"""Release a held-out oracle after the completed human record is frozen."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_heldout import HELDOUT_PRIVATE_PATH
from scripts.stage1_heldout_release import release_heldout_oracle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--preparation-ref", required=True)
    parser.add_argument("--records-ref", required=True)
    parser.add_argument("--private-output", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    private = arguments.private_output or root / HELDOUT_PRIVATE_PATH
    release_heldout_oracle(
        root,
        arguments.run_manifest,
        private,
        preparation_ref=arguments.preparation_ref,
        records_ref=arguments.records_ref,
        released_at=datetime.now(timezone.utc),
    )
    print("Released the held-out oracle after verifying the frozen human record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
