"""Integration tests for the FastAPI API and bot manager."""

import time
import pytest
from fastapi.testclient import TestClient
from api.server import app, manager


@pytest.fixture(autouse=True)
def reset_manager():
    """Reset bot manager state between tests."""
    manager.kill_all()
    for bot in manager.bots.values():
        bot.trades.clear()
        bot.pnl_history.clear()
        bot.total_pnl = 0.0
        bot.win_count = 0
        bot.loss_count = 0
        bot.started_at = None
        bot.thread = None
    yield
    manager.kill_all()


client = TestClient(app)


class TestBotAPI:
    def test_get_bots_returns_4(self):
        resp = client.get("/api/bots")
        assert resp.status_code == 200
        bots = resp.json()
        assert len(bots) == 4
        keys = {b["key"] for b in bots}
        assert keys == {"macd", "rsi", "cvd", "copytrade"}

    def test_start_and_stop_bot(self):
        resp = client.post("/api/bots/macd/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        # Starting again should return already_running
        resp = client.post("/api/bots/macd/start")
        assert resp.json()["status"] == "already_running"

        resp = client.post("/api/bots/macd/stop")
        assert resp.json()["status"] == "stopped"

        # Stopping again should return already_stopped
        resp = client.post("/api/bots/macd/stop")
        assert resp.json()["status"] == "already_stopped"

    def test_start_unknown_bot(self):
        resp = client.post("/api/bots/unknown/start")
        assert "error" in resp.json()

    def test_kill_all(self):
        client.post("/api/bots/macd/start")
        client.post("/api/bots/rsi/start")
        resp = client.post("/api/bots/kill-all")
        data = resp.json()
        assert data["status"] == "killed"
        assert len(data["bots"]) == 2


class TestMetricsAPI:
    def test_get_metrics(self):
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_pnl" in data
        assert "sharpe_ratio" in data
        assert "total_trades" in data
        assert "best_strategy" in data


class TestRiskAPI:
    def test_get_risk(self):
        resp = client.get("/api/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_pnl" in data
        assert "max_daily_loss" in data
        assert "open_positions" in data


class TestTradesAPI:
    def test_get_trades_empty(self):
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_trades_with_limit(self):
        resp = client.get("/api/trades?limit=10")
        assert resp.status_code == 200


class TestSettingsAPI:
    def test_get_settings(self):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "position_size" in data
        assert "dry_run" in data

    def test_update_settings_valid(self):
        resp = client.put("/api/settings", json={"position_size": 5.0})
        assert resp.status_code == 200
        assert resp.json()["position_size"] == 5.0

    def test_update_settings_rejects_negative(self):
        resp = client.put("/api/settings", json={"position_size": -5})
        assert resp.status_code == 422

    def test_update_settings_rejects_too_large(self):
        resp = client.put("/api/settings", json={"position_size": 9999})
        assert resp.status_code == 422

    def test_update_settings_rejects_stop_loss_over_100(self):
        resp = client.put("/api/settings", json={"stop_loss_pct": 150})
        assert resp.status_code == 422

    def test_update_dry_run(self):
        resp = client.put("/api/settings", json={"dry_run": True})
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True


class TestDashboard:
    def test_root_serves_dashboard(self):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_docs_page(self):
        resp = client.get("/docs.html")
        assert resp.status_code == 200
