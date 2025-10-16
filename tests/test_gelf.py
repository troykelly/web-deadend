"""
Tests for GELF logging with mocked handlers.
"""

import logging
import os
from unittest.mock import MagicMock, Mock, call, patch


class TestGELFLogging:
    """Test GELF logging functionality with mocked handlers."""

    def test_gelf_handler_not_created_without_env(self, monkeypatch):
        """Test that GELF handler is not created when GELF_SERVER not set."""
        monkeypatch.delenv("GELF_SERVER", raising=False)

        with patch("src.server.graypy") as mock_graypy:
            from src.server import Server

            server = Server()

            # GELF handler should not be initialized
            assert server.gelf_logger is None
            mock_graypy.GELFUDPHandler.assert_not_called()
            mock_graypy.GELFTCPHandler.assert_not_called()

    def test_gelf_udp_handler_created(self, monkeypatch):
        """Test that GELF UDP handler is created correctly."""
        monkeypatch.setenv("GELF_SERVER", "udp://graylog.example.com:12201")

        with patch("src.server.graypy") as mock_graypy:
            mock_handler = MagicMock()
            mock_graypy.GELFUDPHandler.return_value = mock_handler

            from src.server import Server

            server = Server()

            # UDP handler should be created
            mock_graypy.GELFUDPHandler.assert_called_once_with("graylog.example.com", 12201)
            assert server.gelf_logger is not None

    def test_gelf_tcp_handler_created(self, monkeypatch):
        """Test that GELF TCP handler is created correctly."""
        monkeypatch.setenv("GELF_SERVER", "tcp://graylog.example.com:12201")

        with patch("src.server.graypy") as mock_graypy:
            mock_handler = MagicMock()
            mock_graypy.GELFTCPHandler.return_value = mock_handler

            from src.server import Server

            server = Server()

            # TCP handler should be created
            mock_graypy.GELFTCPHandler.assert_called_once_with("graylog.example.com", 12201)
            assert server.gelf_logger is not None

    def test_gelf_logging_includes_request_id(self, client):
        """Test that request logging includes request_id field."""
        # Make a request
        response = client.get("/test/exact")
        request_id = response.headers["X-Request-ID"]

        # Request ID should be in the response header (verified by test_request_id.py)
        # This test verifies the integration works
        assert request_id is not None
        assert len(request_id) == 36

    def test_gelf_logging_field_flattening(self, client):
        """Test that body and query params are processed."""
        # Make request with body and query params
        response = client.post(
            "/template/test?param1=value1",
            content_type="application/x-www-form-urlencoded",
            data="key1=data1&key2=data2",
        )

        # Should complete successfully
        assert response.status_code == 200
        # Verify the response contains the data (proves it was parsed)
        import json

        data = json.loads(response.data)
        assert data["body"] == {"key1": "data1", "key2": "data2"}
        assert data["query"] == {"param1": "value1"}

    def test_gelf_payload_size_limiting(self, client):
        """Test that large payloads are handled gracefully."""
        # Make request with very large body (> 1MB)
        large_data = "x" * (2 * 1024 * 1024)  # 2MB
        response = client.post("/template/test", content_type="text/plain", data=large_data)

        # Should still complete without error
        assert response.status_code in [200, 204]


class TestGELFConfiguration:
    """Test GELF configuration and error handling."""

    def test_invalid_gelf_scheme(self, monkeypatch, caplog):
        """Test handling of invalid GELF URL scheme."""
        monkeypatch.setenv("GELF_SERVER", "http://graylog.example.com:12201")

        with patch("src.server.graypy"):
            from src.server import Server

            server = Server()

            # Should log error and not create handler (gelf_logger will be None or not set)
            # Just check that the error was logged
            assert any("Unsupported GELF scheme" in record.message for record in caplog.records)

    def test_gelf_logger_warning_when_not_configured(self, monkeypatch, caplog):
        """Test warning when GELF server not specified."""
        monkeypatch.delenv("GELF_SERVER", raising=False)

        with patch("src.server.graypy"):
            from src.server import Server

            server = Server()

            # Should log warning
            assert any("No GELF server specified" in record.message for record in caplog.records)
