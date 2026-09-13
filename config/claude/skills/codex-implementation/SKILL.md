---
name: codex-implementation
description: Ask Codex CLI (gpt-5.6) to implement code changes in the local workspace. This is how gpt-5.6 is invoked for implementation work. Use when the user asks Claude to have Codex or gpt-5.6 build a feature, fix a bug, refactor code, update tests, apply a concrete plan, or make edits where an independent implementation agent would be useful. Prefer Claude's normal implementation flow for small edits Claude can make directly.
---

# Codex Implementation

Use Codex as a separate local implementation agent when the task is broad, benefits from another model's coding pass, or the user explicitly asks Claude to have Codex implement something.

Do not delegate implementation just to avoid understanding the code yourself. Claude remains responsible for the final answer: inspect Codex's changes, run relevant verification when practical, and report what actually changed.

## Workflow

1. Clarify the implementation target: feature, bugfix, refactor, test update, migration, or plan step
2. Inspect enough local context to give Codex a precise task and avoid sending it on a broad search
3. Inspect the worktree state so user changes are not mistaken for Codex changes
4. Create the artifact directory, write the focused prompt, assign and register the invocation ID, and atomically initialize `invocation.json` using the shared lifecycle schema before authentication preflight
5. Record the preflight outcome; dispatch only when it passes, using the supervised command below
6. Confirm invocation-owned descendants have stopped, then capture the resulting worktree state, inspect the changes and report, perform independent verification, and record the outcomes
7. After verification, claim and finish the learning hook from existing evidence, then present the implementation result

Read the [Codex invocation lifecycle](../learn-to-use-codex/references/invocation-lifecycle.md) before preparing the invocation. Use the repository root as the working directory for all captures and execution below. Start with these artifact paths:

```bash
ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-implementation.XXXXXX")"
REPORT="$ARTIFACT_DIR/report.md"
PROMPT="$ARTIFACT_DIR/prompt.md"
LIFECYCLE="{{CLAUDE_HOME}}/skills/learn-to-use-codex/scripts/invocation_lifecycle.py"
```

Verify that the resolved artifact directory is outside the editable repository and any additional delegated write roots; choose another location if needed. Keep snapshots outside the delegated edit scope and exclude artifact paths from the implementation prompt's allowed files.

Write the prompt and initialize the registered invocation record with all lifecycle fields, using `null` for unavailable values. Record `sandbox_mode: workspace-write` and `approval_policy: never` explicitly: this unattended workflow must permit scoped edits without waiting for interactive approval. Run and record authentication and capability preflight only after that record exists. If these policies are unavailable or an operation is denied, record the blocker and return to the caller; never disable the sandbox or silently widen permissions. A blocked preflight skips execution, records verification limitations, and proceeds to the learning hook without claiming implementation success.

After a passing preflight, capture the baseline before dispatch:

```bash
git status --short > "$ARTIFACT_DIR/status-before.txt"
git diff --binary --no-ext-diff --no-textconv > "$ARTIFACT_DIR/diff-before.patch"
git diff --cached --binary --no-ext-diff --no-textconv > "$ARTIFACT_DIR/staged-before.patch"
git ls-files --others -z > "$ARTIFACT_DIR/untracked-before.paths"
if git rev-parse --verify HEAD > "$ARTIFACT_DIR/head-before.txt" 2>/dev/null; then
  :
else
  printf '%s\n' UNBORN > "$ARTIFACT_DIR/head-before.txt"
fi
```

Before continuing, snapshot the actual contents of every preexisting untracked file Codex could modify, including files outside the requested target and ignored files within its writable scope. Consume `untracked-before.paths` as NUL-delimited paths, never newline-delimited text or shell word splitting. Preserve relative paths, bytes, file type, permissions and symlink targets without following symlinks; record content hashes and relevant metadata in a manifest outside the editable workspace. Verify the copies against the originals before dispatch. A filename list or ordinary Git diff is not a content backup. If files are changing concurrently, cannot be copied, or cannot be safely enumerated, stop before launching and resolve the incomplete baseline with the caller.

`UNBORN` is valid only for a repository with no initial commit; investigate any other HEAD lookup failure before dispatch. Choose `TIMEOUT_SECONDS` and `TIMEOUT_RATIONALE` for the task, then use this single execution path. The conditional preserves the supervisor status and permits cleanup handling under `set -e`:

```bash
SUPERVISOR_EXIT=0
if python3 "$LIFECYCLE" supervise \
  --artifact-dir "$ARTIFACT_DIR" --stdin "$PROMPT" \
  --timeout "$TIMEOUT_SECONDS" --timeout-rationale "$TIMEOUT_RATIONALE" \
  -- codex --ask-for-approval never exec --sandbox workspace-write -C "$PWD" -; then
  SUPERVISOR_EXIT=0
else
  SUPERVISOR_EXIT=$?
fi
```

Before capturing final after-state, inspect the supervisor's process and cleanup records. Use the shared lifecycle contract to identify invocation-owned descendants and servers by PID and matching start identity, stop only those attributable processes within the cleanup deadline, and verify they are gone. Never use a broad process-name kill. The helper's process-group result alone does not prove detached descendants have stopped; record the caller's cleanup outcome and supporting evidence in `invocation.json`.

If ownership or cleanup remains unresolved, preserve stdout/stderr and partial changes, label any captured state as provisional, record the verification limitation, and report it through the learning hook and task result. Do not mark the implementation finally verified or treat that capture as a stable final snapshot. Capture final status, patches, HEAD, and untracked paths below only after cleanup is confirmed:

