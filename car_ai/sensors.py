"""The sensor catalogue — one source of truth for the whole application.

Every layer reads from this table: the poller learns what to query, the
dashboard learns what to draw, the alert monitor learns the safe ranges, and
the LLM tool schemas are generated from it. Adding a sensor is a one-entry
change here, not an edit in five files.

`context` is not decoration. A 3B model handed the bare number 98 will
confidently invent what it means; handed 98 alongside "normal operating range
is ~90-104 C", it stays grounded. Every value that reaches the model carries
its unit and its normal range.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Poll tiers. The ELM327 is the bottleneck (roughly 50-100 ms per PID), so
# polling 18 sensors every cycle would make the dashboard sluggish for no gain
# -- ambient air temperature does not need a 5-second refresh.
TIER_PRIMARY = 1    # every cycle: the numbers you actually watch while driving
TIER_SECONDARY = 3  # every 3rd cycle
TIER_SLOW = 6       # every 6th cycle: fault codes, MIL status


@dataclass(frozen=True)
class Sensor:
    key: str                      # our stable internal name
    command: str                  # attribute name on obd.commands
    label: str                    # what the dashboard prints
    unit: str                     # display unit
    context: str                  # plain-English meaning, handed to the model
    to_unit: str | None = None    # pint conversion target, if needed
    decimals: int = 0
    tier: int = TIER_PRIMARY
    primary: bool = False         # rendered as a large tile
    warn_low: float | None = None
    crit_low: float | None = None
    warn_high: float | None = None
    crit_high: float | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)  # fallback cmd names


# ---------------------------------------------------------------------------
# Thresholds are deliberately conservative and vehicle-generic. They are a
# starting point for "something looks off", not a substitute for a mechanic.
# ---------------------------------------------------------------------------
SENSORS: tuple[Sensor, ...] = (
    Sensor(
        key="speed", command="SPEED", label="Speed", unit="km/h",
        to_unit="kph", primary=True, tier=TIER_PRIMARY,
        context="Road speed of the vehicle.",
    ),
    Sensor(
        key="rpm", command="RPM", label="Engine RPM", unit="rpm",
        primary=True, tier=TIER_PRIMARY,
        warn_high=4500, crit_high=6000,
        context="Engine crankshaft speed. Idle is ~600-900 rpm; highway "
                "cruising is ~1500-3000 rpm. Sustained high rpm means the "
                "engine is working hard.",
    ),
    Sensor(
        key="coolant_temp", command="COOLANT_TEMP", label="Coolant", unit="°C",
        to_unit="celsius", primary=True, tier=TIER_PRIMARY,
        warn_low=60, warn_high=105, crit_high=112,
        context="Engine coolant temperature. Normal operating range is "
                "~90-104 C. Above ~110 C is a genuine overheating risk and "
                "the driver should pull over. Below ~60 C once warmed up "
                "suggests a stuck-open thermostat.",
    ),
    Sensor(
        key="engine_load", command="ENGINE_LOAD", label="Engine Load", unit="%",
        primary=True, tier=TIER_PRIMARY,
        context="Calculated engine load -- the share of maximum available "
                "torque currently in use. Low at idle and cruising, high "
                "under hard acceleration or climbing.",
    ),
    Sensor(
        key="fuel_level", command="FUEL_LEVEL", label="Fuel", unit="%",
        primary=True, tier=TIER_SECONDARY,
        warn_low=15, crit_low=8,
        context="Fuel remaining as a percentage of tank capacity. NOTE: this "
                "is an optional OBD-II PID and many vehicles -- BMW in "
                "particular -- do not expose it. If it reads as unsupported, "
                "the car genuinely does not provide it.",
    ),
    Sensor(
        key="voltage", command="CONTROL_MODULE_VOLTAGE", label="Battery", unit="V",
        decimals=1, primary=True, tier=TIER_SECONDARY,
        warn_low=12.0, crit_low=11.5, warn_high=15.0, crit_high=15.5,
        context="Control module supply voltage, a good proxy for charging "
                "system health. Engine off: ~12.6 V. Engine running: ~13.5-14.7 V "
                "with the alternator charging. Below ~12 V while running "
                "suggests the alternator is not keeping up.",
    ),

    # --- secondary: useful context, smaller tiles -------------------------
    Sensor(
        key="intake_temp", command="INTAKE_TEMP", label="Intake Air", unit="°C",
        to_unit="celsius", tier=TIER_SECONDARY, warn_high=70,
        context="Temperature of air entering the engine. Typically close to "
                "ambient, higher in slow traffic or after heat soak.",
    ),
    Sensor(
        key="throttle", command="THROTTLE_POS", label="Throttle", unit="%",
        tier=TIER_SECONDARY,
        context="Throttle plate position. ~10-15 percent at idle on many cars, "
                "not zero.",
    ),
    Sensor(
        key="oil_temp", command="OIL_TEMP", label="Oil Temp", unit="°C",
        to_unit="celsius", tier=TIER_SECONDARY,
        warn_high=125, crit_high=140,
        context="Engine oil temperature. Normal is ~90-120 C once warm. "
                "Frequently unsupported on the standard OBD-II PID.",
    ),
    Sensor(
        key="ambient_temp", command="AMBIANT_AIR_TEMP", label="Outside Air",
        unit="°C", to_unit="celsius", tier=TIER_SLOW,
        aliases=("AMBIENT_AIR_TEMP",),
        context="Outside air temperature as measured by the vehicle.",
    ),
    Sensor(
        key="maf", command="MAF", label="Air Flow", unit="g/s",
        decimals=1, tier=TIER_SECONDARY,
        context="Mass air flow into the engine. Rises with load and rpm.",
    ),
    Sensor(
        key="intake_pressure", command="INTAKE_PRESSURE", label="Intake Press.",
        unit="kPa", tier=TIER_SECONDARY,
        context="Manifold absolute pressure. On a turbocharged engine, values "
                "above ambient (~101 kPa) indicate boost.",
    ),
    Sensor(
        key="timing_advance", command="TIMING_ADVANCE", label="Timing", unit="°",
        decimals=1, tier=TIER_SECONDARY,
        context="Ignition timing advance before top dead centre. Persistent "
                "retard under load can indicate the knock sensor pulling "
                "timing -- often a fuel quality or engine health signal.",
    ),
    Sensor(
        key="short_fuel_trim", command="SHORT_FUEL_TRIM_1", label="STFT B1", unit="%",
        decimals=1, tier=TIER_SECONDARY,
        warn_low=-15, warn_high=15, crit_low=-25, crit_high=25,
        context="Short-term fuel trim, bank 1. The ECU's live correction to "
                "the fuel mixture. Near 0 percent is healthy; consistently "
                "beyond +/-10 percent suggests a vacuum leak, a failing sensor, "
                "or fuel delivery trouble.",
    ),
    Sensor(
        key="long_fuel_trim", command="LONG_FUEL_TRIM_1", label="LTFT B1", unit="%",
        decimals=1, tier=TIER_SECONDARY,
        warn_low=-15, warn_high=15, crit_low=-25, crit_high=25,
        context="Long-term fuel trim, bank 1 -- the ECU's learned correction. "
                "Same interpretation as short-term trim but reflects a "
                "persistent condition rather than a momentary one.",
    ),
    Sensor(
        key="run_time", command="RUN_TIME", label="Run Time", unit="s",
        tier=TIER_SECONDARY,
        context="Seconds since the engine started this drive.",
    ),
    Sensor(
        key="fuel_rate", command="FUEL_RATE", label="Fuel Rate", unit="L/h",
        decimals=1, tier=TIER_SECONDARY,
        context="Current rate of fuel consumption. Often unsupported.",
    ),
)

SENSORS_BY_KEY: dict[str, Sensor] = {s.key: s for s in SENSORS}
PRIMARY_KEYS: tuple[str, ...] = tuple(s.key for s in SENSORS if s.primary)
SECONDARY_KEYS: tuple[str, ...] = tuple(s.key for s in SENSORS if not s.primary)


# ---------------------------------------------------------------------------
# Diagnostic trouble codes
# ---------------------------------------------------------------------------
# python-OBD returns (code, description) pairs and its description table is
# authoritative, so this map is a FALLBACK only -- used for mock mode and for
# the occasional code python-OBD does not know. It is never used to override a
# description the library supplied.
DTC_FALLBACK_DESCRIPTIONS: dict[str, str] = {
    "P0420": "Catalyst system efficiency below threshold (Bank 1)",
    "P0430": "Catalyst system efficiency below threshold (Bank 2)",
    "P0128": "Coolant thermostat below regulating temperature",
    "P0171": "System too lean (Bank 1)",
    "P0174": "System too lean (Bank 2)",
    "P0172": "System too rich (Bank 1)",
    "P0300": "Random / multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0442": "Evaporative emission system leak detected (small leak)",
    "P0455": "Evaporative emission system leak detected (large leak)",
    "P0011": "Camshaft position - timing over-advanced (Bank 1)",
    "P0217": "Engine over temperature condition",
}


def describe_dtc(code: str, supplied: str | None = None) -> str:
    """Prefer the description python-OBD gave us; fall back to our table."""
    if supplied:
        return supplied
    return DTC_FALLBACK_DESCRIPTIONS.get(
        code, "Unknown code -- look it up in a DTC reference."
    )


def classify(sensor: Sensor, value: float | None) -> str:
    """Return 'ok', 'warn', or 'crit' for a reading. Never raises."""
    if value is None:
        return "ok"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "ok"
    if sensor.crit_high is not None and v >= sensor.crit_high:
        return "crit"
    if sensor.crit_low is not None and v <= sensor.crit_low:
        return "crit"
    if sensor.warn_high is not None and v >= sensor.warn_high:
        return "warn"
    if sensor.warn_low is not None and v <= sensor.warn_low:
        return "warn"
    return "ok"


def normal_range_text(sensor: Sensor) -> str | None:
    """Human phrasing of the safe band, for alert messages."""
    lo = sensor.warn_low
    hi = sensor.warn_high
    if lo is not None and hi is not None:
        return f"{lo:g}-{hi:g} {sensor.unit}"
    if hi is not None:
        return f"below {hi:g} {sensor.unit}"
    if lo is not None:
        return f"above {lo:g} {sensor.unit}"
    return None
