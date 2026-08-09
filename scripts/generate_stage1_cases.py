#!/usr/bin/env python3
"""Generate the committed Stage 1 public discovery artifacts."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage1_case_system import generate_stage1_artifacts


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest = generate_stage1_artifacts(root, root / "data" / "stage1" / "generated")
    print(
        "Generated "
        f"{manifest['case_count']} public foundation cases with seed "
        f"{manifest['generator_seed']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
