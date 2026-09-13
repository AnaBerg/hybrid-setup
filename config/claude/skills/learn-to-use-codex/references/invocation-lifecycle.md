# Codex Invocation Lifecycle

Use this contract for every intended Codex call (`codex exec`, `codex review`, or a wrapper that runs either). The caller owns the record; a wrapper may update it but must not redefine the schema or retry policy.

## Artifact layout and record

Create a unique artifact directory and register `{invocation_id, artifact_dir}` in the parent workflow before authentication preflight. Preserve target-relative paths when freezing files with duplicate basenames, and verify one manifest entry and content hash for every requested target before dispatch.

Store `invocation.json` and update it atomically through a same-directory temporary file plus rename. Keep review artifacts outside the reviewed snapshot. Use these fields, with unavailable values set to `null` rather than omitted or inferred:

```text
schema_version, invocation_id, linked_invocation_id, caller, purpose,
model_alias, effort, sandbox_mode, approval_policy, enabled_tools, artifact_dir,
prompt_path, stdout_path, stderr_path, report_path, learning_result_path,
state, preflight_result, retry_owner, attempt, retry, snapshot,
created_at, dispatched_at, ended_at, root_pid, process_group_id,
process_start_identity, timeout_seconds, timeout_rationale, deadline_at,
report_deadline_at, hook_deadline_at, exit_code, signal, timed_out,
cancelled, owned_servers, cleanup_outcome, cleanup_confirmed_at,
report_validity, verification_status, verification_summary
```

Advance `state` through the applicable terminal path: `created`, `preflight_passed` or `preflight_blocked`, `dispatched`, `exited`/`timed_out`/`cancelled`, `cleaned`, `verified`, `hook_pending`, and `hook_complete`. A parent sweep collects missing terminal evidence before interpreting a non-terminal record.

Use distinct files: `stdout.log` for raw Codex stdout, `stderr.log`, `report.md` for the validated Codex-only report, and `learning-result.md` for hook output. Never append hook content to the Codex report.

## Preflight classification

Record exactly one of `passed`, `not_logged_in`, `missing_cli`, `network_error`, `invalid_status`, or `other`.

- `not_logged_in` and `missing_cli` are terminal external blockers and are not retryable.
- `network_error`, `invalid_status`, and `other` end the current invocation. The wrapper returns them without retrying; a parent that owns retries decides whether the evidence qualifies for its one technical retry.

Run the learning hook for every blocked intended call after recording the outcome.

## Process supervision and deadlines

Use `../scripts/invocation_lifecycle.py` for process supervision and marker operations when local Python is available. Treat its generated `invocation.json`, stdout/stderr, exit state, cleanup state, and marker validation as the default implementation; an alternative must preserve the same observable contract.

Launch Codex under a platform-appropriate supervisor with an isolated process group or equivalent descendant-tracking mechanism. Before waiting, atomically record the root PID, process-group/session identifier, process start identity, timeout, rationale, and absolute deadline. On macOS, use process APIs or an available supervisor; do not assume GNU `setsid` exists.

Choose and record a bounded Codex timeout from the task size and expected tool use. Codex runs routinely exceed a 10-minute default, so set the bound explicitly rather than inheriting a shell default. A parent-owned technical retry may use a different bounded timeout only when the prior evidence shows the original bound was insufficient; record the reason and keep the same reviewer role and models.

On timeout or cancellation, signal the process group, then any identity-matched descendants. Poll until the group and recorded descendants are gone. Guard against PID reuse with the recorded start identity. Record exit code, signal, timeout/cancellation state, and cleanup outcome before deciding retry eligibility.

Record each caller-owned server as `{pid, process_group_id, process_start_identity, port, kind}`. Stop and confirm only servers owned by the invocation. Include detached Codex-started descendants (dev servers, simulators, browsers started by `codex-computer-use`) when they can be attributed safely; otherwise record cleanup as unresolved.

Codex execution, report materialization/validation, cleanup, and the learning hook have separate bounded deadlines. The wrapper's overall deadline includes all four plus a cleanup margin. Once a valid raw report exists, a later validation or hook timeout must not consume a reviewer retry: preserve the report, stop the wrapper if needed, and let the parent validate it and finish the hook.

## Hook ownership and markers

For direct calls, the caller finishes independent verification before running the hook. For `review-loop`, the parent owns final report validation and hook finalization; the wrapper records evidence and returns the raw report without claiming the hook is complete on the parent's behalf.

Create `learning-hook.<invocation_id>.started` in the invocation artifact directory with exclusive-create semantics. Its JSON content contains `schema_version`, `invocation_id`, owner PID/start identity, `started_at`, and `hook_deadline_at`. If it already exists and its matching owner is alive before the deadline, do not take over. A parent may take over only when the owner is confirmed dead or the deadline passed, using an exclusive `learning-hook.<invocation_id>.takeover` marker.

Write `learning-hook.<invocation_id>.done` through a same-directory temporary file plus atomic rename. A valid done marker is parseable JSON containing the matching `invocation_id`, evidence digest, outcome class, proposal IDs or `[]`, and `completed_at`. A missing or invalid done marker never counts as completion. Hook failure is recorded separately and never changes the Codex result, reviewer retry decision, paired coverage, or mergeability.

The parent maintains an invocation-ID-to-artifact-directory index for every dispatched wrapper and sweeps it after process cleanup. A valid done marker suppresses duplicate analysis; otherwise the parent applies the takeover rules and completes the hook from existing evidence.

## Review-loop provenance and result schema

Use one canonical provenance block in the prompt and raw report:

```text
reviewer_type: <type>
source: codex
reviewer_identity: <source, specialty, wrapper model, Codex alias, effort>
invocation_id: <stable ID>
parent_workflow: review-loop
attempt: <1..3>
retry: <0|1>
snapshot: <identifier>
retry_owner: parent
findings: <structured list>
residual_gaps: <text or none>
```

The wrapper must not translate field names, supply missing provenance after Codex exits, retry, or change models. A missing or mismatched required field makes the report malformed and returns control to the parent.
