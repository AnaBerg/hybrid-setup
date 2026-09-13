"""Hermetic installer coverage; no test reads or writes the real user config."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hybrid_install", REPO / "scripts" / "install.py")
install = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = install
SPEC.loader.exec_module(install)

START = "<!-- hybrid-setup:start -->"
END = "<!-- hybrid-setup:end -->"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Resolve macOS's /var -> /private/var temp ancestor symlink.
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "new home"
        self.source = self.root / "source"
        # Keep Windows runtime variables while isolating all configuration discovery.
        self.env = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                          if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH"}}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        for agent, name in (("codex", "AGENTS.md"), ("claude", "CLAUDE.md")):
            target = self.source / agent
            (target / "skills" / "demo").mkdir(parents=True)
            (target / name).write_text(
                "# Shared preferences\nHome: {{HOME}}\nCodex: {{CODEX_HOME}}\nClaude: {{CLAUDE_HOME}}\n",
                encoding="utf-8",
            )
            (target / "skills" / "demo" / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Fixture skill\n---\nUse {{HOME}}.\n",
                encoding="utf-8",
            )

    def run_install(self, *args, system="Linux"):
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        with patch.object(install.platform, "system", return_value=system):
            with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
                return install.main(["--home", str(self.home), "--source", str(self.source), *args])

    def tree(self, path):
        if not path.exists():
            return None
        return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}

    def assert_success(self, code):
        self.assertEqual(code, 0, self.stderr.getvalue())

    def test_fresh_install_all_supported_operating_systems(self):
        for system in ("Linux", "Darwin", "Windows"):
            with self.subTest(system=system):
                self.home = self.root / system / "home with spaces"
                self.assert_success(self.run_install(system=system))
                for agent, name in (("codex", "AGENTS.md"), ("claude", "CLAUDE.md")):
                    config = self.home / ("." + agent)
                    content = (config / name).read_text(encoding="utf-8")
                    self.assertEqual(content.count(START), 1)
                    self.assertEqual(content.count(END), 1)
                    self.assertIn(self.home.as_posix(), content)
                    self.assertNotIn("{{", content)
                    skill = (config / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
                    self.assertIn(self.home.as_posix(), skill)

    def test_unsupported_os_fails_without_writes(self):
        self.assertNotEqual(self.run_install(system="Plan9"), 0)
        self.assertFalse(self.home.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction integration test")
    def test_windows_junction_is_rejected_without_external_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        config = self.home / ".codex"
        config.mkdir(parents=True)
        junction = config / "skills"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                       check=True, capture_output=True, text=True)
        try:
            self.assertNotEqual(self.run_install(system="Windows"), 0)
            self.assertIn("Reparse point", self.stderr.getvalue())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((config / "AGENTS.md").exists())
        finally:
            junction.rmdir()

    def test_idempotence_does_not_create_new_backups(self):
        self.assert_success(self.run_install())
        before = self.tree(self.home)
        self.assert_success(self.run_install())
        self.assertEqual(self.tree(self.home), before)

    def test_custom_instructions_and_unrelated_skills_preserved(self):
        config = self.home / ".codex"
        (config / "skills" / "personal").mkdir(parents=True)
        custom = "# My preferences\nKeep this exact text.\n"
        (config / "AGENTS.md").write_text(custom, encoding="utf-8")
        (config / "skills" / "personal" / "SKILL.md").write_text("Personal", encoding="utf-8")
        self.assert_success(self.run_install())
        content = (config / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(custom, content)
        self.assertEqual((config / "skills" / "personal" / "SKILL.md").read_text(), "Personal")
        backups = list((config / ".hybrid-setup-backups").rglob("AGENTS.md"))
        self.assertTrue(backups)
        self.assertIn(custom, [p.read_text(encoding="utf-8") for p in backups])

    def test_managed_block_update_preserves_surrounding_content(self):
        config = self.home / ".codex"
        config.mkdir(parents=True)
        (config / "AGENTS.md").write_text(
            "Before\n" + START + "\nOld managed content\n" + END + "\nAfter\n", encoding="utf-8"
        )
        self.assert_success(self.run_install())
        content = (config / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(content.startswith("Before\n"))
        self.assertTrue(content.endswith("After\n"))
        self.assertNotIn("Old managed content", content)
        self.assertEqual(content.count(START), 1)

    def test_conflicting_skill_aborts_both_agents_then_force_backs_up(self):
        skill = self.home / ".claude" / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("Existing local skill", encoding="utf-8")
        before = self.tree(self.home)
        self.assertNotEqual(self.run_install(), 0)
        self.assertEqual(self.tree(self.home), before)
        self.assertFalse((self.home / ".codex").exists())
        self.assert_success(self.run_install("--force"))
        backups = list((self.home / ".claude" / ".hybrid-setup-backups").rglob("SKILL.md"))
        self.assertIn("Existing local skill", [p.read_text(encoding="utf-8") for p in backups])

    def test_cli_paths_override_environment_with_spaces(self):
        os.environ["CODEX_HOME"] = str(self.root / "unused codex")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root / "unused claude")
        codex, claude = self.root / "custom codex", self.root / "custom claude"
        self.assert_success(self.run_install("--codex-home", str(codex), "--claude-home", str(claude)))
        self.assertTrue((codex / "AGENTS.md").is_file())
        self.assertTrue((claude / "CLAUDE.md").is_file())
        self.assertIn(codex.as_posix(), (claude / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertFalse((self.root / "unused codex").exists())
        self.assertFalse((self.root / "unused claude").exists())
        self.assertFalse(self.home.exists())

    def test_environment_paths_used_without_cli_overrides(self):
        codex, claude = self.root / "env codex", self.root / "env claude"
        os.environ.update(CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(claude))
        self.assert_success(self.run_install())
        self.assertTrue((codex / "AGENTS.md").exists())
        self.assertTrue((claude / "CLAUDE.md").exists())
        self.assertFalse(self.home.exists())

    def test_dry_run_has_zero_mutations(self):
        before = self.tree(self.root)
        self.assert_success(self.run_install("--dry-run"))
        self.assertEqual(self.tree(self.root), before)

    def test_missing_source_file_aborts_before_writes(self):
        (self.source / "claude" / "CLAUDE.md").unlink()
        self.assertNotEqual(self.run_install(), 0)
        self.assertFalse(self.home.exists())

    def test_broken_existing_managed_markers_rejected(self):
        config = self.home / ".claude"
        config.mkdir(parents=True)
        for content in (START + "\nunfinished", END, START + START + END + END):
            with self.subTest(content=content):
                (config / "CLAUDE.md").write_text(content, encoding="utf-8")
                before = self.tree(self.home)
                self.assertNotEqual(self.run_install(), 0)
                self.assertEqual(self.tree(self.home), before)

    def test_same_target_paths_rejected(self):
        target = self.root / "same target"
        self.assertNotEqual(self.run_install("--codex-home", str(target), "--claude-home", str(target)), 0)
        self.assertFalse(target.exists())

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Symlink creation unavailable: " + str(exc))

    def test_source_symlink_rejected(self):
        outside = self.root / "outside.md"
        outside.write_text("Do not follow", encoding="utf-8")
        self.make_symlink(self.source / "codex" / "skills" / "demo" / "linked.md", outside)
        self.assertNotEqual(self.run_install(), 0)
        self.assertFalse(self.home.exists())

    def test_destination_symlink_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.home.mkdir()
        self.make_symlink(self.home / ".codex", outside, directory=True)
        self.assertNotEqual(self.run_install(), 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.home / ".claude").exists())

    def test_cli_subprocess_is_isolated_from_real_home(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "install.py"),
             "--home", str(self.home), "--source", str(self.source)],
            env=dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home)),
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".codex" / "AGENTS.md").exists())
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").exists())

    def test_default_home_discovery_without_override(self):
        with patch.object(install.Path, "home", return_value=self.home):
            with contextlib.redirect_stdout(io.StringIO()):
                code = install.main(["--source", str(self.source)])
        self.assertEqual(code, 0)
        self.assertTrue((self.home / ".codex" / "AGENTS.md").is_file())
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").is_file())

    def test_relative_overrides_rejected(self):
        for option in ("--home", "--source", "--codex-home", "--claude-home"):
            with self.subTest(option=option):
                self.assertNotEqual(self.run_install(option, "relative/path"), 0)
                self.assertFalse(self.home.exists())

    def test_nested_targets_and_source_overlap_rejected(self):
        for codex, claude in ((self.root / "config", self.root / "config" / "claude"),
                              (self.source / "codex", self.root / "claude")):
            before = self.tree(self.root)
            self.assertNotEqual(self.run_install("--codex-home", str(codex),
                                                "--claude-home", str(claude)), 0)
            self.assertEqual(before, self.tree(self.root))

    def test_non_directory_destination_parent_rejected(self):
        self.home.mkdir()
        (self.home / ".codex").write_text("not a directory")
        before = self.tree(self.home)
        self.assertNotEqual(self.run_install(), 0)
        self.assertEqual(before, self.tree(self.home))

    def test_invalid_source_markers_and_encoding_rejected(self):
        source_file = self.source / "codex" / "AGENTS.md"
        for value in (START.encode(), b"\xff\xfe"):
            source_file.write_bytes(value)
            self.assertNotEqual(self.run_install(), 0)
            self.assertFalse(self.home.exists())

    def test_reverse_existing_markers_rejected(self):
        config = self.home / ".codex"
        config.mkdir(parents=True)
        (config / "AGENTS.md").write_text(END + "\n" + START)
        before = self.tree(self.home)
        self.assertNotEqual(self.run_install(), 0)
        self.assertEqual(before, self.tree(self.home))

    def test_dry_run_force_keeps_conflicts_untouched(self):
        skill = self.home / ".codex" / "skills" / "demo" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b"local content")
        before = self.tree(self.root)
        self.assert_success(self.run_install("--dry-run", "--force"))
        self.assertEqual(before, self.tree(self.root))

    def test_identical_unmanaged_guidance_adopted_without_duplication(self):
        self.assert_success(self.run_install())
        doc = self.home / ".codex" / "AGENTS.md"
        text = doc.read_text(encoding="utf-8")
        doc.write_text(text.replace(START + "\n", "").replace(END + "\n", ""), encoding="utf-8")
        self.assert_success(self.run_install())
        self.assertEqual(doc.read_text(encoding="utf-8").count("# Shared preferences"), 1)

    def test_binary_files_preserved_and_nested_templates_rendered(self):
        skill = self.source / "codex" / "skills" / "demo"
        (skill / "assets").mkdir()
        (skill / "assets" / "data.bin").write_bytes(b"\x00\xff{{HOME}}")
        (skill / "agents").mkdir()
        (skill / "agents" / "openai.yaml").write_text('path: "{{CODEX_HOME}}"\n')
        self.assert_success(self.run_install())
        target = self.home / ".codex" / "skills" / "demo"
        self.assertEqual((target / "assets" / "data.bin").read_bytes(), b"\x00\xff{{HOME}}")
        self.assertIn((self.home / ".codex").as_posix(), (target / "agents" / "openai.yaml").read_text())

    def test_backup_failure_leaves_originals_untouched(self):
        self.assert_success(self.run_install())
        original = self.tree(self.home)
        (self.source / "codex" / "AGENTS.md").write_text("changed template")
        with patch.object(install, "atomic_write", side_effect=OSError("disk full")):
            self.assertNotEqual(self.run_install(), 0)
        self.assertEqual(original, self.tree(self.home))

    def test_post_plan_change_is_not_overwritten(self):
        self.assert_success(self.run_install())
        (self.source / "codex" / "AGENTS.md").write_text("updated")
        args = install.parser().parse_args(["--home", str(self.home), "--source", str(self.source)])
        changes = install.plan(args)
        document = self.home / ".codex" / "AGENTS.md"
        document.write_bytes(b"concurrent edit")
        with self.assertRaises(ValueError):
            install.apply(changes)
        self.assertEqual(document.read_bytes(), b"concurrent edit")

    def test_real_payload_installs_and_reinstalls_in_sandbox(self):
        self.source = REPO / "config"
        self.assert_success(self.run_install())
        snapshot = self.tree(self.home)
        self.assert_success(self.run_install())
        self.assertEqual(snapshot, self.tree(self.home))
        for tool, count in (("codex", 6), ("claude", 7)):
            root = self.home / ("." + tool)
            self.assertEqual(len(list((root / "skills").glob("*/SKILL.md"))), count)
            for file in root.rglob("*.md"):
                self.assertNotIn("{{CODEX_HOME}}", file.read_text(encoding="utf-8"))
                self.assertNotIn("{{CLAUDE_HOME}}", file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
