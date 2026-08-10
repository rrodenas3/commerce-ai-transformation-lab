from __future__ import annotations

import csv
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.score_stage1_manual import score_manual_records
from scripts.score_stage1_heldout_manual import (
    _validate_release_manifest,
    score_heldout_run,
)
from scripts.stage1_case_system import build_oracle, load_stage1_policy, read_jsonl
from scripts.stage1_heldout import (
    HELDOUT_CASE_COUNT,
    HELDOUT_DATASET_ROLE,
    HELDOUT_EVALUATION_PACK_ID,
    HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
    HELDOUT_ORACLE_RELEASE_PATH,
    HELDOUT_ORACLE_EXPOSURE_PREPARED,
    HELDOUT_PRIVATE_PATH,
    HELDOUT_PUBLIC_PATH,
    OPERATOR_HIDDEN_FIELDS,
    build_heldout_cases,
    generate_heldout_artifacts,
    prepare_heldout_run,
    validate_heldout_run_bindings,
)
from scripts.stage1_heldout_release import (
    release_heldout_oracle,
    validate_completed_records_for_release,
)
from scripts.stage1_scoring import MANUAL_TEMPLATE_FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXED_MATERIAL = "heldout-test-material-2026-08-10-aaaaaaaaaaaaaaaaaaaaaaaa"


