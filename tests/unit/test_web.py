from decimal import Decimal
import json
import socket

import pytest

from tradeos.config.loader import load_config
from tradeos.data.historical_dataset import load_bars_from_csv
from tradeos.data.sample_dataset import write_sample_dataset
from tradeos.main import _run_single_backtest
from tradeos.web.app import create_app
from dataclasses import replace


HEADERS = {"X-TradeOS-Request": "research"}


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline dashboard attempted network/broker access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("tradeos.execution.broker.AlpacaPaperBroker.from_env", forbidden)
    monkeypatch.setattr("tradeos.execution.broker.FakeBroker.submit_order", forbidden)
    app = create_app()
    app.testing = True
    return app.test_client()


def run(client, payload=None):
    return client.post("/api/backtest", json={} if payload is None else payload, headers=HEADERS)


def test_web_sample_matches_existing_cli_engine(client, tmp_path):
    response = run(client)
    assert response.status_code == 200
    data = response.json
    path = tmp_path / "sample.csv"
    write_sample_dataset(path)
    config = load_config("config/paper.yaml")
    config = replace(config, strategy=replace(config.strategy, entry_threshold_pct=Decimal("0.005"), exit_threshold_pct=Decimal("0.005")))
    result = _run_single_backtest(config, load_bars_from_csv(path))
    assert data["summary"]["total_pnl"] == str(result.total_pnl)
    assert data["summary"]["order_intents_created"] == 2
    assert data["summary"]["closed_trades"] == 1
    assert data["summary"]["total_cycles"] == 1500
    assert data["broker_orders_submitted"] == 0
    assert len(data["curve"]) == 1500
    assert data["positions"] == []
    assert len(data["dataset"]["sha256"]) == 64
    assert data == run(client).json
    json.dumps(data, allow_nan=False)


def test_costs_and_momentum(client):
    response = run(client, {"commission": "1", "slippage_bps": "5"})
    assert response.status_code == 200
    assert Decimal(response.json["summary"]["total_costs"]) > 0
    assert Decimal(response.json["summary"]["total_pnl"]) < Decimal("4.3164")
    assert run(client, {"strategy": "momentum_breakout"}).status_code == 200


@pytest.mark.parametrize("payload", [
    {"strategy": "live"}, {"strategy": []}, {"entry_percent": "NaN"},
    {"exit_percent": "Infinity"}, {"entry_percent": "0"},
    {"commission": "-1"}, {"slippage_bps": "101"}, {"entry_percent": {}},
    {"csv": ""}, {"csv": 12}, {"csv": "symbol\nAAPL"},
    {"submit_orders": True}, {"config": "config/live.yaml"},
    {"csv": "x" * 2_000_001},
])
def test_bad_settings_rejected(client, payload):
    response = run(client, payload)
    assert response.status_code == 400
    assert response.json["error"]


def test_upload_and_rejection(client, tmp_path):
    path = tmp_path / "bars.csv"
    write_sample_dataset(path, bars_per_symbol=30)
    csv = path.read_text()
    response = run(client, {"csv": csv})
    assert response.status_code == 200
    assert response.json["dataset"]["kind"] == "uploaded"
    assert response.json["dataset"]["bars"] == 90
    assert run(client, {"csv": csv.replace("AAPL", "UNKNOWN")}).status_code == 400
    assert run(client, {"csv": csv.replace("15Min", "1Min")}).status_code == 400


def test_limits_and_request_boundary(client):
    assert client.post("/api/backtest", json={}).status_code == 400
    assert client.post("/api/backtest", data="{}", headers=HEADERS).status_code == 400
    assert run(client, []).status_code == 400
    assert run(client, {"csv": "x" * 2_100_001}).status_code == 413
    assert client.get("/api/backtest").status_code == 405
    assert client.post("/api/order", json={}).status_code == 404


def test_page_and_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Research desk" in response.data
    assert b"OFFLINE ONLY" in response.data
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/dashboard.js").status_code == 200
    assert client.get("/static/dashboard.css").status_code == 200


def test_busy_and_lock_released_after_failure(client, monkeypatch):
    def fail(*args):
        assert run(client).status_code == 429
        raise ValueError("invalid test dataset")
    monkeypatch.setattr("tradeos.web.app.run_research", fail)
    assert run(client).status_code == 400
    monkeypatch.setattr("tradeos.web.app.run_research", lambda *args: {"ok": True})
    assert run(client).json == {"ok": True}
