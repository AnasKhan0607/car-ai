"""The LLM side: llama3.2:3b under Ollama, with tools.

Three failure modes of the previous version are fixed here, and they are the
reason this file exists as its own module:

1. MULTI-ROUND TOOL CALLING. The old code ran the model once, executed the
   tools, ran it a second time, and printed whatever came back. If the model
   wanted a second tool on that second turn -- routine for a 3B model on a
   question like "how is my engine doing?" -- the reply had no text content and
   the user saw "None". Here we loop until the model produces prose, capped at
   MAX_TOOL_ROUNDS so a confused model cannot spin forever.

2. HISTORY TRIMMING. Ollama is serving this model with num_ctx=4096. An
   untrimmed conversation silently pushes the system prompt out of the window
   after a dozen exchanges, and the model quietly stops following its
   instructions. We keep the system prompt pinned and drop the oldest turns.

3. TOOL RESULTS ARE NAMED. Each tool message carries `tool_name`, so a turn
   with two tool calls does not hand the model two anonymous blobs. This
   requires ollama>=0.5; the field does not exist on 0.4.x and is silently
   dropped there, which is why requirements.txt pins it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from .prompt import VIA_TOOLS, system_prompt, vehicle_name
from .sensors import SENSORS_BY_KEY
from .tools import build_registry, dispatch

log = logging.getLogger("car_ai.assistant")

DEFAULT_MODEL = "llama3.2:3b"
MAX_TOOL_ROUNDS = 4
# Temperature 0: this is a diagnostic tool reading numbers back, not a writer.
# At the default temperature the 3B model, handed speed 0, sometimes said
# "50 km/h". num_ctx is pinned so the budget below matches the real window.
MODEL_OPTIONS = {"temperature": 0, "num_ctx": 4096}
# Rough budget in characters (~4 chars/token) for everything after the system
# prompt, leaving room in a 4096-token window for the model's own reply.
HISTORY_CHAR_BUDGET = 7000

SYSTEM_PROMPT = system_prompt(VIA_TOOLS)

# Things drivers ask about that no OBD-II reading covers. Asked "what's my tyre
# pressure?", the 3B model grabbed the nearest number and answered "35 kPa,
# within normal range" -- that was the fuel level. A prompt rule did not stop
# it, so these are answered in code, before the model is asked.
NOT_ON_OBD: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, re.I)) for name, pattern in (
        ("tyre pressure", r"\b(tyre|tire)s?\b"),
        ("oil level", r"\boil\s+(level|amount|quantity)\b|\b(low|enough)\s+oil\b"),
        ("brake pad wear", r"\bbrake\s*(pad|disc|rotor|fluid)s?\b"),
        ("coolant level", r"\bcoolant\s+(level|amount)\b|\b(low|enough)\s+coolant\b"),
        ("washer fluid", r"\bwasher\b|\bwiper\s+fluid\b"),
    )
)


def not_on_obd(question: str) -> str | None:
    """Name of the unreadable thing the question asks about, if any."""
    for name, pattern in NOT_ON_OBD:
        if pattern.search(question):
            return name
    return None


# Any number in an answer must have come from a tool result. Even at
# temperature 0, given speed 0 among twenty ok readings, the model has said
# "the engine is running at a moderate speed of 78 km/h". A number that appears
# nowhere in what the tools returned is invented, so the answer is replaced
# with a summary written by code from the real readings.
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
# A number followed by a unit (optionally as a range, "60-115 °C"). Checked as
# a pair: the model has also said "a moderate speed of 758 km/h" -- 758 was
# the rpm, so the number alone was real but the claim was invented.
_UNITS = {
    "km/h": "km/h", "kph": "km/h", "mph": "mph", "rpm": "rpm",
    "°c": "c", "c": "c", "degrees": "c", "v": "v", "volts": "v", "volt": "v",
    "%": "%", "percent": "%", "kpa": "kpa", "bar": "bar", "g/s": "g/s",
    "km": "km", "l/h": "l/h", "s": "s", "seconds": "s",
}
_WITH_UNIT = re.compile(
    r"(?<![\w.])(-?\d+(?:\.\d+)?)(?:\s*(?:-|to|–)\s*(-?\d+(?:\.\d+)?))?\s*"
    r"(km/h|kph|mph|rpm|°c|°|degrees|volts?|v|%|percent|kpa|bar|g/s|km|l/h|seconds|s|c)"
    r"(?![\w/])", re.I)


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER.findall(text)}


def _pairs(text: str) -> set[tuple[float, str]]:
    out = set()
    for lo, hi, unit in _WITH_UNIT.findall(text):
        u = _UNITS.get(unit.lower(), "c" if unit == "°" else unit.lower())
        out |= {(round(float(n), 1), u) for n in (lo, hi) if n}
    return out


def _known(trace: list[dict[str, Any]]) -> tuple[set[float], set[tuple[float, str]]]:
    numbers: set[float] = set()
    pairs: set[tuple[float, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("value"), (int, float)) and node.get("unit"):
                pairs.update(_pairs(f"{node['value']} {node['unit']}"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            pairs.update(_pairs(node))

    for call in trace:
        walk(call["result"])
        numbers |= _numbers(json.dumps(call["result"], default=str))
    numbers = {round(n, 1) for n in numbers} | {float(round(n)) for n in numbers}
    pairs |= {(float(round(n)), u) for n, u in pairs}
    return numbers, pairs


def invented_numbers(answer: str, trace: list[dict[str, Any]]) -> set[float]:
    """Numbers in `answer` no tool returned, or returned with another unit.

    The car's description in the system prompt ("2020 BMW 430i ... 2.0 L") is
    a given fact too, so it counts as known.
    """
    numbers, pairs = _known(trace + [{"tool": "system_prompt",
                                      "result": {"vehicle": vehicle_name()}}])
    bad = {n for n in _numbers(answer) if round(n, 1) not in numbers}
    for n, u in _pairs(answer):
        if (n, u) not in pairs and (float(round(n)), u) not in pairs:
            bad.add(n)
    return bad


def readings_summary(trace: list[dict[str, Any]]) -> str:
    """Plain, code-written answer from the readings the tools returned."""
    lines: list[str] = []
    for call in trace:
        result = call["result"]
        sensors = result.get("readings")
        if not isinstance(sensors, dict):
            sensors = {call["tool"]: result} if "value" in result else {}
        # Glanceable: the primary readings and anything not ok; the rest is
        # summed up rather than listed (twenty lines is not readable in a car).
        quiet = 0
        for key, r in sensors.items():
            sensor = SENSORS_BY_KEY.get(key)
            if r.get("status") == "ok" and len(sensors) > 1 and not (sensor and sensor.primary):
                quiet += 1
                continue
            label = r.get("sensor") or (sensor.label if sensor else key.replace("_", " "))
            flag = "" if r.get("status") == "ok" else f" ({r.get('status')})"
            lines.append(f"{label}: {r['value']} {r.get('unit', '')}".rstrip() + flag)
        if quiet:
            lines.append(f"{quiet} other readings are all normal.")
        if "codes" in result:
            codes = result["codes"]
            lines.append("No stored fault codes." if not codes else
                         "Fault codes: " + "; ".join(
                             f"{c['code']} {c['description']}" for c in codes))
        if result.get("fault_codes"):
            lines.append("Fault codes: " + "; ".join(result["fault_codes"]))
        if result.get("active_alerts"):
            lines.append("Alerts: " + "; ".join(result["active_alerts"]))
        if "alerts" in result:
            alerts = result["alerts"]
            lines.append("No active alerts." if not alerts else
                         "Alerts: " + "; ".join(a["message"] for a in alerts))
    if not lines:
        return ("I couldn't put an answer together from the readings. Try "
                "asking about one thing, like coolant or battery.")
    simulated = any(c["result"].get("data_source") == "simulated" for c in trace) or any(
        r.get("data_source") == "simulated" for c in trace
        for r in (c["result"].get("readings") or {}).values() if isinstance(r, dict))
    head = ("SIMULATED data, not a real car. " if simulated else "") + \
        "Here's what the car is reporting:"
    return head + "\n" + "\n".join(lines)


MOCK_WARNING = """

