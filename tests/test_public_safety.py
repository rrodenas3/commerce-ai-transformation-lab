import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
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
