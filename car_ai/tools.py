"""The tool layer the LLM calls.

Two decisions carried over from the original design, because they were right:

* EVERY TOOL RESULT CARRIES ITS UNIT AND ITS NORMAL RANGE. Handing a 3B model
  the bare integer 98 invites it to invent what that means. Handing it
  {"value": 98, "unit": "C", "context": "normal is ~90-104 C"} keeps it
  grounded in something true.

* THE DISPATCHER NEVER RAISES. Small local models occasionally invent a tool
  name. Returning a structured error that lists the real tools lets the model
  correct itself on the next turn instead of taking the conversation down.

One decision that is new: tools read the poller's in-memory snapshot rather
than querying the car. A tool call is therefore instant and cannot contend with
the dashboard for the serial port. The trade-off -- readings can be up to one
poll interval old -- is made visible: every result carries `reading_age_s`, and
stale data says so.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .sensors import PRIMARY_KEYS, SENSORS, SENSORS_BY_KEY, normal_range_text


def _describe(state, key: str) -> dict[str, Any]:
    """Render one sensor reading as something a small model can reason about."""
    sensor = SENSORS_BY_KEY[key]
    reading = state.readings.get(key)

    if reading is None:
        return {
            "sensor": sensor.label,
            "available": False,
            "reason": "Not read yet -- the poller has not completed a cycle.",
        }

    if reading.status == "unsupported":
        return {
            "sensor": sensor.label,
            "available": False,
            "reason": "This vehicle does not support this sensor. Tell the user "
                      "their car does not report it -- do NOT estimate a value.",
        }

    if reading.value is None:
        return {
            "sensor": sensor.label,
            "available": False,
            "reason": (reading.detail or "No reading available.") +
                      " Say you could not read it -- do NOT estimate a value.",
        }

    out: dict[str, Any] = {
        "sensor": sensor.label,
        "available": True,
        "value": reading.value,
        "unit": sensor.unit,
        "status": reading.status,          # ok | warn | crit
        "context": sensor.context,
        "reading_age_s": round(reading.age, 1),
        "data_source": "simulated" if state.mode == "mock" else "live vehicle",
    }
    band = normal_range_text(sensor)
    if band:
        out["normal_range"] = band
    if reading.stale:
        out["warning"] = (f"This reading is {round(reading.age)}s old and may no "
                          f"longer reflect the car. Say so in your answer.")
    return out


def build_registry(source) -> tuple[dict[str, Callable[[], dict]], list[dict]]:
    """Build the {name: fn} dispatch table and the JSON schemas for Ollama.

    All tools take zero arguments. That is deliberate: small models are far more
    reliable at picking a name than at filling in a parameter, and the six
    readings a driver actually asks about fit comfortably as named tools.
    """
    registry: dict[str, Callable[[], dict]] = {}
    schemas: list[dict] = []

    def register(name: str, description: str, fn: Callable[[], dict]) -> None:
        registry[name] = fn
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        })

    # One no-argument reader per primary sensor.
    for key in PRIMARY_KEYS:
        sensor = SENSORS_BY_KEY[key]
        register(
            f"get_{key}",
            f"Read the vehicle's {sensor.label.lower()} "
            f"({sensor.unit}). {sensor.context.split('.')[0]}.",
            lambda k=key: _describe(source.snapshot(), k),
        )

    def get_all_readings() -> dict:
        state = source.snapshot()
        readings = {k: _describe(state, k) for k in SENSORS_BY_KEY}
        available = {k: v for k, v in readings.items() if v.get("available")}
        unavailable = sorted(k for k, v in readings.items() if not v.get("available"))
        return {
            "readings": available,
            "unavailable_sensors": unavailable,
            "data_source": "simulated" if state.mode == "mock" else "live vehicle",
            "context": "Every sensor currently readable on this vehicle. Sensors "
                       "listed under unavailable_sensors cannot be read -- do not "
                       "guess values for them.",
        }

    register("get_all_readings",
             "Read every available sensor at once. Use this for broad questions "
             "like 'how is my car doing?' or 'is everything okay?'.",
             get_all_readings)

    def get_fault_codes() -> dict:
        state = source.snapshot()
        if not state.dtcs:
            return {
                "codes": [],
                "count": 0,
                "check_engine_light": state.mil_on,
                "context": "No diagnostic trouble codes are stored -- the ECU has "
                           "recorded no faults.",
            }
        return {
            "codes": state.dtcs,
            "count": len(state.dtcs),
            "check_engine_light": state.mil_on,
            "context": "Stored OBD-II diagnostic trouble codes. The description "
                       "for each code is authoritative -- use it rather than "
                       "recalling what the code means from memory.",
        }

    register("get_fault_codes",
             "Read stored OBD-II diagnostic trouble codes (DTCs) and whether the "
             "check-engine light is on.",
             get_fault_codes)

    def get_active_alerts() -> dict:
        alerts = source.monitor.active_alerts() if getattr(source, "monitor", None) else []
        return {
            "alerts": alerts,
            "count": len(alerts),
            "context": "Problems the background monitor has already detected. An "
                       "empty list means nothing is currently out of range.",
        }

    register("get_active_alerts",
             "List any problems the background monitor has flagged (overheating, "
             "low voltage, new fault codes, and so on).",
             get_active_alerts)

    def get_vehicle_status() -> dict:
        state = source.snapshot()
        return {
            "data_source": "simulated" if state.mode == "mock" else "live vehicle",
            "adapter_status": state.connection_status,
            "detail": state.connection_detail,
            "supported_sensor_count": state.supported_count,
            "poll_interval_s": source.interval,
            "context": ("IMPORTANT: this session is running on SIMULATED data. "
                        "Any value you report is fake and must be described as "
                        "such." if state.mode == "mock" else
                        "This session is reading a real vehicle."),
        }

    register("get_vehicle_status",
             "Check whether the OBD adapter is connected and whether the data is "
             "live or simulated.",
             get_vehicle_status)

    return registry, schemas


def dispatch(registry: dict[str, Callable[[], dict]], name: str) -> dict:
    """Run a tool by name. Returns a dict no matter what happens."""
    fn = registry.get(name)
    if fn is None:
        return {
            "error": f"There is no tool called '{name}'.",
            "available_tools": sorted(registry),
            "hint": "Call one of the available tools listed above.",
        }
    try:
        return fn()
    except Exception as exc:
        return {"error": f"Tool '{name}' failed: {exc}",
                "hint": "Tell the user the reading could not be taken."}
