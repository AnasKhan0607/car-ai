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
import threading
from typing import Any

from .tools import build_registry, dispatch

log = logging.getLogger("car_ai.assistant")

DEFAULT_MODEL = "llama3.2:3b"
MAX_TOOL_ROUNDS = 4
# Rough budget in characters (~4 chars/token) for everything after the system
# prompt, leaving room in a 4096-token window for the model's own reply.
HISTORY_CHAR_BUDGET = 7000

SYSTEM_PROMPT = """You are Car AI, a diagnostic assistant running on a computer \
inside the vehicle, with live access to its OBD-II sensors.

Rules you must follow:
- To answer anything about the car's condition, CALL A TOOL. Never state a \
sensor value you did not read from a tool.
- If a tool says a sensor is unavailable or unsupported, say plainly that the \
car does not report it. Never estimate or substitute a number.
- Each reading comes with its unit and its normal range. Use that range to say \
clearly whether the value is normal, and explain what it means.
- Talk like a good mechanic explaining things to a driver who is not technical: \
concrete, brief, no jargon without a translation.
- If something is dangerous -- overheating especially -- say so first and say \
what to do about it.
- Keep answers to a few sentences unless asked for detail. The driver may be \
reading this at a glance."""

MOCK_WARNING = """

CRITICAL: this session is running on SIMULATED data, not a real car. Every \
value you report is fake. Mention this in your first answer so the user is \
never misled."""


class Assistant:
    """Owns one conversation. Safe to call from a web request thread."""

    def __init__(self, source, model: str = DEFAULT_MODEL):
        self.source = source
        self.model = model
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
        with self._lock:
            return self._ask_locked(question)

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
                                       tools=self.schemas)
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
                return {"answer": answer, "tool_calls": trace, "error": None}

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

        while len(self._messages) > 2 and size(self._messages) > HISTORY_CHAR_BUDGET:
            self._messages.pop(0)

        # Never leave a dangling tool result as the first message: without the
        # assistant turn that requested it, it is meaningless to the model.
        while self._messages and self._messages[0].get("role") == "tool":
            self._messages.pop(0)
