#!/usr/bin/env python3
"""Verify the repository against its public evidence and disclosure policy."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator


SKIPPED_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
MAX_TEXT_FILE_BYTES = 2_000_000
VISUAL_ASSET_DIRECTORY = "docs/company-playbook/assets/infographics"
VISUAL_ASSET_MANIFEST = f"{VISUAL_ASSET_DIRECTORY}/manifest.json"
VISUAL_ASSET_SCHEMA = "visual-asset-register/v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SIGNER_FINGERPRINT_RE = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
VISUAL_REQUIRED_STRING_FIELDS = ("id", "title", "decision", "filename", "sha256")
VISUAL_REQUIRED_INTEGER_FIELDS = ("bytes", "width", "height")
MANDATORY_EVIDENCE_FIELDS = {
    "evidence_status",
    "public_safe",
    "maturity",
    "limitations",
}
MANDATORY_CANONICAL_SOURCE_FIELDS = {
    "source",
    "owner",
    "version",
    "evidence_status",
    "sensitivity",
    "permitted_use",
    "review_state",
    "replacement_or_expiry",
    "knowledge_type",
    "authority_scope",
    "conflict_policy",
    "generated_content_authority",
    "visual_evidence_boundary",
    "regression_trigger",
    "outcome_evidence",
    "research_as_of",
    "source_freshness",
}
NONEMPTY_STRING_LIST_POLICY_KEYS = {
    "allowed_evidence_statuses",
    "allowed_knowledge_types",
    "allowed_maturities",
    "allowed_review_states",
    "allowed_sensitivities",
    "allowed_visual_evidence_boundaries",
    "canonical_source_globs",
    "required_journey_fields",
    "required_canonical_source_fields",
    "evidence_artifact_globs",
    "forbidden_file_names",
    "forbidden_file_suffixes",
    "forbidden_path_globs",
    "required_root_files",
    "required_source_release_paths",
    "source_release_globs",
}


class PolicyValidationError(ValueError):
    """Raised when the publication policy cannot enforce its required controls."""


class RepositoryInventoryError(RuntimeError):
    """Raised when a Git worktree's publishable paths cannot be inventoried."""


def policy_error(message: str) -> PolicyValidationError:
    return PolicyValidationError(f"invalid publication policy: {message}")


