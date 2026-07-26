# Agent Action Authority Examples

<!-- toolkit-trust-card:start -->
> **Public contract:** Stable pattern · about 5 min · Python 3 · no model · no network
>
> **Operation:** Read-only check; examples may use temporary files
>
> **A pass establishes:** Synthetic actions and scoped grants receive the expected allow, reject, or reapproval decisions.
>
> **It does not establish:** The classifier does not execute actions, provide a sandbox, or infer security-relevant scope.
>
> **First check:** `python3 action_authority_check.py --self-test`
<!-- toolkit-trust-card:end -->

A tiny collection of runnable examples for checking instructions and plans,
classifying model or agent actions before execution, and recovering safely when
a state-changing tool's outcome is unknown.

The examples do not call a model, use a network, or execute actions. They read
synthetic inputs, return deterministic receipts, and check the result against
the expected decision.

## Is the Agent Rewriting the Plan?

Run the new Plan Fidelity Gate before action-authority checking:

```sh
python3 plan_fidelity_check.py --self-test
```

It keeps four questions separate:

| Layer | Question | Checker |
| --- | --- | --- |
| Instructions | Do the named sources agree? | `plan_fidelity_check.py preflight` |
| Plan | Does the candidate preserve the confirmed process? | `plan_fidelity_check.py check-plan` |
| Action | Is the actual operation allowed? | `action_authority_check.py` |
| Recovery | What is safe after an uncertain effect? | `effect_recovery_check.py` |

The harness owns the approved plan digest, current stage, single-use
authorization, three separate budgets, stop conditions, and promotion. The
agent may vary ordinary detail inside an approved stage, but route, tools,
effects, budgets, approvals, and stage changes require an external decision.

Read the plain-language [Plan Fidelity Gate guide](PLAN_FIDELITY_GATE.md) or
install the strict, explicitly invoked
[`$plan-fidelity` Skill](skills/plan-fidelity/SKILL.md).

## Did the Tool Already Run?

After a timeout or interruption, run the synthetic recovery cases:

```sh
python3 effect_recovery_check.py --self-test
```

Three representative outcomes are:

```text
PASS lost_response_read_back_finds_effect record_observed_success
PASS timeout_read_back_reports_absence retry_same_operation_id
PASS unavailable_read_back_stops_for_review stop_for_review
```

These mean: preserve an observed success, retry only after evidence of absence,
and stop when the outcome cannot be proven.

## Why It Exists

Agent workflows should separate suggestion from authority. A model can propose a
read, write, network, or publish action, but the application should classify the
action before anything happens.

They should also separate goal ownership from procedural authority. A capable
agent may propose a useful repair without owning permission to change the
route, retry budget, review boundary, or next stage.

They should also separate approving one tool call from granting reusable
authority. A reusable grant should be explicit about the tool identity,
application-defined argument scope, and expiry.

After dispatch, a timeout or interruption is not proof that the tool failed.
The caller should preserve the operation identity and distinguish a proven
failure from a successful effect whose response was lost.

## Run

```sh
python3 action_authority_check.py --self-test
python3 action_authority_check.py examples/action_cases.jsonl
python3 scoped_approval_check.py --self-test
python3 scoped_approval_check.py examples/scoped_approval_cases.jsonl
python3 effect_recovery_check.py --self-test
python3 effect_recovery_check.py examples/effect_recovery_cases.jsonl
python3 plan_fidelity_check.py --self-test
python3 plan_fidelity_check.py preflight examples/plan-fidelity/manifest.json
python3 plan_fidelity_check.py check-envelope examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json
python3 plan_fidelity_check.py check-plan examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json examples/plan-fidelity/state.json examples/plan-fidelity/accepted_plan.json
```

Expected result:

```text
PASS read_only_report_allowed allowed
PASS expected_public_write_needs_approval requires_approval
PASS expected_public_write_with_authority allowed
PASS protected_path_write_rejected rejected
PASS destructive_command_rejected rejected
PASS credential_export_rejected rejected
PASS network_publish_needs_confirmation requires_human_confirmation
```

The scoped approval example prints the exact-scope passes and the cases that
fail closed:

```text
PASS no_grant_requires_approval requires_approval
PASS exact_scope_reused approved_within_scope
PASS scope_key_order_is_irrelevant approved_within_scope
PASS changed_revision_requires_approval requires_approval
PASS changed_environment_requires_approval requires_approval
PASS different_namespace_requires_approval requires_approval
PASS expired_grant_requires_approval requires_approval
PASS expiry_boundary_requires_approval requires_approval
PASS malformed_expiry_requires_approval requires_approval
PASS missing_scope_requires_approval requires_approval
PASS non_approved_grant_requires_approval requires_approval
PASS additional_scope_field_requires_approval requires_approval
PASS self_test
```

## Scoped Approval Grants

Some agent frameworks support both approval for one call and a sticky approval
for later calls to the same tool. The middle case is application-owned: reuse
an approval only while a deliberately constructed scope still matches.

This example models an idempotent synthetic tool:

```text
deploy_service(environment, revision)
```

A grant for `release.deploy_service`, `environment=staging`, and
`revision=abc123` does not cover a different revision, production, another
namespace, malformed scope, or an expired grant. Dictionary key order does not
matter, but every scope field does.

