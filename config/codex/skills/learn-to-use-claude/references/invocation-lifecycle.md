# Claude Invocation Lifecycle

Use this contract for every intended Claude model call. The caller owns the record; a wrapper may update it but must not redefine the schema or retry policy.

## Artifact layout and record

Create a unique artifact directory and register `{invocation_id, artifact_dir}` in the parent workflow before authentication preflight. Preserve target-relative paths when freezing files with duplicate basenames, and verify one manifest entry and content hash for every requested target before dispatch.

Store `invocation.json` and update it atomically through a same-directory temporary file plus rename. Keep review artifacts outside the reviewed snapshot. Use these fields, with unavailable values set to `null` rather than omitted or inferred:

```text
schema_version, invocation_id, linked_invocation_id, caller, purpose,
model_alias, effort, permissions, enabled_tools, artifact_dir,
prompt_path, stdout_path, stderr_path, report_path, learning_result_path,
state, preflight_result, retry_owner, attempt, retry, snapshot,
created_at, dispatched_at, ended_at, root_pid, process_group_id,
process_start_identity, timeout_seconds, timeout_rationale, deadline_at,
report_deadline_at, hook_deadline_at, exit_code, signal, timed_out,
cancelled, owned_servers, cleanup_outcome, cleanup_confirmed_at,
report_validity, verification_status, verification_summary
```

Advance `state` through the applicable terminal path: `created`, `preflight_passed` or `preflight_blocked`, `dispatched`, `exited`/`timed_out`/`cancelled`, `cleaned`, `verified`, `hook_pending`, and `hook_complete`. A parent sweep collects missing terminal evidence before interpreting a non-terminal record.

Use distinct files: `stdout.log` for raw Claude stdout, `stderr.log`, `report.md` for the validated Claude-only report, and `learning-result.md` for hook output. Never append hook content to the Claude report.

## Preflight classification

Record exactly one of `passed`, `not_logged_in`, `missing_cli`, `network_error`, `invalid_status`, or `other`.

- `not_logged_in` and `missing_cli` are terminal external blockers and are not retryable.
- `network_error`, `invalid_status`, and `other` end the current invocation. The wrapper returns them without retrying; a parent that owns retries decides whether the evidence qualifies for its one technical retry.

Run the learning hook for every blocked intended call after recording the outcome.

## Process supervision and deadlines

Use `../scripts/invocation_lifecycle.py` for process supervision and marker operations when local Python is available. Treat its generated `invocation.json`, stdout/stderr, exit state, cleanup state, and marker validation as the default implementation; an alternative must preserve the same observable contract.

Launch Claude under a platform-appropriate supervisor with an isolated process group or equivalent descendant-tracking mechanism. Before waiting, atomically record the root PID, process-group/session identifier, process start identity, timeout, rationale, and absolute deadline. On macOS, use process APIs or an available supervisor; do not assume GNU `setsid` exists.

Choose and record a bounded Claude timeout from the task size and expected tool use. A parent-owned technical retry may use a different bounded timeout only when the prior evidence shows the original bound was insufficient; record the reason and keep the same reviewer role and models.

On timeout or cancellation, signal the process group, then any identity-matched descendants. Poll until the group and recorded descendants are gone. Guard against PID reuse with the recorded start identity. Record exit code, signal, timeout/cancellation state, and cleanup outcome before deciding retry eligibility.

Record each caller-owned server as `{pid, process_group_id, process_start_identity, port, kind}`. Stop and confirm only servers owned by the invocation. Include detached Claude-started descendants when they can be attributed safely; otherwise record cleanup as unresolved.

Claude execution, report materialization/validation, cleanup, and the learning hook have separate bounded deadlines. The wrapper's overall deadline includes all four plus a cleanup margin. Once a valid raw report exists, a later validation or hook timeout must not consume a reviewer retry: preserve the report, stop the wrapper if needed, and let the parent validate it and finish the hook.

## Hook ownership and markers

The supplied supervisor uses POSIX process groups, not full descendant containment. It records `process_group_cleanup_outcome` separately. Successful exits with no remaining group report `cleanup_outcome: not_required`; requested cleanup reports `unresolved` even after the original group exits because descendants can call `setsid` or double-fork. Neither result proves full-tree containment. The caller must independently attribute and verify detached descendants before recording full-tree cleanup as confirmed or retrying. The helper currently requires POSIX APIs; use an equivalent platform supervisor on Windows.

For direct calls, the caller finishes independent verification before running the hook. For `review-loop`, the parent owns final report validation and hook finalization; the wrapper records evidence and returns the raw report without claiming the hook is complete on the parent's behalf.

Claim with `hook-claim --owner-pid <long-lived caller PID> --claim-token <unique random token> --deadline <future timezone-aware ISO timestamp>`. Keep that caller alive through completion; never use the short-lived helper PID. The started/takeover records retain its PID/start identity and token. The helper serializes claim, takeover and completion using an OS-released file lock. Exit 0 grants ownership; 2 means busy, 3 already complete, and 4 invalid completion. Only exit 0 authorizes analysis. Takeover requires owner death or deadline expiry and fences the prior token from completion.

Complete with `hook-complete --claim-token <the granted token>` plus the result and outcome arguments. Completion requires the current token, live matching owner and unexpired deadline. Under the same lock, write the done marker atomically only if absent; never overwrite a completed result. A valid done marker contains the invocation ID, claim token, outcome, proposal IDs, completion time, result path and a digest matching that file. Missing or invalid completion never suppresses pending work. Hook failure remains separate from the model result, reviewer retries, paired coverage and mergeability.

The parent maintains an invocation-ID-to-artifact-directory index for every dispatched wrapper and sweeps it after process cleanup. A valid done marker suppresses duplicate analysis; otherwise the parent applies the takeover rules and completes the hook from existing evidence.

## Review-loop provenance and result schema

Use one canonical provenance block in the prompt and raw report:

```text
reviewer_type: <type>
source: claude
reviewer_identity: <source, specialty, wrapper model, Claude alias, effort>
invocation_id: <stable ID>
parent_workflow: review-loop
attempt: <1..3>
retry: <0|1>
snapshot: <identifier>
retry_owner: parent
findings: <structured list>
residual_gaps: <text or none>
```

The wrapper must not translate field names, supply missing provenance after Claude exits, retry, or change models. A missing or mismatched required field makes the report malformed and returns control to the parent.
