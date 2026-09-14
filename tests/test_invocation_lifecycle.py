"""Regression tests for the two POSIX invocation lifecycle payloads."""

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "config" / tool / "skills" / skill / "scripts/invocation_lifecycle.py"
           for tool, skill in [("codex", "learn-to-use-claude"), ("claude", "learn-to-use-codex")]]


@unittest.skipUnless(os.name == "posix", "Lifecycle helpers currently require POSIX process APIs")
class LifecycleTests(unittest.TestCase):
    def exercise(self, callback):
        for index, script in enumerate(SCRIPTS):
            with self.subTest(script=str(script)), tempfile.TemporaryDirectory() as temporary:
                spec = importlib.util.spec_from_file_location(f"lifecycle_{index}", script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                artifact = Path(temporary)
                module.atomic_json(artifact / "invocation.json", {"invocation_id": "regression"})
                callback(module, script, artifact)

    def claim_args(self, artifact, token="owner"):
        return argparse.Namespace(artifact_dir=str(artifact), owner_pid=os.getpid(), claim_token=token,
                                  deadline=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).isoformat())

    def complete_args(self, artifact, token="owner"):
        result = artifact / "learning-result.md"
        result.write_text("No reusable proposals.\n", encoding="utf-8")
        return argparse.Namespace(artifact_dir=str(artifact), claim_token=token, result=str(result),
                                  outcome="no_proposals", proposal_id=[])

    def test_completion_requires_claim_and_matching_owner(self):
        def check(module, script, artifact):
            self.assertEqual(module.hook_complete(self.complete_args(artifact)), 2)
            self.assertEqual(module.hook_claim(self.claim_args(artifact)), 0)
            self.assertEqual(module.hook_complete(self.complete_args(artifact, "wrong")), 2)
            self.assertEqual(module.hook_complete(self.complete_args(artifact)), 0)
            done = module.marker_paths(artifact, "regression")[2]
            original = done.read_bytes()
            self.assertEqual(module.hook_complete(self.complete_args(artifact)), 3)
            self.assertEqual(done.read_bytes(), original)
            self.assertEqual(module.hook_claim(self.claim_args(artifact)), 3)
        self.exercise(check)

    def test_claim_helper_exit_keeps_long_lived_owner_and_excludes_competitor(self):
        def check(module, script, artifact):
            args = self.claim_args(artifact)
            command = [sys.executable, str(script), "hook-claim", "--artifact-dir", str(artifact),
                       "--owner-pid", str(os.getpid()), "--deadline", args.deadline]
            first = subprocess.Popen(command + ["--claim-token", "first"])
            second = subprocess.Popen(command + ["--claim-token", "second"])
            self.assertEqual(sorted([first.wait(timeout=5), second.wait(timeout=5)]), [0, 2])
            started = module.load_json(module.marker_paths(artifact, "regression")[0])
            self.assertEqual(started["owner_pid"], os.getpid())
            self.assertEqual(module.hook_claim(args), 2)
        self.exercise(check)

    def test_takeover_fences_expired_token(self):
        def check(module, script, artifact):
            self.assertEqual(module.hook_claim(self.claim_args(artifact)), 0)
            started = module.marker_paths(artifact, "regression")[0]
            value = module.load_json(started)
            value["hook_deadline_at"] = "2000-01-01T00:00:00+00:00"
            module.atomic_json(started, value)
            self.assertEqual(module.hook_claim(self.claim_args(artifact, "replacement")), 0)
            self.assertEqual(module.hook_complete(self.complete_args(artifact)), 2)
            self.assertEqual(module.hook_complete(self.complete_args(artifact, "replacement")), 0)
        self.exercise(check)

    def test_tampered_result_does_not_count_as_done(self):
        def check(module, script, artifact):
            module.hook_claim(self.claim_args(artifact))
            module.hook_complete(self.complete_args(artifact))
            (artifact / "learning-result.md").write_text("modified", encoding="utf-8")
            self.assertEqual(module.hook_status(argparse.Namespace(artifact_dir=str(artifact))), 4)
            self.assertEqual(module.hook_claim(self.claim_args(artifact)), 4)
        self.exercise(check)

    def test_utc_z_deadlines_work_for_claim_existing_owner_and_completion(self):
        def check(module, script, artifact):
            args = self.claim_args(artifact)
            args.deadline = args.deadline.replace("+00:00", "Z")
            self.assertEqual(module.parse_deadline(args.deadline).utcoffset(), dt.timedelta(0))
            self.assertEqual(module.hook_claim(args), 0)
            self.assertEqual(module.hook_claim(self.claim_args(artifact, "competitor")), 2)
            self.assertEqual(module.hook_complete(self.complete_args(artifact)), 0)
        self.exercise(check)

    def test_corrupted_done_marker_returns_contract_exit_code(self):
        def check(module, script, artifact):
            done = module.marker_paths(artifact, "regression")[2]
            for data in ["{broken", "[]", "null"]:
                done.write_text(data, encoding="utf-8")
                args = argparse.Namespace(artifact_dir=str(artifact))
                self.assertEqual(module.hook_status(args), 4)
                result = subprocess.run([sys.executable, str(script), "hook-status", "--artifact-dir", str(artifact)],
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 4)
        self.exercise(check)

    def test_execution_budget_starts_after_launch_and_matches_recorded_deadline(self):
        def check(module, script, artifact):
            child = Mock(pid=12345, returncode=0)
            child.poll.side_effect = [None, 0]
            args = self.supervise_args(artifact, "unused")
            args.timeout = 1.0
            with patch.object(module.subprocess, "Popen", return_value=child), \
                    patch.object(module, "process_identity", return_value="identity"), \
                    patch.object(module, "group_alive", return_value=False), \
                    patch.object(module.time, "monotonic", side_effect=[0.0, 100.0, 100.25, 101.0]):
                self.assertEqual(module.supervise(args), 0)
            child.wait.assert_called_once_with(timeout=0.1)
            record = module.load_json(artifact / "invocation.json")
            elapsed = module.parse_deadline(record["deadline_at"]) - module.parse_deadline(record["dispatched_at"])
            self.assertEqual(elapsed.total_seconds(), args.timeout)
            self.assertEqual(record["duration_seconds"], 101.0)
        self.exercise(check)

    def supervise_args(self, artifact, code):
        return argparse.Namespace(artifact_dir=str(artifact), stdin=None, timeout=0.4, grace=0.15,
                                  timeout_rationale="regression", command=[sys.executable, "-c", code])

    def test_postlaunch_interruption_reaps_child_and_is_not_launch_failure(self):
        def check(module, script, artifact):
            children = []
            original = subprocess.Popen
            def capture(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child
            with patch.object(module.subprocess, "Popen", side_effect=capture), patch.object(module, "process_identity", side_effect=InterruptedError):
                code = module.supervise(self.supervise_args(artifact, "import time; time.sleep(60)"))
            self.assertEqual(code, 130)
            self.assertIsNotNone(children[0].poll())
            self.assertEqual(module.load_json(artifact / "invocation.json")["state"], "cancelled")
        self.exercise(check)

    def test_cancellation_during_postlaunch_setup_is_recorded(self):
        def check(module, script, artifact):
            def cancel(pid):
                os.kill(os.getpid(), signal.SIGTERM)
                return "test-identity"
            with patch.object(module, "process_identity", side_effect=cancel):
                code = module.supervise(self.supervise_args(artifact, "import time; time.sleep(60)"))
            record = module.load_json(artifact / "invocation.json")
            self.assertEqual(code, 143)
            self.assertEqual(record["state"], "cancelled")
            self.assertTrue(record["cancelled"])
        self.exercise(check)

    def test_success_reports_no_cleanup_required_without_claiming_containment(self):
        def check(module, script, artifact):
            # Some desktop sandboxes deny killpg even after the group disappears.
            with patch.object(module, "group_alive", return_value=False):
                self.assertEqual(module.supervise(self.supervise_args(artifact, "print('report')")), 0)
            record = module.load_json(artifact / "invocation.json")
            self.assertEqual(record["cleanup_outcome"], "not_required")
            self.assertIsNone(record["cleanup_confirmed_at"])
            self.assertEqual(record["cleanup_scope"], "process_group_only")
        self.exercise(check)

    def test_detached_descendant_never_reports_confirmed_tree_cleanup(self):
        def check(module, script, artifact):
            child_pid = None
            try:
                code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(15)'],start_new_session=True); print(p.pid,flush=True); time.sleep(15)"
                args = self.supervise_args(artifact, code)
                args.timeout = 3.0
                return_code = module.supervise(args)
                captured = (artifact / "stdout.log").read_text().strip()
                self.assertTrue(captured.isdecimal(), "Detached fixture did not publish its PID before timeout")
                child_pid = int(captured)
                self.assertEqual(return_code, 124)
                record = module.load_json(artifact / "invocation.json")
                self.assertEqual(record["cleanup_outcome"], "unresolved")
                self.assertIsNone(record["cleanup_confirmed_at"])
                os.kill(child_pid, 0)
            finally:
                if child_pid is None and (artifact / "stdout.log").exists():
                    captured = (artifact / "stdout.log").read_text().strip()
                    if captured.isdecimal():
                        child_pid = int(captured)
                if child_pid:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        self.exercise(check)


if __name__ == "__main__":
    unittest.main()
