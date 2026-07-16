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

A tiny collection of runnable examples for classifying model or agent actions
before execution.

The examples do not call a model and do not execute actions. They read
synthetic JSONL cases, classify each proposed action or approval scope, and
check the result against the expected decision.

## Why It Exists

Agent workflows should separate suggestion from authority. A model can propose a
read, write, network, or publish action, but the application should classify the
action before anything happens.

They should also separate approving one tool call from granting reusable
authority. A reusable grant should be explicit about the tool identity,
application-defined argument scope, and expiry.

## Run

```sh
python3 action_authority_check.py --self-test
python3 action_authority_check.py examples/action_cases.jsonl
python3 scoped_approval_check.py --self-test
python3 scoped_approval_check.py examples/scoped_approval_cases.jsonl
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

All cases are synthetic. Do not add private prompts, real assistant logs,
connector exports, credentials, local paths, or personal data.

## Scope

This is a classifier example, not a sandbox. It prints the decision a host
application should enforce before executing anything.

## Quality Checks

```sh
python3 action_authority_check.py --self-test
python3 action_authority_check.py examples/action_cases.jsonl
python3 scoped_approval_check.py --self-test
python3 scoped_approval_check.py examples/scoped_approval_cases.jsonl
python3 -m py_compile action_authority_check.py scoped_approval_check.py
```
