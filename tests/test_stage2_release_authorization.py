import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.authorize_stage2_release import (
    ReleaseAuthorizationError,
    _verify_openpgp_signature,
    verify_release_authorization,
)
from scripts.public_source_release import build_source_release_manifest


TEST_SIGNER = "A" * 40
OTHER_SIGNER = "B" * 40


class Stage2ReleaseAuthorizationTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is required for release authorization tests")
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name) / "repository"
        self.root.mkdir()
        self.manifest_path = Path(temporary_directory.name) / "release-manifest.json"
        self._git("init", "--quiet")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Release Test")
        (self.root / "policy").mkdir()
        (self.root / "README.md").write_text(
            "creator-evaluated synthetic local MVP\n", encoding="utf-8"
        )
        (self.root / "evidence.json").write_text(
            '{"evidence":"synthetic-observed"}\n', encoding="utf-8"
        )
        (self.root / "keys").mkdir()
        (self.root / "keys" / "release-signing-public-key.asc").write_text(
            "test-only public signing key fixture\n", encoding="utf-8"
        )
        (self.root / "journey").mkdir()
        (self.root / "journey" / ".keep").write_text("public journey fixture\n", encoding="utf-8")
        decision_root = self.root / "data" / "stage2" / "decision-pack"
        decision_root.mkdir(parents=True)
        (decision_root / "summary.json").write_text(
            '{"human_evidence":"not_observed","maturity_ceiling":"local-mvp"}\n',
            encoding="utf-8",
        )
        (decision_root / "decision-output.json").write_text(
            '{"authorises_company_pilot":false,"maturity_ceiling":"local-mvp",'
            '"next_action":{"authorises_company_pilot":false}}\n',
            encoding="utf-8",
        )
        demo_root = self.root / "demo" / "data"
        demo_root.mkdir(parents=True)
        (demo_root / "evidence-pack.json").write_text(
            json.dumps(
                {
                    "public_safe": True,
                    "read_only": True,
                    "maturity": {
                        "supported_ceiling": "local-mvp",
                        "publication_status": "not_authorised_until_valid_signed_release_tag",
                    },
                    "evidence_boundary": {
                        "synthetic": True,
                        "human_evidence": "not_observed",
                        "independent_validation": False,
                        "live_customer_outcome": "not_observed",
                        "realised_value": "not_observed",
                        "simulated_actions": True,
                        "simulated_approvals": True,
                        "unsent_communications": True,
                    },
                    "cases": [
                        {
                            "synthetic": True,
                            "human_reviewed": False,
                            "no_realised_value": True,
                            "validation_label": "non-independent",
                            "communication_label": "unsent",
                            "action_label": "simulated",
                            "approval_label": "simulated",
                        }
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.policy = self._policy()
        self._write_policy()
        self._commit_all("reviewed source")
        self.source_commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.source_tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        self.manifest = build_source_release_manifest(self.root, "HEAD")
        self._write_manifest(self.manifest)
        committed_manifest = self.root / "policy" / "public-source-release.json"
        committed_manifest.write_bytes(self.manifest_path.read_bytes())
        self._commit_all("bind public release manifest")
        self.trusted_policy_ref = "refs/heads/main"
        self._git("branch", "-M", "main")
        self.trusted_policy_commit = self._git("rev-parse", "HEAD").stdout.strip()

    def _policy(self) -> dict:
        return {
            "allowed_evidence_statuses": ["synthetic-observed"],
            "allowed_knowledge_types": ["decision"],
            "allowed_maturities": ["foundation", "local-mvp"],
            "current_maturity": "local-mvp",
            "allowed_review_states": ["accepted-public-source"],
            "allowed_sensitivities": ["public"],
            "allowed_visual_evidence_boundaries": ["explanatory-only"],
            "required_journey_fields": [
                "evidence_status",
                "public_safe",
                "maturity",
                "limitations",
            ],
            "canonical_source_globs": ["docs/plans/*.md"],
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
                "evidence.json",
                "keys/release-signing-public-key.asc",
            ],
            "required_source_release_paths": [
                "policy/publication-policy.json",
                "evidence.json",
                "keys/release-signing-public-key.asc",
            ],
            "evidence_artifact_globs": ["docs/*.md"],
            "forbidden_file_names": [".env"],
            "forbidden_file_suffixes": [".pem"],
            "forbidden_path_globs": ["artifacts/private/**"],
            "forbidden_text_patterns": [
                {"name": "private path", "pattern": r"(?i)[A-Z]:\\\\Users\\\\"}
            ],
            "allowed_binary_files": [],
            "required_root_files": ["README.md"],
            "release_authorization": {
                "schema_version": "stage2-release-authorization-policy/v1",
                "release_id": "stage2-local-mvp-v1",
                "required_tag_prefix": "stage2-local-mvp-v",
                "manifest_path": "policy/public-source-release.json",
                "manifest_self_excluded": True,
                "required_maturity": "local-mvp",
                "allowed_signer_fingerprints": [TEST_SIGNER],
                "public_key_path": "keys/release-signing-public-key.asc",
                "authorization_status": "awaiting-owner-signed-tag",
            },
            "claim_boundaries": {
                "evidence_class": "creator-evaluated-synthetic",
                "supported_maturity": "local-mvp",
                "maximum_without_independent_human_evidence": "local-mvp",
                "human_evidence": "not-observed",
                "realised_value": "not-observed",
                "pilot_or_production_authority": False,
                "publication_requires_owner_signed_tag": True,
            },
        }

    def _git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )

    def _write_policy(self) -> None:
        (self.root / "policy" / "publication-policy.json").write_text(
            json.dumps(self.policy, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_manifest(self, payload: dict) -> None:
        self.manifest_path.write_bytes(
            (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )

    def _commit_all(self, message: str) -> None:
        self._git("add", ".")
        self._git("commit", "--quiet", "-m", message)

    def _tag_message(self, **overrides: str) -> str:
        fields = {
            "release_id": "stage2-local-mvp-v1",
            "source_commit": self.source_commit,
            "source_tree": self.source_tree,
            "bundle_manifest_sha256": hashlib.sha256(
                self.manifest_path.read_bytes()
            ).hexdigest(),
            "maturity": "local-mvp",
            "trusted_policy_commit": self.trusted_policy_commit,
        }
        fields.update(overrides)
        return "\n".join(
            [
                "stage2-release-authorization/v1",
                *(f"{key}: {value}" for key, value in fields.items()),
            ]
        )

    def _create_annotated_tag(self, tag: str = "stage2-local-mvp-v1", **overrides: str) -> str:
        self._git(
            "tag",
            "-a",
            tag,
            self.trusted_policy_commit,
            "-m",
            self._tag_message(**overrides),
        )
        return tag

    @staticmethod
    def _valid_signature(_root: Path, _tag_ref: str) -> str:
        return TEST_SIGNER

    def _verify(self, tag: str, **overrides):
        signature_verifier = overrides.pop(
            "signature_verifier", self._valid_signature
        )
        return verify_release_authorization(
            self.root,
            tag,
            self.manifest_path,
            signature_verifier=signature_verifier,
            trusted_policy_ref=self.trusted_policy_ref,
            **overrides,
        )

    def test_clean_reviewed_source_annotated_tag_and_pinned_identity_pass(self):
        tag = self._create_annotated_tag()

        receipt = self._verify(tag)

        self.assertEqual("stage2-release-authorization/v1", receipt["schema_version"])
        self.assertEqual(self.source_commit, receipt["source_commit"])
        self.assertEqual(self.source_tree, receipt["source_tree"])
        self.assertEqual(TEST_SIGNER, receipt["signer_fingerprint"])
        self.assertEqual(self.trusted_policy_commit, receipt["trusted_policy_commit"])
        self.assertEqual(self.trusted_policy_commit, receipt["release_commit"])
        self.assertTrue(receipt["clean_checkout_attested"])
        self.assertFalse(receipt["publication_performed"])

    def test_trusted_manifest_commit_rejects_unrelated_file_changes(self):
        (self.root / "unexpected.txt").write_text("not in release closure\n", encoding="utf-8")
        self._commit_all("add unrelated manifest-commit content")
        self.trusted_policy_commit = self._git("rev-parse", "HEAD").stdout.strip()
        tag = self._create_annotated_tag("stage2-local-mvp-v-unrelated")

        with self.assertRaisesRegex(ReleaseAuthorizationError, "manifest commit"):
            self._verify(tag)

    def test_trusted_manifest_commit_cannot_rewrite_signer_policy(self):
        self.policy["release_authorization"]["allowed_signer_fingerprints"] = [
            OTHER_SIGNER
        ]
        self._write_policy()
        self._commit_all("rewrite signer policy in manifest commit")
        self.trusted_policy_commit = self._git("rev-parse", "HEAD").stdout.strip()
        tag = self._create_annotated_tag("stage2-local-mvp-v-policy-rewrite")

        with self.assertRaisesRegex(ReleaseAuthorizationError, "manifest commit"):
            self._verify(tag)

    def test_lightweight_and_unsigned_tags_are_rejected(self):
        self._git("tag", "stage2-local-mvp-v-lightweight")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "annotated tag"):
            verify_release_authorization(
                self.root,
                "stage2-local-mvp-v-lightweight",
                self.manifest_path,
                signature_verifier=self._valid_signature,
                trusted_policy_ref=self.trusted_policy_ref,
            )

        tag = self._create_annotated_tag("stage2-local-mvp-v-unsigned")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "signature verification failed"):
            verify_release_authorization(
                self.root,
                tag,
                self.manifest_path,
                trusted_policy_ref=self.trusted_policy_ref,
            )

    def test_wrong_or_unpinned_signer_is_rejected(self):
        tag = self._create_annotated_tag()

        with self.assertRaisesRegex(ReleaseAuthorizationError, "not pinned"):
            self._verify(
                tag,
                signature_verifier=lambda _root, _tag_ref: OTHER_SIGNER,
            )

        self.policy["release_authorization"]["allowed_signer_fingerprints"] = []
        self.policy["release_authorization"]["public_key_path"] = None
        self._write_policy()
        self._commit_all("remove signing identity")
        self.source_commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.source_tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        blocked_manifest = build_source_release_manifest(self.root, "HEAD")
        self._write_manifest(blocked_manifest)
        (self.root / "policy" / "public-source-release.json").write_bytes(
            self.manifest_path.read_bytes()
        )
        self._commit_all("bind blocked release manifest")
        self.trusted_policy_commit = self._git("rev-parse", "HEAD").stdout.strip()
        blocked_tag = self._create_annotated_tag(
            "stage2-local-mvp-v-blocked",
            bundle_manifest_sha256=hashlib.sha256(
                self.manifest_path.read_bytes()
            ).hexdigest(),
        )
        with self.assertRaisesRegex(ReleaseAuthorizationError, "no allowed signer"):
            self._verify(blocked_tag)

    def test_release_identity_source_tree_bundle_and_maturity_are_bound(self):
        cases = {
            "release_id": {"release_id": "replayed-release"},
            "source_commit": {"source_commit": "0" * 40},
            "source_tree": {"source_tree": "0" * 40},
            "bundle": {"bundle_manifest_sha256": "0" * 64},
            "maturity": {"maturity": "foundation"},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                tag = self._create_annotated_tag(
                    f"stage2-local-mvp-v-{label}", **override
                )
                with self.assertRaises(ReleaseAuthorizationError):
                    self._verify(tag)

    def test_dirty_tracked_and_untracked_worktrees_are_rejected(self):
        tag = self._create_annotated_tag()
        (self.root / "README.md").write_text("post-review mutation\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "worktree is not clean"):
            self._verify(tag)

        self._git("restore", "README.md")
        (self.root / "untracked.txt").write_text("new source\n", encoding="utf-8")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "worktree is not clean"):
            self._verify(tag)

    def test_manifest_self_inclusion_and_byte_mutation_are_rejected(self):
        tag = self._create_annotated_tag()
        included = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        included["artifacts"].append(
            {
                "path": "policy/public-source-release.json",
                "sha256": "0" * 64,
            }
        )
        self._write_manifest(included)
        with self.assertRaisesRegex(ReleaseAuthorizationError, "must exclude itself"):
            self._verify(tag)

        self._write_manifest(self.manifest)
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b" ")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "canonical UTF-8 JSON"):
            self._verify(tag)

    def test_tag_name_annotation_fields_and_head_identity_fail_closed(self):
        tag = self._create_annotated_tag("other-v1")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "required prefix"):
            self._verify(tag)

        self._git("checkout", "--detach", "HEAD~0")
        (self.root / "unrelated.txt").write_text("later\n", encoding="utf-8")
        self._commit_all("later source")
        with self.assertRaisesRegex(ReleaseAuthorizationError, "HEAD does not match"):
            self._verify(tag)

    def test_signed_tag_object_cannot_be_replayed_under_an_alias(self):
        tag = self._create_annotated_tag()
        tag_object = self._git("rev-parse", f"refs/tags/{tag}").stdout.strip()
        alias = "stage2-local-mvp-v-replayed-alias"
        self._git("update-ref", f"refs/tags/{alias}", tag_object)

        with self.assertRaisesRegex(ReleaseAuthorizationError, "replayed tag object"):
            self._verify(alias)

    def test_tagged_commit_cannot_supply_its_own_signer_policy(self):
        trusted_commit = self.trusted_policy_commit
        self._git("checkout", "-b", "attacker")
        self.policy["release_authorization"]["allowed_signer_fingerprints"] = [
            OTHER_SIGNER
        ]
        self._write_policy()
        self._commit_all("attacker rewrites signer trust")
        attacker_commit = self._git("rev-parse", "HEAD").stdout.strip()
        attacker_tree = self._git("rev-parse", "HEAD^{tree}").stdout.strip()
        attacker_manifest = build_source_release_manifest(self.root, "HEAD")
        self._write_manifest(attacker_manifest)
        self.source_commit = attacker_commit
        self.source_tree = attacker_tree
        tag = self._create_annotated_tag("stage2-local-mvp-v-attacker")

        with self.assertRaisesRegex(ReleaseAuthorizationError, "not contained"):
            verify_release_authorization(
                self.root,
                tag,
                self.manifest_path,
                signature_verifier=lambda _root, _tag: OTHER_SIGNER,
                trusted_policy_ref=self.trusted_policy_ref,
            )
        self.assertEqual(
            trusted_commit,
            self._git("rev-parse", self.trusted_policy_ref).stdout.strip(),
        )

    def test_openpgp_status_uses_primary_identity_for_signing_subkey(self):
        signing = "C" * 40
        primary = TEST_SIGNER
        status = (
            "[GNUPG:] VALIDSIG "
            f"{signing} 2026-08-12 0 0 4 0 1 10 00 {primary}\n"
        ).encode("ascii")

        with patch(
            "scripts.authorize_stage2_release._git",
            return_value=subprocess.CompletedProcess([], 0, b"", status),
        ):
            self.assertEqual(primary, _verify_openpgp_signature(self.root, "refs/tags/x"))

        direct_status = (
            "[GNUPG:] VALIDSIG "
            f"{primary} 2026-08-12 0 0 4 0 1 10 00\n"
        ).encode("ascii")
        with patch(
            "scripts.authorize_stage2_release._git",
            return_value=subprocess.CompletedProcess([], 0, b"", direct_status),
        ):
            self.assertEqual(primary, _verify_openpgp_signature(self.root, "refs/tags/x"))


if __name__ == "__main__":
    unittest.main()
