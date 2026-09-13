"""Hermetic coverage for both distributed copies of the PR poller."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_poller(side):
    path = ROOT / "config" / side / "skills/babysit-pr/scripts/poll_prs.py"
    spec = importlib.util.spec_from_file_location(f"poll_{side}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PollerTests(unittest.TestCase):
    def setUp(self):
        self.modules = [load_poller(side) for side in ("codex", "claude")]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state.json"
        self.args = ["owner/repo#1", "--state-file", str(self.state)]

    def test_defaults_and_explicit_bounds(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                self.assertEqual(module.parse_args(self.args).max_cycles, 1)
                self.assertIsNone(module.parse_args(self.args + ["--watch"]).max_cycles)
                self.assertEqual(module.parse_args(self.args + ["--watch", "--max-cycles", "1200"]).max_cycles, 1200)

    def test_invalid_intervals_and_bounds(self):
        for module in self.modules:
            for option in (["--interval", "0"], ["--interval", "-1"],
                           ["--interval", "nan"], ["--interval", "inf"],
                           ["--max-cycles", "0"]):
                with self.subTest(module=module.__name__, option=option):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        module.parse_args(self.args + ["--watch"] + option)
            fixture = module.parse_args(self.args + ["--watch", "--fixture", "fixture.json", "--interval", "0"])
            self.assertEqual(fixture.interval, 0)

    def test_every_gh_call_has_a_timeout(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                result = subprocess.CompletedProcess([], 0, "{}", "")
                with mock.patch.object(module.subprocess, "run", return_value=result) as run:
                    module.ensure_gh_auth("gh")
                    with mock.patch.object(module, "gh_executable", return_value="gh"):
                        module.fetch_live("owner/repo", 1)
                self.assertEqual(run.call_count, 5)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["timeout"], module.GH_TIMEOUT_SECONDS)

    def test_timeouts_are_handled_for_auth_and_json(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                with mock.patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 30)):
                    for invoke in (lambda: module.run_json(["gh", "api"]),
                                   lambda: module.ensure_gh_auth("gh")):
                        with self.assertRaisesRegex(RuntimeError, "timed out"):
                            invoke()
                    with mock.patch.object(module, "gh_executable", return_value="gh"), contextlib.redirect_stderr(io.StringIO()) as error:
                        self.assertEqual(module.main(self.args), 1)
                    self.assertEqual(json.loads(error.getvalue())["type"], "poll_error")

    def test_continuous_watch_runs_past_old_limit_until_interrupt(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                output = io.StringIO()
                with mock.patch.object(module, "gh_executable", return_value="gh"), mock.patch.object(module, "ensure_gh_auth"), mock.patch.object(module, "fetch_live", return_value={"pr": {}}) as fetch, mock.patch.object(module.time, "sleep", side_effect=[None] * 11 + [KeyboardInterrupt]), contextlib.redirect_stdout(output):
                    self.assertEqual(module.main(self.args + ["--watch"]), 130)
                self.assertEqual(fetch.call_count, 12)
                self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["cycle"], 12)

    def test_poll_timeout_emits_error_and_next_cycle_retries(self):
        for module in self.modules:
            with self.subTest(module=module.__name__):
                error = io.StringIO()
                timeout = subprocess.TimeoutExpired("gh", 30)
                result = subprocess.CompletedProcess([], 0, "{}", "")
                with mock.patch.object(module, "gh_executable", return_value="gh"), mock.patch.object(module, "ensure_gh_auth"), mock.patch.object(module.subprocess, "run", side_effect=[timeout] + [result] * 4), mock.patch.object(module.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(error):
                    self.assertEqual(module.main(self.args + ["--watch", "--max-cycles", "2"]), 1)
                self.assertIn("timed out", json.loads(error.getvalue())["message"])
                summaries = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual([item["errors"] for item in summaries], [1, 0])
                sleep.assert_called_once_with(60.0)

    def test_fixture_zero_interval_and_one_shot(self):
        fixture = Path(self.temp.name) / "fixture.json"
        fixture.write_text(json.dumps({"owner/repo#1": {"pr": {}}}), encoding="utf-8")
        for module in self.modules:
            for watch, expected in (([], 1), (["--watch", "--max-cycles", "3"], 3)):
                with self.subTest(module=module.__name__, watch=watch):
                    with mock.patch.object(module, "gh_executable", side_effect=AssertionError("fixture must not invoke gh")), mock.patch.object(module.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(module.main(self.args + ["--fixture", str(fixture), "--interval", "0"] + watch), 0)
                    self.assertEqual(len(output.getvalue().splitlines()), expected)
                    self.assertEqual(sleep.call_count, expected - 1)


if __name__ == "__main__":
    unittest.main()