def _validate_relative_policy_path(
    value: str,
    *,
    key: str,
    allow_glob: bool,
) -> None:
    if "\\" in value:
        raise policy_error(f"'{key}' entries must use repository-relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {"", "."}:
        raise policy_error(f"'{key}' contains an unsafe path: {value!r}")
    if not allow_glob and any(character in value for character in "*?["):
        raise policy_error(f"'{key}' must contain exact paths, not globs: {value!r}")


def validate_policy(policy: Any) -> None:
    if not isinstance(policy, dict):
        raise policy_error("top-level value must be an object")

    required_keys = NONEMPTY_STRING_LIST_POLICY_KEYS | {
        "current_maturity",
        "forbidden_text_patterns",
        "allowed_binary_files",
        "release_authorization",
        "claim_boundaries",
    }
    missing_keys = sorted(required_keys - policy.keys())
    if missing_keys:
        raise policy_error(f"missing required key(s): {', '.join(missing_keys)}")

    for key in sorted(NONEMPTY_STRING_LIST_POLICY_KEYS):
        value = policy[key]
        if not isinstance(value, list) or not value:
            raise policy_error(f"'{key}' must be a nonempty list")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise policy_error(f"'{key}' must contain only nonempty strings")

    current_maturity = policy["current_maturity"]
    if not isinstance(current_maturity, str) or not current_maturity.strip():
        raise policy_error("'current_maturity' must be a nonempty string")
    if current_maturity not in policy["allowed_maturities"]:
        raise policy_error("'current_maturity' must appear in 'allowed_maturities'")

    required_fields = set(policy["required_journey_fields"])
    missing_fields = sorted(MANDATORY_EVIDENCE_FIELDS - required_fields)
    if missing_fields:
        raise policy_error(
            "'required_journey_fields' is missing mandatory field(s): "
            + ", ".join(missing_fields)
        )

    required_canonical_fields = set(policy["required_canonical_source_fields"])
    missing_canonical_fields = sorted(
        MANDATORY_CANONICAL_SOURCE_FIELDS - required_canonical_fields
    )
    if missing_canonical_fields:
        raise policy_error(
            "'required_canonical_source_fields' is missing mandatory field(s): "
            + ", ".join(missing_canonical_fields)
        )

    patterns = policy["forbidden_text_patterns"]
    if not isinstance(patterns, list) or not patterns:
        raise policy_error("'forbidden_text_patterns' must be a nonempty list")
    for index, entry in enumerate(patterns):
        if not isinstance(entry, dict):
            raise policy_error(
                f"'forbidden_text_patterns[{index}]' must be an object"
            )
        name = entry.get("name")
        pattern = entry.get("pattern")
        if not isinstance(name, str) or not name.strip():
            raise policy_error(
                f"'forbidden_text_patterns[{index}].name' must be a nonempty string"
            )
        if not isinstance(pattern, str) or not pattern:
            raise policy_error(
                f"'forbidden_text_patterns[{index}].pattern' must be a nonempty string"
            )
        try:
            re.compile(pattern)
        except re.error as error:
            raise policy_error(
                f"invalid regex for forbidden text pattern '{name}': {error}"
            ) from error

    binary_files = policy["allowed_binary_files"]
    if not isinstance(binary_files, list) or any(
        not isinstance(item, str) or not item.strip() for item in binary_files
    ):
        raise policy_error("'allowed_binary_files' must be a list of exact paths")

    for key in (
        "required_root_files",
        "required_source_release_paths",
        "allowed_binary_files",
    ):
        for value in policy[key]:
            _validate_relative_policy_path(value, key=key, allow_glob=False)
    for key in (
        "canonical_source_globs",
        "evidence_artifact_globs",
        "forbidden_path_globs",
        "source_release_globs",
    ):
        for value in policy[key]:
            _validate_relative_policy_path(value, key=key, allow_glob=True)

    release_authorization = policy["release_authorization"]
    expected_release_fields = {
        "schema_version",
        "release_id",
        "required_tag_prefix",
        "manifest_path",
        "manifest_self_excluded",
        "required_maturity",
        "allowed_signer_fingerprints",
        "public_key_path",
        "authorization_status",
    }
    if not isinstance(release_authorization, dict):
        raise policy_error("'release_authorization' must be an object")
    if set(release_authorization) != expected_release_fields:
        raise policy_error(
            "'release_authorization' has missing or unknown field(s)"
        )
    if (
        release_authorization["schema_version"]
        != "stage2-release-authorization-policy/v1"
    ):
        raise policy_error("unsupported release authorization schema")
    for field in (
        "release_id",
        "required_tag_prefix",
        "manifest_path",
        "required_maturity",
        "authorization_status",
    ):
        value = release_authorization[field]
        if not isinstance(value, str) or not value.strip():
            raise policy_error(
                f"'release_authorization.{field}' must be a nonempty string"
            )
    _validate_relative_policy_path(
        release_authorization["manifest_path"],
        key="release_authorization.manifest_path",
        allow_glob=False,
    )
    if release_authorization["manifest_self_excluded"] is not True:
        raise policy_error("release manifest self-exclusion must be true")
    if release_authorization["required_maturity"] != current_maturity:
        raise policy_error(
            "release authorization maturity must equal current maturity"
        )
    fingerprints = release_authorization["allowed_signer_fingerprints"]
    if not isinstance(fingerprints, list) or any(
        not isinstance(value, str) or not SIGNER_FINGERPRINT_RE.fullmatch(value)
        for value in fingerprints
    ):
        raise policy_error(
            "allowed signer identities must be full hexadecimal fingerprints"
        )
    if len({value.upper() for value in fingerprints}) != len(fingerprints):
        raise policy_error("allowed signer fingerprints must be unique")
    if len(fingerprints) > 1:
        raise policy_error("release policy must pin at most one signer identity")
    public_key_path = release_authorization["public_key_path"]
    if fingerprints:
        if not isinstance(public_key_path, str) or not public_key_path.strip():
            raise policy_error(
                "a pinned signer identity requires a public signing key path"
            )
        _validate_relative_policy_path(
            public_key_path,
            key="release_authorization.public_key_path",
            allow_glob=False,
        )
        if public_key_path not in policy["required_source_release_paths"]:
            raise policy_error(
                "the public signing key must be a required source-release path"
            )
        if not any(
            fnmatch.fnmatchcase(public_key_path, pattern)
            for pattern in policy["source_release_globs"]
        ):
            raise policy_error(
                "the public signing key must be included in source release globs"
            )
    elif public_key_path is not None:
        raise policy_error(
            "an unpinned release policy must not declare a public signing key path"
        )
    authorization_status = release_authorization["authorization_status"]
    if authorization_status not in {
        "awaiting-owner-signed-tag",
        "authorized-by-owner-signed-tag",
    }:
        raise policy_error("unsupported release authorization status")
    if authorization_status != "awaiting-owner-signed-tag":
        raise policy_error(
            "repository policy cannot claim tag authorization; verify the Git tag"
        )
    manifest_path = release_authorization["manifest_path"]
    if any(
        fnmatch.fnmatchcase(manifest_path, pattern)
        for pattern in policy["source_release_globs"]
    ):
        raise policy_error(
            "release manifest path must be excluded from source release globs"
        )
    if manifest_path in policy["required_source_release_paths"]:
        raise policy_error(
            "release manifest path must be excluded from its own source closure"
        )

    claim_boundaries = policy["claim_boundaries"]
    expected_claim_fields = {
        "evidence_class",
        "supported_maturity",
        "maximum_without_independent_human_evidence",
        "human_evidence",
        "realised_value",
        "pilot_or_production_authority",
        "publication_requires_owner_signed_tag",
    }
    if not isinstance(claim_boundaries, dict):
        raise policy_error("'claim_boundaries' must be an object")
    if set(claim_boundaries) != expected_claim_fields:
        raise policy_error("'claim_boundaries' has missing or unknown field(s)")
    exact_claim_values = {
        "evidence_class": "creator-evaluated-synthetic",
        "supported_maturity": current_maturity,
        "maximum_without_independent_human_evidence": "local-mvp",
        "human_evidence": "not-observed",
        "realised_value": "not-observed",
        "pilot_or_production_authority": False,
        "publication_requires_owner_signed_tag": True,
    }
    for field, expected in exact_claim_values.items():
        if claim_boundaries.get(field) != expected:
            raise policy_error(f"unsupported claim boundary field '{field}'")
    maturity_ceiling = claim_boundaries[
        "maximum_without_independent_human_evidence"
    ]
    if maturity_ceiling not in policy["allowed_maturities"]:
        raise policy_error("claim-boundary maturity ceiling is unsupported")
    if policy["allowed_maturities"].index(current_maturity) > policy[
        "allowed_maturities"
    ].index(maturity_ceiling):
        raise policy_error("current maturity exceeds the no-human evidence ceiling")


def load_policy(root: Path) -> dict[str, Any]:
    policy_path = root / "policy" / "publication-policy.json"
    try:
        policy_text = policy_path.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        raise FileNotFoundError(f"missing publication policy: {policy_path}")
    policy = json.loads(policy_text)
    validate_policy(policy)
    return policy


def _git_publishable_files(root: Path) -> list[Path] | None:
    try:
        worktree_probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except FileNotFoundError as error:
        if (root / ".git").exists():
            raise RepositoryInventoryError(
                "cannot inventory Git-publishable files: git is unavailable"
            ) from error
        return None

    if worktree_probe.returncode != 0 or worktree_probe.stdout.strip() != "true":
        return None

    inventory = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if inventory.returncode != 0:
        detail = os.fsdecode(inventory.stderr).strip() or "unknown git error"
        raise RepositoryInventoryError(
            f"cannot inventory Git-publishable files: {detail}"
        )

    files = []
    for raw_relative in inventory.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = Path(os.fsdecode(raw_relative))
        candidate = root / relative
        if candidate.is_file():
            files.append(candidate)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def repository_files(root: Path) -> Iterator[Path]:
    git_files = _git_publishable_files(root)
    if git_files is not None:
        yield from git_files
        return

    for directory, directory_names, file_names in os.walk(root, topdown=True):
        directory_names[:] = sorted(
            name for name in directory_names if name not in SKIPPED_DIRECTORIES
        )
        for file_name in sorted(file_names):
            yield Path(directory) / file_name


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def parse_front_matter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return values
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return None


def check_required_files(root: Path, policy: dict[str, Any]) -> list[str]:
    return [
        f"missing required file: {relative_path}"
        for relative_path in policy["required_root_files"]
        if not (root / relative_path).is_file()
    ]


def _matches_forbidden_path(relative: str, pattern: str) -> bool:
    lowered_relative = relative.lower()
    lowered_pattern = pattern.lower()
    if "/" not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(lowered_relative).name, lowered_pattern)
    return fnmatch.fnmatchcase(lowered_relative, lowered_pattern)