CRITICAL: this session is running on SIMULATED data, not a real car. Every \
value you report is fake. Mention this in your first answer so the user is \
never misled."""


class Assistant:
    """Owns one conversation. Safe to call from a web request thread."""

    def __init__(self, source, model: str = DEFAULT_MODEL, claude=None):
        self.source = source
        self.model = model
        self.claude = claude    # brain.ClaudeBrain, or None for local-only
        self.registry, self.schemas = build_registry(source)
        self._lock = threading.Lock()
        self._messages: list[dict[str, Any]] = []
        self.reset()

    # -- public ----------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._messages = []

    def history(self) -> list[dict[str, Any]]:
        """User-visible turns only -- tool traffic stays internal."""
        with self._lock:
            return [m for m in self._messages
                    if m.get("role") in ("user", "assistant") and m.get("content")]

    def ask(self, question: str) -> dict[str, Any]:
        """Answer one question. Returns {answer, tool_calls, error}."""
        result = self._answer(question)
        # MOCK_WARNING asks the model to say the data is simulated; on the Pi it
        # ignored that in every test answer. The label is the invariant, so
        # code adds it.
        answer = result.get("answer")
        if (answer and self.source.snapshot().mode == "mock"
                and "simulat" not in answer.lower()):
            result["answer"] = "(Simulated data, not a real car.) " + answer
        return result

    def _answer(self, question: str) -> dict[str, Any]:
        unreadable = not_on_obd(question)
        if unreadable:
            return {
                "answer": f"I can't read your {unreadable}: the car doesn't "
                          f"report it over the diagnostic port this system "
                          f"uses. Check the car's own dashboard display, or "
                          f"check it by hand.",
                "tool_calls": [], "error": None, "brain": "rule",
            }
        with self._lock:
            if self.claude is not None:
                answered = self._ask_claude_locked(question)
                if answered:
                    return answered
            result = self._ask_locked(question)
            if result.get("error"):
                # Last resort: no model at all. Readings are still facts.
                log.warning("no model available: %s", result["error"])
                summary = readings_summary([{
                    "tool": "get_all_readings",
                    "result": dispatch(self.registry, "get_all_readings")}])
                return {"answer": "I can't reach an AI model right now. "
                                  + summary, "tool_calls": [], "error": None,
                        "brain": "none"}
            result["brain"] = "ollama"
            return result

    def _ask_claude_locked(self, question: str) -> dict[str, Any] | None:
        """Claude's answer, or None to fall through to the local model."""
        trace = [{"tool": "get_all_readings",
                  "result": dispatch(self.registry, "get_all_readings")}]
        history = []
        pending = None
        for m in self._messages:
            if m.get("role") == "user":
                pending = m.get("content")
            elif m.get("role") == "assistant" and m.get("content") and pending:
                history.append((pending, m["content"]))
                pending = None
        result = self.claude.ask(question, trace[0]["result"], history)
        if "answer" not in result:
            return None
        answer = result["answer"]
        # Same rule as the local model: a number Claude was not given, or one
        # given a different unit, means the answer is not trusted.
        invented = invented_numbers(answer, trace)
        if invented:
            log.warning("claude answer had numbers not in the readings %s; "
                        "replacing it: %r", sorted(invented), answer)
            answer = readings_summary(trace)
        # Keep the shared history, so a follow-up that falls back to Ollama
        # still knows what was asked.
        self._messages += [{"role": "user", "content": question},
                           {"role": "assistant", "content": answer}]
        self._trim()
        return {"answer": answer, "tool_calls": trace, "error": None,
                "brain": "claude",
                **({"replaced_invented_numbers": sorted(invented)}
                   if invented else {})}

    # -- internals -------------------------------------------------------
    def _system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        if self.source.snapshot().mode == "mock":
            prompt += MOCK_WARNING
        return prompt

    def _ask_locked(self, question: str) -> dict[str, Any]:
        try:
            import ollama
        except ImportError:
            return {"answer": None, "tool_calls": [],
                    "error": "The 'ollama' Python package is not installed. "
                             "Run: pip install -r requirements.txt"}

        self._messages.append({"role": "user", "content": question})
        self._trim()
        trace: list[dict[str, Any]] = []

        for round_no in range(MAX_TOOL_ROUNDS):
            payload = [{"role": "system", "content": self._system_prompt()}]
            payload.extend(self._messages)

            try:
                response = ollama.chat(model=self.model, messages=payload,
                                       tools=self.schemas, options=MODEL_OPTIONS)
            except Exception as exc:
                log.exception("ollama call failed")
                self._messages.pop()  # do not poison history with a dead turn
                return {
                    "answer": None, "tool_calls": trace,
                    "error": f"Could not reach the model ({exc}). Check that "
                             f"Ollama is running (systemctl status ollama) and "
                             f"that '{self.model}' is pulled.",
                }

            message = response.message
            self._messages.append({
                "role": "assistant",
                "content": message.content or "",
                **({"tool_calls": message.tool_calls} if message.tool_calls else {}),
            })

            if not message.tool_calls:
                answer = (message.content or "").strip()
                if not answer:
                    answer = ("I could not put that into words -- try asking "
                              "again, or more specifically.")
                if not trace:
                    # Answered without reading the car. Seen on the Pi: "is
                    # anything wrong?" -> "looks healthy" during a misfire.
                    # Whatever it said, it isn't from the car, so show the car.
                    log.warning("local model answered without a tool; "
                                "replacing it: %r", answer)
                    trace.append({"tool": "get_all_readings",
                                  "result": dispatch(self.registry, "get_all_readings")})
                    answer = readings_summary(trace)
                    self._messages[-1]["content"] = answer
                invented = invented_numbers(answer, trace)
                if invented:
                    log.warning("answer had numbers no tool returned %s; "
                                "replacing it: %r", sorted(invented), answer)
                    answer = readings_summary(trace)
                    # Keep the history consistent with what the driver saw.
                    self._messages[-1]["content"] = answer
                return {"answer": answer, "tool_calls": trace, "error": None,
                        **({"replaced_invented_numbers": sorted(invented)}
                           if invented else {})}

            for call in message.tool_calls:
                name = call.function.name
                result = dispatch(self.registry, name)
                trace.append({"tool": name, "result": result})
                log.info("tool %s -> %s", name, result)
                self._messages.append({
                    "role": "tool",
                    "tool_name": name,          # needs ollama>=0.5
                    "content": json.dumps(result, default=str),
                })
            self._trim()

        # The model kept asking for tools and never answered. Rather than loop
        # forever, hand back what we actually read so the turn is not wasted.
        return {
            "answer": "I read the sensors but could not summarise them. The "
                      "raw readings are below.",
            "tool_calls": trace,
            "error": None,
        }

    def _trim(self) -> None:
        """Drop the oldest turns until the history fits the context window.

        The system prompt is never in `self._messages` -- it is prepended fresh
        on every call -- so it cannot be trimmed away, which is precisely the
        bug this avoids.
        """
        def size(msgs) -> int:
            return sum(len(str(m.get("content", ""))) for m in msgs)

        # Only earlier turns are eligible. The current question and its tool
        # results stay even if they alone exceed the budget: dropping the
        # question left the model a page of readings with nothing asked, and
        # it answered a question nobody asked ("your air filter is dirty").
        def current_turn_start() -> int:
            for i in range(len(self._messages) - 1, -1, -1):
                if self._messages[i].get("role") == "user":
                    return i
            return 0

        while current_turn_start() > 0 and size(self._messages) > HISTORY_CHAR_BUDGET:
            self._messages.pop(0)

        # Never leave a dangling tool result as the first message: without the
        # assistant turn that requested it, it is meaningless to the model.
        while self._messages and self._messages[0].get("role") == "tool":
            self._messages.pop(0)
