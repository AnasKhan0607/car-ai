#!/usr/bin/env python3
"""Ask a running Car AI a fixed set of questions and print what came back.

    python3 scripts/chat_check.py                  # against localhost:5000
    python3 scripts/chat_check.py http://pi5.local:5000

Stdlib only, so it runs on the Pi without the venv. It doesn't assert: the
answers come from a model, so a human reads them against the GROUND TRUTH
line. Each question probes one known failure:

- broad question     -> invented problems, or numbers given the wrong unit
- tyre pressure      -> a confident number for something OBD can't read
- fuel per hour      -> a sensor this car doesn't support (fuel_rate)
- speed while parked -> the original "72 km/h in the driveway" bug
- follow-up          -> history trimming and tool_name on tool results

Used for #6 (Ollama) and meant for comparing brains in #7.
"""
import json, sys, time, urllib.request
BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5000"
def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=300))
def state():
    return json.load(urllib.request.urlopen(BASE + "/api/state", timeout=5))
QUESTIONS = [
    (True,  "How is the car doing?"),
    (True,  "What's my coolant temperature, and is that okay?"),
    (True,  "What's my tire pressure?"),
    (True,  "How much fuel am I using per hour right now?"),
    (True,  "Do I have any fault codes?"),
    (True,  "How fast am I going?"),
    (False, "And what about the battery?"),
]
s = state()["readings"]
print("GROUND TRUTH:", {k: s[k]["value"] for k in ("speed","rpm","coolant_temp","voltage","fuel_level","oil_temp")}, "fuel_rate:", s["fuel_rate"]["status"])
for reset, q in QUESTIONS:
    if reset: post("/api/chat/reset")
    t = time.time()
    r = post("/api/chat", {"message": q})
    dt = time.time() - t
    print("\n" + "=" * 70)
    print(f"Q: {q}   [{dt:.1f}s{'' if reset else ', follow-up'}]")
    print("brain:", r.get("brain"), "| tools:", [c["tool"] for c in r.get("tool_calls", [])])
    if r.get("error"): print("ERROR:", r["error"])
    print("A:", r.get("answer"))
    if r.get("replaced_invented_numbers"): print("REPLACED (invented):", r["replaced_invented_numbers"])
    sys.stdout.flush()
