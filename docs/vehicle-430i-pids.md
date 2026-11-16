# 2020 BMW 430i (F36, B46): supported OBD-II data

Captured 2026-11-15 from the car through the CH340 ELM327 (`/dev/ttyUSB0`,
CAN 11/500), parked with the engine idling and warm. python-OBD reports **111
supported commands**, which is not 111 readings:

| Mode | Count | What |
|---|---|---|
| 01 | 42 | Live data (39 readings + 3 support bitmaps), below |
| 02 | 41 | Freeze frame: a snapshot of the mode-01 values taken when a fault code sets |
| 03 / 07 | 2 | Stored / pending fault codes (the app reads these) |
| 04 | 1 | **Clear fault codes. Never sent: the app is read-only.** |
| 06 | 19 | On-board monitor test results: catalyst, O2 sensors and heaters, VVT, EVAP, fuel system, misfire per cylinder 1–4 |
| 09 | 4 | VIN, calibration ID, CVN (not recorded here: this repo is public) |
| — | 2 | Adapter's own version and voltage |

## Live data (mode 01)

`Shown` is the key in `car_ai/sensors.py` if the dashboard reads it.

| PID | python-OBD name | At warm idle | Shown |
|---|---|---|---|
| `01` | `STATUS` | (status bitfield) |  |
| `03` | `FUEL_STATUS` | Closed loop, using O2 sensor feedback |  |
| `04` | `ENGINE_LOAD` | 9.02 % | `engine_load` |
| `05` | `COOLANT_TEMP` | 109 °C | `coolant_temp` |
| `06` | `SHORT_FUEL_TRIM_1` | -10.2 % | `short_fuel_trim` |
| `07` | `LONG_FUEL_TRIM_1` | 0 % | `long_fuel_trim` |
| `0B` | `INTAKE_PRESSURE` | 33 kPa | `intake_pressure` |
| `0C` | `RPM` | 738 rpm | `rpm` |
| `0D` | `SPEED` | 0 km/h | `speed` |
| `0E` | `TIMING_ADVANCE` | 5 ° | `timing_advance` |
| `0F` | `INTAKE_TEMP` | 50 °C | `intake_temp` |
| `10` | `MAF` | 2.61 g/s | `maf` |
| `11` | `THROTTLE_POS` | 10.6 % | `throttle` |
| `13` | `O2_SENSORS` | Which O2 sensors exist (bank 1 only) |  |
| `15` | `O2_B1S2` | 0.635 V |  |
| `1C` | `OBD_COMPLIANCE` | OBD-II as defined by the CARB |  |
| `1F` | `RUN_TIME` | 9071 s | `run_time` |
| `21` | `DISTANCE_W_MIL` | 0 km | `distance_with_mil` |
| `23` | `FUEL_RAIL_PRESSURE_DIRECT` | 9970 kPa | `fuel_rail_pressure` |
| `2E` | `EVAPORATIVE_PURGE` | 0 % |  |
| `2F` | `FUEL_LEVEL` | 38 % | `fuel_level` |
| `30` | `WARMUPS_SINCE_DTC_CLEAR` | 255 |  |
| `31` | `DISTANCE_SINCE_DTC_CLEAR` | 28822 km | `distance_since_codes_cleared` |
| `33` | `BAROMETRIC_PRESSURE` | 100 kPa | `barometric_pressure` |
| `34` | `O2_S1_WR_CURRENT` | -0.0312 mA |  |
| `3C` | `CATALYST_TEMP_B1S1` | 420 °C | `catalyst_temp` |
| `41` | `STATUS_DRIVE_CYCLE` | (status bitfield) |  |
| `42` | `CONTROL_MODULE_VOLTAGE` | 14.9 V | `voltage` |
| `43` | `ABSOLUTE_LOAD` | 20.8 % |  |
| `44` | `COMMANDED_EQUIV_RATIO` | 1 |  |
| `45` | `RELATIVE_THROTTLE_POS` | 0.392 % |  |
| `46` | `AMBIANT_AIR_TEMP` | 21 °C | `ambient_temp` |
| `47` | `THROTTLE_POS_B` | 11 % |  |
| `49` | `ACCELERATOR_POS_D` | 13.7 % |  |
| `4A` | `ACCELERATOR_POS_E` | 13.7 % |  |
| `4C` | `THROTTLE_ACTUATOR` | 0.392 % |  |
| `51` | `FUEL_TYPE` | Gasoline |  |
| `56` | `LONG_O2_TRIM_B1` | 0 % |  |
| `5C` | `OIL_TEMP` | 111 °C | `oil_temp` |

Not reported by this car: `FUEL_RATE` (`015E`), which the dashboard
shows as unsupported. Only bank 1 exists (inline-4), and of the O2 sensors only
the downstream narrowband (`O2_B1S2`) and the upstream wideband current
(`O2_S1_WR_CURRENT`) are exposed.

The car also stored `WARMUPS_SINCE_DTC_CLEAR` = 255, the PID's maximum, so that
one is saturated and not informative here.
