import copy
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.verify_public_safety import (
    MAX_TEXT_FILE_BYTES,
    check_text_patterns,
    load_policy,
    verify_repository,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicSafetyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "allowed_evidence_statuses": [
                "research-grounded",
                "synthetic-observed",
            ],
            "allowed_knowledge_types": [
                "client-fact",
                "observation",
                "external-research",
                "raul-method",
                "assumption",
                "hypothesis",
                "recommendation",
                "generated-content",
                "decision",
                "approved-learning",
            ],
            "allowed_maturities": [
                "foundation",
                "specification",
                "local-mvp",
                "independently-reviewed-local-mvp",
                "pilot",
                "production",
            ],
            "current_maturity": "foundation",
            "allowed_review_states": ["accepted-public-source"],
            "allowed_sensitivities": ["public"],
            "allowed_visual_evidence_boundaries": ["explanatory-only"],
            "required_journey_fields": [
                "evidence_status",
                "public_safe",
                "maturity",
                "limitations",
            ],
            "evidence_artifact_globs": ["journey/*.md", "docs/*.md"],
            "canonical_source_globs": [
                "docs/company-playbook/*.md",
                "docs/plans/*.md",
            ],
            "required_canonical_source_fields": [
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
            ],
            "source_release_globs": [
                "policy/publication-policy.json",
                "docs/company-playbook/*.md",
                "docs/plans/*.md",
            ],
            "required_source_release_paths": [
                "policy/publication-policy.json",
                "docs/company-playbook/README.md",
                "docs/plans/rausellos-plan.md",
            ],
            "forbidden_file_names": [".env", "credentials.json"],
            "forbidden_file_suffixes": [".pem", ".key"],
            "forbidden_path_globs": [
                ".env.*",
                "*.sqlite",
                "*.sqlite3",
                "*.db",
                "tmp/**",
                "artifacts/private/**",
                "data/private/**",
            ],
            "forbidden_text_patterns": [
                {"name": "private path", "pattern": r"(?i)[A-Z]:\\Users\\[^\\\s]+"},
                {"name": "test token", "pattern": r"TOKEN_[A-Z0-9]{12}"},
            ],
            "allowed_binary_files": [],
            "required_root_files": ["README.md"],
        }
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.make_repo(self.root)

    def artifact_text(self, **overrides: str | None) -> str:
        metadata: dict[str, str | None] = {
            "evidence_status": "research-grounded",
            "public_safe": "true",
            "maturity": "foundation",
            "limitations": "no evaluated outcome",
            "source": "repository-authored synthesis",
            "owner": "Raul Rausell",
            "version": "2026-08-10",
            "sensitivity": "public",
            "permitted_use": "approved public canonical source",
            "review_state": "accepted-public-source",
            "replacement_or_expiry": "replace only through reviewed source release",
            "knowledge_type": "raul-method",
            "authority_scope": "method guidance; client authority remains external",
            "conflict_policy": "surface-and-block-dependent-claims",
            "generated_content_authority": "none",
            "visual_evidence_boundary": "explanatory-only",
            "regression_trigger": "material-change",
            "outcome_evidence": "none",
            "research_as_of": "2026-08-10",
            "source_freshness": "review-on-import-or-material-change",
        }
        metadata.update(overrides)
        front_matter = "\n".join(
            f"{key}: {value}" for key, value in metadata.items() if value is not None
        )
        return f"---\n{front_matter}\n---\n\n# Journey\n"

    def write_policy(self, root: Path, policy: dict | None = None) -> None:
        (root / "policy" / "publication-policy.json").write_text(
            json.dumps(policy or self.policy), encoding="utf-8"
        )

    def make_repo(self, root: Path) -> None:
        (root / "policy").mkdir(parents=True)
        (root / "journey").mkdir()
        (root / "README.md").write_text("synthetic laboratory\n", encoding="utf-8")
        self.write_policy(root)
        (root / "journey" / "README.md").write_text(
            self.artifact_text(), encoding="utf-8"
        )

    def init_git(self, root: Path) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is required for publishable-file inventory tests")
        subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            capture_output=True,
        )

    @staticmethod
    def png_bytes(width: int = 3, height: int = 2) -> bytes:
        ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        ihdr = (
            struct.pack(">I", len(ihdr_data))
            + b"IHDR"
            + ihdr_data
            + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
        )
        iend = b"\x00\x00\x00\x00IEND\xaeB`\x82"
        return b"\x89PNG\r\n\x1a\n" + ihdr + iend

    def install_visual_fixture(
        self,
        *,
        payload: bytes | None = None,
        width: int = 3,
        height: int = 2,
    ) -> tuple[Path, Path, dict]:
        asset_directory = (
            self.root / "docs" / "company-playbook" / "assets" / "infographics"
        )
        asset_directory.mkdir(parents=True)
        filename = "V01-test-visual-v03-landscape.png"
        relative = f"docs/company-playbook/assets/infographics/{filename}"
        asset_path = asset_directory / filename
        asset_payload = payload if payload is not None else self.png_bytes(width, height)
        asset_path.write_bytes(asset_payload)
        manifest = {
            "schema_version": "visual-asset-register/v1",
            "assets": [
                {
                    "id": "V01",
                    "title": "Test visual",
                    "decision": "Test the byte-integrity control.",
                    "filename": filename,
                    "bytes": len(asset_payload),
                    "width": width,
                    "height": height,
                    "sha256": hashlib.sha256(asset_payload).hexdigest(),
                }
            ],
        }
        manifest_path = asset_directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.policy["allowed_binary_files"] = [relative]
        self.policy["required_source_release_paths"].extend(
            [
                "docs/company-playbook/assets/infographics/manifest.json",
                relative,
            ]
        )
        self.write_policy(self.root)
        return asset_path, manifest_path, manifest

    def commit_all(self, message: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.root), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "--quiet", "-m", message],
            check=True,
            capture_output=True,
        )

    def prepare_visual_source_release_fixture(
        self,
        *,
        include_prompt_provenance: bool = False,
    ) -> tuple[Path, Path, dict, Path | None]:
        self.policy["source_release_globs"] = [
            "policy/publication-policy.json",
            "docs/company-playbook/*.md",
            "docs/company-playbook/assets/infographics/*.png",
            "docs/company-playbook/assets/infographics/manifest.json",
        ]
        self.policy["required_source_release_paths"] = [
            "policy/publication-policy.json"
        ]
        asset_path, manifest_path, manifest = self.install_visual_fixture()
        prompt_path: Path | None = None
        if include_prompt_provenance:
            prompt_path = (
                self.root
                / "docs"
                / "company-playbook"
                / "09_VISUAL_SYSTEM_AND_INFOGRAPHIC_PROMPTS.md"
            )
            prompt_payload = (
                self.artifact_text()
                + "\nPrompt contract for the repository visual series.\n"
            ).encode("utf-8")
            prompt_path.write_bytes(prompt_payload)
            manifest["source_prompt_path"] = (
                "../../09_VISUAL_SYSTEM_AND_INFOGRAPHIC_PROMPTS.md"
            )
            manifest["source_prompt_attachment_sha256"] = hashlib.sha256(
                b"original external attachment"
            ).hexdigest()
            manifest["source_prompt_repository_sha256"] = hashlib.sha256(
                prompt_payload
            ).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.init_git(self.root)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Public Safety Test"],
            check=True,
            capture_output=True,
        )
        self.commit_all("visual source fixture")
        return asset_path, manifest_path, manifest, prompt_path

    def test_valid_repository_passes(self):
        self.assertEqual([], verify_repository(self.root))

    def test_secret_file_and_token_are_rejected(self):
        (self.root / ".env").write_text("TOKEN_ABCDEF123456\n", encoding="utf-8")
        errors = verify_repository(self.root)
        self.assertTrue(any("forbidden file name" in error for error in errors))
        self.assertTrue(any("test token" in error for error in errors))

    def test_private_machine_path_is_rejected(self):
        (self.root / "notes.md").write_text(
            "Local source: C:\\Users\\someone\\private.txt\n", encoding="utf-8"
        )
        errors = verify_repository(self.root)
        self.assertTrue(any("private path" in error for error in errors))

    def test_unix_private_machine_paths_are_rejected_by_production_policy(self):
        notes = self.root / "notes.md"
        text = "\n".join(
            [
                "Local source: " + "/" + "Users/alice/private/client-plan.md",
                "Local source: " + "/" + "home/alice/company-data/orders.csv",
            ]
        )
        errors = check_text_patterns(
            self.root,
            {notes: text},
            load_policy(PROJECT_ROOT),
        )
        self.assertTrue(any("private macOS user path" in error for error in errors))
        self.assertTrue(any("private Linux user path" in error for error in errors))

    def test_missing_journey_metadata_is_rejected(self):
        (self.root / "journey" / "README.md").write_text(
            "# Journey\n", encoding="utf-8"
        )
        errors = verify_repository(self.root)
        self.assertTrue(any("missing front matter" in error for error in errors))

    def test_docs_evidence_metadata_is_required(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "DECISION_LOG.md").write_text(
            "# Decision log\n", encoding="utf-8"
        )
        errors = verify_repository(self.root)
        self.assertTrue(
            any(
                "missing front matter: docs/DECISION_LOG.md" in error
                for error in errors
            )
        )

    def test_unapproved_evidence_status_is_rejected(self):
        path = self.root / "journey" / "README.md"
        path.write_text(
            self.artifact_text(evidence_status="production-proven"),
            encoding="utf-8",
        )
        errors = verify_repository(self.root)
        self.assertTrue(any("unsupported evidence_status" in error for error in errors))

    def test_playbook_rejects_unsupported_evidence_vocabulary_without_content_echo(self):
        playbook = self.root / "docs" / "company-playbook"
        playbook.mkdir(parents=True)
        path = playbook / "01_EXECUTIVE_BLUEPRINT.md"
        sentinel = "PRIVATE_SOURCE_TEXT_MUST_NOT_BE_ECHOED"
        path.write_text(
            self.artifact_text(
                evidence_status="executive-blueprint",
                maturity="reference-design",
            )
            + sentinel,
            encoding="utf-8",
        )

        errors = verify_repository(self.root)

        self.assertIn(
            "unsupported evidence_status: "
            "docs/company-playbook/01_EXECUTIVE_BLUEPRINT.md",
            errors,
        )
        self.assertIn(
            "unsupported maturity: "
            "docs/company-playbook/01_EXECUTIVE_BLUEPRINT.md",
            errors,
        )
        self.assertNotIn(sentinel, "\n".join(errors))

    def test_plan_missing_required_canonical_source_field_is_rejected(self):
        plans = self.root / "docs" / "plans"
        plans.mkdir(parents=True)
        (plans / "rausellos-plan.md").write_text(
            self.artifact_text(owner=None), encoding="utf-8"
        )

        errors = verify_repository(self.root)

        self.assertIn(
            "missing canonical source field 'owner': docs/plans/rausellos-plan.md",
            errors,
        )

    def test_public_safe_false_and_absent_are_rejected(self):
        path = self.root / "journey" / "README.md"
        for value in ("false", None):
            with self.subTest(public_safe=value):
                path.write_text(
                    self.artifact_text(public_safe=value), encoding="utf-8"
                )
                errors = verify_repository(self.root)
                self.assertTrue(any("public_safe must be true" in error for error in errors))

    def test_maturity_above_current_release_is_rejected(self):
        path = self.root / "journey" / "README.md"
        for maturity in ("pilot", "production"):
            with self.subTest(maturity=maturity):
                path.write_text(
                    self.artifact_text(maturity=maturity), encoding="utf-8"
                )
                errors = verify_repository(self.root)
                self.assertTrue(
                    any("exceeds current maturity 'foundation'" in error for error in errors)
                )

    def test_unscannable_files_fail_closed(self):
        payloads = {
            "large.txt": b"a" * (MAX_TEXT_FILE_BYTES + 1),
            "utf16.txt": "private text".encode("utf-16"),
            "invalid.bin": b"\x80\x81\x82",
        }
        for relative, payload in payloads.items():
            with self.subTest(relative=relative):
                path = self.root / relative
                path.write_bytes(payload)
                errors = verify_repository(self.root)
                self.assertTrue(
                    any(
                        error == f"unscannable file is not allowlisted: {relative}"
                        for error in errors
                    )
                )
                path.unlink()

    def test_binary_allowlist_is_exact_not_extension_wide(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x80")
        (assets / "other.png").write_bytes(b"\x89PNG\r\n\x1a\n\x80")
        self.policy["allowed_binary_files"] = ["assets/logo.png"]
        self.write_policy(self.root)

        errors = verify_repository(self.root)

        self.assertFalse(any("assets/logo.png" in error for error in errors))
        self.assertTrue(any("assets/other.png" in error for error in errors))

    def test_visual_asset_manifest_and_png_pass(self):
        self.install_visual_fixture()

        self.assertEqual([], verify_repository(self.root))

    def test_visual_asset_same_path_byte_mutation_is_rejected(self):
        asset_path, _, _ = self.install_visual_fixture()
        asset_path.write_bytes(asset_path.read_bytes() + b"mutated")

        errors = verify_repository(self.root)

        self.assertTrue(any("visual asset byte count mismatch" in error for error in errors))
        self.assertTrue(any("visual asset SHA-256 mismatch" in error for error in errors))

    def test_visual_asset_invalid_png_is_rejected_even_when_hash_matches(self):
        self.install_visual_fixture(payload=b"not a PNG but manifest-bound")

        errors = verify_repository(self.root)

        self.assertTrue(any("invalid PNG visual asset" in error for error in errors))
        self.assertFalse(any("visual asset SHA-256 mismatch" in error for error in errors))

    def test_visual_asset_duplicate_manifest_filename_is_rejected(self):
        _, manifest_path, manifest = self.install_visual_fixture()
        manifest["assets"].append(copy.deepcopy(manifest["assets"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        errors = verify_repository(self.root)

        self.assertTrue(
            any("duplicate visual asset filename in manifest" in error for error in errors)
        )

    def test_visual_asset_missing_and_extra_manifest_entries_are_rejected(self):
        asset_path, manifest_path, manifest = self.install_visual_fixture()
        original_asset = manifest["assets"][0]
        manifest["assets"] = []
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        missing_errors = verify_repository(self.root)

        self.assertTrue(
            any("'assets' must be a nonempty list" in error for error in missing_errors)
        )

        second_payload = self.png_bytes(4, 3)
        second_filename = "V02-extra-visual-v03-landscape.png"
        (asset_path.parent / second_filename).write_bytes(second_payload)
        manifest["assets"] = [
            original_asset,
            {
                "id": "V02",
                "title": "Extra visual",
                "decision": "Exercise manifest-policy parity.",
                "filename": second_filename,
                "bytes": len(second_payload),
                "width": 4,
                "height": 3,
                "sha256": hashlib.sha256(second_payload).hexdigest(),
            },
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        extra_errors = verify_repository(self.root)

        self.assertTrue(
            any(
                "visual asset manifest path is missing from allowed_binary_files"
                in error
                for error in extra_errors
            )
        )
        self.assertTrue(
            any(
                "visual asset manifest path is missing from required_source_release_paths"
                in error
                for error in extra_errors
            )
        )

    def test_visual_asset_policy_entries_must_be_exact_and_unique(self):
        self.install_visual_fixture()
        relative = self.policy["allowed_binary_files"][0]
        extra = relative.replace("V01-test", "V02-extra")
        cases = {
            "missing allowlist": {
                "allowed_binary_files": [],
            },
            "extra allowlist": {
                "allowed_binary_files": [relative, extra],
            },
            "missing release path": {
                "required_source_release_paths": [
                    path
                    for path in self.policy["required_source_release_paths"]
                    if path != relative
                ],
            },
            "extra release path": {
                "required_source_release_paths": [
                    *self.policy["required_source_release_paths"],
                    extra,
                ],
            },
            "duplicate policy paths": {
                "allowed_binary_files": [relative, relative],
                "required_source_release_paths": [
                    *self.policy["required_source_release_paths"],
                    relative,
                ],
            },
        }
        original_policy = copy.deepcopy(self.policy)
        for label, overrides in cases.items():
            with self.subTest(label=label):
                policy = copy.deepcopy(original_policy)
                policy.update(overrides)
                self.write_policy(self.root, policy)
                errors = verify_repository(self.root)
                if label.startswith("missing"):
                    self.assertTrue(any("is missing from" in error for error in errors))
                elif label.startswith("extra"):
                    self.assertTrue(
                        any("is absent from visual asset manifest" in error for error in errors)
                    )
                else:
                    self.assertTrue(
                        any("duplicate infographic PNG path" in error for error in errors)
                    )

    def test_visual_asset_missing_and_unregistered_disk_files_are_rejected(self):
        asset_path, _, _ = self.install_visual_fixture()
        asset_path.unlink()

        missing_errors = verify_repository(self.root)

        self.assertTrue(any("visual asset file is missing" in error for error in missing_errors))

        asset_path.write_bytes(self.png_bytes())
        extra_path = asset_path.parent / "V99-unregistered.png"
        extra_path.write_bytes(self.png_bytes())

        extra_errors = verify_repository(self.root)

        self.assertTrue(
            any("on disk is absent from visual asset manifest" in error for error in extra_errors)
        )

    def test_visual_asset_hash_byte_count_and_dimensions_are_verified(self):
        _, manifest_path, manifest = self.install_visual_fixture()
        original_asset = copy.deepcopy(manifest["assets"][0])
        cases = {
            "hash": {"sha256": "0" * 64},
            "bytes": {"bytes": original_asset["bytes"] + 1},
            "dimensions": {"width": original_asset["width"] + 1},
        }
        expected_messages = {
            "hash": "visual asset SHA-256 mismatch",
            "bytes": "visual asset byte count mismatch",
            "dimensions": "visual asset dimension mismatch",
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                manifest["assets"] = [{**original_asset, **override}]
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                errors = verify_repository(self.root)
                self.assertTrue(
                    any(expected_messages[label] in error for error in errors),
                    errors,
                )

    def test_force_tracked_sensitive_paths_are_rejected(self):
        cases = [
            ".env.production",
            "customer.sqlite",
            "customer.sqlite3",
            "customer.db",
            "tmp/session.txt",
            "artifacts/private/report.txt",
            "data/private/orders.csv",
        ]
        for relative in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.make_repo(root)
                (root / ".gitignore").write_text(
                    ".env.*\n*.sqlite\n*.sqlite3\n*.db\ntmp/\n"
                    "artifacts/private/\ndata/private/\n",
                    encoding="utf-8",
                )
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")
                self.init_git(root)
                subprocess.run(
                    ["git", "-C", str(root), "add", "-f", relative],
                    check=True,
                    capture_output=True,
                )
                errors = verify_repository(root)
                self.assertTrue(
                    any(error == f"forbidden path: {relative}" for error in errors)
                )

    def test_ignored_file_is_excluded_but_force_tracked_file_is_scanned(self):
        (self.root / ".gitignore").write_text(".env\n", encoding="utf-8")
        secret = self.root / ".env"
        secret.write_text("TOKEN_ABCDEF123456\n", encoding="utf-8")
        self.init_git(self.root)

        self.assertEqual([], verify_repository(self.root))

        subprocess.run(
            ["git", "-C", str(self.root), "add", "-f", ".env"],
            check=True,
            capture_output=True,
        )
        errors = verify_repository(self.root)
        self.assertTrue(any("forbidden file name: .env" == error for error in errors))
        self.assertTrue(any("test token" in error for error in errors))

    def test_force_tracked_file_in_skipped_directory_is_scanned(self):
        (self.root / ".gitignore").write_text(".venv/\n", encoding="utf-8")
        leak = self.root / ".venv" / "leak.txt"
        leak.parent.mkdir()
        leak.write_text("TOKEN_ABCDEF123456\n", encoding="utf-8")
        self.init_git(self.root)
        subprocess.run(
            ["git", "-C", str(self.root), "add", "-f", ".venv/leak.txt"],
            check=True,
            capture_output=True,
        )

        errors = verify_repository(self.root)

        self.assertTrue(any("test token" in error for error in errors))

    def test_committed_production_policy_is_valid(self):
        policy = load_policy(PROJECT_ROOT)
        self.assertEqual("foundation", policy["current_maturity"])

    def test_source_release_manifest_reproduces_frozen_commit_hashes(self):
        from scripts.public_source_release import (
            build_source_release_manifest,
            verify_source_release_manifest,
        )

        playbook = self.root / "docs" / "company-playbook"
        plans = self.root / "docs" / "plans"
        playbook.mkdir(parents=True)
        plans.mkdir(parents=True)
        playbook_path = playbook / "README.md"
        plan_path = plans / "rausellos-plan.md"
        playbook_path.write_text(self.artifact_text(), encoding="utf-8")
        plan_path.write_text(
            self.artifact_text(knowledge_type="decision"), encoding="utf-8"
        )
        self.init_git(self.root)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Public Safety Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "--quiet", "-m", "fixture"],
            check=True,
            capture_output=True,
        )

        first = build_source_release_manifest(self.root, "HEAD")
        second = build_source_release_manifest(self.root, "HEAD")
        committed_playbook = subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "show",
                "HEAD:docs/company-playbook/README.md",
            ],
            check=True,
            capture_output=True,
        ).stdout
        playbook_path.write_text("changed after commit\n", encoding="utf-8")

        self.assertEqual(first, second)
        self.assertEqual([], verify_source_release_manifest(self.root, first))
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            first["source_commit"],
        )
        artifacts = {item["path"]: item["sha256"] for item in first["artifacts"]}
        self.assertEqual(
            hashlib.sha256(committed_playbook).hexdigest(),
            artifacts["docs/company-playbook/README.md"],
        )
        self.assertEqual(sorted(artifacts), list(artifacts))

    def test_source_release_rejects_committed_same_path_visual_mutation(self):
        from scripts.public_source_release import (
            PublicSourceReleaseError,
            build_source_release_manifest,
        )

        asset_path, _, _, _ = self.prepare_visual_source_release_fixture()
        build_source_release_manifest(self.root, "HEAD")
        asset_path.write_bytes(asset_path.read_bytes() + b"committed mutation")
        self.commit_all("mutate visual bytes without manifest update")

        with self.assertRaisesRegex(
            PublicSourceReleaseError,
            "source snapshot fails visual asset validation",
        ):
            build_source_release_manifest(self.root, "HEAD")

    def test_source_release_rejects_committed_stale_visual_digest(self):
        from scripts.public_source_release import (
            PublicSourceReleaseError,
            build_source_release_manifest,
        )

        _, manifest_path, manifest, _ = self.prepare_visual_source_release_fixture()
        build_source_release_manifest(self.root, "HEAD")
        manifest["assets"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.commit_all("commit stale visual digest")

        with self.assertRaisesRegex(
            PublicSourceReleaseError,
            "visual asset SHA-256 mismatch",
        ):
            build_source_release_manifest(self.root, "HEAD")

    def test_source_release_binds_repository_prompt_digest_not_attachment_digest(self):
        from scripts.public_source_release import (
            PublicSourceReleaseError,
            build_source_release_manifest,
        )

        _, _, manifest, prompt_path = self.prepare_visual_source_release_fixture(
            include_prompt_provenance=True
        )
        self.assertIsNotNone(prompt_path)
        self.assertNotEqual(
            manifest["source_prompt_attachment_sha256"],
            manifest["source_prompt_repository_sha256"],
        )
        build_source_release_manifest(self.root, "HEAD")
        assert prompt_path is not None
        prompt_path.write_bytes(prompt_path.read_bytes() + b"\ncommitted prompt change\n")
        self.commit_all("mutate prompt without provenance update")

        with self.assertRaisesRegex(
            PublicSourceReleaseError,
            "visual source prompt SHA-256 mismatch",
        ):
            build_source_release_manifest(self.root, "HEAD")

    def test_missing_and_wrong_type_policy_keys_are_rejected(self):
        cases = {
            "missing": {key: value for key, value in self.policy.items() if key != "forbidden_text_patterns"},
            "wrong type": {**self.policy, "required_root_files": "README.md"},
        }
        for label, policy in cases.items():
            with self.subTest(label=label):
                self.write_policy(self.root, policy)
                errors = verify_repository(self.root)
                self.assertTrue(any("invalid publication policy" in error for error in errors))

    def test_invalid_policy_regex_is_rejected(self):
        policy = copy.deepcopy(self.policy)
        policy["forbidden_text_patterns"][0]["pattern"] = "["
        self.write_policy(self.root, policy)

        errors = verify_repository(self.root)

        self.assertTrue(any("invalid publication policy" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
