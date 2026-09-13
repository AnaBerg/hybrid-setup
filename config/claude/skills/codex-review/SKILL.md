---
name: codex-review
description: Ask Codex CLI (gpt-5.6) for an independent code review of uncommitted changes, branch diff, a commit, or a specific implementation. This is how gpt-5.6 is invoked for review work. Use when the user asks Claude to have Codex or gpt-5.6 review work, when the mode-selection rubric calls for a gpt-5.6 review perspective, or when Codex should audit a diff, find bugs or regressions, or compare Claude's implementation against requirements. For a review by Claude itself, use the normal review process instead.
---

# Codex Review

Use Codex as an independent reviewer when the user wants a second-pass review or when a change is broad enough that another agent's perspective is useful.

Prefer Claude's normal review process for small local checks. Do not delegate review just to avoid reading the code yourself. Treat Codex's output as evidence, not authority.

## Workflow

1. Identify the review target: uncommitted changes, base branch, commit SHA, PR checkout, or specific files
2. Create a temporary artifact directory for the Codex report
3. Run `codex review` with a focused review prompt
4. Read Codex's report and verify important claims against the code before presenting them

Use one of these command shapes:

```bash
ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/code-review.XXXXXX")"
REPORT="$ARTIFACT_DIR/report.md"
PROMPT="$ARTIFACT_DIR/prompt.md"

# review staged, unstaged, and untracked changes
codex -C "$PWD" review --uncommitted - < "$PROMPT" > "$REPORT"

# review current branch against base branch
codex -C "$PWD" review --base main - < "$PROMPT" > "$REPORT"

# review a single commit
codex -C "$PWD" review --commit <sha> - < "$PROMPT" > "$REPORT"
```

## Review prompt

Ask Codex to use a code-review stance:

```text
Review these changes for bugs, regressions, missing tests, security issues, and requirement mismatches.

Prioritize findings over summary. For each finding include:
- severity
- file and line reference
- concrete failure mode
- suggested fix direction

DO NOT edit files. If there are no substantive findings, say so and name any residual test gaps.
```

Add task-specific context when useful: requirements, risky areas, expected behavior, relevant tests, or files Claude is unsure about.

## Invocation lifecycle and learning hook

Every intended Codex invocation from this skill — success, error, timeout, cancellation, empty or malformed report, cleanup failure, or a preflight that blocked the call — uses the shared lifecycle contract and requires exactly one `$learn-to-use-codex` analysis.

Read and follow the canonical [Codex invocation lifecycle](../learn-to-use-codex/references/invocation-lifecycle.md). Before authentication preflight, assign a stable `invocation_id` and create its `invocation.json` record in the artifact directory. Use `../learn-to-use-codex/scripts/invocation_lifecycle.py` to supervise the process, record the outcome atomically, confirm process-tree cleanup, and manage the hook ownership markers:

```bash
LIFECYCLE="{{CLAUDE_HOME}}/skills/learn-to-use-codex/scripts/invocation_lifecycle.py"

python3 "$LIFECYCLE" supervise \
  --artifact-dir "$ARTIFACT_DIR" --stdin "$PROMPT" \
  --timeout <bounded seconds> --timeout-rationale "<task-sized reason>" \
  -- codex -C "$PWD" review --uncommitted - < "$PROMPT"

python3 "$LIFECYCLE" hook-claim --artifact-dir "$ARTIFACT_DIR" --deadline <iso8601>
# ... run $learn-to-use-codex from existing evidence, writing learning-result.md ...
python3 "$LIFECYCLE" hook-complete --artifact-dir "$ARTIFACT_DIR" \
  --result "$ARTIFACT_DIR/learning-result.md" --outcome <class>
```

Each retry is a new linked `invocation_id`. Run the hook exactly once per intended invocation, after independent verification. Keep learning output in `learning-result.md`, never appended to the Codex report, and never invoke Codex, another model, a subagent, or `review-loop` to perform the analysis. Hook failure is recorded separately and never changes the Codex result or this skill's reported outcome. Pending learning proposals do not block the original task and are applied only after explicit user approval of the specific proposal.

## Reporting back

Before relaying a Codex finding, inspect the cited code or diff enough to decide whether the finding is real. In the user-facing response, separate confirmed issues from Codex suggestions you did not verify.

If Codex finds nothing, say that clearly and mention what review target it inspected.

If `codex` is not installed or the command fails, report the error and offer to review the changes directly instead.
