"""Flask app serving the dashboard and the chat endpoint.

The server does no OBD work of its own -- it only reads the poller's snapshot,
so an HTTP request can never block on the serial port and the dashboard keeps
refreshing while the model is thinking about a question.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request

from .sensors import PRIMARY_KEYS, SECONDARY_KEYS, SENSORS_BY_KEY

log = logging.getLogger("car_ai.server")


def create_app(source, monitor, assistant) -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            primary=[SENSORS_BY_KEY[k] for k in PRIMARY_KEYS],
            secondary=[SENSORS_BY_KEY[k] for k in SECONDARY_KEYS],
            model=assistant.model,
            interval=source.interval,
        )

    @app.get("/api/state")
    def state():
        snap = source.snapshot().as_dict()
        snap["alerts"] = monitor.active_alerts()
        snap["log_path"] = str(monitor.log_path) if monitor.log_path else None
        return jsonify(snap)

    @app.get("/api/alerts/history")
    def alert_history():
        return jsonify({"alerts": monitor.alert_history()})

    @app.post("/api/chat")
    def chat():
        data = request.get_json(silent=True) or {}
        question = (data.get("message") or "").strip()
        if not question:
            return jsonify({"error": "Empty message."}), 400
        if len(question) > 2000:
            return jsonify({"error": "Message too long."}), 400
        result = assistant.ask(question)
        return jsonify(result)

    @app.post("/api/chat/reset")
    def chat_reset():
        assistant.reset()
        return jsonify({"ok": True})

    @app.errorhandler(500)
    def server_error(exc):
        log.exception("unhandled error")
        return jsonify({"error": "Internal error -- check the server log."}), 500

    return app
