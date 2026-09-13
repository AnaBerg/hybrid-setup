---
name: claude-review
description: Invoke Claude Code CLI as a read-only independent reviewer of plans, requirements, uncommitted changes, branch diffs, commits, or implementations. Use when the user explicitly asks Codex to have Claude, Fable, Opus, or Sonnet review work; when a plan or implementation needs a taste-heavy second opinion; or when Codex wants an independent audit for bugs, regressions, missing tests, security issues, or requirement mismatches.
---

# Claude Review

Use Claude as a second opinion. Keep the run read-only and verify every important finding against the source before presenting it.

## Select the model

- Default to `fable` with `high` effort for plan and implementation review.
- Use `opus` with `high` effort when UI/UX, API design, copy, or product taste dominates.
- Use `sonnet` with `medium` effort only for bounded, lower-risk reviews.
- Never use Haiku. Use only `low`, `medium`, or `high` effort.

## Invocation lifecycle and learning hook

Read and follow the canonical [Claude invocation lifecycle](../learn-to-use-claude/references/invocation-lifecycle.md). Before authentication preflight, reuse the parent's `invocation_id` or assign a stable unique ID if none was supplied. Create and register the per-invocation artifact directory and atomic `invocation.json` immediately. Set `retry_owner` to the direct caller or parent workflow. Each permitted retry is a separate invocation with a new linked ID.

Claude execution, report materialization/validation, cleanup, and the learning hook use separate bounded deadlines under the canonical contract. For a direct call, validate the report and run `$learn-to-use-claude` afterward. Under `review-loop`, return the raw report and evidence; the parent owns final validation and hook finalization. Include empty/malformed output, cancellation, and preflight-blocked intended calls. Return the `.done` marker path and proposals separately from the review report. Hook failure does not alter review success, paired coverage, retry count, or mergeability and never triggers a reviewer retry.

## Authentication preflight

Run `claude auth status` after creating the invocation record and before preparing a paid invocation. Apply the canonical preflight classification and retry mapping. Continue only when it reports `"loggedIn": true`. Ask the user to run `claude auth login` only for `not_logged_in`; otherwise report the classified blocker. Do not start an interactive login on the user's behalf. Record the outcome and complete or hand off the learning hook before returning.

## Prepare the review target

Identify one target: a plan file, requirements, uncommitted changes, a base branch comparison, a commit, or named files. Capture any Git diff with Codex before invoking Claude so Claude does not need shell access. Include untracked file names separately and allow Claude to read only relevant workspace files.

Use the per-invocation artifact directory created before preflight for the prompt, review snapshot, separate stdout/stderr, report, process metadata, and hook markers. Add that directory explicitly because it is outside the repository.

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

git status --short > "$TASK_ARTIFACT_DIR/status.txt"
git diff --binary > "$TASK_ARTIFACT_DIR/unstaged.diff"
git diff --cached --binary > "$TASK_ARTIFACT_DIR/staged.diff"

python3 "$CLAUDE_LIFECYCLE" supervise \
  --artifact-dir "$TASK_ARTIFACT_DIR" \
  --stdin "$TASK_PROMPT" \
  --timeout "$CLAUDE_TIMEOUT_SECONDS" \
  --timeout-rationale "<model, effort, target-size, and tool-use basis>" \
  -- claude -p \
  --model "$CLAUDE_MODEL" \
  --effort "$CLAUDE_EFFORT" \
  --permission-mode plan \
  --tools "Read,Grep,Glob" \
  --add-dir "$TASK_ARTIFACT_DIR" \
  --output-format text \
  --no-session-persistence
```

The lifecycle helper provides platform-appropriate process-group supervision and writes raw stdout, stderr, exit/signal, and cleanup evidence before deriving `report.md`. Keep separate report-validation and hook deadlines in the record. Track caller-owned or safely attributable detached servers separately. Do not rely only on the surrounding tool timeout. Only the declared retry owner may approve a bounded timeout adjustment from failure evidence.

For branch or commit reviews, replace the snapshot commands with the appropriate `git diff <base>...HEAD` or `git show <sha>` output. For plan reviews, point the prompt directly at the plan and requirements. Do not enable Bash, Edit, Write, NotebookEdit, or computer-use tools for a read-only review.

## Review prompt

```text
Act as an independent reviewer. Do not edit files.

Invocation provenance:
<copy the canonical provenance and result field names from the lifecycle reference without translation>

Review target:
<plan, requirements, diff snapshot, commit snapshot, or file list>

Context and requirements:
<task-specific constraints and expected behavior>

Prioritize substantive findings over summary. For every finding include:
- severity
- file and line, plan section, or diff hunk
- concrete failure mode or unmet requirement
- concise fix direction

Check for correctness, regressions, missing tests, security issues, edge cases, scope drift, and requirement mismatches. For plans, also check completeness, sequencing, validation, rollback risk, and unsupported assumptions. If there are no substantive findings, say so and identify residual test or evidence gaps.
```

## Validate findings

Read the report and inspect each cited location. Remove false positives, distinguish confirmed findings from unverified suggestions, and do not relay a claim merely because Claude made it. The declared `retry_owner` alone decides whether to retry. When a parent workflow owns retries, return an incomplete attempt without rerunning or changing models. For a direct call owned by this skill, an incomplete review may be rerun with better context or a stronger model, using a new linked invocation ID.

Lead the user-facing response with confirmed findings ordered by severity. If none remain, state that clearly and name the inspected target and residual gaps.
