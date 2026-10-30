"""Private Codespaces/local dashboard. Start with python -m tradeos.web.app."""

import argparse
from pathlib import Path
from threading import Lock

from flask import Flask, jsonify, render_template, request

from tradeos.web.service import run_research


def create_app(config_path: str | Path = "config/paper.yaml") -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2_100_000
    config_path = Path(config_path).resolve()
    run_lock = Lock()

    @app.after_request
    def headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/backtest")
    def backtest():
        # JSON + custom header prevent cross-origin forms from starting work.
        # No CORS permission is granted, and no filesystem paths are accepted.
        if request.headers.get("X-TradeOS-Request") != "research" or not request.is_json:
            return jsonify(error="Use the dashboard to start an offline backtest"), 400
        payload = request.get_json()
        if not isinstance(payload, dict):
            return jsonify(error="Research settings must be a JSON object"), 400
        if not run_lock.acquire(blocking=False):
            return jsonify(error="A backtest is already running. Try again shortly."), 429
        try:
            return jsonify(run_research(payload, config_path))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except Exception:
            app.logger.exception("Offline backtest failed")
            return jsonify(error="Backtest failed. Check the server log; no broker orders were submitted."), 500
        finally:
            run_lock.release()

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error="CSV limit: 2 MB and 10,000 bars"), 413

    return app


def main():
    parser = argparse.ArgumentParser(description="TradeOS offline browser dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", default="config/paper.yaml")
    args = parser.parse_args()
    create_app(args.config).run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
