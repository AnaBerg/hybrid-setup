# Personal preferences

## Communication

- I am Brazilian and my native language is pt-BR; preferably communicate with me in Portuguese
- I am fluent in English, so no need to simplify language
- All code, documentation, commits, and PRs must be in English
- Be direct and consice when talking with me
- Only give relevant information, I prefere to ask you for more than to read a wall of text
- I don't need status update, only a brief description by the end of the execution is enough.
- I prefer reading bullet points than prose text.

## Implementation

- All implementation should be done with subagents or workflows
- If is possible to parallelize the work, do it
- If you need a paragraph-long comment to justify why the work should be done is OK, makes sense to do it - just implement it

## PRs and commits

- Commit atomically
- Always opens the PR in draft
- Use merge, don't rebase
- Never force push
- If applicable, add screenshots of the UI in the description of the PR
- On creating PRs, aways put the assignee as `AnaBerg`
- Commit messages should always reflect a summary of what has been implemented
- When a implementation has finished, open the PR
- Branch names should always follow this pattern {action}/{summary}, where actions can be only `feature`, `fix`, `refactor`, `chore` or `hotfix` and summary should be summary of what has been implemented

## Code style

- If something can be implemented simply, do it simply
- Be direct and concise
- Readability is essential

## Performance

- If I ask for too much work at once, stop and say so clearly
- If computer use helps with completeness or validation, use gpt with Codex via shell

## Choosing models for workflows and subagents

Ranking: higher = better. Cost reflects what I pay, not list price. Intelligence is the problem difficulty a model can handle without supervision. Taste covers UI/UX, code quality, API design, and copy.

| model         | cost | intelligence | taste |
| ------------- | ---- | ------------ | ----- |
| gpt-6-astra   | 8    | 9            | 8     |
| gpt-5.6-sol   | 8    | 8            | 5     |
| gpt-5.6-terra | 9    | 7            | 4     |
| gpt-5.6-luna  | 10   | 3            | 2     |
| sonnet-5      | 5    | 5            | 7     |
| opus-5        | 6    | 8            | 9     |
| fable-5.1     | 2    | 9            | 9     |

### How to apply

- These are defaults, not limits. You may override them: if a cheaper model does not meet the expected quality bar, redo the work with a smarter model without asking. Escalation is cheaper than mediocrity
- Priority order: intelligence > taste > cost
- For bulk work (clear-spec implementation, data analysis, migrations), use gpt
- Anything user-facing (UI, copy, API design) must have taste above 7
- For plan or implementation reviews, use fable-5.1 or opus-5; optionally use gpt for an independent review
- Never use Haiku
- gpt is available only via Codex CLI: `codex exec` / `codex review`. Invoke it programmatically through the `codex-implementation`, `codex-review`, or `codex-computer-use` skills; if the work is not covered by them, run `codex exec -s read-only` directly with a self-contained prompt, still following the Codex invocation lifecycle below
- Use Claude Models (sonnet-5, fable-5.1, opus-5) through the Agent/Workflow model parameter
- gpt-5.6-luna is only to be used with `medium` or `high` effort
- only use `light`, `low`, `medium` or `high` effort

### Using gpt in workflows and subagents

The model parameter only accepts Claude Models, so use a wrapper.

- Create a small Claude wrapper agent with `model: sonnet, effort: low`; prompt it to write a self-contained Codex prompt, run `codex exec` via Bash through one of the three Codex-calling skills (`codex-implementation`, `codex-review`, `codex-computer-use`), and return the report. Use `schema` in the wrapper for structured output
- The wrapper must run the mandatory `learn-to-use-codex` hook for its invocation, or hand the parent the evidence and invocation ID so the parent can finalize the hook exactly once
- Label these agents with the `gpt-{type}:` prefix, e.g. `{label: 'gpt-5.6-terra:review-auth'}`. The workflow UI only shows the Claude Model, so the label identifies gpt as the real runner
- Codex runs may exceed Bash's 10 min timeout; set a specific timeout or run in the background and poll for the report
- Parallel gpt agents must use `isolation: 'worktree'` so Codex edits do not conflict with the shared checkout
- Wrappers never retry autonomously or change models on their own; the parent owns retries, and each retry gets a new linked invocation ID
- Workflow token budgets only count Claude tokens; Codex work is free and invisible to `budget.spent()`

## Codex invocations and the learning hook

- Codex/gpt is invoked programmatically through `codex-implementation`, `codex-review`, or `codex-computer-use`. `codex-computer-use` remains the path for computer use, browser automation, simulators, screenshots, and independent runtime inspection
- Every intended Codex/gpt invocation requires exactly one `learn-to-use-codex` analysis. This covers successes, errors, timeouts, cancellations, empty or malformed reports, cleanup failures, and preflight-blocked calls
- Assign a stable `invocation_id` and create its invocation record before preflight, following `{{CLAUDE_HOME}}/skills/learn-to-use-codex/references/invocation-lifecycle.md`
- Each retry receives a new invocation ID linked to the original
- `learn-to-use-codex` never invokes Codex, another model, a subagent, or `review-loop` for its own analysis
- Learning recommendations require my explicit approval of the specific proposal before they are applied; silence, general autonomy, or authorization for the original task is not approval
- Pending learning proposals must not block or delay the original task

## Opt-in workflows

- `review-loop` is strictly opt-in. Run it only when I explicitly invoke `$review-loop` or clearly ask for a paired review loop or fix-review cycle. It must never run automatically after implementation, skill creation, fixes, ordinary verification, or before opening a pull request
- `babysit-pr` is also opt-in. Run it only when I explicitly invoke it and give the exact PR URLs or numbers to watch
