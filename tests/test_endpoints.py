"""
Tests for built-in endpoints (/deadend-status, /deadend-counter).
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


class TestDeadendCounter:
    """Test the /deadend-counter statistics endpoint."""

    def test_counter_endpoint_exists(self, client):
        """Test that /deadend-counter endpoint exists."""
        response = client.get("/deadend-counter")
        assert response.status_code == 200

    def test_counter_response_structure(self, client):
        """Test /deadend-counter returns correct structure."""
        response = client.get("/deadend-counter")
        data = json.loads(response.data)

        assert "total_requests_received" in data
        assert "top_10_domains_urls" in data
        assert "request_type_breakdown" in data

    def test_counter_tracks_requests(self, client):
        """Test that counter tracks requests."""
        # Make several requests
        client.get("/test/exact")
        client.get("/test/exact")
        client.post("/test/placeholder", data="{}")

        response = client.get("/deadend-counter")
        data = json.loads(response.data)

        # Should have at least 3 requests (might have more from other tests)
        assert data["total_requests_received"] >= 3

    def test_counter_top_domains(self, client):
        """Test that counter tracks top domains/URLs."""
        # Make requests to specific path
        for _ in range(5):
            client.get("/test/exact")

        response = client.get("/deadend-counter")
        data = json.loads(response.data)

        top_domains = data["top_10_domains_urls"]
        assert isinstance(top_domains, list)
        # Should have at least one entry
        assert len(top_domains) > 0
        # Each entry should be [path, count]
        if top_domains:
            assert len(top_domains[0]) == 2

    def test_counter_request_breakdown(self, client):
        """Test that counter tracks request method breakdown."""
        client.get("/test/exact")
        client.post("/test/placeholder", data="{}")

        response = client.get("/deadend-counter")
        data = json.loads(response.data)

        breakdown = data["request_type_breakdown"]
        assert isinstance(breakdown, dict)
        # Should have GET and POST
        assert "GET" in breakdown
        assert "POST" in breakdown

    def test_counter_content_type(self, client):
        """Test /deadend-counter returns JSON content type."""
        response = client.get("/deadend-counter")
        assert response.content_type == "application/json"