class Stage1HeldoutTests(unittest.TestCase):
    def _project(self, directory: str) -> Path:
        root = Path(directory)
        policy_target = root / "data" / "stage1" / "policy.json"
        protocol_target = root / "docs" / "STAGE1_HELDOUT_EVALUATION_PROTOCOL.md"
        policy_target.parent.mkdir(parents=True, exist_ok=True)
        protocol_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROJECT_ROOT / "data" / "stage1" / "policy.json", policy_target)
        shutil.copyfile(
            PROJECT_ROOT / "docs" / "STAGE1_HELDOUT_EVALUATION_PROTOCOL.md",
            protocol_target,
        )
        (root / ".gitignore").write_text("artifacts/private/\n", encoding="utf-8")
        return root

    def _generate(self, root: Path, material: str = FIXED_MATERIAL):
        public = root / HELDOUT_PUBLIC_PATH
        private = root / HELDOUT_PRIVATE_PATH
        manifest = generate_heldout_artifacts(
            root,
            public,
            private,
            generation_material=material,
        )
        return manifest, public, private

    def _prepare(self, root: Path, public: Path) -> Path:
        output = (
            root / "data" / "stage1" / "heldout" / "runs" / "scc-01-heldout-creator-001"
        )
        prepare_heldout_run(
            root,
            public,
            output,
            run_id="scc-01-heldout-creator-001",
            reviewer_code="CREATOR-01",
            operator_role="creator",
            prepared_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
        )
        return output

    def _complete_records(self, run: Path, private: Path) -> bytes:
        cases = read_jsonl(run / "case-pack.jsonl")
        oracles = {
            oracle["case_id"]: oracle for oracle in read_jsonl(private / "oracle.jsonl")
        }
        start = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
        with (run / "manual-records.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=MANUAL_TEMPLATE_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for index, case in enumerate(cases):
                oracle = oracles[case["case_id"]]
                case_start = start + timedelta(seconds=index * 30)
                writer.writerow(
                    {
                        "case_id": case["case_id"],
                        "reviewer_code": "CREATOR-01",
                        "run_type": "manual-no-ai",
                        "started_at_utc": case_start.isoformat().replace("+00:00", "Z"),
                        "ended_at_utc": (case_start + timedelta(seconds=20))
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "active_handling_seconds": "20",
                        "recommended_action": oracle["preferred_action"],
                        "route": oracle["required_route"],
                        "evidence_used_pipe_delimited": "|".join(
                            oracle["required_evidence"]
                        ),
                        "message_facts_pipe_delimited": "case_received",
                        "confidence_1_to_5": "4",
                        "help_requested": "false",
                        "handoff_count": "0",
                        "policy_lookup_count": "1",
                        "notes_without_personal_data": "",
                    }
                )
        return (run / "manual-records.csv").read_bytes()

    @staticmethod
    def _git(root: Path, *arguments: str, when: str | None = None) -> str:
        environment = os.environ.copy()
        if when:
            environment["GIT_AUTHOR_DATE"] = when
            environment["GIT_COMMITTER_DATE"] = when
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def test_generation_is_deterministic_balanced_and_keeps_oracle_private(self):
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            first_root = self._project(first)
            second_root = self._project(second)
            first_manifest, first_public, first_private = self._generate(first_root)
            second_manifest, second_public, second_private = self._generate(second_root)

            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(
                (first_public / "cases.jsonl").read_bytes(),
                (second_public / "cases.jsonl").read_bytes(),
            )
            self.assertEqual(
                (first_private / "oracle.jsonl").read_bytes(),
                (second_private / "oracle.jsonl").read_bytes(),
            )
            self.assertFalse((first_public / "oracle.released.jsonl").exists())
            self.assertEqual(HELDOUT_CASE_COUNT, first_manifest["case_count"])
            self.assertEqual(
                {family: 4 for family in first_manifest["case_families"]},
                first_manifest["case_families"],
            )
            self.assertEqual(
                "withheld-until-record-freeze", first_manifest["generator_seed_status"]
            )
            self.assertEqual(
                "answer-file-not-published", first_manifest["oracle_release_status"]
            )
            self.assertEqual(
                hashlib.sha256(
                    (first_private / "oracle.jsonl").read_bytes()
                ).hexdigest(),
                first_manifest["artifacts_sha256"]["oracle.released.jsonl"],
            )
            cases = read_jsonl(first_public / "cases.jsonl")
            self.assertTrue(
                all(OPERATOR_HIDDEN_FIELDS.isdisjoint(case) for case in cases)
            )
            policy = load_stage1_policy(first_root)
            with self.assertRaisesRegex(ValueError, "withheld release material"):
                build_oracle(cases[0], policy)
            internal_cases = build_heldout_cases(policy, FIXED_MATERIAL)
            self.assertEqual(
                internal_cases[0]["case_id"],
                build_oracle(
                    internal_cases[0],
                    policy,
                    heldout_release_material=FIXED_MATERIAL,
                )["case_id"],
            )

    def test_generation_material_changes_pack_and_commitment(self):
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            first_root = self._project(first)
            second_root = self._project(second)
            first_manifest, first_public, _ = self._generate(first_root)
            second_manifest, second_public, _ = self._generate(
                second_root,
                "heldout-test-material-2026-08-10-bbbbbbbbbbbbbbbbbbbbbbbb",
            )
            self.assertNotEqual(
                first_manifest["generator_seed_commitment_sha256"],
                second_manifest["generator_seed_commitment_sha256"],
            )
            self.assertNotEqual(
                (first_public / "cases.jsonl").read_bytes(),
                (second_public / "cases.jsonl").read_bytes(),
            )

    def test_committed_pack_and_blank_run_match_every_public_commitment(self):
        public = PROJECT_ROOT / HELDOUT_PUBLIC_PATH
        run = (
            PROJECT_ROOT
            / "data"
            / "stage1"
            / "heldout"
            / "runs"
            / "scc-01-heldout-creator-001"
        )
        manifest_bytes = (public / "manifest.json").read_bytes()
        cases_bytes = (public / "cases.jsonl").read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        metadata = json.loads((run / "run-manifest.json").read_text(encoding="utf-8"))
        cases = read_jsonl(public / "cases.jsonl")

        self.assertEqual(HELDOUT_CASE_COUNT, len(cases))
        self.assertEqual(
            hashlib.sha256(cases_bytes).hexdigest(),
            manifest["artifacts_sha256"]["cases.jsonl"],
        )
        self.assertEqual(
            manifest["artifacts_sha256"]["cases.jsonl"],
            metadata["artifacts"]["cases"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(manifest_bytes).hexdigest(),
            metadata["artifacts"]["artifact_manifest"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256((run / "case-pack.jsonl").read_bytes()).hexdigest(),
            metadata["run_files"]["case_pack"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256((run / "policy.json").read_bytes()).hexdigest(),
            metadata["run_files"]["policy_copy"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256((run / "manual-records.csv").read_bytes()).hexdigest(),
            metadata["run_files"]["records_template"]["sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                (
                    PROJECT_ROOT / "docs" / "STAGE1_HELDOUT_EVALUATION_PROTOCOL.md"
                ).read_bytes()
            ).hexdigest(),
            metadata["instructions"]["sha256"],
        )
        self.assertEqual(
            [case["case_id"] for case in cases], metadata["assigned_case_ids"]
        )
        self.assertTrue(all(OPERATOR_HIDDEN_FIELDS.isdisjoint(case) for case in cases))

    def test_committed_pack_contains_no_released_or_completed_answer_artifact(self):
        public = PROJECT_ROOT / HELDOUT_PUBLIC_PATH
        run = (
            PROJECT_ROOT
            / "data"
            / "stage1"
            / "heldout"
            / "runs"
            / "scc-01-heldout-creator-001"
        )
        self.assertFalse((PROJECT_ROOT / HELDOUT_ORACLE_RELEASE_PATH).exists())
        self.assertFalse((PROJECT_ROOT / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH).exists())
        self.assertFalse((run / "manual-summary.json").exists())
        self.assertEqual(
            {"cases.jsonl", "manifest.json"}, {path.name for path in public.iterdir()}
        )
        with (run / "manual-records.csv").open(encoding="utf-8", newline="") as handle:
            records = list(csv.DictReader(handle))
        self.assertEqual(HELDOUT_CASE_COUNT, len(records))
        self.assertTrue(all(not row["recommended_action"] for row in records))

    def test_preparation_creates_only_case_policy_records_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            manifest, public, _ = self._generate(root)
            run = self._prepare(root, public)
            self.assertEqual(
                {
                    "case-pack.jsonl",
                    "manual-records.csv",
                    "policy.json",
                    "run-manifest.json",
                },
                {path.name for path in run.iterdir()},
            )
            metadata = json.loads(
                (run / "run-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                HELDOUT_ORACLE_EXPOSURE_PREPARED, metadata["oracle_exposure_status"]
            )
            self.assertEqual(
                HELDOUT_EVALUATION_PACK_ID,
                metadata["evaluation_pack"]["evaluation_pack_id"],
            )
            self.assertEqual(HELDOUT_CASE_COUNT, len(metadata["assigned_case_ids"]))
            self.assertEqual(
                manifest["artifacts_sha256"]["oracle.released.jsonl"],
                metadata["oracle"]["sha256_commitment"],
            )
            with (run / "manual-records.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                blank = list(csv.DictReader(handle))
            self.assertEqual(HELDOUT_CASE_COUNT, len(blank))
            self.assertTrue(all(not row["recommended_action"] for row in blank))

    def test_blank_records_cannot_satisfy_release_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, _ = self._generate(root)
            run = self._prepare(root, public)
            metadata = json.loads(
                (run / "run-manifest.json").read_text(encoding="utf-8")
            )
            with self.assertRaisesRegex(ValueError, "missing required manual field"):
                validate_completed_records_for_release(
                    (run / "manual-records.csv").read_bytes(), metadata
                )

    def test_run_binding_rejects_substituted_artifact_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, _ = self._generate(root)
            run = self._prepare(root, public)
            metadata = json.loads(
                (run / "run-manifest.json").read_text(encoding="utf-8")
            )
            public_manifest_bytes = (public / "manifest.json").read_bytes()
            public_manifest = json.loads(public_manifest_bytes.decode("utf-8"))
            metadata["artifacts"]["cases"]["path"] = "substituted/cases.jsonl"
            with self.assertRaisesRegex(ValueError, "artifact bindings are stale"):
                validate_heldout_run_bindings(
                    metadata,
                    public_manifest,
                    public_manifest_bytes=public_manifest_bytes,
                    public_cases_bytes=(public / "cases.jsonl").read_bytes(),
                    prepared_case_pack_bytes=(run / "case-pack.jsonl").read_bytes(),
                    policy_bytes=(
                        root / "data" / "stage1" / "policy.json"
                    ).read_bytes(),
                    prepared_policy_bytes=(run / "policy.json").read_bytes(),
                    instructions_bytes=(
                        root / "docs" / "STAGE1_HELDOUT_EVALUATION_PROTOCOL.md"
                    ).read_bytes(),
                )

    def test_release_requires_git_frozen_records_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, private = self._generate(root)
            run = self._prepare(root, public)
            self._git(root, "init")
            self._git(root, "config", "user.email", "lab@example.invalid")
            self._git(root, "config", "user.name", "Synthetic Lab")
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze blank held-out run",
                when="2026-08-10T09:10:00+00:00",
            )
            preparation_sha = self._git(root, "rev-parse", "HEAD")

            records_snapshot = self._complete_records(run, private)
            self._git(root, "add", str((run / "manual-records.csv").relative_to(root)))
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze completed held-out record",
                when="2026-08-10T11:00:00+00:00",
            )
            records_sha = self._git(root, "rev-parse", "HEAD")
            release = release_heldout_oracle(
                root,
                run / "run-manifest.json",
                private,
                preparation_ref=preparation_sha,
                records_ref=records_sha,
                released_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(
                "oracle-file-released-after-record-freeze",
                release["state_transition"]["to"],
            )
            self.assertEqual(
                hashlib.sha256(records_snapshot).hexdigest(),
                release["records_freeze"]["records_sha256"],
            )
            self.assertTrue((root / HELDOUT_ORACLE_RELEASE_PATH).is_file())
            self.assertTrue((root / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH).is_file())

            metadata = json.loads(
                (run / "run-manifest.json").read_text(encoding="utf-8")
            )
            public_manifest = json.loads(
                (public / "manifest.json").read_text(encoding="utf-8")
            )
            tampered_release = copy.deepcopy(release)
            tampered_release["state_transition"]["released_at_utc"] = (
                "2026-08-10T10:30:00Z"
            )
            with self.assertRaisesRegex(ValueError, "must follow the records commit"):
                _validate_release_manifest(
                    tampered_release,
                    metadata,
                    public_manifest,
                    (root / HELDOUT_ORACLE_RELEASE_PATH).read_bytes(),
                    records_snapshot,
                )
            with (run / "manual-records.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                completed_records = list(csv.DictReader(handle))
            summary = score_manual_records(
                read_jsonl(run / "case-pack.jsonl"),
                read_jsonl(root / HELDOUT_ORACLE_RELEASE_PATH),
                completed_records,
                run_metadata=metadata,
            )
            self.assertEqual(HELDOUT_DATASET_ROLE, summary["dataset_role"])
            self.assertEqual(HELDOUT_CASE_COUNT, summary["case_count"])
            self.assertEqual(
                HELDOUT_CASE_COUNT, summary["successful_or_safe_escalation_count"]
            )
            summary_path = run / "manual-summary.json"
            end_to_end_summary = score_heldout_run(
                root,
                input_path=run / "manual-records.csv",
                output_path=summary_path,
                cases_path=run / "case-pack.jsonl",
                oracle_path=root / HELDOUT_ORACLE_RELEASE_PATH,
                run_manifest_path=run / "run-manifest.json",
                release_manifest_path=root / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH,
            )
            self.assertEqual(HELDOUT_CASE_COUNT, end_to_end_summary["case_count"])
            self.assertTrue(summary_path.is_file())
            (run / "manual-records.csv").write_bytes(records_snapshot + b"\n")
            with self.assertRaisesRegex(
                ValueError, "current records do not match the pre-release freeze"
            ):
                score_heldout_run(
                    root,
                    input_path=run / "manual-records.csv",
                    output_path=run / "tampered-summary.json",
                    cases_path=run / "case-pack.jsonl",
                    oracle_path=root / HELDOUT_ORACLE_RELEASE_PATH,
                    run_manifest_path=run / "run-manifest.json",
                    release_manifest_path=(root / HELDOUT_ORACLE_RELEASE_MANIFEST_PATH),
                )

    def test_release_rejects_historical_case_pack_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, private = self._generate(root)
            run = self._prepare(root, public)
            self._git(root, "init")
            self._git(root, "config", "user.email", "lab@example.invalid")
            self._git(root, "config", "user.name", "Synthetic Lab")
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze blank held-out run",
                when="2026-08-10T09:10:00+00:00",
            )
            preparation_sha = self._git(root, "rev-parse", "HEAD")
            original_case_pack = (run / "case-pack.jsonl").read_bytes()
            self._complete_records(run, private)
            (run / "case-pack.jsonl").write_bytes(original_case_pack + b"\n")
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze substituted held-out record",
                when="2026-08-10T11:00:00+00:00",
            )
            records_sha = self._git(root, "rev-parse", "HEAD")
            (run / "case-pack.jsonl").write_bytes(original_case_pack)
            with self.assertRaisesRegex(ValueError, "frozen .*case-pack.jsonl bytes"):
                release_heldout_oracle(
                    root,
                    run / "run-manifest.json",
                    private,
                    preparation_ref=preparation_sha,
                    records_ref=records_sha,
                    released_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                )

    def test_release_rejects_answer_artifact_in_records_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, private = self._generate(root)
            run = self._prepare(root, public)
            self._git(root, "init")
            self._git(root, "config", "user.email", "lab@example.invalid")
            self._git(root, "config", "user.name", "Synthetic Lab")
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze blank held-out run",
                when="2026-08-10T09:10:00+00:00",
            )
            preparation_sha = self._git(root, "rev-parse", "HEAD")
            self._complete_records(run, private)
            leaked_oracle = root / HELDOUT_ORACLE_RELEASE_PATH
            leaked_oracle.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(private / "oracle.jsonl", leaked_oracle)
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: invalid record with answer artifact",
                when="2026-08-10T11:00:00+00:00",
            )
            records_sha = self._git(root, "rev-parse", "HEAD")
            leaked_oracle.unlink()
            with self.assertRaisesRegex(
                ValueError, "must be absent before oracle release"
            ):
                release_heldout_oracle(
                    root,
                    run / "run-manifest.json",
                    private,
                    preparation_ref=preparation_sha,
                    records_ref=records_sha,
                    released_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                )

    def test_release_rejects_transient_oracle_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, private = self._generate(root)
            run = self._prepare(root, public)
            self._git(root, "init")
            self._git(root, "config", "user.email", "lab@example.invalid")
            self._git(root, "config", "user.name", "Synthetic Lab")
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze blank held-out run",
                when="2026-08-10T09:10:00+00:00",
            )
            preparation_sha = self._git(root, "rev-parse", "HEAD")

            transient_oracle = root / HELDOUT_ORACLE_RELEASE_PATH
            transient_oracle.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(private / "oracle.jsonl", transient_oracle)
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: invalid transient oracle",
                when="2026-08-10T09:20:00+00:00",
            )
            transient_oracle.unlink()
            self._complete_records(run, private)
            self._git(root, "add", ".")
            self._git(
                root,
                "commit",
                "-m",
                "evidence: freeze completed held-out record",
                when="2026-08-10T11:00:00+00:00",
            )
            records_sha = self._git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "immediate child"):
                release_heldout_oracle(
                    root,
                    run / "run-manifest.json",
                    private,
                    preparation_ref=preparation_sha,
                    records_ref=records_sha,
                    released_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
                )

    def test_public_pack_cannot_be_prepared_after_oracle_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._project(directory)
            _, public, private = self._generate(root)
            (root / HELDOUT_ORACLE_RELEASE_PATH).parent.mkdir(
                parents=True, exist_ok=True
            )
            shutil.copyfile(
                private / "oracle.jsonl", root / HELDOUT_ORACLE_RELEASE_PATH
            )
            output = root / "data" / "stage1" / "heldout" / "runs" / "blocked-run"
            with self.assertRaisesRegex(ValueError, "already released"):
                prepare_heldout_run(
                    root,
                    public,
                    output,
                    run_id="blocked-run",
                    reviewer_code="CREATOR-01",
                    operator_role="creator",
                    prepared_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
