#!/usr/bin/env python3
"""Check synthetic reusable approval grants without executing a tool."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APPROVED_WITHIN_SCOPE = "approved_within_scope"
REQUIRES_APPROVAL = "requires_approval"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def canonical_scope(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return None


def nonempty_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def classify(case: dict[str, Any]) -> str:
    """Return approval reuse only for an exact, live, explicitly approved scope."""

    call = case.get("call")
    grant = case.get("grant")
    now = parse_timestamp(case.get("now"))

    if not isinstance(call, dict) or not isinstance(grant, dict) or now is None:
        return REQUIRES_APPROVAL
    if grant.get("decision") != "approved":
        return REQUIRES_APPROVAL

    call_tool = nonempty_text(call.get("tool"))
    grant_tool = nonempty_text(grant.get("tool"))
    call_namespace = nonempty_text(call.get("namespace"))
    grant_namespace = nonempty_text(grant.get("namespace"))
    call_scope = canonical_scope(call.get("scope"))
    grant_scope = canonical_scope(grant.get("scope"))
    expires_at = parse_timestamp(grant.get("expires_at"))

    if None in {
        call_tool,
        grant_tool,
        call_namespace,
        grant_namespace,
        call_scope,
        grant_scope,
        expires_at,
    }:
        return REQUIRES_APPROVAL
    if now >= expires_at:
        return REQUIRES_APPROVAL
    if call_tool != grant_tool or call_namespace != grant_namespace:
        return REQUIRES_APPROVAL
    if call_scope != grant_scope:
        return REQUIRES_APPROVAL
    return APPROVED_WITHIN_SCOPE


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"invalid jsonl at line {line_number}: {exc}")
        if not isinstance(value, dict):
            fail(f"case at line {line_number} must be an object")
        cases.append(value)
    if not cases:
        fail("no cases found")
    return cases


def run_cases(cases: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            fail("case missing id")
        if case_id in seen:
            fail(f"duplicate case id {case_id}")
        seen.add(case_id)

        decision = classify(case)
        expected = case.get("expected_decision")
        if decision != expected:
            fail(f"{case_id} expected {expected}, got {decision}")
        print(f"PASS {case_id} {decision}")


def default_cases_path() -> Path:
    return Path(__file__).with_name("examples") / "scoped_approval_cases.jsonl"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        run_cases(load_cases(default_cases_path()))
        print("PASS self_test")
        return 0
    if len(argv) != 2:
        fail("usage: python3 scoped_approval_check.py --self-test|examples/scoped_approval_cases.jsonl")
    run_cases(load_cases(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
