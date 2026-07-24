#!/usr/bin/env python3
"""Choose a safe recovery action after an ambiguous synthetic tool outcome."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EXECUTE_ONCE = "execute_once"
RETURN_RECORDED_RESULT = "return_recorded_result"
REJECT_KEY_REUSE = "reject_idempotency_key_reuse"
RECORD_OBSERVED_SUCCESS = "record_observed_success"
RETRY_SAME_OPERATION_ID = "retry_same_operation_id"
WAIT_FOR_CURRENT_ATTEMPT = "wait_for_current_attempt"
STOP_FOR_REVIEW = "stop_for_review"

KNOWN_RECORD_STATES = {
    "committed",
    "failed_before_effect",
    "in_flight",
    "outcome_unknown",
}
DECISIVE_READ_BACK_STATES = {"effect_present", "effect_absent"}
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def fail(message: str) -> None:
    raise SystemExit(f"FAIL {message}")


def nonempty_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def request_digest(tool: str, arguments: dict[str, Any]) -> str | None:
    payload = {"arguments": arguments, "tool": tool}
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def receipt(
    *,
    case_id: Any,
    operation_id: str | None,
    digest: str | None,
    prior_state: str,
    read_back_status: str,
    decision: str,
    reason: str,
    result: Any = None,
) -> dict[str, Any]:
    value = {
        "schema_version": "effect-recovery/v0.1",
        "case_id": case_id,
        "operation_id": operation_id,
        "request_digest": digest,
        "prior_state": prior_state,
        "read_back_status": read_back_status,
        "decision": decision,
        "reason": reason,
    }
    if result is not None:
        value["result"] = result
    return value


def evaluate(case: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic recovery receipt without executing the tool."""

    case_id = case.get("id")
    operation = case.get("operation")
    if not isinstance(operation, dict):
        return receipt(
            case_id=case_id,
            operation_id=None,
            digest=None,
            prior_state="invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="operation is missing or malformed",
        )

    operation_id = nonempty_text(operation.get("id"))
    tool = nonempty_text(operation.get("tool"))
    arguments = operation.get("arguments")
    if operation_id is None or tool is None or not isinstance(arguments, dict):
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=None,
            prior_state="invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="operation needs a stable id, tool name, and arguments object",
        )

    digest = request_digest(tool, arguments)
    if digest is None:
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=None,
            prior_state="invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="operation arguments are not canonically serializable",
        )

    record = case.get("record")
    if record is None:
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state="none",
            read_back_status="not_checked",
            decision=EXECUTE_ONCE,
            reason="no prior attempt is recorded for this operation id",
        )
    if not isinstance(record, dict):
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state="invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="prior attempt record is malformed",
        )

    record_id = nonempty_text(record.get("operation_id"))
    record_digest = nonempty_text(record.get("request_digest"))
    state = nonempty_text(record.get("state"))
    if record_id != operation_id or record_digest is None or not DIGEST.fullmatch(record_digest):
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state or "invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="prior attempt record is not valid for this operation id",
        )
    if record_digest != digest:
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state or "invalid",
            read_back_status="not_checked",
            decision=REJECT_KEY_REUSE,
            reason="the operation id is already bound to different arguments",
        )
    if state not in KNOWN_RECORD_STATES:
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state or "invalid",
            read_back_status="not_checked",
            decision=STOP_FOR_REVIEW,
            reason="prior attempt state is missing or unknown",
        )

    if state == "committed":
        if record.get("result") is None:
            return receipt(
                case_id=case_id,
                operation_id=operation_id,
                digest=digest,
                prior_state=state,
                read_back_status="not_checked",
                decision=STOP_FOR_REVIEW,
                reason="committed attempt is missing its recorded result",
            )
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status="not_checked",
            decision=RETURN_RECORDED_RESULT,
            reason="the same operation already has a committed result",
            result=record["result"],
        )

    if state == "in_flight":
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status="not_checked",
            decision=WAIT_FOR_CURRENT_ATTEMPT,
            reason="an attempt is still in flight, so a concurrent retry is unsafe",
        )

    if state == "failed_before_effect":
        if nonempty_text(record.get("evidence")) is None:
            return receipt(
                case_id=case_id,
                operation_id=operation_id,
                digest=digest,
                prior_state=state,
                read_back_status="not_checked",
                decision=STOP_FOR_REVIEW,
                reason="failure-before-effect needs explicit evidence before retry",
            )
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status="not_checked",
            decision=RETRY_SAME_OPERATION_ID,
            reason="recorded evidence says the attempt failed before any effect",
        )

    read_back = case.get("read_back")
    if not isinstance(read_back, dict):
        read_back_status = "unavailable"
        read_back_evidence = None
    else:
        read_back_status = nonempty_text(read_back.get("status")) or "unavailable"
        read_back_evidence = nonempty_text(read_back.get("evidence"))

    if read_back_status in DECISIVE_READ_BACK_STATES and read_back_evidence is None:
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status=read_back_status,
            decision=STOP_FOR_REVIEW,
            reason="read-back claim is missing supporting evidence",
        )

    if read_back_status == "effect_present":
        if read_back.get("result") is None:
            return receipt(
                case_id=case_id,
                operation_id=operation_id,
                digest=digest,
                prior_state=state,
                read_back_status=read_back_status,
                decision=STOP_FOR_REVIEW,
                reason="observed effect is missing a recoverable result",
            )
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status=read_back_status,
            decision=RECORD_OBSERVED_SUCCESS,
            reason="read-back reports that the prior attempt produced the effect",
            result=read_back["result"],
        )
    if read_back_status == "effect_absent":
        return receipt(
            case_id=case_id,
            operation_id=operation_id,
            digest=digest,
            prior_state=state,
            read_back_status=read_back_status,
            decision=RETRY_SAME_OPERATION_ID,
            reason="read-back reports that the effect is absent",
        )
    return receipt(
        case_id=case_id,
        operation_id=operation_id,
        digest=digest,
        prior_state=state,
        read_back_status=read_back_status,
        decision=STOP_FOR_REVIEW,
        reason="the prior outcome remains unknown, so retry safety is unproven",
    )


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

        first = evaluate(case)
        expected = case.get("expected_decision")
        if first["decision"] != expected:
            fail(f"{case_id} expected {expected}, got {first['decision']}")
        expected_digest = case.get("expected_request_digest")
        if expected_digest is not None and first["request_digest"] != expected_digest:
            fail(
                f"{case_id} expected digest {expected_digest}, "
                f"got {first['request_digest']}"
            )
        print(f"PASS {case_id} {first['decision']}")


def default_cases_path() -> Path:
    return Path(__file__).with_name("examples") / "effect_recovery_cases.jsonl"


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        run_cases(load_cases(default_cases_path()))
        print("PASS self_test")
        return 0
    if len(argv) != 2:
        fail(
            "usage: python3 effect_recovery_check.py "
            "--self-test|examples/effect_recovery_cases.jsonl"
        )
    run_cases(load_cases(Path(argv[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
