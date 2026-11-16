/* Car AI dashboard front end: cockpit dials, sensor tiles, chat.
 *
 * Polls /api/state on its own clock. That interval is intentionally shorter
 * than the OBD poll interval: the server just hands back an in-memory
 * snapshot, so asking often is nearly free and the screen never lags the car
 * by more than a second beyond the actual read.
 *
 * The chat request runs independently -- a model answer can take seconds, and
 * the dashboard must keep updating the whole time.
 *
 * Dials obey the same rules as the tiles always did: a reading with no value
 * draws an EMPTY dial (no arc, no needle, "--"), never one resting at zero;
 * unsupported is dimmed; stale is greyed. Their scale and warn/crit bands come
 * from sensors.py via data attributes, not from numbers in this file. */

const STATE_POLL_MS = 2000;
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (sel) => document.querySelector(sel);
const tiles = new Map();
document.querySelectorAll(".tile").forEach((el) => tiles.set(el.dataset.key, el));
const gauges = new Map();

let consecutiveFailures = 0;

/* ---------------- formatting ---------------- */

function fmt(reading, decimals = null) {
  if (reading == null) return "--";
  if (reading.value === null || reading.value === undefined) {
    return reading.status === "unsupported" ? "n/s" : "--";
  }
  if (reading.key === "run_time") return runTime(reading.value)[0];
  if (decimals !== null) return Number(reading.value).toFixed(decimals);
  return String(reading.value);
}

/* Run time arrives in seconds (the unit the model sees too), but "9071 s" or
   "151:11 s" means nothing at a glance. Returns [number, unit] for the tile. */
function runTime(seconds) {
  const s = Math.floor(seconds);
  if (s < 60) return [String(s), "s"];
  const m = Math.floor(s / 60);
  if (m < 60) return [String(m), "min"];
  return [`${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`, "min"];
}

function noteFor(reading) {
  if (!reading) return "";
  switch (reading.status) {
    case "unsupported": return "not supported by this car";
    case "no_data":     return "no response";
    case "error":       return reading.detail || "read error";
    default:            return reading.stale ? `stale · ${reading.age}s old` : "";
  }
}

/* ---------------- dials ---------------- */

// 270° sweep, open at the bottom: from 135° (lower left) clockwise to 405°.
const START = 135, SWEEP = 270, CX = 100, CY = 100;

function polar(r, deg) {
  const a = (deg * Math.PI) / 180;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
}

