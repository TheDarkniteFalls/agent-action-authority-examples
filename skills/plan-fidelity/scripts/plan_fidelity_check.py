#!/usr/bin/env python3
"""Check instruction and plan fidelity without executing an agent action."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


PREFLIGHT_SCHEMA = "instruction-manifest/v0.1"
ENVELOPE_SCHEMA = "plan-fidelity-envelope/v0.1"
STATE_SCHEMA = "plan-fidelity-state/v0.1"
PLAN_SCHEMA = "candidate-plan/v0.1"
PREFLIGHT_RECEIPT_SCHEMA = "instruction-preflight-receipt/v0.1"
ENVELOPE_RECEIPT_SCHEMA = "plan-envelope-receipt/v0.1"
GATE_RECEIPT_SCHEMA = "plan-fidelity-receipt/v0.1"

ACCEPT = "accept_current_slice"
REPAIR = "repair_plan"
REAPPROVAL = "reapproval_required"
STOP = "stop_for_review"

EVALUATED_AND_ALLOWED = 0
INPUT_ERROR = 1
EVALUATED_AND_HELD = 2

SOURCE_ROLES = {"harness_policy", "instruction", "task", "reference"}
SOURCE_SUFFIXES = {".json", ".md", ".txt"}
MAX_SOURCE_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 256 * 1024
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
EFFECT_RANK = {
    "read": 0,
    "proposal": 1,
    "local_write": 2,
    "external_action": 3,
    "destructive": 4,
}
CONSTRAINT_FIELDS = {
    "effect_ceiling",
    "required_effect",
    "allowed_routes",
    "required_route",
    "stop_after",
    "next_slice",
    "success_boundary",
}
PERMITTED_REPAIRS = {
    "add_effect_recovery_checkpoint",
    "add_required_checkpoint",
    "remove_out_of_scope_step",
    "supply_missing_stage_metadata",
}


class InputError(ValueError):
    """Raised when an input cannot be evaluated deterministically."""


def duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InputError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    try:
        value = json.loads(text, object_pairs_hook=duplicate_safe_object)
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"{path} must contain one JSON object")
    return value


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InputError(f"value is not canonically serializable: {exc}") from exc


def digest_value(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value)).hexdigest()}"


def digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{field} must be a non-empty string")
    return value.strip()


def string_list(value: Any, field: str, *, unique: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise InputError(f"{field} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(nonempty_text(item, f"{field}[{index}]"))
    if unique and len(set(result)) != len(result):
        raise InputError(f"{field} must not contain duplicates")
    return result


def counter(value: Any, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{field} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise InputError(f"{field} must be {qualifier}")
    return value


def reject_unknown_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise InputError(f"{field} has unknown fields: {', '.join(unknown)}")


def stable_finding(
    *, code: str, kind: str, message: str, locations: Iterable[str]
) -> dict[str, Any]:
    normalized_locations = sorted(set(locations))
    finding_id = "pf-" + digest_value(
        {
            "code": code,
            "kind": kind,
            "locations": normalized_locations,
            "message": message,
        }
    ).split(":", 1)[1][:12]
    return {
        "id": finding_id,
        "code": code,
        "kind": kind,
        "locations": normalized_locations,
        "message": message,
    }


def base_negatives() -> dict[str, Any]:
    return {
        "actions_executed": False,
        "model_called": False,
        "network_used": False,
        "promotion_decision": "not_promoted",
        "state_mutating": False,
    }


def load_sources(
    manifest: dict[str, Any], manifest_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    reject_unknown_keys(
        manifest, {"schema_version", "sources", "constraints"}, "manifest"
    )
    if manifest.get("schema_version") != PREFLIGHT_SCHEMA:
        raise InputError(f"manifest schema_version must be {PREFLIGHT_SCHEMA}")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise InputError("manifest.sources must be a non-empty list")
    raw_constraints = manifest.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise InputError("manifest.constraints must be a list")

    root = manifest_dir.resolve()
    source_ids: set[str] = set()
    sources: list[dict[str, Any]] = []
    total_bytes = 0
    for index, raw_source in enumerate(raw_sources):
        field = f"manifest.sources[{index}]"
        if not isinstance(raw_source, dict):
            raise InputError(f"{field} must be an object")
        reject_unknown_keys(raw_source, {"id", "role", "path", "text"}, field)
        source_id = nonempty_text(raw_source.get("id"), f"{field}.id")
        if source_id in source_ids:
            raise InputError(f"duplicate source id: {source_id}")
        source_ids.add(source_id)
        role = nonempty_text(raw_source.get("role"), f"{field}.role")
        if role not in SOURCE_ROLES:
            raise InputError(f"{field}.role must be one of {sorted(SOURCE_ROLES)}")
        has_path = "path" in raw_source
        has_text = "text" in raw_source
        if has_path == has_text:
            raise InputError(f"{field} needs exactly one of path or text")

        if has_text:
            text = raw_source["text"]
            if not isinstance(text, str):
                raise InputError(f"{field}.text must be a string")
            encoded = text.encode("utf-8")
            origin = "inline"
        else:
            path_text = nonempty_text(raw_source["path"], f"{field}.path")
            supplied = Path(path_text)
            if supplied.is_absolute():
                raise InputError(f"{field}.path must be relative to the manifest")
            candidate = root / supplied
            resolved = candidate.resolve()
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise InputError(f"{field}.path escapes the manifest directory") from exc
            if candidate.suffix.lower() not in SOURCE_SUFFIXES:
                raise InputError(f"{field}.path must end in .md, .txt, or .json")
            if not resolved.is_file():
                raise InputError(f"{field}.path is not a readable file")
            try:
                encoded = resolved.read_bytes()
                text = encoded.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise InputError(f"cannot read UTF-8 source {path_text}: {exc}") from exc
            origin = path_text

        if len(encoded) > MAX_SOURCE_BYTES:
            raise InputError(f"{field} exceeds {MAX_SOURCE_BYTES} bytes")
        total_bytes += len(encoded)
        if total_bytes > MAX_BUNDLE_BYTES:
            raise InputError(f"source bundle exceeds {MAX_BUNDLE_BYTES} bytes")
        sources.append(
            {
                "digest": digest_bytes(encoded),
                "id": source_id,
                "origin": origin,
                "role": role,
                "text": text,
            }
        )

    constraints: list[dict[str, Any]] = []
    for index, raw_constraint in enumerate(raw_constraints):
        field = f"manifest.constraints[{index}]"
        if not isinstance(raw_constraint, dict):
            raise InputError(f"{field} must be an object")
        reject_unknown_keys(raw_constraint, {"source_id", "field", "value"}, field)
        source_id = nonempty_text(raw_constraint.get("source_id"), f"{field}.source_id")
        if source_id not in source_ids:
            raise InputError(f"{field}.source_id does not name a supplied source")
        name = nonempty_text(raw_constraint.get("field"), f"{field}.field")
        if name not in CONSTRAINT_FIELDS:
            raise InputError(f"{field}.field must be one of {sorted(CONSTRAINT_FIELDS)}")
        value = raw_constraint.get("value")
        if name in {"effect_ceiling", "required_effect"}:
            value = nonempty_text(value, f"{field}.value")
            if value not in EFFECT_RANK:
                raise InputError(f"{field}.value has an unknown effect class")
        elif name == "allowed_routes":
            value = string_list(value, f"{field}.value")
            if not value:
                raise InputError(f"{field}.value must not be empty")
        elif name == "next_slice":
            value = nonempty_text(value, f"{field}.value")
            if value not in {"automatic", "manual_only"}:
                raise InputError(f"{field}.value must be automatic or manual_only")
        else:
            value = nonempty_text(value, f"{field}.value")
        constraints.append(
            {
                "field": name,
                "location": f"manifest:constraints[{index}]",
                "source_id": source_id,
                "value": value,
            }
        )

    bundle_digest = digest_value(
        {
            "constraints": [
                {key: item[key] for key in ("field", "source_id", "value")}
                for item in constraints
            ],
            "sources": [
                {key: source[key] for key in ("digest", "id", "role")}
                for source in sources
            ],
        }
    )
    return sources, constraints, bundle_digest


def matching_lines(
    sources: list[dict[str, Any]], pattern: re.Pattern[str], roles: set[str] | None = None
) -> list[str]:
    matches: list[str] = []
    for source in sources:
        if roles is not None and source["role"] not in roles:
            continue
        for line_number, line in enumerate(source["text"].splitlines(), start=1):
            if pattern.search(line):
                matches.append(f"{source['id']}:{line_number}")
    return matches


def preflight_manifest(
    manifest: dict[str, Any], manifest_dir: Path
) -> dict[str, Any]:
    sources, constraints, bundle_digest = load_sources(manifest, manifest_dir)
    findings: list[dict[str, Any]] = []

    by_field: dict[str, list[dict[str, Any]]] = {}
    for constraint in constraints:
        by_field.setdefault(constraint["field"], []).append(constraint)

    ceilings = by_field.get("effect_ceiling", [])
    requirements = by_field.get("required_effect", [])
    if ceilings and requirements:
        ceiling = min(ceilings, key=lambda item: EFFECT_RANK[item["value"]])
        required = max(requirements, key=lambda item: EFFECT_RANK[item["value"]])
        if EFFECT_RANK[required["value"]] > EFFECT_RANK[ceiling["value"]]:
            findings.append(
                stable_finding(
                    code="contradictory_instructions",
                    kind="exact",
                    message=(
                        f"required effect {required['value']} exceeds effect ceiling "
                        f"{ceiling['value']}"
                    ),
                    locations=[ceiling["location"], required["location"]],
                )
            )

    allowed_route_sets = [set(item["value"]) for item in by_field.get("allowed_routes", [])]
    if allowed_route_sets:
        allowed_routes = set.intersection(*allowed_route_sets)
        for required in by_field.get("required_route", []):
            if required["value"] not in allowed_routes:
                locations = [required["location"]]
                locations.extend(item["location"] for item in by_field["allowed_routes"])
                findings.append(
                    stable_finding(
                        code="contradictory_instructions",
                        kind="exact",
                        message=f"required route {required['value']} is not allowed",
                        locations=locations,
                    )
                )

    for scalar in ("next_slice", "stop_after"):
        values = by_field.get(scalar, [])
        distinct = {digest_value(item["value"]) for item in values}
        if len(distinct) > 1:
            findings.append(
                stable_finding(
                    code="contradictory_instructions",
                    kind="exact",
                    message=f"supplied instructions disagree about {scalar}",
                    locations=[item["location"] for item in values],
                )
            )

    phrase_sets = {
        "proposal": matching_lines(
            sources, re.compile(r"\bproposal only\b|\bdo not implement\b", re.I)
        ),
        "implementation": matching_lines(
            sources,
            re.compile(r"\bimplement\b|\bmodify files?\b|\bmake (?:the )?changes?\b", re.I),
        ),
        "read_only": matching_lines(
            sources, re.compile(r"\bread[- ]only\b|\bdo not (?:modify|write|change)\b", re.I)
        ),
        "write": matching_lines(
            sources, re.compile(r"\b(?:edit|write|overwrite|delete)\b", re.I)
        ),
        "offline": matching_lines(
            sources, re.compile(r"\bno network\b|\boffline\b|\bnetwork forbidden\b", re.I)
        ),
        "network": matching_lines(
            sources, re.compile(r"\b(?:browse|web lookup|publish|push|upload|network request)\b", re.I)
        ),
        "stop": matching_lines(
            sources, re.compile(r"\bstop (?:after|for|before|when)\b|\bhuman review\b", re.I)
        ),
        "continue": matching_lines(
            sources, re.compile(r"\bcontinue\b|\bnext (?:slice|stage|improvement|task)\b", re.I)
        ),
    }
    phrase_conflicts = [
        ("proposal", "implementation", "scope_escalation", "proposal-only text conflicts with implementation text"),
        ("read_only", "write", "scope_escalation", "read-only text conflicts with write text"),
        ("offline", "network", "route_drift", "offline text conflicts with a network route"),
        ("stop", "continue", "automatic_next_slice", "a stop boundary conflicts with continuation text"),
    ]
    for left, right, code, message in phrase_conflicts:
        if phrase_sets[left] and phrase_sets[right]:
            findings.append(
                stable_finding(
                    code=code,
                    kind="review",
                    message=message,
                    locations=[phrase_sets[left][0], phrase_sets[right][0]],
                )
            )

    unbounded = matching_lines(
        sources,
        re.compile(r"\bkeep trying\b|\bretry until\b|\bdo not stop\b|\bnever give up\b", re.I),
        {"harness_policy", "instruction", "task"},
    )
    bounded = matching_lines(
        sources, re.compile(r"\b(?:budget|limit|stop after|at most|up to)\b|\b\d+\b", re.I)
    )
    if unbounded and not bounded:
        findings.append(
            stable_finding(
                code="unbounded_retry",
                kind="review",
                message="retry language has no visible budget or stop rule",
                locations=[unbounded[0]],
            )
        )

    directive = re.compile(
        r"^\s*(?:please\s+)?(?:upload|delete|run|send|publish|push|edit|install|ignore|continue)\b|\bmust\s+(?:upload|delete|run|send|publish|push|edit|install|ignore|continue)\b",
        re.I,
    )
    for location in matching_lines(sources, directive, {"reference"}):
        findings.append(
            stable_finding(
                code="untrusted_directive",
                kind="review",
                message="directive-like text came from a source labelled as reference data",
                locations=[location],
            )
        )

    completion = matching_lines(
        sources,
        re.compile(r"\b(?:finish|complete|resolve|done)\b", re.I),
        {"harness_policy", "instruction", "task"},
    )
    if completion and not by_field.get("success_boundary"):
        findings.append(
            stable_finding(
                code="missing_success_boundary",
                kind="review",
                message="completion language has no structured success boundary",
                locations=[completion[0]],
            )
        )

    unique = {finding["id"]: finding for finding in findings}
    findings = sorted(unique.values(), key=lambda item: (item["kind"], item["code"], item["id"]))
    has_exact = any(item["kind"] == "exact" for item in findings)
    has_review = any(item["kind"] == "review" for item in findings)
    status = "blocked" if has_exact else "review_required" if has_review else "clear"
    receipt = {
        "authority_granted": False,
        "bundle_digest": bundle_digest,
        "findings": findings,
        "mode": "preflight",
        "schema_version": PREFLIGHT_RECEIPT_SCHEMA,
        "source_digests": [
            {key: source[key] for key in ("digest", "id", "role")} for source in sources
        ],
        "status": status,
    }
    receipt.update(base_negatives())
    return receipt


def validate_stage(raw_stage: Any, index: int) -> dict[str, Any]:
    field = f"envelope.stages[{index}]"
    if not isinstance(raw_stage, dict):
        raise InputError(f"{field} must be an object")
    reject_unknown_keys(raw_stage, {"allowed_tools", "effect_ceiling", "id", "route"}, field)
    stage_id = nonempty_text(raw_stage.get("id"), f"{field}.id")
    route = nonempty_text(raw_stage.get("route"), f"{field}.route")
    tools = string_list(raw_stage.get("allowed_tools"), f"{field}.allowed_tools")
    effect = nonempty_text(raw_stage.get("effect_ceiling"), f"{field}.effect_ceiling")
    if effect not in EFFECT_RANK:
        raise InputError(f"{field}.effect_ceiling has an unknown effect class")
    return {"allowed_tools": tools, "effect_ceiling": effect, "id": stage_id, "route": route}


def validate_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "acknowledged_review_findings",
        "approval_checkpoints",
        "effect_retry_budget",
        "exceptions",
        "goal_id",
        "next_slice",
        "permitted_repairs",
        "plan_failure_budget",
        "plan_id",
        "schema_version",
        "slice_id",
        "source_bundle_digest",
        "stage_attempt_budget",
        "stages",
        "stop_conditions",
        "success_boundary",
    }
    reject_unknown_keys(envelope, allowed, "envelope")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA:
        raise InputError(f"envelope schema_version must be {ENVELOPE_SCHEMA}")
    normalized: dict[str, Any] = {
        "schema_version": ENVELOPE_SCHEMA,
        "plan_id": nonempty_text(envelope.get("plan_id"), "envelope.plan_id"),
        "goal_id": nonempty_text(envelope.get("goal_id"), "envelope.goal_id"),
        "slice_id": nonempty_text(envelope.get("slice_id"), "envelope.slice_id"),
        "source_bundle_digest": nonempty_text(
            envelope.get("source_bundle_digest"), "envelope.source_bundle_digest"
        ),
        "acknowledged_review_findings": string_list(
            envelope.get("acknowledged_review_findings"),
            "envelope.acknowledged_review_findings",
        ),
        "plan_failure_budget": counter(
            envelope.get("plan_failure_budget"), "envelope.plan_failure_budget", positive=True
        ),
        "stage_attempt_budget": counter(
            envelope.get("stage_attempt_budget"), "envelope.stage_attempt_budget", positive=True
        ),
        "effect_retry_budget": counter(
            envelope.get("effect_retry_budget"), "envelope.effect_retry_budget"
        ),
        "permitted_repairs": string_list(
            envelope.get("permitted_repairs"), "envelope.permitted_repairs"
        ),
        "approval_checkpoints": string_list(
            envelope.get("approval_checkpoints"), "envelope.approval_checkpoints"
        ),
        "stop_conditions": string_list(
            envelope.get("stop_conditions"), "envelope.stop_conditions"
        ),
        "success_boundary": nonempty_text(
            envelope.get("success_boundary"), "envelope.success_boundary"
        ),
        "next_slice": nonempty_text(envelope.get("next_slice"), "envelope.next_slice"),
    }
    if not DIGEST_PATTERN.fullmatch(normalized["source_bundle_digest"]):
        raise InputError("envelope.source_bundle_digest must be a sha256 digest")
    unknown_repairs = sorted(set(normalized["permitted_repairs"]) - PERMITTED_REPAIRS)
    if unknown_repairs:
        raise InputError(f"envelope has unknown permitted repairs: {', '.join(unknown_repairs)}")
    if normalized["next_slice"] != "manual_only":
        raise InputError("v0.1 requires envelope.next_slice to be manual_only")

    raw_stages = envelope.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise InputError("envelope.stages must be a non-empty list")
    stages = [validate_stage(item, index) for index, item in enumerate(raw_stages)]
    stage_ids = [stage["id"] for stage in stages]
    if len(set(stage_ids)) != len(stage_ids):
        raise InputError("envelope stage ids must be unique")
    normalized["stages"] = stages

    raw_exceptions = envelope.get("exceptions", [])
    if not isinstance(raw_exceptions, list):
        raise InputError("envelope.exceptions must be a list")
    exceptions: list[dict[str, Any]] = []
    exception_ids: set[str] = set()
    for index, item in enumerate(raw_exceptions):
        field = f"envelope.exceptions[{index}]"
        if not isinstance(item, dict):
            raise InputError(f"{field} must be an object")
        reject_unknown_keys(
            item, {"expires_after_stage", "id", "non_precedent", "scope", "stage_id"}, field
        )
        exception_id = nonempty_text(item.get("id"), f"{field}.id")
        if exception_id in exception_ids:
            raise InputError("envelope exception ids must be unique")
        exception_ids.add(exception_id)
        stage_id = nonempty_text(item.get("stage_id"), f"{field}.stage_id")
        expires_after_stage = nonempty_text(
            item.get("expires_after_stage"), f"{field}.expires_after_stage"
        )
        if stage_id not in stage_ids or expires_after_stage not in stage_ids:
            raise InputError(f"{field} must name stages in the current envelope")
        if item.get("non_precedent") is not True:
            raise InputError(f"{field}.non_precedent must be true")
        exceptions.append(
            {
                "expires_after_stage": expires_after_stage,
                "id": exception_id,
                "non_precedent": True,
                "scope": nonempty_text(item.get("scope"), f"{field}.scope"),
                "stage_id": stage_id,
            }
        )
    normalized["exceptions"] = exceptions
    return normalized


def check_envelope_receipt(
    preflight: dict[str, Any], raw_envelope: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = validate_envelope(raw_envelope)
    findings: list[dict[str, Any]] = []
    exact = [item for item in preflight["findings"] if item["kind"] == "exact"]
    review_ids = {item["id"] for item in preflight["findings"] if item["kind"] == "review"}
    acknowledged = set(envelope["acknowledged_review_findings"])
    if exact:
        findings.extend(exact)
    if envelope["source_bundle_digest"] != preflight["bundle_digest"]:
        findings.append(
            stable_finding(
                code="source_bundle_digest_mismatch",
                kind="exact",
                message="the envelope is bound to a different instruction bundle",
                locations=["envelope:source_bundle_digest"],
            )
        )
    missing_ack = sorted(review_ids - acknowledged)
    stale_ack = sorted(acknowledged - review_ids)
    if missing_ack:
        findings.append(
            stable_finding(
                code="unreviewed_preflight_findings",
                kind="exact",
                message="the envelope does not acknowledge every current review finding",
                locations=["envelope:acknowledged_review_findings"],
            )
        )
    if stale_ack:
        findings.append(
            stable_finding(
                code="stale_review_acknowledgement",
                kind="exact",
                message="the envelope acknowledges findings that are not in the current bundle",
                locations=["envelope:acknowledged_review_findings"],
            )
        )

    plan_digest = digest_value(envelope)
    valid = not findings
    receipt = {
        "envelope_digest": plan_digest,
        "authority_granted": False,
        "decision": "ready_for_human_confirmation" if valid else STOP,
        "findings": sorted(findings, key=lambda item: (item["code"], item["id"])),
        "mode": "check-envelope",
        "reason": (
            "envelope is structurally valid and ready for external human confirmation"
            if valid
            else "envelope cannot be confirmed until the findings are resolved"
        ),
        "schema_version": ENVELOPE_RECEIPT_SCHEMA,
        "source_bundle_digest": preflight["bundle_digest"],
        "status": "valid_for_confirmation" if valid else "blocked",
    }
    receipt.update(base_negatives())
    return receipt, envelope


def validate_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "active_exception_ids",
        "approved_plan_digest",
        "authorization_id",
        "authorization_status",
        "current_stage",
        "effect_retries_used",
        "failure_labels",
        "plan_failures_used",
        "schema_version",
        "stage_attempts_used",
    }
    reject_unknown_keys(raw_state, allowed, "state")
    if raw_state.get("schema_version") != STATE_SCHEMA:
        raise InputError(f"state schema_version must be {STATE_SCHEMA}")
    digest = nonempty_text(raw_state.get("approved_plan_digest"), "state.approved_plan_digest")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise InputError("state.approved_plan_digest must be a sha256 digest")
    status = nonempty_text(raw_state.get("authorization_status"), "state.authorization_status")
    if status not in {"consumed", "issued"}:
        raise InputError("state.authorization_status must be issued or consumed")
    return {
        "active_exception_ids": string_list(
            raw_state.get("active_exception_ids", []), "state.active_exception_ids"
        ),
        "approved_plan_digest": digest,
        "authorization_id": nonempty_text(
            raw_state.get("authorization_id"), "state.authorization_id"
        ),
        "authorization_status": status,
        "current_stage": nonempty_text(raw_state.get("current_stage"), "state.current_stage"),
        "effect_retries_used": counter(
            raw_state.get("effect_retries_used"), "state.effect_retries_used"
        ),
        "failure_labels": string_list(
            raw_state.get("failure_labels", []), "state.failure_labels", unique=False
        ),
        "plan_failures_used": counter(
            raw_state.get("plan_failures_used"), "state.plan_failures_used"
        ),
        "schema_version": STATE_SCHEMA,
        "stage_attempts_used": counter(
            raw_state.get("stage_attempts_used"), "state.stage_attempts_used"
        ),
    }


def validate_candidate(raw_plan: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "approval_checkpoints",
        "authorization_id",
        "current_stage",
        "effect",
        "effect_recovery_checkpoint",
        "effect_retry_budget",
        "effect_retry_requested",
        "goal_id",
        "next_slice",
        "ordered_stages",
        "plan_failure_budget",
        "plan_id",
        "requested_process_changes",
        "requests_next_stage",
        "route",
        "schema_version",
        "slice_id",
        "stage_attempt_budget",
        "step_details",
        "stop_condition_reached",
        "stop_conditions",
        "success_boundary",
        "tools",
        "uses_exceptions",
    }
    reject_unknown_keys(raw_plan, allowed, "plan")
    if raw_plan.get("schema_version") != PLAN_SCHEMA:
        raise InputError(f"plan schema_version must be {PLAN_SCHEMA}")
    normalized = dict(raw_plan)
    for field in ("plan_id", "goal_id", "slice_id", "current_stage", "authorization_id"):
        normalized[field] = nonempty_text(raw_plan.get(field), f"plan.{field}")
    for field in (
        "approval_checkpoints",
        "ordered_stages",
        "requested_process_changes",
        "stop_conditions",
        "tools",
        "uses_exceptions",
    ):
        if field in raw_plan:
            normalized[field] = string_list(raw_plan[field], f"plan.{field}")
    for field in ("effect_retry_requested", "effect_recovery_checkpoint", "requests_next_stage"):
        if field in raw_plan and not isinstance(raw_plan[field], bool):
            raise InputError(f"plan.{field} must be true or false")
    if "stop_condition_reached" in raw_plan and raw_plan["stop_condition_reached"] is not None:
        normalized["stop_condition_reached"] = nonempty_text(
            raw_plan["stop_condition_reached"], "plan.stop_condition_reached"
        )
    for field in ("plan_failure_budget", "stage_attempt_budget", "effect_retry_budget"):
        if field in raw_plan:
            normalized[field] = counter(raw_plan[field], f"plan.{field}")
    if "effect" in raw_plan:
        normalized["effect"] = nonempty_text(raw_plan["effect"], "plan.effect")
        if normalized["effect"] not in EFFECT_RANK:
            raise InputError("plan.effect has an unknown effect class")
    for field in ("route", "next_slice", "success_boundary"):
        if field in raw_plan:
            normalized[field] = nonempty_text(raw_plan[field], f"plan.{field}")
    return normalized


def remaining(budget: int, used: int) -> int:
    return max(0, budget - used)


def gate_receipt(
    *,
    envelope: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    preflight: dict[str, Any],
    decision: str,
    reason_code: str,
    reason: str,
    findings: list[dict[str, Any]],
    plan_failures_after: int | None = None,
    required_next_gate: str = "none",
) -> dict[str, Any]:
    before_plan = state["plan_failures_used"]
    after_plan = before_plan if plan_failures_after is None else plan_failures_after
    dispatch_allowed = decision == ACCEPT
    terminal_state = {
        ACCEPT: "accepted_current_slice",
        REPAIR: "awaiting_repaired_plan",
        REAPPROVAL: "awaiting_external_reapproval",
        STOP: "stopped_for_review",
    }[decision]
    proposed_state = {
        "active_exception_ids": state["active_exception_ids"],
        "approved_plan_digest": state["approved_plan_digest"],
        "authorization_id": state["authorization_id"],
        "authorization_status": state["authorization_status"],
        "current_stage": state["current_stage"],
        "effect_retries_used": state["effect_retries_used"],
        "failure_labels": state["failure_labels"],
        "plan_failures_used": after_plan,
        "schema_version": STATE_SCHEMA,
        "stage_attempts_used": state["stage_attempts_used"],
    }
    receipt = {
        "approved_plan_digest": digest_value(envelope),
        "approved_route": next(
            stage["route"] for stage in envelope["stages"] if stage["id"] == state["current_stage"]
        ),
        "authorization_id": state["authorization_id"],
        "budgets": {
            "effect_retries": {
                "after": state["effect_retries_used"],
                "before": state["effect_retries_used"],
                "budget": envelope["effect_retry_budget"],
                "remaining": remaining(
                    envelope["effect_retry_budget"], state["effect_retries_used"]
                ),
            },
            "plan_failures": {
                "after": after_plan,
                "before": before_plan,
                "budget": envelope["plan_failure_budget"],
                "remaining": remaining(envelope["plan_failure_budget"], after_plan),
            },
            "stage_attempts": {
                "after": state["stage_attempts_used"],
                "before": state["stage_attempts_used"],
                "budget": envelope["stage_attempt_budget"],
                "remaining": remaining(
                    envelope["stage_attempt_budget"], state["stage_attempts_used"]
                ),
            },
        },
        "current_stage": state["current_stage"],
        "decision": decision,
        "dispatch_allowed": dispatch_allowed,
        "findings": sorted(findings, key=lambda item: (item["code"], item["id"])),
        "gate_status": "completed",
        "mode": "check-plan",
        "plan_id": envelope["plan_id"],
        "proposed_route": plan.get("route", "missing"),
        "proposed_state": proposed_state,
        "reason": reason,
        "reason_code": reason_code,
        "required_next_gate": required_next_gate,
        "schema_version": GATE_RECEIPT_SCHEMA,
        "source_bundle_digest": preflight["bundle_digest"],
        "state_persisted": False,
        "terminal_state": terminal_state,
    }
    receipt.update(base_negatives())
    return receipt


def one_finding(code: str, message: str, location: str = "plan") -> dict[str, Any]:
    return stable_finding(code=code, kind="exact", message=message, locations=[location])


def is_subsequence(proposed: list[str], expected: list[str]) -> bool:
    iterator = iter(expected)
    return all(any(item == expected_item for expected_item in iterator) for item in proposed)


def evaluate_plan(
    preflight: dict[str, Any],
    raw_envelope: dict[str, Any],
    raw_state: dict[str, Any],
    raw_plan: dict[str, Any],
) -> dict[str, Any]:
    envelope_receipt, envelope = check_envelope_receipt(preflight, raw_envelope)
    state = validate_state(raw_state)
    plan = validate_candidate(raw_plan)

    if envelope_receipt["status"] != "valid_for_confirmation":
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=STOP,
            reason_code="instruction_or_envelope_conflict",
            reason="instruction preflight or envelope confirmation is blocked",
            findings=envelope_receipt["findings"],
        )

    approved_digest = digest_value(envelope)
    if state["approved_plan_digest"] != approved_digest:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REAPPROVAL,
            reason_code="approved_plan_digest_mismatch",
            reason="controller state is bound to a different approved plan",
            findings=[
                one_finding(
                    "process_change", "approved plan digest differs from controller state", "state"
                )
            ],
        )

    stage_ids = [stage["id"] for stage in envelope["stages"]]
    if state["current_stage"] not in stage_ids:
        raise InputError("state.current_stage is not in the approved envelope")
    exception_map = {item["id"]: item for item in envelope["exceptions"]}
    unknown_active = sorted(set(state["active_exception_ids"]) - set(exception_map))
    if unknown_active:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REAPPROVAL,
            reason_code="untrusted_prior_exception",
            reason="controller state references an exception outside the current approved plan",
            findings=[
                one_finding(
                    "process_change", "a prior exception cannot become present authority", "state"
                )
            ],
        )
    for exception_id in state["active_exception_ids"]:
        if exception_map[exception_id]["stage_id"] != state["current_stage"]:
            return gate_receipt(
                envelope=envelope,
                state=state,
                plan=plan,
                preflight=preflight,
                decision=REAPPROVAL,
                reason_code="exception_outside_stage",
                reason="the exception is not scoped to the current authorized stage",
                findings=[one_finding("process_change", "exception scope does not match stage", "state")],
            )

    if state["authorization_status"] != "issued":
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REAPPROVAL,
            reason_code="stage_authorization_consumed",
            reason="the single-use stage authorization is already consumed",
            findings=[one_finding("automatic_next_slice", "stage authorization is not reusable", "state")],
        )
    if state["stage_attempts_used"] >= envelope["stage_attempt_budget"]:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=STOP,
            reason_code="stage_attempt_budget_exhausted",
            reason="the current stage has used its externally counted attempt budget",
            findings=[
                one_finding(
                    "unbounded_retry",
                    "different failure labels do not reset the current-stage attempt budget",
                    "state",
                )
            ],
        )
    if state["plan_failures_used"] >= envelope["plan_failure_budget"]:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=STOP,
            reason_code="plan_failure_budget_exhausted",
            reason="the plan-generation failure budget is already exhausted",
            findings=[one_finding("unbounded_retry", "no more candidate plans are authorized", "state")],
        )

    identity_pairs = [
        ("plan_id", envelope["plan_id"]),
        ("goal_id", envelope["goal_id"]),
        ("slice_id", envelope["slice_id"]),
        ("current_stage", state["current_stage"]),
        ("authorization_id", state["authorization_id"]),
    ]
    process_findings: list[dict[str, Any]] = []
    for field, expected in identity_pairs:
        if plan[field] != expected:
            process_findings.append(
                one_finding("process_change", f"plan.{field} differs from controller authority")
            )

    if plan.get("requested_process_changes", []):
        process_findings.append(
            one_finding("process_change", "the candidate explicitly requests a process change")
        )
    if plan.get("requests_next_stage", False):
        process_findings.append(
            one_finding(
                "automatic_next_slice", "the candidate requests a stage not authorized by the controller"
            )
        )

    expected_stages = stage_ids
    proposed_stages = plan.get("ordered_stages")
    repair_findings: list[dict[str, Any]] = []
    repair_kinds: set[str] = set()
    if proposed_stages is None:
        repair_findings.append(
            one_finding("missing_stage_metadata", "candidate omits the approved stage order")
        )
        repair_kinds.add("supply_missing_stage_metadata")
    elif proposed_stages != expected_stages:
        if set(proposed_stages).issubset(expected_stages) and is_subsequence(
            proposed_stages, expected_stages
        ):
            repair_findings.append(
                one_finding("missing_required_stage", "candidate omits an approved required stage")
            )
            repair_kinds.add("add_required_checkpoint")
        else:
            process_findings.append(
                one_finding("process_change", "candidate changes or reorders approved stages")
            )

    current_stage = next(item for item in envelope["stages"] if item["id"] == state["current_stage"])
    if "route" not in plan:
        repair_findings.append(one_finding("missing_stage_metadata", "candidate omits its route"))
        repair_kinds.add("supply_missing_stage_metadata")
    elif plan["route"] != current_stage["route"]:
        process_findings.append(one_finding("route_drift", "candidate changes the approved route"))

    if "tools" not in plan:
        repair_findings.append(one_finding("missing_stage_metadata", "candidate omits its tools"))
        repair_kinds.add("supply_missing_stage_metadata")
    else:
        extra_tools = sorted(set(plan["tools"]) - set(current_stage["allowed_tools"]))
        if extra_tools:
            repair_findings.append(
                one_finding(
                    "scope_escalation", f"candidate uses tools outside the stage: {', '.join(extra_tools)}"
                )
            )
            repair_kinds.add("remove_out_of_scope_step")

    if "effect" not in plan:
        repair_findings.append(one_finding("missing_stage_metadata", "candidate omits its effect"))
        repair_kinds.add("supply_missing_stage_metadata")
    elif EFFECT_RANK[plan["effect"]] > EFFECT_RANK[current_stage["effect_ceiling"]]:
        repair_findings.append(
            one_finding("scope_escalation", "candidate effect exceeds the approved stage ceiling")
        )
        repair_kinds.add("remove_out_of_scope_step")

    exact_process_fields = [
        ("plan_failure_budget", envelope["plan_failure_budget"]),
        ("stage_attempt_budget", envelope["stage_attempt_budget"]),
        ("effect_retry_budget", envelope["effect_retry_budget"]),
        ("approval_checkpoints", envelope["approval_checkpoints"]),
        ("stop_conditions", envelope["stop_conditions"]),
        ("success_boundary", envelope["success_boundary"]),
        ("next_slice", envelope["next_slice"]),
    ]
    for field, expected in exact_process_fields:
        if field not in plan:
            repair_findings.append(
                one_finding("missing_stage_metadata", f"candidate omits {field}")
            )
            repair_kinds.add("supply_missing_stage_metadata")
        elif plan[field] != expected:
            code = "automatic_next_slice" if field == "next_slice" else "process_change"
            process_findings.append(one_finding(code, f"candidate changes approved {field}"))

    used_exceptions = set(plan.get("uses_exceptions", []))
    if not used_exceptions.issubset(set(state["active_exception_ids"])):
        process_findings.append(
            one_finding(
                "process_change", "candidate relies on an exception not active in controller state"
            )
        )

    reached = plan.get("stop_condition_reached")
    if reached is not None:
        if reached not in envelope["stop_conditions"]:
            process_findings.append(
                one_finding("process_change", "candidate claims an unapproved stop condition")
            )
        else:
            return gate_receipt(
                envelope=envelope,
                state=state,
                plan=plan,
                preflight=preflight,
                decision=STOP,
                reason_code="approved_stop_condition_reached",
                reason=f"approved stop condition reached: {reached}",
                findings=[one_finding("successful_stop", "the authorized work stopped correctly")],
            )

    if process_findings:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REAPPROVAL,
            reason_code="process_change_requested",
            reason="candidate changes controller-owned process authority",
            findings=process_findings,
        )

    effect_retry_requested = plan.get("effect_retry_requested", False)
    required_next_gate = "action_authority"
    if effect_retry_requested:
        if state["effect_retries_used"] >= envelope["effect_retry_budget"]:
            return gate_receipt(
                envelope=envelope,
                state=state,
                plan=plan,
                preflight=preflight,
                decision=STOP,
                reason_code="effect_retry_budget_exhausted",
                reason="the effect-retry budget is exhausted",
                findings=[one_finding("unbounded_retry", "another effect retry is not authorized")],
            )
        if plan.get("effect_recovery_checkpoint") is not True:
            repair_findings.append(
                one_finding(
                    "ambiguous_effect_retry",
                    "state-changing retry must first pass through effect recovery",
                )
            )
            repair_kinds.add("add_effect_recovery_checkpoint")
        else:
            required_next_gate = "effect_recovery"

    unsupported_repairs = sorted(repair_kinds - set(envelope["permitted_repairs"]))
    if unsupported_repairs:
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REAPPROVAL,
            reason_code="repair_not_preapproved",
            reason="candidate needs a repair kind not preapproved by the envelope",
            findings=repair_findings,
        )
    if repair_findings:
        failures_after = state["plan_failures_used"] + 1
        if failures_after >= envelope["plan_failure_budget"]:
            return gate_receipt(
                envelope=envelope,
                state=state,
                plan=plan,
                preflight=preflight,
                decision=STOP,
                reason_code="plan_failure_budget_exhausted",
                reason="this repairable candidate exhausts the plan-failure budget",
                findings=repair_findings,
                plan_failures_after=failures_after,
            )
        return gate_receipt(
            envelope=envelope,
            state=state,
            plan=plan,
            preflight=preflight,
            decision=REPAIR,
            reason_code="authority_preserving_repair_available",
            reason="candidate may be resubmitted with only the named preapproved repairs",
            findings=repair_findings,
            plan_failures_after=failures_after,
        )

    return gate_receipt(
        envelope=envelope,
        state=state,
        plan=plan,
        preflight=preflight,
        decision=ACCEPT,
        reason_code="current_slice_matches_approved_process",
        reason="candidate stays inside the current stage and approved process",
        findings=[],
        required_next_gate=required_next_gate,
    )


def render_preflight(receipt: dict[str, Any]) -> str:
    heading = {
        "blocked": "REVIEW REQUIRED",
        "clear": "PREFLIGHT CLEAR",
        "review_required": "REVIEW REQUIRED",
    }[receipt["status"]]
    lines = [heading, ""]
    if receipt["findings"]:
        for index, finding in enumerate(receipt["findings"], start=1):
            lines.append(f"{index}. {finding['code']}: {finding['message']}")
            lines.append(f"   Locations: {', '.join(finding['locations'])}")
    else:
        lines.append("No deterministic contradictions or review findings were found.")
    lines.extend(
        [
            "",
            f"Bundle digest: {receipt['bundle_digest']}",
            "Authority granted: no",
            "Nothing was executed. No network or model was used.",
        ]
    )
    return "\n".join(lines)


def render_envelope(receipt: dict[str, Any]) -> str:
    if receipt["status"] == "valid_for_confirmation":
        return "\n".join(
            [
                "ENVELOPE READY FOR HUMAN CONFIRMATION",
                "",
                f"Envelope digest: {receipt['envelope_digest']}",
                "Store this digest in controller-owned state only after confirmation.",
                "Authority granted: no",
                "Nothing was executed. No network or model was used.",
            ]
        )
    lines = ["REVIEW REQUIRED", ""]
    for index, finding in enumerate(receipt["findings"], start=1):
        lines.append(f"{index}. {finding['code']}: {finding['message']}")
    lines.extend(["", "Authority granted: no", "Nothing was executed."])
    return "\n".join(lines)


def render_gate(receipt: dict[str, Any]) -> str:
    heading = {
        ACCEPT: "ACCEPT CURRENT SLICE",
        REPAIR: "REPAIR PLAN",
        REAPPROVAL: "REAPPROVAL REQUIRED",
        STOP: "STOP FOR REVIEW",
    }[receipt["decision"]]
    budgets = receipt["budgets"]
    lines = [heading, "", receipt["reason"]]
    for finding in receipt["findings"]:
        lines.append(f"- {finding['code']}: {finding['message']}")
    lines.extend(
        [
            "",
            (
                "Plan failures: "
                f"{budgets['plan_failures']['after']}/{budgets['plan_failures']['budget']}"
            ),
            (
                "Stage attempts: "
                f"{budgets['stage_attempts']['after']}/{budgets['stage_attempts']['budget']}"
            ),
            (
                "Effect retries: "
                f"{budgets['effect_retries']['after']}/{budgets['effect_retries']['budget']}"
            ),
            f"Next gate: {receipt['required_next_gate']}",
            "State persisted: no",
            "Nothing was executed. The next slice was not promoted.",
        ]
    )
    return "\n".join(lines)


def clear_manifest() -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "sources": [
            {
                "id": "policy",
                "role": "harness_policy",
                "text": "Prepare a proposal only. Stop for human review.",
            },
            {
                "id": "task",
                "role": "task",
                "text": "Complete a synthetic proposal using local fixtures.",
            },
        ],
        "constraints": [
            {"source_id": "policy", "field": "effect_ceiling", "value": "proposal"},
            {"source_id": "task", "field": "required_effect", "value": "proposal"},
            {"source_id": "policy", "field": "allowed_routes", "value": ["local_files"]},
            {"source_id": "task", "field": "required_route", "value": "local_files"},
            {"source_id": "policy", "field": "next_slice", "value": "manual_only"},
            {"source_id": "policy", "field": "stop_after", "value": "human_review"},
            {
                "source_id": "task",
                "field": "success_boundary",
                "value": "synthetic proposal ready for review",
            },
        ],
    }


def self_test() -> None:
    preflight = preflight_manifest(clear_manifest(), Path.cwd())
    if preflight["status"] != "clear":
        raise AssertionError("clear manifest did not pass preflight")
    envelope = {
        "schema_version": ENVELOPE_SCHEMA,
        "plan_id": "synthetic-proposal-v1",
        "goal_id": "synthetic-goal",
        "slice_id": "proposal-slice",
        "source_bundle_digest": preflight["bundle_digest"],
        "acknowledged_review_findings": [],
        "stages": [
            {
                "id": "inspect",
                "route": "local_files",
                "allowed_tools": ["read_file"],
                "effect_ceiling": "read",
            },
            {
                "id": "review",
                "route": "human_review",
                "allowed_tools": [],
                "effect_ceiling": "proposal",
            },
        ],
        "plan_failure_budget": 2,
        "stage_attempt_budget": 2,
        "effect_retry_budget": 0,
        "permitted_repairs": sorted(PERMITTED_REPAIRS),
        "approval_checkpoints": ["before_state_change", "before_external_action"],
        "stop_conditions": ["human_review_reached", "budget_exhausted"],
        "success_boundary": "synthetic proposal ready for review",
        "next_slice": "manual_only",
        "exceptions": [],
    }
    _, normalized_envelope = check_envelope_receipt(preflight, envelope)
    plan_digest = digest_value(normalized_envelope)
    state = {
        "schema_version": STATE_SCHEMA,
        "approved_plan_digest": plan_digest,
        "authorization_id": "auth-inspect-001",
        "authorization_status": "issued",
        "current_stage": "inspect",
        "plan_failures_used": 0,
        "stage_attempts_used": 0,
        "effect_retries_used": 0,
        "failure_labels": [],
        "active_exception_ids": [],
    }
    plan = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": envelope["plan_id"],
        "goal_id": envelope["goal_id"],
        "slice_id": envelope["slice_id"],
        "authorization_id": state["authorization_id"],
        "current_stage": state["current_stage"],
        "ordered_stages": ["inspect", "review"],
        "route": "local_files",
        "tools": ["read_file"],
        "effect": "read",
        "plan_failure_budget": 2,
        "stage_attempt_budget": 2,
        "effect_retry_budget": 0,
        "approval_checkpoints": envelope["approval_checkpoints"],
        "stop_conditions": envelope["stop_conditions"],
        "success_boundary": envelope["success_boundary"],
        "next_slice": "manual_only",
        "requested_process_changes": [],
        "requests_next_stage": False,
        "uses_exceptions": [],
        "effect_retry_requested": False,
        "effect_recovery_checkpoint": False,
        "step_details": ["Inspect named synthetic inputs."],
    }

    cases: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], str]] = []
    cases.append(("accepted_current_stage", envelope, state, plan, ACCEPT))

    missing = copy.deepcopy(plan)
    missing.pop("ordered_stages")
    cases.append(("missing_metadata_repairs", envelope, state, missing, REPAIR))

    exhausted_state = copy.deepcopy(state)
    exhausted_state["plan_failures_used"] = 1
    cases.append(("second_plan_failure_stops", envelope, exhausted_state, missing, STOP))

    stage_exhausted = copy.deepcopy(state)
    stage_exhausted["stage_attempts_used"] = 2
    stage_exhausted["failure_labels"] = ["environment_error", "schema_error"]
    cases.append(("different_labels_same_stage_stop", envelope, stage_exhausted, plan, STOP))

    process_change = copy.deepcopy(plan)
    process_change["requested_process_changes"] = ["create_adapter"]
    cases.append(("adapter_requires_reapproval", envelope, state, process_change, REAPPROVAL))

    stale_state = copy.deepcopy(state)
    stale_state["approved_plan_digest"] = "sha256:" + "0" * 64
    cases.append(("digest_mismatch_reapproves", envelope, stale_state, plan, REAPPROVAL))

    review_state = copy.deepcopy(state)
    review_state["authorization_id"] = "auth-review-001"
    review_state["current_stage"] = "review"
    review_plan = copy.deepcopy(plan)
    review_plan.update(
        {
            "authorization_id": "auth-review-001",
            "current_stage": "review",
            "route": "human_review",
            "tools": [],
            "effect": "proposal",
            "stop_condition_reached": "human_review_reached",
        }
    )
    cases.append(("review_checkpoint_is_successful_stop", envelope, review_state, review_plan, STOP))

    promotion = copy.deepcopy(plan)
    promotion["requests_next_stage"] = True
    cases.append(("next_stage_not_automatic", envelope, state, promotion, REAPPROVAL))

    prior_exception = copy.deepcopy(state)
    prior_exception["active_exception_ids"] = ["old-one-off"]
    cases.append(("one_off_is_not_precedent", envelope, prior_exception, plan, REAPPROVAL))

    retry_envelope = copy.deepcopy(envelope)
    retry_envelope["effect_retry_budget"] = 1
    retry_envelope["stages"][0]["effect_ceiling"] = "external_action"
    _, retry_normalized = check_envelope_receipt(preflight, retry_envelope)
    retry_state = copy.deepcopy(state)
    retry_state["approved_plan_digest"] = digest_value(retry_normalized)
    retry_plan = copy.deepcopy(plan)
    retry_plan.update(
        {
            "effect": "external_action",
            "effect_retry_budget": 1,
            "effect_retry_requested": True,
            "effect_recovery_checkpoint": True,
        }
    )
    cases.append(("uncertain_effect_routes_to_recovery", retry_envelope, retry_state, retry_plan, ACCEPT))

    for case_id, case_envelope, case_state, case_plan, expected in cases:
        first = evaluate_plan(preflight, case_envelope, case_state, case_plan)
        second = evaluate_plan(preflight, case_envelope, case_state, case_plan)
        if first != second:
            raise AssertionError(f"{case_id} is not deterministic")
        if first["decision"] != expected:
            raise AssertionError(
                f"{case_id} expected {expected}, got {first['decision']}"
            )
        if case_id == "uncertain_effect_routes_to_recovery":
            if first["required_next_gate"] != "effect_recovery":
                raise AssertionError("uncertain effect did not route to effect recovery")
        print(f"PASS {case_id} {first['decision']}")

    conflict = clear_manifest()
    conflict["sources"][1]["text"] = "Implement and publish the result, then continue."
    conflict["constraints"][1]["value"] = "external_action"
    conflict_receipt = preflight_manifest(conflict, Path.cwd())
    if conflict_receipt["status"] != "blocked":
        raise AssertionError("contradictory manifest did not block")
    print("PASS contradictory_instructions_block preflight")

    skill_root = Path(__file__).resolve().parents[1]
    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    metadata_text = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
    if not skill_text.startswith("---\nname: plan-fidelity\n"):
        raise AssertionError("skill frontmatter is malformed")
    if "allow_implicit_invocation: false" not in metadata_text:
        raise AssertionError("skill must require explicit invocation")
    if "$plan-fidelity" not in metadata_text:
        raise AssertionError("skill default prompt must name $plan-fidelity")
    print("PASS skill_package explicit_invocation")
    print("PASS self_test")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subparsers.add_parser("preflight", help="check instruction sources")
    preflight_parser.add_argument("manifest", type=Path)
    preflight_parser.add_argument("--json", action="store_true", dest="as_json")

    envelope_parser = subparsers.add_parser(
        "check-envelope", help="validate and digest a human-reviewable envelope"
    )
    envelope_parser.add_argument("manifest", type=Path)
    envelope_parser.add_argument("envelope", type=Path)
    envelope_parser.add_argument("--json", action="store_true", dest="as_json")

    plan_parser = subparsers.add_parser("check-plan", help="compare a plan with controller state")
    plan_parser.add_argument("manifest", type=Path)
    plan_parser.add_argument("envelope", type=Path)
    plan_parser.add_argument("state", type=Path)
    plan_parser.add_argument("plan", type=Path)
    plan_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def print_result(value: dict[str, Any], as_json: bool, renderer: Any) -> None:
    if as_json:
        print(canonical_json(value).decode("utf-8"))
    else:
        print(renderer(value))


def main(argv: list[str]) -> int:
    if argv[1:] == ["--self-test"]:
        self_test()
        return EVALUATED_AND_ALLOWED
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    try:
        manifest = load_json(args.manifest)
        preflight = preflight_manifest(manifest, args.manifest.resolve().parent)
        if args.command == "preflight":
            print_result(preflight, args.as_json, render_preflight)
            return EVALUATED_AND_ALLOWED if preflight["status"] == "clear" else EVALUATED_AND_HELD

        raw_envelope = load_json(args.envelope)
        if args.command == "check-envelope":
            receipt, _ = check_envelope_receipt(preflight, raw_envelope)
            print_result(receipt, args.as_json, render_envelope)
            return (
                EVALUATED_AND_ALLOWED
                if receipt["status"] == "valid_for_confirmation"
                else EVALUATED_AND_HELD
            )

        raw_state = load_json(args.state)
        raw_plan = load_json(args.plan)
        receipt = evaluate_plan(preflight, raw_envelope, raw_state, raw_plan)
        print_result(receipt, args.as_json, render_gate)
        return EVALUATED_AND_ALLOWED if receipt["decision"] == ACCEPT else EVALUATED_AND_HELD
    except InputError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return INPUT_ERROR


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
