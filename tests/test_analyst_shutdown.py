"""Process-level stdout regressions; runnable with unittest without pytest."""
from __future__ import annotations

import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from src.analyst import cli
from src.analyst.common import SYNTHETIC_NOTICE

REPO_ROOT = Path(__file__).resolve().parents[1]

# BufferedWriter retains data after a raw write fails. Keeping the wrapper in
# both stdout slots exercises CPython's real shutdown flush in the child.
FAILED_STDOUT_CHILD = """
import errno
import io
import runpy
import sys

class Full(io.RawIOBase):
    def writable(self):
        return True

    def write(self, data):
        raise OSError(errno.ENOSPC, "synthetic full stream")

stream = io.TextIOWrapper(
    io.BufferedWriter(Full(), buffer_size=65536), encoding="utf-8"
)
sys.stdout = sys.__stdout__ = stream
sys.argv = ["src.analyst", "exercise", "list", *sys.argv[1:]]
runpy.run_module("src.analyst", run_name="__main__")
"""


class AnalystShutdownTests(unittest.TestCase):
    def run_child(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-B", *args], cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )

    def test_buffered_stdout_failure_preserves_exit_6(self) -> None:
        for flag in ([], ["--json"]):
            with self.subTest(flag=flag):
                proc = self.run_child(["-c", FAILED_STDOUT_CHILD, *flag])
                self.assertEqual(proc.returncode, 6, proc.stderr)
                self.assertEqual(proc.stdout, b"")
                # Exactly the existing diagnostic, without teardown traceback.
                self.assertEqual(proc.stderr, (
                    b"error: output failed: OSError: [Errno 28] synthetic full stream "
                    b"(the command itself had finished with exit code 0 "
                    b"before its output was written)\n"
                ))

    def test_successful_stdout(self) -> None:
        for flag in ([], ["--json"]):
            with self.subTest(flag=flag):
                proc = self.run_child(["-m", "src.analyst", "exercise", "list", *flag])
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stderr, b"")
                if flag:
                    envelope = json.loads(proc.stdout)
                    self.assertEqual(envelope["exit_code"], 0)
                    self.assertIs(envelope["ok"], True)
                    self.assertEqual(envelope["command"], "exercise list")
                    self.assertIs(envelope["synthetic"], True)
                else:
                    self.assertTrue(proc.stdout.startswith(SYNTHETIC_NOTICE.encode()))
                    self.assertIn(b"EX01", proc.stdout)

    def test_embedded_main_preserves_caller_stream(self) -> None:
        class RecoverableFull(io.BytesIO):
            failing = True

            def write(self, data: bytes) -> int:
                if self.failing:
                    raise OSError(errno.ENOSPC, "synthetic full stream")
                return super().write(data)

        for flag in ([], ["--json"]):
            with self.subTest(flag=flag):
                raw = RecoverableFull()
                stream = io.TextIOWrapper(
                    io.BufferedWriter(raw, buffer_size=65536), encoding="utf-8"
                )
                err = io.StringIO()
                try:
                    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(err):
                        self.assertEqual(cli.main(["exercise", "list", *flag]), 6)
                        self.assertIs(sys.stdout, stream)
                        self.assertFalse(stream.closed)
                    self.assertTrue(err.getvalue().startswith("error: output failed:"))
                    # The caller can recover and flush its still-owned buffer.
                    raw.failing = False
                    stream.flush()
                    self.assertTrue(raw.getvalue())
                finally:
                    raw.failing = False
                    stream.close()


if __name__ == "__main__":
    unittest.main()
