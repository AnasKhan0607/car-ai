"""Claude as the main brain, via Claude Code headless (`claude -p`).

Ollama stays the offline path (assistant.py); this module only knows how to
ask Claude once and how to fail fast. Design constraints, from
docs/claude-terms.md:

- Call the real, unmodified `claude` binary on the owner's own login. Never
  read its OAuth token ourselves.
- Run it from an empty working directory. From ~/car-ai it would load this
  repo's CLAUDE.md, hooks and skills on every question.
- Event-driven only: this is called when the driver asks, never on a timer.
- Fail fast and remember the failure. In a car the usual failure is "no
  signal", and making the driver wait 30 s on every question to rediscover
  that is worse than going straight to the local model for a while.

Claude gets no tools. It is handed the same compact readings the Ollama path
gets from get_all_readings (value, unit, status, normal range, fault codes,
alerts) and answers in one turn, which is both faster and keeps every number
it can see traceable for the invented-number check.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("car_ai.brain")

CLAUDE_TIMEOUT_S = 30.0
# How long to skip Claude after each kind of failure.
BACKOFF_S = {
    "missing": 3600.0,   # binary not installed
    "auth": 600.0,       # not logged in / token rejected
    "limit": 1800.0,     # subscription usage limit
    "timeout": 120.0,    # no signal, or the API is slow
    "error": 60.0,       # anything else
}
HISTORY_TURNS = 3        # recent exchanges passed along for follow-ups

SYSTEM_PROMPT = """You are Car AI, a diagnostic assistant on a computer inside \
the driver's car (a 2020 BMW 430i Gran Coupe, F36, B46 2.0 L turbo). Each \
question comes with the car's current readings as JSON.

Rules you must follow:
- Use only the readings given. Never state a value that is not in them, and \
never move a number from one sensor to another.
- If what the driver asks about is not in the readings or is listed as \
unavailable, say the car does not report it. Never estimate.
- Each reading has a status (ok, warn, crit) and usually a normal range. Use \
them: if the status is ok, the reading is normal for this car, even if it \
sounds high.
- Only mention a problem if the readings show one (status warn or crit, a \
fault code, an alert). If nothing is wrong, say the car looks healthy.
- If something is dangerous, overheating especially, say so first and say \
what to do.
- If data_source is "simulated", say the data is simulated.
- The driver may be driving. Two short sentences unless asked for more. \
Plain words, no markdown, no lists."""


def find_claude() -> str | None:
    """The claude binary, also where the native installer puts it (systemd
    services don't get ~/.local/bin on PATH)."""
    found = os.environ.get("CAR_AI_CLAUDE_BIN") or shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude"
    return str(local) if local.exists() else None


def classify_failure(payload: dict[str, Any] | None, stderr: str = "") -> str:
    """Map a failed `claude -p` run to a BACKOFF_S key. Never raises."""
    text = " ".join([
        str((payload or {}).get("result") or ""),
        str((payload or {}).get("api_error_status") or ""),
        stderr or "",
    ]).lower()
    if any(s in text for s in ("not logged in", "/login", "401", "403",
                               "authentication", "oauth", "invalid api key")):
        return "auth"
    if any(s in text for s in ("usage limit", "rate limit", "limit reached",
                               "429", "out of credits", "credit balance")):
        return "limit"
    return "error"


class ClaudeBrain:
    def __init__(self, binary: str, model: str | None = None,
                 workdir: Path | None = None,
                 timeout: float = CLAUDE_TIMEOUT_S):
        self.binary = binary
        self.model = model or os.environ.get("CAR_AI_CLAUDE_MODEL") or None
        self.timeout = timeout
        self.workdir = workdir or (Path.home() / ".local" / "share" / "car-ai"
                                   / "claude-cwd")
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._skip_until = 0.0
        self.last_failure: str | None = None

    @property
    def available(self) -> bool:
        return time.time() >= self._skip_until

    def ask(self, question: str, readings: dict[str, Any],
            history: list[tuple[str, str]]) -> dict[str, Any]:
        """Return {"answer": str} or {"failure": kind}. Never raises."""
        if not self.available:
            return {"failure": f"skipped ({self.last_failure}, backing off)"}

        prompt = self._prompt(question, readings, history)
        cmd = [self.binary, "-p", prompt, "--output-format", "json",
               "--system-prompt", SYSTEM_PROMPT,
               "--tools", "", "--no-session-persistence", "--strict-mcp-config"]
        if self.model:
            cmd += ["--model", self.model]

        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=self.workdir, capture_output=True,
                                  text=True, timeout=self.timeout,
                                  stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return self._fail("missing", "claude binary not found")
        except subprocess.TimeoutExpired:
            return self._fail("timeout", f"no answer in {self.timeout:g}s")
        except Exception as exc:               # never take the chat down
            return self._fail("error", repr(exc))

        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            return self._fail(classify_failure(None, proc.stderr),
                              (proc.stderr or proc.stdout)[:200])

        answer = str(payload.get("result") or "").strip()
        if payload.get("is_error") or proc.returncode != 0 or not answer:
            return self._fail(classify_failure(payload, proc.stderr),
                              answer[:200] or f"exit {proc.returncode}")

        self.last_failure = None
        log.info("claude answered in %.1fs (est. $%s)", time.time() - started,
                 payload.get("total_cost_usd"))
        return {"answer": answer}

    def _fail(self, kind: str, detail: str) -> dict[str, Any]:
        self.last_failure = kind
        self._skip_until = time.time() + BACKOFF_S[kind]
        log.warning("claude unavailable (%s: %s); using the local model for "
                    "%.0f min", kind, detail, BACKOFF_S[kind] / 60)
        return {"failure": kind}

    @staticmethod
    def _prompt(question: str, readings: dict[str, Any],
                history: list[tuple[str, str]]) -> str:
        parts = ["Current readings:", json.dumps(readings, default=str)]
        if history:
            parts.append("\nEarlier in this conversation:")
            for q, a in history[-HISTORY_TURNS:]:
                parts += [f"Driver: {q}", f"You: {a}"]
        parts += ["\nDriver's question:", question]
        return "\n".join(parts)
