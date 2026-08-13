#!/usr/bin/env python3
"""Verify, but never perform, owner authorization for a Stage 2 release.

The public bundle manifest is a deterministic artifact derived from a reviewed
Git commit. It is deliberately excluded from its own artifact inventory. A
signed annotated tag then binds the raw manifest digest to that exact commit,
tree, release identity, and maturity. This script validates the binding; it
does not create a tag, publish files, or advance repository state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

try:
    from scripts.public_source_release import (
        FULL_COMMIT_RE,
        PublicSourceReleaseError,
        build_source_release_manifest,
    )
    from scripts.verify_public_safety import (
        SHA256_RE,
        SIGNER_FINGERPRINT_RE,
        PolicyValidationError,
        validate_policy,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from public_source_release import (  # type: ignore[no-redef]
        FULL_COMMIT_RE,
        PublicSourceReleaseError,
        build_source_release_manifest,
    )
    from verify_public_safety import (  # type: ignore[no-redef]
        SHA256_RE,
        SIGNER_FINGERPRINT_RE,
        PolicyValidationError,
        validate_policy,
    )


AUTHORIZATION_SCHEMA = "stage2-release-authorization/v1"
OBJECT_ID_RE = FULL_COMMIT_RE
FINGERPRINT_RE = SIGNER_FINGERPRINT_RE
TAG_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
REQUIRED_ANNOTATION_FIELDS = {
    "release_id",
    "source_commit",
    "source_tree",
    "bundle_manifest_sha256",
    "maturity",
    "trusted_policy_commit",
}


class ReleaseAuthorizationError(RuntimeError):
    """Raised when release authorization cannot be proven exactly."""


SignatureVerifier = Callable[[Path, str], str]


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
        }
    )
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=environment,
        )
    except FileNotFoundError as error:
        raise ReleaseAuthorizationError("git is unavailable") from error
    if result.returncode != 0 and not allow_failure:
        raise ReleaseAuthorizationError("required Git object is unavailable")
    return result


def _decode_ascii(payload: bytes, label: str) -> str:
    try:
        return payload.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ReleaseAuthorizationError(f"{label} is not valid ASCII") from error


def _resolve_object(root: Path, expression: str, label: str) -> str:
    value = _decode_ascii(
        _git(root, ["rev-parse", "--verify", expression]).stdout,
        label,
    ).lower()
    if not OBJECT_ID_RE.fullmatch(value):
        raise ReleaseAuthorizationError(f"{label} is invalid")
    return value


def _validate_tag_name(tag_name: str, required_prefix: str) -> str:
    if (
        not isinstance(tag_name, str)
        or not TAG_NAME_RE.fullmatch(tag_name)
        or ".." in tag_name
        or "@{" in tag_name
        or "//" in tag_name
        or tag_name.endswith((".", "/", ".lock"))
    ):
        raise ReleaseAuthorizationError("release tag name is invalid")
    if not tag_name.startswith(required_prefix):
        raise ReleaseAuthorizationError("release tag does not use the required prefix")
    return f"refs/tags/{tag_name}"


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        manifest = json.loads(payload.decode("utf-8"))
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseAuthorizationError(
            "public bundle manifest is not valid UTF-8 JSON"
        ) from error
    if not isinstance(manifest, dict):
        raise ReleaseAuthorizationError("public bundle manifest must be an object")
    canonical = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if payload != canonical:
        raise ReleaseAuthorizationError(
            "public bundle manifest must be canonical UTF-8 JSON with LF endings"
        )
    return manifest, payload


def _load_committed_policy(root: Path, trusted_policy_ref: str) -> dict[str, Any]:
    raw = _git(
        root,
        ["show", f"{trusted_policy_ref}:policy/publication-policy.json"],
    ).stdout
    try:
        policy = json.loads(raw.decode("utf-8"))
        validate_policy(policy)
    except (UnicodeDecodeError, json.JSONDecodeError, PolicyValidationError) as error:
        raise ReleaseAuthorizationError(
            "trusted branch has no valid publication policy"
        ) from error
    return policy


def _authorization_policy(policy: dict[str, Any]) -> dict[str, Any]:
    # _load_committed_policy has already applied the shared fail-closed schema.
    return policy["release_authorization"]


def _validate_manifest_artifact_paths(manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ReleaseAuthorizationError("public bundle artifact inventory is invalid")
    paths: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ReleaseAuthorizationError("public bundle artifact inventory is invalid")
        relative = artifact.get("path")
        digest = artifact.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ReleaseAuthorizationError("public bundle artifact inventory is invalid")
        pure_path = PurePosixPath(relative)
        if (
            pure_path.is_absolute()
            or ".." in pure_path.parts
            or "\\" in relative
            or relative in {"", "."}
            or not SHA256_RE.fullmatch(digest)
        ):
            raise ReleaseAuthorizationError("public bundle artifact inventory is unsafe")
        paths.append(relative)
    if len(set(paths)) != len(paths):
        raise ReleaseAuthorizationError("public bundle artifact inventory has duplicates")
    if paths != sorted(paths):
        raise ReleaseAuthorizationError("public bundle artifact inventory is not sorted")
def _assert_manifest_self_exclusion(
    manifest: dict[str, Any], manifest_policy_path: str
) -> None:
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list) and any(
        isinstance(artifact, dict)
        and artifact.get("path") == manifest_policy_path
        for artifact in artifacts
    ):
        raise ReleaseAuthorizationError("public bundle manifest must exclude itself")
    _validate_manifest_artifact_paths(manifest)


def _assert_clean_reviewed_checkout(root: Path, source_commit: str) -> None:
    head = _resolve_object(root, "HEAD^{commit}", "HEAD commit")
    if head != source_commit:
        raise ReleaseAuthorizationError("HEAD does not match the reviewed source commit")
    status_result = _git(
        root,
        [
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ],
    )
    if status_result.stdout:
        raise ReleaseAuthorizationError(
            "release worktree is not clean; tracked and untracked changes are forbidden"
        )


def _assert_trusted_ancestry(
    root: Path, source_commit: str, trusted_policy_ref: str
) -> str:
    trusted_commit = _resolve_object(
        root, f"{trusted_policy_ref}^{{commit}}", "trusted policy commit"
    )
    result = _git(
        root,
        ["merge-base", "--is-ancestor", source_commit, trusted_commit],
        allow_failure=True,
    )
    if result.returncode != 0:
        raise ReleaseAuthorizationError(
            "release commit is not contained in the trusted branch"
        )
    return trusted_commit


def _assert_manifest_commit_scope(
    root: Path, source_commit: str, trusted_policy_commit: str
) -> None:
    parent = _resolve_object(
        root, f"{trusted_policy_commit}^{{commit}}^", "trusted manifest parent"
    )
    if parent != source_commit:
        raise ReleaseAuthorizationError(
            "trusted manifest commit is not the immediate child of the reviewed source"
        )
    raw_changed = _git(
        root,
        ["diff", "--name-only", "--no-renames", "-z", source_commit, trusted_policy_commit],
    ).stdout
    try:
        changed = raw_changed.decode("utf-8", errors="strict").split("\x00")
    except UnicodeDecodeError as error:
        raise ReleaseAuthorizationError(
            "trusted manifest commit paths are not valid UTF-8"
        ) from error
    allowed = {"policy/public-source-release.json"}
    if {path for path in changed if path} - allowed:
        raise ReleaseAuthorizationError(
            "trusted manifest commit contains unrelated file changes"
        )


def _parse_annotation(message: str) -> dict[str, str]:
    lines = message.replace("\r\n", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].strip() != AUTHORIZATION_SCHEMA:
        raise ReleaseAuthorizationError("release tag annotation schema is invalid")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("-----BEGIN PGP SIGNATURE-----"):
            break
        if not line.strip():
            continue
        if ":" not in line:
            raise ReleaseAuthorizationError("release tag annotation is malformed")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in fields:
            raise ReleaseAuthorizationError("release tag annotation has duplicate fields")
        fields[key] = value
    if set(fields) != REQUIRED_ANNOTATION_FIELDS:
        raise ReleaseAuthorizationError(
            "release tag annotation has missing or unknown fields"
        )
    return fields


def _tag_annotation(
    root: Path, tag_ref: str, expected_tag_name: str
) -> dict[str, str]:
    object_type = _decode_ascii(
        _git(root, ["cat-file", "-t", tag_ref]).stdout,
        "release tag type",
    )
    if object_type != "tag":
        raise ReleaseAuthorizationError("release authorization requires an annotated tag")
    tag_object = _git(root, ["cat-file", "tag", tag_ref]).stdout
    header, separator, raw_message = tag_object.partition(b"\n\n")
    if not separator:
        raise ReleaseAuthorizationError("release tag object is malformed")
    try:
        header_lines = header.decode("utf-8", errors="strict").splitlines()
        message = raw_message.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ReleaseAuthorizationError(
            "release tag object is not valid UTF-8"
        ) from error
    tag_header = [line[4:] for line in header_lines if line.startswith("tag ")]
    if tag_header != [expected_tag_name]:
        raise ReleaseAuthorizationError(
            "replayed tag object does not bind the requested tag name"
        )
    if not raw_message:
        raise ReleaseAuthorizationError("release tag annotation is missing")
    return _parse_annotation(message)


def _verify_openpgp_signature(root: Path, tag_ref: str) -> str:
    result = _git(root, ["verify-tag", "--raw", tag_ref], allow_failure=True)
    if result.returncode != 0:
        raise ReleaseAuthorizationError("release tag signature verification failed")
    identities: list[tuple[str, str]] = []
    marker = b"[GNUPG:] VALIDSIG "
    for raw_line in (result.stderr + b"\n" + result.stdout).splitlines():
        if not raw_line.startswith(marker):
            continue
        try:
            fields = raw_line[len(marker) :].decode("ascii", errors="strict").split()
        except UnicodeDecodeError as error:
            raise ReleaseAuthorizationError(
                "release tag signature status is not valid ASCII"
            ) from error
        if len(fields) not in {9, 10}:
            raise ReleaseAuthorizationError(
                "release tag signature status is incomplete"
            )
        signing_fingerprint = fields[0].upper()
        primary_fingerprint = fields[9].upper() if len(fields) == 10 else signing_fingerprint
        if not FINGERPRINT_RE.fullmatch(signing_fingerprint):
            raise ReleaseAuthorizationError(
                "release tag signature did not expose a full signing fingerprint"
            )
        if not FINGERPRINT_RE.fullmatch(primary_fingerprint):
            primary_fingerprint = signing_fingerprint
        identities.append((signing_fingerprint, primary_fingerprint))
    primary_identities = {primary for _signing, primary in identities}
    if len(identities) != 1 or len(primary_identities) != 1:
        raise ReleaseAuthorizationError(
            "release tag signature did not expose exactly one OpenPGP identity"
        )
    return identities[0][1]


def verify_release_authorization(
    root: Path,
    tag_name: str,
    manifest_path: Path,
    *,
    signature_verifier: SignatureVerifier | None = None,
    trusted_policy_ref: str = "refs/remotes/origin/main",
) -> dict[str, Any]:
    """Return a public-safe verification receipt without changing any state."""
    root = root.resolve()
    manifest, manifest_bytes = _read_manifest(manifest_path.resolve())
    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not OBJECT_ID_RE.fullmatch(source_commit):
        raise ReleaseAuthorizationError(
            "public bundle manifest has an invalid source commit"
        )
    resolved_source = _resolve_object(
        root, f"{source_commit}^{{commit}}", "reviewed source commit"
    )
    if resolved_source != source_commit:
        raise ReleaseAuthorizationError(
            "public bundle manifest source commit is not canonical"
        )
    trusted_policy_commit = _assert_trusted_ancestry(
        root, source_commit, trusted_policy_ref
    )
    _assert_manifest_commit_scope(root, source_commit, trusted_policy_commit)
    _assert_clean_reviewed_checkout(root, trusted_policy_commit)

    policy = _load_committed_policy(root, trusted_policy_ref)
    authorization_policy = _authorization_policy(policy)
    _assert_manifest_self_exclusion(
        manifest, authorization_policy["manifest_path"]
    )
    try:
        expected_manifest = build_source_release_manifest(
            root, source_commit, policy_commit=trusted_policy_ref
        )
    except PublicSourceReleaseError as error:
        raise ReleaseAuthorizationError(
            "reviewed source cannot reproduce the public bundle manifest"
        ) from error
    if manifest != expected_manifest:
        raise ReleaseAuthorizationError(
            "public bundle manifest does not reproduce from the reviewed source commit"
        )
    tag_ref = _validate_tag_name(
        tag_name, authorization_policy["required_tag_prefix"]
    )
    annotation = _tag_annotation(root, tag_ref, tag_name)
    tag_commit = _resolve_object(root, f"{tag_ref}^{{commit}}", "release tag commit")
    source_tree = _resolve_object(
        root, f"{source_commit}^{{tree}}", "reviewed source tree"
    )
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    expected_annotation = {
        "release_id": authorization_policy["release_id"],
        "source_commit": source_commit,
        "source_tree": source_tree,
        "bundle_manifest_sha256": manifest_digest,
        "maturity": authorization_policy["required_maturity"],
        "trusted_policy_commit": trusted_policy_commit,
    }
    if tag_commit != trusted_policy_commit:
        raise ReleaseAuthorizationError(
            "release tag does not point to the trusted manifest commit"
        )
    if annotation != expected_annotation:
        raise ReleaseAuthorizationError(
            "release tag annotation does not bind the reviewed source and bundle"
        )

    fingerprints = authorization_policy["allowed_signer_fingerprints"]
    if not fingerprints:
        raise ReleaseAuthorizationError(
            "release policy has no allowed signer fingerprint pinned"
        )
    verifier = signature_verifier or _verify_openpgp_signature
    fingerprint = verifier(root, tag_ref).upper()
    if not FINGERPRINT_RE.fullmatch(fingerprint):
        raise ReleaseAuthorizationError(
            "release tag signature identity is not a full fingerprint"
        )
    if fingerprint not in {value.upper() for value in fingerprints}:
        raise ReleaseAuthorizationError(
            "release tag signature identity is not pinned in publication policy"
        )

    return {
        "schema_version": AUTHORIZATION_SCHEMA,
        "release_id": authorization_policy["release_id"],
        "tag": tag_name,
        "release_commit": tag_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "bundle_manifest_sha256": manifest_digest,
        "maturity": authorization_policy["required_maturity"],
        "signer_fingerprint": fingerprint,
        "trusted_policy_commit": trusted_policy_commit,
        "clean_checkout_attested": True,
        "manifest_self_excluded": True,
        "publication_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the signed owner authorization for a Stage 2 release."
    )
    parser.add_argument("--verify-tag", required=True)
    parser.add_argument(
        "--trusted-policy-ref",
        default="refs/remotes/origin/main",
        help="Protected branch ref that owns the signer policy and must contain the tag commit.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help=(
            "Deterministic public bundle manifest derived outside the reviewed "
            "commit (defaults to policy/public-source-release.json)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest_path = arguments.manifest or (
        root / "policy" / "public-source-release.json"
    )
    try:
        receipt = verify_release_authorization(
            root,
            arguments.verify_tag,
            manifest_path,
            trusted_policy_ref=arguments.trusted_policy_ref,
        )
    except ReleaseAuthorizationError as error:
        print(f"Stage 2 release authorization failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