```bash
git status --short > "$ARTIFACT_DIR/status-after.txt"
git diff --binary --no-ext-diff --no-textconv > "$ARTIFACT_DIR/diff-after.patch"
git diff --cached --binary --no-ext-diff --no-textconv > "$ARTIFACT_DIR/staged-after.patch"
git ls-files --others -z > "$ARTIFACT_DIR/untracked-after.paths"
if git rev-parse --verify HEAD > "$ARTIFACT_DIR/head-after.txt" 2>/dev/null; then
  :
else
  printf '%s\n' UNBORN > "$ARTIFACT_DIR/head-after.txt"
fi
```

Inspect `SUPERVISOR_EXIT`, the captured before/after state, and the supervisor's `invocation.json`, `stdout.log`, `stderr.log`, and candidate `report.md`. Compare every snapshotted untracked path against its original content hash and metadata, even if it is now tracked, staged or deleted, and inspect newly created files separately. When a mismatch affects unrelated user work, preserve both versions and investigate ownership; do not blindly restore over concurrent user changes.

The tracked patches include binary bytes and bypass external diff/text conversion. To verify recovery, use a separate disposable checkout at the recorded baseline HEAD, apply the staged patch with `git apply --index` first, then apply the worktree patch with `git apply`, and compare both index and worktree contents. Reconstruct an unborn baseline in an empty temporary repository. Never replay these patches over the live workspace or concurrent user changes automatically.

Compare the recorded HEAD values as well as the working-tree diffs. If both are commits and differ, inspect `git log <before>..<after>` and the corresponding commit/tree diffs: a clean worktree may hide changes Codex committed. If the baseline was `UNBORN`, inspect the new history including its root commit and the full resulting tree. Treat rewritten or unexpected history as a discrepancy and inspect it without resetting or force-pushing.

Perform the independent checks below and persist `report_validity`, `verification_status`, and `verification_summary` before claiming the learning hook. If execution failed, inspect any partial changes and record what could and could not be verified.

If the implementation should be constrained to specific files, say that explicitly in the prompt. If the repository has user changes already, tell Codex not to overwrite or revert unrelated work.

## Implementation prompt

Ask Codex to act as a local implementation agent:

```text
You are implementing a code change in this local repository.

Goal:
<state the requested feature, bugfix, refactor, or plan step>

Context:
- Working directory: <repo path>
- Relevant files: <files Claude already identified>
- Existing user changes to preserve: <known dirty files or "none observed">
- Constraints: <style, architecture, APIs, compatibility, no unrelated refactors>
- Verification to run: <tests, lint, typecheck, manual checks, or "choose targeted checks">

Tasks:
1. Inspect the relevant code before editing.
2. Make the smallest coherent implementation that satisfies the goal.
3. Add or update tests when the risk or behavior change warrants it.
4. Run targeted verification if available.
5. Report the files changed, verification results, and any remaining risks or blockers.

Do not revert unrelated user changes. Do not make broad refactors unless required for the goal. If requirements are ambiguous, make a conservative assumption and state it in the report.
```

Add task-specific context when useful: acceptance criteria, failing test output, issue text, design constraints, API contracts, migration rules, or a written implementation plan.

## Reviewing Codex changes

After Codex runs, Claude must inspect the resulting diff before responding to the user.

Check for:

- unrelated edits or reverted user work
- behavior that does not match the user's request
- missing tests for meaningful behavior changes
- lint, type, formatting, or build problems
- risky assumptions Codex made without evidence

Run targeted verification whenever practical. If verification fails, either fix the issue directly or report the failure clearly with the relevant output. Do not claim the implementation is complete based only on Codex's report.

## Invocation lifecycle and learning hook

Every intended Codex invocation from this skill — success, error, timeout, cancellation, empty or malformed report, cleanup failure, or a preflight that blocked the call — uses the shared lifecycle contract and requires exactly one `$learn-to-use-codex` analysis.

The workflow above is the only model execution path in this skill. After its independent verification step (or after recording a blocked preflight and its verification limitations), use the same artifact directory for the learning hook:

```bash
HOOK_TOKEN="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
python3 "$LIFECYCLE" hook-claim --artifact-dir "$ARTIFACT_DIR" --deadline <iso8601> \
  --owner-pid "$$" --claim-token "$HOOK_TOKEN"
# Continue only on exit 0. Keep this owner shell alive through completion.
# Exit 2 means busy, 3 means already complete, and 4 means invalid completion.
# ... run $learn-to-use-codex from existing evidence, writing learning-result.md ...
python3 "$LIFECYCLE" hook-complete --artifact-dir "$ARTIFACT_DIR" \
  --result "$ARTIFACT_DIR/learning-result.md" --outcome <class> --claim-token "$HOOK_TOKEN"
```

Each retry is a new linked `invocation_id`. Run the hook exactly once per intended invocation, after independent verification. Keep learning output in `learning-result.md`, never appended to the Codex report, and never invoke Codex, another model, a subagent, or `review-loop` to perform the analysis. Hook failure is recorded separately and never changes the Codex result or this skill's reported outcome. Pending learning proposals do not block the original task and are applied only after explicit user approval of the specific proposal.

## Reporting back

In the user-facing response, include:

- what Codex was asked to implement
- what files or behavior changed after Claude inspected the diff
- what verification ran and whether it passed
- any failures, blockers, assumptions, or follow-up work

If Codex made unsuitable changes, say so and either correct them or leave them out of the reported result. If `codex` is not installed or the command fails, report the error and continue with Claude's normal implementation process when feasible.
