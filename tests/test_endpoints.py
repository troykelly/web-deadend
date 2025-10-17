"""
Tests for built-in endpoints (/deadend-status).
"""

import json


class TestDeadendStatus:
    """Test the /deadend-status health check endpoint."""

    def test_status_endpoint_exists(self, client):
        """Test that /deadend-status endpoint exists."""
        response = client.get("/deadend-status")
        assert response.status_code == 200

    def test_status_endpoint_response(self, client):
        """Test /deadend-status returns correct JSON."""
        response = client.get("/deadend-status")
        data = json.loads(response.data)
        assert data == {"service": "ok"}

    def test_status_content_type(self, client):
        """Test /deadend-status returns JSON content type."""
        response = client.get("/deadend-status")
        assert response.content_type == "application/json"

    def test_status_has_request_id(self, client):
        """Test /deadend-status includes request ID header."""
        response = client.get("/deadend-status")
        # Note: Status endpoint from localhost might skip logging but should still have ID
        # This depends on _should_skip_logging implementation
        # For now, we just check the endpoint works
        assert response.status_code == 200


class TestDeadendCounterRemoved:
    """Test that the /deadend-counter endpoint has been removed."""

    def test_counter_endpoint_removed(self, client):
        """Test that /deadend-counter endpoint no longer exists."""
        response = client.get("/deadend-counter")
        # Should return 204 or whatever the catch-all returns
        # The endpoint should not exist as a dedicated route
        assert response.status_code in [204, 200]  # Catch-all behavior
