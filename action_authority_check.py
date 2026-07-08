#!/usr/bin/env python3
"""Classify synthetic agent actions before execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ALLOWED = "allowed"
REQUIRES_APPROVAL = "requires_approval"
REQUIRES_HUMAN_CONFIRMATION = "requires_human_confirmation"
REJECTED = "rejected"

PROTECTED_PREFIXES = (
    ".env",
    ".git/",
    ".ssh/",
    "credentials/",
    "private/",
    "secrets/",
)

PRIVATE_WORDS = ("credential", "secret", "private", "token")
DESTRUCTIVE_COMMANDS = ("rm -rf", "git reset --hard", "git clean -fd")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def is_protected_path(path: str) -> bool:
    clean = path.strip().lstrip("./")
    return clean.startswith(PROTECTED_PREFIXES) or any(word in clean.lower() for word in PRIVATE_WORDS)


def is_destructive(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in DESTRUCTIVE_COMMANDS)


def classify(case: dict) -> str:
    action = case.get("action", {})
    action_type = action.get("type")
    paths = action.get("paths", [])
    command = action.get("command", "")

    if action_type == "destructive_command" or is_destructive(command):
        return REJECTED
    if action_type in {"credential_export", "private_export"}:
        return REJECTED
    if any(is_protected_path(path) for path in paths):
        return REJECTED
    if action_type in {"read", "report"}:
        return ALLOWED
    if action_type == "write":
        expected = set(case.get("expected_write_paths", []))
        requested = set(paths)
        if requested and requested <= expected and case.get("explicit_expected_write") is True:
            return ALLOWED
        if requested and requested <= expected:
            return REQUIRES_APPROVAL
        return REJECTED
    if action_type in {"network", "publish"}:
        return REQUIRES_HUMAN_CONFIRMATION
    return REQUIRES_APPROVAL


def load_cases(path: Path) -> list[dict]:
    cases = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"cannot read {path}: {exc}")

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            fail(f"invalid jsonl at line {line_number}: {exc}")
    if not cases:
        fail("no cases found")
    return cases


def run_cases(cases: list[dict]) -> None:
    seen = set()
    for case in cases:
        case_id = case.get("id")
        if not case_id:
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
    return Path(__file__).with_name("examples") / "action_cases.jsonl"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        run_cases(load_cases(default_cases_path()))
        print("PASS self_test")
        return 0
    if len(argv) != 2:
        fail("usage: python3 action_authority_check.py --self-test|examples/action_cases.jsonl")
    run_cases(load_cases(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
