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
3. Check the worktree state before running Codex so user changes are not mistaken for Codex changes
4. Create a temporary artifact directory for the prompt and implementation report
5. Run Codex with a focused implementation prompt that states allowed files, constraints, expected tests, and reporting requirements
6. After Codex finishes, inspect the diff and run targeted verification before presenting the result

Use this command shape:

```bash
ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-implementation.XXXXXX")"
REPORT="$ARTIFACT_DIR/report.md"
PROMPT="$ARTIFACT_DIR/prompt.md"

git status --short > "$ARTIFACT_DIR/status-before.txt"
codex -C "$PWD" - < "$PROMPT" > "$REPORT"
git status --short > "$ARTIFACT_DIR/status-after.txt"
git diff > "$ARTIFACT_DIR/diff-after.patch"
```

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

Read and follow the canonical [Codex invocation lifecycle](../learn-to-use-codex/references/invocation-lifecycle.md). Before authentication preflight, assign a stable `invocation_id` and create its `invocation.json` record in the artifact directory. Use `../learn-to-use-codex/scripts/invocation_lifecycle.py` to supervise the process, record the outcome atomically, confirm process-tree cleanup, and manage the hook ownership markers:

```bash
LIFECYCLE="{{CLAUDE_HOME}}/skills/learn-to-use-codex/scripts/invocation_lifecycle.py"

python3 "$LIFECYCLE" supervise \
  --artifact-dir "$ARTIFACT_DIR" --stdin "$PROMPT" \
  --timeout <bounded seconds> --timeout-rationale "<task-sized reason>" \
  -- codex -C "$PWD" - < "$PROMPT"

python3 "$LIFECYCLE" hook-claim --artifact-dir "$ARTIFACT_DIR" --deadline <iso8601>
# ... run $learn-to-use-codex from existing evidence, writing learning-result.md ...
python3 "$LIFECYCLE" hook-complete --artifact-dir "$ARTIFACT_DIR" \
  --result "$ARTIFACT_DIR/learning-result.md" --outcome <class>
```

Each retry is a new linked `invocation_id`. Run the hook exactly once per intended invocation, after independent verification. Keep learning output in `learning-result.md`, never appended to the Codex report, and never invoke Codex, another model, a subagent, or `review-loop` to perform the analysis. Hook failure is recorded separately and never changes the Codex result or this skill's reported outcome. Pending learning proposals do not block the original task and are applied only after explicit user approval of the specific proposal.

## Reporting back

In the user-facing response, include:

- what Codex was asked to implement
- what files or behavior changed after Claude inspected the diff
- what verification ran and whether it passed
- any failures, blockers, assumptions, or follow-up work

If Codex made unsuitable changes, say so and either correct them or leave them out of the reported result. If `codex` is not installed or the command fails, report the error and continue with Claude's normal implementation process when feasible.
