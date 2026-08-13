#!/usr/bin/env python3
"""Build and verify a reproducible public-corpus release from committed bytes."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

try:
    from scripts.verify_public_safety import (
        PolicyValidationError,
        VISUAL_ASSET_DIRECTORY,
        VISUAL_ASSET_MANIFEST,
        check_artifact_metadata,
        check_visual_asset_snapshot,
        parse_front_matter,
        validate_policy,
        verify_repository,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from verify_public_safety import (  # type: ignore[no-redef]
        PolicyValidationError,
        VISUAL_ASSET_DIRECTORY,
        VISUAL_ASSET_MANIFEST,
        check_artifact_metadata,
        check_visual_asset_snapshot,
        parse_front_matter,
        validate_policy,
        verify_repository,
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


def _commit_payloads(root: Path, commit: str) -> dict[str, bytes]:
    """Load exact immutable Git blobs through one batched object read."""
    raw_entries = _git(root, ["ls-tree", "-r", "-z", commit, "--"])
    entries: list[tuple[str, bytes]] = []
    for entry in raw_entries.split(b"\0"):
        if not entry:
            continue
        header, separator, raw_path = entry.partition(b"\t")
        fields = header.split()
        if not separator or len(fields) != 3 or fields[1] != b"blob":
            raise PublicSourceReleaseError(
                "source snapshot contains a non-blob tree entry"
            )
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
            raise PublicSourceReleaseError(
                "source snapshot contains an unsafe path"
            )
        entries.append((relative, fields[2]))

    payloads: dict[str, bytes] = {}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "--batch"],
            input=b"".join(object_id + b"\n" for _path, object_id in entries),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as error:
        raise PublicSourceReleaseError("git is unavailable") from error
    if result.returncode != 0:
        raise PublicSourceReleaseError("committed source objects are unavailable")

    cursor = 0
    for relative, expected_object_id in entries:
        header_end = result.stdout.find(b"\n", cursor)
        if header_end < 0:
            raise PublicSourceReleaseError("Git batch object stream is truncated")
        header = result.stdout[cursor:header_end].split()
        if (
            len(header) != 3
            or header[0] != expected_object_id
            or header[1] != b"blob"
            or not header[2].isdigit()
        ):
            raise PublicSourceReleaseError("Git batch object header is invalid")
        size = int(header[2])
        start = header_end + 1
        end = start + size
        if end >= len(result.stdout) or result.stdout[end : end + 1] != b"\n":
            raise PublicSourceReleaseError("Git batch object payload is truncated")
        if relative in payloads:
            raise PublicSourceReleaseError(
                "source snapshot contains duplicate paths"
            )
        payloads[relative] = result.stdout[start:end]
        cursor = end + 1
    if cursor != len(result.stdout):
        raise PublicSourceReleaseError("Git batch object stream has trailing bytes")
    return payloads


def _commit_modes(root: Path, commit: str) -> dict[str, str]:
    raw = _git(root, ["ls-tree", "-r", "-z", commit, "--"])
    modes: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        header, separator, raw_path = entry.partition(b"\t")
        if not separator:
            raise PublicSourceReleaseError("source snapshot tree entry is malformed")
        try:
            mode = header.split(b" ", 1)[0].decode("ascii")
            relative = raw_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PublicSourceReleaseError("source snapshot tree entry is invalid") from error
        modes[relative] = mode
    return modes


def _matches_any(relative: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _decoded_json_strings(payload: bytes, relative: str) -> list[str]:
    if not relative.endswith((".json", ".jsonl")):
        return []
    try:
        text = payload.decode("utf-8")
        values = (
            [json.loads(line) for line in text.splitlines() if line.strip()]
            if relative.endswith(".jsonl")
            else [json.loads(text)]
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicSourceReleaseError(
            f"release JSON artifact is invalid: {relative}"
        ) from error

    strings: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                strings.append(str(key))
                visit(item)

    for value in values:
        visit(value)
    return strings


def _assert_release_payload_safe(
    relative: str, payload: bytes, policy: dict[str, Any]
) -> None:
    patterns = [
        (entry["name"], re.compile(entry["pattern"]))
        for entry in policy["forbidden_text_patterns"]
    ]
    try:
        raw_text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = ""
    surfaces = [raw_text, *_decoded_json_strings(payload, relative)]
    for name, pattern in patterns:
        if any(pattern.search(surface) for surface in surfaces):
            raise PublicSourceReleaseError(
                f"release artifact contains forbidden text pattern '{name}': {relative}"
            )


def _validate_snapshot_with_trusted_policy(
    root: Path,
    source_commit: str,
    trusted_policy_commit: str,
    committed_paths: Sequence[str],
    payload_loader: Callable[[str], bytes],
) -> None:
    with tempfile.TemporaryDirectory(prefix="public-source-policy-") as temporary:
        snapshot = Path(temporary)
        for relative in committed_paths:
            destination = snapshot.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload_loader(relative))
        trusted_policy = (
            payload_loader("policy/publication-policy.json")
            if trusted_policy_commit == source_commit
            else _commit_bytes(
                root, trusted_policy_commit, "policy/publication-policy.json"
            )
        )
        policy_path = snapshot / "policy" / "publication-policy.json"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_bytes(trusted_policy)
        errors = verify_repository(snapshot)
    if errors:
        raise PublicSourceReleaseError(
            "source snapshot fails trusted publication policy: "
            + "; ".join(errors)
        )


def _load_committed_policy(
    root: Path,
    commit: str,
    payload_loader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    load_payload = payload_loader or (lambda relative: _commit_bytes(root, commit, relative))
    raw_policy = load_payload("policy/publication-policy.json")
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


def _validate_committed_visual_assets(
    root: Path,
    commit: str,
    policy: dict[str, Any],
    committed_paths: Sequence[str],
    payload_loader: Callable[[str], bytes] | None = None,
) -> None:
    """Apply the visual integrity contract to bytes from one Git snapshot."""
    load_payload = payload_loader or (lambda relative: _commit_bytes(root, commit, relative))
    committed_by_key: dict[str, str] = {}
    for relative in committed_paths:
        key = relative.casefold()
        if key in committed_by_key and committed_by_key[key] != relative:
            raise PublicSourceReleaseError(
                "source snapshot contains case-colliding paths"
            )
        committed_by_key[key] = relative

    manifest_relative = committed_by_key.get(VISUAL_ASSET_MANIFEST.casefold())
    manifest_payload = (
        load_payload(manifest_relative)
        if manifest_relative is not None
        else None
    )
    prefix = f"{VISUAL_ASSET_DIRECTORY}/"
    visual_paths = [
        relative
        for relative in committed_paths
        if relative.startswith(prefix)
        and PurePosixPath(relative).suffix.lower() == ".png"
    ]
    asset_payloads = {
        relative.casefold(): load_payload(relative)
        for relative in visual_paths
    }

    def load_committed_payload(relative: str) -> bytes | None:
        committed_relative = committed_by_key.get(relative.casefold())
        if committed_relative is None:
            return None
        return load_payload(committed_relative)

    errors = check_visual_asset_snapshot(
        policy,
        manifest_payload,
        asset_payloads,
        visual_control_present=manifest_relative is not None or bool(visual_paths),
        repository_payload_loader=load_committed_payload,
    )
    if errors:
        raise PublicSourceReleaseError(
            "source snapshot fails visual asset validation: "
            + "; ".join(sorted(set(errors)))
        )


def build_source_release_manifest(
    root: Path, commit: str, *, policy_commit: str | None = None
) -> dict[str, Any]:
    """Build a deterministic manifest from one immutable Git commit."""
    root = root.resolve()
    source_commit = _resolve_commit(root, commit)
    trusted_policy_commit = _resolve_commit(root, policy_commit or source_commit)
    payload_cache = _commit_payloads(root, source_commit)

    def load_committed_payload(relative: str) -> bytes:
        try:
            return payload_cache[relative]
        except KeyError as error:
            raise PublicSourceReleaseError(
                "committed source artifact is unavailable"
            ) from error

    if trusted_policy_commit == source_commit:
        policy = _load_committed_policy(root, source_commit, load_committed_payload)
    else:
        policy = _load_committed_policy(root, trusted_policy_commit)
    committed_paths = _commit_paths(root, source_commit)
    if sorted(payload_cache) != committed_paths:
        raise PublicSourceReleaseError(
            "source snapshot archive differs from the committed tree"
        )
    _validate_snapshot_with_trusted_policy(
        root,
        source_commit,
        trusted_policy_commit,
        committed_paths,
        load_committed_payload,
    )
    _validate_committed_visual_assets(
        root,
        source_commit,
        policy,
        committed_paths,
        load_committed_payload,
    )
    release_paths = [
        relative
        for relative in committed_paths
        if _matches_any(relative, policy["source_release_globs"])
    ]
    modes = _commit_modes(root, source_commit)
    unsafe_modes = [
        relative
        for relative in release_paths
        if modes.get(relative) not in {"100644", "100755"}
    ]
    if unsafe_modes:
        raise PublicSourceReleaseError(
            "source release contains non-regular Git entries: "
            + ", ".join(sorted(unsafe_modes))
        )
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
        payload = load_committed_payload(relative)
        _assert_release_payload_safe(relative, payload, policy)
        artifact: dict[str, Any] = {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if relative.endswith(".md") and _matches_any(
            relative, policy["evidence_artifact_globs"]
        ):
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
    root: Path, manifest: Any, *, policy_commit: str | None = None
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
        expected = build_source_release_manifest(
            root, source_commit, policy_commit=policy_commit
        )
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


def materialize_source_release(
    root: Path,
    manifest: dict[str, Any],
    destination: Path,
    *,
    policy_commit: str | None = None,
) -> dict[str, Any]:
    """Materialize exactly one verified manifest into a new directory."""
    root = root.resolve()
    errors = verify_source_release_manifest(
        root, manifest, policy_commit=policy_commit
    )
    if errors:
        raise PublicSourceReleaseError(
            "source release manifest cannot be materialized: " + "; ".join(errors)
        )

    destination = destination.resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise PublicSourceReleaseError(
            "source release destination must be absent and therefore empty"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_commit = manifest["source_commit"]
    modes = _commit_modes(root, source_commit)
    payloads = _commit_payloads(root, source_commit)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-", dir=destination.parent
        )
    )
    installed = False
    try:
        for artifact in manifest["artifacts"]:
            relative = artifact["path"]
            try:
                payload = payloads[relative]
            except KeyError as error:
                raise PublicSourceReleaseError(
                    f"source release artifact is unavailable: {relative}"
                ) from error
            if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                raise PublicSourceReleaseError(
                    f"source release artifact digest changed: {relative}"
                )
            target = staging.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if modes.get(relative) == "100755":
                target.chmod(0o755)

        expected = {
            artifact["path"]: artifact["sha256"]
            for artifact in manifest["artifacts"]
        }
        observed: dict[str, str] = {}
        for path in sorted(staging.rglob("*")):
            if path.is_symlink():
                raise PublicSourceReleaseError(
                    "materialized source release contains a symbolic link"
                )
            if not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            if path.stat().st_nlink != 1:
                raise PublicSourceReleaseError(
                    "materialized source release contains a hard-linked file"
                )
            observed[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != expected:
            raise PublicSourceReleaseError(
                "materialized source release inventory differs from the manifest"
            )
        staging.rename(destination)
        installed = True
    finally:
        if not installed and staging.exists():
            shutil.rmtree(staging)

    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return {
        "source_commit": source_commit,
        "artifact_count": len(manifest["artifacts"]),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "destination": str(destination),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, verify, or materialize a committed public source release."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--commit", required=True)
    build_parser.add_argument(
        "--policy-ref",
        help="Optional protected ref that supplies the release inventory policy.",
    )
    build_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument(
        "--policy-ref",
        help="Optional protected ref that supplied the release inventory policy.",
    )
    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--manifest", type=Path, required=True)
    materialize_parser.add_argument("--destination", type=Path, required=True)
    materialize_parser.add_argument(
        "--policy-ref",
        help="Optional protected ref that supplied the release inventory policy.",
    )
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = root.resolve() if root is not None else Path(__file__).resolve().parents[1]
    try:
        if arguments.command == "build":
            manifest = build_source_release_manifest(
                root, arguments.commit, policy_commit=arguments.policy_ref
            )
            write_source_release_manifest(arguments.output, manifest)
            print("Public source release manifest written.")
            return 0

        manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        if arguments.command == "materialize":
            receipt = materialize_source_release(
                root,
                manifest,
                arguments.destination,
                policy_commit=arguments.policy_ref,
            )
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0
        errors = verify_source_release_manifest(
            root, manifest, policy_commit=arguments.policy_ref
        )
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
