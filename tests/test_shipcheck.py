"""Contract tests for shipcheck. Run: python -m unittest discover -s tests -v

Every test builds REAL artifacts (stdlib zipfile/tarfile), installs REAL
wheels with pip, and asserts the exit code and findings a consumer would
observe -- nothing internal.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "shipcheck.py")
sys.path.insert(0, os.path.join(ROOT, "examples"))
from make_fixtures import make_wheel, make_sdist  # noqa: E402


def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def make_src(tmp, body='__version__ = "0.1.0"\n'):
    src = os.path.join(tmp, "src")
    os.makedirs(os.path.join(src, "mypkg"), exist_ok=True)
    with open(os.path.join(src, "mypkg", "__init__.py"), "w") as fh:
        fh.write(body)
    return src


class ShipcheckContract(unittest.TestCase):
    def test_clean_artifacts_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = make_src(tmp)
            dist = os.path.join(tmp, "dist")
            make_wheel(dist)
            make_sdist(dist)
            code, out, _ = run_cli(dist, "--src", src)
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)
        self.assertIn("2 artifacts", out)

    def test_empty_wheel_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            make_wheel(dist, modules={})
            code, out, _ = run_cli(dist, "--src", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("empty-package", out)
        self.assertIn("no Python modules", out)

    def test_unimportable_wheel_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            make_wheel(dist, modules={"mypkg/__init__.py":
                                      "import definitely_missing_dep_xyz\n"})
            code, out, _ = run_cli(dist, "--src", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("unimportable", out)
        self.assertIn("import mypkg failed", out)

    def test_metadata_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            make_wheel(dist, version="0.2.0", meta_version="0.1.0")
            code, out, _ = run_cli(dist, "--src", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("metadata-mismatch", out)
        self.assertIn("0.2.0", out)

    def test_broken_entrypoint_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            make_wheel(dist, entry_points="[console_scripts]\n"
                                          "foo = mypkg.missing:main\n")
            code, out, _ = run_cli(dist, "--src", tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("broken-entrypoint", out)
        self.assertIn("mypkg.missing", out)

    def test_stale_artifact_warns_and_strict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            wheel = make_wheel(dist)
            past = time.time() - 100
            os.utime(wheel, (past, past))
            src = make_src(tmp)
            code, out, _ = run_cli(dist, "--src", src)
            self.assertEqual(code, 0, out)
            self.assertIn("stale-artifact", out)
            code, out, _ = run_cli(dist, "--src", src, "--strict")
            self.assertEqual(code, 1, out)

    def test_no_dist_warns_and_strict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_cli(tmp)
            self.assertEqual(code, 0, out)
            self.assertIn("no-dist", out)
            code, out, _ = run_cli(tmp, "--strict")
            self.assertEqual(code, 1, out)

    def test_allow_suppresses_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            make_wheel(dist, modules={"mypkg/__init__.py":
                                      "import definitely_missing_dep_xyz\n"})
            code, out, _ = run_cli(dist, "--src", tmp,
                                   "--allow",
                                   "unimportable=needs GPU at runtime")
        self.assertEqual(code, 0, out)
        self.assertIn("clean", out)
        self.assertIn("1 exempt by --allow", out)

    def test_json_reports_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            wheel = make_wheel(dist, modules={"mypkg/__init__.py":
                                              "import definitely_missing_dep_xyz\n"})
            code, out, _ = run_cli(dist, "--src", tmp, "--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertFalse(data["ok"])
        self.assertEqual(data["counts"]["fail"], 1)
        self.assertEqual(data["counts"]["suppressed"], 0)
        self.assertIn(os.path.basename(wheel), data["scanned"]["artifacts"][0])
        self.assertEqual(data["findings"][0]["rule"], "unimportable")
        self.assertIn("suggestion", data["findings"][0])


if __name__ == "__main__":
    unittest.main()
