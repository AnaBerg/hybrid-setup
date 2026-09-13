---
name: codex-computer-use
description: Ask Codex CLI (gpt-5.6) to run local app verification that needs computer use, browser automation, simulators, screenshots, app launching, or independent runtime inspection. This is how gpt-5.6 is invoked for computer-use work. Use when the user asks Claude to test a flow, verify UI behavior, inspect a running app, capture screenshots, or report confirmation and feedback about implemented behavior that benefits from computer-use functionality.
---

# Codex Computer Use

Use Codex as a separate local verification agent when the task needs a real UI interaction, screenshots, simulator/browser/device state, or an independent runtime check outside Claude's current context.

Do not use this for ordinary code reading, typechecking, linting, or tests Claude can run directly. Launching apps, simulators, or browsers to verify the requested work is fine without asking; ask first only if the run could disrupt the user's environment beyond that (closing their apps, changing system settings, acting real accounts or data).

## Workflow

1. Identify the verification target: local URL, app command, simulator/device, specific user flow, screenshot expectation, or runtime behavior to inspect
2. Start any required dev server or app process yourself when Claude can do that directly
3. Create a temporary artifact directory for the Codex prompt, screenshots, and report
4. Run Codex with a focused computer-use prompt that tells it exactly what to launch, inspect, click, capture, and report
5. Read Codex's report and verify any important claim against local evidence when practical before presenting it to the user

Use this command shape:

```bash
ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codex-computer-use.XXXXXX")"
REPORT="$ARTIFACT_DIR/report.md"
PROMPT="$ARTIFACT_DIR/prompt.md"

codex -C "$PWD" - < "$PROMPT" > "$REPORT"
```

If screenshots, traces, or logs are important, instruct Codex to save them under `$ARTIFACT_DIR` and include the file paths in its report.

## Computer use prompt

Ask Codex to act as a local computer-use verification agent:

```text
You are verifying a local app or UI behavior with computer-use tools.

Goal:
<state the user-visible behavior or flow to verify>

Context:
- Working directory: <repo or project path>
- App/server command or existing URL: <command or URL>
- Relevant files or implementation notes: <short context>
- Artifact directory for screenshots/logs: <artifact dir>

Tasks:
1. Launch or open the target environment if needed.
2. Interact with the UI like a user would.
3. Capture screenshots or logs for meaningful states.
4. Check the requested behavior, including obvious edge cases.
5. Report what passed, what failed, and the concrete evidence.

Do not edit files unless explicitly asked. Do not make destructive changes, send real messages, purchase anything, or change real account data. If a step is blocked, explain the blocker and what you observed.
```

Add task-specific instructions when useful: viewport size, credentials to avoid, exact route, expected copy, selectors to inspect, simulator/device choice, or known fragile states.

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

Before relaying Codex's result, read the report and inspect any screenshot, log, or cited local artifact needed to understand the conclusion. Treat Codex's output as evidence from an independent run, not as final authority.

In the user-facing response, include:

- what Codex verified
- whether the requested behavior passed or failed
- the main evidence, including screenshot or log paths when useful
- any blocker, uncertainty, or unverified claim
- the local command or URL that was used, if it matters for reproduction

If Codex reports a failure, separate observed behavior from suggested fixes. If Codex cannot run because the CLI is unavailable, blocked, or missing computer-use capability, report the error and continue with whatever direct verification Claude can perform.
