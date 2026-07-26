#!/usr/bin/env python3
"""Run the canonical checker bundled with the plan-fidelity Skill."""

from __future__ import annotations

import runpy
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parent
    / "skills"
    / "plan-fidelity"
    / "scripts"
    / "plan_fidelity_check.py"
)
runpy.run_path(str(SCRIPT), run_name="__main__")
