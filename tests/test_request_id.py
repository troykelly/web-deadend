"""
Tests for UUIDv7 request ID generation and handling.
"""

import time

import uuid_utils


class TestRequestID:
    """Test UUIDv7 request ID generation and usage."""

    def test_request_id_generated(self, client):
        """Test that every request gets a UUIDv7 request ID."""
        response = client.get("/test/exact")
        assert "X-Request-ID" in response.headers
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 36  # UUID format: 8-4-4-4-12
        assert request_id.count("-") == 4

    def test_request_id_is_uuid7(self, client):
        """Test that request ID is a valid UUIDv7."""
        response = client.get("/test/exact")
        request_id = response.headers["X-Request-ID"]

        # Parse as UUID and check version
        uuid_obj = uuid_utils.UUID(request_id)
        assert uuid_obj.version == 7
        assert str(uuid_obj.variant) == "specified in RFC 4122"

    def test_request_id_unique(self, client):
        """Test that each request gets a unique ID."""
        ids = set()
        for _ in range(10):
            response = client.get("/test/exact")
            ids.add(response.headers["X-Request-ID"])

        assert len(ids) == 10  # All IDs should be unique

    def test_request_id_time_sorted(self, client):
        """Test that UUIDv7 IDs are time-sortable."""
        ids = []
        for _ in range(5):
            response = client.get("/test/exact")
            ids.append(response.headers["X-Request-ID"])
            time.sleep(0.002)  # Small delay to ensure different timestamps

        # UUIDv7 should be sortable by string comparison
        sorted_ids = sorted(ids)
        assert ids == sorted_ids  # Original order should match sorted order

    def test_request_id_in_template(self, client):
        """Test that request ID is available in response templates."""
        response = client.post("/test/placeholder", content_type="application/json", data="{}")

        import json

        data = json.loads(response.data)
        request_id_header = response.headers["X-Request-ID"]
        request_id_body = data["request_id"]

        assert request_id_header == request_id_body

    def test_request_id_on_404(self, client):
        """Test that request ID is present even for 204 responses."""
        response = client.get("/nonexistent/route")
        assert response.status_code == 204
        assert "X-Request-ID" in response.headers

    def test_request_id_different_methods(self, client):
        """Test request ID generation for different HTTP methods."""
        methods = ["GET", "POST", "PUT", "DELETE", "PATCH"]
        ids = []

        for method in methods:
            response = client.open("/test/exact", method=method)
            assert "X-Request-ID" in response.headers
            ids.append(response.headers["X-Request-ID"])

        # All should be unique
        assert len(set(ids)) == len(ids)