function arc(r, from, to) {
  const [x1, y1] = polar(r, from), [x2, y2] = polar(r, to);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function el(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function niceStep(range, target) {
  const raw = range / target, mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
}

// One shared gradient for every "ok" value arc.
(function defs() {
  const svg = el("svg", { width: 0, height: 0, style: "position:absolute" }, document.body);
  const g = el("linearGradient", { id: "grad-ok", x1: "0", y1: "1", x2: "1", y2: "0" },
               el("defs", {}, svg));
  el("stop", { offset: "0", "stop-color": "#4b7bff" }, g);
  el("stop", { offset: "1", "stop-color": "#2fd8ff" }, g);
})();

function buildGauge(root) {
  const big = root.classList.contains("gauge--big");
  const num = (k) => (root.dataset[k] === "" ? null : Number(root.dataset[k]));
  const g = {
    root, big, min: num("min"), max: num("max"), decimals: Number(root.dataset.decimals),
    warnLow: num("warnLow"), critLow: num("critLow"),
    warnHigh: num("warnHigh"), critHigh: num("critHigh"),
  };
  const svg = root.querySelector(".dial");
  const R = big ? 82 : 78, W = big ? 9 : 14;
  const frac = (v) => Math.min(1, Math.max(0, (v - g.min) / (g.max - g.min)));
  const deg = (v) => START + SWEEP * frac(v);

  el("path", { class: "track", d: arc(R, START, START + SWEEP), "stroke-width": W }, svg);

  // Warn/crit zones just outside the track, from the sensor's thresholds.
  const bandR = R + W / 2 + (big ? 4 : 3), bandW = big ? 3 : 4;
  const band = (from, to, kind) => {
    if (from === null || to === null || to <= from) return;
    el("path", { class: `band band--${kind}`, d: arc(bandR, deg(from), deg(to)),
                 "stroke-width": bandW }, svg);
  };
  band(g.warnHigh, g.critHigh ?? g.max, "warn");
  band(g.critHigh, g.max, "crit");
  band(g.critLow ?? g.min, g.warnLow, "warn");
  band(g.min, g.critLow, "crit");

  g.value = el("path", { class: "value", d: arc(R, START, START + SWEEP),
                         "stroke-width": W, pathLength: 100,
                         "stroke-dasharray": 100, "stroke-dashoffset": 100 }, svg);

  if (big) {
    const step = niceStep(g.max - g.min, 7);
    const scale = g.max >= 1000 ? 1000 : 1;        // rpm labels read "x1000"
    for (let v = g.min; v <= g.max + 1e-9; v += step / 2) {
      const major = Math.abs(((v - g.min) / step) % 1) < 1e-9;
      const a = deg(v);
      const [x1, y1] = polar(R - W / 2 - 3, a), [x2, y2] = polar(R - W / 2 - (major ? 11 : 7), a);
      el("line", { class: `tick${major ? " tick--major" : ""}`, x1, y1, x2, y2 }, svg);
      if (major) {
        const [lx, ly] = polar(R - W / 2 - 21, a);
        el("text", { class: "tick-label", x: lx, y: ly }, svg).textContent =
          String(Math.round((v / scale) * 10) / 10);
      }
    }
    // Top centre, between the "3" and "4": the only spot clear of the
    // numerals and the label underneath.
    if (scale > 1) el("text", { class: "tick-label", x: CX, y: CY - 46 }, svg).textContent = "x1000";
    // A short pointer riding the inside of the arc, not a full needle: a
    // needle through the centre would cut across the big numerals.
    g.needle = el("g", { class: "needle" }, svg);
    el("line", { x1: CX + R - W - 16, y1: CY, x2: CX + R - W / 2 + 2, y2: CY }, g.needle);
  }

  g.set = (v) => {
    const f = frac(v);
    g.value.setAttribute("stroke-dashoffset", (100 - f * 100).toFixed(2));
    if (g.needle) g.needle.style.transform = `rotate(${START + SWEEP * f}deg)`;
  };
  return g;
}

document.querySelectorAll(".gauge").forEach((root) => gauges.set(root.dataset.key, buildGauge(root)));

function renderGauges(state) {
  for (const [key, g] of gauges) {
    const r = state.readings[key];
    const has = !!r && r.value !== null && r.value !== undefined;
    g.root.querySelector(".num").textContent = fmt(r ? { ...r, key } : null, g.decimals || null);
    g.root.dataset.status = r ? r.status : "no_data";
    g.root.dataset.stale = r && r.stale ? "true" : "false";
    g.root.dataset.hasValue = has ? "true" : "false";
    g.root.querySelector(".gauge-note").textContent = noteFor(r);
    if (has) g.set(r.value);
  }
}

/* ---------------- tiles, status, alerts ---------------- */

function renderTiles(state) {
  for (const [key, tile] of tiles) {
    const r = state.readings[key];
    tile.querySelector(".num").textContent = fmt(r ? { ...r, key } : null);
    if (key === "run_time") {
      // The template prints the sensor's unit ("s"); swap it to match.
      tile.querySelector(".unit").textContent =
        r && r.value != null ? runTime(r.value)[1] : r ? r.unit : "";
    }
    tile.dataset.status = r ? r.status : "no_data";
    tile.dataset.stale = r && r.stale ? "true" : "false";
    const note = tile.querySelector(".tile-note");
    if (note) note.textContent = noteFor(r);
  }
}

function renderMode(state) {
  const pill = $("#mode-pill");
  const conn = state.connection || {};
  if (state.mode === "mock") {
    pill.className = "pill pill--mock";
    pill.textContent = "simulated data";
  } else if (conn.status === "connected") {
    pill.className = "pill pill--live";
    pill.textContent = "live";
  } else if (conn.status === "connecting") {
    pill.className = "pill pill--idle";
    pill.textContent = "connecting…";
  } else {
    pill.className = "pill pill--down";
    pill.textContent = "no adapter";
  }
  pill.title = conn.detail || "";

  const bits = [];
  if (conn.supported_count) bits.push(`${conn.supported_count} PIDs supported`);
  if (conn.port && conn.port !== "None") bits.push(conn.port);
  if (conn.protocol && conn.protocol !== "None") bits.push(conn.protocol);
  if (state.log_path) bits.push(`logging → ${state.log_path.split("/").pop()}`);
  $("#footer-meta").textContent = bits.join("  ·  ");
}

function renderAlerts(state) {
  const box = $("#alerts");
  const alerts = state.alerts || [];
  box.hidden = alerts.length === 0;
  box.innerHTML = alerts
    .map((a) => `<div class="alert alert--${a.severity}">
        <strong>${esc(a.title)}</strong><span>${esc(a.message)}</span>
      </div>`)
    .join("");
}

function renderDTCs(state) {
  const body = $("#dtc-body"), chip = $("#dtc-chip");
  $("#mil-badge").hidden = state.mil_on !== true;
  const n = (state.dtcs || []).length;
  chip.hidden = n === 0 && state.mil_on !== true;
  chip.className = "chip" + (state.mil_on ? " chip--crit" : "");
  chip.textContent = state.mil_on ? `check engine · ${n}` : `${n} code${n === 1 ? "" : "s"}`;
  if (!state.dtc_read_at) { body.textContent = "Reading…"; return; }
  if (!n) { body.innerHTML = `<span class="ok-text">No stored fault codes.</span>`; return; }
  body.innerHTML = state.dtcs
    .map((d) => `<div class="dtc"><code>${esc(d.code)}</code><p>${esc(d.description)}</p></div>`)
    .join("");
}

/* The glow under the car (and the page's ambient light) follows the car's
   worst state, so a problem is visible from the corner of the eye. */
function renderMood(state) {
  const statuses = Object.values(state.readings || {}).map((r) => r.status);
  const severities = (state.alerts || []).map((a) => a.severity);
  const all = [...statuses, ...severities];
  const mood = all.includes("crit") || state.mil_on ? "crit"
             : all.includes("warn") || (state.dtcs || []).length ? "warn" : "ok";
  if (mood !== document.body.dataset.mood) {
    document.body.dataset.mood = mood;
    document.documentElement.style.setProperty("--glow", `var(--${mood})`);
    window.dispatchEvent(new CustomEvent("car-mood", { detail: mood }));
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const state = await res.json();
    consecutiveFailures = 0;
    renderMode(state);
    renderGauges(state);
    renderTiles(state);
    renderAlerts(state);
    renderDTCs(state);
    renderMood(state);
  } catch (err) {
    // The server going away should look different from the car going away.
    if (++consecutiveFailures >= 2) {
      const pill = $("#mode-pill");
      pill.className = "pill pill--down";
      pill.textContent = "server unreachable";
    }
  }
}

function tickClock() {
  $("#clock").textContent = new Date()
    .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------------- chat ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function addMessage(cls, html) {
  const node = document.createElement("div");
  node.className = `msg msg--${cls}`;
  node.innerHTML = html;
  $("#chat-log").appendChild(node);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return node;
}

let busy = false;

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  $("#chat-send").disabled = true;
  $("#chat-input").value = "";
  addMessage("user", `<p>${esc(question)}</p>`);

  const pending = addMessage("bot",
    `<p class="thinking"><span></span><span></span><span></span> reading sensors…</p>`);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question }),
    });
    const data = await res.json();

    if (data.error) {
      pending.remove();
      addMessage("error", `<p>${esc(data.error)}</p>`);
    } else {
      // Which brain answered: the driver should know when they're offline
      // and getting the small local model, or no model at all.
      const BRAIN = { claude: "Claude", ollama: "local model (offline)",
                      none: "readings only, no AI", rule: "built-in answer" };
      const via = BRAIN[data.brain] ? `<span class="via">${BRAIN[data.brain]}</span>` : "";
      const trace = via + (data.tool_calls || [])
        .map((t) => `<span>${esc(t.tool)}()</span>`).join("");
      pending.className = "msg msg--bot";
      pending.innerHTML = `<p>${esc(data.answer).replace(/\n/g, "<br>")}</p>` +
        (trace ? `<div class="trace">${trace}</div>` : "");
    }
  } catch (err) {
    pending.remove();
    addMessage("error", `<p>Could not reach the assistant: ${esc(err.message)}</p>`);
  } finally {
    busy = false;
    $("#chat-send").disabled = false;
    $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  }
}

/* ---------------- wiring ---------------- */

function showView(view) {
  document.body.dataset.view = view;
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("is-on", t.dataset.view === view));
}

document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => showView(t.dataset.view)));
$("#dtc-chip").addEventListener("click", () => showView("sensors"));

$("#chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  ask($("#chat-input").value);
});
$("#suggestions").addEventListener("click", (e) => {
  if (e.target.tagName === "BUTTON") ask(e.target.textContent);
});
$("#chat-reset").addEventListener("click", async () => {
  await fetch("/api/chat/reset", { method: "POST" });
  $("#chat-log").innerHTML =
    `<div class="msg msg--bot"><p>Conversation cleared. Ask me anything about the car.</p></div>`;
});
$("#chat-toggle").addEventListener("click", () => document.body.classList.toggle("chat-open"));
$("#chat-close").addEventListener("click", () => document.body.classList.remove("chat-open"));

refresh();
tickClock();
setInterval(refresh, STATE_POLL_MS);
setInterval(tickClock, 20000);
