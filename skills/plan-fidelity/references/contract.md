# Plan Fidelity v0.1 Contract

## Contents

- Instruction manifest
- Confirmed envelope
- Controller state
- Candidate plan
- Decisions and counters
- Host responsibilities

## Instruction manifest

Use schema `instruction-manifest/v0.1`:

```json
{
  "schema_version": "instruction-manifest/v0.1",
  "sources": [
    {"id": "policy", "role": "harness_policy", "path": "policy.md"},
    {"id": "task", "role": "task", "text": "Prepare a synthetic plan."}
  ],
  "constraints": [
    {"source_id": "policy", "field": "effect_ceiling", "value": "proposal"},
    {"source_id": "task", "field": "required_effect", "value": "proposal"},
    {"source_id": "policy", "field": "allowed_routes", "value": ["local_files"]},
    {"source_id": "task", "field": "required_route", "value": "local_files"},
    {"source_id": "policy", "field": "next_slice", "value": "manual_only"},
    {"source_id": "policy", "field": "stop_after", "value": "human_review"},
    {"source_id": "task", "field": "success_boundary", "value": "plan ready for review"}
  ]
}
```

Roles are `harness_policy`, `instruction`, `task`, and `reference`. Supply
exactly one of `path` or `text`. Paths are UTF-8 `.md`, `.txt`, or `.json`
files relative to the manifest. The checker does not crawl, follow URLs, or
expand includes.

Exact structured contradictions block. Deterministic phrase matches are
review findings and must be acknowledged in the confirmed envelope. Neither
kind grants authority.

## Confirmed envelope

Use schema `plan-fidelity-envelope/v0.1`. The human confirms consequential
process choices while leaving ordinary `step_details` to the agent:

```json
{
  "schema_version": "plan-fidelity-envelope/v0.1",
  "plan_id": "synthetic-plan-v1",
  "goal_id": "synthetic-goal",
  "slice_id": "review-slice",
  "source_bundle_digest": "sha256:...",
  "acknowledged_review_findings": [],
  "stages": [
    {
      "id": "inspect",
      "route": "local_files",
      "allowed_tools": ["read_file"],
      "effect_ceiling": "read"
    },
    {
      "id": "review",
      "route": "human_review",
      "allowed_tools": [],
      "effect_ceiling": "proposal"
    }
  ],
  "plan_failure_budget": 2,
  "stage_attempt_budget": 2,
  "effect_retry_budget": 0,
  "permitted_repairs": [
    "add_effect_recovery_checkpoint",
    "add_required_checkpoint",
    "remove_out_of_scope_step",
    "supply_missing_stage_metadata"
  ],
  "approval_checkpoints": ["before_state_change", "before_external_action"],
  "stop_conditions": ["human_review_reached", "budget_exhausted"],
  "success_boundary": "synthetic plan ready for human review",
  "next_slice": "manual_only",
  "exceptions": []
}
```

Run `check-envelope` to validate and digest the envelope. The command does not
approve it. A human or external controller must confirm the digest and store it
outside the agent's writable authority.

An exception, when explicitly approved through a new envelope, needs `id`,
`stage_id`, `scope`, `expires_after_stage`, and `non_precedent: true`. An
exception absent from the current envelope grants no authority.

## Controller state

Use schema `plan-fidelity-state/v0.1`:

```json
{
  "schema_version": "plan-fidelity-state/v0.1",
  "approved_plan_digest": "sha256:...",
  "authorization_id": "auth-inspect-001",
  "authorization_status": "issued",
  "current_stage": "inspect",
  "plan_failures_used": 0,
  "stage_attempts_used": 0,
  "effect_retries_used": 0,
  "failure_labels": [],
  "active_exception_ids": []
}
```

The checker reads this state and proposes counter changes in its receipt. It
never writes or consumes state. The host must persist counters, atomically
consume a single-use authorization before dispatch, and issue a new
authorization for another stage.

## Candidate plan

Use schema `candidate-plan/v0.1`. Repeat all authority-bearing fields. Put
ordinary implementation choices in `step_details`; the checker treats those as
opaque, while the later action-authority gate still evaluates the actual action.

```json
{
  "schema_version": "candidate-plan/v0.1",
  "plan_id": "synthetic-plan-v1",
  "goal_id": "synthetic-goal",
  "slice_id": "review-slice",
  "authorization_id": "auth-inspect-001",
  "current_stage": "inspect",
  "ordered_stages": ["inspect", "review"],
  "route": "local_files",
  "tools": ["read_file"],
  "effect": "read",
  "plan_failure_budget": 2,
  "stage_attempt_budget": 2,
  "effect_retry_budget": 0,
  "approval_checkpoints": ["before_state_change", "before_external_action"],
  "stop_conditions": ["human_review_reached", "budget_exhausted"],
  "success_boundary": "synthetic plan ready for human review",
  "next_slice": "manual_only",
  "requested_process_changes": [],
  "requests_next_stage": false,
  "uses_exceptions": [],
  "effect_retry_requested": false,
  "effect_recovery_checkpoint": false,
  "step_details": ["Inspect the named synthetic files."]
}
```

## Decisions and counters

- `accept_current_slice`: send the current proposed action to action-authority
  checking. It does not authorize execution or another stage.
- `repair_plan`: increment the plan-failure counter once and resubmit only with
  a named, preapproved authority-preserving repair.
- `reapproval_required`: stop because controller-owned process authority would
  change. Do not increment the plan-failure counter.
- `stop_for_review`: stop successfully because a stop boundary or budget was
  reached.

Keep three counters separate:

- Plan failures count repairable candidate-plan violations.
- Stage attempts count attempts at the current workflow stage, regardless of
  changing diagnostic labels.
- Effect retries count retries after dispatch and belong to effect recovery.

Parser errors, instruction conflicts, and reapproval requests do not consume a
plan failure. A receipt proposes state but always reports
`state_persisted=false` and `promotion_decision=not_promoted`.

## Host responsibilities

The checker is not a sandbox. The host must:

1. Protect the confirmed envelope and controller state from agent writes.
2. Withhold dispatch on exit code `2` or `dispatch_allowed=false`.
3. Persist accepted counter transitions itself.
4. Atomically consume stage authorization before dispatch.
5. Pass actual actions through action-authority checking.
6. Observe state independently and use effect recovery after uncertain writes.
7. Issue a new authorization for every stage or slice.
