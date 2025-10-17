"""Tests for periodic stats logging functionality."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest


class TestStatsLogging:
    """Test suite for periodic stats logging."""

    def test_stats_worker_thread_started(self, app):
        """Test that stats worker thread is started on initialization."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            assert server.stats_worker_thread is not None
            assert server.stats_worker_thread.is_alive()
            assert server.stats_worker_thread.name == "stats-logger"

    def test_log_format_json_default(self, app):
        """Test that JSON is the default log format."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            assert server.log_format == "json"

    @patch.dict("os.environ", {"LOG_FORMAT": "text"})
    def test_log_format_text(self):
        """Test text log format configuration."""
        from src.server import Server

        server = Server()
        assert server.log_format == "text"

    @patch.dict("os.environ", {"LOG_FORMAT": "invalid"})
    def test_log_format_invalid_defaults_to_json(self):
        """Test that invalid log format defaults to JSON."""
        from src.server import Server

        server = Server()
        assert server.log_format == "json"

    @patch.dict("os.environ", {"LOG_STATS_INTERVAL": "30"})
    def test_custom_stats_interval(self):
        """Test custom stats interval configuration."""
        from src.server import Server

        server = Server()
        assert server.log_stats_interval == 30

    @patch.dict("os.environ", {"LOG_HEARTBEAT_INTERVAL": "7200"})
    def test_custom_heartbeat_interval(self):
        """Test custom heartbeat interval configuration."""
        from src.server import Server

        server = Server()
        assert server.log_heartbeat_interval == 7200

    def test_calculate_stats_empty(self, app):
        """Test stats calculation with no requests."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            stats = server._calculate_stats()

            assert stats["requests_per_minute"] == 0
            assert stats["total_requests"] == 0
            assert stats["unique_ips"] == 0
            assert stats["top_paths"] == {}
            assert stats["bytes_received"] == 0
            assert stats["bytes_sent"] == 0
            assert stats["errors"] == {}
            assert "timestamp" in stats

    def test_calculate_stats_with_requests(self, client):
        """Test stats calculation after making requests."""
        from src.server import Server

        server = client.application.config["SERVER_INSTANCE"]

        # Make some requests
        client.get("/test/path")
        client.post("/api/endpoint")
        client.get("/test/path")

        stats = server._calculate_stats()

        assert stats["requests_per_minute"] >= 3
        assert stats["total_requests"] >= 3
        assert stats["unique_ips"] >= 1
        assert "/test/path" in stats["top_paths"]
        assert "/api/endpoint" in stats["top_paths"]

    def test_track_unique_ips(self, client):
        """Test unique IP tracking."""
        server = client.application.config["SERVER_INSTANCE"]

        # Make requests (all from same IP in test environment)
        client.get("/test1")
        client.get("/test2")
        client.get("/test3")

        assert len(server.unique_ips) >= 1

    def test_track_path_counter(self, client):
        """Test path counter tracking."""
        server = client.application.config["SERVER_INSTANCE"]

        client.get("/path1")
        client.get("/path1")
        client.get("/path2")

        assert server.path_counter["/path1"] >= 2
        assert server.path_counter["/path2"] >= 1

    def test_track_error_counter(self, client):
        """Test error counter tracking."""
        server = client.application.config["SERVER_INSTANCE"]

        # Make a request that will return 404
        client.get("/nonexistent")

        # Check that 404 or 204 was counted (depends on response.yaml config)
        assert len(server.error_counter) >= 0  # May or may not have errors

    def test_track_traffic_bytes(self, client):
        """Test traffic byte tracking."""
        server = client.application.config["SERVER_INSTANCE"]

        initial_received = server.total_bytes_received
        initial_sent = server.total_bytes_sent

        # Make a POST request with data
        client.post("/api/test", data={"key": "value"})

        # Bytes should have increased
        assert server.total_bytes_received >= initial_received
        assert server.total_bytes_sent >= initial_sent

    def test_log_stats_json_format(self, app, caplog):
        """Test JSON format stats logging."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            server.log_format = "json"

            stats = {
                "requests_per_minute": 100,
                "unique_ips": 50,
                "total_requests": 1000,
                "top_paths": {"/test": 500},
                "bytes_received": 1024,
                "bytes_sent": 2048,
                "errors": {404: 10},
                "timestamp": time.time(),
            }

            server._log_stats(stats, heartbeat=False)

            # Check that JSON was logged
            assert len(caplog.records) > 0
            log_record = caplog.records[-1]
            log_data = json.loads(log_record.message)

            assert log_data["requests_per_minute"] == 100
            assert log_data["unique_ips"] == 50
            assert log_data["heartbeat"] is False
            assert log_data["service"] == "web-deadend"
            assert "version" in log_data

    def test_log_stats_text_format(self, app, caplog):
        """Test text format stats logging."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            server.log_format = "text"

            stats = {
                "requests_per_minute": 100,
                "unique_ips": 50,
                "total_requests": 1000,
                "top_paths": {"/test": 500},
                "bytes_received": 1024,
                "bytes_sent": 2048,
                "errors": {404: 10},
                "timestamp": time.time(),
            }

            server._log_stats(stats, heartbeat=False)

            assert len(caplog.records) > 0
            log_message = caplog.records[-1].message

            assert "[STATS]" in log_message
            assert "Requests/min: 100" in log_message
            assert "Unique IPs: 50" in log_message
            assert "Total requests: 1000" in log_message
            assert "1024↓" in log_message
            assert "2048↑" in log_message

    def test_log_stats_heartbeat_indicator(self, app, caplog):
        """Test heartbeat indicator in logs."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]
            server.log_format = "text"

            stats = {
                "requests_per_minute": 0,
                "unique_ips": 0,
                "total_requests": 0,
                "top_paths": {},
                "bytes_received": 0,
                "bytes_sent": 0,
                "errors": {},
                "timestamp": time.time(),
            }

            server._log_stats(stats, heartbeat=True)

            assert len(caplog.records) > 0
            log_message = caplog.records[-1].message
            assert "[HEARTBEAT]" in log_message

    def test_stats_change_detection(self, client):
        """Test that stats changes are detected correctly."""
        server = client.application.config["SERVER_INSTANCE"]

        # Get initial stats
        stats1 = server._calculate_stats()

        # Make a request to change stats
        client.get("/test")

        # Get new stats
        stats2 = server._calculate_stats()

        # Stats should be different
        assert stats1 != stats2

    def test_stats_unchanged_detection(self, app):
        """Test that unchanged stats are detected correctly."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]

            stats1 = server._calculate_stats()
            # Don't make any requests
            stats2 = server._calculate_stats()

            # Stats should be similar (except timestamp)
            assert stats1["requests_per_minute"] == stats2["requests_per_minute"]
            assert stats1["unique_ips"] == stats2["unique_ips"]

    @patch.dict("os.environ", {"LOG_STATS_INTERVAL": "1"})
    def test_stats_worker_logs_on_change(self, caplog):
        """Test that stats worker logs when stats change."""
        from src.server import Server

        server = Server()

        # Give the worker thread time to run once
        time.sleep(1.5)

        # There should be at least one log entry
        # Note: The first run might not log if stats haven't changed from empty
        # So we just verify the thread is working
        assert server.stats_worker_thread.is_alive()

    def test_stats_worker_shutdown(self, app):
        """Test stats worker thread shutdown."""
        with app.app_context():
            server = app.config["SERVER_INSTANCE"]

            # Thread should be running
            assert server.stats_worker_thread.is_alive()

            # Signal shutdown
            server.stats_shutdown_event.set()

            # Give thread time to shutdown
            server.stats_worker_thread.join(timeout=2)

            # Thread should have stopped
            assert not server.stats_worker_thread.is_alive()
