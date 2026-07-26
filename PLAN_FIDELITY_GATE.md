# Instruction Preflight and Plan Fidelity Gate

An agent may own the goal, but the harness owns the route, retry budget, and
stop button.

This example checks whether supplied instructions agree and whether a proposed
agent plan stays inside a human-confirmed process. It calls no model, makes no
network request, executes no action, and changes no controller state.

## The problem in plain language

A capable agent can remain faithful to an outcome while quietly changing the
approved way of reaching it. When a stage becomes difficult, it may add a
script, switch tools, reinterpret a retry, skip review, or continue into the
next slice. Each step can sound helpful while the cumulative process is no
longer the one a person approved.

Plan fidelity makes that procedural authority explicit. It does not attempt to
detect desire, personality, or malicious intent. It compares observable data:
the instructions, confirmed process, controller state, and candidate plan.

## Four layers, four questions

| Layer | Question | Example in this repository |
| --- | --- | --- |
| Instructions | Do the named sources agree? | Instruction preflight |
| Plan | Does the candidate preserve the approved process? | Plan Fidelity Gate |
| Action | Is this actual operation allowed? | Action authority and scoped approval |
| Recovery | What is safe after an uncertain effect? | Effect recovery |

The Plan Fidelity Gate does not duplicate action classification or effect
recovery. An accepted current-stage plan moves to action-authority checking. A
state-changing retry moves to effect recovery before another attempt.

## Quick start

Run the complete deterministic self-test:

```sh
python3 plan_fidelity_check.py --self-test
```

Run the readable synthetic path:

```sh
python3 plan_fidelity_check.py preflight examples/plan-fidelity/manifest.json
python3 plan_fidelity_check.py check-envelope examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json
python3 plan_fidelity_check.py check-plan examples/plan-fidelity/manifest.json examples/plan-fidelity/envelope.json examples/plan-fidelity/state.json examples/plan-fidelity/accepted_plan.json
```

Add `--json` to any command for a canonical machine-readable receipt.

Exit code `0` means evaluation completed and the current slice may advance to
the next named gate. Exit code `2` means evaluation completed correctly but
dispatch is held for repair, reapproval, or review. Exit code `1` means the
input could not be evaluated.

## 1. Preflight named instructions

The manifest labels each source as a harness policy, instruction, task, or
reference. Sources may be inline or explicitly named UTF-8 Markdown, text, or
JSON files beside the manifest. The checker rejects absolute paths, path
escapes, oversized bundles, unsupported types, and duplicate JSON keys. It
does not crawl a repository, follow links, or expand includes.

Preflight reports:

- **Exact findings** from structured constraints, such as an external action
  required under a proposal-only effect ceiling.
- **Review findings** from a small deterministic phrase registry, such as
  proposal-versus-implementation language or an imperative in reference data.

Phrase findings are review aids, not semantic understanding. Raw text never
silently becomes authority. The human confirms a normalized envelope after
reviewing them.

## 2. Confirm and freeze the process

The envelope names the goal, slice, ordered stages, route and tool boundaries,
effect ceilings, approval checkpoints, success boundary, stop conditions, and
three separate budgets. `check-envelope` validates it and prints a canonical
digest.

The command does not approve the envelope. A human or external controller
confirms that digest and stores it in controller-owned state. If the agent can
edit the authoritative envelope or state, the gate cannot enforce its claim.

The contract freezes consequential process choices while leaving ordinary
implementation detail inside an approved stage open to the agent. Candidate
`step_details` are opaque here; the actual operation is still checked later.

## 3. Keep three budgets separate

| Budget | What it counts | Who updates it |
| --- | --- | --- |
| Plan failures | Repairable rejected candidate plans | External controller from the receipt |
| Stage attempts | Attempts at the current approved stage | External controller after execution evidence |
| Effect retries | Retries after a state-changing dispatch | Effect-recovery controller |

Changing an error label from “environment” to “schema” does not reset the
current-stage attempt budget. Diagnostic labels may help explain a failure, but
they do not control the counter.

The checker is read-only. Its receipt proposes a counter value and always says
`state_persisted=false`.

## 4. Treat process changes as new authority

The candidate may vary harmless details. It may not silently change:

- the goal, slice, current stage, or stage order;
- the route, tool boundary, or effect ceiling;
- approval checkpoints, budgets, stop conditions, or success boundary;
- the controller's single-use authorization; or
- `next_slice=manual_only`.

A process change returns `reapproval_required`. The recommendation is retained
for human review but is not executed.

One-off exceptions are scoped to a newly digested envelope, expire at a named
stage, and carry `non_precedent=true`. An exception absent from the current
envelope grants no authority, even if a similar workaround was approved before.

## Decisions

| Decision | Meaning |
| --- | --- |
| `accept_current_slice` | Current stage matches; continue only to the named action or recovery gate. |
| `repair_plan` | One named authority-preserving repair may be submitted while budget remains. |
| `reapproval_required` | Process authority would change; generation stops for an external decision. |
| `stop_for_review` | The harness reached an approved stop or exhausted a budget correctly. |

Stopping is a successful gate outcome. It proves that the authorized process
stopped where intended; it does not claim that the larger goal succeeded.

Every gate receipt includes the approved digest, source digest, current stage,
authorization ID, all three counters, findings, proposed state, decision,
reason, next gate, and these explicit negatives:

```text
model_called=false
actions_executed=false
network_used=false
state_mutating=false
state_persisted=false
promotion_decision=not_promoted
```

## Install the strict Codex Skill

Ask Codex:

```text
Install the plan-fidelity Skill from https://github.com/TheDarkniteFalls/agent-action-authority-examples/tree/main/skills/plan-fidelity
```

On the next turn, invoke it explicitly:

```text
Use $plan-fidelity to preflight these named instructions and check my proposed plan. Do not execute actions.
```

The Skill is deliberately not invoked implicitly. It follows the gate, stops
on blocking receipts, and never treats an accepted plan as action authority.
It cannot restrain a host that ignores the receipt or lets the agent rewrite
controller state.

## Synthetic coverage

The self-test fixes these expected outcomes:

1. exact current-stage plan accepted;
2. missing metadata receives a permitted repair;
3. repeated repairable failure exhausts the plan budget;
4. differently labelled failures exhaust one stage budget;
5. an adapter or other process change requires reapproval;
6. changed approved-plan digest requires reapproval;
7. a human-review checkpoint is a successful stop;
8. the next stage is not promoted automatically;
9. a prior one-off exception grants no authority; and
10. an uncertain state-changing effect routes to effect recovery.

The separate conflicting manifest also demonstrates contradictory instructions
and untrusted directive detection before a plan is evaluated.

## Bounded claim

A harness can prevent an outcome-driven agent from silently rewriting an
approved process by making plan changes, retry budgets, stops, and stage
promotion externally enforceable.

This repository demonstrates the contract and receipts. It is not a sandbox,
transaction manager, general prompt-understanding system, agent-alignment
solution, or claim about one particular model. Enforcement remains the host's
responsibility.
