---
name: plan-fidelity
description: Preflight instruction bundles and check structured agent plans against controller-owned routes, budgets, approvals, stop conditions, and single-stage authority. Use when a user wants a strict proposal-before-action boundary, a persistent agent must not rewrite its process, retries or stage promotion need external control, or supplied instructions may conflict.
---

# Plan Fidelity

Keep process authority outside the agent. Use the bundled deterministic checker;
do not decide that your own deviation is harmless.

## Follow the gate

1. Identify only the instruction, policy, task, and reference sources the user
   explicitly supplied. Do not crawl the repository, follow links, or expand
   includes.
2. Read [references/contract.md](references/contract.md) before creating or
   interpreting manifest, envelope, state, or candidate-plan JSON.
3. Resolve `scripts/plan_fidelity_check.py` relative to this `SKILL.md`. Run
   `preflight` before proposing action.
4. Stop on exact contradictions. Present deterministic review findings to the
   human; do not resolve them through completion-oriented inference.
5. Draft a process envelope only after preflight. Run `check-envelope`, show its
   digest, and require explicit human confirmation before that digest enters
   controller-owned state.
6. Treat the confirmed envelope and harness state as read-only authority. If
   they are inside your writable surface, stop and explain that the gate cannot
   enforce its claim.
7. Structure the candidate plan and run `check-plan`. Opaque `step_details` may
   vary, but every authority-bearing field must be declared.
8. Obey the receipt:
   - `accept_current_slice`: hand the current proposed action to action-authority
     checking. Do not execute it here.
   - `repair_plan`: use only the named preapproved repair, then resubmit while
     the external budget remains.
   - `reapproval_required`: stop and present the proposed process change.
   - `stop_for_review`: stop successfully. Do not relabel stopping as failure.
9. Route uncertain state-changing effects to the repository's effect-recovery
   pattern. Do not spend an effect retry as a plan repair.
10. Never persist counters, consume authorization, reuse an exception, promote
    another stage, or begin another slice. Those are controller actions.

## Preserve authority boundaries

- Treat each stage authorization as single use.
- Count current-stage attempts independently of diagnostic error labels.
- Treat one-off exceptions as scoped, expiring, and non-precedential.
- Allow ordinary implementation detail only inside the approved stage, route,
  tools, effect ceiling, budgets, checkpoints, and stopping rules.
- Never automatically rewrite a candidate plan or confirmed envelope.
- Never claim this Skill is a sandbox. It is effective only when the host
  withholds dispatch on a blocking receipt and protects controller state.

## Keep inputs private

Use temporary local files for private instructions. Never copy raw prompts,
logs, personal data, credentials, connector exports, or local paths into a
public fixture or receipt. Receipts should retain digests, logical source IDs,
and line locations rather than full source contents.
