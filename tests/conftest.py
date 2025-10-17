"""
Pytest configuration and fixtures for web-deadend test suite.
"""

import os
import tempfile
from unittest.mock import MagicMock, Mock, patch

import pytest
import yaml

from src.server import Server


@pytest.fixture
def app(set_test_env):
    """Create and configure a test Flask application instance."""
    with patch("src.server.graypy"):  # Mock GELF to avoid network calls
        server = Server()
        server.app.config["TESTING"] = True
        server.app.config["SERVER_INSTANCE"] = server  # Store server instance for tests
        yield server.app
        # Cleanup: Stop any background threads
        if hasattr(server, "stats_worker_thread") and server.stats_worker_thread:
            server.stats_shutdown_event.set()
            server.stats_worker_thread.join(timeout=2)
        if hasattr(server, "gelf_worker_thread") and server.gelf_worker_thread:
            server.gelf_queue.put(None)
            server.gelf_worker_thread.join(timeout=2)


@pytest.fixture
def client(app):
    """Create a Flask test client."""
    return app.test_client()


@pytest.fixture
def mock_gelf_handler():
    """Mock GELF handler for testing logging without network calls."""
    handler = MagicMock()
    handler.host = "mock-gelf.example.com"
    handler.port = 12201
    return handler


@pytest.fixture
def sample_responses_yaml(tmp_path):
    """Create a temporary responses.yaml file for testing."""
    responses = {
        "/test/exact": {
            "GET": {
                "mediatype": "text/plain",
                "base64": False,
                "responsestatus": 200,
                "body": "exact match",
            }
        },
        "/test/{param}": {
            "POST": {
                "mediatype": "application/json",
                "base64": False,
                "responsestatus": 201,
                "body": '{"param": "{{ matched.param }}", "request_id": "{{ request.id }}"}',
            }
        },
        "/test/%IP%/%EPOCH%": {
            "GET": {
                "mediatype": "text/plain",
                "base64": False,
                "responsestatus": 200,
                "body": "IP: {{ matched.IP }}, Epoch: {{ matched.EPOCH }}",
            }
        },
        "r/\\/regex\\/(?P<value>.*?)\\/test": {
            "GET": {
                "mediatype": "text/plain",
                "base64": False,
                "responsestatus": 200,
                "body": "Regex value: {{ matched.value }}",
            }
        },
        "/template/test": {
            "POST": {
                "mediatype": "application/json",
                "base64": False,
                "responsestatus": 200,
                "body": '{"id": "{{ request.id }}", "safe_ip": "{{ request.safe_ip }}", "epoch": {{ request.epoch }}, "body": {{ body | tojson }}, "query": {{ query | tojson }}, "requestdata": "{{ requestdata }}"}',
            }
        },
        "/ota/service/request": {
            "POST": {
                "mediatype": "application/xml",
                "base64": False,
                "responsestatus": 200,
                "body": '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n  <status>success</status>\n  <url>{{ request.protocol }}://{{ request.host }}/reporting/{{ request.safe_ip }}/{{ request.epoch }}/ping.txt{{ requestdata }}</url>\n  <md5>5d41402abc4b2a76b9719d911017c592</md5>\n  <request_id>{{ request.id }}</request_id>\n  <description country="ELSE">Connectivity verification test</description>\n</root>',
            }
        },
        "/reporting/%IP%/%EPOCH%/ping.txt": {
            "GET": {
                "mediatype": "text/plain",
                "base64": False,
                "responsestatus": 200,
                "body": "hello - request {{ request.id }}",
            }
        },
    }

    responses_file = tmp_path / "responses.yaml"
    with open(responses_file, "w") as f:
        yaml.dump(responses, f)

    return str(responses_file)


@pytest.fixture
def mock_uuid7():
    """Provide controlled UUIDv7 for deterministic testing."""

    def _make_uuid(value="0199ef2d-deaa-77b0-bd1e-63ae90349a5f"):
        mock = Mock()
        mock.__str__ = Mock(return_value=value)
        mock.version = 7
        mock.variant = "specified in RFC 4122"
        return mock

    return _make_uuid


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch, sample_responses_yaml):
    """Set environment variables for testing."""
    monkeypatch.setenv("RESPONSES_FILE", sample_responses_yaml)
    monkeypatch.setenv("DEBUG_LEVEL", "DEBUG")
    monkeypatch.setenv("TESTING", "true")  # Disable stats worker in tests
    # Don't set GELF_SERVER to avoid network calls in tests
    monkeypatch.delenv("GELF_SERVER", raising=False)


@pytest.fixture
def mock_graypy(mocker):
    """Mock the graypy module to prevent network calls."""
    mock_gelf_udp = mocker.MagicMock()
    mock_gelf_tcp = mocker.MagicMock()

    mock_module = mocker.MagicMock()
    mock_module.GELFUDPHandler.return_value = mock_gelf_udp
    mock_module.GELFTCPHandler.return_value = mock_gelf_tcp

    mocker.patch.dict("sys.modules", {"graypy": mock_module})

    return {"module": mock_module, "udp_handler": mock_gelf_udp, "tcp_handler": mock_gelf_tcp}
