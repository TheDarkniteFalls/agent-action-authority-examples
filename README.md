# Agent Action Authority Examples

A tiny, runnable example for classifying model or agent actions before
execution.

The demo does not call a model and does not execute actions. It reads synthetic
JSONL cases, classifies each proposed action, and checks the result against the
expected decision.

## Why It Exists

Agent workflows should separate suggestion from authority. A model can propose a
read, write, network, or publish action, but the application should classify the
action before anything happens.

## Run

```sh
python3 action_authority_check.py --self-test
python3 action_authority_check.py examples/action_cases.jsonl
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
python3 -m py_compile action_authority_check.py
```
