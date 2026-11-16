"""The one system prompt both brains use.

Claude (brain.py) and the local model (assistant.py) used to carry their own
copies of the rules, which drifted: only one of them knew which car it was in,
and they disagreed on how long an answer should be. The rules now live in
system_prompt.txt; each brain fills in only how it gets its readings.
"""

from __future__ import annotations

from pathlib import Path

from .sensors import VEHICLE_NAMES, VEHICLE_PROFILE

PROMPT_FILE = Path(__file__).with_name("system_prompt.txt")

# How each brain receives readings -- the one thing that genuinely differs.
# Capitals on purpose: phrased politely, the 3B model answered "is anything
# wrong?" with "looks healthy" without reading anything, during a misfire.
VIA_TOOLS = ("You MUST CALL A TOOL before answering anything about the car: "
             "every reading you may use comes from a tool result.")
VIA_JSON = "Each question comes with the car's current readings as JSON."


def vehicle_name() -> str:
    return VEHICLE_NAMES.get(VEHICLE_PROFILE or "", "the driver's car")


def system_prompt(readings_source: str) -> str:
    return PROMPT_FILE.read_text().strip().format(
        vehicle=vehicle_name(), readings_source=readings_source)
