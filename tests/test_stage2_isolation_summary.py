import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
import shutil

from scripts.build_stage2_isolation_summary import (
    ATTESTATION,
    OUTPUT,
    IsolationSummaryError,
    _canonical,
    build_isolation_summary,
    verify_isolation_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage2IsolationSummaryTests(unittest.TestCase):
    def test_committed_summary_reproduces_canonical_public_sources(self):
        if not (PROJECT_ROOT / ATTESTATION).is_file():
            self.skipTest("raw host-path attestation is intentionally excluded")
        expected = _canonical(build_isolation_summary(PROJECT_ROOT))
        self.assertEqual(expected, (PROJECT_ROOT / OUTPUT).read_bytes())

    def test_changed_projected_control_cannot_verify(self):
        if not (PROJECT_ROOT / ATTESTATION).is_file():
            self.skipTest("raw host-path attestation is intentionally excluded")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "data/stage2/runs/S2-CF-RUN-0005/outer/isolation-attestation.json",
                "data/stage2/runs/S2-CF-RUN-0005/release-states/0004-eligibility-verified.json",
                OUTPUT.as_posix(),
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / relative, target)
            changed = deepcopy(build_isolation_summary(root))
            changed["verified_controls"]["network"] = "enabled"
            (root / OUTPUT).write_bytes(_canonical(changed))
            with self.assertRaisesRegex(IsolationSummaryError, "differs"):
                verify_isolation_summary(root)

    def test_public_bundle_verifies_preserved_projection_without_private_path_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "data/stage2/runs/S2-CF-RUN-0005/release-states/0004-eligibility-verified.json",
                OUTPUT.as_posix(),
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / relative, target)

            self.assertFalse(
                (root / "data/stage2/runs/S2-CF-RUN-0005/outer/isolation-attestation.json").exists()
            )
            verify_isolation_summary(root, public_projection_only=True)

            changed = deepcopy(json.loads((root / OUTPUT).read_text(encoding="utf-8")))
            changed["verified_controls"]["network"] = "enabled"
            (root / OUTPUT).write_bytes(_canonical(changed))
            with self.assertRaisesRegex(IsolationSummaryError, "does not bind"):
                verify_isolation_summary(root, public_projection_only=True)


if __name__ == "__main__":
    unittest.main()
