---
name: car-ai-run
description: Launch the Car AI dashboard — locally against the simulator, or on the Pi against a real car or fullscreen in kiosk mode. Use when asked to run, start, demo, or screenshot the app, or to confirm a change works in the real UI.
---

# Running Car AI

Pick the lightest thing that answers the question.

## 1. Changed tool/alert/sensor logic? Start here.

```bash
python app.py --selftest
```

No adapter, no Ollama, no network, no third-party deps. Exercises all ten
tools, the alert engine, and the hallucinated-tool-name recovery path. If this
fails, nothing else is worth trying.

## 2. Changed the UI or anything user-visible? Run the simulator.

```bash
python app.py --mock                        # healthy car
python app.py --mock --scenario overheat    # ramps into warn then crit
```

Dashboard at <http://localhost:5000>. Needs the venv
(`pip install -r requirements.txt`) — Flask, not python-OBD.

Scenarios: `normal`, `overheat`, `low_fuel`, `misfire`, `charging_fault`.

**Always verify a UI change in a scenario that exercises the state you
touched.** `normal` shows healthy tiles only; the alert banners, the amber/red
tile treatment and the "pull over" copy only appear under a fault scenario.
`overheat` takes ~2 min to reach warn and ~3 to reach crit — to see the alert
states immediately, force them in the browser rather than waiting:

```js
document.querySelector('.tile[data-key="coolant_temp"]').dataset.status = 'crit';
```

## 3. Verifying in a browser

The dashboard polls `/api/state` every 2s and renders from an in-memory
snapshot, so it is cheap to screenshot repeatedly. Useful checks:

```bash
curl -s localhost:5000/api/state | python -m json.tool | head -40
```

Confirm `mode`, `connection.status`, and that unsupported sensors report
`"status": "unsupported"` rather than a value.

## 4. On the Pi

```bash
ssh master@pi5.local          # password auth; key auth is not set up
cd ~/car-ai && source ~/car_ai_env/bin/activate
python app.py --mock          # or: python app.py   (live, adapter attached)
```

Reachable from any machine on the LAN at `http://pi5.local:5000` — it binds
`0.0.0.0` by default.

**The chat only works on the Pi**, where Ollama runs. On a dev machine without
Ollama the dashboard is fully functional and `/api/chat` returns a clean
"could not reach the model" error. That is expected, not a bug.

## 5. Fullscreen on the Pi's display

```bash
./scripts/kiosk.sh            # handles venv + screen blanking + Chromium
```

Needs a monitor attached and the desktop session running — over SSH alone there
is nothing to draw on. Chromium logs `gbm_wrapper ... dma_buf` and
`DEPRECATED_ENDPOINT` errors; both are noise.

## Stopping

```bash
pkill -f "app.py"
pkill chromium
```
