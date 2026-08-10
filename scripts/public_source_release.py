#!/usr/bin/env python3
"""Build and verify a reproducible public-corpus release from committed bytes."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

try:
    from scripts.verify_public_safety import (
        PolicyValidationError,
        check_artifact_metadata,
        parse_front_matter,
        validate_policy,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_public_safety import (  # type: ignore[no-redef]
        PolicyValidationError,
        check_artifact_metadata,
        parse_front_matter,
        validate_policy,
    )


SCHEMA_VERSION = "public-source-release/v1"
GENERATOR_VERSION = "1.0.0"
FULL_COMMIT_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class PublicSourceReleaseError(RuntimeError):
    """Raised when a committed source release cannot be reproduced safely."""


def _git(root: Path, arguments: Sequence[str]) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as error:
        raise PublicSourceReleaseError("git is unavailable") from error
    if result.returncode != 0:
        raise PublicSourceReleaseError("committed source snapshot is unavailable")
    return result.stdout


def _resolve_commit(root: Path, commit: str) -> str:
    if not isinstance(commit, str) or not commit.strip() or "\x00" in commit:
        raise PublicSourceReleaseError("source commit reference is invalid")
    resolved = _git(root, ["rev-parse", "--verify", f"{commit}^{{commit}}"])
    try:
        full_commit = resolved.decode("ascii").strip().lower()
    except UnicodeDecodeError as error:
        raise PublicSourceReleaseError("resolved source commit is invalid") from error
    if not FULL_COMMIT_RE.fullmatch(full_commit):
        raise PublicSourceReleaseError("resolved source commit is invalid")
    return full_commit


def _commit_paths(root: Path, commit: str) -> list[str]:
    raw_paths = _git(root, ["ls-tree", "-r", "--name-only", "-z", commit, "--"])
    paths: list[str] = []
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        try:
            relative = raw_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PublicSourceReleaseError(
                "source snapshot contains a non-UTF-8 path"
            ) from error
        pure_path = PurePosixPath(relative)
        if (
            pure_path.is_absolute()
            or ".." in pure_path.parts
            or "\\" in relative
            or relative in {"", "."}
        ):
            raise PublicSourceReleaseError("source snapshot contains an unsafe path")
        paths.append(relative)
    return sorted(set(paths))


def _commit_bytes(root: Path, commit: str, relative: str) -> bytes:
    return _git(root, ["show", f"{commit}:{relative}"])


def _matches_any(relative: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _load_committed_policy(root: Path, commit: str) -> dict[str, Any]:
    raw_policy = _commit_bytes(root, commit, "policy/publication-policy.json")
    try:
        policy = json.loads(raw_policy.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicSourceReleaseError(
            "committed publication policy is not valid UTF-8 JSON"
        ) from error
    try:
        validate_policy(policy)
    except PolicyValidationError as error:
        raise PublicSourceReleaseError(str(error)) from error
    return policy


def build_source_release_manifest(root: Path, commit: str) -> dict[str, Any]:
    """Build a deterministic manifest from one immutable Git commit."""
    root = root.resolve()
    source_commit = _resolve_commit(root, commit)
    policy = _load_committed_policy(root, source_commit)
    committed_paths = _commit_paths(root, source_commit)
    release_paths = [
        relative
        for relative in committed_paths
        if _matches_any(relative, policy["source_release_globs"])
    ]
    missing_required = sorted(
        set(policy["required_source_release_paths"]) - set(release_paths)
    )
    if missing_required:
        raise PublicSourceReleaseError(
            "source snapshot is missing required release path(s): "
            + ", ".join(missing_required)
        )

    artifacts: list[dict[str, Any]] = []
    metadata_errors: list[str] = []
    for relative in release_paths:
        payload = _commit_bytes(root, source_commit, relative)
        artifact: dict[str, Any] = {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if relative.endswith(".md"):
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            is_canonical = _matches_any(
                relative, policy["canonical_source_globs"]
            )
            metadata_errors.extend(
                check_artifact_metadata(
                    relative,
                    text,
                    policy,
                    canonical_source=is_canonical,
                )
            )
            if is_canonical and text is not None:
                metadata = parse_front_matter(text)
                if metadata is not None and all(
                    metadata.get(field)
                    for field in policy["required_canonical_source_fields"]
                ):
                    artifact["metadata"] = {
                        field: metadata[field]
                        for field in policy["required_canonical_source_fields"]
                    }
        artifacts.append(artifact)

    if metadata_errors:
        raise PublicSourceReleaseError(
            "source snapshot fails public metadata validation: "
            + "; ".join(sorted(set(metadata_errors)))
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "source_commit": source_commit,
        "hash_algorithm": "sha256",
        "maturity": policy["current_maturity"],
        "release_boundary": {
            "client_outcome_evidence": False,
            "generated_content_changes_authority": False,
            "material_change_requires_regression": True,
        },
        "artifacts": artifacts,
    }


def verify_source_release_manifest(
    root: Path, manifest: Any
) -> list[str]:
    """Return safe validation errors for a source-release manifest."""
    if not isinstance(manifest, dict):
        return ["source release manifest must be an object"]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return ["source release manifest has an unsupported schema version"]
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not FULL_COMMIT_RE.fullmatch(
        source_commit
    ):
        return ["source release manifest has an invalid source commit"]
    try:
        expected = build_source_release_manifest(root, source_commit)
    except PublicSourceReleaseError as error:
        return [str(error)]
    if manifest != expected:
        return ["source release manifest does not match the frozen commit"]
    return []


def write_source_release_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write canonical UTF-8, LF, sorted-key JSON with a final newline."""
    path.write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify a committed public source release manifest."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--commit", required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if arguments.command == "build":
            manifest = build_source_release_manifest(root, arguments.commit)
            write_source_release_manifest(arguments.output, manifest)
            print("Public source release manifest written.")
            return 0

        manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        errors = verify_source_release_manifest(root, manifest)
    except (
        FileNotFoundError,
        IsADirectoryError,
        json.JSONDecodeError,
        PublicSourceReleaseError,
    ) as error:
        print(f"Public source release failed: {error}", file=sys.stderr)
        return 1
    if errors:
        print("Public source release verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Public source release verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
