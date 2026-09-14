# Personal preferences

## Communication

- Communicate with me in Brazilian Portuguese by default.
- I am fluent in English; do not simplify technical language unnecessarily.
- Write all code, code comments, documentation, commits, branches, and pull requests in English.
- Be direct and concise. Give only relevant information; I prefer asking for more detail over reading a wall of text.
- I don't need status update, only a brief description by the end of the execution is enough.
- I prefer reading bullet points than prose text.

## Implementation

- Perform implementation through subagents or workflows whenever they are available and allowed by the active environment.
- Parallelize independent work when practical.
- If a change is clearly justified and within scope, implement it without asking for unnecessary confirmation.
- Preserve existing user changes and inspect the worktree before delegating edits.

## Pull requests and commits

- Commit atomically.
- Open pull requests as drafts.
- Use merge, not rebase.
- Never force-push.
- Add UI screenshots to the pull request description when applicable.
- Assign new pull requests to `AnaBerg`.
- Make commit messages summarize what was implemented.
- When an implementation is complete and repository access permits it, open the pull request.
- Name branches `{action}/{summary}` where `action` is one of `feature`, `fix`, `refactor`, `chore`, or `hotfix`.

## Code style

- Prefer the simplest correct implementation.
- Optimize for readability.
- Keep code and explanations direct and concise.

## Work sizing and verification

- If a request is too large to execute reliably as one unit, stop and say so clearly.
- Use computer-use tooling when it materially improves completeness or validation.
- Verify delegated work independently before reporting completion.
- Run `$review-loop` only when I explicitly request or invoke it. Do not start it automatically after implementations, skill creation, fixes, verification, or before opening a pull request.

## Model routing

Rank models primarily by intelligence, then taste, then cost. Cost reflects my effective cost, not list price.

| model         | cost | intelligence | taste |
| ------------- | ---- | ------------ | ----- |
| gpt-6-astra   | 8    | 9            | 8     |
| gpt-5.6-sol   | 8    | 8            | 5     |
| gpt-5.6-terra | 9    | 7            | 4     |
| gpt-5.6-luna  | 10   | 3            | 2     |
| sonnet-5      | 5    | 5            | 7     |
| opus-5        | 6    | 8            | 9     |
| fable-5.1     | 2    | 9            | 9     |

- Treat these as defaults, not limits. If a cheaper model misses the quality bar, retry with a stronger model without asking.
- Use native Codex agents for bulk work such as clear-spec implementation, analysis, and migrations.
- Use a model with taste above 7 for user-facing UI, copy, API design, and similar judgment-heavy work.
- Prefer Fable or Astra for plan reviews and implementation reviews. Add an independent Codex review when useful.
- Never use Haiku.
- Invoke Claude programmatically through the `claude-implementation` and `claude-review` skills.
- Run exactly one `$learn-to-use-claude` analysis for every intended Claude/Fable/Opus/Sonnet model invocation, direct or through a wrapper/review-loop, including success, failure, timeout, cancellation, invalid/empty output, cleanup, and preflight-blocked calls. Assign a stable `invocation_id` and create its artifact record before preflight; wrapper and parent share atomic `.started` and `.done` hook markers to avoid duplicate or missed hooks. Each retry has a new linked ID. Use existing sanitized evidence only; this analysis must not invoke models, Claude skills, review-loop, subagents, benchmarks, or retries. Its guidance recommendations are proposal-only until I explicitly approve the specific displayed proposals; original-task authorization or silence does not approve them, and pending proposals must not block the original task. Apply approved guidance changes outside the hook through the normal implementation workflow; use `$review-loop` only when I explicitly request it.
- Use Claude CLI model aliases (`fable`, `opus`, or `sonnet`) so the configured current version is selected. Use only `low`, `medium`, or `high` effort.
- Run parallel editing agents in separate worktrees so their changes cannot conflict.
- The calling Codex agent remains responsible for inspecting changes, validating claims, and reporting the final result.

### Using Claude in Codex swarm workflows

- Treat Codex's native multi-agent/subagent workflow as the swarm feature for Claude-backed work.
- Use a bounded `gpt-5.6-luna` subagent with `medium` effort as the default wrapper. Escalate the wrapper only when it must perform substantial reasoning itself.
- Give each wrapper a self-contained task and instruct it to invoke exactly one appropriate Claude-calling skill: `claude-implementation` or `claude-review`, followed by its required `$learn-to-use-claude` hook.
- Name wrapper tasks clearly with the `claude_<model>_<type>_<summary>` pattern, for example `claude_fable_review_auth`.
- Split only independent work across the swarm. Keep the number of agents bounded, wait for every dispatched agent, and return concise summaries instead of raw logs.
- Let Luna prepare the Claude prompt, run `claude -p`, monitor the invocation, and return the report path plus a structured summary to the parent agent.
- Use explicit, safe timeouts. Follow the canonical lifecycle in `{{CODEX_HOME}}/skills/learn-to-use-claude/references/invocation-lifecycle.md`: atomically record the root PID, process-group/session identifier, process start identity, stdout, stderr, exit code, signal, deadlines, and cleanup outcome. On timeout or cancellation, terminate the identity-matched Claude process group and descendants and confirm exit before retrying or finishing. Track and stop any local server owned by the invocation.
- Parallel read-only Claude runs may share the checkout. Parallel Claude runs that edit files must use distinct worktrees; merge only after reviewing each diff.
- The parent Codex agent must independently verify Claude's claims, tests, diffs, and artifacts before presenting or merging the result.
