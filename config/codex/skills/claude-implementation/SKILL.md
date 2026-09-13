---
name: claude-implementation
description: Invoke Claude Code CLI as an independent local implementation agent, then inspect and verify its changes. Use when the user explicitly asks Codex to have Claude, Fable, Opus, or Sonnet implement a feature, fix, refactor, migration, test update, or concrete plan; or when a broad implementation benefits from a separate taste-heavy or independent coding pass.
---

# Claude Implementation

Use Claude as a separate implementation agent while retaining responsibility for scope, review, and final verification.

## Select the model

- Use `fable` with `high` effort for complex reasoning, architecture, or judgment-heavy code.
- Use `opus` with `high` effort for user-facing UI, copy, API design, or work where taste is important.
- Use `sonnet` with `medium` effort for bounded, routine changes when speed matters.
- Never use Haiku. Use only `low`, `medium`, or `high` effort.
- Override the default when the first result misses the expected quality bar.

## Invocation lifecycle and learning hook

Read and follow the canonical [Claude invocation lifecycle](../learn-to-use-claude/references/invocation-lifecycle.md). Before authentication preflight, reuse the parent's `invocation_id` or assign a stable unique ID if none was supplied. Create and register the per-invocation artifact directory and atomic `invocation.json` immediately. Set `retry_owner` to the direct caller or parent workflow; this skill never retries or changes models when a parent owns retries. Each permitted retry is a separate invocation with a new linked ID.

For direct calls, run `$learn-to-use-claude` in a bounded finally-style step after success and independent verification, or failure and confirmed cleanup. For a parent workflow, return the record and artifacts so the parent can finish verification and the hook under the canonical marker/takeover protocol. Include empty/malformed output, cancellation, and preflight-blocked intended calls. Return the `.done` marker path and proposals separately from the implementation result. Hook failure does not change the Claude result or trigger a retry.

## Authentication preflight

Run `claude auth status` after creating the invocation record and before preparing a paid invocation. Apply the canonical preflight classification and retry mapping. Continue only when it reports `"loggedIn": true`. Ask the user to run `claude auth login` only for `not_logged_in`; otherwise report the classified blocker. Do not start an interactive login on the user's behalf. Record the outcome and complete or hand off the learning hook before returning.

## Workflow

1. Assign the invocation ID, create its artifact record, confirm Claude authentication, then inspect the repository and clarify the exact implementation target.
2. Check `git status --short` and identify user changes that Claude must preserve.
3. Prepare a self-contained prompt with the goal, relevant files, acceptance criteria, constraints, and verification commands.
4. Use the existing per-invocation artifact directory for the prompt, separate stdout/stderr, report, process metadata, and before/after state.
5. Run Claude non-interactively with edit permission, an explicit timeout, and recorded PID/process-tree metadata. On timeout or cancellation, terminate the complete process tree, confirm exit, and stop any server started by the caller for this invocation.
6. Inspect the resulting diff yourself and run targeted verification.
7. Correct unsuitable changes or report blockers clearly. Do not trust Claude's report without checking it.

Use this command shape:

```bash
INVOCATION_ID="<stable-id-assigned-before-preflight>"
TASK_ARTIFACT_DIR="<caller-supplied-per-invocation-directory>"
TASK_PROMPT="$TASK_ARTIFACT_DIR/prompt.md"
TASK_REPORT="$TASK_ARTIFACT_DIR/report.md"
TASK_STDERR="$TASK_ARTIFACT_DIR/stderr.log"
CLAUDE_MODEL="fable"
CLAUDE_EFFORT="high"
CLAUDE_TIMEOUT_SECONDS="<bounded-timeout>"
CLAUDE_LIFECYCLE="{{CODEX_HOME}}/skills/learn-to-use-claude/scripts/invocation_lifecycle.py"

git status --short > "$TASK_ARTIFACT_DIR/status-before.txt"
python3 "$CLAUDE_LIFECYCLE" supervise \
  --artifact-dir "$TASK_ARTIFACT_DIR" \
  --stdin "$TASK_PROMPT" \
  --timeout "$CLAUDE_TIMEOUT_SECONDS" \
  --timeout-rationale "<model, effort, target-size, and tool-use basis>" \
  -- claude -p \
  --model "$CLAUDE_MODEL" \
  --effort "$CLAUDE_EFFORT" \
  --permission-mode acceptEdits \
  --output-format text \
  --no-session-persistence
git status --short > "$TASK_ARTIFACT_DIR/status-after.txt"
git diff --binary > "$TASK_ARTIFACT_DIR/diff-after.patch"
```

The lifecycle helper provides platform-appropriate process-group supervision and writes raw stdout, stderr, exit/signal, and cleanup evidence before deriving `report.md`. Keep separate report-validation and hook deadlines in the record. Track caller-owned or safely attributable detached servers separately. Do not rely only on the surrounding tool timeout. Only the declared retry owner may approve a bounded timeout adjustment from failure evidence.

Do not use `--dangerously-skip-permissions`. If permissions block a required command, inspect the failure and either perform the command directly or report the blocker.

For multiple Claude implementation runs, use distinct named worktrees with `--worktree <name>` and merge only after independently reviewing each result. Never let parallel editing agents share a checkout.

## Prompt template

```text
You are implementing a code change in this local repository.

Goal:
<requested feature, fix, refactor, migration, or plan step>

Context:
- Working directory: <absolute repository path>
- Relevant files: <files already identified>
- Existing changes to preserve: <dirty files or "none observed">
- Constraints: <style, architecture, compatibility, scope boundaries>
- Acceptance criteria: <observable requirements>
- Verification: <tests, lint, typecheck, build, or targeted checks>

Tasks:
1. Inspect the relevant code before editing.
2. Make the smallest coherent implementation that satisfies the goal.
3. Add or update tests when warranted.
4. Run targeted verification.
5. Report files changed, verification results, assumptions, and remaining risks.

Do not revert unrelated changes, broaden scope, force-push, rebase, or make destructive changes. If ambiguity remains, choose the most conservative reasonable interpretation and state it.
```

## Report back

Report what Claude was asked to do, what actually changed after inspecting the diff, verification results, and any assumption, failure, or residual risk. Attribute claims to evidence rather than merely repeating Claude's report.
