# Hybrid Setup

Personal configuration for using Codex and Claude Code together. Each assistant can delegate implementation or review to the other, collect lessons from those calls, and run explicitly requested PR workflows.

The repository contains portable copies of the global instruction files and the integration skills developed for this setup. It does not contain credentials, login sessions, conversation history, machine-specific MCP servers, `config.toml`, or Claude `settings.json`.

## Included configuration

| Configuration | Purpose |
| --- | --- |
| `config/codex/AGENTS.md` | Communication, implementation, commit/PR preferences and Claude model routing |
| `config/claude/CLAUDE.md` | Equivalent Claude preferences and Codex delegation workflows |
| Codex: `claude-implementation`, `claude-review` | Invoke Claude programmatically and inspect its results |
| Claude: `codex-implementation`, `codex-review`, `codex-computer-use` | Invoke Codex for implementation, review or runtime inspection |
| `learn-to-use-claude`, `learn-to-use-codex` | Analyze invocation evidence and propose improvements for explicit approval |
| `review-loop` | User-requested paired reviews, finding consolidation and authorized fixes |
| `babysit-pr` | Poll specific PRs and validate comments/CI failures before fixing them |
| `should-i-merge` | Independent Astra/Fable assessment of whether a PR should be merged |

These are personal defaults: pt-BR conversation, English code/documentation, draft PRs assigned to `AnaBerg`, atomic commits, merge rather than rebase, and no force-push. Review-loop remains opt-in. Read and customize the templates before installing them for another user. Model names reflect the source configuration and must be available in your account.

## Install on a new machine

Requirements: Python 3.10 or newer. Install Codex CLI, Claude Code and Git separately; GitHub workflows also require authenticated `gh`. This installer does not install CLIs or sign in to services.

Clone this repository, then run from its root:

```sh
# Linux or macOS
python3 scripts/install.py --dry-run
python3 scripts/install.py
```

```powershell
# Windows (PowerShell)
py -3 scripts/install.py --dry-run
py -3 scripts/install.py
```

The installer detects Linux, macOS or Windows. For each application it selects the destination in this order:

| Application | Explicit argument | Environment variable | Default |
| --- | --- | --- | --- |
| Codex | `--codex-home` | `CODEX_HOME` | `<user home>/.codex` |
| Claude | `--claude-home` | `CLAUDE_CONFIG_DIR` | `<user home>/.claude` |

Existing directories at the selected locations are reused; missing ones are created. It does not scan unrelated disks or profiles. `--home` changes the fallback user home, useful for staging and testing; environment variables still take precedence unless explicit application paths are supplied. Overrides must be absolute.

```sh
python3 scripts/install.py --codex-home /custom/codex --claude-home /custom/claude
```

```powershell
py -3 scripts/install.py --codex-home 'D:\Assistant Config\codex' --claude-home 'D:\Assistant Config\claude'
```

Skills are installed under each selected directory's `skills/`. Instruction templates are inserted into `AGENTS.md` and `CLAUDE.md` between `<!-- hybrid-setup:start -->` and `<!-- hybrid-setup:end -->`. Text outside that block is preserved. Later installs replace only that managed block and leave unrelated skills in place. Existing personal instructions outside the block may conflict with these defaults; inspect the resulting file when adopting the configuration.

`{{CODEX_HOME}}`, `{{CLAUDE_HOME}}` and `{{HOME}}` placeholders in text templates are rendered to the selected machine's paths. They are template syntax, not environment variables; install the templates rather than copying them manually.

The installer plans changes before writing. `--dry-run` creates no files or backups. Identical files are skipped. Differing existing skill files stop the install unless you pass `--force`; changed files are backed up beneath the corresponding application's `.hybrid-setup-backups/` directory. `--force` permits replacement of colliding skill files, not deletion of unrelated content. Writes are atomic per file; a filesystem failure can still interrupt a multi-file installation, so backups are retained.

```sh
python3 scripts/install.py --dry-run --force
python3 scripts/install.py --force
```

To undo an update, restore the relevant files from the printed backup location. To remove the managed preferences, delete their delimited block; remove only this repository's installed skill directories if you also want to uninstall the skills. Newly created files have no previous version to restore.

## Platform support

The installer and its tests run on native Linux, macOS and Windows. The bundled skills preserve the existing integration behavior: several examples use Bash, and the invocation lifecycle helpers use POSIX process groups and `ps`. Their runtime requires a POSIX environment; use WSL with the CLIs and configuration installed inside WSL for those workflows on Windows. Installing the files on native Windows does not make these helpers PowerShell-native. Paths alone do not translate shell commands between operating systems.

## Tests and CI

Run the installer tests locally without network access or real home-directory writes:

```sh
python3 -m unittest discover -s tests -v
```

On Windows, use `py -3 -m unittest discover -s tests -v`. GitHub Actions runs the suite on Ubuntu, macOS and Windows with Python 3.10 and 3.13 for pushes, pull requests and manual dispatches. Tests cover destination discovery, fresh/existing installations, managed instructions, idempotence, backups, conflict handling, dry-run and invalid paths. They test the installer rather than executing paid model calls or PR workflows.

## Updating the configuration

Edit `config/codex` and `config/claude`, review the diff and rerun the installer tests. Run a dry-run on your machine, then reinstall with `--force` when replacing previously installed skill versions. Changes made directly to your live configuration are not automatically synchronized back into this repository.