def check_file_paths(root: Path, files: list[Path], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden_names = {name.lower() for name in policy["forbidden_file_names"]}
    forbidden_suffixes = tuple(
        suffix.lower() for suffix in policy["forbidden_file_suffixes"]
    )

    for path in files:
        relative = path.relative_to(root).as_posix()
        lowered_name = path.name.lower()
        if lowered_name in forbidden_names:
            errors.append(f"forbidden file name: {relative}")
        if lowered_name.endswith(forbidden_suffixes):
            errors.append(f"forbidden file suffix: {relative}")
        if any(
            _matches_forbidden_path(relative, pattern)
            for pattern in policy["forbidden_path_globs"]
        ):
            errors.append(f"forbidden path: {relative}")
    return errors


def check_scannable_files(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[str]:
    allowed_binary_files = {
        relative.casefold() for relative in policy["allowed_binary_files"]
    }
    return [
        f"unscannable file is not allowlisted: {path.relative_to(root).as_posix()}"
        for path, text in text_by_path.items()
        if text is None
        and path.relative_to(root).as_posix().casefold() not in allowed_binary_files
    ]


def _visual_png_policy_paths(policy: dict[str, Any], key: str) -> list[str]:
    prefix = f"{VISUAL_ASSET_DIRECTORY}/"
    return [
        value
        for value in policy[key]
        if value.startswith(prefix) and PurePosixPath(value).suffix.lower() == ".png"
    ]


def _duplicate_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for path in paths:
        key = path.casefold()
        if key in seen:
            duplicates.add(path)
        seen.add(key)
    return sorted(duplicates, key=str.casefold)


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _resolve_visual_manifest_reference(value: str) -> str | None:
    if "\\" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        return None

    parts = list(PurePosixPath(VISUAL_ASSET_MANIFEST).parent.parts)
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        return None
    return PurePosixPath(*parts).as_posix()


def _png_dimensions(payload: bytes) -> tuple[int, int] | str:
    if len(payload) < 33:
        return "file is too short to contain a PNG signature and IHDR chunk"
    if payload[:8] != PNG_SIGNATURE:
        return "PNG signature is invalid"

    ihdr_length = int.from_bytes(payload[8:12], "big")
    if ihdr_length != 13:
        return f"first PNG chunk has IHDR length {ihdr_length}, expected 13"
    if payload[12:16] != b"IHDR":
        return "first PNG chunk is not IHDR"

    ihdr_data = payload[16:29]
    expected_crc = int.from_bytes(payload[29:33], "big")
    actual_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        return "IHDR CRC is invalid"

    width = int.from_bytes(ihdr_data[:4], "big")
    height = int.from_bytes(ihdr_data[4:8], "big")
    if width <= 0 or height <= 0:
        return "IHDR width and height must be positive"
    return width, height


def check_visual_asset_snapshot(
    policy: dict[str, Any],
    manifest_payload: bytes | None,
    asset_payloads: dict[str, bytes],
    *,
    visual_control_present: bool = False,
    repository_payload_loader: Callable[[str], bytes | None] | None = None,
) -> list[str]:
    """Validate one visual corpus from an explicit path-to-bytes snapshot."""
    errors: list[str] = []
    allowed_paths = _visual_png_policy_paths(policy, "allowed_binary_files")
    release_paths = _visual_png_policy_paths(policy, "required_source_release_paths")

    visual_control_present = visual_control_present or (
        bool(allowed_paths)
        or bool(release_paths)
        or manifest_payload is not None
        or bool(asset_payloads)
    )
    if not visual_control_present:
        return []

    for key, paths in (
        ("allowed_binary_files", allowed_paths),
        ("required_source_release_paths", release_paths),
    ):
        for duplicate in _duplicate_paths(paths):
            errors.append(f"duplicate infographic PNG path in {key}: {duplicate}")

    if manifest_payload is None:
        errors.append(f"visual asset manifest is missing: {VISUAL_ASSET_MANIFEST}")
        return errors

    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        errors.append(f"invalid visual asset manifest: {error}")
        return errors

    if not isinstance(manifest, dict):
        return errors + ["invalid visual asset manifest: top-level value must be an object"]
    if manifest.get("schema_version") != VISUAL_ASSET_SCHEMA:
        errors.append(
            "invalid visual asset manifest: 'schema_version' must be "
            f"'{VISUAL_ASSET_SCHEMA}'"
        )

    prompt_path = manifest.get("source_prompt_path")
    prompt_sha256 = manifest.get("source_prompt_repository_sha256")
    if prompt_path is not None or prompt_sha256 is not None:
        if not isinstance(prompt_path, str) or not prompt_path.strip():
            errors.append(
                "invalid visual asset manifest: 'source_prompt_path' must be a "
                "nonempty repository-relative path when prompt provenance is present"
            )
        elif (resolved_prompt_path := _resolve_visual_manifest_reference(prompt_path)) is None:
            errors.append(
                "invalid visual asset manifest: 'source_prompt_path' resolves outside "
                "the repository or uses an unsafe path"
            )
        elif (
            not isinstance(prompt_sha256, str)
            or not SHA256_RE.fullmatch(prompt_sha256)
        ):
            errors.append(
                "invalid visual asset manifest: 'source_prompt_repository_sha256' "
                "must be 64 lowercase hexadecimal characters"
            )
        elif repository_payload_loader is None:
            errors.append(
                "visual source prompt cannot be verified without a repository payload loader"
            )
        else:
            prompt_payload = repository_payload_loader(resolved_prompt_path)
            if prompt_payload is None:
                errors.append(
                    f"visual source prompt file is missing: {resolved_prompt_path}"
                )
            elif hashlib.sha256(prompt_payload).hexdigest() != prompt_sha256:
                errors.append(
                    f"visual source prompt SHA-256 mismatch: {resolved_prompt_path}"
                )

    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("invalid visual asset manifest: 'assets' must be a nonempty list")
        return errors

    manifest_paths: dict[str, tuple[str, dict[str, Any]]] = {}
    for index, asset in enumerate(assets):
        location = f"assets[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"invalid visual asset manifest: '{location}' must be an object")
            continue

        invalid_entry = False
        for field in VISUAL_REQUIRED_STRING_FIELDS:
            value = asset.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    "invalid visual asset manifest: "
                    f"'{location}.{field}' must be a nonempty string"
                )
                invalid_entry = True
        for field in VISUAL_REQUIRED_INTEGER_FIELDS:
            if not _positive_integer(asset.get(field)):
                errors.append(
                    "invalid visual asset manifest: "
                    f"'{location}.{field}' must be a positive integer"
                )
                invalid_entry = True
        sha256 = asset.get("sha256")
        if isinstance(sha256, str) and not SHA256_RE.fullmatch(sha256):
            errors.append(
                "invalid visual asset manifest: "
                f"'{location}.sha256' must be 64 lowercase hexadecimal characters"
            )
            invalid_entry = True

        filename = asset.get("filename")
        if isinstance(filename, str):
            filename_path = PurePosixPath(filename)
            if (
                "\\" in filename
                or filename_path.is_absolute()
                or filename_path.name != filename
                or filename_path.suffix.lower() != ".png"
            ):
                errors.append(
                    "invalid visual asset manifest: "
                    f"'{location}.filename' must be a simple PNG filename"
                )
                invalid_entry = True

        if invalid_entry:
            continue

        relative = f"{VISUAL_ASSET_DIRECTORY}/{filename}"
        key = relative.casefold()
        if key in manifest_paths:
            errors.append(f"duplicate visual asset filename in manifest: {filename}")
            continue
        manifest_paths[key] = (relative, asset)

    manifest_path_set = set(manifest_paths)
    for policy_key, paths in (
        ("allowed_binary_files", allowed_paths),
        ("required_source_release_paths", release_paths),
    ):
        policy_by_key = {path.casefold(): path for path in paths}
        for key in sorted(manifest_path_set - set(policy_by_key)):
            errors.append(
                f"visual asset manifest path is missing from {policy_key}: "
                f"{manifest_paths[key][0]}"
            )
        for key in sorted(set(policy_by_key) - manifest_path_set):
            errors.append(
                f"infographic PNG in {policy_key} is absent from visual asset manifest: "
                f"{policy_by_key[key]}"
            )

    disk_paths = {relative.casefold(): relative for relative in asset_payloads}

    for key in sorted(set(disk_paths) - manifest_path_set):
        errors.append(
            "infographic PNG on disk is absent from visual asset manifest: "
            f"{disk_paths[key]}"
        )
    for key in sorted(manifest_path_set - set(disk_paths)):
        errors.append(f"visual asset file is missing: {manifest_paths[key][0]}")

    for key in sorted(manifest_path_set & set(disk_paths)):
        relative, asset = manifest_paths[key]
        payload = asset_payloads[key]

        actual_bytes = len(payload)
        if actual_bytes != asset["bytes"]:
            errors.append(
                f"visual asset byte count mismatch: {relative} "
                f"(manifest {asset['bytes']}, actual {actual_bytes})"
            )
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != asset["sha256"]:
            errors.append(f"visual asset SHA-256 mismatch: {relative}")

        dimensions = _png_dimensions(payload)
        if isinstance(dimensions, str):
            errors.append(f"invalid PNG visual asset: {relative}: {dimensions}")
            continue
        actual_width, actual_height = dimensions
        if actual_width != asset["width"] or actual_height != asset["height"]:
            errors.append(
                f"visual asset dimension mismatch: {relative} "
                f"(manifest {asset['width']}x{asset['height']}, "
                f"actual {actual_width}x{actual_height})"
            )

    return errors


def check_visual_asset_manifest(root: Path, policy: dict[str, Any]) -> list[str]:
    """Bind working-tree infographic paths to reviewed bytes and dimensions."""
    errors: list[str] = []
    manifest_path = root / VISUAL_ASSET_MANIFEST
    asset_directory = root / VISUAL_ASSET_DIRECTORY
    manifest_payload: bytes | None = None
    asset_payloads: dict[str, bytes] = {}

    def load_repository_payload(relative: str) -> bytes | None:
        path = root / PurePosixPath(relative)
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError as error:
            errors.append(f"cannot read visual source prompt {relative}: {error}")
            return None

    if manifest_path.is_file():
        try:
            manifest_payload = manifest_path.read_bytes()
        except OSError as error:
            errors.append(f"cannot read visual asset manifest: {error}")

    if asset_directory.is_dir():
        try:
            asset_paths = [
                path
                for path in asset_directory.iterdir()
                if path.is_file() and path.suffix.lower() == ".png"
            ]
        except OSError as error:
            errors.append(f"cannot inventory visual asset directory: {error}")
            asset_paths = []
        for path in asset_paths:
            relative = path.relative_to(root).as_posix()
            try:
                asset_payloads[relative.casefold()] = path.read_bytes()
            except OSError as error:
                errors.append(f"cannot read visual asset file {relative}: {error}")
    elif (
        _visual_png_policy_paths(policy, "allowed_binary_files")
        or _visual_png_policy_paths(policy, "required_source_release_paths")
    ):
        errors.append(f"visual asset directory is missing: {VISUAL_ASSET_DIRECTORY}")

    errors.extend(
        check_visual_asset_snapshot(
            policy,
            manifest_payload,
            asset_payloads,
            visual_control_present=asset_directory.exists(),
            repository_payload_loader=load_repository_payload,
        )
    )
    return errors


def check_text_patterns(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    patterns = [
        (entry["name"], re.compile(entry["pattern"]))
        for entry in policy["forbidden_text_patterns"]
    ]

    for path, text in text_by_path.items():
        if text is None:
            continue
        relative = path.relative_to(root).as_posix()
        for name, pattern in patterns:
            match = pattern.search(text)
            if match:
                line_number = text.count("\n", 0, match.start()) + 1
                errors.append(f"forbidden text pattern '{name}': {relative}:{line_number}")
    return errors


def _evidence_files(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[Path]:
    publishable_paths = set(text_by_path)
    matched_paths: set[Path] = set()
    for pattern in policy["evidence_artifact_globs"]:
        matched_paths.update(
            path
            for path in root.glob(pattern)
            if path in publishable_paths and path.suffix.lower() == ".md"
        )
    return sorted(matched_paths, key=lambda path: path.relative_to(root).as_posix())


def _canonical_source_files(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[Path]:
    publishable_paths = set(text_by_path)
    matched_paths: set[Path] = set()
    for pattern in policy["canonical_source_globs"]:
        matched_paths.update(
            path
            for path in root.glob(pattern)
            if path in publishable_paths and path.suffix.lower() == ".md"
        )
    return sorted(matched_paths, key=lambda path: path.relative_to(root).as_posix())


def check_artifact_metadata(
    relative: str,
    text: str | None,
    policy: dict[str, Any],
    *,
    canonical_source: bool,
) -> list[str]:
    """Validate public metadata without exposing the artifact body."""
    if text is None:
        return [f"evidence artifact must be UTF-8 text: {relative}"]

    metadata = parse_front_matter(text)
    if metadata is None:
        return [f"missing front matter: {relative}"]

    errors: list[str] = []
    for field in policy["required_journey_fields"]:
        if not metadata.get(field):
            errors.append(f"missing evidence field '{field}': {relative}")
    if canonical_source:
        for field in policy["required_canonical_source_fields"]:
            if not metadata.get(field):
                errors.append(
                    f"missing canonical source field '{field}': {relative}"
                )

    evidence_status = metadata.get("evidence_status")
    if evidence_status and evidence_status not in policy["allowed_evidence_statuses"]:
        errors.append(f"unsupported evidence_status: {relative}")
    if metadata.get("public_safe", "").lower() != "true":
        errors.append(f"public_safe must be true: {relative}")

    maturity = metadata.get("maturity")
    allowed_maturities = policy["allowed_maturities"]
    current_maturity = policy["current_maturity"]
    if maturity and maturity not in allowed_maturities:
        errors.append(f"unsupported maturity: {relative}")
    elif (
        maturity
        and allowed_maturities.index(maturity)
        > allowed_maturities.index(current_maturity)
    ):
        errors.append(
            f"maturity '{maturity}' exceeds current maturity "
            f"'{current_maturity}': {relative}"
        )

    if canonical_source:
        constrained_fields = {
            "knowledge_type": "allowed_knowledge_types",
            "review_state": "allowed_review_states",
            "sensitivity": "allowed_sensitivities",
            "visual_evidence_boundary": "allowed_visual_evidence_boundaries",
        }
        for field, policy_key in constrained_fields.items():
            value = metadata.get(field)
            if value and value not in policy[policy_key]:
                errors.append(f"unsupported {field}: {relative}")

        exact_values = {
            "conflict_policy": "surface-and-block-dependent-claims",
            "generated_content_authority": "none",
            "regression_trigger": "material-change",
            "outcome_evidence": "none",
        }
        for field, expected in exact_values.items():
            value = metadata.get(field)
            if value and value != expected:
                errors.append(f"unsupported {field}: {relative}")
    return errors


def check_journey_metadata(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not (root / "journey").is_dir():
        errors.append("missing journey directory")

    evidence_files = set(_evidence_files(root, text_by_path, policy))
    canonical_files = set(_canonical_source_files(root, text_by_path, policy))
    for path in sorted(
        evidence_files | canonical_files,
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        errors.extend(
            check_artifact_metadata(
                relative,
                text_by_path[path],
                policy,
                canonical_source=path in canonical_files,
            )
        )
    return errors


def check_stage2_claim_boundary_artifacts(
    root: Path,
    text_by_path: dict[Path, str | None],
    policy: dict[str, Any],
) -> list[str]:
    """Verify the machine-readable no-human release boundary at local-MVP."""
    if policy["current_maturity"] != "local-mvp":
        return []

    relatives = {
        "summary": "data/stage2/decision-pack/summary.json",
        "decision": "data/stage2/decision-pack/decision-output.json",
        "evidence": "demo/data/evidence-pack.json",
    }
    payloads: dict[str, Any] = {}
    errors: list[str] = []
    for label, relative in relatives.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        text = text_by_path.get(path)
        if text is None:
            errors.append(f"missing Stage 2 claim-boundary artifact: {relative}")
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            errors.append(f"Stage 2 claim boundary is invalid JSON: {relative}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"Stage 2 claim boundary must be an object: {relative}")
            continue
        payloads[label] = payload

    if set(payloads) != set(relatives):
        return errors

    summary = payloads["summary"]
    if summary.get("human_evidence") != "not_observed":
        errors.append("Stage 2 claim boundary inflates human evidence")
    if summary.get("maturity_ceiling") != "local-mvp":
        errors.append("Stage 2 claim boundary exceeds the local-MVP ceiling")

    decision = payloads["decision"]
    next_action = decision.get("next_action")
    if decision.get("authorises_company_pilot") is not False:
        errors.append("Stage 2 claim boundary authorises a company pilot")
    if decision.get("maturity_ceiling") != "local-mvp":
        errors.append("Stage 2 decision exceeds the local-MVP ceiling")
    if (
        not isinstance(next_action, dict)
        or next_action.get("authorises_company_pilot") is not False
    ):
        errors.append("Stage 2 next action authorises a company pilot")

    evidence = payloads["evidence"]
    maturity = evidence.get("maturity")
    boundary = evidence.get("evidence_boundary")
    cases = evidence.get("cases")
    if evidence.get("public_safe") is not True or evidence.get("read_only") is not True:
        errors.append("Stage 2 claim boundary is not public-safe and read-only")
    if (
        not isinstance(maturity, dict)
        or maturity.get("publication_status")
        != "not_authorised_until_valid_signed_release_tag"
        or maturity.get("supported_ceiling") != "local-mvp"
    ):
        errors.append("Stage 2 claim boundary misstates publication maturity")
    expected_boundary = {
        "synthetic": True,
        "human_evidence": "not_observed",
        "independent_validation": False,
        "live_customer_outcome": "not_observed",
        "realised_value": "not_observed",
        "simulated_actions": True,
        "simulated_approvals": True,
        "unsent_communications": True,
    }
    if boundary != expected_boundary:
        errors.append("Stage 2 claim boundary inflates the evidence class")
    if not isinstance(cases, list) or not cases:
        errors.append("Stage 2 claim boundary has no complete case index")
    else:
        allowed_simulation_labels = {"simulated", "not_applicable"}
        for case in cases:
            if not isinstance(case, dict):
                errors.append("Stage 2 claim boundary has an invalid case record")
                break
            if (
                case.get("synthetic") is not True
                or case.get("human_reviewed") is not False
                or case.get("no_realised_value") is not True
                or case.get("validation_label") != "non-independent"
                or case.get("communication_label")
                not in {"unsent", "not_applicable"}
                or case.get("action_label") not in allowed_simulation_labels
                or case.get("approval_label") not in allowed_simulation_labels
            ):
                errors.append("Stage 2 claim boundary inflates a case evidence label")
                break
    return errors


def verify_repository(root: Path) -> list[str]:
    root = root.resolve()
    try:
        policy = load_policy(root)
    except (FileNotFoundError, json.JSONDecodeError, PolicyValidationError) as error:
        return [str(error)]

    try:
        files = list(repository_files(root))
    except RepositoryInventoryError as error:
        return [str(error)]

    text_by_path = {path: read_text(path) for path in files}
    errors: list[str] = []
    errors.extend(check_required_files(root, policy))
    errors.extend(check_file_paths(root, files, policy))
    errors.extend(check_scannable_files(root, text_by_path, policy))
    errors.extend(check_visual_asset_manifest(root, policy))
    errors.extend(check_text_patterns(root, text_by_path, policy))
    errors.extend(check_journey_metadata(root, text_by_path, policy))
    errors.extend(check_stage2_claim_boundary_artifacts(root, text_by_path, policy))
    return sorted(set(errors))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = verify_repository(root)
    if errors:
        print("Public-safety verification failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Public-safety verification passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
