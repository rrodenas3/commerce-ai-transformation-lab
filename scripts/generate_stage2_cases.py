#!/usr/bin/env python3
"""Generate or verify the committed Stage 2 development artifacts."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.stage2_case_system import generate_stage2_development_artifacts


def _files(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def verify_committed(project_root: Path, committed_root: Path) -> list[str]:
    """Regenerate in isolation and report every missing, extra, or changed byte path."""

    with tempfile.TemporaryDirectory(prefix="stage2-verify-") as temporary:
        generated_root = Path(temporary)
        generate_stage2_development_artifacts(project_root, generated_root)
        expected = _files(generated_root)
        actual = _files(committed_root)
    errors = []
    for relative in sorted(expected.keys() - actual.keys()):
        errors.append(f"missing committed artifact: {relative}")
    for relative in sorted(actual.keys() - expected.keys()):
        errors.append(f"unexpected committed artifact: {relative}")
    for relative in sorted(expected.keys() & actual.keys()):
        if expected[relative] != actual[relative]:
            errors.append(f"byte mismatch: {relative}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the public synthetic Stage 2 development denominator."
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="regenerate in a temporary directory and compare every committed byte",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write to an alternate directory (generation mode only)",
    )
    args = parser.parse_args(argv)
    if args.verify and args.output is not None:
        parser.error("--verify and --output cannot be used together")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    committed_root = project_root / "data" / "stage2" / "development"
    if args.verify:
        errors = verify_committed(project_root, committed_root)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Stage 2 development artifacts are byte-stable and complete (24 cases).")
        return 0
    output_root = args.output.resolve() if args.output else committed_root
    manifest = generate_stage2_development_artifacts(project_root, output_root)
    print(
        f"Generated {manifest['case_count']} public synthetic development cases "
        f"across {len(manifest['family_counts'])} families."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
