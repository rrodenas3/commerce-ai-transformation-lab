from __future__ import annotations

import json
import hashlib
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import scripts.stage2_evaluation_release as release_module
import scripts.run_stage2_isolated as isolated_module
from scripts.generate_stage2_evaluation import resolve_clean_git_binding, write_evaluation_pack
from scripts.run_stage2_isolated import (
    BASE_IMAGE,
    RUNTIME_MODULES,
    build_docker_command,
    materialize_runtime_build_context,
    runtime_build_context_inventory,
    run_inner_evaluation,
    validate_docker_inspect,
    verify_image_info,
)
from scripts.stage2_evaluation_release import (
    EvaluationRelease,
    ReleaseIntegrityError,
    main as release_main,
    validate_outer_attestation,
)
from scripts.stage2_contracts import canonical_json_bytes, canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
FAKE_COMMIT = "1" * 40
FAKE_TREE = "2" * 40
FAKE_IMAGE_ID = "sha256:" + "a" * 64
SECCOMP_PROFILE = (ROOT / "containers/stage2-evaluation/seccomp.json").resolve()
INSPECT_LABELS = {
    "stage2.base_image_digest": BASE_IMAGE.split("@", 1)[1],
    "stage2.build_input_sha256": "b" * 64,
    "stage2.seccomp_profile_sha256": hashlib.sha256(SECCOMP_PROFILE.read_bytes()).hexdigest(),
    "stage2.source_commit": FAKE_COMMIT,
    "stage2.source_tree": FAKE_TREE,
}