The application constructs the scope; the checker does not guess which raw
arguments are security-relevant. It returns `approved_within_scope` only for an
exact, live, explicitly approved grant. Every other condition returns
`requires_approval`.

For the OpenAI Agents SDK, this is an application pattern rather than a claimed
SDK feature. The SDK's current
[human-in-the-loop guide](https://github.com/openai/openai-agents-python/blob/main/docs/human_in_the_loop.md)
documents per-call decisions and tool-wide `always_approve=True`. An application
can keep resolving the current interruption per call, store any deliberately
reusable grant in its own policy state, and use a callable `needs_approval` rule
to check later calls. This repository does not import, wrap, or modify the SDK.

### Grant Fixture Shape

Each scoped approval JSONL row contains:

- `id`: case name.
- `now`: explicit timezone-aware evaluation time.
- `call`: `tool`, `namespace`, and the application-defined `scope`.
- `grant`: optional explicit decision, matching identity and scope, and
  `expires_at`.
- `expected_decision`: `approved_within_scope` or `requires_approval`.

This is deliberately fail-closed. Missing, malformed, changed, rejected, or
expired evidence never becomes reusable authority.

## Prevent Duplicate Agent Tool Calls After Timeout Or Interruption

When a state-changing agent tool times out, "the call failed" and "the response
was lost after success" can look identical to the caller. Blindly retrying can
duplicate an order, message, booking, file write, or other external effect.

[`effect_recovery_check.py`](effect_recovery_check.py) demonstrates a small
application-owned recovery contract. It binds one stable operation ID to a
canonical digest of the tool name and arguments, preserves the prior attempt
state, and requires explicit read-back evidence before retrying an ambiguous
outcome.

Before following `execute_once`, the host must atomically reserve the operation
ID and digest as `in_flight`. If another caller already reserved it, reload that
record and evaluate again instead of dispatching a concurrent duplicate.

| Evidence | Decision |
| --- | --- |
| No prior attempt | `execute_once` |
| Same request already committed | `return_recorded_result` |
| Operation ID reused for different tool arguments | `reject_idempotency_key_reuse` |
| Prior outcome unknown; read-back reports the effect | `record_observed_success` |
| Prior outcome unknown; read-back reports no effect | `retry_same_operation_id` |
| Prior outcome unknown; read-back unavailable | `stop_for_review` |
| Matching attempt still in flight | `wait_for_current_attempt` |
| Record reports failure before any effect and includes evidence | `retry_same_operation_id` |

Each fixture contains an `operation` with a stable `id`, `tool`, and
`arguments`; an optional durable `record`; optional application-specific
`read_back` evidence; and the `expected_decision`. The checker returns a
deterministic receipt containing the operation ID, request digest, prior state,
read-back status, decision, and reason. It never treats missing evidence as
proof that an effect is absent.

## Decisions

| Action | Decision |
| --- | --- |
| Read-only/report action | `allowed` |
| Write to expected public path | `requires_approval`, unless explicit expected-write authority is present |
| Write to protected path | `rejected` |
| Destructive command | `rejected` |
| Credential/private/export action | `rejected` |
| Network/publish action | `requires_human_confirmation` |

## Fixture Shape

Each JSONL row contains:

- `id`: case name.
- `action`: proposed action with `type`, optional `paths`, and optional
  `command`.
- `expected_write_paths`: paths the workflow expected to touch.
- `explicit_expected_write`: whether the workflow already granted write
  authority for those expected paths.
- `expected_decision`: expected classifier result.

## Public Data Notice

All cases are synthetic. Do not add private prompts, real instruction bundles,
controller state, assistant logs, connector exports, credentials, local paths,
or personal data.

## Scope

These are decision examples, not a sandbox or transaction manager. They do not
execute, retry, or undo an action, and they cannot provide exactly-once delivery
when the underlying tool or service lacks durable idempotency and read-back
support. The recovery checker validates the supplied record and evidence shape;
the host application remains responsible for collecting trustworthy evidence
and persisting operation state durably.

The Plan Fidelity Gate is likewise effective only when the host protects the
confirmed envelope and controller state from agent writes, withholds dispatch
on blocking receipts, atomically consumes stage authorization, and refuses
automatic promotion. The bundled Skill follows that procedure but cannot turn
natural-language guidance into a sandbox.

## Quality Checks

```sh
python3 action_authority_check.py --self-test
python3 action_authority_check.py examples/action_cases.jsonl
python3 scoped_approval_check.py --self-test
python3 scoped_approval_check.py examples/scoped_approval_cases.jsonl
python3 effect_recovery_check.py --self-test
python3 effect_recovery_check.py examples/effect_recovery_cases.jsonl
python3 plan_fidelity_check.py --self-test
python3 plan_fidelity_check.py preflight examples/plan-fidelity/manifest.json
python3 plan_fidelity_check.py check-envelope examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json
python3 plan_fidelity_check.py check-plan examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json examples/plan-fidelity/state.json examples/plan-fidelity/accepted_plan.json
python3 -m py_compile action_authority_check.py scoped_approval_check.py effect_recovery_check.py plan_fidelity_check.py skills/plan-fidelity/scripts/plan_fidelity_check.py
```
