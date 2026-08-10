#!/usr/bin/env python3
"""Verify the repository against its public evidence and disclosure policy."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


SKIPPED_DIRECTORIES = {".git", "__pycache__", ".pytest_cache", ".venv", "venv"}
MAX_TEXT_FILE_BYTES = 2_000_000
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
    errors.extend(check_text_patterns(root, text_by_path, policy))
    errors.extend(check_journey_metadata(root, text_by_path, policy))
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
