"""
Tests for async GELF logging with queue and background worker thread.
All tests use mocking to avoid actual network calls.
"""

import queue
import threading
import time
from unittest.mock import MagicMock, call, patch

from src.server import Server


class TestAsyncGELFSetup:
    """Test async GELF queue and worker thread setup."""

    def test_gelf_queue_created_when_server_configured(self, monkeypatch):
        """Test that GELF queue is created when GELF_SERVER is set."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()

            # Queue should be created
            assert server.gelf_queue is not None
            assert isinstance(server.gelf_queue, queue.Queue)
            assert server.gelf_queue.maxsize == 10000

    def test_gelf_queue_not_created_without_server(self, monkeypatch):
        """Test that GELF queue is NOT created when GELF_SERVER is not set."""
        monkeypatch.delenv("GELF_SERVER", raising=False)

        server = Server()

        # Queue should not be created
        assert server.gelf_queue is None
        assert server.gelf_worker_thread is None

    def test_worker_thread_started_when_queue_created(self, monkeypatch):
        """Test that worker thread starts when GELF is configured."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()

            # Worker thread should exist and be running
            assert server.gelf_worker_thread is not None
            assert isinstance(server.gelf_worker_thread, threading.Thread)
            # Changed from daemon=True to daemon=False for graceful shutdown support
            assert server.gelf_worker_thread.daemon is False
            assert server.gelf_worker_thread.name == "gelf-logger"
            assert server.gelf_worker_thread.is_alive()

    def test_tcp_handler_created_for_tcp_scheme(self, monkeypatch):
        """Test that TCP handler is created for tcp:// scheme."""
        monkeypatch.setenv("GELF_SERVER", "tcp://localhost:12201")

        with patch("graypy.GELFTCPHandler") as mock_tcp:
            server = Server()

            # TCP handler should be called
            mock_tcp.assert_called_once_with("localhost", 12201)

    def test_udp_handler_created_for_udp_scheme(self, monkeypatch):
        """Test that UDP handler is created for udp:// scheme."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler") as mock_udp:
            server = Server()

            # UDP handler should be called
            mock_udp.assert_called_once_with("localhost", 12201)


class TestAsyncGELFLogging:
    """Test that logs are queued asynchronously without blocking."""

    def test_logs_queued_not_blocking(self, monkeypatch):
        """Test that logs are added to queue (non-blocking operation)."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            # Mock the queue to verify put_nowait is called
            with patch.object(server.gelf_queue, "put_nowait") as mock_put:
                # Make a request
                response = client.get("/test")

                # Verify put_nowait was called (non-blocking)
                mock_put.assert_called_once()

                # Verify the queued data structure
                call_args = mock_put.call_args[0][0]
                assert isinstance(call_args, tuple)
                assert len(call_args) == 2
                message, gelf_data = call_args

                # Verify message format
                assert "GET /test" in message
                assert "204" in message  # No content status

                # Verify gelf_data contains required fields
                assert "request_id" in gelf_data
                assert "method" in gelf_data
                assert gelf_data["method"] == "GET"
                assert gelf_data["path"] == "/test"

    def test_queue_full_handling(self, monkeypatch):
        """Test graceful handling when queue is full."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            # Mock the queue to raise queue.Full
            with patch.object(server.gelf_queue, "put_nowait", side_effect=queue.Full):
                # Make a request - should not crash
                response = client.get("/test")

                # Request should succeed even though logging failed
                assert response.status_code == 204

    def test_multiple_requests_queued(self, monkeypatch):
        """Test that multiple requests are all queued."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            with patch.object(server.gelf_queue, "put_nowait") as mock_put:
                # Make multiple requests
                client.get("/test1")
                client.post("/test2", json={"key": "value"})
                client.put("/test3")

                # All should be queued
                assert mock_put.call_count == 3