class Stage2ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_temporary = tempfile.TemporaryDirectory()
        template = Path(cls.template_temporary.name)
        cls.template_pack = template / "pack"
        cls.template_private = template / "private"
        cls.template_output = template / "output"
        write_evaluation_pack(
            ROOT, cls.template_pack, cls.template_private,
            source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"n" * 32,
        )
        run_inner_evaluation(cls.template_pack, cls.template_output)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.template_temporary.cleanup()

    def _release(self, root: Path) -> tuple[EvaluationRelease, Path, Path]:
        pack = root / "pack"
        private = root / "private"
        run = root / "run"
        write_evaluation_pack(ROOT, pack, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"n" * 32)
        return EvaluationRelease(pack, private, run, test_fixture_mode=True), pack, private

    def _canonical_release(self, root: Path) -> tuple[EvaluationRelease, dict[str, object], Path]:
        source = root / "clean-source"
        source.mkdir()
        for relative in (
            "containers/stage2-evaluation/Dockerfile",
            "containers/stage2-evaluation/seccomp.json",
            "data/stage2/development/cases.jsonl",
            *(f"scripts/{name}" for name in RUNTIME_MODULES),
        ):
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email", "stage2@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Stage2 Test"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "core.autocrlf", "false"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "clean source"], check=True)
        binding = resolve_clean_git_binding(source)
        pack = root / "canonical-pack"
        private = root / "canonical-private"
        run = root / "canonical-run"
        write_evaluation_pack(
            ROOT, pack, private,
            source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"c" * 32,
        )
        pins_path = pack / "pins.json"
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
        pins.update(binding)
        pins["runtime_build_input_sha256"] = canonical_sha256(
            runtime_build_context_inventory(source)
        )
        pins_path.chmod(0o644)
        pins_path.write_bytes(canonical_json_bytes(pins))
        manifest_path = pack / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            source_binding_status="verified-clean-git-export",
            source_commit=binding["source_commit"],
            source_tree=binding["source_tree"],
            status="frozen-before-evaluated-run",
        )
        manifest["artifact_sha256"]["pins.json"] = hashlib.sha256(pins_path.read_bytes()).hexdigest()
        manifest_path.chmod(0o644)
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        receipt_material = {
            "base_image_digest": BASE_IMAGE.split("@", 1)[1],
            "build_input_sha256": pins["runtime_build_input_sha256"],
            "image_id": FAKE_IMAGE_ID,
            "schema_version": "stage2-image-build-receipt/v2",
            "seccomp_profile_sha256": pins["seccomp_profile_sha256"],
            "source_commit": binding["source_commit"],
            "source_tree": binding["source_tree"],
        }
        receipt = {**receipt_material, "receipt_sha256": canonical_sha256(receipt_material)}
        return EvaluationRelease(pack, private, run, source_root=source), receipt, source

    def _write_attestation(self, release: EvaluationRelease) -> None:
        preparation = release.preparation()
        seal = release.output_seal()
        attestation = {
            "absolute_path_probe": "denied",
            "canonical_run": True,
            "capabilities": "ALL_DROPPED",
            "completed_output_inventory_sha256": seal["artifact_inventory_sha256"],
            "container_user": "65532:65532",
            "cpu_limit": "0.5",
            "evaluated_environment": ["PYTHONPATH=/app", "TMPDIR=/work"],
            "git_object_probe": "denied",
            "home_mount": "absent",
            "home_environment_probe": "denied",
            "home_path_probe": "denied",
            "base_image_digest": BASE_IMAGE.split("@", 1)[1],
            "build_input_sha256": preparation["expected_build_input_sha256"],
            "image_build_receipt_sha256": preparation["image_build_receipt_sha256"],
            "image_id": preparation["expected_image_id"],
            "image_source_commit": preparation["source_commit"],
            "image_source_tree": preparation["source_tree"],
            "input_manifest_sha256": preparation["input_manifest_sha256"],
            "memory_limit_bytes": 268435456,
            "mounts": [
                {"access": "read-only", "source_identity": preparation["input_root_identity"], "target": "/input"},
            ],
            "network": "none",
            "no_new_privileges": True,
            "oracle_mount": "absent",
            "outer_materialized_output": True,
            "parent_path_probe": "denied",
            "pids_limit": 1,
            "private_mount": "absent",
            "repository_mount": "absent",
            "root_filesystem": "read-only",
            "schema_version": "stage2-isolation-attestation/v2",
            "seccomp_profile_path": str(
                (ROOT / "containers/stage2-evaluation/seccomp.json").resolve()
            ),
            "seccomp_profile_sha256": preparation["expected_seccomp_profile_sha256"],
            "seccomp_profile_identity_verified_through_create": True,
            "seccomp_profile_source": "outer-materialized-from-frozen-committed-bytes",
            "seccomp_denials": ["clone", "clone3", "execveat", "fork", "socket", "socketpair", "vfork"],
            "socket_probe": "denied",
            "subprocess_probe": "denied",
            "workspace_mount": "isolated-tmpfs-rw",
            "wall_time_limit_seconds": 180,
            "writer": "outer-launcher-after-container-exit",
        }
        release.attestation_path.parent.mkdir(parents=True)
        release.attestation_path.write_bytes(canonical_json_bytes(attestation))

    def _populate_output(self, release: EvaluationRelease) -> None:
        shutil.copytree(self.template_output, release.output_root, dirs_exist_ok=True)

    def test_release_state_machine_cannot_skip_or_roll_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            with self.assertRaises(ReleaseIntegrityError):
                release.verify_eligibility()
            with self.assertRaises(ReleaseIntegrityError):
                release.release_oracle()
            self.assertEqual(release.state()["state"], "running")

    def test_unverified_binding_cannot_prepare_without_explicit_noncanonical_test_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); pack = root / "pack"; private = root / "private"; run = root / "run"
            write_evaluation_pack(ROOT, pack, private, source_commit=FAKE_COMMIT, source_tree=FAKE_TREE, nonce=b"n" * 32)
            release = EvaluationRelease(pack, private, run)
            with self.assertRaises(ReleaseIntegrityError):
                release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)

    def test_canonical_prepare_uses_explicit_clean_source_not_dirty_module_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, receipt, source = self._canonical_release(root)
            pins = json.loads((release.pack_root / "pins.json").read_text(encoding="utf-8"))
            module_root = root / "lock-dirtied-module-checkout"
            shutil.copytree(source, module_root, ignore=shutil.ignore_patterns(".git"))
            subprocess.run(["git", "init", "-q", str(module_root)], check=True)
            subprocess.run(["git", "-C", str(module_root), "config", "user.email", "stage2@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(module_root), "config", "user.name", "Stage2 Test"], check=True)
            subprocess.run(["git", "-C", str(module_root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(module_root), "commit", "-qm", "module source"], check=True)
            lock = module_root / "data/stage2/runs/S2-CF-RUN-0002/.release-writer.lock"
            lock.parent.mkdir(parents=True)
            lock.write_bytes(b"stage2-release-writer-lock/v1\n")
            self.assertTrue(
                subprocess.run(
                    ["git", "-C", str(module_root), "status", "--porcelain=v1", "--untracked-files=all"],
                    check=True, capture_output=True, text=True,
                ).stdout
            )
            with mock.patch.object(release_module, "PROJECT_ROOT", module_root):
                prepared = release.prepare(
                    source_commit=pins["source_commit"],
                    source_tree=pins["source_tree"],
                    image_receipt=receipt,
                )
            self.assertEqual(prepared["source_commit"], pins["source_commit"])
            self.assertEqual(release.state()["state"], "running")

    def test_canonical_prepare_requires_explicit_clean_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, receipt, _ = self._canonical_release(Path(temporary))
            pins = json.loads((release.pack_root / "pins.json").read_text(encoding="utf-8"))
            release.source_root = None
            with self.assertRaisesRegex(ReleaseIntegrityError, "explicit clean source checkout"):
                release.prepare(
                    source_commit=pins["source_commit"],
                    source_tree=pins["source_tree"],
                    image_receipt=receipt,
                )
            self.assertEqual(release.state()["state"], "not-started")

    def test_canonical_prepare_rejects_dirty_or_mismatched_explicit_source(self) -> None:
        for defect in ("dirty", "mismatched"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                release, receipt, source = self._canonical_release(Path(temporary))
                pins = json.loads((release.pack_root / "pins.json").read_text(encoding="utf-8"))
                marker = source / "prepare-source-defect.txt"
                marker.write_text(defect + "\n", encoding="utf-8")
                if defect == "mismatched":
                    subprocess.run(["git", "-C", str(source), "config", "user.email", "stage2@example.invalid"], check=True)
                    subprocess.run(["git", "-C", str(source), "config", "user.name", "Stage2 Test"], check=True)
                    subprocess.run(["git", "-C", str(source), "add", marker.name], check=True)
                    subprocess.run(["git", "-C", str(source), "commit", "-qm", "mismatched source"], check=True)
                with self.assertRaises(ReleaseIntegrityError):
                    release.prepare(
                        source_commit=pins["source_commit"],
                        source_tree=pins["source_tree"],
                        image_receipt=receipt,
                    )
                self.assertEqual(release.state()["state"], "not-started")

    def test_superseded_pack_id_and_schema_cannot_enter_current_release(self) -> None:
        for field, value in (
            ("pack_id", "S2-EVALUATION-20260811-V1"),
            ("schema_version", "stage2-confirmatory-pack/v1"),
            ("pack_id", "S2-EVALUATION-20260812-V2"),
            ("schema_version", "stage2-confirmatory-pack/v2"),
            ("pack_id", "S2-EVALUATION-20260812-V3"),
            ("schema_version", "stage2-confirmatory-pack/v3"),
            ("pack_id", "S2-EVALUATION-20260812-V4"),
            ("schema_version", "stage2-confirmatory-pack/v4"),
            ("pack_id", "S2-EVALUATION-20260812-V5"),
            ("schema_version", "stage2-confirmatory-pack/v5"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                release, pack, _ = self._release(Path(temporary))
                manifest_path = pack / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest[field] = value
                manifest_path.chmod(0o644)
                manifest_path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaises(ReleaseIntegrityError):
                    release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
                self.assertEqual(release.state()["state"], "not-started")

    def test_v5_running_state_cannot_advance_under_v6_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            state_path = release.state_dir / "0001-running.json"
            state_path.chmod(0o644)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["pack_id"] = "S2-EVALUATION-20260812-V5"
            state_material = {key: value for key, value in state.items() if key != "record_digest"}
            state["record_digest"] = canonical_sha256(state_material)
            state_path.write_bytes(canonical_json_bytes(state))
            before = {path.name: path.read_bytes() for path in release.state_dir.iterdir()}

            with self.assertRaisesRegex(ReleaseIntegrityError, "current pack identity"):
                release.prepare_freeze()

            self.assertEqual(
                {path.name: path.read_bytes() for path in release.state_dir.iterdir()},
                before,
            )

    def test_superseded_preparation_pins_and_seal_cannot_continue(self) -> None:
        for defect in ("preparation", "pins"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                release, pack, _ = self._release(Path(temporary))
                release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
                target = release.run_root / "preparation.json" if defect == "preparation" else pack / "pins.json"
                target.chmod(0o644)
                value = json.loads(target.read_text(encoding="utf-8"))
                if defect == "preparation":
                    value["pack_id"] = "S2-EVALUATION-20260812-V5"
                else:
                    value["pack_schema"] = "stage2-confirmatory-pack/v5"
                target.write_bytes(canonical_json_bytes(value))
                before = {path.name: path.read_bytes() for path in release.state_dir.iterdir()}
                with self.assertRaises(ReleaseIntegrityError):
                    release.prepare_freeze()
                self.assertEqual(
                    {path.name: path.read_bytes() for path in release.state_dir.iterdir()},
                    before,
                )

        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze()
            release.freeze_outputs()
            seal = release.run_root / "output-seal.json"
            seal.chmod(0o644)
            value = json.loads(seal.read_text(encoding="utf-8"))
            value["pack_id"] = "S2-EVALUATION-20260812-V5"
            seal.write_bytes(canonical_json_bytes(value))
            before = {path.name: path.read_bytes() for path in release.state_dir.iterdir()}
            with self.assertRaises(ReleaseIntegrityError):
                release.verify_eligibility()
            self.assertEqual(
                {path.name: path.read_bytes() for path in release.state_dir.iterdir()},
                before,
            )

    def test_runtime_context_digest_is_identical_across_clean_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "stage2@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Stage2 Test"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "core.autocrlf", "false"], check=True)
            dockerfile = source / "containers/stage2-evaluation/Dockerfile"
            dockerfile.parent.mkdir(parents=True)
            dockerfile.write_bytes(b"FROM scratch\nCOPY scripts /app/scripts\n")
            (dockerfile.parent / "seccomp.json").write_bytes(SECCOMP_PROFILE.read_bytes())
            scripts = source / "scripts"
            scripts.mkdir()
            for name in RUNTIME_MODULES:
                (scripts / name).write_bytes(b"VALUE = 'committed'\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "runtime"], check=True)

            crlf = root / "crlf"
            subprocess.run(
                ["git", "-c", "core.autocrlf=true", "clone", "-q", str(source), str(crlf)],
                check=True,
            )
            self.assertNotEqual(
                (source / "scripts/recovery_actions.py").read_bytes(),
                (crlf / "scripts/recovery_actions.py").read_bytes(),
            )
            self.assertEqual(
                runtime_build_context_inventory(source),
                isolated_module._test_only_runtime_build_context_inventory(crlf),
            )
            self.assertEqual(
                canonical_sha256(runtime_build_context_inventory(source)),
                canonical_sha256(
                    isolated_module._test_only_runtime_build_context_inventory(crlf)
                ),
            )
            context = root / "materialized"
            context.mkdir()
            _, committed_blobs = isolated_module._committed_runtime_blobs(
                crlf, require_clean=False
            )
            self.assertEqual(
                isolated_module._materialize_runtime_blobs(context, committed_blobs),
                runtime_build_context_inventory(source),
            )
            self.assertEqual(
                (context / "scripts/recovery_actions.py").read_bytes(),
                (source / "scripts/recovery_actions.py").read_bytes(),
            )

    def test_runtime_context_inventory_rejects_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "stage2@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "Stage2 Test"], check=True)
            dockerfile = source / "containers/stage2-evaluation/Dockerfile"
            dockerfile.parent.mkdir(parents=True)
            dockerfile.write_bytes(b"FROM scratch\n")
            (dockerfile.parent / "seccomp.json").write_bytes(SECCOMP_PROFILE.read_bytes())
            scripts = source / "scripts"
            scripts.mkdir()
            for name in RUNTIME_MODULES:
                (scripts / name).write_bytes(b"VALUE = 1\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "runtime"], check=True)
            (scripts / "recovery_actions.py").write_bytes(b"VALUE = 2\n")
            with self.assertRaises(RuntimeError):
                runtime_build_context_inventory(source)

    def test_freeze_seals_inventory_and_rejects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, private = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze()
            release.freeze_outputs()
            target = release.output_root / "assisted" / "S2-CASE-5001" / "events" / "workflow.jsonl"
            target.chmod(0o644)
            target.write_bytes(b"{}\n")
            with self.assertRaises(ReleaseIntegrityError):
                release.verify_eligibility()
            self.assertEqual(release.state()["state"], "output-frozen")

    def test_crash_between_seal_and_state_never_partially_qualifies_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze()
            with mock.patch.object(release, "_append_state", side_effect=RuntimeError("injected crash")):
                with self.assertRaises(RuntimeError):
                    release.freeze_outputs()
            self.assertEqual(release.state()["state"], "freeze-prepared")
            self.assertTrue((release.run_root / "output-seal.json").is_file())
            release.freeze_outputs()
            self.assertEqual(release.state()["state"], "output-frozen")

    def test_torn_pending_state_and_persistent_kernel_lock_recover_without_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.state_dir.mkdir(parents=True)
            (release.state_dir / ".pending-0001-dead").write_bytes(b'{"torn":')
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self.assertEqual(release.state()["state"], "running")
            self.assertEqual(
                (release.run_root / ".release-writer.lock").read_bytes(),
                b"stage2-release-kernel-lock/v1\n",
            )
            self.assertEqual(
                [path.name for path in release.state_dir.glob("[0-9][0-9][0-9][0-9]-*.json")],
                ["0001-running.json"],
            )

    def test_atomic_kernel_lock_rejects_concurrent_process_and_invalid_empty_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release, _, _ = self._release(root)
            contender = """
import sys
from pathlib import Path
from scripts.stage2_evaluation_release import EvaluationRelease, ReleaseIntegrityError
root = Path(sys.argv[1])
release = EvaluationRelease(root / 'pack', root / 'private', root / 'run', test_fixture_mode=True)
try:
    with release._locked():
        pass
except ReleaseIntegrityError:
    raise SystemExit(23)
"""
            with release._locked():
                blocked = subprocess.run(
                    [sys.executable, "-c", contender, str(root)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            self.assertEqual(blocked.returncode, 23, blocked.stderr)
            acquired_after_release = subprocess.run(
                [sys.executable, "-c", contender, str(root)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(acquired_after_release.returncode, 0, acquired_after_release.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.run_root.mkdir(parents=True, exist_ok=True)
            lock = release.run_root / ".release-writer.lock"
            lock.write_bytes(b"")
            with self.assertRaises(ReleaseIntegrityError):
                with release._locked():
                    pass
            self.assertEqual(lock.read_bytes(), b"")

    def test_pack_or_threshold_mutation_after_preparation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            thresholds = pack / "thresholds.json"
            thresholds.chmod(0o644)
            value = json.loads(thresholds.read_text(encoding="utf-8"))
            value["minimum_safe_routing_basis_points"] = 0
            thresholds.write_bytes(canonical_json_bytes(value))
            with self.assertRaises(ReleaseIntegrityError):
                release.prepare_freeze()
            self.assertEqual(release.state()["state"], "running")

    def test_outer_attestation_must_prove_canonical_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze()
            release.freeze_outputs()
            bad = {
                "schema_version": "stage2-isolation-attestation/v1",
                "writer": "evaluated-process",
            }
            with self.assertRaises(ReleaseIntegrityError):
                validate_outer_attestation(bad, release.preparation(), release.output_seal())

    def test_valid_attestation_allows_one_way_oracle_release_and_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, private = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze()
            release.freeze_outputs()
            self._write_attestation(release)
            release.verify_eligibility()
            release.release_oracle()
            report = release.score()
            self.assertEqual(release.state()["state"], "scored")
            self.assertEqual(report["denominator_conservation"]["all_scheduled_cases"], 36)
            self.assertTrue((release.run_root / "oracle-release.json").exists())
            self.assertTrue((private / "oracle.jsonl").exists())

    def test_post_oracle_result_is_regression_only_and_cannot_replace_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            release.verify_eligibility(); release.release_oracle(); release.score()
            original = (release.run_root / "score.json").read_bytes()
            regression = release.record_regression_result({"case_id": "S2-CF-CASE-0001", "result": "FIXED"})
            self.assertEqual(regression["evidence_class"], "regression-only-post-oracle")
            self.assertEqual((release.run_root / "score.json").read_bytes(), original)

    def test_raw_exact_zero_failure_persists_terminal_invalidation_before_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            raw = {
                "critical_control_failures": ["FALSE_VERIFICATION"],
                "validation_digest": "d" * 64,
            }
            with mock.patch(
                "scripts.stage2_evaluation_release.validate_pre_oracle_outputs",
                return_value=raw,
            ):
                with self.assertRaises(ReleaseIntegrityError):
                    release.verify_eligibility()
            self.assertEqual(release.state()["state"], "invalidated")
            self.assertFalse((release.run_root / "oracle-release.json").exists())
            with self.assertRaises(ReleaseIntegrityError):
                release.release_oracle()

    def test_pre_eligibility_never_opens_private_oracle_or_nonce(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, private = self._release(Path(temporary))
            forbidden = {
                (private / "oracle.jsonl").resolve(),
                (private / "oracle-nonce.bin").resolve(),
            }
            original_read_bytes = Path.read_bytes

            def guarded_read_bytes(path: Path) -> bytes:
                if path.resolve() in forbidden:
                    raise AssertionError(f"pre-eligibility private read: {path.name}")
                return original_read_bytes(path)

            with mock.patch.object(Path, "read_bytes", guarded_read_bytes):
                release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
                self._populate_output(release)
                release.prepare_freeze()
                release.freeze_outputs()
                self._write_attestation(release)
                release.verify_materialized()
                release.verify_eligibility()
            self.assertEqual(release.state()["state"], "eligibility-verified")

    def test_missing_private_material_fails_only_at_oracle_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, private = self._release(Path(temporary))
            (private / "oracle.jsonl").chmod(0o644)
            (private / "oracle-nonce.bin").chmod(0o644)
            (private / "oracle.jsonl").unlink()
            (private / "oracle-nonce.bin").unlink()
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            release.verify_eligibility()
            self.assertEqual(release.state()["state"], "eligibility-verified")
            with self.assertRaises(ReleaseIntegrityError):
                release.release_oracle()
            self.assertEqual(release.state()["state"], "eligibility-verified")
            self.assertFalse((release.run_root / "oracle-release.json").exists())

    def test_private_hardlink_is_rejected_at_oracle_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, private = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            release.verify_eligibility()
            oracle = private / "oracle.jsonl"
            payload = oracle.read_bytes()
            oracle.chmod(0o644)
            oracle.unlink()
            outside = Path(temporary) / "outside-oracle.jsonl"
            outside.write_bytes(payload)
            os.link(outside, oracle)
            with self.assertRaises(ReleaseIntegrityError):
                release.release_oracle()
            self.assertEqual(release.state()["state"], "eligibility-verified")

    def test_private_reader_opens_once_and_reads_from_that_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private_file = root / "oracle.jsonl"
            payload = b'{"case_id":"S2-CASE-CF-0001"}\n'
            private_file.write_bytes(payload)
            real_open = os.open
            with mock.patch.object(release_module.os, "open", side_effect=real_open) as opener:
                self.assertEqual(
                    release_module._read_private_file_once(private_file, root, max_bytes=1024),
                    payload,
                )
            self.assertEqual(opener.call_count, 1)
            flags = opener.call_args.args[1]
            if hasattr(os, "O_NOFOLLOW"):
                self.assertTrue(flags & os.O_NOFOLLOW)

    def test_private_reader_rejects_path_swap_before_consuming_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private_file = root / "oracle.jsonl"
            replacement = root / "replacement.jsonl"
            private_file.write_bytes(b"trusted\n")
            replacement.write_bytes(b"swapped\n")
            original_stat = os.stat(private_file, follow_symlinks=False)
            replacement_stat = os.stat(replacement, follow_symlinks=False)
            with (
                mock.patch.object(
                    release_module,
                    "_path_stat_no_follow",
                    side_effect=[original_stat, replacement_stat],
                ),
                mock.patch.object(release_module, "_open_private_descriptor", return_value=991),
                mock.patch.object(release_module, "_descriptor_stat", return_value=original_stat),
                mock.patch.object(release_module.os, "read") as reader,
                mock.patch.object(release_module.os, "close"),
            ):
                with self.assertRaises(ReleaseIntegrityError):
                    release_module._read_private_file_once(private_file, root, max_bytes=1024)
            reader.assert_not_called()

    def test_wrong_private_material_fails_only_at_oracle_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, _, private = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            release.verify_eligibility()
            oracle = private / "oracle.jsonl"
            oracle.chmod(0o644)
            oracle.write_bytes(oracle.read_bytes() + b"{}\n")
            with self.assertRaises(ReleaseIntegrityError):
                release.release_oracle()
            self.assertEqual(release.state()["state"], "eligibility-verified")
            self.assertFalse((release.run_root / "oracle-release.json").exists())

    def test_container_command_has_outer_enforced_restrictions(self) -> None:
        command = build_docker_command(
            image=FAKE_IMAGE_ID,
            input_root=Path("C:/input").resolve(),
            output_root=Path("C:/output").resolve(),
        )
        joined = " ".join(command)
        for required in (
            "--network none",
            "--cap-drop ALL",
            "--security-opt no-new-privileges:true",
            "--pids-limit 1",
            "--read-only",
            "target=/input,readonly",
            "--tmpfs /work:rw,noexec,nosuid,nodev,size=256m",
        ):
            self.assertIn(required, joined)
        self.assertNotIn(".git", joined)
        self.assertNotIn("private", joined.lower())
        self.assertNotIn("target=/output", joined)

    def test_mutable_tag_and_tampered_build_labels_cannot_enter_create(self) -> None:
        pins = {
            "runtime_build_input_sha256": "b" * 64,
            "seccomp_profile_sha256": hashlib.sha256(SECCOMP_PROFILE.read_bytes()).hexdigest(),
            "source_commit": FAKE_COMMIT,
            "source_tree": FAKE_TREE,
        }
        receipt = verify_image_info(
            {"Config": {"Labels": INSPECT_LABELS}, "Id": FAKE_IMAGE_ID},
            pins,
        )
        self.assertEqual(receipt["seccomp_profile_sha256"], pins["seccomp_profile_sha256"])
        command = build_docker_command(
            image=receipt["image_id"],
            input_root=Path("C:/input").resolve(),
            seccomp_profile=SECCOMP_PROFILE,
        )
        self.assertEqual(command[-1], FAKE_IMAGE_ID)
        self.assertNotIn("stage2-evaluation:mutable", command)
        with self.assertRaises(RuntimeError):
            build_docker_command(
                image="stage2-evaluation:mutable",
                input_root=Path("C:/input").resolve(),
                seccomp_profile=SECCOMP_PROFILE,
            )
        tampered = json.loads(json.dumps({"Config": {"Labels": INSPECT_LABELS}, "Id": FAKE_IMAGE_ID}))
        tampered["Config"]["Labels"]["stage2.build_input_sha256"] = "c" * 64
        with self.assertRaises(RuntimeError):
            verify_image_info(tampered, pins)
        tampered_seccomp = json.loads(json.dumps({"Config": {"Labels": INSPECT_LABELS}, "Id": FAKE_IMAGE_ID}))
        tampered_seccomp["Config"]["Labels"]["stage2.seccomp_profile_sha256"] = "d" * 64
        with self.assertRaises(RuntimeError):
            verify_image_info(tampered_seccomp, pins)

    def test_minimal_image_context_excludes_oracle_evaluator_and_release_modules(self) -> None:
        forbidden = {
            "evaluate_recovery_workflow.py",
            "generate_stage2_evaluation.py",
            "stage2_evaluation_release.py",
        }
        self.assertFalse(forbidden & set(RUNTIME_MODULES))
        for name in RUNTIME_MODULES:
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertNotIn("from scripts.evaluate_recovery_workflow", source)
            self.assertNotIn("from scripts.generate_stage2_evaluation", source)
            self.assertNotIn("from scripts.stage2_evaluation_release", source)

    def test_docker_inspect_is_independently_checked_before_attestation(self) -> None:
        input_root = Path("C:/input").resolve(); output_root = Path("C:/output").resolve()
        inline_profile = json.dumps(
            json.loads(SECCOMP_PROFILE.read_text(encoding="utf-8")),
            separators=(",", ":"),
            sort_keys=True,
        )
        inspected = {
            "Config": {
                "Labels": INSPECT_LABELS,
                "User": "65532:65532",
                "Cmd": [],
                "Entrypoint": ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"],
            },
            "Image": FAKE_IMAGE_ID,
            "HostConfig": {
                "CapDrop": ["ALL"], "Memory": 268435456, "NanoCpus": 500000000,
                "NetworkMode": "none", "PidsLimit": 1, "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true", f"seccomp={inline_profile}"],
                "Tmpfs": {"/work": "rw,noexec,nosuid,nodev,size=256m"},
                "IpcMode": "none",
                "Ulimits": [{"Hard": 64, "Name": "nofile", "Soft": 64}],
            },
            "Mounts": [
                {"Destination": "/input", "RW": False, "Source": str(input_root), "Type": "bind"},
            ],
        }
        validate_docker_inspect(
            inspected,
            input_root,
            output_root,
            expected_image_id=FAKE_IMAGE_ID,
            expected_labels=INSPECT_LABELS,
            seccomp_profile=SECCOMP_PROFILE,
        )
        wrong_image = json.loads(json.dumps(inspected))
        wrong_image["Image"] = "sha256:" + "f" * 64
        with self.assertRaises(RuntimeError):
            validate_docker_inspect(
                wrong_image,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )
        alternate_seccomp = json.loads(json.dumps(inspected))
        alternate_seccomp["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges:true",
            "seccomp=C:/alternate-weaker-profile.json",
        ]
        with self.assertRaises(RuntimeError):
            validate_docker_inspect(
                alternate_seccomp,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )
        inspected["HostConfig"]["NetworkMode"] = "default"
        with self.assertRaises(RuntimeError):
            validate_docker_inspect(
                inspected,
                input_root,
                output_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )

    def test_docker_desktop_inline_seccomp_must_semantically_equal_frozen_profile(self) -> None:
        input_root = Path("C:/input").resolve()
        frozen_profile = json.loads(SECCOMP_PROFILE.read_text(encoding="utf-8"))
        inline_profile = json.dumps(frozen_profile, separators=(",", ":"), sort_keys=True)
        inspected = {
            "Config": {
                "Labels": INSPECT_LABELS,
                "User": "65532:65532",
                "Cmd": [],
                "Entrypoint": ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"],
            },
            "Image": FAKE_IMAGE_ID,
            "HostConfig": {
                "CapDrop": ["ALL"], "Memory": 268435456, "NanoCpus": 500000000,
                "NetworkMode": "none", "PidsLimit": 1, "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true", f"seccomp={inline_profile}"],
                "Tmpfs": {"/work": "rw,noexec,nosuid,nodev,size=256m"},
                "IpcMode": "none",
                "Ulimits": [{"Hard": 64, "Name": "nofile", "Soft": 64}],
            },
            "Mounts": [{"Destination": "/input", "RW": False, "Source": str(input_root), "Type": "bind"}],
        }
        validate_docker_inspect(
            inspected,
            input_root,
            expected_image_id=FAKE_IMAGE_ID,
            expected_labels=INSPECT_LABELS,
            seccomp_profile=SECCOMP_PROFILE,
        )

        altered_profile = json.loads(json.dumps(frozen_profile))
        altered_profile["defaultAction"] = "SCMP_ACT_ERRNO"
        inspected["HostConfig"]["SecurityOpt"] = [
            "no-new-privileges:true",
            f"seccomp={json.dumps(altered_profile, separators=(',', ':'), sort_keys=True)}",
        ]
        with self.assertRaisesRegex(RuntimeError, "exact frozen seccomp profile"):
            validate_docker_inspect(
                inspected,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )

        frozen_bytes = SECCOMP_PROFILE.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            held_profile = Path(temporary) / "seccomp.json"
            held_profile.write_bytes(frozen_bytes)
            held_profile.write_bytes(canonical_json_bytes(altered_profile))
            held_profile.write_bytes(frozen_bytes)
            self.assertEqual(held_profile.read_bytes(), frozen_bytes)
            with self.assertRaisesRegex(RuntimeError, "exact frozen seccomp profile"):
                validate_docker_inspect(
                    inspected,
                    input_root,
                    expected_image_id=FAKE_IMAGE_ID,
                    expected_labels=INSPECT_LABELS,
                    seccomp_profile=held_profile,
                    expected_seccomp_bytes=frozen_bytes,
                )

    def test_docker_text_output_is_strict_utf8_and_accented_mount_identity_is_exact(self) -> None:
        raw = '{"Mounts":[{"Source":"C:/Users/roden/OneDrive/Imágenes/input"}]}'.encode("utf-8")

        def completed(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
            self.assertEqual(options["encoding"], "utf-8")
            self.assertEqual(options["errors"], "strict")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=raw.decode(str(options["encoding"]), errors=str(options["errors"])),
                stderr="",
            )

        with mock.patch.object(isolated_module.subprocess, "run", side_effect=completed):
            result = isolated_module._run_docker_text(
                ["docker", "inspect", "container-id"], timeout=30
            )
        decoded = json.loads(result.stdout)
        self.assertEqual(
            decoded["Mounts"][0]["Source"],
            "C:/Users/roden/OneDrive/Imágenes/input",
        )
        self.assertNotEqual(
            decoded["Mounts"][0]["Source"],
            "C:/Users/roden/OneDrive/ImÃ¡genes/input",
        )

        with tempfile.TemporaryDirectory() as temporary:
            input_root = Path(temporary) / "Imágenes" / "input"
            input_root.mkdir(parents=True)
            inline_profile = json.dumps(
                json.loads(SECCOMP_PROFILE.read_text(encoding="utf-8")),
                separators=(",", ":"),
                sort_keys=True,
            )
            inspected = {
                "Config": {
                    "Labels": INSPECT_LABELS,
                    "User": "65532:65532",
                    "Cmd": [],
                    "Entrypoint": ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"],
                },
                "Image": FAKE_IMAGE_ID,
                "HostConfig": {
                    "CapDrop": ["ALL"], "Memory": 268435456, "NanoCpus": 500000000,
                    "NetworkMode": "none", "PidsLimit": 1, "ReadonlyRootfs": True,
                    "SecurityOpt": ["no-new-privileges:true", f"seccomp={inline_profile}"],
                    "Tmpfs": {"/work": "rw,noexec,nosuid,nodev,size=256m"},
                    "IpcMode": "none",
                    "Ulimits": [{"Hard": 64, "Name": "nofile", "Soft": 64}],
                },
                "Mounts": [{"Destination": "/input", "RW": False, "Source": str(input_root.resolve()), "Type": "bind"}],
            }
            validate_docker_inspect(
                inspected,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )
            inspected["Mounts"][0]["Source"] = str(input_root.resolve()).replace("Imágenes", "ImÃ¡genes")
            with self.assertRaisesRegex(RuntimeError, "prepared host identity"):
                validate_docker_inspect(
                    inspected,
                    input_root,
                    expected_image_id=FAKE_IMAGE_ID,
                    expected_labels=INSPECT_LABELS,
                    seccomp_profile=SECCOMP_PROFILE,
                )

    def test_exact_path_echo_cannot_prove_applied_seccomp_even_with_correct_held_bytes(self) -> None:
        input_root = Path("C:/input").resolve()
        frozen_bytes = SECCOMP_PROFILE.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "seccomp.json"
            profile.write_bytes(frozen_bytes)
            inspected = {
                "Config": {
                    "Labels": INSPECT_LABELS,
                    "User": "65532:65532",
                    "Cmd": [],
                    "Entrypoint": ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"],
                },
                "Image": FAKE_IMAGE_ID,
                "HostConfig": {
                    "CapDrop": ["ALL"], "Memory": 268435456, "NanoCpus": 500000000,
                    "NetworkMode": "none", "PidsLimit": 1, "ReadonlyRootfs": True,
                    "SecurityOpt": ["no-new-privileges:true", f"seccomp={profile.resolve()}"],
                    "Tmpfs": {"/work": "rw,noexec,nosuid,nodev,size=256m"},
                    "IpcMode": "none",
                    "Ulimits": [{"Hard": 64, "Name": "nofile", "Soft": 64}],
                },
                "Mounts": [{"Destination": "/input", "RW": False, "Source": str(input_root), "Type": "bind"}],
            }
            with self.assertRaisesRegex(RuntimeError, "exact frozen seccomp profile"):
                validate_docker_inspect(
                    inspected,
                    input_root,
                    expected_image_id=FAKE_IMAGE_ID,
                    expected_labels=INSPECT_LABELS,
                    seccomp_profile=profile,
                    expected_seccomp_bytes=frozen_bytes,
                )

    def test_held_seccomp_identity_rejects_swapped_path_and_tampered_bytes(self) -> None:
        from scripts.run_stage2_isolated import _verify_held_seccomp_profile

        frozen_bytes = SECCOMP_PROFILE.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected_path = root / "expected.json"
            swapped_path = root / "swapped.json"
            expected_path.write_bytes(frozen_bytes)
            swapped_path.write_bytes(frozen_bytes)
            descriptor = os.open(expected_path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            try:
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    _verify_held_seccomp_profile(swapped_path, descriptor, frozen_bytes)
            finally:
                os.close(descriptor)

            tampered_path = root / "tampered.json"
            tampered_path.write_bytes(frozen_bytes + b" ")
            descriptor = os.open(tampered_path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            try:
                with self.assertRaisesRegex(RuntimeError, "bytes changed"):
                    _verify_held_seccomp_profile(tampered_path, descriptor, frozen_bytes)
            finally:
                os.close(descriptor)

    def test_docker_inspect_rejects_extra_mount_and_unconfined_seccomp(self) -> None:
        input_root = Path("C:/input").resolve()
        inline_profile = json.dumps(
            json.loads(SECCOMP_PROFILE.read_text(encoding="utf-8")),
            separators=(",", ":"),
            sort_keys=True,
        )
        inspected = {
            "Config": {
                "Labels": INSPECT_LABELS,
                "User": "65532:65532",
                "Cmd": [],
                "Entrypoint": ["/usr/bin/env", "-i", "PYTHONPATH=/app", "TMPDIR=/work", "/usr/local/bin/python", "/app/scripts/run_stage2_isolated.py", "--inner"],
            },
            "Image": FAKE_IMAGE_ID,
            "HostConfig": {
                "CapDrop": ["ALL"], "Memory": 268435456, "NanoCpus": 500000000,
                "NetworkMode": "none", "PidsLimit": 1, "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges:true", f"seccomp={inline_profile}"],
                "Tmpfs": {"/work": "rw,noexec,nosuid,nodev,size=256m"},
                "IpcMode": "none",
                "Ulimits": [{"Hard": 64, "Name": "nofile", "Soft": 64}],
            },
            "Mounts": [{"Destination": "/input", "RW": False, "Source": str(input_root), "Type": "bind"}],
        }
        extra = json.loads(json.dumps(inspected))
        extra["Mounts"].append({"Destination": "/repo", "RW": False, "Source": "C:/repo", "Type": "bind"})
        with self.assertRaises(RuntimeError):
            validate_docker_inspect(
                extra,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )
        unconfined = json.loads(json.dumps(inspected))
        unconfined["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true", "seccomp=unconfined"]
        with self.assertRaises(RuntimeError):
            validate_docker_inspect(
                unconfined,
                input_root,
                expected_image_id=FAKE_IMAGE_ID,
                expected_labels=INSPECT_LABELS,
                seccomp_profile=SECCOMP_PROFILE,
            )

    def test_inner_runtime_rejects_evaluator_or_oracle_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            case_path = pack / "cases.jsonl"
            case_path.chmod(0o644)
            first, *rest = case_path.read_text(encoding="utf-8").splitlines()
            record = json.loads(first)
            record["oracle"] = "leak"
            case_path.write_text("\n".join([json.dumps(record), *rest]) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                run_inner_evaluation(pack, release.output_root)

    def test_inner_runtime_rejects_named_family_metadata_in_any_json_artifact(self) -> None:
        for relative in ("manifest.json", "acquisition-contract.json", "provider-requests.jsonl"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                release, pack, _ = self._release(Path(temporary))
                path = pack / relative
                path.chmod(0o644)
                if relative.endswith(".jsonl"):
                    first, *rest = path.read_text(encoding="utf-8").splitlines()
                    value = json.loads(first)
                    value["evaluation_family"] = "named-leak"
                    path.write_bytes(canonical_json_bytes(value) + b"".join(
                        line.encode("utf-8") + b"\n" for line in rest
                    ))
                else:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    value["family_counts"] = {"named-leak": 3}
                    path.write_bytes(canonical_json_bytes(value))
                manifest_path = pack / "manifest.json"
                if relative != "manifest.json":
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["artifact_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
                    manifest_path.chmod(0o644)
                    manifest_path.write_bytes(canonical_json_bytes(manifest))
                with self.assertRaises(ValueError):
                    run_inner_evaluation(pack, release.output_root)

    def test_inner_runtime_rejects_any_extra_mounted_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            (pack / "oracle.jsonl").write_bytes(b"{}\n")
            with self.assertRaises(ValueError):
                run_inner_evaluation(pack, release.output_root)

    def test_dry_run_launcher_never_self_attests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            from scripts.run_stage2_isolated import launch_container
            command = launch_container(pack, release.output_root, image=FAKE_IMAGE_ID, dry_run=True)
            self.assertIn("docker", command)
            self.assertFalse((release.output_root / "isolation-attestation.json").exists())

    def test_outer_verify_rejects_forged_attestation_and_current_output_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release, pack, private = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            verified = release.verify_materialized()
            self.assertEqual(verified["status"], "verified")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(
                    release_main(
                        [
                            "verify-materialized",
                            "--pack-root", str(pack),
                            "--private-root", str(private),
                            "--run-root", str(release.run_root),
                        ]
                    ),
                    0,
                )
            self.assertIn('"status":"verified"', output.getvalue())
            attestation = release.attestation_path
            attestation.chmod(0o644)
            value = json.loads(attestation.read_text(encoding="utf-8"))
            value["image_id"] = "sha256:" + "f" * 64
            attestation.write_bytes(canonical_json_bytes(value))
            with self.assertRaises((ReleaseIntegrityError, RuntimeError)):
                release.verify_materialized()
            with self.assertRaises(ReleaseIntegrityError):
                release_main(
                    [
                        "verify-materialized",
                        "--pack-root", str(pack),
                        "--private-root", str(private),
                        "--run-root", str(release.run_root),
                    ]
                )

        with tempfile.TemporaryDirectory() as temporary:
            release, pack, _ = self._release(Path(temporary))
            release.prepare(source_commit=FAKE_COMMIT, source_tree=FAKE_TREE)
            self._populate_output(release)
            release.prepare_freeze(); release.freeze_outputs(); self._write_attestation(release)
            target = release.output_root / "capability-probes.json"
            target.chmod(0o644)
            target.write_bytes(target.read_bytes() + b" ")
            with self.assertRaises(ReleaseIntegrityError):
                release.verify_materialized()


if __name__ == "__main__":
    unittest.main()
