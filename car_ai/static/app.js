/* Car AI dashboard front end.
 *
 * Polls /api/state on its own clock. That interval is intentionally shorter
 * than the OBD poll interval: the server just hands back an in-memory
 * snapshot, so asking often is nearly free and the screen never lags the car
 * by more than a second beyond the actual read.
 *
 * The chat request runs independently -- a model answer can take many seconds
 * on a Pi, and the dashboard must keep updating the whole time. */

const STATE_POLL_MS = 2000;

const $ = (sel) => document.querySelector(sel);
const tiles = new Map();
document.querySelectorAll(".tile").forEach((el) => tiles.set(el.dataset.key, el));

let consecutiveFailures = 0;

/* ---------------- dashboard ---------------- */

function fmt(reading) {
  if (reading == null) return "--";
  if (reading.value === null || reading.value === undefined) {
    return reading.status === "unsupported" ? "n/s" : "--";
  }
  if (reading.key === "run_time") {
    const s = Math.floor(reading.value);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  return String(reading.value);
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

function renderTiles(state) {
  for (const [key, el] of tiles) {
    const r = state.readings[key];
    el.querySelector(".num").textContent = fmt(r ? { ...r, key } : null);
    el.dataset.status = r ? r.status : "no_data";
    el.dataset.stale = r && r.stale ? "true" : "false";
    const note = el.querySelector(".tile-note");
    if (note) note.textContent = noteFor(r);
    if (r) el.title = noteFor(r) || `${r.label}: ${r.value ?? "unavailable"} ${r.unit}`;
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
    pill.textContent = "live · connected";
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
  const body = $("#dtc-body");
  $("#mil-badge").hidden = state.mil_on !== true;
  if (!state.dtc_read_at) { body.textContent = "Reading…"; return; }
  if (!state.dtcs.length) {
    body.innerHTML = `<span style="color:var(--ok)">No stored fault codes.</span>`;
    return;
  }
  body.innerHTML = state.dtcs
    .map((d) => `<div class="dtc"><code>${esc(d.code)}</code><p>${esc(d.description)}</p></div>`)
    .join("");
}

async function refresh() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    const state = await res.json();
    consecutiveFailures = 0;
    renderMode(state);
    renderTiles(state);
    renderAlerts(state);
    renderDTCs(state);
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
  const el = document.createElement("div");
  el.className = `msg msg--${cls}`;
  el.innerHTML = html;
  $("#chat-log").appendChild(el);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return el;
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
      const trace = (data.tool_calls || [])
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

$("#chat-toggle").addEventListener("click", () => {
  document.body.classList.toggle("chat-open");
});

refresh();
tickClock();
setInterval(refresh, STATE_POLL_MS);
setInterval(tickClock, 20000);