class TestGELFWorkerThread:
    """Test that worker thread processes queued items."""

    def test_worker_processes_queued_items(self, monkeypatch):
        """Test that worker thread processes items from queue."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()

            # Mock the gelf_logger to verify it gets called
            mock_logger = MagicMock()
            server.gelf_logger = mock_logger

            # Add an item directly to the queue
            test_message = "GET /test 200 10ms"
            test_data = {"method": "GET", "path": "/test"}
            server.gelf_queue.put((test_message, test_data))

            # Wait for worker to process (with timeout)
            server.gelf_queue.join()  # Block until queue is empty

            # Verify logger.info was called
            mock_logger.info.assert_called_once_with(test_message, extra=test_data)

    def test_worker_handles_errors_gracefully(self, monkeypatch):
        """Test that worker thread continues after errors."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()

            # Mock the gelf_logger to raise an exception
            mock_logger = MagicMock()
            mock_logger.info.side_effect = [Exception("Network error"), None]
            server.gelf_logger = mock_logger

            # Add two items to queue
            server.gelf_queue.put(("message1", {"test": "data1"}))
            server.gelf_queue.put(("message2", {"test": "data2"}))

            # Wait for processing
            time.sleep(0.5)  # Give worker time to process

            # Worker should have attempted both (even though first failed)
            assert mock_logger.info.call_count == 2

    def test_worker_shutdown_on_none_signal(self, monkeypatch):
        """Test that worker thread shuts down on None signal."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()

            # Worker should be running
            assert server.gelf_worker_thread.is_alive()

            # Send shutdown signal
            server.gelf_queue.put(None)

            # Wait for thread to exit
            server.gelf_worker_thread.join(timeout=2.0)

            # Thread should have stopped
            assert not server.gelf_worker_thread.is_alive()


class TestGELFDataFormatting:
    """Test that GELF data is formatted correctly for logging."""

    def test_body_dict_flattened(self, monkeypatch):
        """Test that dict body is flattened into separate fields."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            with patch.object(server.gelf_queue, "put_nowait") as mock_put:
                # Send request with JSON body
                client.post("/test", json={"username": "alice", "action": "login"})

                # Get the queued data
                call_args = mock_put.call_args[0][0]
                message, gelf_data = call_args

                # Body should be flattened
                assert "body_username" in gelf_data
                assert gelf_data["body_username"] == "alice"
                assert "body_action" in gelf_data
                assert gelf_data["body_action"] == "login"

                # Original body should be preserved as JSON
                assert "body_json" in gelf_data

    def test_query_params_flattened(self, monkeypatch):
        """Test that query params are flattened into separate fields."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            with patch.object(server.gelf_queue, "put_nowait") as mock_put:
                # Send request with query params
                client.get("/test?user=alice&page=2")

                # Get the queued data
                call_args = mock_put.call_args[0][0]
                message, gelf_data = call_args

                # Query params should be flattened
                assert "query_user" in gelf_data
                assert gelf_data["query_user"] == "alice"
                assert "query_page" in gelf_data
                assert gelf_data["query_page"] == "2"

                # Original query_params should be preserved as JSON
                assert "query_params_json" in gelf_data

    def test_large_payload_truncation(self, monkeypatch):
        """Test that oversized payloads are truncated."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler"):
            server = Server()
            client = server.app.test_client()

            with patch.object(server.gelf_queue, "put_nowait") as mock_put:
                # Create a very large JSON body
                large_body = {"data": "X" * (2 * 1024 * 1024)}  # 2MB of data

                client.post("/test", json=large_body)

                # Get the queued data
                call_args = mock_put.call_args[0][0]
                message, gelf_data = call_args

                # Body should be truncated/removed
                assert "body_data" not in gelf_data
                assert (
                    gelf_data["body"]
                    == "Request body too large, removed to prevent payload overflow"
                )


class TestGELFIntegration:
    """End-to-end integration tests for async GELF logging."""

    def test_end_to_end_request_logging(self, monkeypatch):
        """Test complete flow from request to GELF logging."""
        monkeypatch.setenv("GELF_SERVER", "udp://localhost:12201")

        with patch("graypy.GELFUDPHandler") as mock_handler_class:
            # Create a mock handler instance
            mock_handler = MagicMock()
            mock_handler_class.return_value = mock_handler

            server = Server()
            client = server.app.test_client()

            # Mock the actual GELF logger
            mock_logger = MagicMock()
            server.gelf_logger = mock_logger

            # Make a request
            response = client.post(
                "/api/login", json={"username": "testuser"}, headers={"X-Custom": "header"}
            )

            # Wait for async processing
            server.gelf_queue.join()
            time.sleep(0.1)  # Give worker thread time to process

            # Verify logger.info was called
            assert mock_logger.info.called

            # Get the logged data
            call_args = mock_logger.info.call_args
            message = call_args[0][0]
            extra_data = call_args[1]["extra"]

            # Verify message format
            assert "POST" in message
            assert "/api/login" in message

            # Verify extra data contains all required fields
            assert extra_data["method"] == "POST"
            assert extra_data["path"] == "/api/login"
            assert "request_id" in extra_data
            assert "timestamp" in extra_data
            assert "body_username" in extra_data
            assert extra_data["body_username"] == "testuser"

    def test_no_gelf_logging_without_queue(self, monkeypatch):
        """Test that no GELF logging happens when GELF_SERVER not set."""
        monkeypatch.delenv("GELF_SERVER", raising=False)

        server = Server()
        client = server.app.test_client()

        # Make a request
        response = client.get("/test")

        # Should succeed without GELF logging
        assert response.status_code == 204
        assert server.gelf_queue is None
